"""Parallel entrypoint for OpenFAST/TurbSim DEL pipeline.

Usage examples:
    python run_parallel.py --workers 4
    python run_parallel.py --workers 8 --no-skip-existing

Environment-variable overrides for portability (optional):
    OPENFAST_EXE, TURBSIM_EXE, OPENFAST_CASE_DIR,
    TURBSIM_TEMPLATE, INFLOW_FILE, OUTPUT_DIR, TURBSIM_DIR
"""

from __future__ import annotations

import argparse
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from long_term_del import compute_long_term_del_sensitivity
from main import _benchmark_gate, _error_log_path, _metadata_path, log_error
from parameters import (
    BENCHMARK_TOLERANCE_PCT,
    CI_CONFIDENCE,
    CI_METHOD,
    CUT_IN_SPEED,
    CUT_OUT_SPEED,
    DEL_PRECISION,
    DEL_UNITS,
    DRY_RUN,
    N_REF,
    N_SEEDS,
    OUTLIER_MAD_THRESHOLD,
    OUTPUT_DIR,
    RECORD_METADATA,
    SKIP_EXISTING,
    SN_SLOPES,
    TRANSIENT_CUTOFF,
    TURBSIM_DIR,
    UNIT_CONVERSION,
    USABLE_TIME,
    WIND_SPEEDS,
)
from plot_results import plot_del_vs_windspeed, plot_sensitivity_barchart
from rainflow_del import compute_channel_dels
from read_output import read_openfast_output
from run_openfast import run_openfast, validate_openfast_setup
from run_turbsim import run_turbsim
from wind_params import WEIBULL_PARAMS


@dataclass
class CaseResult:
    wind_speed: int
    seed: int
    row: Optional[Dict[str, float]] = None
    read_metadata: Optional[dict] = None
    error: Optional[str] = None


def _build_del_columns() -> List[str]:
    return [
        "wind_speed",
        "seed",
        "RootMyb1_DEL",
        "RootMxb1_DEL",
        "TwrBsMyt_DEL",
        "TwrBsMxt_DEL",
    ]


def _case_worker(
    wind_speed: int,
    seed_idx: int,
    dry_run: bool,
    skip_existing: bool,
    print_lock: threading.Lock,
) -> CaseResult:
    case_label = f"ws{int(wind_speed):02d}_seed{seed_idx + 1:02d}"

    def _printer(msg: str) -> None:
        with print_lock:
            print(f"[{case_label}] {msg}", flush=True)

    ok_turb, turb_msg = run_turbsim(
        wind_speed=wind_speed,
        seed_idx=seed_idx,
        dry_run=dry_run,
        skip_existing=skip_existing,
        printer=_printer,
    )
    if not ok_turb:
        return CaseResult(
            wind_speed=wind_speed,
            seed=seed_idx + 1,
            error=f"TurbSim failed at wind={wind_speed}, seed={seed_idx + 1}: {turb_msg}",
        )

    ok_fast, fast_msg = run_openfast(
        wind_speed=wind_speed,
        seed_idx=seed_idx,
        bts_path=turb_msg,
        dry_run=dry_run,
        skip_existing=skip_existing,
        printer=_printer,
    )
    if not ok_fast:
        return CaseResult(
            wind_speed=wind_speed,
            seed=seed_idx + 1,
            error=f"OpenFAST failed at wind={wind_speed}, seed={seed_idx + 1}: {fast_msg}",
        )

    if dry_run:
        return CaseResult(wind_speed=wind_speed, seed=seed_idx + 1)

    try:
        ts_df, read_metadata = read_openfast_output(
            output_base=fast_msg,
            required_channels=list(SN_SLOPES.keys()),
            transient_cutoff=TRANSIENT_CUTOFF,
            usable_time=USABLE_TIME,
        )
    except Exception as exc:
        return CaseResult(
            wind_speed=wind_speed,
            seed=seed_idx + 1,
            error=f"Read/trim failed at wind={wind_speed}, seed={seed_idx + 1}: {exc}",
        )

    try:
        dels = compute_channel_dels(ts_df, SN_SLOPES, N_REF)
        dels_pub = {ch: val * UNIT_CONVERSION for ch, val in dels.items()}
    except Exception as exc:
        return CaseResult(
            wind_speed=wind_speed,
            seed=seed_idx + 1,
            error=f"Rainflow/DEL failed at wind={wind_speed}, seed={seed_idx + 1}: {exc}",
        )

    row = {
        "wind_speed": int(wind_speed),
        "seed": int(seed_idx + 1),
    }
    row.update({f"{ch}_DEL": float(val) for ch, val in dels_pub.items()})

    return CaseResult(
        wind_speed=wind_speed,
        seed=seed_idx + 1,
        row=row,
        read_metadata=read_metadata,
    )


def _write_checkpoint(del_csv: str, rows: List[Dict[str, float]], del_cols: List[str]) -> None:
    if not rows:
        return
    del_df = pd.DataFrame(rows, columns=del_cols)
    del_df = del_df.sort_values(["wind_speed", "seed"]).reset_index(drop=True)
    del_df.to_csv(del_csv, index=False)


def run_parallel_pipeline(workers: int, dry_run: bool, skip_existing: bool) -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(TURBSIM_DIR, exist_ok=True)

    ok_setup, setup_msg = validate_openfast_setup()
    if not ok_setup:
        err = f"OpenFAST setup validation failed: {setup_msg}"
        print(f"Pipeline stopped: {err}")
        log_error(err)
        return

    del_cols = _build_del_columns()
    del_csv = os.path.abspath(os.path.join(OUTPUT_DIR, "del_results.csv"))

    metadata_log = {
        "timestamp": datetime.now().isoformat(),
        "runner": "run_parallel.py",
        "workers": workers,
        "configuration": {
            "wind_speeds": WIND_SPEEDS,
            "n_seeds": N_SEEDS,
            "cut_in_ms": CUT_IN_SPEED,
            "cut_out_ms": CUT_OUT_SPEED,
            "transient_cutoff_s": TRANSIENT_CUTOFF,
            "usable_time_s": USABLE_TIME,
            "n_ref": N_REF,
            "sn_slopes": SN_SLOPES,
            "del_units": DEL_UNITS,
            "unit_conversion_factor": UNIT_CONVERSION,
            "benchmark_tolerance_pct": BENCHMARK_TOLERANCE_PCT,
            "ci_method": CI_METHOD,
            "ci_confidence": CI_CONFIDENCE,
            "outlier_threshold_mad": OUTLIER_MAD_THRESHOLD,
        },
        "results_summary": {},
        "benchmark_results": None,
        "errors": [],
    }

    rows: List[Dict[str, float]] = []
    completed_cases = set()

    if skip_existing and os.path.exists(del_csv):
        try:
            existing_del_df = pd.read_csv(del_csv)
            if all(col in existing_del_df.columns for col in del_cols):
                for _, rec in existing_del_df[del_cols].dropna(subset=["wind_speed", "seed"]).iterrows():
                    ws_key = int(float(rec["wind_speed"]))
                    seed_key = int(float(rec["seed"]))
                    completed_cases.add((ws_key, seed_key))
                    rows.append(
                        {
                            "wind_speed": ws_key,
                            "seed": seed_key,
                            "RootMyb1_DEL": float(rec["RootMyb1_DEL"]),
                            "RootMxb1_DEL": float(rec["RootMxb1_DEL"]),
                            "TwrBsMyt_DEL": float(rec["TwrBsMyt_DEL"]),
                            "TwrBsMxt_DEL": float(rec["TwrBsMxt_DEL"]),
                        }
                    )
                if completed_cases:
                    print(f"Resume mode: loaded {len(completed_cases)} existing DEL rows from {del_csv}")
        except Exception as exc:
            log_error(f"Could not load existing DEL checkpoint CSV ({del_csv}): {exc}")

    cases = []
    for wind_speed in WIND_SPEEDS:
        for seed_idx in range(N_SEEDS):
            case_key = (int(wind_speed), seed_idx + 1)
            if case_key not in completed_cases:
                cases.append((int(wind_speed), seed_idx))

    if not cases:
        print("No pending cases found. Proceeding with post-processing from existing DEL rows.")

    print_lock = threading.Lock()
    total_cases = len(cases)
    completed = 0

    if cases:
        print(f"Starting parallel run with {workers} workers across {total_cases} pending cases.")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {
            pool.submit(_case_worker, ws, seed_idx, dry_run, skip_existing, print_lock): (ws, seed_idx)
            for ws, seed_idx in cases
        }

        for fut in as_completed(future_map):
            res = fut.result()
            completed += 1

            if res.error:
                log_error(res.error)
                metadata_log["errors"].append(res.error)
            elif res.row is not None:
                rows.append(res.row)
                if RECORD_METADATA and res.read_metadata is not None:
                    metadata_log["results_summary"][f"ws{res.wind_speed}_seed{res.seed}"] = res.read_metadata
                _write_checkpoint(del_csv, rows, del_cols)

            print(f"Progress: {completed}/{total_cases} cases completed.", flush=True)

    if dry_run:
        print("Dry-run complete. No DEL computations or output files were written.")
        return

    del_df = pd.DataFrame(rows, columns=del_cols)
    del_df = del_df.sort_values(["wind_speed", "seed"]).reset_index(drop=True)

    for ch in SN_SLOPES.keys():
        col = f"{ch}_DEL"
        if col in del_df.columns:
            del_df[col] = del_df[col].round(DEL_PRECISION)

    del_df.to_csv(del_csv, index=False)
    print(f"Saved per-bin DELs: {del_csv}")

    if del_df.empty:
        err = "No valid DEL rows were produced. Check outputs/errors.log for TurbSim/OpenFAST/read failures."
        print(f"Pipeline stopped: {err}")
        log_error(err)
        metadata_log["errors"].append(err)
        with open(_metadata_path(), "w", encoding="utf-8") as f_meta:
            json.dump(metadata_log, f_meta, indent=2)
        return

    print("Running benchmark validation gate...")
    gate_pass, gate_results = _benchmark_gate(del_df, list(SN_SLOPES.keys()))
    metadata_log["benchmark_results"] = gate_results

    if gate_pass:
        print(f"BENCHMARK GATE PASSED: All channels within +/-{BENCHMARK_TOLERANCE_PCT}% tolerance.")
    else:
        print("BENCHMARK GATE FAILED. Details saved to metadata.")

    print("Computing long-term DEL with operating-range conditioning...")
    sens_df, lt_metadata = compute_long_term_del_sensitivity(
        del_results=del_df,
        weibull_params=WEIBULL_PARAMS,
        sn_slopes=SN_SLOPES,
        cut_in=CUT_IN_SPEED,
        cut_out=CUT_OUT_SPEED,
        validate=True,
    )

    for col in sens_df.columns:
        if col != "method" and col.endswith("_DELLT"):
            sens_df[col] = sens_df[col].round(DEL_PRECISION)

    metadata_log["results_summary"]["long_term_metadata"] = lt_metadata

    sens_csv = os.path.abspath(os.path.join(OUTPUT_DIR, "long_term_del_sensitivity.csv"))
    sens_df.to_csv(sens_csv, index=False)
    print(f"Saved long-term DEL sensitivity: {sens_csv}")

    print("Generating plots...")
    plot_del_vs_windspeed(
        del_df,
        output_path=os.path.abspath(os.path.join(OUTPUT_DIR, "del_vs_windspeed.png")),
    )
    plot_sensitivity_barchart(
        sens_df,
        output_path=os.path.abspath(os.path.join(OUTPUT_DIR, "del_sensitivity_barchart.png")),
    )

    metadata_json = _metadata_path()
    with open(metadata_json, "w", encoding="utf-8") as f_meta:
        json.dump(metadata_log, f_meta, indent=2, default=str)

    print("Pipeline complete.")
    print(f"DEL results: {del_csv}")
    print(f"Long-term sensitivity: {sens_csv}")
    print(f"Metadata: {metadata_json}")
    print(f"Error log (if any): {_error_log_path()}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run DEL pipeline in parallel across wind-speed/seed bins.")
    default_workers = max(1, (os.cpu_count() or 2) // 2)

    parser.add_argument(
        "--workers",
        type=int,
        default=default_workers,
        help=f"Number of parallel workers (default: {default_workers}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=DRY_RUN,
        help="Prepare and log jobs without launching TurbSim/OpenFAST.",
    )
    parser.add_argument(
        "--skip-existing",
        dest="skip_existing",
        action="store_true",
        default=SKIP_EXISTING,
        help="Skip jobs with existing .bts/.out outputs/checkpoint rows.",
    )
    parser.add_argument(
        "--no-skip-existing",
        dest="skip_existing",
        action="store_false",
        help="Force rerun even if outputs/checkpoints exist.",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.workers < 1:
        raise ValueError("--workers must be >= 1")

    run_parallel_pipeline(
        workers=args.workers,
        dry_run=bool(args.dry_run),
        skip_existing=bool(args.skip_existing),
    )


if __name__ == "__main__":
    main()
