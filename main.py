"""End-to-end pipeline for OpenFAST/TurbSim fatigue DEL and long-term DEL sensitivity.

Implements:
- Per-bin DEL (Eq. 5) via rainflow cycle counting.
- Long-term DEL (Eq. 6) with operating-range Weibull conditioning.
- Bootstrap confidence intervals for uncertainty quantification.
- Outlier detection (modified z-score, MAD-based).
- Benchmark validation gate with publication-unit conversion.
- Full reproducibility metadata logging.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import scipy.stats

from long_term_del import compute_long_term_del_sensitivity
from parameters import (
    BENCHMARK_METHOD,
    BENCHMARK_REFERENCE_KNM,
    BENCHMARK_TOLERANCE_PCT,
    BOOTSTRAP_RESAMPLES,
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
    UNIT_CONVERSION,
    USABLE_TIME,
    WIND_SPEEDS,
    TURBSIM_DIR,
)
from plot_results import plot_del_vs_windspeed, plot_sensitivity_barchart
from rainflow_del import compute_channel_dels
from read_output import read_openfast_output
from run_openfast import run_openfast, validate_openfast_setup
from run_turbsim import run_turbsim
from wind_params import WEIBULL_PARAMS


def _error_log_path() -> str:
    return os.path.abspath(os.path.join(OUTPUT_DIR, "errors.log"))


def _metadata_path() -> str:
    return os.path.abspath(os.path.join(OUTPUT_DIR, "metadata.json"))


def log_error(message: str) -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(_error_log_path(), "a", encoding="utf-8") as f_log:
        f_log.write(f"[{ts}] {message}\n")


def _detect_outliers_mad(values: np.ndarray, threshold: float = 3.5) -> np.ndarray:
    """Detect outliers using modified z-score with median absolute deviation.
    
    Returns boolean mask where True indicates outlier.
    """
    if len(values) < 3:
        return np.zeros(len(values), dtype=bool)
    
    median = np.median(values)
    mad = np.median(np.abs(values - median))
    if mad < 1e-10:
        return np.zeros(len(values), dtype=bool)
    
    mod_z_scores = 0.6745 * (values - median) / mad
    return np.abs(mod_z_scores) > threshold


def _bootstrap_ci(values: np.ndarray, n_resamples: int = 1000, ci: float = 0.95) -> Tuple[float, float]:
    """Compute bootstrap confidence interval for the mean.
    
    Returns:
        (lower_bound, upper_bound) for the given confidence level.
    """
    if len(values) == 0:
        return (0.0, 0.0)
    
    boot_means = []
    rng = np.random.RandomState(42)  # Deterministic seed for reproducibility.
    for _ in range(n_resamples):
        resample = rng.choice(values, size=len(values), replace=True)
        boot_means.append(np.mean(resample))
    
    alpha = 1.0 - ci
    lower_pct = 100.0 * (alpha / 2.0)
    upper_pct = 100.0 * (1.0 - alpha / 2.0)
    return (np.percentile(boot_means, lower_pct), np.percentile(boot_means, upper_pct))


def _compute_uncertainty_metrics(
    del_df: pd.DataFrame,
    channels: List[str],
) -> pd.DataFrame:
    """Compute per-bin uncertainty: mean, std, CV, and bootstrap CI.
    
    Returns DataFrame with columns:
        wind_speed, {channel}_DEL_mean, {channel}_DEL_std, {channel}_DEL_cv,
        {channel}_DEL_ci_low, {channel}_DEL_ci_high, {channel}_DEL_outlier_count
    """
    out_cols = ["wind_speed"]
    for ch in channels:
        out_cols.extend(
            [
                f"{ch}_DEL_mean",
                f"{ch}_DEL_std",
                f"{ch}_DEL_cv",
                f"{ch}_DEL_ci_low",
                f"{ch}_DEL_ci_high",
                f"{ch}_DEL_outlier_count",
            ]
        )

    if del_df.empty or "wind_speed" not in del_df.columns:
        return pd.DataFrame(columns=out_cols)

    grouped = del_df.groupby("wind_speed")
    records = []
    
    for ws, group in grouped:
        rec = {"wind_speed": ws}
        for ch in channels:
            col = f"{ch}_DEL"
            if col not in group.columns:
                rec[f"{ch}_DEL_mean"] = 0.0
                rec[f"{ch}_DEL_std"] = 0.0
                rec[f"{ch}_DEL_cv"] = 0.0
                rec[f"{ch}_DEL_ci_low"] = 0.0
                rec[f"{ch}_DEL_ci_high"] = 0.0
                rec[f"{ch}_DEL_outlier_count"] = 0
                continue

            values = np.asarray(group[col].to_numpy(), dtype=float)
            
            # Outlier detection.
            outlier_mask = _detect_outliers_mad(values, threshold=OUTLIER_MAD_THRESHOLD)
            outlier_count = int(np.sum(outlier_mask))
            non_outliers = values[~outlier_mask]
            
            if len(non_outliers) > 0:
                mean_val = float(np.mean(non_outliers))
                std_val = float(np.std(non_outliers, ddof=1) if len(non_outliers) > 1 else 0.0)
                cv_val = float(std_val / mean_val if mean_val > 0 else 0.0)
                ci_low, ci_high = _bootstrap_ci(non_outliers, n_resamples=BOOTSTRAP_RESAMPLES, ci=CI_CONFIDENCE)
            else:
                mean_val = 0.0
                std_val = 0.0
                cv_val = 0.0
                ci_low, ci_high = (0.0, 0.0)
            
            rec[f"{ch}_DEL_mean"] = mean_val
            rec[f"{ch}_DEL_std"] = std_val
            rec[f"{ch}_DEL_cv"] = cv_val
            rec[f"{ch}_DEL_ci_low"] = ci_low
            rec[f"{ch}_DEL_ci_high"] = ci_high
            rec[f"{ch}_DEL_outlier_count"] = outlier_count
        
        records.append(rec)
    
    return pd.DataFrame(records, columns=out_cols)


def _benchmark_gate(del_df: pd.DataFrame, channels: List[str]) -> Tuple[bool, Dict[str, object]]:
    """Validate per-bin DEL against benchmark reference with tolerance.
    
    Returns:
        (pass_gate, results_dict) where pass_gate is True if all channels pass.
    """
    results = {
        "method": BENCHMARK_METHOD,
        "tolerance_pct": BENCHMARK_TOLERANCE_PCT,
        "channels_passed": [],
        "channels_failed": [],
        "details": {},
    }
    
    # Use mean values per bin for comparison.
    uncertainty_df = _compute_uncertainty_metrics(del_df, channels)

    if uncertainty_df.empty or "wind_speed" not in uncertainty_df.columns:
        results["reason"] = "No valid DEL data available for benchmark comparison."
        return False, results
    
    for ws in uncertainty_df["wind_speed"].values:
        for ch in channels:
            ref_val = BENCHMARK_REFERENCE_KNM.get(ch)
            if ref_val is None:
                continue
            
            mean_key = f"{ch}_DEL_mean"
            mean_val = uncertainty_df[uncertainty_df["wind_speed"] == ws][mean_key].values
            if len(mean_val) == 0:
                continue
            
            mean_val = float(mean_val[0])
            pct_error = 100.0 * abs(mean_val - ref_val) / ref_val if ref_val != 0 else 0.0
            
            ch_detail = f"{ch}_ws{int(ws)}"
            results["details"][ch_detail] = {
                "reference_knm": ref_val,
                "measured_knm": mean_val,
                "error_pct": pct_error,
                "pass": pct_error <= BENCHMARK_TOLERANCE_PCT,
            }
            
            if pct_error <= BENCHMARK_TOLERANCE_PCT:
                if ch not in results["channels_passed"]:
                    results["channels_passed"].append(ch)
            else:
                if ch not in results["channels_failed"]:
                    results["channels_failed"].append(ch)
    
    pass_gate = len(results["channels_failed"]) == 0 and len(results["channels_passed"]) > 0
    return pass_gate, results


def run_pipeline() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(TURBSIM_DIR, exist_ok=True)

    ok_setup, setup_msg = validate_openfast_setup()
    if not ok_setup:
        err = f"OpenFAST setup validation failed: {setup_msg}"
        print(f"Pipeline stopped: {err}")
        log_error(err)
        return

    del_cols = [
        "wind_speed",
        "seed",
        "RootMyb1_DEL",
        "RootMxb1_DEL",
        "TwrBsMyt_DEL",
        "TwrBsMxt_DEL",
    ]
    del_csv = os.path.abspath(os.path.join(OUTPUT_DIR, "del_results.csv"))

    metadata_log = {
        "timestamp": datetime.now().isoformat(),
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

    rows = []
    completed_cases = set()
    if SKIP_EXISTING and os.path.exists(del_csv):
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
                if len(completed_cases) > 0:
                    print(f"Resume mode: loaded {len(completed_cases)} existing DEL rows from {del_csv}")
        except Exception as exc:
            log_error(f"Could not load existing DEL checkpoint CSV ({del_csv}): {exc}")

    for wind_speed in WIND_SPEEDS:
        for seed_idx in range(N_SEEDS):
            case_key = (int(wind_speed), seed_idx + 1)
            if case_key in completed_cases:
                print(
                    f"[Bin {wind_speed} m/s | Seed {seed_idx + 1}/{N_SEEDS}] "
                    "Skipping DEL computation (checkpoint exists)."
                )
                continue

            print(f"[Bin {wind_speed} m/s | Seed {seed_idx + 1}/{N_SEEDS}] Running...")

            ok_turb, turb_msg = run_turbsim(
                wind_speed=wind_speed,
                seed_idx=seed_idx,
                dry_run=DRY_RUN,
                skip_existing=SKIP_EXISTING,
                printer=print,
            )
            if not ok_turb:
                err = f"TurbSim failed at wind={wind_speed}, seed={seed_idx + 1}: {turb_msg}"
                log_error(err)
                metadata_log["errors"].append(err)
                continue
            bts_path = turb_msg

            ok_fast, fast_msg = run_openfast(
                wind_speed=wind_speed,
                seed_idx=seed_idx,
                bts_path=bts_path,
                dry_run=DRY_RUN,
                skip_existing=SKIP_EXISTING,
                printer=print,
            )
            if not ok_fast:
                err = f"OpenFAST failed at wind={wind_speed}, seed={seed_idx + 1}: {fast_msg}"
                log_error(err)
                metadata_log["errors"].append(err)
                continue

            if DRY_RUN:
                continue

            output_base = fast_msg
            try:
                ts_df, read_metadata = read_openfast_output(
                    output_base=output_base,
                    required_channels=list(SN_SLOPES.keys()),
                    transient_cutoff=TRANSIENT_CUTOFF,
                    usable_time=USABLE_TIME,
                )
                if RECORD_METADATA:
                    metadata_log["results_summary"][f"ws{int(wind_speed)}_seed{seed_idx+1}"] = read_metadata
            except Exception as exc:
                err = f"Read/trim failed at wind={wind_speed}, seed={seed_idx + 1}: {exc}"
                log_error(err)
                metadata_log["errors"].append(err)
                continue

            try:
                dels = compute_channel_dels(ts_df, SN_SLOPES, N_REF)
                # Convert to publication units (kN·m) if needed.
                dels_pub = {ch: val * UNIT_CONVERSION for ch, val in dels.items()}
            except Exception as exc:
                err = f"Rainflow/DEL failed at wind={wind_speed}, seed={seed_idx + 1}: {exc}"
                log_error(err)
                metadata_log["errors"].append(err)
                continue

            row: Dict[str, object] = {
                "wind_speed": wind_speed,
                "seed": seed_idx + 1,
            }
            row.update({f"{ch}_DEL": val for ch, val in dels_pub.items()})
            rows.append(row)

    del_df = pd.DataFrame(rows, columns=del_cols)
    
    # Round to publication precision.
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

    # Benchmark gate.
    print("Running benchmark validation gate...")
    gate_pass, gate_results = _benchmark_gate(del_df, list(SN_SLOPES.keys()))
    metadata_log["benchmark_results"] = gate_results
    
    if gate_pass:
        print(f"✓ BENCHMARK GATE PASSED: All channels within ±{BENCHMARK_TOLERANCE_PCT}% tolerance.")
    else:
        print(f"✗ BENCHMARK GATE FAILED. Details saved to metadata.")
        failed_channels = gate_results.get("channels_failed", [])
        if isinstance(failed_channels, list):
            for ch in failed_channels:
                print(f"  - {ch} outside tolerance.")

    # Long-term DEL with operating-range conditioning.
    print("Computing long-term DEL with operating-range conditioning...")
    sens_df, lt_metadata = compute_long_term_del_sensitivity(
        del_results=del_df,
        weibull_params=WEIBULL_PARAMS,
        sn_slopes=SN_SLOPES,
        cut_in=CUT_IN_SPEED,
        cut_out=CUT_OUT_SPEED,
        validate=True,
    )
    
    # Round to publication precision.
    for col in sens_df.columns:
        if col != "method" and col.endswith("_DELLT"):
            sens_df[col] = sens_df[col].round(DEL_PRECISION)
    
    metadata_log["results_summary"]["long_term_metadata"] = lt_metadata
    
    sens_csv = os.path.abspath(os.path.join(OUTPUT_DIR, "long_term_del_sensitivity.csv"))
    sens_df.to_csv(sens_csv, index=False)
    print(f"Saved long-term DEL sensitivity: {sens_csv}")
    print(f"  Weibull conditioning: cut-in={CUT_IN_SPEED} m/s, cut-out={CUT_OUT_SPEED} m/s")
    print(f"  Normalization factors (operating range): {lt_metadata}")

    # Plots.
    print("Generating plots...")
    plot_del_vs_windspeed(
        del_df,
        output_path=os.path.abspath(os.path.join(OUTPUT_DIR, "del_vs_windspeed.png")),
    )
    plot_sensitivity_barchart(
        sens_df,
        output_path=os.path.abspath(os.path.join(OUTPUT_DIR, "del_sensitivity_barchart.png")),
    )

    # Save metadata.
    metadata_json = _metadata_path()
    with open(metadata_json, "w", encoding="utf-8") as f_meta:
        json.dump(metadata_log, f_meta, indent=2, default=str)

    print("Pipeline complete.")
    print(f"DEL results: {del_csv}")
    print(f"Long-term sensitivity: {sens_csv}")
    print(f"Metadata: {metadata_json}")
    print(f"Error log (if any): {_error_log_path()}")
    if gate_pass:
        print("\n✓ All scientific validation gates passed. Results ready for publication.")
    else:
        print("\n⚠ Review benchmark gate results before publication.")


if __name__ == "__main__":
    run_pipeline()
