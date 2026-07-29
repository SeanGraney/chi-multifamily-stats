"""F1-S2 (Layer 2) — what reopening a workspace gets served by the record-shaping memo.

The PM asked, on dispatch, whether loading a workspace interacts with
`storage/pulls.py`'s memo at all. It does, unavoidably: a workspace load is
`GET /api/workspaces/{key}` followed by `POST /api/derive`, and every derive
resolves its evidence through `load_shaped_pull(pull_ref, config)`. The
workspace contributes no evidence of its own — it only decides *which* ref is
resolved and how the comps are curated — so whatever the memo is holding for
that ref is what the reopened workspace shows.

These two tests deliberately do NOT use the `workspaces` fixture, so they run
today against `POST /api/search` + `POST /api/derive` rather than waiting for
F1-S2's routes. The property under test belongs to the ref, not to the route
that resolves it.

STATE OF THE MEMO KEY
---------------------
ADR-001 specified `(pull_ref, pull_digest, config, as_of)`; F0-S2 dropped the
digest on the premise "a refresh writes a new ref, it never rewrites one", and
**F4-S9 already restored it** as `_evidence_version` — `(name, size, mtime_ns)`
per raw file — precisely because that premise is false for a partial-pull
resume. The first test below is the regression pin for that, on the workspace
path.

The second test is a FINDING, reported to the PM and marked strict-xfail rather
than fixed: `_evidence_version` covers `cache/<key>/raw/`, but the shaped result
also depends on the MANIFEST (`as_of` and the window are read from it), and the
manifest is not in the key. `run_pull` restamps `as_of` to the day of the
attempt whenever any query is owed — including when every one of them then
FAILS and no byte is written. See the test for the measured consequence.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from f1s2_support import (
    DERIVE_PATH,
    PLAN_ARGS,
    SEARCH_BODY,
    SEARCH_PATH,
    TODAY,
    curation,
    record,
)


def _doms(response) -> list[int]:
    return sorted(comp["effective_dom"] for comp in response.json()["comps"])


def test_reopening_a_workspace_sees_evidence_that_arrived_after_the_memo_was_warmed(
    api, pull, fixture_mode, clear_caches
) -> None:
    """A completed resume must reach a workspace reopened in the same process.

    §5a's "a pull interrupted at 3/4 costs exactly 1 call to finish" appends
    new evidence *under the existing ref*. A memo that ignored content would
    keep serving the pre-completion comps forever, and a missing cohort skews
    every number the reopened workspace shows — silently, since the workspace
    itself restores perfectly.

    Expected GREEN on `main` (F4-S9's `_evidence_version`); this is the
    regression pin for the F1 flow that depends on it.
    """
    from rentcomp.client.planner import fetchable_queries, plan_pull_queries
    from rentcomp.client.rentcast import fixture_signature

    before = api.post(DERIVE_PATH, json=curation(pull.ref))
    assert before.status_code == 200, before.text[:300]
    count_before = len(before.json()["comps"])

    # A new raw response appears under the SAME ref, exactly as completing a
    # partial pull would file it.
    query = fetchable_queries(plan_pull_queries(TODAY, **PLAN_ARGS))[0]
    from rentcomp.storage.cache import write_raw_response

    write_raw_response(
        pull.ref,
        f"{fixture_signature(query.params)[:8]}-extra-off000",
        json.dumps([record(90), record(91)]).encode("utf-8"),
        meta={"sig": "extra", "offset": 0},
    )

    after = api.post(DERIVE_PATH, json=curation(pull.ref))
    assert after.status_code == 200, after.text[:300]

    assert len(after.json()["comps"]) > count_before, (
        f"evidence appended under {pull.ref} is invisible to a derive in the same process "
        f"({count_before} comps before and after). A workspace reopened after a resume would "
        "keep showing the pre-completion set — every statistic short by a cohort, with nothing "
        "on screen to say so."
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "QA FINDING (F1-S2, reported to the PM, not fixed here). `run_pull` restamps the "
        "manifest's `as_of` to the day of the attempt whenever a query is owed — including "
        "when every one of them FAILS and no byte is written — while `storage/pulls.py`'s memo "
        "key (`_evidence_version`) covers only the raw files. Same disk, two answers: measured "
        "on 2026-07-28, effective_dom 150-155 before a restart and 180-185 after, for evidence "
        "that never changed. Strict, so it FAILS the day the gap is closed and demands its own "
        "removal rather than decaying into a note. PM to rule whether the fix is the restamp "
        "(as_of should only advance when bytes actually arrive) or the memo key."
    ),
)
def test_a_reopened_workspace_derives_the_same_numbers_before_and_after_a_restart(
    api, pull, fixture_mode, clear_caches
) -> None:
    """"Restored exactly as last left" has to survive closing the tool.

    The F1 story is explicitly about coming back "...weeks later", so the
    process boundary is the normal case, not an edge. Here the same workspace,
    over the same unchanged bytes, derives one set of numbers while the memo is
    warm and a different set after a restart — because a failed refresh moved
    the manifest's `as_of` forward and only one of the two paths noticed.

    The failure is a 30-day shift in every `effective_dom`: a censored comp's
    DOM-so-far is measured from `as_of`, and `as_of` moved without any
    re-observation of the market.
    """
    warm = api.post(DERIVE_PATH, json=curation(pull.ref))
    assert warm.status_code == 200, warm.text[:300]
    doms_warm = _doms(warm)

    # A refresh attempt a month later in which every query fails: the saved
    # responses are gone, so nothing is fetched and nothing is written.
    for saved in fixture_mode.glob("*.json"):
        saved.unlink()
    raw_before = pull.raw_stat_snapshot()

    from rentcomp.client.pull import run_pull

    outcome = run_pull(**PLAN_ARGS, today=TODAY + timedelta(days=30), force_refresh=True)
    assert outcome.complete is False and pull.raw_stat_snapshot() == raw_before, (
        "the setup for this test did not hold: the refresh attempt was supposed to fetch "
        "nothing and write nothing"
    )

    clear_caches()  # the restart
    cold = api.post(DERIVE_PATH, json=curation(pull.ref))
    assert cold.status_code == 200, cold.text[:300]

    assert _doms(cold) == doms_warm, (
        f"the same workspace over the same bytes derives {_doms(cold)} after a restart and "
        f"{doms_warm} before it. Nothing about the evidence changed — a refresh that fetched "
        "nothing moved the manifest's `as_of`, and `effective_dom` is measured from `as_of`."
    )


def test_the_search_route_still_reports_the_pull_that_is_actually_on_disk(api, pull) -> None:
    """Whatever the memo holds, `POST /api/search` answers off the manifest.

    The safety net under the finding above: a user reopening a search is told
    what the entry really contains (`pull_status` reads the manifest every
    time, no memo), so the gap is confined to the derived numbers.
    """
    response = api.post(SEARCH_PATH, json=SEARCH_BODY)

    assert response.status_code == 200, response.text[:300]
    assert response.json()["pull_ref"] == pull.ref
    assert response.json()["cache_status"] in ("hit", "stale")
    assert response.json()["calls_spent"] == 0
