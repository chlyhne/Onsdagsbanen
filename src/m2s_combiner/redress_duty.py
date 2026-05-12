from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata

import pandas as pd


DEFAULT_DUTY_ASSIGNMENTS_PATH = Path("redress_duty_assignments.csv")
REDRESS_DUTY_RESULT_SOURCE = "redress-duty"
REQUIRED_DUTY_COLUMNS = ["year", "race_local", "group", "series", "competitor"]
OPTIONAL_DUTY_COLUMNS = ["note"]
ALLOWED_DUTY_COLUMNS = [*REQUIRED_DUTY_COLUMNS, *OPTIONAL_DUTY_COLUMNS]


@dataclass(frozen=True)
class DutyApplicationContext:
    group_label: str
    class_names: list[str]
    payload_by_class: dict[str, dict[str, object]]
    selected_races: list[str]
    event_year: int


@dataclass(frozen=True)
class DutyApplicationResult:
    parsed_races_by_class: dict[str, dict[str, pd.DataFrame]]
    applied_count: int


def _normalize_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKD", text)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def empty_duty_assignments() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            *REQUIRED_DUTY_COLUMNS,
            *OPTIONAL_DUTY_COLUMNS,
            "race_local_norm",
            "group_norm",
            "series_norm",
            "competitor_norm",
        ]
    )


def load_duty_assignments(path: Path, *, required: bool) -> pd.DataFrame:
    if not path.exists():
        if required:
            raise FileNotFoundError(f"Duty assignment file not found: {path}")
        return empty_duty_assignments()

    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    unknown_columns = sorted(set(frame.columns).difference(ALLOWED_DUTY_COLUMNS))
    if unknown_columns:
        raise ValueError(
            f"Duty assignment file has unsupported columns: {', '.join(unknown_columns)}"
        )

    missing_columns = [column for column in REQUIRED_DUTY_COLUMNS if column not in frame.columns]
    if missing_columns:
        raise ValueError(
            f"Duty assignment file is missing required columns: {', '.join(missing_columns)}"
        )

    if frame.empty:
        return empty_duty_assignments()

    work = frame.loc[:, [column for column in ALLOWED_DUTY_COLUMNS if column in frame.columns]].copy()
    for column in REQUIRED_DUTY_COLUMNS:
        work[column] = work[column].map(lambda value: str(value or "").strip())
    for column in OPTIONAL_DUTY_COLUMNS:
        if column not in work.columns:
            work[column] = ""
        work[column] = work[column].map(lambda value: str(value or "").strip())

    non_empty_mask = work[REQUIRED_DUTY_COLUMNS + OPTIONAL_DUTY_COLUMNS].apply(
        lambda row: any(str(value).strip() for value in row),
        axis=1,
    )
    work = work.loc[non_empty_mask].reset_index(drop=True)
    if work.empty:
        return empty_duty_assignments()

    missing_value_rows: list[str] = []
    for row_index, row in work.iterrows():
        missing_values = [column for column in REQUIRED_DUTY_COLUMNS if not str(row[column]).strip()]
        if missing_values:
            missing_value_rows.append(
                f"row {row_index + 2}: missing {', '.join(missing_values)}"
            )
    if missing_value_rows:
        raise ValueError(
            "Duty assignment file has incomplete rows: " + "; ".join(missing_value_rows)
        )

    year_values = pd.to_numeric(work["year"], errors="coerce")
    invalid_year_rows = work.loc[year_values.isna(), "year"].index.tolist()
    if invalid_year_rows:
        row_labels = ", ".join(str(index + 2) for index in invalid_year_rows)
        raise ValueError(f"Duty assignment file has invalid year values on rows: {row_labels}")
    work["year"] = year_values.astype(int)

    work["race_local"] = work["race_local"].map(lambda value: str(value).strip().upper())
    invalid_race_rows = work.index[~work["race_local"].str.fullmatch(r"R\d+")].tolist()
    if invalid_race_rows:
        row_labels = ", ".join(str(index + 2) for index in invalid_race_rows)
        raise ValueError(f"Duty assignment file has invalid race_local values on rows: {row_labels}")

    work["race_local_norm"] = work["race_local"]
    work["group_norm"] = work["group"].map(_normalize_text)
    work["series_norm"] = work["series"].map(_normalize_text)
    work["competitor_norm"] = work["competitor"].map(_normalize_text)

    duplicate_mask = work.duplicated(
        subset=["year", "race_local_norm", "group_norm", "series_norm", "competitor_norm"],
        keep=False,
    )
    if duplicate_mask.any():
        duplicate_rows = ", ".join(str(index + 2) for index in work.index[duplicate_mask].tolist())
        raise ValueError(f"Duty assignment file contains duplicate assignments on rows: {duplicate_rows}")

    return work.reset_index(drop=True)


def duty_assignment_years(duty_assignments: pd.DataFrame) -> tuple[int, ...]:
    if duty_assignments.empty or "year" not in duty_assignments.columns:
        return ()
    return tuple(sorted({int(value) for value in duty_assignments["year"].dropna().tolist()}))


def filter_relevant_duty_assignments(
    duty_assignments: pd.DataFrame,
    *,
    event_year: int,
    group_label: str,
    selected_races: list[str],
) -> pd.DataFrame:
    if duty_assignments.empty:
        return duty_assignments.iloc[0:0].copy()

    selected_race_set = {str(label).strip().upper() for label in selected_races}
    return duty_assignments.loc[
        (duty_assignments["year"] == int(event_year))
        & (duty_assignments["group_norm"] == _normalize_text(group_label))
        & (duty_assignments["race_local_norm"].isin(selected_race_set))
    ].copy()


def apply_group_duty_assignments(
    context: DutyApplicationContext,
    parsed_races_by_class: dict[str, dict[str, pd.DataFrame]],
    duty_assignments: pd.DataFrame,
    redress_lookup: pd.DataFrame,
) -> DutyApplicationResult:
    relevant = filter_relevant_duty_assignments(
        duty_assignments,
        event_year=context.event_year,
        group_label=context.group_label,
        selected_races=context.selected_races,
    )
    if relevant.empty:
        return DutyApplicationResult(parsed_races_by_class=parsed_races_by_class, applied_count=0)

    series_by_norm = {_normalize_text(class_name): class_name for class_name in context.class_names}
    entry_meta_by_class = {
        class_name: _entry_meta_by_competitor(context.payload_by_class[class_name])
        for class_name in context.class_names
    }
    mutated = {
        class_name: {race_label: frame.copy() for race_label, frame in race_map.items()}
        for class_name, race_map in parsed_races_by_class.items()
    }

    lookup = filter_relevant_duty_assignments(
        redress_lookup,
        event_year=context.event_year,
        group_label=context.group_label,
        selected_races=context.selected_races,
    )
    applied_count = 0

    for _, assignment in relevant.iterrows():
        series_norm = str(assignment["series_norm"])
        class_name = series_by_norm.get(series_norm)
        if not class_name:
            raise ValueError(
                f"Duty assignment references unknown series '{assignment['series']}' for group '{context.group_label}'."
            )

        race_label = str(assignment["race_local"]).strip().upper()
        if race_label not in mutated.get(class_name, {}):
            raise ValueError(
                f"Duty assignment references unavailable race '{race_label}' in series '{class_name}'."
            )

        race_frame = mutated[class_name][race_label].copy()
        competitor_norm = str(assignment["competitor_norm"])
        matching_indices = race_frame.index[race_frame["competitor"].map(_normalize_text) == competitor_norm].tolist()
        if len(matching_indices) > 1:
            raise ValueError(
                f"Multiple rows found for competitor '{assignment['competitor']}' in {context.group_label} / {class_name} / {race_label}."
            )

        existing_row = race_frame.loc[matching_indices[0]].copy() if matching_indices else None
        if existing_row is not None and pd.notna(existing_row.get("beregnet_seconds")):
            print(
                f"[{context.group_label}] Skipping duty assignment for '{assignment['competitor']}' in {class_name} / {race_label} because that boat already has a scored time."
            )
            continue

        competitor_history = pd.concat(mutated[class_name].values(), ignore_index=True)
        competitor_history = competitor_history.loc[
            competitor_history["competitor"].map(_normalize_text) == competitor_norm
        ].copy()

        entry_meta = entry_meta_by_class[class_name].get(competitor_norm, {})
        if existing_row is None and competitor_history.empty and not entry_meta:
            raise ValueError(
                f"Duty assignment competitor '{assignment['competitor']}' is not present in series roster '{class_name}'."
            )

        lookup_matches = lookup.loc[
            (lookup["series_norm"] == series_norm)
            & (lookup["competitor_norm"] == competitor_norm)
            & (lookup["race_local_norm"] == race_label)
            & (lookup["observed"] != True)
        ].copy()
        if lookup_matches.empty:
            raise ValueError(
                f"No redress prediction found for duty assignment '{assignment['competitor']}' in {context.group_label} / {class_name} / {race_label}."
            )
        if len(lookup_matches) > 1:
            raise ValueError(
                f"Multiple redress predictions found for duty assignment '{assignment['competitor']}' in {context.group_label} / {class_name} / {race_label}."
            )

        prediction = lookup_matches.iloc[0]
        predicted_beregnet_seconds = pd.to_numeric([prediction.get("pred_cf_beregnet_seconds")], errors="coerce")[0]
        predicted_sailed_seconds = pd.to_numeric([prediction.get("pred_cf_sailed_seconds")], errors="coerce")[0]
        if pd.isna(predicted_beregnet_seconds) or pd.isna(predicted_sailed_seconds):
            raise ValueError(
                f"Redress prediction for '{assignment['competitor']}' in {context.group_label} / {class_name} / {race_label} is missing predicted times."
            )

        race_index = int(race_label[1:])
        length_nm = _first_numeric(race_frame.get("length_nm", pd.Series(dtype="float64")))
        hdcp = None
        if existing_row is not None and pd.notna(existing_row.get("hdcp")):
            hdcp = float(existing_row.get("hdcp"))
        elif race_index in entry_meta.get("hdcp_by_race", {}):
            hdcp = float(entry_meta["hdcp_by_race"][race_index])
        else:
            hdcp = _first_numeric(competitor_history.get("hdcp", pd.Series(dtype="float64")))

        if hdcp is None:
            raise ValueError(
                f"Could not determine handicap for duty assignment '{assignment['competitor']}' in {context.group_label} / {class_name} / {race_label}."
            )
        if length_nm is None:
            raise ValueError(
                f"Could not determine course length for duty assignment '{assignment['competitor']}' in {context.group_label} / {class_name} / {race_label}."
            )

        synthetic_row = {
            "series": class_name,
            "race": race_label,
            "competitor": existing_row.get("competitor") if existing_row is not None else entry_meta.get("competitor") or _first_non_empty(competitor_history.get("competitor", pd.Series(dtype="object"))) or str(assignment["competitor"]),
            "boat_name": existing_row.get("boat_name") if existing_row is not None else entry_meta.get("boat_name") or _first_non_empty(competitor_history.get("boat_name", pd.Series(dtype="object"))),
            "boat_type": existing_row.get("boat_type") if existing_row is not None else entry_meta.get("boat_type") or _first_non_empty(competitor_history.get("boat_type", pd.Series(dtype="object"))),
            "sail_number": existing_row.get("sail_number") if existing_row is not None else entry_meta.get("sail_number") or _first_non_empty(competitor_history.get("sail_number", pd.Series(dtype="object"))),
            "sail_number_country": existing_row.get("sail_number_country") if existing_row is not None else entry_meta.get("sail_number_country") or _first_non_empty(competitor_history.get("sail_number_country", pd.Series(dtype="object"))),
            "skipper": existing_row.get("skipper") if existing_row is not None else entry_meta.get("skipper") or _first_non_empty(competitor_history.get("skipper", pd.Series(dtype="object"))),
            "hdcp": hdcp,
            "length_nm": length_nm,
            "beregnet_seconds": float(predicted_beregnet_seconds),
            "sailed_seconds": float(predicted_sailed_seconds),
            "race_status_code": "REDRESS",
            "race_points": pd.NA,
            "race_rank_raw": pd.NA,
            "result_source": REDRESS_DUTY_RESULT_SOURCE,
            "result_note": str(assignment.get("note") or "").strip(),
        }

        if matching_indices:
            target_index = matching_indices[0]
            for column, value in synthetic_row.items():
                race_frame.loc[target_index, column] = value
        else:
            race_frame = pd.concat([race_frame, pd.DataFrame([synthetic_row])], ignore_index=True)

        mutated[class_name][race_label] = race_frame.reset_index(drop=True)
        applied_count += 1

    return DutyApplicationResult(parsed_races_by_class=mutated, applied_count=applied_count)


def _entry_meta_by_competitor(payload: dict[str, object]) -> dict[str, dict[str, object]]:
    meta_by_competitor: dict[str, dict[str, object]] = {}
    entries = payload.get("EntryResults")
    if not isinstance(entries, list):
        return meta_by_competitor

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        competitor = str(entry.get("TeamName") or "").strip()
        competitor_norm = _normalize_text(competitor)
        if not competitor_norm:
            continue

        boat_name = str(entry.get("BoatName") or "").strip()
        boat_type = str(entry.get("BoatType") or "").strip() or boat_name
        sail_number = str(entry.get("SailNumber") or "").strip()
        sail_number_country = str(entry.get("SailNumberCountry") or "").strip()
        skipper = str(entry.get("Skipper") or "").strip()
        hdcp_by_race: dict[int, float] = {}

        race_results = entry.get("EntryRaceResults")
        if isinstance(race_results, list):
            for race_result in race_results:
                if not isinstance(race_result, dict):
                    continue
                race_index = race_result.get("OverallRaceIndex")
                hdcp_value = race_result.get("Hdcp")
                if not isinstance(race_index, int) or not isinstance(hdcp_value, (int, float)):
                    continue
                hdcp_by_race[race_index] = float(hdcp_value)

        meta_by_competitor[competitor_norm] = {
            "competitor": competitor,
            "boat_name": boat_name,
            "boat_type": boat_type,
            "sail_number": sail_number,
            "sail_number_country": sail_number_country,
            "skipper": skipper,
            "hdcp_by_race": hdcp_by_race,
        }

    return meta_by_competitor


def _first_non_empty(series: pd.Series) -> str:
    for value in series:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _first_numeric(series: pd.Series) -> float | None:
    values = pd.to_numeric(series, errors="coerce")
    values = values.dropna()
    if values.empty:
        return None
    return float(values.iloc[0])