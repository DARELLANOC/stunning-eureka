"""Hub-height Weibull parameters used for long-term DEL sensitivity."""

# Hub-height Weibull parameters (k = shape, c = scale in m/s)
# From Section 3.1 analysis.
# Three methods: Method of Moments (MOM), Energy Pattern Factor (EPF),
# Maximum Likelihood Estimation (MLE).

WEIBULL_PARAMS = {
    "MOM": {"k": 1.9679, "c": 8.2025},
    "EPF": {"k": 1.8905, "c": 8.1934},
    "MLE": {"k": 1.9946, "c": 8.2454},
}
