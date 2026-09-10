"""
pump_sizing.py

Domain logic for pump selection - the actual engineering decision
pipeline, with no LLM or tool-calling machinery involved.

Pipeline: filter by flow range -> require H(Q_target) >= H_required ->
find the real operating point (root finding, since the pump isn't
trimmed to hit Q_target exactly) -> optional NPSH margin gate ->
evaluate the passing candidates.

The module provides deterministic engineering calculations used by the
agent-facing tools in tools.py. The final selection among feasible
candidates is intentionally left to the agent, allowing it to consider
engineering trade-offs rather than applying a fixed optimization rule.

Stage 1 uses a target flow and required head as inputs. Stage 2 evaluates
pumps against a complete system curve H_system(Q), finding the actual
operating point from the intersection between pump and system curves.

tools.py imports from here and wraps the engineering functions as thin
@tool functions for the agent - it holds no pump physics itself.
"""
from dataclasses import dataclass
from typing import Callable

import numpy as np
from scipy.optimize import brentq

from pump_catalog import get_pump, get_npsh_curve, list_pumps

DEFAULT_MIN_NPSH_MARGIN = 0.5  # meters, used only when NPSH data is available


@dataclass
class PumpEvaluation:
    model_id: str
    passed: bool
    reason: str
    Q_target: float
    H_required: float
    H_at_Q_target: float | None = None
    Q_real: float | None = None      # real operating point (stage-1 approx.)
    H_real: float | None = None
    P_real: float | None = None      # None if coef_P not digitized yet
    NPSHr_real: float | None = None  # None if NPSH data not available
    NPSH_margin: float | None = None

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id, "passed": self.passed, "reason": self.reason,
            "Q_target": self.Q_target, "H_required": self.H_required,
            "H_at_Q_target": self.H_at_Q_target, "Q_real": self.Q_real,
            "H_real": self.H_real, "P_real": self.P_real,
            "NPSHr_real": self.NPSHr_real, "NPSH_margin": self.NPSH_margin,
        }


def evaluate_H(pump: dict, Q: float) -> float:
    """H(Q) for a pump entry, using its own polynomial degree (H_degree)."""
    return float(np.polyval(pump["coef_H"], Q))


def evaluate_P(pump: dict, Q: float) -> float | None:
    """P(Q) for a pump entry, or None if not digitized yet."""
    if pump.get("coef_P") is None:
        return None
    return float(np.polyval(pump["coef_P"], Q))


def evaluate_NPSHr(pump: dict, Q: float) -> float | None:
    """NPSHr(Q) for a pump entry. Interpolates linearly between the two
    diameter extremes of the shared NPSH curve when it covers a range
    (KSB catalogs often publish only the smallest and largest trim of a
    casing). Returns None if the pump has no npsh_curve_id yet."""
    curve_id = pump.get("npsh_curve_id")
    if curve_id is None:
        return None
    curve = get_npsh_curve(curve_id)
    d_lo, d_hi = curve["diameter_range_mm"]
    d = pump["impeller_mm"]
    if d_lo == d_hi:
        return float(np.polyval(curve["coef_NPSHr"], Q))
    npshr_lo = float(np.polyval(curve["coef_NPSHr_lo"], Q))
    npshr_hi = float(np.polyval(curve["coef_NPSHr_hi"], Q))
    frac = (d - d_lo) / (d_hi - d_lo)
    return npshr_lo + frac * (npshr_hi - npshr_lo)


def find_operating_point(pump: dict, H_required: float | Callable[[float], float]) -> float | None:
    """Solves H_pump(Q) = H_system(Q) for Q within [Q_min_curve,
    Q_max_curve] via root finding (brentq) - the pump isn't trimmed to
    hit the target point exactly, so its real operating point is where
    its curve crosses the system's. Returns None if the curves never
    cross within the digitized range.

    H_required accepts either:
    - a constant (float) - stage-1 simplification, system head treated
      as flat since piping isn't part of the calculation.
    - a callable H(Q) - stage-2 real system curve (see
      pipe_sizing.system_curve), letting the system head actually rise
      with flow like it does physically (static head + friction loss).
    Both paths share this same root-finding logic; only what "H_required"
    means differs.
    """
    H_system = H_required if callable(H_required) else (lambda Q: H_required)
    Q_lo, Q_hi = pump["Q_min_curve"], pump["Q_max_curve"]

    def f(Q):
        return evaluate_H(pump, Q) - H_system(Q)

    f_lo, f_hi = f(Q_lo), f(Q_hi)
    if f_lo == 0:
        return Q_lo
    if f_hi == 0:
        return Q_hi
    if f_lo * f_hi > 0:
        return None
    return float(brentq(f, Q_lo, Q_hi))


def check_npsh_margin(pump: dict, Q: float, NPSHa: float | None) -> tuple[float | None, float | None]:
    """Returns (NPSHr, margin). Both None if NPSH data (pump curve or
    NPSHa) is unavailable - the gate is skipped, not failed, in that case."""
    if NPSHa is None:
        return None, None
    npshr = evaluate_NPSHr(pump, Q)
    if npshr is None:
        return None, None
    return npshr, NPSHa - npshr


def evaluate_candidate(
    model_id: str, Q_target: float, H_required: float,
    NPSHa: float | None = None, min_npsh_margin: float = DEFAULT_MIN_NPSH_MARGIN,
) -> PumpEvaluation:
    """Runs one pump through the full decision pipeline. Rejected
    candidates are returned with a reason, not discarded, so the trail
    stays auditable."""
    pump = get_pump(model_id)

    if not (pump["Q_min_curve"] <= Q_target <= pump["Q_max_curve"]):
        return PumpEvaluation(
            model_id=model_id, passed=False,
            reason=f"Q_target={Q_target} outside curve range [{pump['Q_min_curve']}, {pump['Q_max_curve']}]",
            Q_target=Q_target, H_required=H_required,
        )

    H_at_target = evaluate_H(pump, Q_target)
    if H_at_target < H_required:
        return PumpEvaluation(
            model_id=model_id, passed=False,
            reason=f"H({Q_target})={H_at_target:.2f}m < required {H_required:.2f}m",
            Q_target=Q_target, H_required=H_required, H_at_Q_target=H_at_target,
        )

    Q_real = find_operating_point(pump, H_required)
    if Q_real is None:
        return PumpEvaluation(
            model_id=model_id, passed=False, reason="No operating point found within curve range",
            Q_target=Q_target, H_required=H_required, H_at_Q_target=H_at_target,
        )
    H_real = evaluate_H(pump, Q_real)
    P_real = evaluate_P(pump, Q_real)

    npshr, margin = check_npsh_margin(pump, Q_real, NPSHa)
    if margin is not None and margin < min_npsh_margin:
        return PumpEvaluation(
            model_id=model_id, passed=False,
            reason=f"NPSH margin {margin:.2f}m below required {min_npsh_margin}m",
            Q_target=Q_target, H_required=H_required, H_at_Q_target=H_at_target,
            Q_real=Q_real, H_real=H_real, P_real=P_real, NPSHr_real=npshr, NPSH_margin=margin,
        )

    return PumpEvaluation(
        model_id=model_id, passed=True, reason="ok",
        Q_target=Q_target, H_required=H_required, H_at_Q_target=H_at_target,
        Q_real=Q_real, H_real=H_real, P_real=P_real, NPSHr_real=npshr, NPSH_margin=margin,
    )


def select_best_pump(
    Q_target: float, H_required: float,
    NPSHa: float | None = None, min_npsh_margin: float = DEFAULT_MIN_NPSH_MARGIN,
) -> tuple[PumpEvaluation | None, list[PumpEvaluation]]:
    """Evaluates every candidate in flow range and returns (best,
    all_evaluations), auto-picking the lowest P at the real operating
    point among passing candidates as a deterministic baseline.

    NOT used by the agent-facing tool (see tools.select_pump) - the final
    choice among passing candidates is a judgment call (which diameter/pump
    combo makes engineering sense, not just which has the single lowest
    number), so tools.py exposes every passing candidate and leaves the
    decision to the agent instead of pre-deciding it here.
    """
    candidate_ids = list_pumps(Q_target)
    evaluations = [
        evaluate_candidate(mid, Q_target, H_required, NPSHa, min_npsh_margin)
        for mid in candidate_ids
    ]
    passed = [e for e in evaluations if e.passed]
    if not passed:
        return None, evaluations
    with_power = [e for e in passed if e.P_real is not None]
    best = min(with_power, key=lambda e: e.P_real) if with_power \
        else min(passed, key=lambda e: abs(e.Q_real - Q_target))
    return best, evaluations


def list_candidates(
    Q_target: float, H_required: float,
    NPSHa: float | None = None, min_npsh_margin: float = DEFAULT_MIN_NPSH_MARGIN,
) -> list[PumpEvaluation]:
    """Evaluates every pump whose curve covers Q_target and returns ALL
    evaluations (passed and rejected) - no automatic 'best' pick. The
    deterministic gates (flow range, head requirement, NPSH margin) still
    run here, since those aren't judgment calls - a pump either meets
    them or it doesn't. But choosing among the candidates that DO pass is
    left to the agent: it's a trade-off (power consumption, and once
    piping is modeled, pipe diameter interaction) that benefits from
    engineering reasoning, not a single fixed rule."""
    candidate_ids = list_pumps(Q_target)
    return [
        evaluate_candidate(mid, Q_target, H_required, NPSHa, min_npsh_margin)
        for mid in candidate_ids
    ]


# ---------------------------------------------------------------------------
# Stage 2: evaluating a pump against a real system curve H_system(Q),
# instead of stage 1's constant H_required. There is no fixed Q_target to
# filter candidates by here - the pump's real operating point is wherever
# its curve crosses the system curve, which is exactly what
# find_operating_point solves for. Every pump in the catalog is tried;
# find_operating_point already rejects any whose curve never crosses the
# system curve within its digitized range.
# ---------------------------------------------------------------------------
def evaluate_pump_against_system(
    model_id: str, H_system: Callable[[float], float],
    NPSHa: float | None = None, min_npsh_margin: float = DEFAULT_MIN_NPSH_MARGIN,
) -> PumpEvaluation:
    """Finds where this pump's curve intersects the real system curve
    H_system(Q) (see pipe_sizing.system_curve) and evaluates it there.
    Unlike stage 1's evaluate_candidate, there's no Q_target/H_required
    scalar gate - the operating point isn't chosen, it's found."""
    pump = get_pump(model_id)
    Q_real = find_operating_point(pump, H_system)
    if Q_real is None:
        return PumpEvaluation(
            model_id=model_id, passed=False,
            reason="Pump curve never crosses the system curve within its digitized range",
            Q_target=None, H_required=None,
        )
    H_real = evaluate_H(pump, Q_real)
    P_real = evaluate_P(pump, Q_real)

    npshr, margin = check_npsh_margin(pump, Q_real, NPSHa)
    if margin is not None and margin < min_npsh_margin:
        return PumpEvaluation(
            model_id=model_id, passed=False,
            reason=f"NPSH margin {margin:.2f}m below required {min_npsh_margin}m",
            Q_target=None, H_required=None,
            Q_real=Q_real, H_real=H_real, P_real=P_real, NPSHr_real=npshr, NPSH_margin=margin,
        )

    return PumpEvaluation(
        model_id=model_id, passed=True, reason="ok",
        Q_target=None, H_required=None,
        Q_real=Q_real, H_real=H_real, P_real=P_real, NPSHr_real=npshr, NPSH_margin=margin,
    )


def list_candidates_against_system(
    H_system: Callable[[float], float],
    NPSHa: float | None = None, min_npsh_margin: float = DEFAULT_MIN_NPSH_MARGIN,
) -> list[PumpEvaluation]:
    """Evaluates every pump in the catalog against a real system curve.
    No flow-range pre-filter (unlike stage 1's list_candidates) - the
    operating point is found by intersection, not chosen up front, so
    any pump could in principle intersect the curve within its range."""
    return [
        evaluate_pump_against_system(mid, H_system, NPSHa, min_npsh_margin)
        for mid in list_pumps()
    ]
