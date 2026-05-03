"""Rainflow cycle counting and per-bin DEL computation (Eq. 5)."""

from __future__ import annotations

from typing import Dict, Mapping

import numpy as np
import rainflow


def compute_del_from_series(series: np.ndarray, m: float, n_ref: float) -> float:
    """Compute DEL from one time series using Eq. 5.

    DEL_bin = [ sum_j( S_j^m * n_j ) / N_ref ]^(1/m)
    """
    cycles = rainflow.extract_cycles(np.asarray(series, dtype=float))
    accum = 0.0
    for cycle in cycles:
        # rainflow returns (range, mean, count, i_start, i_end)
        s_j = float(cycle[0])
        n_j = float(cycle[2])
        accum += (s_j ** m) * n_j

    if accum <= 0.0:
        return 0.0
    return (accum / float(n_ref)) ** (1.0 / float(m))


def compute_channel_dels(
    timeseries_df,
    sn_slopes: Mapping[str, float],
    n_ref: float,
) -> Dict[str, float]:
    """Compute per-channel DELs for one (wind speed, seed) realization."""
    dels = {}
    for channel, m_value in sn_slopes.items():
        if channel not in timeseries_df.columns:
            raise KeyError(f"Required channel missing from output: {channel}")
        dels[channel] = compute_del_from_series(timeseries_df[channel].values, m=m_value, n_ref=n_ref)
    return dels
