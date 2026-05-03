#!/usr/bin/env bash
# Copy this file to env_linux.sh and edit for your server, then:
#   source env_linux.sh

export OPENFAST_EXE="/opt/openfast/bin/openfast"
export TURBSIM_EXE="/opt/openfast/bin/turbsim"
export OPENFAST_CASE_DIR="/opt/openfast/r-test/glue-codes/openfast/5MW_Land_DLL_WTurb"

# Optional overrides (normally derived from OPENFAST_CASE_DIR):
# export TURBSIM_TEMPLATE="/opt/openfast/r-test/glue-codes/openfast/5MW_Baseline/Wind/90m_12mps_twr.inp"
# export INFLOW_FILE="/opt/openfast/r-test/glue-codes/openfast/5MW_Baseline/NRELOffshrBsline5MW_InflowWind_12mps.dat"

# Optional output locations:
# export OUTPUT_DIR="/data/wind-study/outputs"
# export TURBSIM_DIR="/data/wind-study/turbsim_winds"
