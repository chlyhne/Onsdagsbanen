from __future__ import annotations

import pandas as pd


LOW_POINT_RULE = "low-point"
HIGH_POINT_RULE = "high-point"
SUPPORTED_SCORING_RULES = {LOW_POINT_RULE, HIGH_POINT_RULE}


def _format_seconds(total_seconds: float) -> str:
    seconds_int = int(round(total_seconds))
    hours, remainder = divmod(seconds_int, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}"


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
            series_count=("series", lambda values: values.dropna().nunique()),
        )
        .sort_values(["race", "beregnet_seconds", "competitor"], ascending=[True, True, True], na_position="last")
        .reset_index(drop=True)
    )

    grouped["race_rank"] = pd.Series(pd.NA, index=grouped.index, dtype="Int64")
    if "race_rank_raw" in grouped.columns:
        grouped["race_rank_raw"] = pd.to_numeric(grouped["race_rank_raw"], errors="coerce")
        raw_rank_mask = grouped["race_rank_raw"].notna()
        grouped.loc[raw_rank_mask, "race_rank"] = grouped.loc[raw_rank_mask, "race_rank_raw"].round().astype("Int64")

    for race_label, race_rows in grouped.groupby("race"):
        _ = race_label
        finish_idx = race_rows[race_rows["beregnet_seconds"].notna()].index
        if len(finish_idx) == 0:
            continue
        ranks = race_rows.loc[finish_idx, "beregnet_seconds"].rank(method="min", ascending=True).astype(int)
        grouped.loc[finish_idx, "race_rank"] = ranks.astype("Int64")

    grouped["race_points"] = pd.to_numeric(grouped["race_points"], errors="coerce")
    if scoring_rule == LOW_POINT_RULE:
        grouped["points"] = pd.Series(pd.NA, index=grouped.index, dtype="Int64")
        finisher_mask = grouped["beregnet_seconds"].notna() & grouped["race_rank"].notna()
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
        non_participation_statuses = {"DNS", "DNC", "DSQ"}
        status_series = grouped["race_status_code"].fillna("").astype(str).str.strip().str.upper()
        non_participation_mask = status_series.isin(non_participation_statuses)

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

    grouped["beregnet_time"] = grouped["beregnet_seconds"].map(
        lambda value: _format_seconds(value) if pd.notna(value) else ""
    )
    status_mask = grouped["beregnet_time"].eq("") & grouped["race_status_code"].astype(str).str.strip().ne("")
    grouped.loc[status_mask, "beregnet_time"] = grouped.loc[status_mask, "race_status_code"].astype(str).str.strip()
    grouped["sailed_time"] = grouped["sailed_seconds"].map(
        lambda value: _format_seconds(value) if pd.notna(value) else ""
    )

    grouped["delta_seconds"] = pd.NA
    grouped["interval_seconds"] = pd.NA
    for race_label, race_rows in grouped.groupby("race"):
        _ = race_label
        finish_idx = race_rows[race_rows["beregnet_seconds"].notna()].index
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
        ]
    ]


def combine_overall_from_races(
    combined_races: pd.DataFrame,
    max_race: int = 18,
    all_competitors: list[str] | None = None,
    discard_after: list[int] | None = None,
    scoring_rule: str = LOW_POINT_RULE,
) -> pd.DataFrame:
    """Create fixed-size overall standings with R1..Rmax columns and integer totals."""
    if scoring_rule not in SUPPORTED_SCORING_RULES:
        raise ValueError(f"Unsupported scoring rule: {scoring_rule}")

    race_columns = [f"R{race_number}" for race_number in range(1, max_race + 1)]

    if combined_races.empty:
        empty = pd.DataFrame(columns=["combined_rank", "competitor", "boat_type", *race_columns, "combined_points", "race_count"])
        return empty

    if "points" not in combined_races.columns:
        raise ValueError("Combined race table must include a points column.")

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
    working["points"] = working["points"].round().astype(int)
    participants_by_race = working.groupby("race")["competitor"].nunique().astype(int).to_dict()

    pivot = working.pivot_table(index="competitor", columns="race", values="points", aggfunc="min")

    if all_competitors:
        competitor_index = pd.Index(sorted({str(item) for item in all_competitors if str(item).strip()}), name="competitor")
        pivot = pivot.reindex(competitor_index)
    roster_size = len(pivot.index)

    if scoring_rule == LOW_POINT_RULE:
        missing_points_by_race = {
            race_label: max(points, roster_size) + 1
            for race_label, points in participants_by_race.items()
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
            result[column] = result[column].astype("Int64")

    result["combined_points"] = pd.Series(combined_points_values, dtype="Int64")
    result["race_count"] = pd.Series(race_count_values, dtype="Int64")
    if boat_type_by_competitor is not None:
        result["boat_type"] = result["competitor"].map(boat_type_by_competitor).fillna("")
    else:
        result["boat_type"] = ""
    ascending_points = scoring_rule == LOW_POINT_RULE
    result = result.sort_values(["combined_points", "competitor"], ascending=[ascending_points, True], na_position="last").reset_index(drop=True)
    result["combined_rank"] = result["combined_points"].rank(method="min", ascending=ascending_points)
    result["combined_rank"] = result["combined_rank"].astype("Int64")

    result.attrs["discarded_races_by_competitor"] = discarded_map
    result.attrs["discard_after"] = discard_after
    result.attrs["discard_count"] = discard_count
    result.attrs["scoring_rule"] = scoring_rule

    return result[["combined_rank", "competitor", "boat_type", *race_columns, "combined_points", "race_count"]]
