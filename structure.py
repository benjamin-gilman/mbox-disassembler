#!/usr/bin/env python3
"""
Phase 2 — Propose a folder taxonomy for the archive.

Runs Phase 1 analysis, sends the results to Claude, displays a proposed
topic structure for human approval, then saves the approved structure to
structure.json (which Phase 3 uses to route and extract messages).

Usage:
    python3 structure.py /path/to/takeout.zip
    python3 structure.py /path/to/takeout.zip --skip-analysis  # if analyze already ran
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import anthropic

# Import Phase 1
from analyze import analyze, print_report


# ---------------------------------------------------------------------------
# Build the prompt from Phase 1 stats + user-supplied context
# ---------------------------------------------------------------------------

def _stats_to_text(stats: dict, source_path: str) -> str:
    """Render Phase 1 stats as a compact text block for the Claude prompt."""
    dates = sorted(stats["dates"])
    lo = dates[0].strftime("%b %Y") if dates else "unknown"
    hi = dates[-1].strftime("%b %Y") if dates else "unknown"
    span = round((dates[-1] - dates[0]).days / 365.25, 1) if len(dates) > 1 else 0

    # Account owner
    if stats["sent_from"]:
        owner = stats["sent_from"].most_common(1)[0][0]
    elif stats["recipients"]:
        owner = stats["recipients"].most_common(1)[0][0]
    else:
        owner = "unknown"

    lines = [
        f"ACCOUNT: {owner}",
        f"DATE RANGE: {lo} – {hi} ({span} years)",
        f"TOTAL EMAILS: {stats['total']:,}",
        f"EMAILS WITH ATTACHMENTS: {stats['msgs_with_attachments']:,}",
        "",
        "EMAIL VOLUME BY YEAR:",
    ]
    for yr in sorted(stats["by_year"]):
        lines.append(f"  {yr}: {stats['by_year'][yr]:,}")

    lines += ["", "TOP SENDERS (top 30):"]
    for addr, cnt in stats["senders"].most_common(30):
        lines.append(f"  {addr}: {cnt:,}")

    lines += ["", "GMAIL LABELS (all):"]
    for lbl, cnt in sorted(stats["labels"].items(), key=lambda x: -x[1]):
        lines.append(f"  {lbl}: {cnt:,}")

    lines += ["", "ATTACHMENT FILE TYPES (top 20):"]
    for ext, cnt in stats["attachment_types"].most_common(20):
        lines.append(f"  .{ext}: {cnt:,}")

    lines += ["", "SUBJECT SAMPLE (first 30):"]
    for s in stats["subject_sample"]:
        lines.append(f"  • {s}")

    return "\n".join(lines)


def gather_context() -> dict:
    """Ask the user the onboarding questions interactively."""
    print()
    print("  ── Phase 2: Context Questions ──────────────────────────")
    print("  Answer a few questions so Claude can propose the right")
    print("  taxonomy. Press Enter to skip any question.")
    print()

    def ask(label, hint=""):
        if hint:
            print(f"  {label}")
            print(f"  ({hint})")
        else:
            print(f"  {label}")
        ans = input("  > ").strip()
        print()
        return ans

    ctx = {}
    ctx["type"] = ask(
        "Personal or business email?",
        "e.g. 'personal', 'small business — landscaping company', 'law firm'"
    )
    ctx["owner"] = ask(
        "Who does this inbox belong to?",
        "name + brief description, e.g. 'Ben Gilman, personal email from high school to today'"
    )
    ctx["use_case"] = ask(
        "Primary use case for this archive?",
        "e.g. 'ask Claude questions about my past', 'find old contracts', 'tax research'"
    )
    ctx["priorities"] = ask(
        "Any specific topics, people, or document types that are especially important?",
        "e.g. 'tax returns across 3 LLCs', 'correspondence with a specific vendor', or leave blank"
    )
    ctx["notes"] = ask(
        "Anything else Claude should know before proposing a structure?",
    )
    return ctx


# ---------------------------------------------------------------------------
# Claude API call
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an expert archivist and information architect. You will be given a
statistical analysis of a Gmail inbox export, plus context about the owner
and use case. Your job is to propose a clear, practical folder taxonomy for
organizing the archive.

Rules:
- Propose 5–10 top-level topic folders. Not too many, not too few.
- Each top-level folder may have 2–5 subtopics where it makes sense.
- Every folder must have: a slug (lowercase, hyphens), a display name, and a
  one-line description of what goes there.
- Also provide "classifiers" for each folder: a list of sender email patterns,
  subject keywords, and Gmail label names that are strong signals for routing
  a message into that folder. These will be used by the extraction script.
- Rank folders by how useful they are for answering questions (most useful first).
- Output ONLY valid JSON — no prose, no markdown fences. The schema is shown below.

OUTPUT SCHEMA:
{
  "archive_name": "slug-for-this-archive",
  "proposed_by": "claude",
  "generated_at": "ISO timestamp",
  "context_summary": "One sentence summarizing who this is and what it's for",
  "topics": [
    {
      "slug": "finance",
      "name": "Finance & Taxes",
      "description": "Tax returns, banking, investments, financial correspondence",
      "subtopics": [
        {
          "slug": "taxes",
          "name": "Taxes",
          "description": "Tax returns, CPA correspondence, IRS notices"
        }
      ],
      "classifiers": {
        "sender_patterns": ["@irs.gov", "cpa", "elyashar"],
        "subject_keywords": ["tax", "return", "1099", "W-2", "refund"],
        "gmail_labels": ["Category Bills"]
      }
    }
  ]
}
"""


def propose_structure(stats: dict, context: dict, source_path: str) -> dict:
    """Call Claude API to generate a taxonomy proposal."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("\nERROR: ANTHROPIC_API_KEY environment variable not set.", file=sys.stderr)
        print("Set it with: export ANTHROPIC_API_KEY=sk-ant-...", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    stats_text = _stats_to_text(stats, source_path)
    context_text = "\n".join(f"{k.upper()}: {v}" for k, v in context.items() if v)

    user_message = f"""Here is the Phase 1 analysis of the inbox:

{stats_text}

Here is context about the owner and use case:

{context_text}

Please propose a folder taxonomy as JSON per the schema in your instructions."""

    print("\n  Calling Claude to propose taxonomy…")
    response = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = response.content[0].text.strip()

    # Strip markdown fences if Claude included them anyway
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]

    try:
        proposal = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"\nERROR: Claude returned invalid JSON: {e}", file=sys.stderr)
        print("Raw response:", raw[:500], file=sys.stderr)
        sys.exit(1)

    proposal["generated_at"] = datetime.utcnow().isoformat()
    return proposal


# ---------------------------------------------------------------------------
# Display proposal
# ---------------------------------------------------------------------------

def display_proposal(proposal: dict):
    print()
    print("  " + "═" * 60)
    print("   PROPOSED ARCHIVE STRUCTURE")
    print("  " + "═" * 60)
    print(f"  Archive name : {proposal.get('archive_name', 'N/A')}")
    print(f"  Summary      : {proposal.get('context_summary', '')}")
    print()

    for i, topic in enumerate(proposal.get("topics", []), 1):
        print(f"  {i}. {topic['name']}  [{topic['slug']}/]")
        print(f"     {topic['description']}")

        subtopics = topic.get("subtopics", [])
        if subtopics:
            for sub in subtopics:
                print(f"     ├── {sub['name']}  [{sub['slug']}/]")
                print(f"     │   {sub['description']}")

        classifiers = topic.get("classifiers", {})
        senders = classifiers.get("sender_patterns", [])
        keywords = classifiers.get("subject_keywords", [])
        if senders or keywords:
            hints = []
            if senders:
                hints.append(f"senders: {', '.join(senders[:4])}")
            if keywords:
                hints.append(f"keywords: {', '.join(keywords[:5])}")
            print(f"     → {'; '.join(hints)}")
        print()

    print("  " + "─" * 60)


# ---------------------------------------------------------------------------
# Approval loop
# ---------------------------------------------------------------------------

def approval_loop(proposal: dict, output_path: Path) -> dict:
    """Show the proposal and let the user approve, regenerate, or edit."""
    display_proposal(proposal)

    while True:
        print("  Options:")
        print("  [y] Approve and save structure.json")
        print("  [r] Regenerate (you'll be asked for additional guidance)")
        print("  [e] Edit the JSON directly before saving")
        print("  [q] Quit without saving")
        choice = input("\n  Choice: ").strip().lower()

        if choice == "y":
            output_path.write_text(json.dumps(proposal, indent=2))
            print(f"\n  ✓ Saved to {output_path}")
            print("  Run Phase 3 next:  python3 extract.py <source> --structure structure.json")
            return proposal

        elif choice == "r":
            guidance = input("  Additional guidance for Claude: ").strip()
            return None, guidance   # caller handles regeneration

        elif choice == "e":
            import tempfile, subprocess
            with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as tf:
                json.dump(proposal, tf, indent=2)
                tf_path = tf.name
            editor = os.environ.get("EDITOR", "nano")
            subprocess.call([editor, tf_path])
            with open(tf_path) as f:
                try:
                    edited = json.load(f)
                    proposal = edited
                    display_proposal(proposal)
                except json.JSONDecodeError as e:
                    print(f"  JSON parse error: {e}. Try again.")
            Path(tf_path).unlink(missing_ok=True)

        elif choice == "q":
            print("  Exiting without saving.")
            sys.exit(0)

        else:
            print("  Please enter y, r, e, or q.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Phase 2: Propose a folder taxonomy for the archive."
    )
    parser.add_argument("source", help="Path to .mbox file or Google Takeout .zip")
    parser.add_argument(
        "--skip-analysis",
        action="store_true",
        help="Skip Phase 1 re-run (faster if you just ran analyze.py)"
    )
    parser.add_argument(
        "--output",
        default="structure.json",
        help="Where to save the approved structure (default: structure.json)"
    )
    args = parser.parse_args()

    source = args.source
    if not Path(source).exists():
        print(f"ERROR: file not found: {source}", file=sys.stderr)
        sys.exit(1)

    # Phase 1
    if args.skip_analysis:
        print("  Skipping Phase 1 re-analysis (--skip-analysis set).")
        print("  NOTE: Running a quick header-only pass is still needed for the prompt.")
        print("  Remove --skip-analysis if you want fresh stats.\n")
        # Still need stats — run it silently
        stats = analyze(source)
    else:
        print(f"Phase 1: Analyzing {source} …")
        stats = analyze(source)
        print_report(stats, source)

    # Onboarding questions
    context = gather_context()

    # Propose + approve loop
    output_path = Path(args.output)
    guidance = None
    while True:
        proposal = propose_structure(stats, context, source)
        if guidance:
            # Append guidance to context for regeneration
            context["additional_guidance"] = guidance

        result = approval_loop(proposal, output_path)
        if result is None:
            _, guidance = result  # type: ignore
        else:
            break


if __name__ == "__main__":
    main()
