#!/usr/bin/env python3
"""
PACER Bulk Updater Cost Analysis Report

Reads all court_data_YYYY_MM_DD.csv files and reports on four metrics for
eight target courts that received a recent change:

    dkt_auto_bulk_updater_err_dollars
    dkt_auto_bulk_updater_err_vol
    dkt_auto_bulk_updater_succ_dollars
    dkt_auto_bulk_updater_succ_vol

Three analysis sections are produced:
  1. Before / After  — average per metric before vs after the change date
  2. Recent Trend    — last 7 days vs prior 7 days (runs every day as data grows)
  3. Time Series     — per-court, per-metric daily table for recent N days

Usage:
    python generate_report.py --change-date 2026-07-08
    python generate_report.py --change-date 2026-07-08 --data-dir /path/to/data
    python generate_report.py --change-date 2026-07-08 --days 14
    python generate_report.py --help
"""

import os
import sys
import glob
import argparse
import textwrap
from datetime import datetime, date, timedelta

import pandas as pd


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

# Minimum absolute daily average change required before flagging as notable.
# Guards against flagging noise when the baseline is near zero.
DOLLAR_MIN_ABS = 0.50   # $0.50 / day
VOL_MIN_ABS    = 5      # 5 transactions / day

# Percentage thresholds (applied only when abs change clears the floor above).
NOTABLE_PCT  = 0.25   # >= 25%  →  *   (notable)
MATERIAL_PCT = 0.50   # >= 50%  →  *** (material)


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


# ─── Change-flagging logic ─────────────────────────────────────────────────────

def classify_change(before_avg: float, after_avg: float, is_dollar: bool) -> str:
    """
    Return a flag string indicating how significant the shift is.
      ''           – no meaningful change
      '*   dir X%' – notable  (>= 25 %, above absolute floor)
      '*** dir X%' – material (>= 50 %, above absolute floor)
    """
    min_abs = DOLLAR_MIN_ABS if is_dollar else VOL_MIN_ABS
    delta = after_avg - before_avg

    if abs(delta) < min_abs:
        return ""

    direction = "UP" if delta > 0 else "DOWN"

    if before_avg == 0:
        return f"*** {direction} from zero"

    pct = delta / before_avg
    abs_pct = abs(pct)

    if abs_pct >= MATERIAL_PCT:
        return f"*** {direction} {pct:+.0%}"
    if abs_pct >= NOTABLE_PCT:
        return f"*   {direction} {pct:+.0%}"
    return ""


# ─── Report sections ───────────────────────────────────────────────────────────

WIDTH = 82

def divider(title: str = ""):
    if title:
        print()
        print("=" * WIDTH)
        print(f"  {title}")
        print("=" * WIDTH)
    else:
        print("=" * WIDTH)


def _court_metric_table(data: pd.DataFrame, label_a: str, label_b: str,
                         get_a, get_b):
    """
    Shared table renderer for the Before/After and Recent Trend sections.

    get_a(court_df) → series for the "before/prior" subset
    get_b(court_df) → series for the "after/recent" subset
    """
    available = [m for m in METRICS if m in data.columns]
    any_printed = False

    for court in TARGET_COURTS:
        c = data[data["court_id"] == court]

        a_rows = get_a(c)
        b_rows = get_b(c)

        if a_rows.empty and b_rows.empty:
            continue

        rows = []
        for metric in available:
            is_dollar = "dollars" in metric
            a_avg = a_rows[metric].mean() if not a_rows.empty else 0.0
            b_avg = b_rows[metric].mean() if not b_rows.empty else 0.0
            delta = b_avg - a_avg
            flag = classify_change(a_avg, b_avg, is_dollar)

            if is_dollar:
                a_str = f"${a_avg:9.2f}"
                b_str = f"${b_avg:9.2f}"
                d_str = f"{delta:+10.2f}"
            else:
                a_str = f"{a_avg:10.1f}"
                b_str = f"{b_avg:10.1f}"
                d_str = f"{delta:+10.1f}"

            rows.append((LABELS[metric], a_str, b_str, d_str, flag))

        print(f"\n  ┌─ {court.upper()}")
        print(f"  │  {'Metric':<12} {label_a:>11} {label_b:>11} {'Delta':>10}  Flag")
        print(f"  │  {'-'*12} {'-'*11} {'-'*11} {'-'*10}  {'-'*22}")
        for label, a_str, b_str, d_str, flag in rows:
            print(f"  │  {label:<12} {a_str:>11} {b_str:>11} {d_str:>10}  {flag}")
        any_printed = True

    if not any_printed:
        print("\n  (No data for target courts in this date range.)")


def print_before_after(data: pd.DataFrame, change_date: date):
    before = data[data["date"] < change_date]
    after  = data[data["date"] >= change_date]
    n_before = before["date"].nunique()
    n_after  = after["date"].nunique()

    before_end = change_date - timedelta(days=1)
    divider(f"BEFORE / AFTER COMPARISON"
            f"   (before={n_before} days through {before_end}"
            f" | after={n_after} days from {change_date})")

    if n_before == 0:
        print("\n  WARNING: No data found before the change date. "
              "Verify --change-date is correct.")
    if n_after == 0:
        print("\n  WARNING: No data found on or after the change date.")

    _court_metric_table(
        data,
        label_a=f"Before avg",
        label_b=f"After avg",
        get_a=lambda c: c[c["date"] < change_date],
        get_b=lambda c: c[c["date"] >= change_date],
    )


def print_recent_trend(data: pd.DataFrame):
    """Compare the most-recent 7 days vs the 7 days before that."""
    all_dates = sorted(data["date"].unique())
    if len(all_dates) < 8:
        print("\n  (Not enough dates for a rolling 7-day trend.)")
        return

    recent_dates = all_dates[-7:]
    prior_dates  = all_dates[-14:-7]

    if not prior_dates:
        print("\n  (Fewer than 14 days of data; skipping rolling trend.)")
        return

    divider(f"RECENT TREND  "
            f"(prior 7d: {min(prior_dates)}–{max(prior_dates)}"
            f"  |  recent 7d: {min(recent_dates)}–{max(recent_dates)})")

    _court_metric_table(
        data,
        label_a="Prior 7d",
        label_b="Recent 7d",
        get_a=lambda c: c[c["date"].isin(prior_dates)],
        get_b=lambda c: c[c["date"].isin(recent_dates)],
    )


def print_time_series(data: pd.DataFrame, n_days: int):
    """
    Daily table: one block per court, one row per metric, dates as columns.
    Values are right-aligned in fixed-width cells for quick visual scanning.
    """
    all_dates = sorted(data["date"].unique())
    show_dates = all_dates[-n_days:]

    divider(f"TIME SERIES — LAST {len(show_dates)} DAYS  "
            f"({show_dates[0]} through {show_dates[-1]})")

    available = [m for m in METRICS if m in data.columns]
    cell_w = 7   # width per date column

    for court in TARGET_COURTS:
        c = data[data["court_id"] == court].set_index("date")

        print(f"\n  {court.upper()}")

        # Header row: MM-DD dates
        date_hdr = "".join(f" {str(d)[5:]:>{cell_w}}" for d in show_dates)
        print(f"  {'Metric':<12}{date_hdr}")
        print(f"  {'-'*12}" + f" {'-'*cell_w}" * len(show_dates))

        for metric in available:
            is_dollar = "dollars" in metric
            label = LABELS[metric]
            row = f"  {label:<12}"
            for d in show_dates:
                if d not in c.index:
                    row += f" {'N/A':>{cell_w}}"
                else:
                    val = c.at[d, metric]
                    if is_dollar:
                        row += f" {val:>{cell_w}.1f}"
                    else:
                        row += f" {int(val):>{cell_w}}"
            print(row)


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="PACER Bulk Updater Cost Analysis Report",
        epilog=textwrap.dedent("""\
            examples:
              %(prog)s --change-date 2026-07-08
              %(prog)s --change-date 2026-07-08 --data-dir /data/pacer
              %(prog)s --change-date 2026-07-08 --days 14
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--change-date",
        metavar="YYYY-MM-DD",
        required=True,
        help="First day of the post-change period (e.g. 2026-07-08).",
    )
    parser.add_argument(
        "--data-dir",
        default=".",
        metavar="DIR",
        help="Directory containing court_data_*.csv files (default: current directory).",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=21,
        metavar="N",
        help="Number of recent days to show in the time series (default: 21).",
    )
    parser.add_argument(
        "--include-cr",
        action="store_true",
        help="Also include the _cr (criminal) variant of each court.",
    )
    parser.add_argument(
        "--no-trend",
        action="store_true",
        help="Omit the rolling 7-day Recent Trend section.",
    )
    parser.add_argument(
        "--no-series",
        action="store_true",
        help="Omit the daily Time Series section.",
    )
    args = parser.parse_args()

    try:
        change_date = date.fromisoformat(args.change_date)
    except ValueError:
        sys.exit(f"ERROR: Invalid date '{args.change_date}'. Use YYYY-MM-DD format.")

    # ── Load ──────────────────────────────────────────────────────────────────
    print("Loading data...", file=sys.stderr)
    raw = load_data(args.data_dir)
    data = filter_data(raw, args.include_cr)

    all_dates  = sorted(data["date"].unique())
    earliest   = min(all_dates)
    latest     = max(all_dates)

    # ── Header ────────────────────────────────────────────────────────────────
    print()
    divider()
    print("  PACER BULK UPDATER COST ANALYSIS REPORT")
    print(f"  Generated : {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"  Data range: {earliest} → {latest}  ({len(all_dates)} days loaded)")
    print(f"  Change date: {change_date}")
    print(f"  Courts    : {', '.join(TARGET_COURTS)}")
    print(f"  Incl. _cr : {'Yes' if args.include_cr else 'No'}")
    divider()

    # ── Sections ──────────────────────────────────────────────────────────────
    print_before_after(data, change_date)

    if not args.no_trend:
        print_recent_trend(data)

    if not args.no_series:
        print_time_series(data, args.days)

    # ── Legend ────────────────────────────────────────────────────────────────
    print()
    divider()
    print("  FLAGS")
    print(f"    ***  material change  >= {MATERIAL_PCT:.0%} shift (and |delta| >= floor)")
    print(f"    *    notable change   >= {NOTABLE_PCT:.0%} shift (and |delta| >= floor)")
    print(f"  FLOORS (suppress noise near zero)")
    print(f"    dollars: |avg delta| >= ${DOLLAR_MIN_ABS:.2f}/day to be flagged")
    print(f"    volume : |avg delta| >= {int(VOL_MIN_ABS):d} transactions/day to be flagged")
    divider()
    print()


if __name__ == "__main__":
    main()
