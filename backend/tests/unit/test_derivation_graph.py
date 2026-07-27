"""F0-S2 [INVARIANT] — the derivation graph's structure: the stage interface,
the union that cannot hold both branches, and the guards that make
idempotence and D19a's feature/target separation properties of the CALL GRAPH
rather than promises in a code review. QA-authored, written RED before any
implementation exists (AGENT_QA.md protocol).

WHY THESE ARE LAYER 1
---------------------
AGENT_QA.md's decision procedure sends an AC to Layer 1 when it can be
asserted by importing a function and calling it with plain values. Three
kinds of thing land here:

* **Structural guards over source** (AST scans). "``effective_dom`` must never
  appear in ``distance()``" (D19a, F11-S1's AC) and "the wall clock is not
  reachable from the pipeline" (ADR-001 §4.1, owner ruling 1) are not
  observable from a browser and barely observable over HTTP — a runtime test
  can only sample the inputs and clocks it happens to try. A source guard is
  total. These are QA-owned by AGENT_QA.md's own example, and they PASS
  VACUOUSLY today: they exist to trip the first time a later story reaches for
  the forbidden thing. (Same technique, and the same reason, as F0-S5's
  existing ``test_derivation_modules_never_read_config_from_disk``.)
* **Type-level exclusivity.** ADR-001 §1.2 makes F11-S3's "guard state and
  curve state are mutually exclusive by construction" a fact of the schema.
  The honest test of that is "``CurveResult`` has no ``reason`` field and
  ``GuardResult`` has no ``curve`` field", not a runtime probe of one sampled
  response.
* **Stage signatures.** ADR-001 §2.2: "a stage declares what it needs by its
  parameter list... a function that was not handed ``effective_dom`` cannot
  reach it." The signature IS the enforcement mechanism, so pinning it is
  pinning the invariant, not pinning a [DEFAULT] technique.

SCOPE: F0-S2 builds the graph, not the statistics. Nothing here asserts a
median, an anchor, a neighbour ordering or a survival probability — those
belong to F4-S3/F4-S5/F8-S1/F10-S1/F11-S1/F11-S2/F11-S3. Where a stage will
be a stub in F0-S2, this file tests that the SEAM is right.

MODULE PATHS PINNED HERE (from ADR-001 §1.1/§1.2/§2.2 where the ADR names
them; QA-PROPOSED where it does not — flagged per item in the handoff):
  rentcomp.models.requests   DeriveRequest, Filters, Subject      (ADR §1.1)
  rentcomp.models.responses  DerivedState, DerivedComp, ...       (ADR §1.2)
  rentcomp.pipeline.derive   derive, DeriveContext, drift_band,
                             DRIFT_SENSITIVITY_PTS                (ADR §2.2)
  rentcomp.pipeline.premium  compute_premiums                     (ADR §2.2)
  rentcomp.stats.knn         select_neighbors                     (ADR §2.2)
  rentcomp.pipeline.keys     comp_key                             (PROPOSED)

EXPECTED RED STATE TODAY: ``test_derivation_graph_modules_exist`` FAILS with
the ImportError detail; the AST guards PASS (vacuously); every other test
SKIPS until the modules exist, then runs for real.
"""

from __future__ import annotations

import ast
import inspect
import typing
from pathlib import Path

import pytest

BACKEND_SRC = Path(__file__).resolve().parents[2] / "src" / "rentcomp"

try:
    from rentcomp.models.requests import DeriveRequest, Filters, Subject
    from rentcomp.models.responses import (
        Anchor,
        Band,
        Breakdown,
        BucketStat,
        CohortStat,
        CurveResult,
        DeriveMeta,
        DerivedComp,
        DerivedState,
        GuardResult,
        Neighbor,
    )
    from rentcomp.pipeline.derive import (
        DRIFT_SENSITIVITY_PTS,
        DeriveContext,
        derive,
        drift_band,
    )
    from rentcomp.pipeline.keys import comp_key
    from rentcomp.pipeline.premium import compute_premiums
    from rentcomp.stats.knn import select_neighbors

    _IMPORT_ERROR: Exception | None = None
except ImportError as _exc:  # pragma: no cover - red state before F0-S2 lands
    _IMPORT_ERROR = _exc

needs_graph = pytest.mark.skipif(
    _IMPORT_ERROR is not None,
    reason=f"the derivation graph is not importable yet (F0-S2 red): {_IMPORT_ERROR}",
)


def test_derivation_graph_modules_exist() -> None:
    """Legible red: one failure that names exactly what is missing."""
    assert _IMPORT_ERROR is None, (
        "Cannot import the derivation graph pinned by ADR-001 §1.1/§1.2/§2.2 "
        f"(see this module's docstring for the path table): {_IMPORT_ERROR}"
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def literal_values(annotation: typing.Any) -> set[typing.Any]:
    """Every ``Literal`` member anywhere inside an annotation."""
    found: set[typing.Any] = set()
    origin = typing.get_origin(annotation)
    if origin is typing.Literal:
        found.update(typing.get_args(annotation))
        return found
    for argument in typing.get_args(annotation):
        found |= literal_values(argument)
    return found


def as_triple(value: typing.Any) -> list[float]:
    """Normalize a drift band to [low, mid, high] whether it is returned as a
    tuple or as a ``Band`` — the ADR does not pin which, and it must not
    matter to this assertion."""
    if hasattr(value, "low") and hasattr(value, "mid") and hasattr(value, "high"):
        return [value.low, value.mid, value.high]
    return list(value)


def python_sources(*packages: str) -> list[Path]:
    files: list[Path] = []
    for package in packages:
        directory = BACKEND_SRC / package
        if directory.is_dir():
            files.extend(sorted(directory.rglob("*.py")))
    return files


def imported_modules(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def parameter_names(function: typing.Any) -> list[str]:
    return list(inspect.signature(function).parameters)


# ---------------------------------------------------------------------------
# D19a — the target can never reach the distance calculation
# ---------------------------------------------------------------------------

#: Names that carry the TARGET (`effective_dom`, censoring) rather than the
#: single FEATURE (`premium`). D19a: selecting neighbours partly by their
#: outcome is target leakage — "circular, and convincing-looking at n≈40".
TARGET_NAMES = {
    "dom",
    "doms",
    "effective_dom",
    "effective_doms",
    "days_on_market",
    "censored",
    "censoring",
    "is_censored",
    "removal_class",
    "removed",
    "leased",
    "lease_date",
    "confirmed",
    "provisional",
    "pending",
}


def test_the_knn_module_cannot_reach_the_outcome() -> None:
    """D19a + F11-S1's AC ("a test asserts DOM is not reachable from
    ``distance()``'s inputs"), asserted over the whole retrieval module rather
    than one function, because a helper in the same file is just as good a
    leak. Vacuous until ``stats/knn.py`` exists; that is the point."""
    module = BACKEND_SRC / "stats" / "knn.py"
    if not module.is_file():
        pytest.skip("stats/knn.py does not exist yet — this guard trips when it does")

    tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in TARGET_NAMES:
            offenders.append(f"name {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in TARGET_NAMES:
            offenders.append(f"attribute .{node.attr}")
        elif isinstance(node, ast.arg) and node.arg in TARGET_NAMES:
            offenders.append(f"parameter {node.arg}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in TARGET_NAMES:
            offenders.append(f"string {node.value!r}")

    assert not offenders, (
        "stats/knn.py can reach the kNN TARGET (D19a): " + "; ".join(sorted(set(offenders)))
        + ". Distance uses `premium` only; the (effective_dom, censored) pair goes to the "
        "KM estimator, never to retrieval."
    )
    assert "rentcomp" not in {
        alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0
    }, (
        "stats/knn.py imports a rentcomp model. ADR-001 §2.2 keeps retrieval on plain values "
        "precisely so that a comp record — which carries the target — is not in scope at all."
    )


def test_no_distance_function_anywhere_takes_an_outcome_parameter() -> None:
    """Belt and braces: wherever ``distance()`` ends up living, its parameter
    list may not mention the target."""
    offenders: list[str] = []
    for source in python_sources("pipeline", "stats"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("distance"):
                for argument in node.args.args + node.args.kwonlyargs:
                    if argument.arg in TARGET_NAMES:
                        offenders.append(f"{source.name}:{node.name}({argument.arg})")
    assert not offenders, "distance() is being handed the target (D19a): " + "; ".join(offenders)


# ---------------------------------------------------------------------------
# idempotence, structurally (ADR-001 §4.1/§4.2, owner ruling 1)
# ---------------------------------------------------------------------------

CLOCK_ATTRIBUTES = {"today", "now", "utcnow", "fromtimestamp", "monotonic", "perf_counter", "timestamp"}
CLOCK_MODULES = {"time"}


def test_pipeline_and_stats_cannot_read_the_wall_clock() -> None:
    """Owner ruling 1, structurally: ``as_of`` is DATA (the pull manifest's
    fetch date), not a clock reading. Without this, every censored comp's DOM
    floor drifts a day at midnight, removals silently re-classify across the
    pending→provisional→confirmed ladder with no new evidence, and AC1
    (identical body ⇒ identical response) is untestable.

    ``date``/``datetime`` may still be imported as TYPES — only reading the
    current time is forbidden."""
    offenders: list[str] = []
    for source in python_sources("pipeline", "stats"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for module in imported_modules(tree) & CLOCK_MODULES:
            offenders.append(f"{source.name}: imports {module}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in CLOCK_ATTRIBUTES:
                offenders.append(f"{source.name}: .{node.attr}()")
            elif isinstance(node, ast.Name) and node.id in {"utcnow", "today"}:
                offenders.append(f"{source.name}: {node.id}")
    assert not offenders, (
        "the wall clock is reachable from the derivation pipeline: "
        + "; ".join(sorted(set(offenders)))
        + ". as_of comes from the pull manifest and is carried in DeriveContext (owner ruling 1)."
    )


IO_ATTRIBUTES = {
    "read_text",
    "read_bytes",
    "write_text",
    "write_bytes",
    "iterdir",
    "rglob",
    "mkdir",
    "unlink",
    "getenv",
    "environ",
}
IO_MODULES = {"os", "pathlib", "httpx", "requests", "urllib", "socket", "fastapi", "sqlite3"}


def test_pipeline_and_stats_do_no_io() -> None:
    """ADR-001 §4.2: no I/O below the API edge. Every input arrives as an
    argument; loading happens in ``api/derive.py``. An ad-hoc read inside a
    stage is invisible to the reactivity invariant and makes two identical
    requests separable by something that is not in the request.

    Scope note: ``client/`` is deliberately NOT scanned — the fixture-mode
    RentCast client (D17/F0-S4) must read fixture files from disk. ADR-001
    §4.2's inclusion of ``client/`` in this guard is flagged to the PM as an
    erratum."""
    offenders: list[str] = []
    for source in python_sources("pipeline", "stats"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for module in imported_modules(tree) & IO_MODULES:
            offenders.append(f"{source.name}: imports {module}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in IO_ATTRIBUTES:
                offenders.append(f"{source.name}: .{node.attr}")
            elif isinstance(node, ast.Name) and node.id in {"open", "getenv"}:
                offenders.append(f"{source.name}: {node.id}()")
    assert not offenders, (
        "the derivation pipeline performs I/O or reads the environment: "
        + "; ".join(sorted(set(offenders)))
    )


# ---------------------------------------------------------------------------
# the price-test union (ADR-001 §1.2, F11-S3 [INVARIANT])
# ---------------------------------------------------------------------------


@needs_graph
def test_a_curve_result_and_a_guard_result_cannot_be_confused() -> None:
    """"There is no value in which both a curve and a guard reason exist" —
    as a fact of the type, so the exclusivity survives codegen into the
    TypeScript view (D12) instead of being a UI convention."""
    assert "reason" not in CurveResult.model_fields, (
        "CurveResult has a 'reason' field — a curve with a guard reason must be unrepresentable"
    )
    assert "curve" not in GuardResult.model_fields, (
        "GuardResult has a 'curve' field — the guard renders INSTEAD of a curve; it is not a "
        "curve with wide error bars (NORTH_STAR)"
    )
    assert "reason" in GuardResult.model_fields, "GuardResult must name why the guard tripped"
    assert "curve" in CurveResult.model_fields, "CurveResult must carry the curve"
    assert literal_values(CurveResult.model_fields["state"].annotation) == {"curve"}
    assert literal_values(GuardResult.model_fields["state"].annotation) == {"insufficient_evidence"}
    assert literal_values(GuardResult.model_fields["reason"].annotation) == {
        "too_few_in_range",
        "all_censored",
        "curve_not_available",
    }, (
        "the two guard reasons of spec §5.4, plus WS-1a's provisional third value "
        "(PM ruling, QUEUE.md row 6a: 'evidence is sufficient but no curve exists yet', "
        "since F11-S2 does not exist — retires when F11-S2/F11-S3's guard-vs-curve branch "
        "selection lands for real) — not a spec change, an honesty fix for a wire contract "
        "with no truthful value to report in that state"
    )


@needs_graph
def test_the_guard_sees_the_whole_drift_band() -> None:
    """Owner ruling 2 — the guard trips if it trips at ANY of drift−2 / drift
    / drift+2. F11-S3 owns the trip behaviour; what F0-S2 must guarantee is
    that the decision has all three in hand, i.e. the candidate premium
    reaches the price test as a band and not as a centre point."""
    for model in (CurveResult, GuardResult):
        annotation = model.model_fields["candidate_premium"].annotation
        assert typing.get_origin(annotation) is Band or annotation is Band or Band in getattr(
            annotation, "__mro__", ()
        ), (
            f"{model.__name__}.candidate_premium is {annotation!r}, not a Band — a centre-only "
            "candidate premium makes the owner's any-edge guard rule unrepresentable"
        )


# ---------------------------------------------------------------------------
# frozen models (ADR-001 §4.3) and the request contract (§1.1)
# ---------------------------------------------------------------------------


@needs_graph
def test_the_request_is_frozen_and_forbids_extra_fields() -> None:
    assert DeriveRequest.model_config.get("frozen") is True, (
        "DeriveRequest is not frozen — a stage could mutate the request and leave a footprint"
    )
    assert DeriveRequest.model_config.get("extra") == "forbid", (
        "DeriveRequest accepts extra fields; 'selections' (a second source of truth for "
        "selection, F5-S2) must be rejected rather than ignored"
    )
    assert "selections" not in DeriveRequest.model_fields, (
        "DeriveRequest has a 'selections' field — selection IS the weight (F5-S2 [INVARIANT])"
    )
    for field in ("pull_ref", "subject", "weights", "include_overrides", "filters", "drift_pct", "candidate_rent"):
        assert field in DeriveRequest.model_fields, f"DeriveRequest is missing '{field}' (ADR-001 §1.1)"


@needs_graph
@pytest.mark.parametrize("model_name", ["DerivedState", "DerivedComp", "Anchor", "BucketStat", "Breakdown"])
def test_response_models_are_frozen(model_name: str) -> None:
    """ADR-001 §4.3: everything frozen, so a stage cannot mutate an upstream
    artifact and leave a footprint for the next request."""
    model = {
        "DerivedState": DerivedState,
        "DerivedComp": DerivedComp,
        "Anchor": Anchor,
        "BucketStat": BucketStat,
        "Breakdown": Breakdown,
    }[model_name]
    assert model.model_config.get("frozen") is True, f"{model_name} is not frozen"


@needs_graph
def test_the_removal_ladder_is_named_confirmed_not_leased() -> None:
    """PM ruling: ARCHITECTURE §3's ``leased`` is an erratum. ``None`` means
    still active — censored is not a kind of leased."""
    values = literal_values(DerivedComp.model_fields["removal_class"].annotation)
    assert values == {"pending", "provisional", "confirmed"}, (
        f"removal_class literals are {sorted(values)}; the ladder is pending/provisional/confirmed"
    )
    assert typing.get_origin(DerivedComp.model_fields["removal_class"].annotation) is typing.Union or type(
        None
    ) in typing.get_args(DerivedComp.model_fields["removal_class"].annotation), (
        "removal_class must be optional — a still-active comp has no removal class at all"
    )


@needs_graph
def test_premium_and_psf_are_optional_on_the_wire() -> None:
    """ADR-001 §1.2: ``premium: float | None`` is first class (14.7% of the
    real pull has no sqft). Never a silent zero."""
    for field in ("psf", "premium", "bucket"):
        assert type(None) in typing.get_args(DerivedComp.model_fields[field].annotation), (
            f"DerivedComp.{field} is not Optional — a comp with no sqft would have to be given "
            "a fabricated value"
        )


# ---------------------------------------------------------------------------
# stage seams (ADR-001 §2.2)
# ---------------------------------------------------------------------------


@needs_graph
def test_the_orchestrator_is_a_plain_function_of_request_and_context() -> None:
    """"The orchestrator is a literal function — the wiring is the diagram."
    Two parameters: the request, and everything the pipeline is allowed to
    know (config, comps, as_of). No registry, no discovery, no globals."""
    assert inspect.isfunction(derive), f"pipeline.derive.derive is {type(derive)!r}, expected a function"
    assert len(parameter_names(derive)) == 2, (
        f"derive() takes {parameter_names(derive)}; ADR-001 §2.2 pins derive(req, ctx)"
    )
    for field in ("config", "comps", "as_of"):
        assert hasattr(DeriveContext, field) or field in getattr(DeriveContext, "model_fields", {}) or field in getattr(
            DeriveContext, "__dataclass_fields__", {}
        ) or field in getattr(DeriveContext, "__annotations__", {}), (
            f"DeriveContext does not carry '{field}' — as_of in particular must arrive as data, "
            "not be read from a clock (owner ruling 1)"
        )


@needs_graph
def test_premium_is_a_per_request_stage_computed_against_cohort_medians() -> None:
    """ADR-001 §2.1's one genuine conflict and its ruling: ``psf`` is record
    shaping (once per pull), ``premium`` is curation derivation (per request),
    because F4-S5 [INVARIANT] defines premium against the cohort median over
    SELECTED comps — so it changes when the user toggles a comp. A
    ``compute_premiums`` that did not take cohort medians as an argument would
    be in the wrong half of the pipeline."""
    parameters = parameter_names(compute_premiums)
    assert len(parameters) == 3, f"compute_premiums{tuple(parameters)}; ADR-001 §2.2 pins three inputs"
    assert any("median" in name for name in parameters), (
        f"compute_premiums{tuple(parameters)} does not take cohort medians — premium would then "
        "be computable once per pull, which contradicts F4-S5"
    )
    assert not (set(parameters) & TARGET_NAMES), (
        f"compute_premiums{tuple(parameters)} is being handed outcome data"
    )

    # None propagation, not the formula: a comp with no psf has no premium.
    result = compute_premiums([None, 2.0], [2025, 2025], {2025: 2.0})
    assert result[0] is None, (
        f"compute_premiums returned {result[0]!r} for a comp with no $/sqft — it must be None, "
        "never 0.0 (a fabricated 'exactly at market' comp)"
    )
    assert result[1] == pytest.approx(0.0), (
        "a comp whose psf equals its cohort median has premium 0.0 by definition"
    )


@needs_graph
def test_neighbour_selection_takes_premiums_and_nothing_else() -> None:
    """F11-S1's AC as a signature. Enforced by Python's own argument binding:
    a function that was not handed ``effective_dom`` cannot reach it."""
    parameters = parameter_names(select_neighbors)
    assert not (set(parameters) & TARGET_NAMES), (
        f"select_neighbors{tuple(parameters)} accepts outcome data (D19a target leakage)"
    )
    assert any("premium" in name for name in parameters), (
        f"select_neighbors{tuple(parameters)} does not take premiums — premium is the only "
        "feature distance may use (D19a)"
    )


# ---------------------------------------------------------------------------
# drift band (F8-S2 [INVARIANT], PM ruling on ±2)
# ---------------------------------------------------------------------------


@needs_graph
def test_the_drift_band_is_minus_two_centre_plus_two() -> None:
    assert as_triple(drift_band(7.0, 2.0)) == [pytest.approx(5.0), pytest.approx(7.0), pytest.approx(9.0)]
    assert as_triple(drift_band(0.0, 2.0)) == [pytest.approx(-2.0), pytest.approx(0.0), pytest.approx(2.0)], (
        "a negative low edge is legal — drift is an assumption about market movement, and "
        "markets go down"
    )


@needs_graph
def test_drift_sensitivity_is_a_module_constant_not_a_config_knob() -> None:
    """PM ruling: ±2 points is a module constant, NOT an F0-S5 knob — one
    fewer thing to keep coherent."""
    assert DRIFT_SENSITIVITY_PTS == pytest.approx(2.0)

    from rentcomp.storage.config import Config

    knobs = [name for name in Config.model_fields if "sensitiv" in name or "drift_pct" in name]
    assert not knobs, (
        f"Config exposes {knobs} — the PM ruled drift sensitivity is a module constant, not a knob"
    )


@needs_graph
def test_config_can_key_the_record_shaping_memo() -> None:
    """ADR-001 §3.1: ``Config`` must be hashable (and genuinely immutable) or
    the record-shaping memo of §3 — keyed on pull ref + digest + Config +
    as_of — raises ``TypeError`` at runtime. Pydantic's ``frozen=True`` blocks
    rebinding a field, not mutation of a mutable field VALUE, so a list-valued
    knob is a half-truth: ``cfg.km_horizons_days.append(90)`` would change
    derived output with no re-derive signal and nothing for the memo key to
    notice."""
    from rentcomp.storage.config import Config

    assert hash(Config()) == hash(Config()), "equal Configs must hash equally"
    assert isinstance(Config().km_horizons_days, tuple), (
        "km_horizons_days is still a list: Config is unhashable and mutable-in-place "
        "(ADR-001 §3.1 — same values, same JSON, same TS type; implementation-only)"
    )


# ---------------------------------------------------------------------------
# naming (PM ruling 2026-07-27, relayed with the F0-S4 dispatch)
# ---------------------------------------------------------------------------


def test_the_weights_module_exports_contribution_shares_not_pcts() -> None:
    """The producer half of the ``contribution_pct`` -> ``contribution_share``
    rename. Pinned at the symbol so the function and the wire field cannot end
    up disagreeing about which unit they are in — the response field is
    asserted in ``tests/api/test_derive_contract.py``.

    Rename only: the arithmetic (``weight / total``, shares sum to one) is
    unchanged and still covered by the developer's own unit tests.
    """
    try:
        from rentcomp.pipeline import weights
    except ImportError as exc:  # pragma: no cover - F0-S2 landed, so this is real breakage
        pytest.fail(f"cannot import rentcomp.pipeline.weights: {exc}")

    assert hasattr(weights, "contribution_shares"), (
        "rentcomp.pipeline.weights has no 'contribution_shares' — the PM ruled "
        "contribution_pcts() renames to contribution_shares() (ratio semantics kept)"
    )
    assert not hasattr(weights, "contribution_pcts"), (
        "'contribution_pcts' still exists — the old name must be removed, not aliased; "
        "an alias keeps the '_pct means percentage points' collision alive"
    )
    assert "contribution_pcts" not in getattr(weights, "__all__", ()), (
        "the old name is still exported in __all__"
    )

    # the rename must not have moved the number: three equal weights, all
    # included, still split the influence three ways.
    shares = weights.contribution_shares([1.0, 1.0, 1.0], [True, True, True])
    assert [round(s, 6) for s in shares] == [round(1 / 3, 6)] * 3, (
        f"contribution_shares returned {shares!r} — the ruling was rename-only; a value of "
        "33.3 would mean the unit changed with the name"
    )


# ---------------------------------------------------------------------------
# comp identity (F13-S1 [INVARIANT])
# ---------------------------------------------------------------------------


class TestProposedContract:
    """QA-proposed contract that ADR-001 does not spell out. The PM/owner
    confirms (or overrides) these before they count as locked — the repo
    convention established by F0-S3/F0-S5."""

    @needs_graph
    def test_comp_key_is_derived_from_address_and_unit(self) -> None:
        """F13-S1 [INVARIANT]: "re-key selections/weights by normalized
        address+unit — ids can churn." Pinned as an importable normalizer so
        that the wire keys, the workspace keys and the refresh re-keying are
        provably the same function rather than three lookalikes.

        PROPOSED: the module path ``rentcomp.pipeline.keys.comp_key`` and the
        two-argument signature. Normalization RULES (what counts as the same
        address) belong to F13-S1; only case- and whitespace-insensitivity is
        asserted here, because a key that is sensitive to those cannot survive
        a refresh at all."""
        assert parameter_names(comp_key) == ["address", "unit"], (
            f"comp_key{tuple(parameter_names(comp_key))} — the key is a function of address and "
            "unit ONLY; anything else in the signature is something that can churn"
        )
        assert comp_key("1234 W Fake St", "2") == comp_key("  1234 w   fake st ", "2"), (
            "comp keys are case/whitespace sensitive — the same listing would key differently "
            "across two pulls"
        )
        assert comp_key("1234 W Fake St", "2") != comp_key("1234 W Fake St", "3"), (
            "two units at one address collapse to the same key"
        )
        assert comp_key("1234 W Fake St", None) != comp_key("1234 W Fake St", "2")
