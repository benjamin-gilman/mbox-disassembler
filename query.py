#!/usr/bin/env python3
"""
Ad-hoc query template for a Gmail Takeout .mbox or .zip archive.

Uses the same streaming state machine as analyze.py. To ask a new question:
  1. Edit FILTER_RE (or the filter logic in check_msg) to match what you want
  2. Edit check_msg to decide what to collect from matching messages
  3. Run:  python3 query.py /path/to/takeout.zip

No external dependencies — stdlib only.
"""

import argparse
import re
import sys
import zipfile
from collections import Counter
from email import message_from_bytes
from email.header import decode_header as _dh
from email.utils import parseaddr, parsedate_to_datetime, getaddresses
from pathlib import Path


# ---------------------------------------------------------------------------
# ✏️  CONFIGURE YOUR QUERY HERE
# ---------------------------------------------------------------------------

# Regex applied to the entire raw message bytes before parsing.
# Set to None to skip the fast pre-filter and inspect every message.
FILTER_RE = re.compile(rb"your search term here", re.IGNORECASE)

# Optional: restrict to a year range (set to None to disable)
YEAR_MIN = None   # e.g. 2008
YEAR_MAX = None   # e.g. 2014


def check_msg(msg, raw: bytes, hits: list):
    """
    Called for every message that passes FILTER_RE.
    Parse what you need and append to hits.

    msg  — email.message.Message object (headers already parsed)
    raw  — full raw bytes of the message (headers + body)
    hits — list to append results to
    """
    subj = _decode(msg.get("Subject", ""))
    from_ = _decode(msg.get("From", ""))
    date_ = msg.get("Date", "")
    labels = msg.get("X-Gmail-Labels", "")

    # Get plain-text body snippet
    body = _get_body(msg)

    # Find the matching context in the body
    m = FILTER_RE.search(raw) if FILTER_RE else None
    if m:
        start = max(0, m.start() - 150)
        snippet = raw[start: m.start() + 300].decode("utf-8", errors="replace").strip()
    else:
        snippet = body[:400]

    hits.append({
        "date":    date_,
        "from":    from_,
        "subject": subj,
        "labels":  labels,
        "snippet": snippet,
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decode(raw_header: str) -> str:
    parts = []
    for b, charset in _dh(str(raw_header)):
        if isinstance(b, bytes):
            parts.append(b.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(b)
    return " ".join(parts)


def _get_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return part.get_payload(decode=True).decode("utf-8", errors="replace")
                except Exception:
                    pass
    else:
        try:
            return msg.get_payload(decode=True).decode("utf-8", errors="replace")
        except Exception:
            pass
    return ""


def _year_of(msg) -> int | None:
    try:
        dt = parsedate_to_datetime(msg.get("Date", ""))
        return dt.year
    except Exception:
        return None


def _open_source(source_path: str):
    if source_path.endswith(".zip"):
        zf = zipfile.ZipFile(source_path)
        mbox_name = next((n for n in zf.namelist() if n.endswith(".mbox")), None)
        if not mbox_name:
            print("ERROR: no .mbox file found in zip.", file=sys.stderr)
            sys.exit(1)
        return zf.open(mbox_name), zf
    return open(source_path, "rb"), None


# ---------------------------------------------------------------------------
# Streaming engine
# ---------------------------------------------------------------------------

MBOX_FROM_RE = re.compile(rb"^From \S+ ")


def run_query(source_path: str) -> list:
    hits = []
    fobj, zctx = _open_source(source_path)

    STATE_FROM, STATE_HEADERS, STATE_BODY = 0, 1, 2
    state = STATE_FROM
    current: list[bytes] = []
    n = 0

    def flush():
        nonlocal n
        if not current:
            return
        raw = b"".join(current)

        # Fast pre-filter
        if FILTER_RE and not FILTER_RE.search(raw):
            return

        try:
            msg = message_from_bytes(raw)
        except Exception:
            return

        # Year filter
        if YEAR_MIN is not None or YEAR_MAX is not None:
            yr = _year_of(msg)
            if yr is None:
                return
            if YEAR_MIN and yr < YEAR_MIN:
                return
            if YEAR_MAX and yr > YEAR_MAX:
                return

        check_msg(msg, raw, hits)

    try:
        for raw_line in fobj:
            if MBOX_FROM_RE.match(raw_line):
                flush()
                current = [raw_line]
                n += 1
                state = STATE_HEADERS
                if n % 5000 == 0:
                    print(f"\r  {n:,} messages scanned, {len(hits)} hits…", end="", flush=True)
            else:
                current.append(raw_line)
        flush()
    finally:
        fobj.close()
        if zctx:
            zctx.close()

    print(f"\r  Done — {n:,} messages scanned, {len(hits)} hits found.      ")
    return hits


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Ad-hoc mbox query template.")
    parser.add_argument("source", help="Path to .mbox file or Google Takeout .zip")
    parser.add_argument("--limit", type=int, default=30, help="Max results to print (default 30)")
    args = parser.parse_args()

    if not Path(args.source).exists():
        print(f"ERROR: file not found: {args.source}", file=sys.stderr)
        sys.exit(1)

    print(f"Querying {args.source} …")
    hits = run_query(args.source)

    for h in hits[: args.limit]:
        print(f"\nDate:    {h['date']}")
        print(f"From:    {h['from']}")
        print(f"Subject: {h['subject']}")
        print(f"Labels:  {h['labels']}")
        print(f"Snippet: …{h['snippet']}…")
        print("─" * 60)

    if len(hits) > args.limit:
        print(f"\n(showing {args.limit} of {len(hits)} hits — use --limit to see more)")


if __name__ == "__main__":
    main()
