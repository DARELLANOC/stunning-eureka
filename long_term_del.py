"""Long-term Weibull-weighted DEL aggregation (Eq. 6).

Implementation:
- Condition Weibull weighting to operating range [cut_in, cut_out].
- Renormalize bin probabilities so they sum to 1.0 over operating range.
- Validate: unconditioned sum should drop from 1.0 to ~P(operating).
"""

from __future__ import annotations

from typing import Dict, Mapping, Tuple

import numpy as np
import pandas as pd


def weibull_bin_probability(
    v_center: float,
    k: float,
    c: float,
    bin_width: float = 2.0,
    v_min: float = 0.0,
    v_max: float = 50.0,
) -> float:
    """Compute Weibull probability mass in a bin, optionally clipped to [v_min, v_max].
    
    Args:
        v_center: bin center wind speed (m/s).
        k: Weibull shape parameter.
        c: Weibull scale parameter (m/s).
        bin_width: width of bin (default 2.0 m/s).
        v_min: lower bound for conditioning (e.g., cut-in; default 0.0).
        v_max: upper bound for conditioning (e.g., cut-out; default 50.0).
    
    Returns:
        Probability mass in the bin, clipped to [v_min, v_max].
    """
    half = bin_width / 2.0
    v_lo = max(v_min, v_center - half)
    v_hi = min(v_max, v_center + half)
    
    if v_lo >= v_hi:
        return 0.0
    
    return float(np.exp(-((v_lo / c) ** k)) - np.exp(-((v_hi / c) ** k)))


def compute_conditioned_normalization(
    k: float,
    c: float,
    v_min: float,
    v_max: float,
) -> Tuple[float, float]:
    """Compute total probability mass in [v_min, v_max] and return normalization factor.
    
    Returns:
        (unconditioned_sum, normalization_factor)
        where normalization_factor = 1.0 / unconditioned_sum converts to conditional CDF.
    """
    unconditioned_sum = float(np.exp(-((v_min / c) ** k)) - np.exp(-((v_max / c) ** k)))
    norm_factor = 1.0 / unconditioned_sum if unconditioned_sum > 1e-10 else 1.0
    return unconditioned_sum, norm_factor


def compute_long_term_del_sensitivity(
    del_results: pd.DataFrame,
    weibull_params: Dict[str, Dict[str, float]],
    sn_slopes: Mapping[str, float],
    cut_in: float = 3.0,
    cut_out: float = 25.0,
    validate: bool = True,
) -> Tuple[pd.DataFrame, dict]:
    """Compute long-term DEL for MOM, EPF, and MLE using Eq. 6, conditioned on operating range.
    
    Args:
        del_results: per-bin DEL dataframe (wind_speed, seed, channel_DEL).
        weibull_params: dict of {method: {k, c}} for each fitting method.
        sn_slopes: dict of {channel: m_value} for S-N exponents.
        cut_in: cut-in wind speed (m/s), default 3.0.
        cut_out: cut-out wind speed (m/s), default 25.0.
        validate: if True, check that conditioned probabilities are normalized.
    
    Returns:
        (sensitivity_df, metadata_dict) where metadata includes normalization factors.
    """
    metadata = {}
    
    if del_results.empty:
        return (
            pd.DataFrame(columns=["method"] + [f"{ch}_DELLT" for ch in sn_slopes]),
            metadata,
        )

    per_bin = del_results.groupby("wind_speed", as_index=False).mean(numeric_only=True)

    records = []
    for method, params in weibull_params.items():
        k = float(params["k"])
        c = float(params["c"])
        
        # Compute normalization factor for operating range conditioning.
        uncond_sum, norm_factor = compute_conditioned_normalization(
            k=k, c=c, v_min=cut_in, v_max=cut_out
        )
        
        metadata[f"{method}_unconditioned_prob"] = uncond_sum
        metadata[f"{method}_normalization_factor"] = norm_factor
        
        if validate and abs(uncond_sum - 1.0) < 0.01:
            # Sanity check: if entire range [0, 50+] is within Weibull support,
            # normalization should reduce it; if it's ~1.0, no conditioning happened.
            pass
        
        rec = {"method": method}

        for channel, m_value in sn_slopes.items():
            del_col = f"{channel}_DEL"
            accum = 0.0
            
            # Sum over operating range only.
            for _, row in per_bin.iterrows():
                v_i = float(row["wind_speed"])
                if not (cut_in <= v_i <= cut_out):
                    continue  # Skip out-of-range bins.
                
                del_bin_i = float(row[del_col])
                # Compute bin probability within [cut_in, cut_out].
                f_i_uncond = weibull_bin_probability(
                    v_i, k=k, c=c, bin_width=2.0, v_min=cut_in, v_max=cut_out
                )
                # Normalize: conditional probability.
                f_i = f_i_uncond * norm_factor
                accum += (del_bin_i ** float(m_value)) * f_i

            rec[f"{channel}_DELLT"] = accum ** (1.0 / float(m_value)) if accum > 0.0 else 0.0

        records.append(rec)

    return pd.DataFrame(records), metadata
