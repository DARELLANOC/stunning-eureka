"""Plot DEL results across wind speed and Weibull sensitivity with units and uncertainty."""

from __future__ import annotations

import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from parameters import DEL_PRECISION, DEL_UNITS


def plot_del_vs_windspeed(del_df: pd.DataFrame, output_path: str) -> None:
    """Plot per-bin DEL vs wind speed with mean and std across seeds."""
    channels = [
        "RootMyb1_DEL",
        "RootMxb1_DEL",
        "TwrBsMyt_DEL",
        "TwrBsMxt_DEL",
    ]
    title_map = {
        "RootMyb1_DEL": "Blade Flapwise (RootMyb1)",
        "RootMxb1_DEL": "Blade Edgewise (RootMxb1)",
        "TwrBsMyt_DEL": "Tower Fore-Aft (TwrBsMyt)",
        "TwrBsMxt_DEL": "Tower Side-Side (TwrBsMxt)",
    }

    fig, axes = plt.subplots(2, 2, figsize=(12, 8), sharex=True)
    axes = axes.flatten()

    if not del_df.empty:
        grouped = del_df.groupby("wind_speed", as_index=False)
        stats = grouped.agg({ch: ["mean", "std"] for ch in channels})
        stats.columns = ["wind_speed"] + [f"{a}_{b}" for a, b in stats.columns.to_list()[1:]]

        for ax, ch in zip(axes, channels):
            x = stats["wind_speed"].values
            y = stats[f"{ch}_mean"].values
            yerr = np.nan_to_num(stats[f"{ch}_std"].values, nan=0.0)
            ax.plot(x, y, marker="o", linewidth=2, markersize=6)
            ax.fill_between(x, y - yerr, y + yerr, alpha=0.25)
            ax.set_title(title_map[ch], fontsize=11, fontweight="bold")
            ax.set_ylabel(f"DEL ({DEL_UNITS})")
            ax.grid(alpha=0.3)

    for ax in axes[-2:]:
        ax.set_xlabel("Wind Speed (m/s)")

    fig.suptitle(f"DEL vs Wind Speed (mean ± std across {len(del_df) // len(del_df['wind_speed'].unique())} seeds)")
    fig.tight_layout()
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    fig.savefig(output_path, dpi=200)
    plt.close(fig)


def plot_sensitivity_barchart(sens_df: pd.DataFrame, output_path: str) -> None:
    """Plot long-term DEL by Weibull method (MOM, EPF, MLE) with grouped bars."""
    channels = [
        "RootMyb1_DELLT",
        "RootMxb1_DELLT",
        "TwrBsMyt_DELLT",
        "TwrBsMxt_DELLT",
    ]
    channel_labels = {
        "RootMyb1_DELLT": "Blade Flapwise",
        "RootMxb1_DELLT": "Blade Edgewise",
        "TwrBsMyt_DELLT": "Tower Fore-Aft",
        "TwrBsMxt_DELLT": "Tower Side-Side",
    }

    fig, ax = plt.subplots(figsize=(10, 6))

    if not sens_df.empty:
        methods = sens_df["method"].tolist()
        x = np.arange(len(methods))
        width = 0.18

        for i, ch in enumerate(channels):
            ax.bar(
                x + (i - 1.5) * width,
                sens_df[ch].values,
                width=width,
                label=channel_labels[ch],
            )

        ax.set_xticks(x)
        ax.set_xticklabels(methods)

    ax.set_ylabel(f"Long-term DEL ({DEL_UNITS})", fontweight="bold")
    ax.set_xlabel("Weibull Fitting Method", fontweight="bold")
    ax.set_title("Long-term DEL Sensitivity by Weibull Method (Operating Range Conditioned)")
    ax.grid(axis="y", alpha=0.3)
    ax.legend(loc="upper left", fontsize=10)

    fig.tight_layout()
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    fig.savefig(output_path, dpi=200)
    plt.close(fig)


__all__ = ["plot_del_vs_windspeed", "plot_sensitivity_barchart"]
