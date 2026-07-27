# PM session handoffs

One file per PM session ending, named `YYYY-MM-DD-NN.md` (NN = 01, 02… within a day).

**A fresh PM session reads the newest `status: UNCONSUMED` handoff first, before
QUEUE.md.** Then it marks that file `status: CONSUMED` (with the date it was
consumed) as its first commit, so the next session can tell at a glance what is
still live. More than one UNCONSUMED file means an earlier session ended without
its successor picking the thread up — read them oldest-first.

## What belongs here vs. in QUEUE.md

`QUEUE.md` is the durable audit trail: story states, rulings, event log. It
answers *what happened*. It is authoritative and a handoff must never contradict
it.

A handoff answers *what I was thinking* — the judgment-level context that dies
with a session and that no file reconstructs:

- what is mid-flight right now, and the exact command/branch state to resume it
- patterns noticed across stories (a recurring failure mode, an agent habit)
- why something was reprioritized, if the reason isn't obvious from the row
- what I would have done next, and why that order
- anything flagged but not yet escalated to the owner
- traps: things that look wrong but are deliberate, or look fine but are fragile

Not: acceptance-criteria detail, test counts, or anything already in a QUEUE row
or a git commit. If a fact is in QUEUE.md, link to it rather than restating it —
a handoff that duplicates the queue goes stale and starts lying.

## When to write one

- Before any deliberate session restart (charter: "immediately before any restart")
- At each story boundary, as cheap insurance — sessions can end without warning
  and the PM cannot see its own usage against the session limit
- Whenever the owner signals the session is near its limit
