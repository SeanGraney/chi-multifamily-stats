"""Layer 2 — our truth (ARCHITECTURE.md §3).

`Spell` and `StitchedComp`: fields non-Optional where the pipeline
guarantees them. The DTO→domain transition is where missing data becomes an
explicit flag instead of a silently propagating None. Populated by the
pipeline stories (F5+).
"""
