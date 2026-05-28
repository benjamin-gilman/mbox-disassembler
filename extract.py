#!/usr/bin/env python3
"""
Phase 3 — Extract emails and attachments into the approved folder structure.

Streams through a .mbox or Google Takeout .zip, routes each message into
the topic/subtopic folders defined in structure.json, and writes:
  - <topic>/<subtopic>/YYYY-MM-DD_sender_subject.html   (human-readable)
  - <topic>/<subtopic>/YYYY-MM-DD_sender_subject.txt    (Claude-readable)
  - <topic>/<subtopic>/attachments/<filename>           (extracted files)

Output always goes to:  output/<archive_name>/   (relative to this script)

Usage:
    python3 extract.py /path/to/takeout.zip
    python3 extract.py /path/to/takeout.zip --incremental
    python3 extract.py /path/to/takeout.zip --skip-topics shopping newsletters-and-noise
"""

import argparse
import hashlib
import json
import re
import sys
import zipfile
from collections import defaultdict
from email import message_from_bytes
from email.header import decode_header as _dh
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Header / text helpers
# ---------------------------------------------------------------------------

MBOX_FROM_RE = re.compile(rb"^From \S+ ")


def _decode_header(raw) -> str:
    parts = []
    for b, charset in _dh(str(raw)):
        if isinstance(b, bytes):
            parts.append(b.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(b)
    return " ".join(parts).strip()


def slugify(text: str, max_len: int = 40) -> str:
    text = str(text).lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:max_len] or "untitled"


def html_to_text(html: str) -> str:
    """Very light HTML → plain text (strips tags, collapses whitespace)."""
    text = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<p[^>]*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_tracking_pixels(html: str) -> str:
    """Remove 1×1 tracking pixel <img> tags."""
    return re.sub(
        r'<img[^>]+(?:width=["\']?1["\']?[^>]+height=["\']?1["\']?'
        r'|height=["\']?1["\']?[^>]+width=["\']?1["\']?)[^>]*/?>',
        "",
        html,
        flags=re.IGNORECASE,
    )


def get_body_parts(msg):
    """Return (html_body, text_body) from a parsed message."""
    html_body = text_body = None
    if msg.is_multipart():
        for part in msg.walk():
            if "attachment" in str(part.get("Content-Disposition", "")).lower():
                continue
            ct = part.get_content_type()
            charset = part.get_content_charset() or "utf-8"
            try:
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                decoded = payload.decode(charset, errors="replace")
                if ct == "text/html" and html_body is None:
                    html_body = decoded
                elif ct == "text/plain" and text_body is None:
                    text_body = decoded
            except Exception:
                pass
    else:
        ct = msg.get_content_type()
        charset = msg.get_content_charset() or "utf-8"
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                decoded = payload.decode(charset, errors="replace")
                if ct == "text/html":
                    html_body = decoded
                else:
                    text_body = decoded
        except Exception:
            pass
    return html_body, text_body


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def classify_message(msg, topics: list, skip_topics: set):
    """
    Route a message to (topic_slug, subtopic_slug).
    Returns ("misc", None) if nothing matches.
    """
    from_raw = _decode_header(msg.get("From", "")).lower()
    _, from_addr = parseaddr(from_raw)
    from_addr = from_addr.lower()
    subject = _decode_header(msg.get("Subject", "")).lower()
    labels = msg.get("X-Gmail-Labels", "").lower()

    try:
        year = parsedate_to_datetime(msg.get("Date", "")).year
    except Exception:
        year = None

    for topic in topics:
        slug = topic["slug"]
        if slug in skip_topics:
            continue

        c = topic.get("classifiers", {})

        # Optional date range gate (e.g. education)
        dr = c.get("date_range")
        if dr and year and not (dr.get("from", 0) <= year <= dr.get("to", 9999)):
            continue

        matched = (
            any(p.lower() in from_addr or p.lower() in from_raw for p in c.get("sender_patterns", []))
            or any(kw.lower() in subject for kw in c.get("subject_keywords", []))
            or any(lbl.lower() in labels for lbl in c.get("gmail_labels", []))
        )

        if matched:
            sub = _subtopic(slug, topic, from_addr, from_raw, subject, year)
            return slug, sub

    return "misc", None


def _subtopic(slug, topic, from_addr, from_raw, subject, year):
    subs = [s["slug"] for s in topic.get("subtopics", [])]
    if not subs:
        return None

    if slug == "finance":
        if any(x in from_addr or x in from_raw for x in ["elyashar", "irs.gov", "ftb"]):
            return "taxes"
        if any(k in subject for k in ["tax", "1099", "w-2", "w2", "refund", "balance due"]):
            return "taxes"
        if any(x in from_addr for x in ["fidelity", "proxyvote", "rowe", "schwab", "vanguard", "etrade"]):
            return "investments"
        return "banking"

    if slug == "education":
        if "cmu.edu" in from_addr or (year and 2009 <= year <= 2013):
            return "cmu"
        return "high-school"

    if slug == "career":
        if any(x in from_addr for x in ["apple.com", "sedgwick"]):
            return "apple"
        return "job-search"

    if slug == "home":
        if any(k in subject for k in ["lease", "rent", "mortgage", "escrow", "condo", "floor", "renovation"]):
            return "real-estate"
        return "utilities-services"

    if slug == "health":
        if any(x in from_addr for x in ["huckleberry", "scrcfertility"]) or \
           any(k in subject for k in ["baby", "diaper", "fertility", "pregnancy"]):
            return "parenting"
        return "medical"

    return None


# ---------------------------------------------------------------------------
# File writing
# ---------------------------------------------------------------------------

HTML_SHELL = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body{{font-family:-apple-system,Arial,sans-serif;max-width:860px;margin:2em auto;padding:0 1em;color:#222}}
  .hdr{{background:#f5f5f5;border-left:3px solid #999;padding:.75em 1em;margin-bottom:1.5em;font-size:.9em;line-height:1.6}}
  .hdr b{{display:inline-block;width:60px}}
</style>
<title>{subject}</title>
</head>
<body>
<div class="hdr">
  <b>From:</b> {from_}<br>
  <b>To:</b> {to}<br>
  <b>Date:</b> {date}<br>
  <b>Subject:</b> {subject}
</div>
{body}
</body>
</html>
"""


def make_filename(date_str: str, from_addr: str, subject: str) -> str:
    try:
        dt = parsedate_to_datetime(date_str)
        date_part = dt.strftime("%Y-%m-%d")
    except Exception:
        date_part = "0000-00-00"
    sender = slugify(from_addr.split("@")[0] if "@" in from_addr else from_addr, 20)
    subj = slugify(subject, 50)
    return f"{date_part}_{sender}_{subj}"


def write_email(msg, folder: Path, base: str):
    folder.mkdir(parents=True, exist_ok=True)
    from_ = _decode_header(msg.get("From", ""))
    to = _decode_header(msg.get("To", ""))
    date = msg.get("Date", "")
    subject = _decode_header(msg.get("Subject", "")) or "(no subject)"
    html_body, text_body = get_body_parts(msg)

    # .html
    html_path = folder / f"{base}.html"
    if not html_path.exists():
        if html_body:
            body_content = strip_tracking_pixels(html_body)
        elif text_body:
            body_content = f"<pre style='white-space:pre-wrap'>{text_body}</pre>"
        else:
            body_content = "<em>(no body)</em>"
        html_path.write_text(
            HTML_SHELL.format(from_=from_, to=to, date=date, subject=subject, body=body_content),
            encoding="utf-8", errors="replace",
        )

    # .txt
    txt_path = folder / f"{base}.txt"
    if not txt_path.exists():
        if text_body:
            plain = text_body
        elif html_body:
            plain = html_to_text(html_body)
        else:
            plain = "(no body)"
        header_block = f"From: {from_}\nTo: {to}\nDate: {date}\nSubject: {subject}\n\n"
        txt_path.write_text(header_block + plain, encoding="utf-8", errors="replace")


def write_attachments(msg, attach_dir: Path, date_prefix: str) -> list[str]:
    if not msg.is_multipart():
        return []
    written = []
    seen_names: defaultdict[str, int] = defaultdict(int)

    for part in msg.walk():
        if "attachment" not in str(part.get("Content-Disposition", "")).lower():
            continue
        raw_name = part.get_filename()
        if raw_name:
            filename = _decode_header(raw_name).strip().strip("'\"")
            filename = re.sub(r"\?=$", "", filename).strip()
        else:
            ext = part.get_content_type().split("/")[-1]
            filename = f"attachment.{ext}"

        seen_names[filename] += 1
        if seen_names[filename] > 1:
            stem, suf = Path(filename).stem, Path(filename).suffix
            filename = f"{stem}_{seen_names[filename]}{suf}"

        safe = slugify(Path(filename).stem, 50) + Path(filename).suffix.lower()
        out_path = attach_dir / f"{date_prefix}_{safe}"

        if not out_path.exists():
            payload = part.get_payload(decode=True)
            if payload:
                attach_dir.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(payload)
                written.append(out_path.name)

    return written


# ---------------------------------------------------------------------------
# Main extraction loop
# ---------------------------------------------------------------------------

def extract(source_path: str, structure: dict, output_root: Path,
            incremental: bool, skip_topics: set):

    manifest_path = output_root / "manifest.json"
    manifest: dict = {}
    if incremental and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        print(f"  Incremental mode — {len(manifest):,} messages already in manifest.")

    topics = structure["topics"]

    # Open source
    if source_path.endswith(".zip"):
        zf = zipfile.ZipFile(source_path)
        mbox_name = next((n for n in zf.namelist() if n.endswith(".mbox")), None)
        if not mbox_name:
            print("ERROR: no .mbox file found in zip.", file=sys.stderr)
            sys.exit(1)
        fobj, zctx = zf.open(mbox_name), zf
    else:
        fobj, zctx = open(source_path, "rb"), None

    topic_counts: defaultdict[str, int] = defaultdict(int)
    filename_seen: defaultdict[str, int] = defaultdict(int)
    n_total = n_written = n_skipped = 0
    current: list[bytes] = []

    def flush():
        nonlocal n_total, n_written, n_skipped
        if not current:
            return
        raw = b"".join(current)
        # Fast dedup key from first 512 bytes
        msg_id = hashlib.md5(raw[:512]).hexdigest()

        if incremental and msg_id in manifest:
            n_skipped += 1
            return

        try:
            msg = message_from_bytes(raw)
        except Exception:
            return

        topic_slug, subtopic_slug = classify_message(msg, topics, skip_topics)

        # Output folder for emails
        parts = [output_root, topic_slug]
        if subtopic_slug:
            parts.append(subtopic_slug)
        email_folder = Path(*parts)
        attach_folder = email_folder / "attachments"

        # Filename
        date_str = msg.get("Date", "")
        _, from_addr = parseaddr(_decode_header(msg.get("From", "")))
        subject = _decode_header(msg.get("Subject", "")) or "no-subject"
        base = make_filename(date_str, from_addr, subject)

        # Collision guard within folder
        fkey = f"{email_folder}/{base}"
        filename_seen[fkey] += 1
        if filename_seen[fkey] > 1:
            base = f"{base}--{filename_seen[fkey]}"

        write_email(msg, email_folder, base)
        write_attachments(msg, attach_folder, base)

        topic_counts[topic_slug] += 1
        n_written += 1

        manifest[msg_id] = {
            "topic": topic_slug,
            "subtopic": subtopic_slug,
            "date": date_str,
            "from": from_addr,
            "file": base,
        }

    try:
        for raw_line in fobj:
            if MBOX_FROM_RE.match(raw_line):
                flush()
                current = [raw_line]
                n_total += 1
                if n_total % 2000 == 0:
                    print(f"\r  {n_total:,} messages · {n_written:,} written…", end="", flush=True)
            else:
                current.append(raw_line)
        flush()
        n_total += 1
    finally:
        fobj.close()
        if zctx:
            zctx.close()

    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"\r  Done — {n_total:,} total · {n_written:,} written · {n_skipped:,} skipped      ")
    print()
    print("  Distribution across topics:")
    for slug, count in sorted(topic_counts.items(), key=lambda x: -x[1]):
        print(f"    {slug:<32} {count:>7,}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Phase 3: Extract emails and attachments into folder structure."
    )
    parser.add_argument("source", help="Path to .mbox or Google Takeout .zip")
    parser.add_argument("--structure", default="structure.json",
                        help="Path to structure.json (default: structure.json)")
    parser.add_argument("--incremental", action="store_true",
                        help="Skip messages already recorded in manifest.json")
    parser.add_argument("--skip-topics", nargs="*", default=[],
                        metavar="SLUG",
                        help="Topic slugs to skip entirely (e.g. shopping newsletters-and-noise)")
    args = parser.parse_args()

    if not Path(args.source).exists():
        print(f"ERROR: source not found: {args.source}", file=sys.stderr)
        sys.exit(1)

    struct_path = Path(args.structure)
    if not struct_path.exists():
        print(f"ERROR: structure file not found: {args.structure}", file=sys.stderr)
        print("Run phase 2 first:  python3 structure.py <source>", file=sys.stderr)
        sys.exit(1)

    structure = json.loads(struct_path.read_text())
    archive_name = structure.get("archive_name", "archive")
    output_root = Path(__file__).parent / "output" / archive_name
    output_root.mkdir(parents=True, exist_ok=True)

    skip = set(args.skip_topics)

    print(f"Phase 3: Extracting → {output_root}")
    if skip:
        print(f"  Skipping topics: {', '.join(skip)}")
    print()

    extract(args.source, structure, output_root, args.incremental, skip)

    print()
    print(f"  Next → python3 index.py {output_root}")


if __name__ == "__main__":
    main()
