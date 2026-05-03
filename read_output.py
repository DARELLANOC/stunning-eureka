"""Parse OpenFAST .outb or .out output files with unit and channel validation."""

from __future__ import annotations

import io
import os
from typing import Dict, Iterable, Optional, Tuple

import numpy as np
import pandas as pd


def _read_out_ascii(path: str) -> Tuple[pd.DataFrame, Optional[Dict[str, str]]]:
    """Read ASCII output and extract channel units if available.
    
    Returns:
        (dataframe, units_dict) where units_dict maps column_name -> unit_string.
    """
    with open(path, "r", encoding="utf-8", errors="ignore") as f_in:
        lines = f_in.readlines()

    header_idx = None
    units_idx = None
    for idx, line in enumerate(lines):
        if line.strip().startswith("Time") and "(s)" in line:
            header_idx = idx
            break
        if line.strip().startswith("Time"):
            header_idx = idx
    
    # Try to find units line (typically immediately after header or with offset +1).
    if header_idx is not None and header_idx + 1 < len(lines):
        maybe_units = lines[header_idx + 1]
        if "(" in maybe_units and ")" in maybe_units:
            units_idx = header_idx + 1
    
    if header_idx is None or header_idx + 2 >= len(lines):
        raise RuntimeError(f"Could not parse ASCII OpenFAST output header: {path}")

    headers = lines[header_idx].split()
    data_start = header_idx + 2 if units_idx is None else header_idx + 2
    data_text = "".join(lines[data_start:])
    df = pd.read_csv(io.StringIO(data_text), sep=r"\s+", names=headers, engine="python")

    # Drop non-numeric footer rows if present.
    df["Time"] = pd.to_numeric(df["Time"], errors="coerce")
    df = df.dropna(subset=["Time"]).reset_index(drop=True)
    
    # Extract units if available.
    units_dict = None
    if units_idx is not None:
        units_line = lines[units_idx]
        units_dict = {}
        parts = units_line.split()
        for i, col in enumerate(headers):
            if i < len(parts):
                match = parts[i]
                if "(" in match and ")" in match:
                    units_dict[col] = match
    
    return df, units_dict


def _read_outb_binary(path: str) -> Tuple[pd.DataFrame, Optional[Dict[str, str]]]:
    """Read binary .outb and extract unit metadata if available.
    
    Returns:
        (dataframe, units_dict).
    """
    # Optional parser via openfast-toolbox.
    try:
        from openfast_toolbox.io import FASTOutputFile  # type: ignore
        fof = FASTOutputFile(path)
        df = fof.toDataFrame()
        units_dict = getattr(fof, 'units', None)
        if units_dict is None:
            # Fallback: try to extract from metadata.
            units_dict = {col: getattr(fof, f'{col}_unit', '') for col in df.columns}
        return df, units_dict
    except Exception:
        pass

    try:
        from pyFAST.input_output.fast_output_file import FASTOutputFile  # type: ignore
        fof = FASTOutputFile(path)
        df = fof.toDataFrame()
        units_dict = getattr(fof, 'units', None)
        return df, units_dict
    except Exception as exc:
        raise RuntimeError(
            "Could not parse .outb. Install openfast-toolbox (or pyFAST) "
            "or configure OpenFAST to write ASCII .out outputs."
        ) from exc


def read_openfast_output(
    output_base: str,
    required_channels: Iterable[str],
    transient_cutoff: float,
    usable_time: float,
) -> Tuple[pd.DataFrame, Dict[str, object]]:
    """Load and transient-trim OpenFAST output; validate channel set, duration, and timestep.
    
    Returns:
        (trimmed_dataframe, metadata_dict) where metadata includes units and sample validation.
    """
    metadata = {}
    outb_path = output_base + ".outb"
    out_path = output_base + ".out"

    if os.path.exists(out_path):
        df, units_dict = _read_out_ascii(out_path)
        metadata["output_format"] = "out"
    elif os.path.exists(outb_path):
        df, units_dict = _read_outb_binary(outb_path)
        metadata["output_format"] = "outb"
    else:
        raise FileNotFoundError(f"No OpenFAST output found for base path: {output_base}")

    if units_dict:
        metadata["units"] = units_dict

    if "Time" not in df.columns:
        raise RuntimeError("OpenFAST output is missing Time column.")

    required_list = list(required_channels)
    for ch in required_list:
        if ch not in df.columns:
            raise RuntimeError(f"OpenFAST output missing required channel: {ch}")

    trimmed = df[df["Time"] >= float(transient_cutoff)].copy()
    if trimmed.empty:
        raise RuntimeError("No samples remain after transient trimming.")

    duration = float(trimmed["Time"].max() - trimmed["Time"].min())
    if duration < float(usable_time):
        raise RuntimeError(
            f"Usable duration too short after trimming: {duration:.2f} s < {usable_time:.2f} s"
        )
    
    # Timestep validation: compute mean and report for QA.
    if len(trimmed) > 1:
        time_diffs = np.diff(trimmed["Time"].values)
        mean_dt = float(np.mean(time_diffs))
        std_dt = float(np.std(time_diffs))
        metadata["mean_timestep_s"] = mean_dt
        metadata["std_timestep_s"] = std_dt
        metadata["n_samples"] = len(trimmed)
        # Flag if timestep is highly variable.
        if std_dt > 0.5 * mean_dt:
            metadata["timestep_warning"] = (
                f"Variable timestep detected: mean={mean_dt:.4f}, std={std_dt:.4f}"
            )
    
    cols = ["Time"] + required_list
    return trimmed[cols].reset_index(drop=True), metadata


__all__ = ["read_openfast_output"]
