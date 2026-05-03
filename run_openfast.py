"""Modify InflowWind input per realization and execute OpenFAST."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from typing import Callable, Optional, Tuple

from parameters import (
    DRY_RUN,
    FST_FILE,
    INFLOW_FILE,
    OPENFAST_CASE_DIR,
    OPENFAST_EXE,
    OUTPUT_DIR,
    SKIP_EXISTING,
)
from run_turbsim import realization_name


def _replace_keyword_value(line: str, keyword: str, value_token: str) -> str:
    patt = re.compile(rf"^(\s*)(\S+)(\s+{re.escape(keyword)}\b.*)$")
    match = patt.match(line)
    if match:
        return f"{match.group(1)}{value_token}{match.group(3)}\n"

    patt2 = re.compile(rf"^(\s*{re.escape(keyword)}\b\s*)(\S+)(.*)$")
    match = patt2.match(line)
    if match:
        return f"{match.group(1)}{value_token}{match.group(3)}\n"

    return line


def validate_openfast_setup() -> Tuple[bool, str]:
    """Validate critical OpenFAST inputs before long batch runs."""
    if not os.path.exists(OPENFAST_EXE):
        return False, f"OpenFAST executable not found: {OPENFAST_EXE}"

    if not os.path.isdir(OPENFAST_CASE_DIR):
        return False, f"OpenFAST case directory not found: {OPENFAST_CASE_DIR}"

    fst_path = os.path.join(OPENFAST_CASE_DIR, FST_FILE)
    if not os.path.exists(fst_path):
        return False, f"FST file not found: {fst_path}"

    inflow_path = INFLOW_FILE if os.path.isabs(INFLOW_FILE) else os.path.join(OPENFAST_CASE_DIR, INFLOW_FILE)
    if not os.path.exists(inflow_path):
        return False, f"InflowWind template not found: {inflow_path}"

    # The NREL 5MW land cases reference ../5MW_Baseline; verify that dependency exists.
    baseline_dir = os.path.normpath(os.path.join(OPENFAST_CASE_DIR, "..", "5MW_Baseline"))
    if not os.path.isdir(baseline_dir):
        return False, (
            "Missing OpenFAST dependency directory required by NREL 5MW case: "
            f"{baseline_dir}"
        )

    # Validate ServoDyn DLL path if the case uses a Bladed-style controller.
    try:
        with open(fst_path, "r", encoding="utf-8", errors="ignore") as f_fst:
            fst_lines = f_fst.readlines()

        servo_token = None
        for line in fst_lines:
            if "ServoFile" in line and '"' in line:
                m = re.search(r'"([^"]+)"', line)
                if m:
                    servo_token = m.group(1)
                    break

        if servo_token and servo_token.lower() != "unused":
            servo_path = os.path.normpath(os.path.join(OPENFAST_CASE_DIR, servo_token))
            if not os.path.exists(servo_path):
                return False, f"ServoDyn file not found: {servo_path}"

            with open(servo_path, "r", encoding="utf-8", errors="ignore") as f_srv:
                srv_lines = f_srv.readlines()

            dll_token = None
            for line in srv_lines:
                if "DLL_FileName" in line and '"' in line:
                    m = re.search(r'"([^"]+)"', line)
                    if m:
                        dll_token = m.group(1)
                        break

            if dll_token and dll_token.lower() not in {"unused", "none"}:
                dll_path = os.path.normpath(os.path.join(os.path.dirname(servo_path), dll_token))
                if not os.path.exists(dll_path):
                    return False, (
                        "Controller DLL not found for ServoDyn Bladed interface: "
                        f"{dll_path}. Build or provide a 64-bit DISCON.dll and update DLL_FileName."
                    )
    except Exception as exc:
        return False, f"OpenFAST setup inspection failed: {exc}"

    return True, "OK"


def _prepare_inflow_file(case_name: str, bts_path: str) -> str:
    # INFLOW_FILE may be an absolute path or a bare filename relative to OPENFAST_CASE_DIR.
    if os.path.isabs(INFLOW_FILE):
        base_inflow = INFLOW_FILE
    else:
        base_inflow = os.path.join(OPENFAST_CASE_DIR, INFLOW_FILE)
    inflow_basename = f"{case_name}_{os.path.basename(INFLOW_FILE)}"
    inflow_out = os.path.join(OPENFAST_CASE_DIR, inflow_basename)
    bts_token = f'"{os.path.abspath(bts_path)}"'

    with open(base_inflow, "r", encoding="utf-8") as f_in:
        lines = f_in.readlines()

    found = False
    out_lines = []
    for line in lines:
        updated = _replace_keyword_value(line, "Filename_BTS", bts_token)
        if updated != line:
            found = True
        out_lines.append(updated)

    if not found:
        for idx, line in enumerate(out_lines):
            if ".bts" in line.lower():
                out_lines[idx] = re.sub(r'"[^"]*"', lambda m: bts_token, line, count=1)
                found = True
                break

    if not found:
        raise RuntimeError("Could not locate Filename_BTS line in InflowWind file.")

    with open(inflow_out, "w", encoding="utf-8") as f_out:
        f_out.writelines(out_lines)

    return inflow_out


def _prepare_fst_file(case_name: str, inflow_file_path: str, out_root_abs: str) -> str:
    base_fst = os.path.join(OPENFAST_CASE_DIR, FST_FILE)
    fst_out = os.path.join(OPENFAST_CASE_DIR, f"{case_name}.fst")

    with open(base_fst, "r", encoding="utf-8") as f_in:
        lines = f_in.readlines()

    inflow_token = f'"{os.path.basename(inflow_file_path)}"'
    outroot_token = f'"{out_root_abs}"'

    out_lines = []
    for line in lines:
        updated = _replace_keyword_value(line, "InflowFile", inflow_token)
        updated = _replace_keyword_value(updated, "OutFileRoot", outroot_token)
        out_lines.append(updated)

    with open(fst_out, "w", encoding="utf-8") as f_out:
        f_out.writelines(out_lines)

    return fst_out


def run_openfast(
    wind_speed: float,
    seed_idx: int,
    bts_path: str,
    dry_run: bool = DRY_RUN,
    skip_existing: bool = SKIP_EXISTING,
    printer: Optional[Callable[[str], None]] = None,
) -> Tuple[bool, str]:
    """Run OpenFAST for one realization and return (success, output_base_path_or_error)."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    case_name = realization_name(wind_speed, seed_idx)
    out_base = os.path.abspath(os.path.join(OUTPUT_DIR, case_name))
    out_out = out_base + ".out"
    out_outb = out_base + ".outb"

    if skip_existing and (os.path.exists(out_outb) or os.path.exists(out_out)):
        if printer:
            printer(f"Skipping OpenFAST (existing): {out_outb if os.path.exists(out_outb) else out_out}")
        return True, out_base

    inflow_case = _prepare_inflow_file(case_name, bts_path)
    fst_case = _prepare_fst_file(case_name, inflow_case, out_base)

    cmd = [OPENFAST_EXE, fst_case]
    if printer:
        printer("OpenFAST command: " + " ".join(cmd))
        printer("OpenFAST running... this case may take several minutes.")

    if dry_run:
        return True, out_base

    t0 = time.time()
    proc = subprocess.run(cmd, cwd=os.path.abspath(OPENFAST_CASE_DIR), capture_output=True, text=True)
    elapsed_s = time.time() - t0
    if printer:
        printer(f"OpenFAST finished in {elapsed_s / 60.0:.1f} min (exit code {proc.returncode}).")

    # OpenFAST v5 exits with code 1 even on successful runs that include non-fatal
    # warnings (ServoDyn, AeroDyn UA, etc.).  Only treat it as a fatal failure
    # when the output explicitly says "FATAL ERROR".
    combined_output = (proc.stdout or "") + (proc.stderr or "")
    if "FATAL ERROR" in combined_output or "Aborting OpenFAST" in combined_output:
        message = combined_output.strip() or "Unknown OpenFAST error"
        return False, message

    # OpenFAST v5 derives OutFileRoot from the FST filename, so outputs land in
    # OPENFAST_CASE_DIR.  Move them into OUTPUT_DIR so read_openfast_output finds them.
    moved_any = False
    for ext in (".outb", ".out", ".sum"):
        src = os.path.join(OPENFAST_CASE_DIR, f"{case_name}{ext}")
        dst = out_base + ext
        if os.path.exists(src):
            shutil.move(src, dst)
            moved_any = True

    # OpenFAST may return non-zero even on warning-only completion; require
    # at least one output artifact to consider the run successful.
    if not moved_any:
        message = combined_output.strip() or (
            "OpenFAST finished without producing expected output files (.out/.outb/.sum)."
        )
        return False, message

    return True, out_base


__all__ = ["run_openfast", "validate_openfast_setup"]
