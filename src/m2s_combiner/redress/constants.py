from __future__ import annotations

EVENT_URLS_BY_YEAR: dict[int, str] = {
    2023: "https://www.manage2sail.com/da-DK/event/dd4d3bd0-3c4b-45af-93fb-54994550ece4#!/",
    2024: "https://www.manage2sail.com/nl/event/43565da6-2ecc-441f-b3ab-f1f00adc646c#!/",
    2025: "https://www.manage2sail.com/da-dk/event/Onsdagsbanen2025#!/",
    2026: "https://www.manage2sail.com/da-dk/event/Onsdagsbanen2026#!/",
}

EXCLUDED_CLASS_PREFIXES_BY_YEAR: dict[int, tuple[str, ...]] = {
    # Extra beginner class in 2023 should not enter the redress model.
    2023: ("lille bane 3",),
}

EPS = 1e-9
Z50 = 0.6744897501960817
NON_OBS_STATUSES = {"DNS", "DNC", "DSQ", "DNF"}
Q_SEARCH_MIN = 1e-12
Q_SEARCH_MAX = 1e-2
# Soft cap floor used for numerical stability of covariance growth.
P_COV_CAP_FLOOR = 365.0
DH_REFERENCE_HDCP = 1000.0
GROUP_Q_CACHE_FILENAME = "redress_group_q_cache.json"
Q_OBJECTIVE_CHOICES = ("rmse", "mle")
Q_OBJECTIVE_DEFAULT = "mle"
