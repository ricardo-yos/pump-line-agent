"""
pipe_sizing.py

Domain logic for pipe sizing - straight-run head loss (Darcy-Weisbach),
no local/minor losses (fittings, valves). The suction and discharge
legs are modeled independently (separate length and diameter each) -
friction loss on the discharge side only affects the system curve
(what the pump has to overcome), while friction loss on the suction
side is what erodes NPSHa - conflating the two would make NPSHa
insensitive to a design choice that, in practice, is specifically used
to protect it (a suction pipe one size up from discharge is a common
way to keep suction velocity - and therefore friction loss - low).

Physical constants:
- Water kinematic viscosity: 1.0e-6 m2/s (approx. at 20C - adjust if a
  different fluid temperature matters for your case; not parameterized
  per-call yet since the project scope is room-temperature water).
- PEAD (HDPE) absolute roughness: 1.5e-6 m (0.0015 mm), a commonly cited
  value for new HDPE pipe (e.g. Crane TP-410 lists drawn tubing/plastic
  pipe in the 0.0015-0.007mm range). This is an engineering approximation,
  not a project-specific measurement - verify against Crane TP-410 or a
  manufacturer datasheet if precision matters for a real installation.
- Standard atmospheric pressure head: 10.33 m of water at sea level.
  Reduce this if the installation is at meaningful altitude (roughly
  -0.12m per 100m of elevation, as a rough approximation - use a real
  barometric/altitude table if precision matters).
- Water vapor pressure head: 0.24 m at 20C. Rises with temperature (this
  is the term that makes NPSH tighter for hot water) - use a steam-table
  value instead of this default if the fluid isn't near room temperature.

Friction factor bifurcation (the one place this project has a genuine
regime decision, not just a lookup):
- Re < 2300: laminar, f = 64/Re (exact, from Hagen-Poiseuille).
- Re >= 2300: turbulent, f solved from the implicit Colebrook-White
  equation via root finding (brentq) - not the explicit Swamee-Jain
  approximation, to stay consistent with using brentq elsewhere in this
  project rather than mixing exact and approximate methods without reason.
- 2300-4000 is the true transition zone (neither correlation is exact
  there); this module treats it as turbulent and flags it via the
  "regime" field in the result, rather than silently picking one without
  telling the caller.
"""
import numpy as np
from scipy.optimize import brentq

from pipe_catalog import PIPE_CATALOG, get_pipe, list_pipes

WATER_KINEMATIC_VISCOSITY_M2S = 1.0e-6  # ~20C
PEAD_ROUGHNESS_M = 1.5e-6  # 0.0015 mm, new HDPE pipe
G = 9.81  # m/s2
STANDARD_ATM_PRESSURE_HEAD_M = 10.33  # sea level
WATER_VAPOR_PRESSURE_HEAD_M = 0.24  # water at ~20C


def velocity_ms(Q_m3h: float, internal_diameter_mm: float) -> float:
    """Mean flow velocity in the pipe, m/s."""
    Q_m3s = Q_m3h / 3600.0
    D_m = internal_diameter_mm / 1000.0
    area_m2 = np.pi * (D_m ** 2) / 4.0
    return Q_m3s / area_m2


def reynolds_number(Q_m3h: float, internal_diameter_mm: float,
                     nu: float = WATER_KINEMATIC_VISCOSITY_M2S) -> float:
    v = velocity_ms(Q_m3h, internal_diameter_mm)
    D_m = internal_diameter_mm / 1000.0
    return v * D_m / nu


def friction_factor(Re: float, internal_diameter_mm: float,
                     roughness_m: float = PEAD_ROUGHNESS_M) -> tuple[float, str]:
    """Returns (f, regime). regime is 'laminar', 'turbulent', or
    'transition (treated as turbulent)' - see module docstring for why
    the transition zone (2300-4000) is handled this way."""
    if Re <= 0:
        return 0.0, "no_flow"
    if Re < 2300:
        return 64.0 / Re, "laminar"

    D_m = internal_diameter_mm / 1000.0
    relative_roughness = roughness_m / D_m

    def colebrook(f):
        # 1/sqrt(f) = -2*log10(eps/(3.7*D) + 2.51/(Re*sqrt(f)))
        return 1.0 / np.sqrt(f) + 2.0 * np.log10(relative_roughness / 3.7 + 2.51 / (Re * np.sqrt(f)))

    f = brentq(colebrook, 1e-5, 1.0)
    regime = "turbulent" if Re >= 4000 else "transition (treated as turbulent)"
    return f, regime


def head_loss_friction_m(Q_m3h: float, internal_diameter_mm: float, length_m: float,
                          roughness_m: float = PEAD_ROUGHNESS_M,
                          nu: float = WATER_KINEMATIC_VISCOSITY_M2S) -> float:
    """Darcy-Weisbach friction head loss, in meters. Straight run only -
    no fittings/valves (see module docstring)."""
    v = velocity_ms(Q_m3h, internal_diameter_mm)
    Re = reynolds_number(Q_m3h, internal_diameter_mm, nu)
    f, _regime = friction_factor(Re, internal_diameter_mm, roughness_m)
    D_m = internal_diameter_mm / 1000.0
    return f * (length_m / D_m) * (v ** 2) / (2 * G)


def system_head_m(Q_m3h: float, static_head_m: float, internal_diameter_mm: float,
                   length_m: float, roughness_m: float = PEAD_ROUGHNESS_M,
                   nu: float = WATER_KINEMATIC_VISCOSITY_M2S) -> float:
    """Total system head at flow Q: static head + friction loss. This is
    H_system(Q) - the real, Q-dependent counterpart to the constant
    H_required used in stage 1."""
    return static_head_m + head_loss_friction_m(Q_m3h, internal_diameter_mm, length_m, roughness_m, nu)


def system_curve(static_head_m: float, internal_diameter_mm: float, length_m: float,
                  roughness_m: float = PEAD_ROUGHNESS_M,
                  nu: float = WATER_KINEMATIC_VISCOSITY_M2S):
    """Returns a callable H_system(Q) closing over the fixed pipe/static
    head parameters - what pump_sizing.find_operating_point needs to find
    the real intersection with a pump curve (stage 2), instead of stage
    1's constant H_required.

    Single-leg version (one diameter, one length) - kept for simpler
    cases; see two_leg_system_curve for suction+discharge modeled
    separately."""
    def H(Q_m3h: float) -> float:
        return system_head_m(Q_m3h, static_head_m, internal_diameter_mm, length_m, roughness_m, nu)
    return H


def two_leg_system_curve(static_head_total_m: float,
                          suction_internal_mm: float, suction_length_m: float,
                          discharge_internal_mm: float, discharge_length_m: float,
                          roughness_m: float = PEAD_ROUGHNESS_M,
                          nu: float = WATER_KINEMATIC_VISCOSITY_M2S):
    """Returns a callable H_system(Q) with suction and discharge legs
    modeled independently (own diameter and length each) - their
    friction losses simply add, since they're in series. Real pump
    projects often size the suction leg one size up from discharge
    specifically to keep suction velocity (and friction loss) low,
    protecting NPSHa - a single shared diameter can't represent that
    choice, which is why this is a separate function from
    system_curve rather than a generalization of it."""
    def H(Q_m3h: float) -> float:
        h_f_suction = head_loss_friction_m(Q_m3h, suction_internal_mm, suction_length_m, roughness_m, nu)
        h_f_discharge = head_loss_friction_m(Q_m3h, discharge_internal_mm, discharge_length_m, roughness_m, nu)
        return static_head_total_m + h_f_suction + h_f_discharge
    return H


def npsh_available(suction_static_head_m: float, suction_internal_mm: float,
                    suction_length_m: float, Q_m3h: float,
                    atm_pressure_head_m: float = STANDARD_ATM_PRESSURE_HEAD_M,
                    vapor_pressure_head_m: float = WATER_VAPOR_PRESSURE_HEAD_M,
                    roughness_m: float = PEAD_ROUGHNESS_M,
                    nu: float = WATER_KINEMATIC_VISCOSITY_M2S) -> float:
    """NPSHa (available NPSH) at the pump suction, at flow Q - the real,
    calculated counterpart to a user-supplied NPSHa value.

    NPSHa = atm_pressure_head + suction_static_head - friction_loss - vapor_pressure_head

    Sign convention for suction_static_head_m (this is the one place a
    wrong sign silently produces a plausible-looking but wrong number,
    so it's spelled out rather than left to a docstring aside):
    - POSITIVE: flooded suction - the source liquid surface sits ABOVE
      the pump centerline (gravity helps push water into the pump).
      This is the safer, more common configuration and improves NPSHa.
    - NEGATIVE: suction lift - the source liquid surface sits BELOW the
      pump centerline (the pump has to lift water up to itself before
      it can even start pushing). This is harder on NPSHa and is the
      configuration to scrutinize when NPSH margin is tight.

    atm_pressure_head_m and vapor_pressure_head_m default to sea-level/
    20C water values (see module docstring) - override them if altitude
    or fluid temperature meaningfully changes either.
    """
    h_f_suction = head_loss_friction_m(Q_m3h, suction_internal_mm, suction_length_m, roughness_m, nu)
    return atm_pressure_head_m + suction_static_head_m - h_f_suction - vapor_pressure_head_m


def viable_pipe_diameters(Q_m3h: float, v_min: float = 1.0, v_max: float = 2.5) -> list[dict]:
    """Every commercial pipe (from pipe_catalog) whose resulting velocity
    at Q_m3h falls within [v_min, v_max] m/s - the range commonly used
    for water transfer lines. Returns a list (not a single 'the'
    diameter): stage 2's point is to expose every viable option so the
    pipe-vs-pump trade-off can be reasoned about, not to pre-decide it.
    Sorted by internal diameter ascending (smallest viable first)."""
    results = []
    for pipe_id in list_pipes():
        pipe = get_pipe(pipe_id)
        v = velocity_ms(Q_m3h, pipe["internal_mm"])
        if v_min <= v <= v_max:
            results.append({
                "pipe_id": pipe_id,
                "internal_mm": pipe["internal_mm"],
                "external_mm": pipe["external_mm"],
                "velocity_ms": v,
            })
    return results
