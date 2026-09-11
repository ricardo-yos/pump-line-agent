"""
tools.py

The @tool decorator (builds each tool's JSON schema from its signature,
same idea as the reference architecture's Episode 2) plus the agent-
facing tool functions themselves. This file holds no pump physics -
that lives in pump_sizing.py (and will hold no pipe physics either,
once piping is added - see pipe_sizing.py). tools.py is just the thin,
JSON-serializable surface the agent dispatches by name; the actual
engineering decision pipeline is importable and testable on its own.

Tool-call telemetry lives here too, next to the decorator that records
it: every call to a @tool-decorated function is logged to
TOOL_CALL_LOG (round, name, args, result size), and write_tool_telemetry()
dumps that log to tool_calls.jsonl. agent.py tags each round by setting
tools_module.CURRENT_ROUND before calling the model - see agent.py's
import of this module.
"""
import inspect
import json
import time
from typing import Callable, get_type_hints

from pump_catalog import PUMP_CATALOG
from pump_sizing import list_candidates, evaluate_candidate, list_candidates_against_system
from pump_catalog import list_pumps
from pipe_sizing import (viable_pipe_diameters, system_curve, two_leg_system_curve,
                          npsh_available, reynolds_number, friction_factor, head_loss_friction_m)


# --- @tool: registers a function, builds its schema from the signature +
# docstring, and wraps it so every call is recorded to TOOL_CALL_LOG.
# Params with a default are optional; Optional[T] (T | None) is unwrapped
# to T for the schema's "type".
TOOLS: list[Callable] = []
TOOL_CALL_LOG: list[dict] = []
CURRENT_ROUND = 0  # set by agent.py's run_agent before each model call
_TYPE_MAP = {str: "string", float: "number", int: "integer", bool: "boolean"}


def tool(func: Callable) -> Callable:
    sig = inspect.signature(func)
    hints = get_type_hints(func)
    properties, required = {}, []

    for name, param in sig.parameters.items():
        py_type = hints.get(name, str)
        if get_origin_is_union_with_none(py_type):
            py_type = [t for t in py_type.__args__ if t is not type(None)][0]
        properties[name] = {"type": _TYPE_MAP.get(py_type, "string")}
        if param.default is inspect.Parameter.empty:
            required.append(name)

    def wrapped(*args, **kwargs):
        result = func(*args, **kwargs)
        TOOL_CALL_LOG.append({
            "round": CURRENT_ROUND,
            "tool": func.__name__,
            "args": kwargs,
            "result_chars": len(result) if isinstance(result, str) else None,
            "timestamp": time.time(),
        })
        return result

    wrapped.__name__ = func.__name__
    wrapped.tool_definition = {
        "type": "function",
        "function": {
            "name": func.__name__,
            "description": (func.__doc__ or "").strip().split("\n")[0],
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }
    TOOLS.append(wrapped)
    return wrapped


def get_origin_is_union_with_none(py_type) -> bool:
    return hasattr(py_type, "__args__") and type(None) in py_type.__args__


def write_tool_telemetry(path: str = "tool_calls.jsonl"):
    """Dumps TOOL_CALL_LOG to a JSONL file, one call per line. Called by
    agent.py's main() at the end of a run - recording only, no side
    effects on the agent loop itself."""
    with open(path, "w", encoding="utf-8") as f:
        for entry in TOOL_CALL_LOG:
            f.write(json.dumps(entry) + "\n")


def _r(x, n: int = 3):
    """Rounds a float to n decimals; passes through anything else. Extra
    digits past engineering precision are just tokens spent for nothing
    once a number goes into a tool result the model has to read - keeps
    payloads lean regardless of provider limits."""
    return round(x, n) if isinstance(x, float) else x


def _strip_none(obj):
    """Recursively drops dict keys whose value is None, and rounds every
    float to 3 decimals, before serializing. Rounding lives here rather
    than in pump_sizing.py's PumpEvaluation.to_dict() (which intentionally
    returns full precision - a prior project decision) so the domain
    layer stays precise and this boundary is the one place payload size
    is trimmed for whichever tool is about to hand data to the model.
    Repeated across many candidates (a grid's combos, a browse page),
    unrounded floats (15+ significant digits each) are the single
    biggest driver of tool-result token bloat - much bigger than the
    number of candidates/combos itself."""
    if isinstance(obj, dict):
        return {k: _strip_none(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_strip_none(v) for v in obj]
    if isinstance(obj, float):
        return round(obj, 3)
    return obj


def _compact_dumps(obj) -> str:
    """json.dumps with no extra whitespace (default separators pad with a
    space after ',' and ':') - a small but free token saving on top of
    _strip_none's None-dropping and float-rounding, applied to every
    tool's output."""
    return json.dumps(_strip_none(obj), separators=(",", ":"))


# ---------------------------------------------------------------------------
# @tool functions - agent-facing surface (JSON-serializable in/out).
# Pump physics lives in pump_sizing.py; these are thin adapters.
# ---------------------------------------------------------------------------
def _categorize_rejection(reason: str) -> str:
    """Groups a rejection reason string into a coarse category, for
    summarizing rejections when the catalog is large the agent gets 
    counts per category instead of every individual rejection."""
    if reason.startswith("Q_target="):
        return "outside_flow_range"
    if "< required" in reason:
        return "insufficient_head"
    if "No operating point" in reason:
        return "no_operating_point_in_range"
    if "NPSH margin" in reason:
        return "npsh_margin_too_low"
    return "other"


@tool
def select_pump(Q_target_m3h: float, H_required_m: float, NPSHa_m: float = None) -> str:
    """Evaluates every pump in the catalog covering the target flow and returns every candidate that passes (head requirement, and NPSH margin if NPSHa_m is given) with its real operating point, power, and NPSH margin - no automatic 'best' pick. Choosing among the passing candidates (e.g. lowest power vs. other engineering trade-offs) is left to you."""
    evals = list_candidates(Q_target_m3h, H_required_m, NPSHa_m)
    passed = [e for e in evals if e.passed]
    rejected = [e for e in evals if not e.passed]

    rejected_summary = {}
    for e in rejected:
        cat = _categorize_rejection(e.reason)
        rejected_summary.setdefault(cat, {"count": 0, "example": e.reason})
        rejected_summary[cat]["count"] += 1

    return _compact_dumps({
        "passing_candidates": [e.to_dict() for e in passed],
        "rejected_summary": rejected_summary,
    })


@tool
def evaluate_pump(model_id: str, Q_target_m3h: float, H_required_m: float, NPSHa_m: float = None) -> str:
    """Evaluates a single pump (by catalog id) against a target flow and required head. Useful to inspect one candidate in detail, e.g. to explain a rejection."""
    return _compact_dumps(evaluate_candidate(model_id, Q_target_m3h, H_required_m, NPSHa_m).to_dict())


@tool
def list_catalog_candidates(Q_target_m3h: float) -> str:
    """Lists catalog pump ids whose curve covers a given target flow, without evaluating H/P/NPSH."""
    return _compact_dumps({"candidates": list_pumps(Q_target_m3h)})


# ---------------------------------------------------------------------------
# Stage 2: piping. size_pipe is a deterministic utility (velocity/head-loss
# math has no judgment call in it, per project convention - see pipe_sizing.py).
#
# compute_pipe_pump_grid + browse_candidates replace what used to be a single
# tool (compare_pipe_pump_options) that returned every passing candidate for
# every diameter in one shot. That doesn't scale: as the pump catalog grows,
# either the payload grows unboundedly (bad for token limits) or candidates
# get truncated - but a truncated list hides model_ids the agent never sees,
# so it can't ask for detail on options outside the truncation window.
# Splitting into "compute once, browse in pages" fixes that: computation
# (cheap, in-process) can grow with the catalog freely, while what actually
# goes in the model's context per call stays a small, controllable page - the
# agent pages through with sort_by instead of needing to already know an id.
#
# Suction and discharge are modeled as independent legs (own diameter, own
# length each), not one shared pipe - see pipe_sizing.py's module docstring
# for why: friction loss on the suction side is what erodes NPSHa_m, so
# collapsing the two into one diameter would make NPSHa_m blind to a choice
# (a bigger suction pipe) that's specifically used to protect it in practice.
# This does mean the grid is diameter combinations (suction x discharge), not
# a single list - still small and catalog-size-independent, since the number
# of viable diameters is bounded by the physical velocity range, not by how
# many pumps exist.
#
# LAST_PIPE_PUMP_GRID holds the full computed result in process state (same
# pattern as a plan living outside message history), valid for the current
# agent run only.
# ---------------------------------------------------------------------------
LAST_PIPE_PUMP_GRID: dict = {}


@tool
def size_pipe(Q_target_m3h: float, static_head_m: float, length_m: float,
              v_min: float = 1.0, v_max: float = 2.5) -> str:
    """Computes every commercial PEAD pipe diameter whose velocity at Q_target_m3h falls within [v_min, v_max] m/s, with Reynolds number, friction factor/regime, friction head loss, and total system head (static + friction) for a single straight run - no fittings/local losses, no suction/discharge distinction. No pump involved. Use compute_pipe_pump_grid for a real project with separate suction and discharge legs and pump selection."""
    options = []
    for p in viable_pipe_diameters(Q_target_m3h, v_min, v_max):
        Re = reynolds_number(Q_target_m3h, p["internal_mm"])
        f, regime = friction_factor(Re, p["internal_mm"])
        h_f = head_loss_friction_m(Q_target_m3h, p["internal_mm"], length_m)
        options.append({
            "pipe_id": p["pipe_id"], "internal_mm": p["internal_mm"], "external_mm": p["external_mm"],
            "velocity_ms": _r(p["velocity_ms"]),
            "reynolds": _r(Re, 0),
            "friction_factor": _r(f, 4),
            "flow_regime": regime,
            "friction_head_loss_m": _r(h_f),
            "system_head_m": _r(static_head_m + h_f),
        })
    return _compact_dumps({"Q_target_m3h": Q_target_m3h, "static_head_m": static_head_m,
                            "length_m": length_m, "pipe_options": options})


@tool
def compute_pipe_pump_grid(Q_target_m3h: float, static_head_total_m: float,
                            suction_length_m: float, discharge_length_m: float,
                            suction_static_head_m: float = None,
                            v_min: float = 1.0, v_max: float = 2.5) -> str:
    """Computes every viable (suction diameter, discharge diameter) combination and evaluates every pump against each combination's real system curve (static head + suction friction + discharge friction, not a flat H). If suction_static_head_m is given, also computes the real NPSHa_m (available NPSH) at the pump suction for each combination and gates candidates on NPSH margin - positive suction_static_head_m means the source is above the pump (flooded suction, better for NPSH), negative means the source is below it (suction lift, worse for NPSH). Without suction_static_head_m, NPSH is not checked at all - ask for it if NPSH margin matters for this system. Caches the full result for browse_candidates and returns a compact summary per combination: velocities, system head, NPSHa_m if computed, how many pumps passed/were rejected, and a preview. Call browse_candidates afterward for more candidates."""
    global LAST_PIPE_PUMP_GRID
    LAST_PIPE_PUMP_GRID = {"Q_target_m3h": Q_target_m3h, "combos": {}}

    suction_options = viable_pipe_diameters(Q_target_m3h, v_min, v_max)
    discharge_options = viable_pipe_diameters(Q_target_m3h, v_min, v_max)

    summary = []
    for sp in suction_options:
        for dp in discharge_options:
            if sp["internal_mm"] < dp["internal_mm"]:
                # A suction pipe narrower than discharge is physically
                # atypical (suction is sized same or larger, never
                # smaller, to protect NPSH margin) - skipping these also
                # keeps the grid roughly half the size, which matters for
                # providers with tight per-minute token caps.
                continue
            combo_key = f"{sp['pipe_id']}|{dp['pipe_id']}"
            H_sys = two_leg_system_curve(
                static_head_total_m,
                sp["internal_mm"], suction_length_m,
                dp["internal_mm"], discharge_length_m,
            )

            npsha = None
            if suction_static_head_m is not None:
                npsha = npsh_available(suction_static_head_m, sp["internal_mm"], suction_length_m, Q_target_m3h)

            evals = list_candidates_against_system(H_sys, npsha)
            passed = [e for e in evals if e.passed]
            rejected = [e for e in evals if not e.passed]

            rejected_summary = {}
            for e in rejected:
                cat = _categorize_rejection(e.reason)
                rejected_summary.setdefault(cat, {"count": 0, "example": e.reason})
                rejected_summary[cat]["count"] += 1

            LAST_PIPE_PUMP_GRID["combos"][combo_key] = {
                "passed": passed, "rejected_summary": rejected_summary,
            }

            preview = min(passed, key=lambda e: abs(e.Q_real - Q_target_m3h)) if passed else None
            preview_dict = None
            if preview is not None:
                preview_dict = preview.to_dict()
                preview_dict["Q_deviation_pct"] = _r(abs(preview.Q_real - Q_target_m3h) / Q_target_m3h * 100)

            summary.append({
                "combo_key": combo_key,
                "suction_pipe_id": sp["pipe_id"], "suction_internal_mm": sp["internal_mm"],
                "suction_velocity_ms": _r(sp["velocity_ms"]),
                "discharge_pipe_id": dp["pipe_id"], "discharge_internal_mm": dp["internal_mm"],
                "discharge_velocity_ms": _r(dp["velocity_ms"]),
                "system_head_at_Q_target_m": _r(H_sys(Q_target_m3h)),
                "npsh_available_m": _r(npsha) if npsha is not None else None,
                "n_passed": len(passed), "rejected_summary": rejected_summary,
                "closest_to_target_preview": preview_dict,
            })

    return _compact_dumps({
        "Q_target_m3h": Q_target_m3h, "static_head_total_m": static_head_total_m,
        "suction_length_m": suction_length_m, "discharge_length_m": discharge_length_m,
        "suction_static_head_m": suction_static_head_m,
        "combos_summary": summary,
    })


@tool
def browse_candidates(suction_pipe_id: str, discharge_pipe_id: str,
                       sort_by: str = "q_deviation", limit: int = 5, offset: int = 0) -> str:
    """Pages through the passing pump candidates for one (suction diameter, discharge diameter) combination, from the most recent compute_pipe_pump_grid call. sort_by is 'q_deviation' (closest real operating flow to the target first, default) or 'power' (lowest power first). Use this to see candidates beyond the initial preview, to compare runner-ups, or to look up detail on a specific model_id you already have - call compute_pipe_pump_grid first if you haven't yet this run."""
    combo_key = f"{suction_pipe_id}|{discharge_pipe_id}"
    if combo_key not in LAST_PIPE_PUMP_GRID.get("combos", {}):
        return _compact_dumps({
            "error": f"No cached results for suction_pipe_id={suction_pipe_id!r}, "
                     f"discharge_pipe_id={discharge_pipe_id!r}. Call compute_pipe_pump_grid first.",
        })

    Q_target_m3h = LAST_PIPE_PUMP_GRID["Q_target_m3h"]
    passed = LAST_PIPE_PUMP_GRID["combos"][combo_key]["passed"]

    if sort_by == "power":
        key_fn = lambda e: e.P_real if e.P_real is not None else float("inf")
    else:
        key_fn = lambda e: abs(e.Q_real - Q_target_m3h)
    ordered = sorted(passed, key=key_fn)
    page = ordered[offset:offset + limit]

    results = []
    for e in page:
        d = e.to_dict()
        d["Q_deviation_pct"] = _r(abs(e.Q_real - Q_target_m3h) / Q_target_m3h * 100)
        results.append(d)

    return _compact_dumps({
        "suction_pipe_id": suction_pipe_id, "discharge_pipe_id": discharge_pipe_id,
        "sort_by": sort_by, "offset": offset, "limit": limit,
        "total_passed": len(passed), "results": results,
    })
