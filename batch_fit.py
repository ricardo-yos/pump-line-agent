"""
batch_fit.py

Batch-runs the fitting logic from fit_curve.py over every digitized CSV
under a directory tree, testing degrees 2/3/4 per curve and picking the
lowest degree that reaches a good fit - the same rule applied manually
throughout the project (see fit_curve.py's module docstring): prefer
the lowest degree with an acceptable R2 over the highest R2 available,
to avoid overfitting digitization noise.

Expects one CSV per curve, named so the script can infer what it is:
    d<diameter>mm_<curve_type>.csv          e.g. d120mm_H.csv, d120mm_P.csv, d104mm_NPSH.csv
    <curve_type>.csv                        e.g. NPSH.csv (single curve, applies to every diameter of that model - no diameter prefix)
curve_type is one of: H, P, NPSH (case-insensitive). NPSH curves are
usually digitized per diameter (e.g. d104mm_NPSH.csv and d139mm_NPSH.csv
for the same model - each fitted independently here; combine them into
an interpolated NPSH_CURVES entry when building pump_catalog.py). The
bare-filename case (NPSH.csv, no diameter) is for the rarer situation
where the catalog publishes only one NPSH curve for the whole casing,
with no diameter dependence shown - diameter_mm comes back as None for
that entry.

Directory layout expected: one subfolder per pump model under the root,
e.g.:
    data/raw_curves/050-032-125/d120mm_H.csv
    data/raw_curves/050-032-125/d120mm_P.csv
    data/raw_curves/050-032-125/d104mm_NPSH.csv
    data/raw_curves/050-032-125/d139mm_NPSH.csv

Usage:
    python batch_fit.py --root data/raw_curves --out fit_results.json
    python batch_fit.py --root data/raw_curves --min-r2 0.995 --max-degree 4
"""
import argparse
import json
import re
import sys
from pathlib import Path

from fit_curve import read_points, fit_polynomial

_FILENAME_RE = re.compile(
    r"^(?:d(?P<diameter>\d+(?:\.\d+)?)mm_)?(?P<curve_type>[A-Za-z]+)\.csv$",
    re.IGNORECASE,
)


def parse_filename(path: Path) -> dict | None:
    """Extracts diameter (if any) and curve type from a filename like
    'd120mm_H.csv' or 'NPSH.csv'. The 'd<diameter>mm_' prefix is optional
    - a bare 'NPSH.csv' is a single curve that applies to every diameter
    of that model (diameter_mm comes back as None in that case). Returns
    None if the filename doesn't match at all (skipped, not an error -
    lets other files coexist in the same folder)."""
    m = _FILENAME_RE.match(path.name)
    if not m:
        return None
    diameter = float(m.group("diameter")) if m.group("diameter") else None
    return {
        "diameter_mm": diameter,
        "curve_type": m.group("curve_type").upper(),
    }


def best_fit(x, y, min_r2: float, max_degree: int) -> dict:
    """Tries degrees 2..max_degree, returns the lowest degree reaching
    min_r2. If none reaches it, returns the degree with the highest R2
    among those tried, flagged with a warning."""
    results = []
    for degree in range(2, max_degree + 1):
        coef, r2 = fit_polynomial(x, y, degree)
        results.append({"degree": degree, "coef": list(coef), "r2": r2})

    good = [r for r in results if r["r2"] >= min_r2]
    if good:
        chosen = min(good, key=lambda r: r["degree"])
        chosen["warning"] = None
    else:
        chosen = max(results, key=lambda r: r["r2"])
        chosen["warning"] = f"No degree reached R2>={min_r2}; using best available (R2={chosen['r2']:.4f})"
    chosen["all_degrees_tried"] = results
    return chosen


def main():
    parser = argparse.ArgumentParser(description="Batch-fit all digitized curve CSVs under a directory tree.")
    parser.add_argument("--root", default="data/raw_curves", help="Root directory to scan recursively")
    parser.add_argument("--out", default="fit_results.json", help="Output JSON path")
    parser.add_argument("--min-r2", type=float, default=0.995, help="Minimum R2 to accept the lowest degree tried")
    parser.add_argument("--max-degree", type=int, default=4, help="Highest degree to try")
    args = parser.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"Root directory not found: {root}", file=sys.stderr)
        sys.exit(1)

    results = {}
    skipped = []

    for csv_path in sorted(root.rglob("*.csv")):
        model = csv_path.parent.name
        meta = parse_filename(csv_path)
        if meta is None:
            skipped.append(str(csv_path))
            continue

        x, y = read_points(str(csv_path))
        fit = best_fit(x, y, args.min_r2, args.max_degree)

        entry = {
            "model": model,
            "file": str(csv_path),
            "curve_type": meta["curve_type"],
            "diameter_mm": meta["diameter_mm"],
            "n_points": len(x),
            "x_min": float(x.min()),
            "x_max": float(x.max()),
            "degree": fit["degree"],
            "coef": fit["coef"],
            "r2": fit["r2"],
            "warning": fit["warning"],
        }
        results.setdefault(model, []).append(entry)

        flag = " ⚠" if fit["warning"] else ""
        print(f"{model:30s} {csv_path.name:25s} degree={fit['degree']} R2={fit['r2']:.5f}{flag}")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print(f"\n{sum(len(v) for v in results.values())} curves fitted, "
          f"{len(skipped)} files skipped (name didn't match pattern).")
    print(f"Results written to {args.out}")

    warned = [e for model_entries in results.values() for e in model_entries if e["warning"]]
    if warned:
        print(f"\n{len(warned)} curve(s) below R2={args.min_r2} at every degree tried - review these:")
        for e in warned:
            print(f"  - {e['model']}/{Path(e['file']).name}: {e['warning']}")

    if skipped:
        print(f"\nSkipped (filename didn't match 'd<diameter>mm_<type>.csv' or '<type>.csv'):")
        for s in skipped:
            print(f"  - {s}")


if __name__ == "__main__":
    main()
