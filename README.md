# OpenFAST DEL Pipeline — Linux Version

Damage Equivalent Load (DEL) analysis pipeline for the NREL 5 MW reference turbine.
Runs TurbSim + OpenFAST across a wind-speed sweep, applies rainflow cycle counting,
and produces a Weibull-weighted long-term DEL sensitivity table.

---

## File Inventory

| File | Purpose |
|------|---------|
| `parameters.py` | Central configuration — paths, sweep settings, DEL parameters |
| `run_turbsim.py` | Generate TurbSim `.inp` files and produce `.bts` wind fields |
| `run_openfast.py` | Prepare per-realization FST/InflowWind files, run OpenFAST, collect outputs |
| `read_output.py` | Parse OpenFAST ASCII `.out` output files into DataFrames |
| `rainflow_del.py` | Rainflow cycle counting and per-channel DEL computation |
| `long_term_del.py` | Weibull-weighted long-term DEL sensitivity (MOM / EPF / MLE) |
| `plot_results.py` | Generate DEL vs. wind speed plots and long-term DEL bar charts |
| `main.py` | Sequential orchestrator (single-threaded, checkpoint-resumable) |
| `run_parallel.py` | Parallel orchestrator using `ThreadPoolExecutor` |
| `wind_params.py` | **Site-specific** Weibull parameters — replace placeholders with real values |
| `requirements.txt` | Python package dependencies |
| `run_parallel_linux.sh` | Bash launcher: creates venv, installs deps, runs the parallel pipeline |
| `env_linux_example.sh` | Template for exporting environment variable overrides |
| `outputs/` | OpenFAST `.out` / `.outb` files and CSV results land here |
| `turbsim_winds/` | TurbSim `.inp` and `.bts` files land here |

---

## Prerequisites

### 1. OpenFAST and TurbSim (Linux binaries)

Build from source or obtain pre-built binaries. The pipeline defaults to:

```
/opt/openfast/bin/openfast
/opt/openfast/bin/turbsim
```

Override via environment variables if your installation differs (see Configuration below).

Build from source (CMake):
```bash
git clone https://github.com/OpenFAST/openfast.git
cd openfast
mkdir build && cd build
cmake .. -DCMAKE_INSTALL_PREFIX=/opt/openfast
make -j$(nproc) openfast turbsim
sudo make install
```

### 2. r-test reference case

The pipeline uses the NREL 5 MW land-based DLL turbine case from the OpenFAST regression tests:

```bash
git clone https://github.com/OpenFAST/r-test.git /opt/openfast/r-test
```

Default case directory:
```
/opt/openfast/r-test/glue-codes/openfast/5MW_Land_DLL_WTurb/
```

The `5MW_Baseline/ServoData/DISCON.so` (or `.dll`) must be present in the sibling `5MW_Baseline/` directory.

### 3. Python ≥ 3.9

```bash
python3 --version   # confirm 3.9+
```

---

## Installation

```bash
cd /path/to/Files_linux

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

---

## Configuration

### Option A — Use defaults

If your binaries and r-test tree are at the default paths listed in `parameters.py`, no
configuration is needed.

### Option B — Environment variables

Copy and edit the example file:

```bash
cp env_linux_example.sh my_env.sh
nano my_env.sh          # fill in your actual paths
source my_env.sh
```

Available overrides:

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENFAST_EXE` | `/opt/openfast/bin/openfast` | Path to OpenFAST binary |
| `TURBSIM_EXE` | `/opt/openfast/bin/turbsim` | Path to TurbSim binary |
| `OPENFAST_CASE_DIR` | r-test 5MW_Land_DLL_WTurb dir | Directory with FST template and supporting files |
| `TURBSIM_TEMPLATE` | `<OPENFAST_CASE_DIR>/TurbSim/…inp` | TurbSim input template |
| `INFLOW_FILE` | `<OPENFAST_CASE_DIR>/…InflowWind.dat` | InflowWind input file |
| `OUTPUT_DIR` | `<script_dir>/outputs` | Where OpenFAST outputs are collected |
| `TURBSIM_DIR` | `<script_dir>/turbsim_winds` | Where `.bts` wind files are stored |

### Site-specific Weibull parameters

Open `wind_params.py` and replace the placeholder `k` and `c` values with the results
from your Weibull fit (MOM, EPF, and MLE methods):

```python
WEIBULL_PARAMS = {
    "MOM": {"k": <your_k>, "c": <your_c>},
    "EPF": {"k": <your_k>, "c": <your_c>},
    "MLE": {"k": <your_k>, "c": <your_c>},
}
```

`k` is the shape parameter, `c` is the scale parameter (m/s).

---

## Running the Pipeline

### Parallel run (recommended)

```bash
bash run_parallel_linux.sh <N_WORKERS>
```

Example — 4 parallel workers (runs up to 4 wind-speed/seed combos simultaneously):

```bash
bash run_parallel_linux.sh 4
```

The script will:
1. Create a `.venv` if it does not already exist
2. Install dependencies from `requirements.txt`
3. Execute `python run_parallel.py --workers <N_WORKERS>`

### Manual parallel run (if venv already active)

```bash
source .venv/bin/activate
python run_parallel.py --workers 4
```

### Sequential run (single-threaded, checkpoint-resumable)

```bash
source .venv/bin/activate
python main.py
```

`main.py` writes results to `outputs/del_results.csv` incrementally and resumes from
that file if re-run (skips completed wind-speed/seed pairs automatically).

### Dry run (preview only, no simulation)

```bash
python run_parallel.py --workers 4 --dry-run
```

### Force re-run (ignore existing results)

```bash
python run_parallel.py --workers 4 --no-skip-existing
```

---

## Expected Outputs

| File | Description |
|------|-------------|
| `outputs/del_results.csv` | Per-realization DEL for each channel, wind speed, and seed |
| `outputs/long_term_del_sensitivity.csv` | Weibull-weighted long-term DEL (one row per method) |
| `outputs/*.png` | DEL vs. wind speed plots; long-term DEL sensitivity bar chart |
| `turbsim_winds/*.bts` | Binary TurbSim wind fields (one per wind-speed/seed combination) |

Default sweep: 11 wind speeds × 6 seeds = **66 OpenFAST simulations**.
Each simulation runs 690 s of physical time (TurbSim generates 700 s of wind data).
Estimated wall time varies by machine; use `--workers` to match available CPU cores.

---

## Key Parameters (`parameters.py`)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `WIND_SPEEDS` | `[4,6,8,10,12,14,16,18,20,22,24]` | Mean hub-height wind speeds (m/s) |
| `N_SEEDS` | `6` | Random seeds per wind speed |
| `ANALYSIS_TIME` | `700` | TurbSim analysis duration (s) |
| `USABLE_TIME` | `625` | Usable wind data used by OpenFAST (s) |
| `TRANSIENT_CUTOFF` | `60` | Seconds discarded at simulation start for DEL (s) |
| `TMax` | `690` | OpenFAST simulation end time in FST template (s) |
| `UNIT_CONVERSION` | `1.0` | OpenFAST v5 already outputs kN·m; keep at 1.0 |
| `SN_SLOPES` | `RootMyb1:10, RootMxb1:10, TwrBsMyt:4, TwrBsMxt:4` | S-N slope m per channel |

---

## Troubleshooting

**`openfast: command not found`**
Set `OPENFAST_EXE` in your environment or confirm the binary path in `parameters.py`.

**`FATAL ERROR` in OpenFAST output**
Check that `OPENFAST_CASE_DIR` points to the correct r-test case with all supporting
files present (`AeroDyn15.dat`, `ElastoDyn.dat`, `ServoDyn.dat`, `DISCON.so`, etc.).

**TurbSim wind array exhausted during simulation**
Confirm `UsableTime = "ALL"` is present in the TurbSim template and that existing
`.bts` files from a previous (shorter) run have been deleted before re-running.

**`ModuleNotFoundError`**
Activate the virtual environment first: `source .venv/bin/activate`

**Permission denied on `.sh` scripts**
```bash
chmod +x run_parallel_linux.sh env_linux_example.sh
```

**Simulation slower than expected**
Use `--workers` equal to the number of physical CPU cores available:
```bash
nproc          # check available cores
bash run_parallel_linux.sh $(nproc)
```
