"""F3-S1 — developer-authored supporting test for the story's own [DEFAULT]:
"normalized address casing/whitespace" (story text, verbatim).

NOT part of QA's locked AC set (case-sensitive hashing alone would satisfy
the three literal AC sentences in `test_cache_store.py`) — this pins the PM
ruling folded into the dispatch: "implement it, don't skip it ... a user
retyping 'Main St' as 'MAIN ST' silently missing the cache defeats the whole
point of protecting the 50-call/month budget (D24)."

Scope is deliberately narrow (see `storage/cache.py`'s module docstring):
only the `address` field is normalized. Every other field is hashed as
typed — this file also pins that a *non-address* whitespace/casing change
still changes the key, so a future edit can't accidentally widen the
normalization to fields where it would be wrong (e.g. `propertyType`, whose
exact casing is meaningful to the RentCast query itself).
"""

from __future__ import annotations

from rentcomp.storage.cache import cache_key


def test_address_casing_does_not_change_the_key() -> None:
    lower = cache_key({"address": "3651 s wood st, chicago, il 60609", "years_back": 1})
    upper = cache_key({"address": "3651 S WOOD ST, CHICAGO, IL 60609", "years_back": 1})
    mixed = cache_key({"address": "3651 S Wood St, Chicago, IL 60609", "years_back": 1})
    assert lower == upper == mixed


def test_address_surrounding_and_repeated_whitespace_does_not_change_the_key() -> None:
    tight = cache_key({"address": "3651 S Wood St, Chicago, IL 60609", "years_back": 1})
    loose = cache_key({"address": "  3651  S Wood   St, Chicago,  IL 60609 ", "years_back": 1})
    assert tight == loose


def test_address_normalization_is_scoped_to_the_address_field_only() -> None:
    """A non-address field's casing/whitespace is a genuinely different
    query shape (e.g. `propertyType` casing is meaningful to the RentCast
    query itself) — normalization must not silently widen to it."""
    baseline = cache_key({"address": "1 Main St", "propertyType": "Single Family"})
    changed = cache_key({"address": "1 Main St", "propertyType": "single family"})
    assert baseline != changed


def test_a_genuinely_different_address_still_changes_the_key() -> None:
    """Normalization must never collapse two real, different addresses —
    only casing/whitespace variants of the SAME address."""
    a = cache_key({"address": "1 Main St, Chicago, IL", "years_back": 1})
    b = cache_key({"address": "2 Main St, Chicago, IL", "years_back": 1})
    assert a != b
