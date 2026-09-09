"""
fit_curve.py

Fits a polynomial approximation to digitized pump catalog curves
(H x Q, P x Q, or NPSHr x Q) extracted from manufacturer performance
charts.

The digitized points are read from external CSV files and converted into
continuous polynomial functions that can be evaluated by the hydraulic
calculation tools used by the Pump Line Agent.

Pump curves are theoretically approximated by a quadratic relationship
under ideal assumptions:

    H = H0 - KQ²

However, real pump performance curves deviate from a perfect parabola due
to non-ideal effects such as hydraulic losses, leakage flow, internal
recirculation, and other mechanical and fluid-dynamic effects.

Polynomial degrees 2, 3, and higher can be evaluated depending on the
specific curve. Degree 3 is used as the default fitting option because it
provided a good balance between fitting accuracy and model complexity when
compared with degree 2 and degree 4 fits on representative digitized
catalog curves.

For batch processing, batch_fit.py applies a model selection strategy:
it selects the lowest polynomial degree that reaches the required R²
threshold, preferring simpler models to avoid fitting digitization noise.

This module contains no embedded catalog data. The original digitized
points are stored externally in data/raw_curves/ and are not versioned in
the public repository.

Usage:
    python fit_curve.py --input data/raw_curves/050-032-125/d120mm_H.csv

    python fit_curve.py \
        --input data/raw_curves/050-032-125/d120mm_H.csv \
        --degree 3 \
        --xlabel "Q [m3/h]" \
        --ylabel "H [m]" \
        --plot
"""

import argparse
import csv
import sys

import numpy as np


def read_points(path: str) -> tuple[np.ndarray, np.ndarray]:
    """Reads a two-column CSV (x, y), with or without a header (non-numeric
    lines are skipped). Accepts both US-style CSVs (comma-separated,
    dot decimal) and the semicolon-separated, comma-decimal format some
    WebPlotDigitizer exports produce (e.g. "0,076;28,027")."""
    xs, ys = [], []
    with open(path, newline="", encoding="utf-8") as f:
        raw_lines = [line for line in f if line.strip()]

    # Detect delimiter: prefer ';' if present, otherwise ','
    delimiter = ";" if any(";" in line for line in raw_lines) else ","

    reader = csv.reader(raw_lines, delimiter=delimiter)
    for row in reader:
        if len(row) < 2:
            continue
        try:
            # Decimal comma -> dot, only when delimiter isn't comma
            # (avoids corrupting a normal US-style row).
            raw_x, raw_y = row[0].strip(), row[1].strip()
            if delimiter == ";":
                raw_x = raw_x.replace(",", ".")
                raw_y = raw_y.replace(",", ".")
            x, y = float(raw_x), float(raw_y)
        except ValueError:
            continue  # skip header or empty line
        xs.append(x)
        ys.append(y)
    if not xs:
        raise ValueError(f"No numeric points found in {path}")
    return np.array(xs), np.array(ys)


def fit_polynomial(x: np.ndarray, y: np.ndarray, degree: int = 3):
    """Fits a polynomial of the given degree (3 by default - see module
    docstring for why) and returns coefficients (descending order, as in
    np.polyfit) plus R-squared."""
    coef = np.polyfit(x, y, degree)
    y_pred = np.polyval(coef, x)
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return coef, r2


def main():
    parser = argparse.ArgumentParser(description="Fit a polynomial to points digitized from a catalog curve.")
    parser.add_argument("--input", required=True, help="Path to the CSV with x,y columns")
    parser.add_argument("--degree", type=int, default=3, help="Polynomial degree (default: 3)")
    parser.add_argument("--xlabel", default="Q [m3/h]", help="X-axis label (plot only)")
    parser.add_argument("--ylabel", default="H [m]", help="Y-axis label (plot only)")
    parser.add_argument("--plot", action="store_true", help="Save a PNG comparing points vs fit")
    parser.add_argument("--plot-output", default=None, help="Output PNG path (default: <input>_fit.png)")
    args = parser.parse_args()

    x, y = read_points(args.input)
    coef, r2 = fit_polynomial(x, y, args.degree)

    print(f"Points read: {len(x)}")
    print(f"X range: {x.min():.3f} to {x.max():.3f}")
    print(f"Coefficients (descending order, np.polyval): {list(coef)}")
    print(f"R-squared: {r2:.5f}")

    if r2 < 0.995:
        print("Warning: R-squared below 0.995 - consider reviewing the digitized points "
              "(wrong curve, noise) or comparing --degree 2/3/4 to see which fits best.",
              file=sys.stderr)

    if args.plot:
        import matplotlib.pyplot as plt

        x_fit = np.linspace(x.min(), x.max(), 200)
        y_fit = np.polyval(coef, x_fit)

        plt.figure(figsize=(8, 5))
        plt.scatter(x, y, s=10, alpha=0.5, label="Digitized points")
        plt.plot(x_fit, y_fit, "r-", label=f"Degree-{args.degree} fit (R2={r2:.4f})")
        plt.xlabel(args.xlabel)
        plt.ylabel(args.ylabel)
        plt.legend()
        plt.grid(True)

        out_path = args.plot_output or (args.input.rsplit(".", 1)[0] + "_fit.png")
        plt.savefig(out_path, dpi=100)
        print(f"Plot saved to: {out_path}")


if __name__ == "__main__":
    main()
