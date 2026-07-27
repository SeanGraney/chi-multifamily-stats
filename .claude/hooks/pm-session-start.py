#!/usr/bin/env python3
"""SessionStart hook — surface the newest UNCONSUMED PM handoff.

The PM cannot see its own usage against the session limit, so sessions end
without warning. `rentcomp-pm/PROJECT_MANAGER.md` tells a fresh PM to consume
the newest UNCONSUMED handoff, but that depends on the instruction being
followed. This hook makes it unconditional: the handoff is injected into the
session's context at startup whether or not any file is read.

Prints a SessionStart hook JSON object on stdout. Silent (exit 0, no output)
when there is nothing unconsumed — a normal mid-project state, not an error.
"""

import glob
import json
import os
import re
import sys

MAX_CHARS = 24000  # generous; a handoff is ~5KB. Guards against runaway injection.
STATUS_RE = re.compile(r"^status:\s*UNCONSUMED\s*$", re.MULTILINE)


def main() -> int:
    root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    handoff_dir = os.path.join(root, "rentcomp-pm", "handoffs")
    if not os.path.isdir(handoff_dir):
        return 0

    # Dated filenames (YYYY-MM-DD-NN.md) sort chronologically as strings.
    # README.md and any other non-dated file is skipped by the glob.
    unconsumed = []
    for path in sorted(glob.glob(os.path.join(handoff_dir, "[0-9]*.md"))):
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        if STATUS_RE.search(text):
            unconsumed.append((os.path.relpath(path, root), text))

    if not unconsumed:
        return 0

    if len(unconsumed) == 1:
        lead = (
            "A PM session handoff is UNCONSUMED. You are a fresh PM session.\n"
            "Per rentcomp-pm/PROJECT_MANAGER.md's boot ritual: mark this file "
            "`status: CONSUMED` (add `consumed: <today>`) and commit that as your "
            "FIRST commit, create the next handoff file, then read QUEUE.md and "
            "resume. Its contents follow — QUEUE.md remains authoritative for "
            "story state; this carries the judgment that would otherwise be lost."
        )
    else:
        lead = (
            f"{len(unconsumed)} PM handoffs are UNCONSUMED, meaning an earlier "
            "session ended without a successor picking up the thread. Read them "
            "oldest-first (order below), consume ALL of them, then create one new "
            "handoff for this session. QUEUE.md remains authoritative for story state."
        )

    parts = [lead, "", "Unconsumed: " + ", ".join(p for p, _ in unconsumed), ""]
    budget = MAX_CHARS
    for path, text in unconsumed:
        if budget <= 0:
            parts.append(f"--- {path} (not shown — size cap; read it directly) ---")
            continue
        body = text[:budget]
        budget -= len(body)
        if len(body) < len(text):
            body += f"\n[truncated at {MAX_CHARS} chars — read {path} directly]"
        parts.append(f"--- BEGIN {path} ---\n{body}\n--- END {path} ---")

    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": "\n".join(parts),
            },
            "suppressOutput": True,
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
