# mbox-disassembler — Project Guide for Claude

This project builds organized, searchable archives from Gmail Takeout `.mbox` exports.
It is designed to work on any inbox — personal or business — and should treat each
archive as a fresh context requiring its own questions and taxonomy.

---

## Project phases

| Phase | Script | Status | Purpose |
|-------|--------|--------|---------|
| 1 | `analyze.py` | ✅ Done | Statistical report: who, what, when, how much |
| 2 | `structure.py` | Planned | AI-propose a topic taxonomy, human approves before anything is written |
| 3 | `extract.py` | Planned | Write emails + attachments to the approved structure |
| 4 | `index.py` | Planned | Generate INDEX.md files, extract PDF text sidecars, write manifest.json |

---

## Two operating modes

### Mode 1 — Local build (one-time, on the user's Mac)
Used for the initial bulk processing of a large mbox/zip archive.

```
mbox zip → Phase 1 → Phase 2 → Phase 3 → Phase 4 → upload folder to Google Drive
```

### Mode 2 — Drive maintenance (ongoing, via Claude + GDrive connector)
Used after the archive is in Google Drive. No local Python required.
Claude reads files natively (PDFs, HTML, text) and updates INDEX.md files in place.

```
New file arrives → user or Claude uploads to correct Drive folder
                 → Claude reads it, extracts key facts
                 → Claude appends a row to the folder's INDEX.md
                 → Claude updates the master INDEX.md summary line
```

---

## Output folder structure

```
[archive-name]/
├── INDEX.md                  ← Master map: what's here, how it's organized, key facts
├── manifest.json             ← Registry of every processed file (path + hash + date)
│                               Used to skip already-processed files on incremental runs
├── contacts/
│   ├── INDEX.md              ← Table: name | role | # emails | date range | key topics
│   └── [contact-name]/
│       ├── INDEX.md          ← Relationship summary, key threads, notable dates
│       └── threads/          ← .html + .txt pairs, one per email thread
│
├── topics/                   ← AI-proposed taxonomy (see Phase 2 questions below)
│   ├── INDEX.md
│   └── [topic]/
│       ├── INDEX.md          ← Table: document | date | sender | one-line summary | path
│       └── [subtopic]/
│
├── attachments/
│   ├── INDEX.md              ← Table: filename | type | date | sender | topic folder | summary
│   └── [topic]/
│       ├── original.pdf      ← original file, intact
│       └── original.txt      ← extracted text sidecar (written at index time)
│
└── timeline/
    ├── INDEX.md
    └── YYYY.md               ← Chronological summary per year
```

---

## Key design decisions

### INDEX.md everywhere
Every folder has an INDEX.md that serves as both a human-readable summary and
Claude's navigation map. Claude reads the master INDEX.md first, then drills into
sub-folder INDEX.md files, then opens individual documents only when needed.

INDEX.md files use **structured tables**, not free-form prose, so Claude can append
a single row during incremental updates without touching the rest of the file.

### Email storage: HTML + plain text sidecar
Each email thread is stored as two files:
- `.html` — cleaned email, tracking pixels stripped, viewable in Chrome
- `.txt` — plain text body only, for Claude to read without HTML parsing noise

### PDF text sidecars
Every PDF attachment gets a `.txt` sidecar containing extracted text.
Written once during Phase 4 (locally). In Drive mode, Claude reads the PDF natively
and writes its own sidecar if one doesn't exist.

### manifest.json
Tracks every processed file by path and content hash. Used by `extract.py --incremental`
to skip files already in the archive and process only new ones.

### Folder naming
All folder and file names: lowercase, hyphens instead of spaces, no special characters.
Dates: YYYY-MM-DD prefix for chronological sorting.
Safe for Google Drive, the filesystem, and URLs.

---

## Questions to ask at the start of every new archive

Before running Phase 2 (structure proposal), Claude should ask the user:

1. **Personal or business?**
   The topic taxonomy, contact categories, and indexing priorities differ significantly.

2. **Who does this inbox belong to?** (name, rough context)
   Helps Claude infer relationships from sender/recipient patterns.

3. **What is the primary use case for this archive?**
   Examples: financial/tax research, legal discovery, personal history, business operations.
   This shapes which topics get top-level folders vs. subtopics.

4. **Are there specific topics, people, or document types that are especially important?**
   Claude will prioritize these in the taxonomy and make them easy to find.

5. **What is the destination after local build?**
   Google Drive (default), local only, or other. Affects naming conventions and
   index format.

6. **Rough date range and volume** (from Phase 1 output — already known at this point).

---

## Lessons learned (from initial test run)

- **Streaming is essential** for large archives. The parser uses a state machine
  (FIND_FROM → HEADERS → BODY) and never loads more than one message into memory.
  A 9.4 GB, 65k-message archive processes in ~3 minutes.

- **Search address fields, not bodies.** Keyword searches on email bodies produce
  massive false-positive rates due to HTML boilerplate. Searching `To`, `Cc`,
  and `Delivered-To` address fields is far more precise — mailing list addresses
  are unambiguous identifiers of membership and subscriptions.

- **Gmail mbox specifics:**
  - `X-Gmail-Labels` header on every message (Inbox, Sent, Archived, Category*, Chat, etc.)
  - `Chat` label = Google Chat messages included in the export
  - `Sent` label used to infer account owner (most common From-address on Sent messages)
  - Distribution lists appear in address fields as `+dist+~<listname>/...@domain`

- **Attachment detection gotchas:**
  - `NO_EXT` bucket = attachments with no filename (usually inline tracking pixels)
  - `PDF?=` / `JPG?=` artifacts = RFC 2231-encoded filenames, need extra decoding
  - Filenames split across MIME continuation lines can be missed by line-by-line scanning

- **MIME-encoded subjects** (`=?utf-8?B?...?=`) must be decoded with
  `email.header.decode_header` before display or comparison.

- **Mixed timezone dates** (some offset-aware, some naive) must be normalized to UTC
  before sorting or comparison.

---

## Running Phase 1

```bash
python3 analyze.py /path/to/takeout-XXXXXX.zip
# or
python3 analyze.py /path/to/archive.mbox
```

No dependencies beyond Python 3 stdlib.

---

## Ad-hoc queries

Use `query.py` as a template. Set `FILTER_RE` and optionally `YEAR_MIN`/`YEAR_MAX`,
then implement `check_msg()` to collect what you need. The streaming engine handles
the rest. See `query.py` for full documentation and examples.
