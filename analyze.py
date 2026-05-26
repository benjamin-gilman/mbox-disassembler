#!/usr/bin/env python3
"""Phase 1 — Analyze a Gmail Takeout .mbox or .zip archive."""

import argparse
import re
import sys
import zipfile
from collections import Counter
from email import message_from_bytes
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Streaming parser
# ---------------------------------------------------------------------------

MBOX_FROM_RE = re.compile(rb"^From \S+ ")

# Inline / tracking types that aren't real user attachments
_SKIP_ATTACH_TYPES = {"image/gif", "image/png", "image/jpeg", "image/jpg",
                      "text/plain", "text/html", "text/calendar"}
CONTENT_DISP_RE = re.compile(rb"^Content-Disposition\s*:", re.IGNORECASE)
FILENAME_RE = re.compile(rb'(?:filename|name)\*?=(?:utf-8\'\')?["\']?([^"\';\r\n\t ]+)', re.IGNORECASE)
CONTENT_TYPE_PART_RE = re.compile(rb"^Content-Type\s*:\s*([^\s;/]+/[^\s;]+)", re.IGNORECASE)


def _decode_header(raw: str) -> str:
    """Decode MIME-encoded words in a header value to a plain string."""
    from email.header import decode_header
    parts = []
    for bval, charset in decode_header(raw):
        if isinstance(bval, bytes):
            parts.append(bval.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(bval)
    return " ".join(parts)


def _open_source(source_path):
    """Return (file_obj, zip_ctx_or_None, mbox_name) for a .zip or .mbox path."""
    if source_path.endswith(".zip"):
        zf = zipfile.ZipFile(source_path)
        mbox_name = next((n for n in zf.namelist() if n.endswith(".mbox")), None)
        if not mbox_name:
            print("ERROR: no .mbox file found inside the zip.", file=sys.stderr)
            sys.exit(1)
        return zf.open(mbox_name), zf, mbox_name
    else:
        return open(source_path, "rb"), None, source_path


def analyze(source_path: str) -> dict:
    stats = {
        "total": 0,
        "dates": [],
        "senders": Counter(),
        "recipients": Counter(),
        "sent_from": Counter(),   # from-address on Sent-labeled messages
        "labels": Counter(),
        "attachment_types": Counter(),
        "msgs_with_attachments": 0,
        "subject_sample": [],     # up to 30 evenly spread subjects
        "by_year": Counter(),
    }

    fobj, zctx, mbox_name = _open_source(source_path)

    # State machine: FIND_FROM → HEADERS → BODY
    STATE_FROM = 0
    STATE_HEADERS = 1
    STATE_BODY = 2

    state = STATE_FROM
    header_lines: list[bytes] = []
    in_attachment_block = False
    cur_has_attachment = False
    cur_attach_ct = b""
    n = 0

    try:
        for raw_line in fobj:
            if MBOX_FROM_RE.match(raw_line):
                # ---- flush previous message ----
                if header_lines:
                    _process_headers(header_lines, stats, n)
                    if cur_has_attachment:
                        stats["msgs_with_attachments"] += 1

                n += 1
                header_lines = []
                in_attachment_block = False
                cur_has_attachment = False
                cur_attach_ct = b""
                state = STATE_HEADERS

                if n % 2000 == 0:
                    print(f"\r  {n:,} messages scanned…", end="", flush=True)

            elif state == STATE_HEADERS:
                if raw_line.strip() == b"":
                    state = STATE_BODY
                else:
                    header_lines.append(raw_line)

            elif state == STATE_BODY:
                # Detect Content-Disposition: attachment lines
                if CONTENT_DISP_RE.match(raw_line):
                    if b"attachment" in raw_line.lower():
                        in_attachment_block = True
                        cur_has_attachment = True
                    else:
                        in_attachment_block = False

                # Detect Content-Type for the attachment block to get extension
                elif in_attachment_block and CONTENT_TYPE_PART_RE.match(raw_line):
                    cur_attach_ct = raw_line

                # Filename gives us the extension
                fn_m = FILENAME_RE.search(raw_line)
                if fn_m and in_attachment_block:
                    fname = fn_m.group(1).decode("utf-8", errors="replace").strip().strip("'\"")
                    ext = Path(fname).suffix.lstrip(".").upper() or "NO_EXT"
                    stats["attachment_types"][ext] += 1
                    in_attachment_block = False  # counted; reset

        # ---- flush last message ----
        if header_lines:
            _process_headers(header_lines, stats, n)
            if cur_has_attachment:
                stats["msgs_with_attachments"] += 1
        n += 1

    finally:
        fobj.close()
        if zctx:
            zctx.close()

    stats["total"] = n
    print(f"\r  Done — {n:,} messages scanned.      ")
    return stats


def _process_headers(lines: list[bytes], stats: dict, msg_index: int):
    try:
        msg = message_from_bytes(b"".join(lines))
    except Exception:
        return

    # Date
    date_str = msg.get("Date", "")
    year = None
    if date_str:
        try:
            dt = parsedate_to_datetime(date_str)
            # Normalize to naive UTC for comparison
            if dt.tzinfo is not None:
                from datetime import timezone
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            stats["dates"].append(dt)
            year = dt.year
            stats["by_year"][year] += 1
        except Exception:
            pass

    # From
    _, from_addr = parseaddr(msg.get("From", ""))
    from_addr = from_addr.lower().strip()
    if from_addr:
        stats["senders"][from_addr] += 1

    # To / Cc
    for hdr in ("To", "Cc"):
        val = msg.get(hdr, "")
        if val:
            for part in val.split(","):
                _, addr = parseaddr(part.strip())
                if addr:
                    stats["recipients"][addr.lower()] += 1

    # Gmail labels
    labels_raw = msg.get("X-Gmail-Labels", "")
    is_sent = False
    for lbl in labels_raw.split(","):
        lbl = lbl.strip()
        if lbl:
            stats["labels"][lbl] += 1
            if lbl.lower() == "sent":
                is_sent = True

    if is_sent and from_addr:
        stats["sent_from"][from_addr] += 1

    # Subject sample: keep up to 30, sampled every N messages
    subj = _decode_header(str(msg.get("Subject", ""))).strip()
    if subj and len(stats["subject_sample"]) < 30:
        stats["subject_sample"].append(subj[:120])


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _bar(val, max_val, width=35) -> str:
    filled = int(val * width / max_val) if max_val else 0
    return "█" * filled + "░" * (width - filled)


def print_report(stats: dict, source_path: str):
    total = stats["total"]
    dates = sorted(stats["dates"])

    sep = "─" * 62

    def section(title):
        print(f"\n  {title}")
        print(f"  {sep[:len(title) + 2]}")

    print()
    print("  " + "═" * 60)
    print("   MBOX ANALYSIS REPORT")
    print("  " + "═" * 60)
    print(f"  Source : {source_path}")

    # Account owner
    if stats["sent_from"]:
        owner = stats["sent_from"].most_common(1)[0][0]
    elif stats["recipients"]:
        owner = stats["recipients"].most_common(1)[0][0]
    else:
        owner = "unknown"
    print(f"  Account: {owner}")

    # Date range
    if dates:
        lo, hi = dates[0], dates[-1]
        span = (hi - lo).days / 365.25
        print(f"  Range  : {lo.strftime('%b %Y')} → {hi.strftime('%b %Y')}  ({span:.1f} years)")

    print(f"  Emails : {total:,}")
    pct = 100 * stats["msgs_with_attachments"] / total if total else 0
    print(f"  With attachments: {stats['msgs_with_attachments']:,}  ({pct:.1f}%)")

    # Volume by year
    if stats["by_year"]:
        section("EMAIL VOLUME BY YEAR")
        max_yr = max(stats["by_year"].values())
        for yr in sorted(stats["by_year"]):
            cnt = stats["by_year"][yr]
            print(f"  {yr}  {_bar(cnt, max_yr)}  {cnt:>7,}")

    # Gmail labels
    if stats["labels"]:
        section("GMAIL LABELS")
        for lbl, cnt in sorted(stats["labels"].items(), key=lambda x: -x[1])[:20]:
            print(f"  {lbl:<35} {cnt:>8,}")

    # Top senders
    section("TOP SENDERS  (by message count)")
    for addr, cnt in stats["senders"].most_common(20):
        print(f"  {addr:<48} {cnt:>6,}")

    # Attachment types
    if stats["attachment_types"]:
        section("ATTACHMENT FILE TYPES")
        # Drop NO_EXT (usually inline tracking pixels) from the display
        display_types = {k: v for k, v in stats["attachment_types"].items() if k != "NO_EXT"}
        if display_types:
            max_a = max(display_types.values())
            for ext, cnt in sorted(display_types.items(), key=lambda x: -x[1])[:25]:
                print(f"  .{ext:<12} {_bar(cnt, max_a, 20)}  {cnt:>6,}")
        no_ext = stats["attachment_types"].get("NO_EXT", 0)
        if no_ext:
            print(f"  (+ {no_ext:,} attachments with no file extension, likely inline images)")

    # Subject sample
    if stats["subject_sample"]:
        section("SUBJECT SAMPLE  (first 30 seen)")
        for s in stats["subject_sample"]:
            print(f"  • {s}")

    print()
    print("  " + "═" * 60)
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Phase 1: Analyze a Gmail Takeout .mbox or .zip archive."
    )
    parser.add_argument("source", help="Path to .mbox file or Google Takeout .zip")
    args = parser.parse_args()

    path = args.source
    if not Path(path).exists():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    print(f"Analyzing {path} …")
    stats = analyze(path)
    print_report(stats, path)


if __name__ == "__main__":
    main()
