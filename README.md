# mbox-disassembler

A toolkit for analyzing, organizing, and querying Gmail Takeout `.mbox` archives —
including all embedded attachments (PDFs, images, spreadsheets, etc.).

Built to work on large archives (tested on a 9.4 GB, 65k-message file) without
ever fully extracting the zip to disk.

---

## Project phases

| Phase | Script | Status | Purpose |
|-------|--------|--------|---------|
| 1 | `analyze.py` | ✅ Done | Stream the archive and produce a statistical report |
| 2 | _(planned)_ | | Propose a folder/index structure based on Phase 1 findings |
| 3 | _(planned)_ | | Extract emails + attachments into the proposed structure |

---

## Phase 1 — `analyze.py`

### Usage

```bash
# Point at a Google Takeout zip (recommended — no extraction needed)
python3 analyze.py /path/to/takeout-XXXXXX.zip

# Or point directly at a .mbox file
python3 analyze.py /path/to/archive.mbox
```

### What it reports

```
Account   — inferred from the most common From-address on Sent-labeled messages
Range     — earliest → latest email date, and total span in years
Emails    — total message count
Attachments — count of messages with attachments, and % of total

EMAIL VOLUME BY YEAR   — bar chart of message count per year
GMAIL LABELS           — all X-Gmail-Labels values and counts (Inbox, Sent, Archived, etc.)
TOP SENDERS            — top 20 From-addresses by message count
ATTACHMENT FILE TYPES  — top 25 extensions (.PDF, .JPG, .DOCX, …) with counts
SUBJECT SAMPLE         — first 30 decoded subject lines (useful for spotting content themes)
```

### No dependencies

Uses only Python 3 standard library (`email`, `zipfile`, `re`, `collections`).

---

## How the streaming parser works

The mbox format separates messages with a line starting with `From ` (the
"envelope From" line). The parser uses a simple state machine rather than
loading the entire file into memory:

```
  ┌──────────────┐   "From " line    ┌─────────────┐   blank line   ┌──────────┐
  │  FIND_FROM   │ ────────────────► │   HEADERS   │ ─────────────► │   BODY   │
  └──────────────┘                   └─────────────┘                └──────────┘
                                            │                             │
                                      collect lines                 scan for
                                      parse with                    Content-Disposition
                                      email module                  + filenames
```

**Headers-only pass (fast):** For each message the parser collects only the
header section (everything before the first blank line) and parses it with
Python's `email.message_from_bytes`. This gives us From, To, Cc, Date,
Subject, and `X-Gmail-Labels` without loading the full message body.

**Body scan (lightweight):** After the blank line the parser switches to
line-by-line regex scanning for:
- `Content-Disposition: attachment` → flags the message as having an attachment
- `filename=` / `name=` parameters → extracts the file extension

This approach processes a 9.4 GB archive in ~3 minutes on a modern Mac without
extracting anything to disk.

---

## Attachment detection

Attachments in mbox are base64-encoded MIME parts embedded in the message body.
The parser detects them by scanning for `Content-Disposition: attachment` headers
within the body, then extracting the filename extension from the `filename=`
parameter on the same or a nearby line.

**Extracting an actual attachment** (confirmed working):
```python
import zipfile
from email import message_from_bytes

with zipfile.ZipFile("takeout.zip") as zf:
    mbox_name = next(n for n in zf.namelist() if n.endswith(".mbox"))
    with zf.open(mbox_name) as f:
        # accumulate lines per message, then:
        msg = message_from_bytes(b"".join(lines))
        for part in msg.walk():
            if part.get_content_type() == "application/pdf":
                raw_bytes = part.get_payload(decode=True)
                # write raw_bytes to disk — fully intact file
```

All attachment bytes are faithfully preserved in the mbox. PDFs, images,
spreadsheets, Word docs, etc. can all be extracted intact.

**Known limitations / cleanup items:**
- Filenames split across MIME continuation lines may be missed
- `NO_EXT` bucket catches attachments without a filename parameter (usually
  inline tracking pixels — hidden from the displayed chart)
- Some encoded filenames produce artifacts like `.PDF?=` — needs RFC 2231
  decoding in a future pass
- Label lines with embedded newlines (`Category\n Updates`) are counted as
  separate labels — needs normalize in `_process_headers`

---

## Account owner detection

Inferred by finding the most common `From:` address among messages with the
`Sent` Gmail label. Falls back to the most common recipient address if no
Sent-labeled messages exist.

---

## Gmail label parsing

Gmail exports add an `X-Gmail-Labels` header to every message:
```
X-Gmail-Labels: Inbox,Important,Category Personal
```
Common label values:
- `Inbox` / `Archived` / `Sent` / `Trash` / `Spam`
- `Important` / `Starred` / `Unread` / `Opened`
- `Category Personal` / `Category Updates` / `Category Promotions` etc.
- `Chat` — Google Chat messages are included in the mbox export
- `IMAP_NotJunk` / `IMAP_$NotJunk` — IMAP client flags

---

## Ad-hoc queries — `query.py`

`query.py` is a reusable template for answering one-off questions about an
archive using the same streaming state machine. Plug in a custom filter and
the script handles all the streaming boilerplate.

**Lesson learned on keyword searches:** Searching email bodies for short strings
produces massive false-positive rates — HTML boilerplate is everywhere.
More reliable approaches:

1. **Filter by date range first** to narrow to the relevant era
2. **Search address fields** (`To`, `Cc`, `Delivered-To`) rather than bodies —
   mailing list addresses are far more specific than body text
3. **CMU-style distribution lists** appear as
   `+dist+~<listname>/...@andrew.cmu.edu` in address fields, making
   organizational membership (clubs, greek orgs, class lists) unambiguous
   without any body scanning at all
