"""Effective weights and contribution shares — the curation half of the graph.

Two rules, both from ADR-001 §1.1 / F5-S2 [INVARIANT]:

* **Selection is the weight.** There is no separate "selected" set to keep in
  sync; weight 0 means excluded-but-visible.
* **A comp the client never mentioned defaults to 1.0** — except a comp with
  no `squareFootage`, which defaults to 0.0 (spec §7, F4-S5): it has no
  $/sqft, so it cannot contribute to a $/sqft statistic, and defaulting it to
  1 would mean silently weighting a comp that contributes nothing.

  The defaulting rule (rather than "the client enumerates every comp") is what
  makes F13-S1 work: after a refresh, comps that are new to the pull arrive
  included at weight 1 without the client having heard of them.

An *explicit* weight always wins, including a positive weight on a no-sqft
comp. That comp still cannot enter a $/sqft median — it has no value to
contribute — but the user's intent is echoed back rather than overridden
silently.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

__all__ = ["contribution_pcts", "effective_weights"]

#: A comp with no $/sqft contributes to no $/sqft statistic, so it starts at 0.
DEFAULT_WEIGHT_NO_SQFT = 0.0
DEFAULT_WEIGHT = 1.0


def effective_weights(
    keys: Sequence[str],
    has_psf: Sequence[bool],
    requested: Mapping[str, float],
) -> list[float]:
    """The weight actually used for each comp, in `keys` order.

    Deliberately iterates `keys` (a stable, ordered sequence) and *looks up*
    the mapping, never the other way round: iterating the request's dict would
    make the result depend on the client's JSON key order.
    """
    if len(keys) != len(has_psf):
        raise ValueError(
            f"keys and has_psf must have equal length (got {len(keys)} and {len(has_psf)})"
        )
    return [
        requested[key]
        if key in requested
        else (DEFAULT_WEIGHT if psf_present else DEFAULT_WEIGHT_NO_SQFT)
        for key, psf_present in zip(keys, has_psf, strict=True)
    ]


def contribution_pcts(
    weights: Sequence[float], included: Sequence[bool]
) -> list[float | None]:
    """Each included comp's share of the total included weight (F5-S2).

    Computed here rather than in the view: "how much is this comp influencing
    the analysis" is a statistic, and D5 puts every statistic in Python.
    `None` for comps that are not contributing at all — a share of an analysis
    a comp is not part of is not 0%, it is undefined.
    """
    total = sum(weight for weight, keep in zip(weights, included, strict=True) if keep)
    if total <= 0.0:
        return [None] * len(weights)
    return [
        (weight / total) if keep else None
        for weight, keep in zip(weights, included, strict=True)
    ]
