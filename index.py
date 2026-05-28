#!/usr/bin/env python3
"""
Phase 4 — Generate INDEX.md files throughout the archive.

Walks every folder in output/<archive>/ and writes an INDEX.md that
serves two purposes:
  1. Human navigation: clickable table of contents in Finder / Google Drive
  2. Claude navigation: structured map Claude reads first to answer questions
     without having to open individual files

Usage:
    python3 index.py output/ben-gilman-personal
    python3 index.py output/ben-gilman-personal --structure structure.json
"""

import argparse
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Reading email metadata from .txt sidecars
# ---------------------------------------------------------------------------

def read_email_meta(txt_path: Path) -> dict:
    """Read From/Date/Subject from the header block of a .txt email file."""
    meta = {"from": "", "date": "", "subject": ""}
    try:
        with open(txt_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line:
                    break
                if line.startswith("From: "):
                    meta["from"] = line[6:].strip()
                elif line.startswith("Date: "):
                    meta["date"] = line[6:].strip()
                elif line.startswith("Subject: "):
                    meta["subject"] = line[9:].strip()
    except Exception:
        pass
    return meta


def format_date(date_str: str) -> str:
    """Parse an email Date header into YYYY-MM-DD."""
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(date_str).strftime("%Y-%m-%d")
    except Exception:
        m = re.match(r"(\d{4}-\d{2}-\d{2})", str(date_str))
        return m.group(1) if m else ""


def sender_short(from_str: str) -> str:
    """Condense a From header to a short display string."""
    from email.utils import parseaddr
    name, addr = parseaddr(from_str)
    if name and name.strip() and name.strip() != addr:
        return name.strip()[:40]
    return addr.split("@")[0][:40] if "@" in addr else addr[:40]


# ---------------------------------------------------------------------------
# Scanning folders
# ---------------------------------------------------------------------------

def scan_emails(folder: Path) -> list:
    """Return sorted-by-date list of email metadata for all .html files in folder."""
    emails = []
    for html in folder.glob("*.html"):
        txt = html.with_suffix(".txt")
        meta = read_email_meta(txt) if txt.exists() else {}
        emails.append({
            "date":    format_date(meta.get("date", "")),
            "from":    meta.get("from", ""),
            "subject": meta.get("subject", html.stem[:70]),
            "html":    html.name,
        })
    emails.sort(key=lambda x: x["date"], reverse=True)
    return emails


def scan_attachments(attach_dir: Path) -> list:
    """Return list of attachment info dicts."""
    if not attach_dir.exists():
        return []
    items = []
    for f in attach_dir.iterdir():
        if f.is_file():
            kb = f.stat().st_size / 1024
            items.append({
                "name": f.name,
                "ext":  f.suffix.upper().lstrip(".") or "FILE",
                "size": f"{kb:.0f} KB" if kb < 1024 else f"{kb/1024:.1f} MB",
            })
    items.sort(key=lambda x: x["name"], reverse=True)
    return items


# ---------------------------------------------------------------------------
# Markdown table renderers
# ---------------------------------------------------------------------------

def email_table_md(emails: list, max_rows: int = 250) -> str:
    if not emails:
        return "_No emails in this folder._\n"
    shown = emails[:max_rows]
    lines = [
        f"_Showing {len(shown):,} of {len(emails):,} emails — most recent first._\n",
        "| Date | From | Subject | View |",
        "|------|------|---------|------|",
    ]
    for e in shown:
        subj = e["subject"][:80].replace("|", "\\|")
        frm  = sender_short(e["from"]).replace("|", "\\|")
        lines.append(f"| {e['date']} | {frm} | {subj} | [open]({e['html']}) |")
    if len(emails) > max_rows:
        lines.append(f"\n_…and {len(emails) - max_rows:,} more. Use query.py to search further._")
    return "\n".join(lines) + "\n"


def attachment_table_md(attachments: list, max_rows: int = 100) -> str:
    if not attachments:
        return "_No attachments._\n"
    shown = attachments[:max_rows]
    lines = [
        f"_Showing {len(shown):,} of {len(attachments):,} attachments._\n",
        "| Filename | Type | Size |",
        "|----------|------|------|",
    ]
    for a in shown:
        lines.append(f"| {a['name'][:80]} | {a['ext']} | {a['size']} |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# INDEX.md writers
# ---------------------------------------------------------------------------

def write_leaf_index(folder: Path, title: str, description: str) -> tuple:
    """Write INDEX.md for a leaf folder containing emails directly."""
    emails = scan_emails(folder)
    attachments = scan_attachments(folder / "attachments")

    sections = [
        f"# {title}",
        f"",
        f"_{description}_",
        f"",
        f"**{len(emails):,} emails** &nbsp;·&nbsp; **{len(attachments):,} attachments**",
        f"",
        f"---",
        f"",
        f"## Emails",
        f"",
        email_table_md(emails),
    ]
    if attachments:
        sections += ["", "## Attachments", "", attachment_table_md(attachments)]

    (folder / "INDEX.md").write_text("\n".join(sections), encoding="utf-8")
    return len(emails), len(attachments)


def write_topic_index(topic_folder: Path, topic_info: dict):
    """Write INDEX.md for a topic folder — lists subtopics or emails directly."""
    name = topic_info["name"]
    desc = topic_info["description"]
    subtopics = topic_info.get("subtopics", [])
    existing_subs = [s for s in subtopics if (topic_folder / s["slug"]).exists()]

    if existing_subs:
        # Has subtopic folders — build a directory-style index
        rows = [
            "| Folder | Description | Emails | Attachments |",
            "|--------|-------------|--------|-------------|",
        ]
        for sub in existing_subs:
            sf = topic_folder / sub["slug"]
            n_e = len(list(sf.glob("*.html")))
            ad = sf / "attachments"
            n_a = len(list(ad.iterdir())) if ad.exists() else 0
            rows.append(
                f"| [{sub['name']}]({sub['slug']}/INDEX.md)"
                f" | {sub['description']} | {n_e:,} | {n_a:,} |"
            )

        content = "\n".join([
            f"# {name}",
            f"",
            f"_{desc}_",
            f"",
            f"---",
            f"",
            f"## Subtopics",
            f"",
            *rows,
            f"",
            f"_Open a subtopic's INDEX.md to see individual emails and attachments._",
        ])
    else:
        # No subtopic folders — emails live directly here
        emails = scan_emails(topic_folder)
        attachments = scan_attachments(topic_folder / "attachments")
        sections = [
            f"# {name}",
            f"",
            f"_{desc}_",
            f"",
            f"**{len(emails):,} emails** &nbsp;·&nbsp; **{len(attachments):,} attachments**",
            f"",
            f"---",
            f"",
            f"## Emails",
            f"",
            email_table_md(emails),
        ]
        if attachments:
            sections += ["", "## Attachments", "", attachment_table_md(attachments)]
        content = "\n".join(sections)

    (topic_folder / "INDEX.md").write_text(content, encoding="utf-8")


def write_master_index(output_root: Path, structure: dict, manifest: dict):
    """Write the master INDEX.md at the archive root."""
    ctx   = structure.get("context_summary", "")
    topics = structure.get("topics", [])
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    topic_counts = Counter(v["topic"] for v in manifest.values())

    topic_rows = [
        "| Folder | Description | Emails |",
        "|--------|-------------|--------|",
    ]
    for t in topics:
        cnt = topic_counts.get(t["slug"], 0)
        topic_rows.append(
            f"| [{t['name']}]({t['slug']}/INDEX.md) | {t['description']} | {cnt:,} |"
        )
    misc_cnt = topic_counts.get("misc", 0)
    if misc_cnt:
        topic_rows.append(f"| [Misc](misc/INDEX.md) | Unclassified messages | {misc_cnt:,} |")

    # Build quick-reference table from structure
    qr_rows = [
        "| Looking for… | Go to |",
        "|--------------|-------|",
    ]
    # Generate quick-reference from topic/subtopic structure
    for t in topics:
        subs = t.get("subtopics", [])
        if subs:
            for s in subs:
                sf = output_root / t["slug"] / s["slug"]
                if sf.exists():
                    qr_rows.append(f"| {s['description']} | [{t['slug']}/{s['slug']}]({t['slug']}/{s['slug']}/INDEX.md) |")
        else:
            tf = output_root / t["slug"]
            if tf.exists():
                qr_rows.append(f"| {t['description']} | [{t['slug']}]({t['slug']}/INDEX.md) |")

    content = "\n".join([
        f"# Archive Index",
        f"",
        f"_{ctx}_",
        f"",
        f"**Generated:** {generated} &nbsp;·&nbsp; **Total emails indexed:** {len(manifest):,}",
        f"",
        f"---",
        f"",
        f"## How to navigate",
        f"",
        f"1. Find the most relevant topic in the table below",
        f"2. Open that folder's `INDEX.md` for a breakdown of subtopics and individual emails",
        f"3. Open `.html` files to read emails in a browser; `.txt` files for plain text",
        f"4. Attachments (PDFs, spreadsheets, images) are in each folder's `attachments/` subfolder",
        f"",
        f"---",
        f"",
        f"## Topics",
        f"",
        *topic_rows,
        f"",
        f"---",
        f"",
        f"## Quick reference",
        f"",
        *qr_rows,
    ])

    (output_root / "INDEX.md").write_text(content, encoding="utf-8")
    print(f"  ✓  INDEX.md  (master)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Phase 4: Generate INDEX.md navigation files throughout the archive."
    )
    parser.add_argument("output_root", help="Path to the archive output folder")
    parser.add_argument("--structure", default="structure.json",
                        help="Path to structure.json (default: ./structure.json)")
    args = parser.parse_args()

    output_root = Path(args.output_root)
    if not output_root.exists():
        print(f"ERROR: output folder not found: {output_root}")
        return

    struct_path = Path(args.structure)
    if not struct_path.exists():
        print(f"ERROR: structure.json not found: {args.structure}")
        return

    structure = json.loads(struct_path.read_text())
    topics    = structure["topics"]

    manifest_path = output_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    print(f"Phase 4: Building index for {output_root}")
    print(f"  {len(manifest):,} messages in manifest")
    print()

    # --- Subtopic / leaf indexes ---
    for topic in topics:
        slug        = topic["slug"]
        topic_folder = output_root / slug
        if not topic_folder.exists():
            continue

        subtopics = topic.get("subtopics", [])
        existing_subs = [s for s in subtopics if (topic_folder / s["slug"]).exists()]

        if existing_subs:
            for sub in existing_subs:
                sub_folder = topic_folder / sub["slug"]
                n_e, n_a = write_leaf_index(
                    sub_folder,
                    title=f"{topic['name']} / {sub['name']}",
                    description=sub["description"],
                )
                print(f"  ✓  {slug}/{sub['slug']}/INDEX.md  ({n_e:,} emails, {n_a:,} attachments)")

        write_topic_index(topic_folder, topic)
        print(f"  ✓  {slug}/INDEX.md")

    # --- misc ---
    misc_folder = output_root / "misc"
    if misc_folder.exists():
        n_e, n_a = write_leaf_index(
            misc_folder,
            title="Misc (Unclassified)",
            description=(
                "Messages that didn't match any topic classifier. "
                "Worth reviewing — may contain useful content to reclassify."
            ),
        )
        print(f"  ✓  misc/INDEX.md  ({n_e:,} emails, {n_a:,} attachments)")

    # --- master ---
    print()
    write_master_index(output_root, structure, manifest)

    print()
    print(f"  All done. Start here:")
    print(f"  open {output_root}/INDEX.md")


if __name__ == "__main__":
    main()
