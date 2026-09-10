"""
pipe_catalog.py

Commercial HDPE (PEAD) pipe catalog used by the pump-line-agent's
piping stage. Fixed at PN10/SDR17 (PE100) for now - see project notes:
this covers the project's typical head range (up to ~40m operating,
comfortable margin even accounting for pump shut-off head) without
making pressure class a dynamic variable per scenario.

Each entry gives the internal diameter (the value that actually matters
for velocity/head-loss calculations) alongside the external nominal
diameter (the commercial designation).

Data source: EN 12201 / ISO 4427, PE100 (MRS 10.0 MPa), PN10/SDR17
column. Verified against a manufacturer technical datasheet (Fersil,
FT PE100 LA EN12201 W, May/2019) reproducing the official EN 12201
dimension table.
"""

PIPE_CATALOG = {
    "pead_pn10_de50": {
        "material": "PEAD (PE100)",
        "pressure_class": "PN10 (SDR17)",
        "external_mm": 50,
        "wall_mm": 3.0,
        "internal_mm": 44.0,
    },
    "pead_pn10_de63": {
        "material": "PEAD (PE100)",
        "pressure_class": "PN10 (SDR17)",
        "external_mm": 63,
        "wall_mm": 3.8,
        "internal_mm": 55.4,
    },
    "pead_pn10_de75": {
        "material": "PEAD (PE100)",
        "pressure_class": "PN10 (SDR17)",
        "external_mm": 75,
        "wall_mm": 4.5,
        "internal_mm": 66.0,
    },
    "pead_pn10_de90": {
        "material": "PEAD (PE100)",
        "pressure_class": "PN10 (SDR17)",
        "external_mm": 90,
        "wall_mm": 5.4,
        "internal_mm": 79.2,
    },
    "pead_pn10_de110": {
        "material": "PEAD (PE100)",
        "pressure_class": "PN10 (SDR17)",
        "external_mm": 110,
        "wall_mm": 6.6,
        "internal_mm": 96.8,
    },
    "pead_pn10_de125": {
        "material": "PEAD (PE100)",
        "pressure_class": "PN10 (SDR17)",
        "external_mm": 125,
        "wall_mm": 7.4,
        "internal_mm": 110.2,
    },
}


def list_pipes() -> list[str]:
    """Returns all pipe ids, ordered by internal diameter (ascending)."""
    return sorted(PIPE_CATALOG, key=lambda pid: PIPE_CATALOG[pid]["internal_mm"])


def get_pipe(pipe_id: str) -> dict:
    if pipe_id not in PIPE_CATALOG:
        raise KeyError(f"Pipe '{pipe_id}' not found in catalog.")
    return PIPE_CATALOG[pipe_id]
