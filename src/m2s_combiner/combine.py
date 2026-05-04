from __future__ import annotations

import pandas as pd


def _format_seconds(total_seconds: float) -> str:
    seconds_int = int(round(total_seconds))
    hours, remainder = divmod(seconds_int, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}"


def combine_races(race_frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Combine per-race rows by matching race labels across all classes."""
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
        "beregnet_seconds",
        "sailed_seconds",
        "boat_name",
        "boat_type",
        "hdcp",
    }
    missing_columns = sorted(required_columns.difference(combined.columns))
    if missing_columns:
        raise ValueError(f"Combined race inputs are missing required columns: {', '.join(missing_columns)}")

    grouped = (
        combined.groupby(["race", "competitor"], as_index=False)
        .agg(
            beregnet_seconds=("beregnet_seconds", "min"),
            sailed_seconds=("sailed_seconds", "min"),
            boat_name=("boat_name", "first"),
            boat_type=("boat_type", "first"),
            hdcp=("hdcp", "first"),
            series_count=("series", lambda values: values.dropna().nunique()),
        )
        .sort_values(["race", "beregnet_seconds", "competitor"], ascending=[True, True, True])
        .reset_index(drop=True)
    )

    grouped["race_rank"] = grouped.groupby("race")["beregnet_seconds"].rank(method="min", ascending=True).astype(int)
    grouped["points"] = grouped["race_rank"].astype(int)
    grouped["beregnet_time"] = grouped["beregnet_seconds"].map(_format_seconds)
    grouped["sailed_time"] = grouped["sailed_seconds"].map(
        lambda value: _format_seconds(value) if pd.notna(value) else ""
    )

    grouped["delta_seconds"] = grouped["beregnet_seconds"] - grouped.groupby("race")["beregnet_seconds"].transform("min")
    grouped["interval_seconds"] = grouped.groupby("race")["beregnet_seconds"].diff().fillna(0.0)
    grouped["delta"] = grouped["delta_seconds"].map(_format_seconds)
    grouped["interval"] = grouped["interval_seconds"].map(_format_seconds)

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
) -> pd.DataFrame:
    """Create fixed-size overall standings with R1..Rmax columns and integer totals."""
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
    dns_points_by_race = (
        working.groupby("race")["competitor"].nunique().add(1).astype(int).to_dict()
    )

    pivot = working.pivot_table(index="competitor", columns="race", values="points", aggfunc="min")

    if all_competitors:
        competitor_index = pd.Index(sorted({str(item) for item in all_competitors if str(item).strip()}), name="competitor")
        pivot = pivot.reindex(competitor_index)
    roster_size = len(pivot.index)

    dns_points_by_race = {
        race_label: max(points, roster_size) + 1
        for race_label, points in dns_points_by_race.items()
    }

    pivot = pivot.reindex(columns=race_columns)

    sailed_races = [label for label in race_columns if label in dns_points_by_race]
    for race_label in sailed_races:
        pivot[race_label] = pivot[race_label].fillna(dns_points_by_race[race_label])

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
            sorted_worst = sorted(row_values, key=lambda item: (item[1], _race_number(item[0])), reverse=True)
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
    result = result.sort_values(["combined_points", "competitor"], ascending=[True, True], na_position="last").reset_index(drop=True)
    result["combined_rank"] = result["combined_points"].rank(method="min", ascending=True)
    result["combined_rank"] = result["combined_rank"].astype("Int64")

    result.attrs["discarded_races_by_competitor"] = discarded_map
    result.attrs["discard_after"] = discard_after
    result.attrs["discard_count"] = discard_count

    return result[["combined_rank", "competitor", "boat_type", *race_columns, "combined_points", "race_count"]]
