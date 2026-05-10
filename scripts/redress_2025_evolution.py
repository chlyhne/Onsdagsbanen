from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
import json
import math
from pathlib import Path
import re
from typing import Any
import unicodedata

import numpy as np
import pandas as pd

from m2s_combiner.cli import DEFAULT_CLASS_GROUPS
from m2s_combiner.parser import parse_available_race_labels_from_result_payload
from m2s_combiner.parser import parse_completed_race_labels_from_result_payload
from m2s_combiner.parser import parse_race_rows_from_result_payload
from m2s_combiner.scraper import fetch_event_bootstrap
from m2s_combiner.scraper import fetch_class_results_batch

EVENT_URLS_BY_YEAR: dict[int, str] = {
    2024: "https://www.manage2sail.com/nl/event/43565da6-2ecc-441f-b3ab-f1f00adc646c#!/",
    2025: "https://www.manage2sail.com/da-dk/event/Onsdagsbanen2025#!/",
    2026: "https://www.manage2sail.com/da-dk/event/Onsdagsbanen2026#!/",
}
EPS = 1e-9
Z50 = 0.6744897501960817
NON_OBS_STATUSES = {"DNS", "DNC", "DSQ", "DNF"}
Q_SEARCH_MIN = 1e-12
Q_SEARCH_MAX = 1e-2
P_CAP_FACTOR = 365.0
PLOT_ACTIVE_YEAR = 2026
GROUP_Q_CACHE_FILENAME = "redress_group_q_cache.json"


@dataclass
class BoatState:
    x: float
    p: float
    last_state_date: datetime | None


@dataclass
class GroupFilterResult:
    history: pd.DataFrame
    nll_sum: float
    observed_count: int
    loo_sq_error_sum: float
    loo_error_count: int


def _race_sort_key(label: str) -> int:
    return int(label[1:])


def _race_num(label: str) -> int:
    try:
        return int(str(label).replace("R", ""))
    except ValueError:
        return 0


def _display_name(value: str) -> str:
    compact = " ".join(str(value).strip().split())
    if not compact:
        return ""
    return compact.title()


def _slugify_filename(value: str) -> str:
    normalized = _normalize_text(value)
    if not normalized:
        return "item"
    return re.sub(r"[^a-z0-9]+", "-", normalized).strip("-") or "item"


def _format_seconds_hms(value: float | int | None) -> str:
    if value is None:
        return ""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return ""
    if not np.isfinite(numeric):
        return ""
    total = int(round(numeric))
    sign = "-" if total < 0 else ""
    total = abs(total)
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{sign}{hours}:{minutes:02d}:{seconds:02d}"


def _format_rank_error(value: float | int | None) -> str:
    if value is None:
        return ""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return ""
    if not np.isfinite(numeric):
        return ""
    rounded = int(round(numeric))
    if rounded > 0:
        return f"+{rounded}"
    return str(rounded)


def _format_percent_signed(value: float | int | None) -> str:
    if value is None:
        return ""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return ""
    if not np.isfinite(numeric):
        return ""
    if abs(numeric) < 0.05:
        return "0.0\\%"
    sign = "+" if numeric > 0 else ""
    formatted = f"{numeric:.1f}"
    return f"{sign}{formatted}\\%"


def _parse_race_date(item: dict[str, Any]) -> datetime | None:
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


def _race_dates_by_label(payloads: dict[str, dict[str, Any]], selected_races: list[str]) -> dict[str, datetime]:
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
            parsed = _parse_race_date(item)
            if parsed is None:
                continue
            dates_by_label[wanted[race_index]].append(parsed)

    resolved: dict[str, datetime] = {}
    for label, values in dates_by_label.items():
        if not values:
            continue
        resolved[label] = min(values)
    return resolved


def _normalize_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKD", text)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _normalize_sail_number(sail_number: object, sail_country: object) -> str:
    base = f"{str(sail_country or '').strip()} {str(sail_number or '').strip()}".strip()
    normalized = re.sub(r"[^A-Za-z0-9]+", "", base).upper()
    return normalized


def _sail_digits(sail: str) -> str:
    return "".join(ch for ch in sail if ch.isdigit())


def _sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return float(SequenceMatcher(None, a, b).ratio())


def _is_blue_x(*values: object) -> bool:
    joined = "".join(_normalize_text(value).replace(" ", "") for value in values)
    return "bluex" in joined


def _class_sort_key(value: str) -> tuple[int, str]:
    match = re.search(r"(\d+)\s*$", value.strip())
    idx = int(match.group(1)) if match else 0
    return idx, value.lower()


def _discover_class_groups(event_url: str) -> list[tuple[str, list[str]]]:
    _, regatta_map = fetch_event_bootstrap(event_url)
    class_names = sorted({meta["name"] for meta in regatta_map.values()})

    group_prefixes: dict[str, str] = {}
    for group_label, group_classes in DEFAULT_CLASS_GROUPS:
        if not group_classes:
            continue
        sample = re.sub(r"\s+\d+$", "", str(group_classes[0]).strip())
        group_prefixes[group_label] = _normalize_text(sample)

    buckets: dict[str, list[str]] = {group_label: [] for group_label, _ in DEFAULT_CLASS_GROUPS}
    for class_name in class_names:
        normalized = _normalize_text(class_name)
        for group_label, prefix in group_prefixes.items():
            if normalized.startswith(prefix):
                buckets[group_label].append(class_name)
                break

    resolved: list[tuple[str, list[str]]] = []
    for group_label, _ in DEFAULT_CLASS_GROUPS:
        names = sorted(set(buckets.get(group_label, [])), key=_class_sort_key)
        if names:
            resolved.append((group_label, names))
    return resolved


def _soft_match_competitors(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()

    work = frame.copy()
    work["competitor_raw"] = work["competitor"].astype(str)
    work["competitor_norm"] = work["competitor"].map(_normalize_text)
    work["boat_name_norm"] = work["boat_name"].map(_normalize_text)
    work["boat_type_norm"] = work["boat_type"].map(_normalize_text)
    work["skipper_norm"] = work["skipper"].map(_normalize_text)
    work["sail_norm"] = work.apply(
        lambda row: _normalize_sail_number(row.get("sail_number"), row.get("sail_number_country")),
        axis=1,
    )
    work["is_blue_x"] = work.apply(
        lambda row: _is_blue_x(row.get("competitor"), row.get("boat_name"), row.get("boat_type")),
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
        prof_sail_digits = _sail_digits(prof_sail)
        prof_blue = bool(profile["is_blue_x"])

        best_idx = -1
        best_score = -1.0
        for idx, canonical in enumerate(canonicals):
            if prof_blue and canonical["is_blue_x"]:
                best_idx = idx
                best_score = 1.0
                break

            comp_sim = max((_sim(prof_comp, value) for value in canonical["competitor_norms"]), default=0.0)
            boat_sim = max((_sim(prof_boat, value) for value in canonical["boat_name_norms"]), default=0.0)
            type_sim = max((_sim(prof_type, value) for value in canonical["boat_type_norms"]), default=0.0)
            skipper_sim = max((_sim(prof_skipper, value) for value in canonical["skipper_norms"]), default=0.0)

            score = 0.18 * comp_sim + 0.28 * boat_sim + 0.16 * type_sim + 0.08 * skipper_sim
            if prof_comp and prof_comp in canonical["competitor_norms"]:
                score += 0.16
            if prof_boat and prof_boat in canonical["boat_name_norms"]:
                score += 0.18
            if prof_sail and canonical["sail_norms"]:
                if prof_sail in canonical["sail_norms"]:
                    score += 0.55
                elif prof_sail_digits and any(_sail_digits(value) == prof_sail_digits for value in canonical["sail_norms"]):
                    score += 0.36

            if score > best_score:
                best_score = score
                best_idx = idx

        has_sail = bool(prof_sail)
        threshold = 0.56 if has_sail else 0.74
        use_existing = (best_idx >= 0) and (best_score >= threshold)

        if use_existing:
            canonical = canonicals[best_idx]
        else:
            canonical_id = "boat_blue_x" if prof_blue else f"boat_{len(canonicals)+1:03d}"
            canonical = {
                "id": canonical_id,
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

    work["canonical_id"] = work.apply(
        lambda row: profile_to_canonical[
            tuple(row[col] for col in profile_cols)
        ],
        axis=1,
    )

    canonical_display = (
        work.groupby("canonical_id")["competitor_raw"]
        .agg(lambda s: s.value_counts(dropna=False).index[0])
        .to_dict()
    )
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


def _build_group_data() -> tuple[list[dict[str, Any]], pd.DataFrame]:
    frames_by_group: dict[str, list[pd.DataFrame]] = {}

    for year in sorted(EVENT_URLS_BY_YEAR):
        event_url = EVENT_URLS_BY_YEAR[year]
        class_groups = _discover_class_groups(event_url)
        class_names = [name for _, group_classes in class_groups for name in group_classes]
        if not class_names:
            continue
        payloads = fetch_class_results_batch(event_url, class_names)

        for group_label, group_classes in class_groups:
            payload_by_class = {name: payloads[name] for name in group_classes}
            available = [set(parse_available_race_labels_from_result_payload(payload_by_class[name])) for name in group_classes]
            aligned = sorted(set.intersection(*available), key=_race_sort_key)
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

            race_dates = _race_dates_by_label(payload_by_class, selected)
            combined["group"] = group_label
            combined["race_date"] = combined["race"].map(race_dates)
            frames_by_group.setdefault(group_label, []).append(combined)

    groups: list[dict[str, Any]] = []
    all_rows: list[pd.DataFrame] = []
    for group_label, parts in frames_by_group.items():
        combined = pd.concat(parts, ignore_index=True)
        if combined.empty:
            continue

        combined = _soft_match_competitors(combined)
        combined["race_local"] = combined["race"].astype(str)

        race_keys = combined.loc[:, ["year", "race", "race_date"]].drop_duplicates().copy()
        race_keys["race_local_num"] = race_keys["race"].map(_race_num)
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

        race_dates: dict[str, datetime] = {}
        for _, row in race_keys.iterrows():
            race_label = str(row["race_global"])
            race_date = row["race_date"]
            if pd.isna(race_date):
                continue
            race_dates[race_label] = pd.Timestamp(race_date).to_pydatetime()

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
    stacked = pd.concat(stacked_rows, ignore_index=True)
    stacked = _soft_match_competitors(stacked)

    updated_groups: list[dict[str, Any]] = []
    for idx, group in enumerate(groups):
        updated = dict(group)
        updated["combined"] = (
            stacked[stacked["_group_idx"] == idx]
            .drop(columns=["_group_idx"])
            .reset_index(drop=True)
        )
        updated_groups.append(updated)

    all_data = (
        stacked.drop(columns=["_group_idx"]).reset_index(drop=True)
        if "_group_idx" in stacked.columns
        else stacked.reset_index(drop=True)
    )
    return updated_groups, all_data


def _estimate_global_q(all_data: pd.DataFrame) -> float:
    if all_data.empty:
        return 1e-5

    working = all_data.copy()
    if "race_status_code" in working.columns:
        status_series = working["race_status_code"]
    else:
        status_series = pd.Series("", index=working.index, dtype="object")
    working["status_upper"] = status_series.fillna("").astype(str).str.upper().str.strip()

    finish_mask = working["beregnet_seconds"].notna() & (~working["status_upper"].isin(NON_OBS_STATUSES))
    finishers = working.loc[finish_mask, ["group", "race", "race_date", "competitor", "beregnet_seconds"]].copy()
    if finishers.empty:
        return 1e-5

    finishers["y"] = np.log(finishers["beregnet_seconds"].astype(float).clip(lower=EPS))
    race_mean = finishers.groupby(["group", "race"])["y"].transform("mean")
    finishers["perf"] = -(finishers["y"] - race_mean)
    finishers = finishers.sort_values(["group", "competitor", "race_date", "race"]).reset_index(drop=True)

    q_samples: list[float] = []
    for (_, _), frame in finishers.groupby(["group", "competitor"], sort=False):
        frame = frame.dropna(subset=["race_date"]).copy()
        if len(frame) < 2:
            continue
        perf = frame["perf"].to_numpy(dtype=float)
        dates = pd.to_datetime(frame["race_date"]).dt.to_pydatetime()
        for idx in range(1, len(perf)):
            delta_days = max(1, (dates[idx] - dates[idx - 1]).days)
            q_samples.append(float((perf[idx] - perf[idx - 1]) ** 2 / delta_days))

    if not q_samples:
        return 1e-5

    q = float(np.median(np.array(q_samples, dtype=float)))
    return max(q, 1e-8)


def _build_competitor_year_group_map(all_data: pd.DataFrame) -> dict[tuple[str, int], str]:
    if all_data.empty:
        return {}

    work = all_data.copy()
    status_series = work["race_status_code"] if "race_status_code" in work.columns else pd.Series("", index=work.index)
    work["status_upper"] = status_series.fillna("").astype(str).str.upper().str.strip()
    work["is_obs"] = work["beregnet_seconds"].notna() & (~work["status_upper"].isin(NON_OBS_STATUSES))

    summary = (
        work.groupby(["competitor", "year", "group"], as_index=False)
        .agg(
            observed_count=("is_obs", "sum"),
            row_count=("race", "count"),
        )
        .sort_values(["competitor", "year", "observed_count", "row_count", "group"], ascending=[True, True, False, False, True])
    )
    chosen = summary.drop_duplicates(subset=["competitor", "year"], keep="first")
    mapping = {
        (str(row["competitor"]), int(row["year"])): str(row["group"])
        for _, row in chosen.iterrows()
    }
    return mapping


def _load_group_q_cache(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    if isinstance(payload, dict) and isinstance(payload.get("group_q"), dict):
        source = payload["group_q"]
    elif isinstance(payload, dict):
        source = payload
    else:
        return {}

    q_map: dict[str, float] = {}
    for key, value in source.items():
        try:
            q_val = float(value)
        except (TypeError, ValueError):
            continue
        if np.isfinite(q_val) and q_val > 0.0:
            q_map[str(key)] = q_val
    return q_map


def _save_group_q_cache(path: Path, q_by_group: dict[str, float]) -> None:
    payload = {
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "group_q": {str(k): float(v) for k, v in sorted(q_by_group.items())},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _prepare_group_runtime(group: dict[str, Any]) -> dict[str, Any]:
    cached = group.get("_runtime")
    if isinstance(cached, dict):
        return cached

    combined: pd.DataFrame = group["combined"].copy()
    combined["competitor"] = combined["competitor"].astype(str)
    combined["status_upper"] = combined["race_status_code"].fillna("").astype(str).str.upper().str.strip()

    competitors = sorted(combined["competitor"].dropna().unique().tolist())
    race_rows_by_label: dict[str, pd.DataFrame] = {}
    race_lookup_by_label: dict[str, dict[str, dict[str, Any]]] = {}
    for race_label, race_rows in combined.groupby("race", sort=False):
        label = str(race_label)
        race_frame = race_rows.copy()
        race_rows_by_label[label] = race_frame
        race_lookup_by_label[label] = (
            race_frame.drop_duplicates(subset=["competitor"], keep="first")
            .set_index("competitor", drop=False)
            .to_dict("index")
        )

    runtime = {
        "combined": combined,
        "competitors": competitors,
        "race_rows_by_label": race_rows_by_label,
        "race_lookup_by_label": race_lookup_by_label,
    }
    group["_runtime"] = runtime
    return runtime


def _mu_hat_given_measurement_variance(
    measurement_variance: float,
    centered_values: np.ndarray,
    p_priors: np.ndarray,
) -> float:
    s_values = np.maximum(EPS, p_priors + measurement_variance)
    weights = 1.0 / s_values
    return float(np.sum(weights * centered_values) / np.sum(weights))


def _measurement_nll(measurement_variance: float, centered_values: np.ndarray, p_priors: np.ndarray) -> float:
    s_values = np.maximum(EPS, p_priors + measurement_variance)
    mu_hat = _mu_hat_given_measurement_variance(measurement_variance, centered_values, p_priors)
    return 0.5 * float(
        np.sum(np.log(2.0 * math.pi * s_values) + np.square(centered_values - mu_hat) / s_values)
    )


def _fit_measurement_variance(centered_values: np.ndarray, p_priors: np.ndarray) -> float:
    if centered_values.size == 0:
        return float("nan")

    lower = EPS
    base_scale = max(
        EPS,
        float(np.var(centered_values, ddof=0)),
        float(np.mean(np.square(centered_values))),
        float(np.mean(p_priors)),
    )
    upper = max(lower * 16.0, base_scale)
    upper_nll = _measurement_nll(upper, centered_values, p_priors)

    for _ in range(8):
        candidate_upper = upper * 4.0
        candidate_nll = _measurement_nll(candidate_upper, centered_values, p_priors)
        if candidate_nll < upper_nll:
            upper = candidate_upper
            upper_nll = candidate_nll
        else:
            break

    phi = (1.0 + math.sqrt(5.0)) / 2.0
    left = lower
    right = upper
    c = right - (right - left) / phi
    d = left + (right - left) / phi
    fc = _measurement_nll(c, centered_values, p_priors)
    fd = _measurement_nll(d, centered_values, p_priors)

    for _ in range(32):
        if fc <= fd:
            right = d
            d = c
            fd = fc
            c = right - (right - left) / phi
            fc = _measurement_nll(c, centered_values, p_priors)
        else:
            left = c
            c = d
            fc = fd
            d = left + (right - left) / phi
            fd = _measurement_nll(d, centered_values, p_priors)

    return max(EPS, float(0.5 * (left + right)))


def _fit_day_parameters(centered_values: np.ndarray, p_priors: np.ndarray) -> tuple[float, float, float]:
    if centered_values.size == 0:
        return float("nan"), float("nan"), 0.0

    r_t = _fit_measurement_variance(centered_values, p_priors)
    mu_hat = _mu_hat_given_measurement_variance(r_t, centered_values, p_priors)
    nll_sum = _measurement_nll(r_t, centered_values, p_priors)
    return mu_hat, r_t, nll_sum


def _compute_observation_step(
    observed: pd.DataFrame,
    prior_by_competitor: dict[str, tuple[float, float, int]],
) -> dict[str, Any]:
    if observed.empty:
        return {
            "b_hat": float("nan"),
            "r_t": float("nan"),
            "innovation_by_competitor": {},
            "gain_by_competitor": {},
            "y_pred_loo_by_competitor": {},
            "nll_sum": 0.0,
            "observed_count": 0,
            "loo_sq_error_sum": 0.0,
            "loo_error_count": 0,
        }

    competitors = observed["competitor"].astype(str).tolist()
    beregnet_seconds = observed["beregnet_seconds"].to_numpy(dtype=float)
    y_values = np.log(np.clip(beregnet_seconds, EPS, None))
    x_priors = np.array([prior_by_competitor[competitor][0] for competitor in competitors], dtype=float)
    p_priors = np.array([prior_by_competitor[competitor][1] for competitor in competitors], dtype=float)

    centered_values = y_values - x_priors
    b_hat, r_t, nll_sum = _fit_day_parameters(centered_values, p_priors)

    y_pred_loo_by_competitor: dict[str, float] = {}
    if len(competitors) == 1:
        y_pred_loo_by_competitor[competitors[0]] = float(b_hat + x_priors[0])
    else:
        for idx, competitor in enumerate(competitors):
            centered_values_loo = np.delete(centered_values, idx)
            p_priors_loo = np.delete(p_priors, idx)
            b_hat_loo, _, _ = _fit_day_parameters(centered_values_loo, p_priors_loo)
            y_pred_loo_by_competitor[competitor] = float(b_hat_loo + x_priors[idx])

    innovations = centered_values - b_hat
    gains = p_priors / (p_priors + r_t)

    innovation_by_competitor = {
        competitor: float(innovation)
        for competitor, innovation in zip(competitors, innovations)
    }
    gain_by_competitor = {
        competitor: float(gain)
        for competitor, gain in zip(competitors, gains)
    }

    loo_sq_error_sum = 0.0
    loo_error_count = 0
    for competitor, x_prior, observed_seconds in zip(competitors, x_priors, beregnet_seconds):
        y_pred_loo = y_pred_loo_by_competitor[competitor]
        pred_seconds = float(np.exp(y_pred_loo))
        if np.isfinite(pred_seconds):
            error = float(observed_seconds - pred_seconds)
            loo_sq_error_sum += error * error
            loo_error_count += 1

    return {
        "b_hat": b_hat,
        "r_t": r_t,
        "innovation_by_competitor": innovation_by_competitor,
        "gain_by_competitor": gain_by_competitor,
        "y_pred_loo_by_competitor": y_pred_loo_by_competitor,
        "nll_sum": nll_sum,
        "observed_count": len(competitors),
        "loo_sq_error_sum": loo_sq_error_sum,
        "loo_error_count": loo_error_count,
    }


def _run_group_filter(
    group: dict[str, Any],
    global_q: float,
    *,
    collect_history: bool = True,
) -> GroupFilterResult:
    runtime = _prepare_group_runtime(group)
    selected_races: list[str] = list(group["selected_races"])
    race_dates: dict[str, datetime] = dict(group["race_dates"])
    group_label = str(group["group"])

    competitors: list[str] = runtime["competitors"]
    race_rows_by_label: dict[str, pd.DataFrame] = runtime["race_rows_by_label"]
    race_lookup_by_label: dict[str, dict[str, dict[str, Any]]] = runtime["race_lookup_by_label"]
    p_cap = float(P_CAP_FACTOR * global_q)
    states: dict[str, BoatState] = {
        competitor: BoatState(x=0.0, p=p_cap, last_state_date=None)
        for competitor in competitors
    }

    history_rows: list[dict[str, Any]] = []
    nll_sum = 0.0
    observed_count = 0
    loo_sq_error_sum = 0.0
    loo_error_count = 0

    for race_label in selected_races:
        race_date = race_dates.get(race_label)
        if race_date is None:
            continue

        race_rows = race_rows_by_label.get(race_label)
        if race_rows is None:
            continue
        race_lookup = race_lookup_by_label.get(race_label, {})

        prior_by_competitor: dict[str, tuple[float, float, int]] = {}
        for competitor in competitors:
            state = states[competitor]
            if state.last_state_date is None:
                delta_days = 0
            else:
                delta_days = max(0, (race_date - state.last_state_date).days)
            x_prior = state.x
            p_prior = min(p_cap, state.p + delta_days * global_q)
            prior_by_competitor[competitor] = (x_prior, p_prior, delta_days)

        obs_mask = race_rows["beregnet_seconds"].notna() & (~race_rows["status_upper"].isin(NON_OBS_STATUSES))
        observed = race_rows.loc[obs_mask, ["competitor", "beregnet_seconds"]].copy()
        observed_step = _compute_observation_step(observed, prior_by_competitor)
        b_hat = float(observed_step["b_hat"])
        r_t = float(observed_step["r_t"])
        innovation_by_competitor = observed_step["innovation_by_competitor"]
        gain_by_competitor = observed_step["gain_by_competitor"]
        y_pred_loo_by_competitor = observed_step["y_pred_loo_by_competitor"]
        nll_sum += float(observed_step["nll_sum"])
        observed_count += int(observed_step["observed_count"])
        loo_sq_error_sum += float(observed_step["loo_sq_error_sum"])
        loo_error_count += int(observed_step["loo_error_count"])

        observed_competitors = set(observed["competitor"].astype(str).tolist()) if not observed.empty else set()

        for competitor in competitors:
            x_prior, p_prior, delta_days = prior_by_competitor[competitor]
            status = ""
            sailed_seconds: float | None = None
            beregnet_seconds: float | None = None
            race_local_value = race_label
            series_value = ""
            if collect_history:
                status_row = race_lookup.get(competitor)
                if status_row is not None:
                    status = str(status_row.get("race_status_code") or "").strip()
                    raw_sailed = status_row.get("sailed_seconds")
                    raw_beregnet = status_row.get("beregnet_seconds")
                    raw_race_local = status_row.get("race_local")
                    raw_series = status_row.get("series")
                    sailed_seconds = float(raw_sailed) if pd.notna(raw_sailed) else None
                    beregnet_seconds = float(raw_beregnet) if pd.notna(raw_beregnet) else None
                    if pd.notna(raw_race_local):
                        race_local_value = str(raw_race_local).strip() or race_label
                    if pd.notna(raw_series):
                        series_value = str(raw_series).strip()
                else:
                    status = "IKKE MED"

            if competitor in observed_competitors and competitor in innovation_by_competitor and not np.isnan(r_t):
                nu = float(innovation_by_competitor[competitor])
                k = float(gain_by_competitor[competitor])
                x_post = float(x_prior + k * nu)
                p_post = float((1.0 - k) * p_prior)
                observed_flag = True
                y_pred_loo = float(y_pred_loo_by_competitor.get(competitor, float("nan")))
            else:
                nu = float("nan")
                k = float("nan")
                x_post = float(x_prior)
                p_post = float(p_prior)
                observed_flag = False
                y_pred_loo = float("nan")

            states[competitor] = BoatState(x=x_post, p=p_post, last_state_date=race_date)

            if collect_history:
                history_rows.append(
                    {
                        "group": group_label,
                        "race": race_label,
                        "race_local": race_local_value,
                        "series": series_value,
                        "race_date": race_date.date().isoformat(),
                        "year": int(race_date.year),
                        "competitor": competitor,
                        "observed": observed_flag,
                        "status": status,
                        "sailed_seconds": sailed_seconds,
                        "beregnet_seconds": beregnet_seconds,
                        "b_t_hat": b_hat,
                        "delta_t_days": delta_days,
                        "global_q": global_q,
                        "x_prior": x_prior,
                        "p_prior": p_prior,
                        "r_t": r_t,
                        "innovation": nu,
                        "kalman_gain": k,
                        "y_pred_loo": y_pred_loo,
                        "x_post": x_post,
                        "p_post": p_post,
                    }
                )

    history = pd.DataFrame(history_rows) if collect_history else pd.DataFrame()
    return GroupFilterResult(
        history=history,
        nll_sum=nll_sum,
        observed_count=observed_count,
        loo_sq_error_sum=loo_sq_error_sum,
        loo_error_count=loo_error_count,
    )


def _run_all_groups_with_transfer(
    groups: list[dict[str, Any]],
    q_by_group: dict[str, float],
    competitor_year_group: dict[tuple[str, int], str],
    *,
    collect_history: bool = True,
) -> tuple[pd.DataFrame, dict[str, float], dict[str, int]]:
    if not groups:
        return pd.DataFrame(), {}, {}

    groups_by_name = {str(group["group"]): group for group in groups}
    p_cap_by_group = {
        group_name: float(P_CAP_FACTOR * q_by_group[group_name])
        for group_name in groups_by_name
    }
    states: dict[str, dict[str, BoatState]] = {}
    competitors_by_group: dict[str, list[str]] = {}
    events: list[dict[str, Any]] = []

    for group_name, group in groups_by_name.items():
        runtime = _prepare_group_runtime(group)
        combined = runtime["combined"]
        competitors = runtime["competitors"]
        competitors_by_group[group_name] = competitors
        states[group_name] = {
            competitor: BoatState(x=0.0, p=p_cap_by_group[group_name], last_state_date=None)
            for competitor in competitors
        }

        selected_races: list[str] = list(group["selected_races"])
        race_dates: dict[str, datetime] = dict(group["race_dates"])
        for race_label in selected_races:
            race_date = race_dates.get(race_label)
            if race_date is None:
                continue
            events.append(
                {
                    "group": group_name,
                    "race": race_label,
                    "race_date": race_date,
                    "year": int(race_date.year),
                    "race_num": _race_num(race_label),
                }
            )

    events = sorted(events, key=lambda item: (item["race_date"], item["race_num"], item["group"], item["race"]))
    first_race_by_group_year: set[tuple[str, int, str]] = set()
    last_race_by_group_year: set[tuple[str, int, str]] = set()
    for group_name in sorted(groups_by_name):
        gevents = [event for event in events if event["group"] == group_name]
        by_year: dict[int, list[dict[str, Any]]] = {}
        for event in gevents:
            by_year.setdefault(int(event["year"]), []).append(event)
        for year, year_events in by_year.items():
            ordered = sorted(year_events, key=lambda item: (item["race_date"], item["race_num"], item["race"]))
            first_race_by_group_year.add((group_name, year, str(ordered[0]["race"])))
            last_race_by_group_year.add((group_name, year, str(ordered[-1]["race"])))

    end_of_year_state: dict[tuple[str, int, str], BoatState] = {}
    nll_by_group: dict[str, float] = {group_name: 0.0 for group_name in groups_by_name}
    obs_by_group: dict[str, int] = {group_name: 0 for group_name in groups_by_name}
    history_rows: list[dict[str, Any]] = []

    for event in events:
        group_name = str(event["group"])
        race_label = str(event["race"])
        race_date = event["race_date"]
        year = int(event["year"])
        global_q = float(q_by_group[group_name])
        p_cap = float(p_cap_by_group[group_name])
        group_states = states[group_name]
        competitors = competitors_by_group[group_name]
        group = groups_by_name[group_name]
        runtime = _prepare_group_runtime(group)
        race_rows_by_label: dict[str, pd.DataFrame] = runtime["race_rows_by_label"]
        race_lookup_by_label: dict[str, dict[str, dict[str, Any]]] = runtime["race_lookup_by_label"]

        if (group_name, year, race_label) in first_race_by_group_year:
            for competitor in competitors:
                assigned_group = competitor_year_group.get((competitor, year))
                prev_group = competitor_year_group.get((competitor, year - 1))
                if assigned_group != group_name or not prev_group or prev_group == group_name:
                    continue
                snapshot = end_of_year_state.get((prev_group, year - 1, competitor))
                if snapshot is None:
                    continue
                group_states[competitor] = BoatState(
                    x=float(snapshot.x),
                    p=float(snapshot.p),
                    last_state_date=snapshot.last_state_date,
                )

        race_rows = race_rows_by_label.get(race_label)
        if race_rows is None:
            continue
        race_lookup = race_lookup_by_label.get(race_label, {})

        prior_by_competitor: dict[str, tuple[float, float, int]] = {}
        for competitor in competitors:
            state = group_states[competitor]
            if state.last_state_date is None:
                delta_days = 0
            else:
                delta_days = max(0, (race_date - state.last_state_date).days)
            x_prior = state.x
            p_prior = min(p_cap, state.p + delta_days * global_q)
            prior_by_competitor[competitor] = (x_prior, p_prior, delta_days)

        obs_mask = race_rows["beregnet_seconds"].notna() & (~race_rows["status_upper"].isin(NON_OBS_STATUSES))
        observed = race_rows.loc[obs_mask, ["competitor", "beregnet_seconds"]].copy()
        observed_step = _compute_observation_step(observed, prior_by_competitor)
        b_hat = float(observed_step["b_hat"])
        r_t = float(observed_step["r_t"])
        innovation_by_competitor = observed_step["innovation_by_competitor"]
        gain_by_competitor = observed_step["gain_by_competitor"]
        y_pred_loo_by_competitor = observed_step["y_pred_loo_by_competitor"]
        nll_by_group[group_name] += float(observed_step["nll_sum"])
        obs_by_group[group_name] += int(observed_step["observed_count"])

        observed_competitors = set(observed["competitor"].astype(str).tolist()) if not observed.empty else set()

        for competitor in competitors:
            x_prior, p_prior, delta_days = prior_by_competitor[competitor]
            status = ""
            sailed_seconds: float | None = None
            beregnet_seconds: float | None = None
            race_local_value = race_label
            series_value = ""
            if collect_history:
                status_row = race_lookup.get(competitor)
                if status_row is not None:
                    status = str(status_row.get("race_status_code") or "").strip()
                    raw_sailed = status_row.get("sailed_seconds")
                    raw_beregnet = status_row.get("beregnet_seconds")
                    raw_race_local = status_row.get("race_local")
                    raw_series = status_row.get("series")
                    sailed_seconds = float(raw_sailed) if pd.notna(raw_sailed) else None
                    beregnet_seconds = float(raw_beregnet) if pd.notna(raw_beregnet) else None
                    if pd.notna(raw_race_local):
                        race_local_value = str(raw_race_local).strip() or race_label
                    if pd.notna(raw_series):
                        series_value = str(raw_series).strip()
                else:
                    status = "IKKE MED"

            if competitor in observed_competitors and competitor in innovation_by_competitor and not np.isnan(r_t):
                nu = float(innovation_by_competitor[competitor])
                k = float(gain_by_competitor[competitor])
                x_post = float(x_prior + k * nu)
                p_post = float((1.0 - k) * p_prior)
                observed_flag = True
                y_pred_loo = float(y_pred_loo_by_competitor.get(competitor, float("nan")))
            else:
                nu = float("nan")
                k = float("nan")
                x_post = float(x_prior)
                p_post = float(p_prior)
                observed_flag = False
                y_pred_loo = float("nan")

            group_states[competitor] = BoatState(x=x_post, p=p_post, last_state_date=race_date)

            if collect_history:
                history_rows.append(
                    {
                        "group": group_name,
                        "race": race_label,
                        "race_local": race_local_value,
                        "series": series_value,
                        "race_date": race_date.date().isoformat(),
                        "year": int(year),
                        "competitor": competitor,
                        "observed": observed_flag,
                        "status": status,
                        "sailed_seconds": sailed_seconds,
                        "beregnet_seconds": beregnet_seconds,
                        "b_t_hat": b_hat,
                        "delta_t_days": delta_days,
                        "global_q": global_q,
                        "x_prior": x_prior,
                        "p_prior": p_prior,
                        "r_t": r_t,
                        "innovation": nu,
                        "kalman_gain": k,
                        "y_pred_loo": y_pred_loo,
                        "x_post": x_post,
                        "p_post": p_post,
                    }
                )

        if (group_name, year, race_label) in last_race_by_group_year:
            for competitor, state in group_states.items():
                end_of_year_state[(group_name, year, competitor)] = BoatState(
                    x=float(state.x),
                    p=float(state.p),
                    last_state_date=state.last_state_date,
                )

    history = pd.DataFrame(history_rows) if collect_history else pd.DataFrame()
    return history, nll_by_group, obs_by_group


def _q_objective(groups: list[dict[str, Any]], q_value: float) -> tuple[float, int]:
    sq_error_sum = 0.0
    obs_count = 0
    for group in groups:
        result = _run_group_filter(group, q_value, collect_history=False)
        sq_error_sum += float(result.loo_sq_error_sum)
        obs_count += int(result.loo_error_count)
    if obs_count == 0:
        return float("inf"), 0

    rmse = float(np.sqrt(sq_error_sum / obs_count))
    return rmse, obs_count


def _evaluate_q_score(
    groups: list[dict[str, Any]],
    q_value: float,
    score_cache: dict[float, tuple[float, int]] | None = None,
) -> tuple[float, int]:
    key = float(q_value)
    if score_cache is not None and key in score_cache:
        return score_cache[key]

    result = _q_objective(groups, key)
    if score_cache is not None:
        score_cache[key] = result
    return result


def _q_diagnostics(
    groups: list[dict[str, Any]],
    q_values: np.ndarray,
    score_cache: dict[float, tuple[float, int]] | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, float | int]] = []
    for q_value in q_values:
        score, obs = _evaluate_q_score(groups, float(q_value), score_cache=score_cache)
        rows.append(
            {
                "q_value": float(q_value),
                "one_step_rmse_seconds": float(score),
                "observations": int(obs),
            }
        )
    return pd.DataFrame(rows).sort_values("q_value").reset_index(drop=True)


def _fit_global_q(
    groups: list[dict[str, Any]],
    initial_q: float,
    *,
    score_cache: dict[float, tuple[float, int]] | None = None,
) -> tuple[float, float, int]:
    search_min = Q_SEARCH_MIN
    candidates = np.logspace(math.log10(search_min), math.log10(Q_SEARCH_MAX), 61, dtype=float)
    candidate_values = sorted(
        set(float(max(search_min, min(Q_SEARCH_MAX, value))) for value in candidates)
        | {float(max(search_min, min(Q_SEARCH_MAX, initial_q)))}
    )

    best_q = candidate_values[0]
    best_score = float("inf")
    best_obs = 0

    for q_value in candidate_values:
        score, obs = _evaluate_q_score(groups, q_value, score_cache=score_cache)
        if score < best_score:
            best_q = q_value
            best_score = score
            best_obs = obs

    for _ in range(2):
        local = [float(best_q * factor) for factor in np.logspace(-0.7, 0.7, 21)]
        local = sorted(set(max(search_min, min(Q_SEARCH_MAX, value)) for value in local))
        for q_value in local:
            score, obs = _evaluate_q_score(groups, q_value, score_cache=score_cache)
            if score < best_score:
                best_q = q_value
                best_score = score
                best_obs = obs

    return best_q, best_score, best_obs


def _export_boat_plot_data(
    frame: pd.DataFrame,
    *,
    allowed_competitors: set[str] | None,
    output_dir: Path,
) -> Path:
    required_columns = [
        "competitor",
        "group",
        "series",
        "race",
        "race_local",
        "race_date",
        "year",
        "observed",
        "x_prior",
        "x_post",
        "x_obs",
        "x_q25",
        "x_q75",
    ]
    missing = [column for column in required_columns if column not in frame.columns]
    if missing:
        raise ValueError(f"Missing required columns for boat-plot export: {', '.join(missing)}")
    if frame.empty:
        raise ValueError("Cannot export boat-plot data from empty frame.")

    output_dir.mkdir(parents=True, exist_ok=True)

    work = frame.copy()
    work["race_num"] = work["race"].map(_race_num)
    work = work.dropna(subset=["race_num"])
    if work.empty:
        raise ValueError("No valid race labels found after parsing race numbers.")

    competitors = sorted(work["competitor"].dropna().astype(str).unique().tolist())
    if allowed_competitors is not None:
        competitors = [name for name in competitors if name in allowed_competitors]
    if not competitors:
        raise ValueError("No competitors matched allowed_competitors for boat-plot export.")

    manifest_rows: list[dict[str, Any]] = []
    for idx, competitor in enumerate(competitors, start=1):
        c = work[work["competitor"].astype(str) == competitor].copy()
        if c.empty:
            raise ValueError(f"No rows for competitor '{competitor}' during plot export.")
        c["race_date_dt"] = pd.to_datetime(c["race_date"], errors="coerce")
        c = c.dropna(subset=["race_date_dt"])
        if c.empty:
            raise ValueError(f"No valid race_date values for competitor '{competitor}'.")
        c = c.sort_values(["race_date_dt", "year", "race_num", "race", "group"]).reset_index(drop=True)
        c["race_local_display"] = c["race_local"].astype(str)
        event_cols = ["year", "race", "race_local_display", "race_date", "group", "race_num", "race_date_dt"]
        events = (
            c.loc[:, event_cols]
            .drop_duplicates()
            .sort_values(["race_date_dt", "year", "race_num", "race_local_display", "race", "group"], na_position="last")
            .reset_index(drop=True)
        )
        if events.empty:
            raise ValueError(f"No race events could be formed for competitor '{competitor}'.")
        events["x_pos"] = np.arange(1, len(events) + 1, dtype=int)
        c = c.merge(
            events.loc[:, ["year", "race", "race_date", "group", "x_pos", "race_local_display"]],
            on=["year", "race", "race_date", "group"],
            how="left",
        )
        c = c.dropna(subset=["x_pos"]).copy()
        c["x_pos"] = c["x_pos"].astype(int)
        c = c.sort_values(["x_pos"]).reset_index(drop=True)

        base = (
            events.loc[:, ["x_pos", "year", "race_local_display", "group"]]
            .rename(columns={"race_local_display": "race_local"})
            .copy()
        )
        base["race_display"] = base.apply(
            lambda row: f"'{int(row['year']) % 100:02d}-{str(row['race_local'])}",
            axis=1,
        )
        base = base.drop(columns=["year"])
        series_values = (
            c.loc[:, ["x_pos", "series"]]
            .drop_duplicates(subset=["x_pos"], keep="first")
            .copy()
        )
        values = c.loc[:, ["x_pos", "observed", "x_prior", "x_post", "x_obs", "x_q25", "x_q75"]].drop_duplicates(
            subset=["x_pos"],
            keep="first",
        )
        values["x_post"] = np.where(values["observed"].astype(bool), values["x_post"], np.nan)
        values = values.drop(columns=["observed"])
        plot_data = (
            base.merge(series_values, on="x_pos", how="left")
            .merge(values, on="x_pos", how="left")
            .sort_values("x_pos")
            .reset_index(drop=True)
        )
        local_y_values: list[float] = (
            plot_data["x_prior"].dropna().astype(float).tolist()
            + plot_data["x_q25"].dropna().astype(float).tolist()
            + plot_data["x_q75"].dropna().astype(float).tolist()
            + plot_data["x_obs"].dropna().astype(float).tolist()
        )
        if not local_y_values:
            raise ValueError(f"No y-values found for competitor '{competitor}' during plot export.")
        local_y_min = float(min(local_y_values))
        local_y_max = float(max(local_y_values))
        local_span = local_y_max - local_y_min
        local_pad = 0.06 * local_span if local_span > 0 else max(abs(local_y_min) * 0.1, 0.05)
        local_y_min -= local_pad
        local_y_max += local_pad

        slug = _slugify_filename(competitor)
        data_name = f"boat_plot_{idx:02d}_{slug}.csv"
        data_path = output_dir / data_name
        plot_data.to_csv(data_path, index=False)

        if plot_data["group"].dropna().empty:
            raise ValueError(f"Missing group values for competitor '{competitor}'.")
        group_for_competitor = str(plot_data["group"].dropna().astype(str).iloc[0])
        competitor_display = _display_name(competitor)
        group_display = _display_name(group_for_competitor)
        manifest_rows.append(
            {
                "competitor": competitor,
                "competitor_display": competitor_display,
                "group": group_for_competitor,
                "group_display": group_display,
                "data_csv": data_name,
                "x_max": int(plot_data["x_pos"].max()),
                "y_min": float(local_y_min),
                "y_max": float(local_y_max),
            }
        )

    if not manifest_rows:
        raise ValueError("No competitor plot data exported.")

    manifest = pd.DataFrame(manifest_rows)
    manifest_path = output_dir / "boat_plot_manifest.csv"
    manifest.to_csv(manifest_path, index=False)
    return manifest_path


def _export_latest_race_table(
    frame: pd.DataFrame,
    *,
    output_path: Path,
    year: int,
    race_local: str,
    group_filter: str | None = None,
    series_filter: str | None = None,
) -> Path:
    required_columns = [
        "competitor",
        "group",
        "series",
        "race_local",
        "year",
        "observed",
        "beregnet_seconds",
        "y_pred_loo",
    ]
    missing = [column for column in required_columns if column not in frame.columns]
    if missing:
        raise ValueError(f"Missing required columns for latest-race table export: {', '.join(missing)}")

    work = frame.copy()
    work["year_num"] = pd.to_numeric(work["year"], errors="coerce")
    if work["year_num"].isna().any():
        raise ValueError("Missing year values in latest-race table export frame.")
    work["race_local_norm"] = work["race_local"].astype(str).str.strip()

    race_rows = work[(work["year_num"].astype(int) == int(year)) & (work["race_local_norm"] == str(race_local))].copy()
    if group_filter is not None:
        race_rows = race_rows[race_rows["group"].astype(str) == str(group_filter)].copy()
    if series_filter is not None:
        race_rows = race_rows[race_rows["series"].astype(str) == str(series_filter)].copy()
    if race_rows.empty:
        if group_filter is None and series_filter is None:
            raise ValueError(f"No rows found for latest-race table: year={year}, race_local={race_local}.")
        filters: list[str] = []
        if group_filter is not None:
            filters.append(f"group={group_filter}")
        if series_filter is not None:
            filters.append(f"series={series_filter}")
        filter_txt = ", ".join(filters)
        raise ValueError(f"No rows found for latest-race table: year={year}, race_local={race_local}, {filter_txt}.")

    observed_mask = race_rows["observed"] == True  # noqa: E712
    race_rows = race_rows.loc[observed_mask].copy()
    if race_rows.empty:
        raise ValueError(f"No observed rows found for latest-race table: year={year}, race_local={race_local}.")

    measured_seconds = pd.to_numeric(race_rows["beregnet_seconds"], errors="coerce")
    if measured_seconds.dropna().empty:
        raise ValueError(f"No observed measured times found for latest-race table: year={year}, race_local={race_local}.")
    loo_pred_seconds = pd.to_numeric(race_rows["y_pred_loo"], errors="coerce").apply(
        lambda y: float(np.exp(y)) if pd.notna(y) and np.isfinite(y) else np.nan
    )
    loo_time_delta_seconds = measured_seconds - loo_pred_seconds
    loo_time_delta_pct = (loo_time_delta_seconds / loo_pred_seconds) * 100.0
    group_series = race_rows["group"].astype(str)
    actual_rank = measured_seconds.groupby(group_series).rank(method="min", ascending=True)
    loo_rank = loo_pred_seconds.groupby(group_series).rank(method="min", ascending=True)
    loo_error_rank = loo_rank - actual_rank

    result = pd.DataFrame(
        {
            "deltager": race_rows["competitor"].astype(str).map(_display_name),
            "malt_tid": measured_seconds.map(_format_seconds_hms),
            "loo_prediktion": loo_pred_seconds.map(_format_seconds_hms),
            "loo_prediktions_fejl": loo_error_rank.map(_format_rank_error),
            "tidsafvigelse": loo_time_delta_seconds.map(_format_seconds_hms),
            "tidsafvigelse_procent": loo_time_delta_pct.map(_format_percent_signed),
            "_sort_tidsafvigelse_pct": loo_time_delta_pct.astype(float),
        }
    )
    result = result.sort_values(["_sort_tidsafvigelse_pct", "deltager"], ascending=[True, True]).reset_index(drop=True)
    result = result.drop(columns=["_sort_tidsafvigelse_pct"])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path, index=False)
    return output_path


def _export_ml_fit_example(history: pd.DataFrame, *, output_dir: Path) -> list[Path]:
    observed = history[history["observed"] == True].copy()  # noqa: E712
    if observed.empty:
        raise ValueError("No observed rows available for ML-fit example export.")

    race_summary_rows: list[dict[str, Any]] = []
    for (group_name, race_label), sub in observed.groupby(["group", "race"]):
        p_values = pd.to_numeric(sub["p_prior"], errors="coerce").dropna()
        if p_values.empty:
            continue
        p_mean = float(p_values.mean())
        p_std = float(p_values.std(ddof=0))
        p_cv = float(p_std / p_mean) if p_mean > 0 else float("inf")
        race_summary_rows.append(
            {
                "group": str(group_name),
                "race": str(race_label),
                "n": int(len(sub)),
                "p_mean": p_mean,
                "p_cv": p_cv,
            }
        )

    race_summary = pd.DataFrame(race_summary_rows)
    if race_summary.empty:
        raise ValueError("Could not summarize races for ML-fit example export.")

    eligible = race_summary[race_summary["n"] >= 10].copy()
    if eligible.empty:
        eligible = race_summary.copy()
    example_row = eligible.sort_values(["p_cv", "n", "group", "race"], ascending=[True, False, True, True]).iloc[0]

    example = observed[
        (observed["group"].astype(str) == str(example_row["group"]))
        & (observed["race"].astype(str) == str(example_row["race"]))
    ].copy()
    if example.empty:
        raise ValueError("Selected ML-fit example race unexpectedly had no observed rows.")

    corrected_time_seconds = pd.to_numeric(example["beregnet_seconds"], errors="coerce") / np.exp(
        pd.to_numeric(example["x_prior"], errors="coerce")
    )
    corrected_time_seconds = corrected_time_seconds[np.isfinite(corrected_time_seconds) & (corrected_time_seconds > 0)]
    if corrected_time_seconds.empty:
        raise ValueError("No finite corrected times available for ML-fit example export.")

    mu_hat = float(pd.to_numeric(example["b_t_hat"], errors="coerce").dropna().iloc[0])
    r_t = float(pd.to_numeric(example["r_t"], errors="coerce").dropna().iloc[0])
    p_ref = float(pd.to_numeric(example["p_prior"], errors="coerce").dropna().mean())
    sigma_total = math.sqrt(max(EPS, p_ref + r_t))

    x_min = min(float(corrected_time_seconds.min()) * 0.92, float(np.exp(mu_hat - 4.0 * sigma_total)))
    x_max = max(float(corrected_time_seconds.max()) * 1.08, float(np.exp(mu_hat + 4.0 * sigma_total)))
    x_grid = np.linspace(max(EPS, x_min), x_max, 400, dtype=float)
    z_grid = (np.log(x_grid) - mu_hat) / (sigma_total * math.sqrt(2.0))
    model_cdf = 0.5 * (1.0 + np.array([math.erf(float(z)) for z in z_grid], dtype=float))
    empirical_sorted = np.sort(corrected_time_seconds.to_numpy(dtype=float))
    empirical_cdf = np.arange(1, len(empirical_sorted) + 1, dtype=float) / len(empirical_sorted)

    empirical_cdf_path = output_dir / "ml_fit_example_empirical_cdf.csv"
    model_cdf_path = output_dir / "ml_fit_example_model_cdf.csv"
    pd.DataFrame({"time_seconds": empirical_sorted, "cdf": empirical_cdf}).to_csv(empirical_cdf_path, index=False)
    pd.DataFrame({"time_seconds": x_grid, "cdf": model_cdf}).to_csv(model_cdf_path, index=False)
    return [empirical_cdf_path, model_cdf_path]


def main() -> int:
    output_dir = Path("analysis")
    output_dir.mkdir(parents=True, exist_ok=True)

    groups, all_data = _build_group_data()
    if not groups:
        raise RuntimeError("No group data could be built.")

    q_fit_rows: list[dict[str, Any]] = []
    q_by_group: dict[str, float] = {}
    q_grid = np.logspace(math.log10(Q_SEARCH_MIN), math.log10(Q_SEARCH_MAX), 61, dtype=float)
    q_diag_frames: list[pd.DataFrame] = []
    q_cache_path = output_dir / GROUP_Q_CACHE_FILENAME
    cached_q_map = _load_group_q_cache(q_cache_path)
    missing_q_groups = [str(group["group"]) for group in groups if str(group["group"]) not in cached_q_map]
    using_cached_q = bool(cached_q_map) and not missing_q_groups

    if using_cached_q:
        for group in groups:
            group_name = str(group["group"])
            group_initial_q = _estimate_global_q(group["combined"])
            group_q = float(cached_q_map[group_name])
            group_score, group_obs = _q_objective([group], group_q)
            q_by_group[group_name] = group_q
            q_fit_rows.append(
                {
                    "group": group_name,
                    "initial_q": float(group_initial_q),
                    "fitted_q": group_q,
                    "q_objective_score": float(group_score),
                    "q_obs": int(group_obs),
                    "q_source": "cache",
                }
            )
            q_diag_frames.append(
                pd.DataFrame(
                    [
                        {
                            "group": group_name,
                            "q_value": group_q,
                            "one_step_rmse_seconds": float(group_score),
                            "observations": int(group_obs),
                            "source": "cache",
                        }
                    ]
                )
            )
    else:
        for group in groups:
            group_name = str(group["group"])
            group_initial_q = _estimate_global_q(group["combined"])
            score_cache: dict[float, tuple[float, int]] = {}
            group_q, group_score, group_obs = _fit_global_q(
                [group],
                initial_q=group_initial_q,
                score_cache=score_cache,
            )
            q_by_group[group_name] = float(group_q)
            q_fit_rows.append(
                {
                    "group": group_name,
                    "initial_q": float(group_initial_q),
                    "fitted_q": float(group_q),
                    "q_objective_score": float(group_score),
                    "q_obs": int(group_obs),
                    "q_source": "fitted",
                }
            )
            q_diag = _q_diagnostics([group], q_grid, score_cache=score_cache)
            q_diag["group"] = group_name
            q_diag["source"] = "fitted"
            q_diag_frames.append(q_diag)
        _save_group_q_cache(q_cache_path, q_by_group)

    competitor_year_group = _build_competitor_year_group_map(all_data)
    history, _, _ = _run_all_groups_with_transfer(
        groups,
        q_by_group,
        competitor_year_group,
        collect_history=True,
    )
    history = history.sort_values(["group", "competitor", "race_date", "race"], ascending=[True, True, True, True]).reset_index(drop=True)

    scope_mask = history.apply(
        lambda row: competitor_year_group.get((str(row["competitor"]), int(row["year"]))) == str(row["group"]),
        axis=1,
    )
    history = history.loc[scope_mask].copy().reset_index(drop=True)

    history["y_pred"] = history["b_t_hat"] + history["x_prior"]
    history["pred_beregnet_seconds"] = np.exp(history["y_pred"])
    history.loc[~np.isfinite(history["pred_beregnet_seconds"]), "pred_beregnet_seconds"] = np.nan
    history["y_pred_cf"] = history["y_pred"]
    observed_with_loo = (history["observed"] == True) & history["y_pred_loo"].notna()  # noqa: E712
    history.loc[observed_with_loo, "y_pred_cf"] = history.loc[observed_with_loo, "y_pred_loo"]
    history["pred_cf_beregnet_seconds"] = np.exp(history["y_pred_cf"])
    history.loc[~np.isfinite(history["pred_cf_beregnet_seconds"]), "pred_cf_beregnet_seconds"] = np.nan
    history["s_pred"] = history["p_prior"] + history["r_t"]
    history["sigma_pred"] = np.sqrt(history["s_pred"].clip(lower=EPS))
    history["pred_cf_expect_seconds"] = np.exp(history["y_pred_cf"] + 0.5 * np.square(history["sigma_pred"]))
    history["pred_cf_q25_seconds"] = np.exp(history["y_pred_cf"] - Z50 * history["sigma_pred"])
    history["pred_cf_q75_seconds"] = np.exp(history["y_pred_cf"] + Z50 * history["sigma_pred"])
    for col in ["pred_cf_expect_seconds", "pred_cf_q25_seconds", "pred_cf_q75_seconds"]:
        history.loc[~np.isfinite(history[col]), col] = np.nan

    history["x_obs"] = history["x_prior"] + history["innovation"]
    history.loc[history["observed"] != True, "x_obs"] = np.nan  # noqa: E712
    history["x_q25"] = history["x_prior"] - Z50 * np.sqrt(history["p_prior"].clip(lower=EPS))
    history["x_q75"] = history["x_prior"] + Z50 * np.sqrt(history["p_prior"].clip(lower=EPS))
    history.loc[~np.isfinite(history["x_obs"]), "x_obs"] = np.nan
    history.loc[~np.isfinite(history["x_q25"]), "x_q25"] = np.nan
    history.loc[~np.isfinite(history["x_q75"]), "x_q75"] = np.nan
    history["z_innovation"] = history["innovation"] / np.sqrt(history["s_pred"].clip(lower=EPS))
    history["error_seconds"] = history["beregnet_seconds"] - history["pred_cf_beregnet_seconds"]
    history["abs_error_seconds"] = history["error_seconds"].abs()

    observed_predictions = history[history["observed"] == True].copy()  # noqa: E712
    all_predictions = history.copy()

    latest = (
        history.sort_values(["group", "competitor", "race_date", "race"]) 
        .groupby(["group", "competitor"], as_index=False)
        .tail(1)
        .loc[:, ["group", "competitor", "race", "race_date", "x_post", "p_post", "global_q"]]
        .rename(columns={"x_post": "x_estimate_latest", "p_post": "p_variance_latest"})
        .reset_index(drop=True)
    )

    per_race = (
        history.groupby(["group", "race", "race_date"], as_index=False)
        .agg(
            b_t_hat=("b_t_hat", "first"),
            r_t=("r_t", "first"),
            boats_observed=("observed", "sum"),
            boats_total=("competitor", "count"),
        )
        .assign(race_num=lambda d: d["race"].map(_race_num))
        .sort_values(["group", "race_num", "race"])
        .reset_index(drop=True)
    )

    history_path = output_dir / "redress_2025_state_history.csv"
    obs_path = output_dir / "redress_2025_observed_predictions.csv"
    latest_path = output_dir / "redress_2025_latest_estimates.csv"
    race_path = output_dir / "redress_2025_race_effects.csv"
    q_diag_path = output_dir / "redress_2025_q_diagnostics.csv"
    q_diag = pd.concat(q_diag_frames, ignore_index=True) if q_diag_frames else pd.DataFrame()

    history.to_csv(history_path, index=False)
    observed_predictions.to_csv(obs_path, index=False)
    latest.to_csv(latest_path, index=False)
    per_race.to_csv(race_path, index=False)
    q_diag.to_csv(q_diag_path, index=False)

    active_2026_competitors = {
        competitor
        for (competitor, year), _group in competitor_year_group.items()
        if int(year) == int(PLOT_ACTIVE_YEAR)
    }

    plot_data_dir = output_dir / "boat_plot_data"
    manifest_path = _export_boat_plot_data(
        all_predictions,
        allowed_competitors=active_2026_competitors,
        output_dir=plot_data_dir,
    )
    latest_race_table_stor_bane1_r2_path = _export_latest_race_table(
        all_predictions,
        output_path=output_dir / "latest_race_2026_r2_stor_bane1_table.csv",
        year=int(PLOT_ACTIVE_YEAR),
        race_local="R2",
        group_filter="Stor Bane",
        series_filter="Stor bane 1",
    )
    latest_race_table_stor_bane2_r2_path = _export_latest_race_table(
        all_predictions,
        output_path=output_dir / "latest_race_2026_r2_stor_bane2_table.csv",
        year=int(PLOT_ACTIVE_YEAR),
        race_local="R2",
        group_filter="Stor Bane",
        series_filter="Stor bane 2",
    )
    latest_race_table_lille_bane1_r2_path = _export_latest_race_table(
        all_predictions,
        output_path=output_dir / "latest_race_2026_r2_lille_bane1_table.csv",
        year=int(PLOT_ACTIVE_YEAR),
        race_local="R2",
        group_filter="Lille Bane",
        series_filter="Lille bane 1",
    )
    latest_race_table_lille_bane2_r2_path = _export_latest_race_table(
        all_predictions,
        output_path=output_dir / "latest_race_2026_r2_lille_bane2_table.csv",
        year=int(PLOT_ACTIVE_YEAR),
        race_local="R2",
        group_filter="Lille Bane",
        series_filter="Lille bane 2",
    )
    ml_fit_example_paths = _export_ml_fit_example(history, output_dir=output_dir)

    if using_cached_q:
        print(f"Loaded group Q from cache: {q_cache_path}")
    else:
        print(f"Saved fitted group Q to cache: {q_cache_path}")

    for row in q_fit_rows:
        print(
            f"{row['group']}: initial Q={row['initial_q']:.3e}, "
            f"fitted Q={row['fitted_q']:.3e}, "
            f"1-step RMSE={row['q_objective_score']:.3f} over {row['q_obs']} observations "
            f"({row.get('q_source', 'unknown')})"
        )
    print(f"Wrote: {history_path}")
    print(f"Wrote: {obs_path}")
    print(f"Wrote: {latest_path}")
    print(f"Wrote: {race_path}")
    print(f"Wrote: {q_diag_path}")
    print(f"Wrote: {manifest_path}")
    print(f"Wrote: {latest_race_table_stor_bane1_r2_path}")
    print(f"Wrote: {latest_race_table_stor_bane2_r2_path}")
    print(f"Wrote: {latest_race_table_lille_bane1_r2_path}")
    print(f"Wrote: {latest_race_table_lille_bane2_r2_path}")
    for path in ml_fit_example_paths:
        print(f"Wrote: {path}")
    print("Note: This script now exports plot data only and does not generate LaTeX.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
