from __future__ import annotations

import ast
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from m2s_combiner.defaults import DEFAULT_CLASS_GROUPS
from m2s_combiner.parser import parse_available_race_labels_from_result_payload
from m2s_combiner.parser import parse_completed_race_labels_from_result_payload
from m2s_combiner.parser import parse_race_rows_from_result_payload
from m2s_combiner.scraper import fetch_class_results_batch
from m2s_combiner.scraper import fetch_event_bootstrap

from .common import class_sort_key
from .common import is_blue_x
from .common import normalize_sail_number
from .common import normalize_text
from .common import race_num
from .common import race_sort_key
from .common import sail_digits
from .common import similarity
from .constants import EXCLUDED_CLASS_PREFIXES_BY_YEAR
from .constants import EVENT_URLS_BY_YEAR


def parse_race_date(item: dict[str, Any]) -> datetime | None:
    start_date = str(item.get("StartDate") or "").strip()
    if start_date:
        normalized = start_date.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            pass

    start_date_with_time = str(item.get("StartDateWithTime") or "").strip()
    if start_date_with_time:
        for pattern in ("%m/%d/%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
            try:
                return datetime.strptime(start_date_with_time, pattern)
            except ValueError:
                continue
    return None


def race_dates_by_label(payloads: dict[str, dict[str, Any]], selected_races: list[str]) -> dict[str, datetime]:
    wanted = {int(label[1:]): label for label in selected_races}
    dates_by_label: dict[str, list[datetime]] = {label: [] for label in selected_races}

    for payload in payloads.values():
        race_infos = payload.get("RaceInfos")
        if not isinstance(race_infos, list):
            continue
        for item in race_infos:
            if not isinstance(item, dict):
                continue
            race_index = item.get("RaceIndex")
            if not isinstance(race_index, int) or race_index not in wanted:
                continue
            parsed = parse_race_date(item)
            if parsed is None:
                continue
            dates_by_label[wanted[race_index]].append(parsed)

    return {label: min(values) for label, values in dates_by_label.items() if values}


def discover_class_groups(event_url: str, *, event_year: int | None = None) -> list[tuple[str, list[str]]]:
    _, regatta_map = fetch_event_bootstrap(event_url)
    class_names = sorted({meta["name"] for meta in regatta_map.values()})

    excluded_prefixes = tuple(
        normalize_text(value) for value in EXCLUDED_CLASS_PREFIXES_BY_YEAR.get(int(event_year), ())
    ) if event_year is not None else ()
    if excluded_prefixes:
        class_names = [
            name
            for name in class_names
            if not any(normalize_text(name).startswith(prefix) for prefix in excluded_prefixes)
        ]

    group_prefixes: dict[str, str] = {}
    for group_label, group_classes in DEFAULT_CLASS_GROUPS:
        if not group_classes:
            continue
        sample = normalize_text(group_classes[0].rsplit(" ", 1)[0])
        group_prefixes[group_label] = sample

    buckets: dict[str, list[str]] = {group_label: [] for group_label, _ in DEFAULT_CLASS_GROUPS}
    for class_name in class_names:
        normalized = normalize_text(class_name)
        for group_label, prefix in group_prefixes.items():
            if normalized.startswith(prefix):
                buckets[group_label].append(class_name)
                break

    resolved: list[tuple[str, list[str]]] = []
    for group_label, _ in DEFAULT_CLASS_GROUPS:
        names = sorted(set(buckets.get(group_label, [])), key=class_sort_key)
        if names:
            resolved.append((group_label, names))
    return resolved


def normalize_person_name(value: object) -> str:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                parsed = ast.literal_eval(stripped)
            except (SyntaxError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                value = parsed
    if isinstance(value, dict):
        first_name = str(value.get("FirstName") or "").strip()
        last_name = str(value.get("LastName") or "").strip()
        combined = " ".join(part for part in [first_name, last_name] if part)
        if combined:
            return normalize_text(combined)
    return normalize_text(value)


def soft_match_competitors(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()

    work = frame.copy()
    work["competitor_raw"] = work["competitor"].astype(str)
    work["competitor_norm"] = work["competitor"].map(normalize_text)
    work["boat_name_norm"] = work["boat_name"].map(normalize_text)
    work["boat_type_norm"] = work["boat_type"].map(normalize_text)
    work["skipper_norm"] = work["skipper"].map(normalize_person_name)
    work["sail_norm"] = work.apply(
        lambda row: normalize_sail_number(row.get("sail_number"), row.get("sail_number_country")),
        axis=1,
    )
    work["is_blue_x"] = work.apply(
        lambda row: is_blue_x(row.get("competitor"), row.get("boat_name"), row.get("boat_type")),
        axis=1,
    )

    profile_cols = [
        "competitor_raw",
        "competitor_norm",
        "boat_name",
        "boat_name_norm",
        "boat_type",
        "boat_type_norm",
        "skipper",
        "skipper_norm",
        "sail_number",
        "sail_number_country",
        "sail_norm",
        "is_blue_x",
    ]
    profiles = (
        work.loc[:, profile_cols + ["race_date"]]
        .drop_duplicates(subset=profile_cols)
        .sort_values(["race_date", "competitor_raw", "boat_name"], na_position="last")
        .reset_index(drop=True)
    )

    canonicals: list[dict[str, Any]] = []
    profile_to_canonical: dict[tuple[Any, ...], str] = {}

    for _, profile in profiles.iterrows():
        key = tuple(profile[col] for col in profile_cols)
        prof_comp = str(profile["competitor_norm"])
        prof_boat = str(profile["boat_name_norm"])
        prof_type = str(profile["boat_type_norm"])
        prof_skipper = str(profile["skipper_norm"])
        prof_sail = str(profile["sail_norm"])
        prof_sail_digits = sail_digits(prof_sail)
        prof_blue = bool(profile["is_blue_x"])

        best_idx = -1
        best_score = -1.0
        for idx, canonical in enumerate(canonicals):
            if prof_blue and canonical["is_blue_x"]:
                best_idx = idx
                best_score = 1.0
                break

            comp_sim = max((similarity(prof_comp, value) for value in canonical["competitor_norms"]), default=0.0)
            boat_sim = max((similarity(prof_boat, value) for value in canonical["boat_name_norms"]), default=0.0)
            type_sim = max((similarity(prof_type, value) for value in canonical["boat_type_norms"]), default=0.0)
            skipper_sim = max((similarity(prof_skipper, value) for value in canonical["skipper_norms"]), default=0.0)
            sail_exact_match = bool(prof_sail) and prof_sail in canonical["sail_norms"]
            sail_digit_match = bool(prof_sail_digits) and any(
                sail_digits(value) == prof_sail_digits for value in canonical["sail_norms"]
            )

            # Sail numbers can be reused across years, so they are only decisive when
            # another identity signal points to the same boat or skipper.
            if (sail_exact_match or sail_digit_match) and comp_sim < 0.5 and boat_sim < 0.5 and skipper_sim < 0.5:
                continue

            score = 0.18 * comp_sim + 0.28 * boat_sim + 0.16 * type_sim + 0.08 * skipper_sim
            if prof_comp and prof_comp in canonical["competitor_norms"]:
                score += 0.16
            if prof_boat and prof_boat in canonical["boat_name_norms"]:
                score += 0.18
            if prof_sail and canonical["sail_norms"]:
                if sail_exact_match:
                    score += 0.55
                elif sail_digit_match:
                    score += 0.36

            if score > best_score:
                best_score = score
                best_idx = idx

        threshold = 0.56 if prof_sail else 0.74
        use_existing = best_idx >= 0 and best_score >= threshold
        if use_existing:
            canonical = canonicals[best_idx]
        else:
            canonical = {
                "id": "boat_blue_x" if prof_blue else f"boat_{len(canonicals) + 1:03d}",
                "is_blue_x": prof_blue,
                "competitor_norms": set(),
                "boat_name_norms": set(),
                "boat_type_norms": set(),
                "skipper_norms": set(),
                "sail_norms": set(),
            }
            canonicals.append(canonical)

        if prof_comp:
            canonical["competitor_norms"].add(prof_comp)
        if prof_boat:
            canonical["boat_name_norms"].add(prof_boat)
        if prof_type:
            canonical["boat_type_norms"].add(prof_type)
        if prof_skipper:
            canonical["skipper_norms"].add(prof_skipper)
        if prof_sail:
            canonical["sail_norms"].add(prof_sail)
        profile_to_canonical[key] = str(canonical["id"])

    work["canonical_id"] = work.apply(lambda row: profile_to_canonical[tuple(row[col] for col in profile_cols)], axis=1)
    canonical_display = work.groupby("canonical_id")["competitor_raw"].agg(lambda s: s.value_counts(dropna=False).index[0]).to_dict()
    if "boat_blue_x" in canonical_display:
        canonical_display["boat_blue_x"] = "Blue X"

    work["competitor"] = work["canonical_id"].map(canonical_display)
    return work.drop(columns=[
        "competitor_norm",
        "boat_name_norm",
        "boat_type_norm",
        "skipper_norm",
        "sail_norm",
        "is_blue_x",
        "canonical_id",
    ])


def build_group_data() -> tuple[list[dict[str, Any]], pd.DataFrame]:
    frames_by_group: dict[str, list[pd.DataFrame]] = {}

    for year in sorted(EVENT_URLS_BY_YEAR):
        event_url = EVENT_URLS_BY_YEAR[year]
        class_groups = discover_class_groups(event_url, event_year=year)
        class_names = [name for _, group_classes in class_groups for name in group_classes]
        if not class_names:
            continue
        payloads = fetch_class_results_batch(event_url, class_names)

        for group_label, group_classes in class_groups:
            payload_by_class = {name: payloads[name] for name in group_classes}
            available = [set(parse_available_race_labels_from_result_payload(payload_by_class[name])) for name in group_classes]
            aligned = sorted(set.intersection(*available), key=race_sort_key)
            completed = [set(parse_completed_race_labels_from_result_payload(payload_by_class[name])) for name in group_classes]
            selected = [label for label in aligned if label in set.intersection(*completed)]
            if not selected:
                continue

            race_frames: list[pd.DataFrame] = []
            for class_name in group_classes:
                parsed = parse_race_rows_from_result_payload(payload_by_class[class_name], class_name, selected)
                for race_label in selected:
                    if race_label not in parsed:
                        continue
                    chunk = parsed[race_label].copy()
                    chunk["year"] = year
                    race_frames.append(chunk)

            if not race_frames:
                continue

            combined = pd.concat(race_frames, ignore_index=True)
            combined = (
                combined.groupby(["year", "race", "series", "competitor"], as_index=False)
                .agg(
                    boat_name=("boat_name", "first"),
                    boat_type=("boat_type", "first"),
                    sail_number=("sail_number", "first"),
                    sail_number_country=("sail_number_country", "first"),
                    skipper=("skipper", "first"),
                    hdcp=("hdcp", "first"),
                    length_nm=("length_nm", "first"),
                    beregnet_seconds=("beregnet_seconds", "min"),
                    sailed_seconds=("sailed_seconds", "min"),
                    race_status_code=("race_status_code", "first"),
                    race_points=("race_points", "min"),
                    race_rank_raw=("race_rank_raw", "min"),
                )
                .reset_index(drop=True)
            )
            if combined.empty:
                continue

            combined["group"] = group_label
            combined["race_date"] = combined["race"].map(race_dates_by_label(payload_by_class, selected))
            frames_by_group.setdefault(group_label, []).append(combined)

    groups: list[dict[str, Any]] = []
    all_rows: list[pd.DataFrame] = []
    for group_label, parts in frames_by_group.items():
        combined = pd.concat(parts, ignore_index=True)
        if combined.empty:
            continue

        combined = soft_match_competitors(combined)
        combined["race_local"] = combined["race"].astype(str)

        race_keys = combined.loc[:, ["year", "race", "race_date"]].drop_duplicates().copy()
        race_keys["race_local_num"] = race_keys["race"].map(race_num)
        race_keys["race_date"] = pd.to_datetime(race_keys["race_date"], errors="coerce")
        race_keys = race_keys.sort_values(["race_date", "year", "race_local_num", "race"], na_position="last").reset_index(drop=True)
        race_keys["race_global_num"] = np.arange(1, len(race_keys) + 1, dtype=int)
        race_keys["race_global"] = race_keys["race_global_num"].map(lambda value: f"R{value}")

        combined = combined.merge(
            race_keys.loc[:, ["year", "race", "race_date", "race_global"]],
            on=["year", "race", "race_date"],
            how="left",
        )
        combined["race"] = combined["race_global"]
        combined = combined.drop(columns=["race_global"])

        race_dates = {
            str(row["race_global"]): pd.Timestamp(row["race_date"]).to_pydatetime()
            for _, row in race_keys.iterrows()
            if pd.notna(row["race_date"])
        }
        selected_races = [f"R{value}" for value in race_keys["race_global_num"].tolist()]
        combined["race_date"] = pd.to_datetime(combined["race_date"], errors="coerce")

        groups.append(
            {
                "group": group_label,
                "classes": [],
                "selected_races": selected_races,
                "race_dates": race_dates,
                "combined": combined,
            }
        )
        all_rows.append(combined)

    all_data = pd.concat(all_rows, ignore_index=True) if all_rows else pd.DataFrame()
    if not groups or all_data.empty:
        return groups, all_data

    stacked_rows: list[pd.DataFrame] = []
    for idx, group in enumerate(groups):
        chunk = group["combined"].copy()
        chunk["_group_idx"] = idx
        stacked_rows.append(chunk)
    stacked = soft_match_competitors(pd.concat(stacked_rows, ignore_index=True))

    updated_groups: list[dict[str, Any]] = []
    for idx, group in enumerate(groups):
        updated = dict(group)
        updated["combined"] = stacked[stacked["_group_idx"] == idx].drop(columns=["_group_idx"]).reset_index(drop=True)
        updated_groups.append(updated)

    all_data = stacked.drop(columns=["_group_idx"]).reset_index(drop=True)
    return updated_groups, all_data


def build_competitor_year_group_map(all_data: pd.DataFrame, non_observed_statuses: set[str]) -> dict[tuple[str, int], str]:
    if all_data.empty:
        return {}

    work = all_data.copy()
    status_series = work["race_status_code"] if "race_status_code" in work.columns else pd.Series("", index=work.index)
    work["status_upper"] = status_series.fillna("").astype(str).str.upper().str.strip()
    work["is_obs"] = work["beregnet_seconds"].notna() & (~work["status_upper"].isin(non_observed_statuses))

    summary = (
        work.groupby(["competitor", "year", "group"], as_index=False)
        .agg(observed_count=("is_obs", "sum"), row_count=("race", "count"))
        .sort_values(["competitor", "year", "observed_count", "row_count", "group"], ascending=[True, True, False, False, True])
    )
    chosen = summary.drop_duplicates(subset=["competitor", "year"], keep="first")
    return {(str(row["competitor"]), int(row["year"])): str(row["group"]) for _, row in chosen.iterrows()}
