#!/usr/bin/env python3
"""
PACER Bulk Updater Cost Analysis Report

Reads all court_data_YYYY_MM_DD.csv files in the target directory and produces
a time series table covering all available dates for four metrics across eight
target courts:

    dkt_auto_bulk_updater_err_dollars
    dkt_auto_bulk_updater_err_vol
    dkt_auto_bulk_updater_succ_dollars
    dkt_auto_bulk_updater_succ_vol

Output is written to both stdout and a CSV file (default: bulk_updater_report.csv).

Usage:
    python generate_report.py
    python generate_report.py --data-dir /path/to/data
    python generate_report.py --output my_report.csv
    python generate_report.py --include-cr
    python generate_report.py --help
"""

import os
import sys
import glob
import warnings
import argparse
import textwrap
from datetime import datetime, date

import pandas as pd

warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)


# ─── Configuration ────────────────────────────────────────────────────────────

TARGET_COURTS = ["nmid", "akd", "med", "ndd", "ned", "wied", "vid", "wyd"]

METRICS = [
    "dkt_auto_bulk_updater_err_dollars",
    "dkt_auto_bulk_updater_err_vol",
    "dkt_auto_bulk_updater_succ_dollars",
    "dkt_auto_bulk_updater_succ_vol",
]

LABELS = {
    "dkt_auto_bulk_updater_err_dollars":  "Err $",
    "dkt_auto_bulk_updater_err_vol":      "Err Vol",
    "dkt_auto_bulk_updater_succ_dollars": "Succ $",
    "dkt_auto_bulk_updater_succ_vol":     "Succ Vol",
}

DAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ─── Data loading ─────────────────────────────────────────────────────────────

def load_data(data_dir: str) -> pd.DataFrame:
    """Read every court_data_YYYY_MM_DD.csv in data_dir and return one DataFrame."""
    pattern = os.path.join(data_dir, "court_data_*.csv")
    files = sorted(glob.glob(pattern))
    if not files:
        sys.exit(f"ERROR: No court_data_*.csv files found in '{data_dir}'")

    frames, skipped = [], 0
    for fpath in files:
        basename = os.path.basename(fpath)
        try:
            stem = basename[len("court_data_"):-len(".csv")]   # → YYYY_MM_DD
            y, m, d = stem.split("_")
            file_date = date(int(y), int(m), int(d))
        except (ValueError, AttributeError):
            print(f"  Warning: cannot parse date from '{basename}', skipping.", file=sys.stderr)
            skipped += 1
            continue

        try:
            chunk = pd.read_csv(fpath, low_memory=False)
        except Exception as exc:
            print(f"  Warning: cannot read '{basename}': {exc}", file=sys.stderr)
            skipped += 1
            continue

        chunk.columns = chunk.columns.str.strip().str.lower()
        chunk["date"] = file_date
        frames.append(chunk)

    if not frames:
        sys.exit("ERROR: No files could be loaded.")

    suffix = f", skipped {skipped}" if skipped else ""
    print(f"  Loaded {len(frames)} file(s){suffix}.", file=sys.stderr)
    return pd.concat(frames, ignore_index=True, sort=False)


def filter_data(df: pd.DataFrame, include_cr: bool) -> pd.DataFrame:
    """Keep only target courts and the four key metric columns."""
    courts = TARGET_COURTS + ([c + "_cr" for c in TARGET_COURTS] if include_cr else [])
    available = [m for m in METRICS if m in df.columns]
    missing = [m for m in METRICS if m not in df.columns]
    if missing:
        print(f"  Warning: columns not found in data: {missing}", file=sys.stderr)
    keep = ["date", "court_id"] + available
    return df.loc[df["court_id"].isin(courts), keep].copy()


# ─── Report building ──────────────────────────────────────────────────────────

def build_wide(data: pd.DataFrame, all_dates: list) -> pd.DataFrame:
    """
    Build a wide-format DataFrame:
      - Rows: one (court, metric) pair per row, plus a leading day-of-week row
      - Columns: court_id, metric, then one column per date (YYYY-MM-DD)

    The day-of-week row has court_id='' and metric='day_of_week'.
    """
    available = [m for m in METRICS if m in data.columns]
    date_cols = [str(d) for d in all_dates]

    rows = []

    # Day-of-week header row
    dow_row = {"court_id": "", "metric": "day_of_week"}
    for d in all_dates:
        dow_row[str(d)] = DAY_ABBR[d.weekday()]
    rows.append(dow_row)

    for court in TARGET_COURTS:
        c = data[data["court_id"] == court].set_index("date")
        for metric in available:
            is_dollar = "dollars" in metric
            row = {"court_id": court, "metric": LABELS[metric]}
            for d in all_dates:
                if d in c.index:
                    val = c.at[d, metric]
                    row[str(d)] = round(float(val), 2) if is_dollar else int(val)
                else:
                    row[str(d)] = ""
            rows.append(row)

    return pd.DataFrame(rows, columns=["court_id", "metric"] + date_cols)


def print_time_series(wide: pd.DataFrame, all_dates: list):
    """
    Print the wide table to stdout in a per-court block layout.
    Each court gets its own block: date headers, day-of-week row, then metric rows.
    """
    available_labels = [LABELS[m] for m in METRICS if m in
                        [r for r in wide["metric"].unique()]]
    date_cols = [str(d) for d in all_dates]
    cell_w = 7

    dow_row = wide[wide["metric"] == "day_of_week"].iloc[0]

    print()
    print("=" * 80)
    print("  TIME SERIES — ALL DATES")
    print(f"  {all_dates[0]} through {all_dates[-1]}  ({len(all_dates)} days)")
    print("=" * 80)

    for court in TARGET_COURTS:
        court_rows = wide[wide["court_id"] == court]
        if court_rows.empty:
            continue

        print(f"\n  {court.upper()}")

        # Date header
        date_hdr = "".join(f" {str(d)[5:]:>{cell_w}}" for d in all_dates)
        print(f"  {'':12}{date_hdr}")

        # Day-of-week row
        dow_hdr = "".join(f" {dow_row[str(d)]:>{cell_w}}" for d in all_dates)
        print(f"  {'Day':12}{dow_hdr}")

        print(f"  {'-'*12}" + f" {'-'*cell_w}" * len(all_dates))

        for _, mrow in court_rows.iterrows():
            label = mrow["metric"]
            is_dollar = label == "Err $" or label == "Succ $"
            row_str = f"  {label:<12}"
            for d in all_dates:
                val = mrow[str(d)]
                if val == "":
                    row_str += f" {'N/A':>{cell_w}}"
                elif is_dollar:
                    row_str += f" {float(val):>{cell_w}.1f}"
                else:
                    row_str += f" {int(val):>{cell_w}}"
            print(row_str)

    print()


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="PACER Bulk Updater Cost Analysis Report",
        epilog=textwrap.dedent("""\
            examples:
              %(prog)s
              %(prog)s --data-dir /data/pacer
              %(prog)s --output my_report.csv
              %(prog)s --include-cr
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--data-dir",
        default=".",
        metavar="DIR",
        help="Directory containing court_data_*.csv files (default: current directory).",
    )
    parser.add_argument(
        "--output",
        default="bulk_updater_report.csv",
        metavar="FILE",
        help="CSV output file path (default: bulk_updater_report.csv).",
    )
    parser.add_argument(
        "--include-cr",
        action="store_true",
        help="Also include the _cr (criminal) variant of each court.",
    )
    args = parser.parse_args()

    # ── Load ──────────────────────────────────────────────────────────────────
    print("Loading data...", file=sys.stderr)
    raw = load_data(args.data_dir)
    data = filter_data(raw, args.include_cr)

    all_dates = sorted(data["date"].unique())
    earliest  = min(all_dates)
    latest    = max(all_dates)

    # ── Build wide table ───────────────────────────────────────────────────────
    wide = build_wide(data, all_dates)

    # ── Print to stdout ────────────────────────────────────────────────────────
    print(f"  Data range: {earliest} → {latest}  ({len(all_dates)} days)", file=sys.stderr)
    print_time_series(wide, all_dates)

    # ── Write CSV ─────────────────────────────────────────────────────────────
    wide.to_csv(args.output, index=False)
    print(f"  CSV written to: {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
