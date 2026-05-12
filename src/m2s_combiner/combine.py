from __future__ import annotations

import math

import numpy as np
import pandas as pd


LOW_POINT_RULE = "low-point"
HIGH_POINT_RULE = "high-point"
FRACTIONAL_POINT_RULE = "fractional-point"
BAYESIAN_POINT_RULE = "bayesian-point"
SUPPORTED_SCORING_RULES = {LOW_POINT_RULE, HIGH_POINT_RULE, FRACTIONAL_POINT_RULE, BAYESIAN_POINT_RULE}
FRACTIONAL_POINT_SCALE = 100
BAYESIAN_EPSILON = 1e-9
BAYESIAN_WIN_PROBABILITY_SIMULATIONS = 20000


def _format_seconds(total_seconds: float) -> str:
    seconds_int = int(round(total_seconds))
    hours, remainder = divmod(seconds_int, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}"


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def combine_races(
    race_frames: list[pd.DataFrame],
    scoring_rule: str = LOW_POINT_RULE,
) -> pd.DataFrame:
    """Combine per-race rows by matching race labels across all classes."""
    if scoring_rule not in SUPPORTED_SCORING_RULES:
        raise ValueError(f"Unsupported scoring rule: {scoring_rule}")

    if not race_frames:
        return pd.DataFrame(
            columns=[
                "race",
                "race_rank",
                "competitor",
                "boat_name",
                "boat_type",
                "hdcp",
                "sailed_time",
                "beregnet_time",
                "delta",
                "interval",
                "points",
                "series_count",
                "bayes_perf",
            ]
        )

    combined = pd.concat(race_frames, ignore_index=True)
    required_columns = {
        "race",
        "competitor",
        "series",
        "boat_name",
        "boat_type",
        "hdcp",
    }
    missing_columns = sorted(required_columns.difference(combined.columns))
    if missing_columns:
        raise ValueError(f"Combined race inputs are missing required columns: {', '.join(missing_columns)}")

    if "beregnet_seconds" not in combined.columns:
        combined["beregnet_seconds"] = pd.NA
    if "sailed_seconds" not in combined.columns:
        combined["sailed_seconds"] = pd.NA
    if "race_status_code" not in combined.columns:
        combined["race_status_code"] = ""
    if "race_points" not in combined.columns:
        combined["race_points"] = pd.NA
    if "race_rank_raw" not in combined.columns:
        combined["race_rank_raw"] = pd.NA
    if "result_source" not in combined.columns:
        combined["result_source"] = ""
    if "result_note" not in combined.columns:
        combined["result_note"] = ""

    def _first_non_empty(values: pd.Series) -> str:
        for value in values:
            text = str(value).strip()
            if text:
                return text
        return ""

    grouped = (
        combined.groupby(["race", "competitor"], as_index=False)
        .agg(
            beregnet_seconds=("beregnet_seconds", "min"),
            sailed_seconds=("sailed_seconds", "min"),
            boat_name=("boat_name", "first"),
            boat_type=("boat_type", "first"),
            hdcp=("hdcp", "first"),
            race_status_code=("race_status_code", _first_non_empty),
            race_points=("race_points", "min"),
            race_rank_raw=("race_rank_raw", "min"),
            result_source=("result_source", _first_non_empty),
            result_note=("result_note", _first_non_empty),
            series_count=("series", lambda values: values.dropna().nunique()),
        )
        .sort_values(["race", "beregnet_seconds", "competitor"], ascending=[True, True, True], na_position="last")
        .reset_index(drop=True)
    )

    status_series = grouped["race_status_code"].fillna("").astype(str).str.strip().str.upper()
    non_participation_statuses = {"DNS", "DNC", "DSQ"}
    non_participation_mask = status_series.isin(non_participation_statuses)

    grouped["race_rank"] = pd.Series(pd.NA, index=grouped.index, dtype="Int64")
    if "race_rank_raw" in grouped.columns:
        grouped["race_rank_raw"] = pd.to_numeric(grouped["race_rank_raw"], errors="coerce")
        raw_rank_mask = grouped["race_rank_raw"].notna()
        grouped.loc[raw_rank_mask, "race_rank"] = grouped.loc[raw_rank_mask, "race_rank_raw"].round().astype("Int64")

    for race_label, race_rows in grouped.groupby("race"):
        _ = race_label
        finish_idx = race_rows[
            race_rows["beregnet_seconds"].notna() & (~non_participation_mask.loc[race_rows.index])
        ].index
        if len(finish_idx) == 0:
            continue
        ranks = race_rows.loc[finish_idx, "beregnet_seconds"].rank(method="min", ascending=True).astype(int)
        grouped.loc[finish_idx, "race_rank"] = ranks.astype("Int64")

    grouped["race_points"] = pd.to_numeric(grouped["race_points"], errors="coerce")
    grouped["bayes_perf"] = pd.Series(pd.NA, index=grouped.index, dtype="Float64")
    grouped.loc[non_participation_mask, "race_rank"] = pd.NA
    if scoring_rule == LOW_POINT_RULE:
        grouped["points"] = pd.Series(pd.NA, index=grouped.index, dtype="Int64")
        finisher_mask = grouped["beregnet_seconds"].notna() & grouped["race_rank"].notna() & (~non_participation_mask)
        grouped.loc[finisher_mask, "points"] = grouped.loc[finisher_mask, "race_rank"]

        fallback_points_by_race = grouped.groupby("race")["competitor"].nunique().add(1).astype(int).to_dict()
        status_points_mask = grouped["points"].isna() & grouped["race_points"].notna()
        if status_points_mask.any():
            status_points = grouped.loc[status_points_mask, "race_points"].round().astype(int)
            fallback_for_status = grouped.loc[status_points_mask, "race"].map(fallback_points_by_race).astype(int)
            grouped.loc[status_points_mask, "points"] = (
                pd.concat([status_points, fallback_for_status], axis=1).max(axis=1).astype("Int64")
            )

        fallback_mask = grouped["points"].isna()
        grouped.loc[fallback_mask, "points"] = (
            grouped.loc[fallback_mask, "race"].map(fallback_points_by_race).astype("Int64")
        )
    else:
        # High-point rule:
        # Non-participation (e.g. DNS/DNC) is 0 points.
        # Participation is 1 point, plus one point per boat left behind for finishers.
        # Fractional-point rule:
        # Same as high-point, then each race score is divided by that race's participant count.
        # Bayesian-point rule:
        # Uses corrected race times, race-level de-biasing, and handicap-adjusted hierarchical shrinkage.
        is_fractional_rule = scoring_rule == FRACTIONAL_POINT_RULE
        is_bayesian_rule = scoring_rule == BAYESIAN_POINT_RULE

        if is_bayesian_rule:
            grouped["points"] = pd.Series(0.0, index=grouped.index, dtype="Float64")
            finisher_mask = (
                grouped["beregnet_seconds"].notna()
                & grouped["race_rank"].notna()
                & (~non_participation_mask)
            )

            if finisher_mask.any():
                finisher_frame = grouped.loc[finisher_mask, ["race", "beregnet_seconds", "hdcp"]].copy()
                finisher_frame["beregnet_seconds"] = pd.to_numeric(finisher_frame["beregnet_seconds"], errors="coerce")
                finisher_frame["hdcp"] = pd.to_numeric(finisher_frame["hdcp"], errors="coerce")

                race_hdcp_mean = finisher_frame.groupby("race")["hdcp"].transform("mean")
                overall_hdcp_mean = float(finisher_frame["hdcp"].mean()) if finisher_frame["hdcp"].notna().any() else 0.0
                hdcp_filled = finisher_frame["hdcp"].fillna(race_hdcp_mean).fillna(overall_hdcp_mean)

                log_time = finisher_frame["beregnet_seconds"].map(lambda value: math.log(max(float(value), BAYESIAN_EPSILON)))

                log_time_centered = log_time - log_time.groupby(finisher_frame["race"]).transform("mean")
                hdcp_centered = hdcp_filled - hdcp_filled.groupby(finisher_frame["race"]).transform("mean")
                beta_denom = float((hdcp_centered ** 2).sum())
                handicap_beta = (
                    float((hdcp_centered * log_time_centered).sum()) / beta_denom
                    if beta_denom > BAYESIAN_EPSILON
                    else 0.0
                )

                adjusted_performance = -(log_time - handicap_beta * hdcp_filled)
                race_bias = adjusted_performance.groupby(finisher_frame["race"]).transform("mean")
                centered_performance = adjusted_performance - race_bias

                global_std = float(centered_performance.std(ddof=0))
                if not math.isfinite(global_std) or global_std <= BAYESIAN_EPSILON:
                    global_std = 1.0

                race_std = centered_performance.groupby(finisher_frame["race"]).transform("std").fillna(global_std)
                race_std = race_std.replace(0.0, global_std)
                z_score = centered_performance / race_std
                race_point = z_score.map(_normal_cdf).astype("Float64")

                grouped.loc[finisher_mask, "points"] = race_point
                grouped.loc[finisher_mask, "bayes_perf"] = centered_performance.astype("Float64")
        else:
            if is_fractional_rule:
                grouped["points"] = pd.Series(0, index=grouped.index, dtype="Int64")
            else:
                grouped["points"] = pd.Series(0, index=grouped.index, dtype="Int64")
            finisher_mask = (
                grouped["beregnet_seconds"].notna()
                & grouped["race_rank"].notna()
                & (~non_participation_mask)
            )
            participating_mask = finisher_mask | (
                (~non_participation_mask)
                & (
                    grouped["race_rank_raw"].notna()
                    | grouped["race_points"].notna()
                    | status_series.ne("")
                )
            )
            grouped.loc[participating_mask, "points"] = 1

            race_participants_by_race = (
                grouped.loc[participating_mask]
                .groupby("race")["competitor"]
                .nunique()
                .astype(int)
                .to_dict()
            )
            if finisher_mask.any() and race_participants_by_race:
                finish_points = (
                    grouped.loc[finisher_mask, "race"].map(race_participants_by_race).fillna(0).astype(int)
                    + 1
                    - grouped.loc[finisher_mask, "race_rank"].astype(int)
                )
                grouped.loc[finisher_mask, "points"] = finish_points.astype("Int64")

            if is_fractional_rule and race_participants_by_race:
                participants_per_row = grouped["race"].map(race_participants_by_race).astype("Float64")
                valid_participant_count_mask = participants_per_row.notna() & participants_per_row.gt(0)
                grouped.loc[valid_participant_count_mask, "points"] = (
                    grouped.loc[valid_participant_count_mask, "points"].astype("Float64")
                    / participants_per_row.loc[valid_participant_count_mask]
                    * FRACTIONAL_POINT_SCALE
                ).round().astype("Int64")

    grouped["beregnet_time"] = grouped["beregnet_seconds"].map(
        lambda value: _format_seconds(value) if pd.notna(value) else ""
    )
    grouped["sailed_time"] = grouped["sailed_seconds"].map(
        lambda value: _format_seconds(value) if pd.notna(value) else ""
    )

    status_text = grouped["race_status_code"].astype(str).str.strip()
    status_upper = status_text.str.upper()
    non_finish_display_statuses = {"DNS", "DNC", "DSQ"}
    non_finish_status_mask = status_upper.isin(non_finish_display_statuses)

    # Display DSQ like DNS/DNC across all scoring rules: show status, never times.
    grouped.loc[non_finish_status_mask, "beregnet_time"] = status_text.loc[non_finish_status_mask]
    grouped.loc[non_finish_status_mask, "sailed_time"] = ""

    other_status_mask = grouped["beregnet_time"].eq("") & status_text.ne("") & (~non_finish_status_mask)
    grouped.loc[other_status_mask, "beregnet_time"] = status_text.loc[other_status_mask]

    grouped["delta_seconds"] = pd.NA
    grouped["interval_seconds"] = pd.NA
    for race_label, race_rows in grouped.groupby("race"):
        _ = race_label
        finish_idx = race_rows[
            race_rows["beregnet_seconds"].notna() & (~non_participation_mask.loc[race_rows.index])
        ].index
        if len(finish_idx) == 0:
            continue
        finish_times = grouped.loc[finish_idx, "beregnet_seconds"]
        grouped.loc[finish_idx, "delta_seconds"] = finish_times - finish_times.min()
        grouped.loc[finish_idx, "interval_seconds"] = finish_times.diff().fillna(0.0)
    grouped["delta"] = grouped["delta_seconds"].map(
        lambda value: _format_seconds(float(value)) if pd.notna(value) else ""
    )
    grouped["interval"] = grouped["interval_seconds"].map(
        lambda value: _format_seconds(float(value)) if pd.notna(value) else ""
    )

    return grouped[
        [
            "race",
            "race_rank",
            "competitor",
            "boat_name",
            "boat_type",
            "hdcp",
            "sailed_time",
            "beregnet_time",
            "delta",
            "interval",
            "points",
            "series_count",
            "bayes_perf",
            "result_source",
            "result_note",
        ]
    ]


def combine_overall_from_races(
    combined_races: pd.DataFrame,
    max_race: int = 18,
    all_competitors: list[str] | None = None,
    discard_after: list[int] | None = None,
    scoring_rule: str = LOW_POINT_RULE,
) -> pd.DataFrame:
    """Create fixed-size overall standings with R1..Rmax columns and per-rule totals."""
    if scoring_rule not in SUPPORTED_SCORING_RULES:
        raise ValueError(f"Unsupported scoring rule: {scoring_rule}")

    race_columns = [f"R{race_number}" for race_number in range(1, max_race + 1)]

    if combined_races.empty:
        empty = pd.DataFrame(columns=["combined_rank", "competitor", "boat_type", *race_columns, "combined_points", "race_count"])
        return empty

    if "points" not in combined_races.columns:
        raise ValueError("Combined race table must include a points column.")

    synthetic_races_by_competitor: dict[str, list[str]] = {}
    if "result_source" in combined_races.columns:
        synthetic_rows = combined_races.loc[
            combined_races["result_source"].fillna("").astype(str).str.strip().eq("redress-duty"),
            ["competitor", "race"],
        ].drop_duplicates()
        for competitor, race in synthetic_rows.itertuples(index=False):
            synthetic_races_by_competitor.setdefault(str(competitor), []).append(str(race))
        for competitor, races in synthetic_races_by_competitor.items():
            synthetic_races_by_competitor[competitor] = sorted(set(races), key=lambda label: int(label[1:]))

    is_low_point_rule = scoring_rule == LOW_POINT_RULE
    is_fractional_rule = scoring_rule == FRACTIONAL_POINT_RULE
    is_bayesian_rule = scoring_rule == BAYESIAN_POINT_RULE

    boat_type_by_competitor: pd.Series | None = None
    if "boat_type" in combined_races.columns:
        boat_type_candidates = combined_races[["competitor", "boat_type"]].copy()
        boat_type_candidates["boat_type"] = boat_type_candidates["boat_type"].fillna("").astype(str).str.strip()
        boat_type_candidates = boat_type_candidates[boat_type_candidates["boat_type"] != ""]
        if not boat_type_candidates.empty:
            boat_type_by_competitor = (
                boat_type_candidates.drop_duplicates(subset=["competitor"], keep="first")
                .set_index("competitor")["boat_type"]
            )

    working = combined_races[["race", "competitor", "points"]].copy()
    working["points"] = pd.to_numeric(working["points"], errors="coerce")
    if working["points"].isna().any():
        raise ValueError("Combined race table points must be numeric.")

    if is_bayesian_rule:
        if "bayes_perf" not in combined_races.columns:
            raise ValueError("Bayesian scoring requires bayes_perf values in combined race table.")

        bayesian_working = combined_races[["race", "competitor", "points", "bayes_perf"]].copy()
        bayesian_working["points"] = pd.to_numeric(bayesian_working["points"], errors="coerce").fillna(0.0)
        bayesian_working["bayes_perf"] = pd.to_numeric(bayesian_working["bayes_perf"], errors="coerce")

        pivot = bayesian_working.pivot_table(index="competitor", columns="race", values="points", aggfunc="min")
        if all_competitors:
            competitor_index = pd.Index(
                sorted({str(item) for item in all_competitors if str(item).strip()}),
                name="competitor",
            )
            pivot = pivot.reindex(competitor_index)

        pivot = pivot.reindex(columns=race_columns)
        sailed_races = sorted(
            set(bayesian_working["race"].dropna().astype(str)).intersection(race_columns),
            key=lambda label: int(label[1:]),
        )
        for race_label in sailed_races:
            pivot[race_label] = pd.to_numeric(pivot[race_label], errors="coerce").fillna(0.0).astype(float)

        perf_rows = bayesian_working.dropna(subset=["bayes_perf"]).copy()
        perf_count = perf_rows.groupby("competitor")["bayes_perf"].count().astype(int)
        perf_mean = perf_rows.groupby("competitor")["bayes_perf"].mean().astype(float)

        if perf_rows.empty:
            sigma2 = 1.0
            tau2 = 1.0
        else:
            centered = perf_rows["bayes_perf"] - perf_rows["competitor"].map(perf_mean)
            competitor_with_data = int((perf_count > 0).sum())
            dof_within = int(len(perf_rows) - competitor_with_data)
            if dof_within > 0:
                sigma2 = float((centered ** 2).sum() / dof_within)
            else:
                sigma2 = float(perf_rows["bayes_perf"].var(ddof=0))
            if not math.isfinite(sigma2) or sigma2 <= BAYESIAN_EPSILON:
                sigma2 = 1.0

            active_means = perf_mean[perf_count > 0]
            if len(active_means) >= 2:
                var_means = float(active_means.var(ddof=1))
                average_noise = float((sigma2 / perf_count[perf_count > 0].astype(float)).mean())
                tau2 = max(var_means - average_noise, BAYESIAN_EPSILON)
            else:
                tau2 = max(0.05 * sigma2, BAYESIAN_EPSILON)

        if perf_rows.empty:
            speed_sigma2 = 1.0
            speed_tau2 = 1.0
            speed_mu0 = 1.0
            speed_count = pd.Series(dtype="int64")
            speed_mean = pd.Series(dtype="float64")
        else:
            speed_rows = perf_rows.copy()
            speed_rows["speed_obs"] = np.exp(speed_rows["bayes_perf"].astype(float))
            speed_count = speed_rows.groupby("competitor")["speed_obs"].count().astype(int)
            speed_mean = speed_rows.groupby("competitor")["speed_obs"].mean().astype(float)

            centered_speed = speed_rows["speed_obs"] - speed_rows["competitor"].map(speed_mean)
            competitor_with_speed_data = int((speed_count > 0).sum())
            speed_dof_within = int(len(speed_rows) - competitor_with_speed_data)
            if speed_dof_within > 0:
                speed_sigma2 = float((centered_speed ** 2).sum() / speed_dof_within)
            else:
                speed_sigma2 = float(speed_rows["speed_obs"].var(ddof=0))
            if not math.isfinite(speed_sigma2) or speed_sigma2 <= BAYESIAN_EPSILON:
                speed_sigma2 = 1.0

            active_speed_means = speed_mean[speed_count > 0]
            speed_mu0 = float(active_speed_means.mean()) if not active_speed_means.empty else 1.0
            if len(active_speed_means) >= 2:
                speed_var_means = float(active_speed_means.var(ddof=1))
                speed_average_noise = float((speed_sigma2 / speed_count[speed_count > 0].astype(float)).mean())
                speed_tau2 = max(speed_var_means - speed_average_noise, BAYESIAN_EPSILON)
            else:
                speed_tau2 = max(0.05 * speed_sigma2, BAYESIAN_EPSILON)

        posterior_perf_by_competitor: dict[str, float] = {}
        posterior_var_by_competitor: dict[str, float] = {}
        posterior_speed_by_competitor: dict[str, float] = {}
        posterior_speed_var_by_competitor: dict[str, float] = {}
        participation_probability_by_competitor: dict[str, float] = {}
        combined_points_values: list[float | None] = []
        next_race_win_probability_values: list[float | None] = []
        race_count_values: list[int] = []
        discarded_map: dict[str, list[str]] = {}

        total_sailed_races = max(len(sailed_races), 1)

        for competitor in pivot.index:
            sample_count = int(perf_count.get(competitor, 0))
            race_count_values.append(sample_count)
            discarded_map[str(competitor)] = []

            if sample_count <= 0:
                combined_points_values.append(None)
                next_race_win_probability_values.append(None)
                continue

            mean_perf = float(perf_mean.get(competitor, 0.0))
            posterior_var = 1.0 / ((sample_count / sigma2) + (1.0 / tau2))
            posterior_perf = posterior_var * ((sample_count * mean_perf) / sigma2)
            posterior_perf_by_competitor[str(competitor)] = posterior_perf
            posterior_var_by_competitor[str(competitor)] = posterior_var
            win_probability = _normal_cdf(posterior_perf / math.sqrt(2.0 * sigma2))
            combined_points_values.append(win_probability)

            mean_speed = float(speed_mean.get(competitor, speed_mu0))
            posterior_speed_var = 1.0 / ((sample_count / speed_sigma2) + (1.0 / speed_tau2))
            posterior_speed = posterior_speed_var * (
                ((sample_count * mean_speed) / speed_sigma2) + (speed_mu0 / speed_tau2)
            )
            posterior_speed_by_competitor[str(competitor)] = posterior_speed
            posterior_speed_var_by_competitor[str(competitor)] = posterior_speed_var
            participation_probability_by_competitor[str(competitor)] = float(
                (sample_count + 1.0) / (total_sailed_races + 2.0)
            )

            # Placeholder; overwritten below by coherent posterior-predictive race-win probabilities.
            next_race_win_probability_values.append(0.0)

        active_competitors = [
            str(competitor)
            for competitor in pivot.index
            if str(competitor) in posterior_speed_by_competitor
        ]
        if active_competitors:
            posterior_speed_means = np.array([posterior_speed_by_competitor[name] for name in active_competitors], dtype=float)
            predictive_std = np.sqrt(
                np.array([speed_sigma2 + posterior_speed_var_by_competitor[name] for name in active_competitors], dtype=float)
            )
            participation_probabilities = np.array(
                [participation_probability_by_competitor[name] for name in active_competitors],
                dtype=float,
            )
            rng = np.random.default_rng(20260508)
            participation_simulations = (
                rng.random((BAYESIAN_WIN_PROBABILITY_SIMULATIONS, len(active_competitors)))
                < participation_probabilities
            )
            speed_simulations = rng.normal(
                loc=posterior_speed_means,
                scale=predictive_std,
                size=(BAYESIAN_WIN_PROBABILITY_SIMULATIONS, len(active_competitors)),
            )
            speed_simulations = np.maximum(speed_simulations, BAYESIAN_EPSILON)
            pace_simulations = 1.0 / speed_simulations
            pace_simulations[~participation_simulations] = np.inf
            winner_idx = pace_simulations.argmin(axis=1)

            no_participants_mask = ~participation_simulations.any(axis=1)
            winner_idx[no_participants_mask] = -1

            next_race_win_probability_by_competitor: dict[str, float] = {}
            for idx, competitor_name in enumerate(active_competitors):
                participated_mask = participation_simulations[:, idx]
                participated_count = int(participated_mask.sum())
                if participated_count <= 0:
                    continue
                win_count = int(((winner_idx == idx) & participated_mask).sum())
                next_race_win_probability_by_competitor[competitor_name] = float(win_count / participated_count)
        else:
            next_race_win_probability_by_competitor = {}

        for idx, competitor in enumerate(pivot.index):
            competitor_key = str(competitor)
            if competitor_key in next_race_win_probability_by_competitor:
                next_race_win_probability_values[idx] = next_race_win_probability_by_competitor[competitor_key]
            elif race_count_values[idx] <= 0:
                next_race_win_probability_values[idx] = None

        result = pivot.reset_index()
        for column in race_columns:
            if column in result.columns:
                result[column] = pd.to_numeric(result[column], errors="coerce").astype("Float64")

        result["combined_points"] = pd.Series(combined_points_values, dtype="Float64")
        result["next_race_win_probability"] = pd.Series(next_race_win_probability_values, dtype="Float64")
        result["race_count"] = pd.Series(race_count_values, dtype="Int64")
        if boat_type_by_competitor is not None:
            result["boat_type"] = result["competitor"].map(boat_type_by_competitor).fillna("")
        else:
            result["boat_type"] = ""

        result = result.sort_values(["combined_points", "competitor"], ascending=[False, True], na_position="last").reset_index(drop=True)
        result["combined_rank"] = result["combined_points"].rank(method="min", ascending=False)
        result["combined_rank"] = result["combined_rank"].astype("Int64")

        result.attrs["discarded_races_by_competitor"] = discarded_map
        result.attrs["discard_after"] = []
        result.attrs["discard_count"] = 0
        result.attrs["scoring_rule"] = scoring_rule
        result.attrs["bayesian_model"] = "time-hierarchical-normal"
        result.attrs["bayesian_sigma2"] = sigma2
        result.attrs["bayesian_tau2"] = tau2
        result.attrs["bayesian_speed_sigma2"] = speed_sigma2
        result.attrs["bayesian_speed_tau2"] = speed_tau2
        result.attrs["bayesian_speed_mu0"] = speed_mu0
        result.attrs["bayesian_win_probability_method"] = "posterior-predictive-monte-carlo-conditional-on-participation"
        result.attrs["bayesian_win_probability_simulations"] = BAYESIAN_WIN_PROBABILITY_SIMULATIONS
        result.attrs["synthetic_races_by_competitor"] = synthetic_races_by_competitor

        return result[
            [
                "combined_rank",
                "competitor",
                "boat_type",
                *race_columns,
                "combined_points",
                "next_race_win_probability",
                "race_count",
            ]
        ]

    working["points"] = working["points"].round().astype(int)
    participants_by_race = working.groupby("race")["competitor"].nunique().astype(int).to_dict()

    pivot = working.pivot_table(index="competitor", columns="race", values="points", aggfunc="min")

    if all_competitors:
        competitor_index = pd.Index(sorted({str(item) for item in all_competitors if str(item).strip()}), name="competitor")
        pivot = pivot.reindex(competitor_index)
    roster_size = len(pivot.index)

    if is_low_point_rule:
        missing_points_by_race = {
            race_label: max(points, roster_size) + 1
            for race_label, points in participants_by_race.items()
        }
    elif is_fractional_rule:
        missing_points_by_race = {
            race_label: 0
            for race_label in participants_by_race
        }
    else:
        missing_points_by_race = {
            race_label: 0
            for race_label in participants_by_race
        }

    pivot = pivot.reindex(columns=race_columns)

    sailed_races = [label for label in race_columns if label in missing_points_by_race]
    for race_label in sailed_races:
        pivot[race_label] = pivot[race_label].fillna(missing_points_by_race[race_label])

    discard_after = sorted({int(value) for value in (discard_after or []) if int(value) > 0})
    discard_count = sum(1 for threshold in discard_after if threshold <= len(sailed_races))

    def _race_number(label: str) -> int:
        return int(label[1:]) if label.startswith("R") else 0

    discarded_map: dict[str, list[str]] = {}
    combined_points_values: list[int | None] = []
    race_count_values: list[int] = []

    for competitor, row in pivot.iterrows():
        row_values: list[tuple[str, int]] = []
        for race_label in sailed_races:
            value = row[race_label]
            if pd.isna(value):
                continue
            row_values.append((race_label, int(value)))

        discarded_labels: list[str] = []
        if discard_count > 0 and row_values:
            if scoring_rule == LOW_POINT_RULE:
                sorted_worst = sorted(row_values, key=lambda item: (item[1], _race_number(item[0])), reverse=True)
            else:
                sorted_worst = sorted(row_values, key=lambda item: (item[1], -_race_number(item[0])))
            discarded_labels = [label for label, _ in sorted_worst[: min(discard_count, len(sorted_worst))]]
            discarded_labels = sorted(discarded_labels, key=_race_number)

        discarded_set = set(discarded_labels)
        kept_values = [value for label, value in row_values if label not in discarded_set]

        if kept_values:
            combined_points_values.append(int(sum(kept_values)))
        else:
            combined_points_values.append(None)

        race_count_values.append(len(kept_values))
        discarded_map[str(competitor)] = discarded_labels

    result = pivot.reset_index()
    for column in race_columns:
        if column in result.columns:
            result[column] = pd.to_numeric(result[column], errors="coerce").round().astype("Int64")

    result["combined_points"] = pd.Series(combined_points_values, dtype="Int64")
    result["race_count"] = pd.Series(race_count_values, dtype="Int64")
    if boat_type_by_competitor is not None:
        result["boat_type"] = result["competitor"].map(boat_type_by_competitor).fillna("")
    else:
        result["boat_type"] = ""
    ascending_points = is_low_point_rule
    result = result.sort_values(["combined_points", "competitor"], ascending=[ascending_points, True], na_position="last").reset_index(drop=True)
    result["combined_rank"] = result["combined_points"].rank(method="min", ascending=ascending_points)
    result["combined_rank"] = result["combined_rank"].astype("Int64")

    result.attrs["discarded_races_by_competitor"] = discarded_map
    result.attrs["discard_after"] = discard_after
    result.attrs["discard_count"] = discard_count
    result.attrs["scoring_rule"] = scoring_rule
    result.attrs["synthetic_races_by_competitor"] = synthetic_races_by_competitor

    return result[["combined_rank", "competitor", "boat_type", *race_columns, "combined_points", "race_count"]]
