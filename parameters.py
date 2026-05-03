"""Centralized user-editable parameters for the DEL pipeline."""

import os
from pathlib import Path

# -- Paths --------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent

OPENFAST_EXE = os.environ.get("OPENFAST_EXE", "/opt/openfast/bin/openfast")
TURBSIM_EXE = os.environ.get("TURBSIM_EXE", "/opt/openfast/bin/turbsim")
OPENFAST_CASE_DIR = os.environ.get(
    "OPENFAST_CASE_DIR",
    "/opt/openfast/r-test/glue-codes/openfast/5MW_Land_DLL_WTurb",
)
FST_FILE = "5MW_Land_DLL_WTurb.fst"

_baseline_dir = os.path.normpath(os.path.join(OPENFAST_CASE_DIR, "..", "5MW_Baseline"))
TURBSIM_TEMPLATE = os.environ.get(
    "TURBSIM_TEMPLATE",
    os.path.join(_baseline_dir, "Wind", "90m_12mps_twr.inp"),
)
INFLOW_FILE = os.environ.get(
    "INFLOW_FILE",
    os.path.join(_baseline_dir, "NRELOffshrBsline5MW_InflowWind_12mps.dat"),
)

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", str(PROJECT_ROOT / "outputs"))
TURBSIM_DIR = os.environ.get("TURBSIM_DIR", str(PROJECT_ROOT / "turbsim_winds"))

# -- Turbine ------------------------------------------------------------------
HUB_HEIGHT = 90.0
ROTOR_DIAMETER = 126.0
RATED_POWER_KW = 5000.0
# NREL 5MW controller thresholds (m/s)
CUT_IN_SPEED = 3.0
RATED_SPEED = 12.0
CUT_OUT_SPEED = 25.0

# -- Simulation design ---------------------------------------------------------
WIND_SPEEDS = [4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24]
N_SEEDS = 6
ANALYSIS_TIME = 700
USABLE_TIME = 625
TRANSIENT_CUTOFF = 60

# -- Turbulence ---------------------------------------------------------------
TURB_CLASS = "B"
I_REF = 0.14
IEC_WIND_TYPE = "NTM"

# -- DEL parameters ------------------------------------------------------------
N_REF = 1e7
SN_SLOPES = {
    "RootMyb1": 10,
    "RootMxb1": 10,
    "TwrBsMyt": 4,
    "TwrBsMxt": 4,
}

# -- Publication units and precision -----------------------------------------------
# All DEL outputs reported in kN·m (kiloNewton-meters)
# 1 decimal place (industry standard for fatigue analysis)
DEL_UNITS = "kN·m"
DEL_PRECISION = 1

# Unit conversion: OpenFAST v5 already outputs in kN·m; no conversion needed
UNIT_CONVERSION = 1.0  # already kN·m

# -- Benchmark gate (IEC 61400-1 tolerance for DEL acceptance) -------- 
# Conservative ±10% tolerance for multi-year aging studies
BENCHMARK_TOLERANCE_PCT = 10.0
# Reference baseline method: "nrel" or "custom"
BENCHMARK_METHOD = "nrel"
# NREL 5MW nominal DEL reference values (kN·m) at rated wind speed 12 m/s
# These are representative mid-study values; actual baseline may vary by load case.
BENCHMARK_REFERENCE_KNM = {
    "RootMyb1": 390.0,  # blade flapwise
    "RootMxb1": 110.0,  # blade edgewise
    "TwrBsMyt": 2500.0, # tower fore-aft
    "TwrBsMxt": 850.0,  # tower side-side
}

# -- Outlier detection and uncertainty -----------------------------------------------
# Modified z-score (MAD-based) threshold for flagging anomalous seeds
OUTLIER_MAD_THRESHOLD = 3.5  # conservative for small n=6
# Confidence interval method: "bootstrap" or "normal"
CI_METHOD = "bootstrap"
CI_CONFIDENCE = 0.95
BOOTSTRAP_RESAMPLES = 1000

# -- Reproducibility and logging -----------------------------------------------
RECORD_METADATA = True  # Log version, seed, timestep info for each run
RECORD_CYCLE_DIAGNOSTICS = False  # Optional: save rainflow cycle distributions

# -- Dev flags ----------------------------------------------------------------
DRY_RUN = False
SKIP_EXISTING = True
