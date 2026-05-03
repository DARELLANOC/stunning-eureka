"""Generate TurbSim input files and execute TurbSim."""

from __future__ import annotations

import os
import re
import subprocess
from typing import Callable, Optional, Tuple

from parameters import (
    ANALYSIS_TIME,
    DRY_RUN,
    HUB_HEIGHT,
    IEC_WIND_TYPE,
    N_SEEDS,
    SKIP_EXISTING,
    TURB_CLASS,
    TURBSIM_DIR,
    TURBSIM_EXE,
    TURBSIM_TEMPLATE,
    USABLE_TIME,
)


def _format_token(old_token: str, value) -> str:
    if isinstance(value, str):
        if old_token.startswith("\"") or old_token.startswith("'"):
            return f'"{value}"'
        return value
    if isinstance(value, int):
        return str(value)
    return f"{float(value):.3f}".rstrip("0").rstrip(".")


def _replace_keyword_value(line: str, keyword: str, value) -> str:
    patt1 = re.compile(rf"^(\s*)(\S+)(\s+{re.escape(keyword)}\b.*)$")
    match = patt1.match(line)
    if match:
        old_token = match.group(2)
        new_token = _format_token(old_token, value)
        return f"{match.group(1)}{new_token}{match.group(3)}\n"

    patt2 = re.compile(rf"^(\s*{re.escape(keyword)}\b\s*)(\S+)(.*)$")
    match = patt2.match(line)
    if match:
        old_token = match.group(2)
        new_token = _format_token(old_token, value)
        return f"{match.group(1)}{new_token}{match.group(3)}\n"

    return line


def realization_name(wind_speed: float, seed_idx: int) -> str:
    return f"ws{int(wind_speed):02d}_seed{seed_idx + 1:02d}"


def turbsim_seed(wind_speed: float, seed_idx: int) -> int:
    # Deterministic unique integer per realization.
    return int(wind_speed * 1000 + (seed_idx + 1))


def build_turbsim_input(wind_speed: float, seed_idx: int) -> Tuple[str, str]:
    os.makedirs(TURBSIM_DIR, exist_ok=True)
    case_name = realization_name(wind_speed, seed_idx)
    input_path = os.path.abspath(os.path.join(TURBSIM_DIR, f"{case_name}.inp"))
    bts_path = os.path.abspath(os.path.join(TURBSIM_DIR, f"{case_name}.bts"))

    with open(TURBSIM_TEMPLATE, "r", encoding="utf-8") as f_in:
        lines = f_in.readlines()

    replacements = {
        "RandSeed1": turbsim_seed(wind_speed, seed_idx),
        "URef": float(wind_speed),
        "IECturbc": TURB_CLASS,
        "IEC_WindType": IEC_WIND_TYPE,
        "HubHt": float(HUB_HEIGHT),
        "AnalysisTime": float(ANALYSIS_TIME),
        "UsableTime": '"ALL"',  # Write full AnalysisTime duration; avoids wind-data exhaustion in OpenFAST.
        "GridHeight": 130.0,
        "GridWidth": 130.0,
    }

    out_lines = []
    for line in lines:
        updated = line
        for key, val in replacements.items():
            updated = _replace_keyword_value(updated, key, val)
        out_lines.append(updated)

    with open(input_path, "w", encoding="utf-8") as f_out:
        f_out.writelines(out_lines)

    return input_path, bts_path


def run_turbsim(
    wind_speed: float,
    seed_idx: int,
    dry_run: bool = DRY_RUN,
    skip_existing: bool = SKIP_EXISTING,
    printer: Optional[Callable[[str], None]] = None,
) -> Tuple[bool, str]:
    """Run TurbSim for one realization and return (success, bts_path)."""
    input_path, bts_path = build_turbsim_input(wind_speed, seed_idx)

    if skip_existing and os.path.exists(bts_path):
        if printer:
            printer(f"Skipping TurbSim (existing): {bts_path}")
        return True, bts_path

    cmd = [TURBSIM_EXE, input_path]
    if printer:
        printer("TurbSim command: " + " ".join(cmd))

    if dry_run:
        return True, bts_path

    proc = subprocess.run(cmd, cwd=os.path.abspath(TURBSIM_DIR), capture_output=True, text=True)
    if proc.returncode != 0:
        message = proc.stderr.strip() or proc.stdout.strip() or "Unknown TurbSim error"
        return False, message

    return True, bts_path


__all__ = ["run_turbsim", "realization_name"]
