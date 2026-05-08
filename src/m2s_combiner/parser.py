from __future__ import annotations

import re

import pandas as pd


def _is_in_allowed_race_range(race_index: int, max_race: int | None) -> bool:
    return race_index >= 1 and (max_race is None or race_index <= max_race)


def _parse_time_to_seconds(value: object) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None

    text = str(value).strip()
    if not text:
        return None

    match = re.search(r"\d{1,2}:\d{2}(?::\d{2})?", text)
    if not match:
        return None

    parts = [int(part) for part in match.group(0).split(":")]
    if len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        hours = 0
        minutes, seconds = parts
    return float(hours * 3600 + minutes * 60 + seconds)


def _clean_text(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    if text.lower() in {"", "nan", "none"}:
        return ""
    return re.sub(r"\s+", " ", text)


def _parse_number(value: object) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = _clean_text(value).replace(",", ".")
    if not text:
        return None
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return None

    try:
        return float(text)
    except ValueError:
        return None


def parse_available_race_labels_from_result_payload(
    payload: dict[str, object],
    max_race: int | None = None,
) -> list[str]:
    """Extract available race labels (R1..Rmax) from regattaresult JSON payload."""
    race_numbers: set[int] = set()

    race_names = payload.get("RaceNames")
    if isinstance(race_names, list):
        for item in race_names:
            if not isinstance(item, dict):
                continue
            race_index = item.get("RaceIndex")
            if isinstance(race_index, int) and _is_in_allowed_race_range(race_index, max_race):
                race_numbers.add(race_index)

    return [f"R{race_number}" for race_number in sorted(race_numbers)]


def parse_completed_race_labels_from_result_payload(
    payload: dict[str, object],
    max_race: int | None = None,
) -> list[str]:
    """Extract completed race labels using corrected-time or scored/status activity."""
    counts_by_race: dict[int, int] = {}

    entries = payload.get("EntryResults")
    if not isinstance(entries, list):
        return []

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        race_results = entry.get("EntryRaceResults")
        if not isinstance(race_results, list):
            continue

        for race_result in race_results:
            if not isinstance(race_result, dict):
                continue

            race_index = race_result.get("OverallRaceIndex")
            if not isinstance(race_index, int) or not _is_in_allowed_race_range(race_index, max_race):
                continue

            corrected_ms = race_result.get("CorrectedTimeMs")
            corrected_text = race_result.get("CorrectedTime")
            race_status_code = _clean_text(race_result.get("RaceStatusCode"))
            race_points = _parse_number(race_result.get("Points"))
            race_rank = race_result.get("Rank")

            has_corrected = isinstance(corrected_ms, (int, float)) or _parse_time_to_seconds(corrected_text) is not None
            has_scoring_activity = (
                bool(race_status_code)
                or race_points is not None
                or isinstance(race_rank, (int, float))
            )
            if has_corrected or has_scoring_activity:
                counts_by_race[race_index] = counts_by_race.get(race_index, 0) + 1

    completed = sorted(race_index for race_index, count in counts_by_race.items() if count > 0)
    return [f"R{race_index}" for race_index in completed]


def parse_discard_after_races_from_result_payload(
    payload: dict[str, object],
    max_race: int | None = None,
) -> list[int]:
    """Extract discard thresholds from payload Discards field (e.g. '6,9,12,15,18;')."""
    discards = str(payload.get("Discards") or "")
    numbers = [int(token) for token in re.findall(r"\d+", discards)]
    return sorted({value for value in numbers if _is_in_allowed_race_range(value, max_race)})


def parse_race_rows_from_result_payload(
    payload: dict[str, object],
    series_label: str,
    race_labels: list[str],
) -> dict[str, pd.DataFrame]:
    """Build race tables with beregnet/sailed seconds directly from regattaresult JSON payload."""
    requested_labels = [label for label in dict.fromkeys(race_labels) if re.fullmatch(r"R\d+", str(label))]
    if not requested_labels:
        return {}

    requested_indices = {int(label[1:]): label for label in requested_labels}
    rows_by_label: dict[str, list[dict[str, object]]] = {label: [] for label in requested_labels}

    entries = payload.get("EntryResults")
    if not isinstance(entries, list):
        return {}

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        competitor = _clean_text(entry.get("TeamName"))
        if not competitor:
            continue
        boat_name = _clean_text(entry.get("BoatName"))
        boat_type = _clean_text(entry.get("BoatType"))
        if not boat_type:
            boat_type = boat_name
        sail_number = _clean_text(entry.get("SailNumber"))
        sail_number_country = _clean_text(entry.get("SailNumberCountry"))
        skipper = _clean_text(entry.get("Skipper"))

        race_results = entry.get("EntryRaceResults")
        if not isinstance(race_results, list):
            continue

        for race_result in race_results:
            if not isinstance(race_result, dict):
                continue

            race_index = race_result.get("OverallRaceIndex")
            if not isinstance(race_index, int) or race_index not in requested_indices:
                continue

            corrected_ms = race_result.get("CorrectedTimeMs")
            if isinstance(corrected_ms, (int, float)):
                beregnet_seconds = float(corrected_ms) / 1000.0
            else:
                beregnet_seconds = _parse_time_to_seconds(race_result.get("CorrectedTime"))

            sailed_seconds = _parse_time_to_seconds(
                race_result.get("RaceTime") or race_result.get("RaceTimeForCalculation")
            )
            hdcp_value = race_result.get("Hdcp")
            if isinstance(hdcp_value, (int, float)):
                hdcp = float(hdcp_value)
            else:
                hdcp = None
            race_status_code = _clean_text(race_result.get("RaceStatusCode"))
            race_points = _parse_number(race_result.get("Points"))
            race_rank_value = race_result.get("Rank")
            if isinstance(race_rank_value, (int, float)):
                race_rank_raw = int(race_rank_value)
            else:
                race_rank_raw = None

            # Keep boats with status/points (e.g. DNC/DNS) even without corrected time.
            if (
                beregnet_seconds is None
                and not race_status_code
                and race_points is None
                and race_rank_raw is None
            ):
                continue

            race_label = requested_indices[race_index]
            rows_by_label[race_label].append(
                {
                    "series": series_label,
                    "race": race_label,
                    "competitor": competitor,
                    "boat_name": boat_name,
                    "boat_type": boat_type,
                    "sail_number": sail_number,
                    "sail_number_country": sail_number_country,
                    "skipper": skipper,
                    "hdcp": hdcp,
                    "beregnet_seconds": beregnet_seconds,
                    "sailed_seconds": sailed_seconds,
                    "race_status_code": race_status_code,
                    "race_points": race_points,
                    "race_rank_raw": race_rank_raw,
                }
            )

    result: dict[str, pd.DataFrame] = {}
    for label in requested_labels:
        rows = rows_by_label.get(label) or []
        if not rows:
            continue
        parsed = pd.DataFrame(rows)
        parsed = parsed.reset_index(drop=True)
        if not parsed.empty:
            result[label] = parsed

    return result
