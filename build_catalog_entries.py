"""
build_catalog_entries.py

Reads fit_results.json (produced by batch_fit.py) and generates ready-to-
review Python source for pump_catalog.py's PUMP_CATALOG and NPSH_CURVES
dicts - so you don't hand-transcribe coefficients for every model and
diameter. Writes to a separate file (default catalog_entries_draft.py)
for you to review and merge into pump_catalog.py yourself, rather than
overwriting it - pump_catalog.py already has hand-curated entries and a
docstring you don't want clobbered.

Per model, groups curves by type:
- H entries (one per digitized diameter) each become one PUMP_CATALOG
  entry, keyed "<model>_d<diameter>mm_<speed>rpm".
- P entries are matched to the H entry of the same diameter, if present;
  a diameter with H but no P gets coef_P=None (not digitized yet, per
  project convention - see pump_catalog.py's own docstring).
- NPSH entries:
    - a single entry with diameter_mm=None -> one shared curve for every
      diameter of the model (coef_NPSHr, no interpolation needed).
    - two or more diameter-specific entries -> the two extremes (lowest
      and highest digitized diameter) become an interpolated NPSH_CURVES
      entry (coef_NPSHr_lo/coef_NPSHr_hi), per the project's convention
      that KSB catalogs publish NPSH only at the smallest/largest trim.
      Any additional in-between NPSH curves are reported but not used
      (see warnings in the printed summary).
    - no NPSH entry for a model -> npsh_curve_id stays None (not
      digitized yet).

Source attribution (catalog document/page) isn't in fit_results.json.
Pass --document if every curve came from the same catalog PDF (common
case - one document covers a whole product line), and the generated
"source" dict fills it in automatically. Page still can't be known per
curve, so it stays a TODO for you to fill from the PDF you digitized.

Usage:
    python build_catalog_entries.py --input fit_results.json --document "1311.46/12-EN-US"
    python build_catalog_entries.py --input fit_results.json --speed-rpm 3500 --manufacturer KSB --document "1311.46/12-EN-US"
"""
import argparse
import json
from collections import defaultdict


def _fmt_coef(coef: list[float]) -> str:
    return "[" + ", ".join(repr(c) for c in coef) + "]"


def _source_dict_str(manufacturer: str, document: str | None, curve_label: str) -> str:
    doc_repr = json.dumps(document) if document else "None"
    return (
        f'{{"manufacturer": "{manufacturer}", "document": {doc_repr}, "page": None,  # TODO: fill page from the catalog PDF\n'
        f'                    "curve": "{curve_label}"}}'
    )


def generate(fit_results: dict, speed_rpm: int, manufacturer: str, document: str | None = None) -> tuple[str, list[str]]:
    """Returns (generated_source, warnings)."""
    warnings = []
    npsh_blocks = []
    pump_blocks = []

    for model, entries in fit_results.items():
        by_type = defaultdict(list)
        for e in entries:
            by_type[e["curve_type"]].append(e)

        h_entries = sorted(
            [e for e in by_type.get("H", []) if e["diameter_mm"] is not None],
            key=lambda e: e["diameter_mm"],
        )
        p_by_diameter = {e["diameter_mm"]: e for e in by_type.get("P", []) if e["diameter_mm"] is not None}

        # --- NPSH curve(s) for this model ---
        npsh_entries = by_type.get("NPSH", [])
        npsh_curve_id = None
        shared = [e for e in npsh_entries if e["diameter_mm"] is None]
        per_diameter = sorted(
            [e for e in npsh_entries if e["diameter_mm"] is not None],
            key=lambda e: e["diameter_mm"],
        )

        if shared:
            if len(shared) > 1:
                warnings.append(f"{model}: multiple diameter-less NPSH curves found - using the first, ignoring the rest.")
            npsh_curve_id = f"npsh_{model}"
            e = shared[0]
            npsh_blocks.append(
                f'    "{npsh_curve_id}": {{\n'
                f'        "coef_NPSHr": {_fmt_coef(e["coef"])},\n'
                f'        "NPSHr_degree": {e["degree"]},\n'
                f'        "diameter_range_mm": (0, 0),  # shared across all diameters - see pump_sizing.evaluate_NPSHr\n'
                f'        "source": {_source_dict_str(manufacturer, document, f"NPSH x Q, {model}, n={speed_rpm}rpm (shared)")},\n'
                f'    }},\n'
            )
        elif per_diameter:
            if len(per_diameter) > 2:
                middle = [e["diameter_mm"] for e in per_diameter[1:-1]]
                warnings.append(
                    f"{model}: {len(per_diameter)} diameter-specific NPSH curves digitized; "
                    f"using extremes ({per_diameter[0]['diameter_mm']}, {per_diameter[-1]['diameter_mm']}), "
                    f"ignoring middle diameter(s) {middle} for interpolation."
                )
            lo, hi = per_diameter[0], per_diameter[-1]
            d_lo, d_hi = lo["diameter_mm"], hi["diameter_mm"]
            npsh_curve_id = f"npsh_{model}_d{d_lo:g}-d{d_hi:g}mm"
            npsh_label = f"NPSH x Q, {model}, d{d_lo:g}/{d_hi:g}mm, n={speed_rpm}rpm"
            npsh_blocks.append(
                f'    "{npsh_curve_id}": {{\n'
                f'        "coef_NPSHr_lo": {_fmt_coef(lo["coef"])},\n'
                f'        "coef_NPSHr_hi": {_fmt_coef(hi["coef"])},\n'
                f'        "NPSHr_degree_lo": {lo["degree"]}, "NPSHr_degree_hi": {hi["degree"]},\n'
                f'        "diameter_range_mm": ({d_lo:g}, {d_hi:g}),\n'
                f'        "source": {_source_dict_str(manufacturer, document, npsh_label)},\n'
                f'    }},\n'
            )
        else:
            warnings.append(f"{model}: no NPSH curve digitized - npsh_curve_id will be None.")

        # --- One PUMP_CATALOG entry per digitized H diameter ---
        if not h_entries:
            warnings.append(f"{model}: no H curve digitized - skipping entirely (can't build a pump entry without H).")
            continue

        for h in h_entries:
            d = h["diameter_mm"]
            pump_id = f"{model}_d{d:g}mm_{speed_rpm}rpm"
            p = p_by_diameter.get(d)
            if p is None:
                warnings.append(f"{pump_id}: H digitized but no matching P at this diameter - coef_P set to None.")
                coef_p_line = "        \"coef_P\": None,\n        \"P_degree\": None,\n"
            else:
                coef_p_line = f'        "coef_P": {_fmt_coef(p["coef"])},\n        "P_degree": {p["degree"]},\n'

            h_label = f"H x Q, {model}, d{d:g}mm, n={speed_rpm}rpm"
            pump_blocks.append(
                f'    "{pump_id}": {{\n'
                f'        "manufacturer": "{manufacturer}",\n'
                f'        "model": "{model}",\n'
                f'        "impeller_mm": {d:g},\n'
                f'        "speed_rpm": {speed_rpm},\n'
                f'        "Q_min_curve": {h["x_min"]!r},\n'
                f'        "Q_max_curve": {h["x_max"]!r},\n'
                f'        "coef_H": {_fmt_coef(h["coef"])},\n'
                f'        "H_degree": {h["degree"]},\n'
                f'{coef_p_line}'
                f'        "npsh_curve_id": {json.dumps(npsh_curve_id)},\n'
                f'        "source": {_source_dict_str(manufacturer, document, h_label)},\n'
                f'    }},\n'
            )

    source = (
        "# --- Generated by build_catalog_entries.py from fit_results.json ---\n"
        "# Review before merging into pump_catalog.py: fill in the 'document'/'page'\n"
        "# TODOs, and double-check Q ranges / coefficients against the digitized curves.\n\n"
        "GENERATED_NPSH_CURVES = {\n" + "".join(npsh_blocks) + "}\n\n"
        "GENERATED_PUMP_CATALOG = {\n" + "".join(pump_blocks) + "}\n"
    )
    return source, warnings


def main():
    parser = argparse.ArgumentParser(description="Generate pump_catalog.py entries from fit_results.json.")
    parser.add_argument("--input", default="fit_results.json")
    parser.add_argument("--output", default="catalog_entries_draft.py")
    parser.add_argument("--speed-rpm", type=int, default=3500)
    parser.add_argument("--manufacturer", default="KSB")
    parser.add_argument("--document", default="1311.46/12-EN-US", help="Catalog document code shared by every curve, e.g. '1311.46/12-EN-US'. Page still needs to be filled in manually per curve.")
    args = parser.parse_args()

    with open(args.input, encoding="utf-8") as f:
        fit_results = json.load(f)

    source, warnings = generate(fit_results, args.speed_rpm, args.manufacturer, args.document)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(source)

    print(f"Generated {args.output}")
    if warnings:
        print(f"\n{len(warnings)} thing(s) to review:")
        for w in warnings:
            print(f"  - {w}")


if __name__ == "__main__":
    main()
