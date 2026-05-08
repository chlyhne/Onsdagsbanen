from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
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
TARGET_GROUP = "Stor Bane"
NON_OBS_STATUSES = {"DNS", "DNC", "DSQ", "DNF"}
Q_SEARCH_MIN = 1e-12
Q_SEARCH_MAX = 1e-2
P_CAP_FACTOR = 30.0


@dataclass
class BoatState:
    x: float
    p: float
    last_state_date: datetime | None


def _race_sort_key(label: str) -> int:
    return int(label[1:])


def _race_num(label: str) -> int:
    try:
        return int(str(label).replace("R", ""))
    except ValueError:
        return 0


def _latex_escape(text: object) -> str:
    value = str(text)
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    return value


def _format_seconds(value: float | int | None) -> str:
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        return ""
    total = int(round(float(value)))
    sign = "-" if total < 0 else ""
    total = abs(total)
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{sign}{hours}:{minutes:02d}:{seconds:02d}"


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
                combined.groupby(["year", "race", "competitor"], as_index=False)
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
    return groups, all_data


def _filter_groups(
    groups: list[dict[str, Any]],
    all_data: pd.DataFrame,
    target_group: str | None,
) -> tuple[list[dict[str, Any]], pd.DataFrame]:
    if not target_group:
        return groups, all_data

    filtered_groups = [group for group in groups if str(group.get("group", "")) == target_group]
    if all_data.empty:
        filtered_all = all_data.copy()
    else:
        filtered_all = all_data[all_data["group"] == target_group].copy()
    return filtered_groups, filtered_all


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


def _run_group_filter(
    group: dict[str, Any],
    global_q: float,
    *,
    collect_history: bool = True,
) -> tuple[pd.DataFrame, float, int]:
    combined: pd.DataFrame = group["combined"].copy()
    selected_races: list[str] = list(group["selected_races"])
    race_dates: dict[str, datetime] = dict(group["race_dates"])
    group_label = str(group["group"])

    competitors = sorted(combined["competitor"].dropna().astype(str).unique().tolist())
    p_cap = float(P_CAP_FACTOR * global_q)
    states: dict[str, BoatState] = {
        competitor: BoatState(x=0.0, p=p_cap, last_state_date=None)
        for competitor in competitors
    }

    history_rows: list[dict[str, Any]] = []
    nll_sum = 0.0
    observed_count = 0

    for race_label in selected_races:
        race_date = race_dates.get(race_label)
        if race_date is None:
            continue

        race_rows = combined[combined["race"] == race_label].copy()
        race_rows["status_upper"] = race_rows["race_status_code"].fillna("").astype(str).str.upper().str.strip()

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
        observed["competitor"] = observed["competitor"].astype(str)
        observed["y"] = np.log(observed["beregnet_seconds"].astype(float).clip(lower=EPS))

        if observed.empty:
            a_hat = float("nan")
            r_t = float("nan")
            innovation_by_competitor: dict[str, float] = {}
            gain_by_competitor: dict[str, float] = {}
            y_pred_loo_by_competitor: dict[str, float] = {}
        else:
            a_samples = observed.apply(
                lambda row: float(row["y"] + prior_by_competitor[row["competitor"]][0]),
                axis=1,
            ).to_numpy(dtype=float)
            a_hat = float(np.median(a_samples))
            a_by_competitor = {
                str(row["competitor"]): float(row["y"] + prior_by_competitor[str(row["competitor"])][0])
                for _, row in observed.iterrows()
            }
            y_pred_loo_by_competitor = {}
            observed_competitors_list = list(a_by_competitor.keys())
            for competitor in observed_competitors_list:
                x_prior, _, _ = prior_by_competitor[competitor]
                others = [a_by_competitor[c] for c in observed_competitors_list if c != competitor]
                a_hat_loo = float(np.median(np.array(others, dtype=float))) if others else a_hat
                y_pred_loo_by_competitor[competitor] = float(a_hat_loo - x_prior)

            innovation_by_competitor = {}
            innovations: list[float] = []
            for _, row in observed.iterrows():
                competitor = str(row["competitor"])
                y_val = float(row["y"])
                x_prior, p_prior, _ = prior_by_competitor[competitor]
                y_prior = a_hat - x_prior
                nu = y_val - y_prior
                innovation_by_competitor[competitor] = nu
                innovations.append(nu)

            innovation_var = float(np.var(np.array(innovations, dtype=float), ddof=0)) if innovations else 0.0
            # Requested behavior: use race variance directly as R_t.
            # This may double-count state uncertainty, but keeps update strength intuitive.
            r_t = max(EPS, innovation_var)

            gain_by_competitor = {}
            for _, row in observed.iterrows():
                competitor = str(row["competitor"])
                _, p_prior, _ = prior_by_competitor[competitor]
                gain_by_competitor[competitor] = float(p_prior / (p_prior + r_t))

            for _, row in observed.iterrows():
                competitor = str(row["competitor"])
                nu = float(innovation_by_competitor[competitor])
                _, p_prior, _ = prior_by_competitor[competitor]
                s_val = max(EPS, float(p_prior + r_t))
                nll_sum += 0.5 * (math.log(2.0 * math.pi * s_val) + (nu * nu) / s_val)
                observed_count += 1

        observed_competitors = set(observed["competitor"].astype(str).tolist()) if not observed.empty else set()

        for competitor in competitors:
            x_prior, p_prior, delta_days = prior_by_competitor[competitor]
            status_row = race_rows[race_rows["competitor"].astype(str) == competitor]
            status = ""
            sailed_seconds: float | None = None
            beregnet_seconds: float | None = None
            if not status_row.empty:
                status = str(status_row.iloc[0].get("race_status_code") or "").strip()
                raw_sailed = status_row.iloc[0].get("sailed_seconds")
                raw_beregnet = status_row.iloc[0].get("beregnet_seconds")
                sailed_seconds = float(raw_sailed) if pd.notna(raw_sailed) else None
                beregnet_seconds = float(raw_beregnet) if pd.notna(raw_beregnet) else None
            else:
                status = "IKKE MED"

            if competitor in observed_competitors and competitor in innovation_by_competitor and not np.isnan(r_t):
                nu = float(innovation_by_competitor[competitor])
                k = float(gain_by_competitor[competitor])
                # Observation model is y = a - x + v, so H = -1 and update uses minus sign.
                x_post = float(x_prior - k * nu)
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
                        "race_date": race_date.date().isoformat(),
                        "year": int(race_date.year),
                        "competitor": competitor,
                        "observed": observed_flag,
                        "status": status,
                        "sailed_seconds": sailed_seconds,
                        "beregnet_seconds": beregnet_seconds,
                        "a_t_hat": a_hat,
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
    return history, nll_sum, observed_count


def _q_objective(groups: list[dict[str, Any]], q_value: float) -> tuple[float, int]:
    frames: list[pd.DataFrame] = []
    for group in groups:
        history, _, _ = _run_group_filter(group, q_value, collect_history=True)
        if not history.empty:
            frames.append(history)
    if not frames:
        return float("inf"), 0
    history = pd.concat(frames, ignore_index=True)
    obs = history[history["observed"] == True].copy()  # noqa: E712
    if obs.empty:
        return float("inf"), 0

    obs["pred_beregnet_seconds_loo"] = np.exp(obs["y_pred_loo"])
    obs.loc[~np.isfinite(obs["pred_beregnet_seconds_loo"]), "pred_beregnet_seconds_loo"] = np.nan
    obs = obs.dropna(subset=["beregnet_seconds", "pred_beregnet_seconds_loo"])
    if obs.empty:
        return float("inf"), 0

    errors = obs["beregnet_seconds"].astype(float) - obs["pred_beregnet_seconds_loo"].astype(float)
    rmse = float(np.sqrt(np.mean(np.square(errors))))
    return rmse, int(len(obs))


def _q_diagnostics(groups: list[dict[str, Any]], q_values: np.ndarray) -> pd.DataFrame:
    rows: list[dict[str, float | int]] = []
    for q_value in q_values:
        score, obs = _q_objective(groups, float(q_value))
        rows.append(
            {
                "q_value": float(q_value),
                "one_step_rmse_seconds": float(score),
                "observations": int(obs),
            }
        )
    return pd.DataFrame(rows).sort_values("q_value").reset_index(drop=True)


def _fit_global_q(groups: list[dict[str, Any]], initial_q: float) -> tuple[float, float, int]:
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
        score, obs = _q_objective(groups, q_value)
        if score < best_score:
            best_q = q_value
            best_score = score
            best_obs = obs

    for _ in range(2):
        local = [float(best_q * factor) for factor in np.logspace(-0.7, 0.7, 21)]
        local = sorted(set(max(search_min, min(Q_SEARCH_MAX, value)) for value in local))
        for q_value in local:
            score, obs = _q_objective(groups, q_value)
            if score < best_score:
                best_q = q_value
                best_score = score
                best_obs = obs

    return best_q, best_score, best_obs


def _coords_scatter(obs: pd.DataFrame) -> str:
    if obs.empty:
        return ""
    rows = []
    for _, row in obs.iterrows():
        x = row.get("beregnet_seconds")
        y = row.get("pred_cf_beregnet_seconds")
        if pd.isna(x) or pd.isna(y):
            continue
        rows.append(f"({float(x):.3f},{float(y):.3f})")
    return " ".join(rows)


def _coords_line(frame: pd.DataFrame, y_col: str) -> str:
    rows = []
    for _, row in frame.iterrows():
        x = row.get("race_num")
        y = row.get(y_col)
        if pd.isna(x) or pd.isna(y):
            continue
        rows.append(f"({int(x)},{float(y):.6f})")
    return " ".join(rows)


def _boat_prediction_plots_latex(frame: pd.DataFrame) -> str:
    if frame.empty:
        return ""

    work = frame.copy()
    work["race_num"] = work["race"].map(_race_num)
    work = work.dropna(subset=["race_num"])
    if work.empty:
        return ""

    global_q_series = work["global_q"].dropna().astype(float)
    if global_q_series.empty:
        return ""
    global_q_value = float(global_q_series.iloc[0])
    prior_sigma = math.sqrt(max(EPS, P_CAP_FACTOR * global_q_value))
    prior_mean = 100.0 * (math.exp(0.5 * prior_sigma * prior_sigma) - 1.0)
    prior_q25 = 100.0 * (math.exp(-Z50 * prior_sigma) - 1.0)
    prior_q75 = 100.0 * (math.exp(+Z50 * prior_sigma) - 1.0)

    x_max = int(work["race_num"].max())
    y_values: list[float] = [prior_q25, prior_q75, prior_mean]
    for col in ["pred_cf_expect_pct_baseline", "pred_cf_q25_pct_baseline", "pred_cf_q75_pct_baseline", "obs_pct_baseline"]:
        values = work[col].dropna().astype(float).tolist()
        y_values.extend(values)

    if y_values:
        y_min = min(y_values)
        y_max = max(y_values)
    else:
        y_min = -1.0
        y_max = 1.0
    span = y_max - y_min
    pad = 0.06 * span if span > 0 else 1.0
    y_min -= pad
    y_max += pad

    sections: list[str] = []
    competitors = sorted(work["competitor"].dropna().astype(str).unique().tolist())
    if "year" in work.columns and "status" in work.columns:
        latest_year = int(work["year"].dropna().astype(int).max())
        active_latest_year = set(
            work[
                (work["year"].astype(int) == latest_year)
                & (work["status"].fillna("").astype(str).str.strip().str.upper() != "IKKE MED")
            ]["competitor"].dropna().astype(str).tolist()
        )
        competitors = [name for name in competitors if name in active_latest_year]
    for idx, competitor in enumerate(competitors):
        c = work[work["competitor"].astype(str) == competitor].sort_values(["race_num", "race_date", "race"])
        mean_coords: list[str] = [f"(0,{prior_mean:.6f})"]
        q25_coords: list[str] = [f"(0,{prior_q25:.6f})"]
        q75_coords: list[str] = [f"(0,{prior_q75:.6f})"]
        for _, row in c.iterrows():
            y_mean = row.get("pred_cf_expect_pct_baseline")
            y_q25 = row.get("pred_cf_q25_pct_baseline")
            y_q75 = row.get("pred_cf_q75_pct_baseline")
            if pd.isna(y_mean) or pd.isna(y_q25) or pd.isna(y_q75):
                continue
            x_race = int(row["race_num"])
            mean_coords.append(f"({x_race},{float(y_mean):.6f})")
            q25_coords.append(f"({x_race},{float(y_q25):.6f})")
            q75_coords.append(f"({x_race},{float(y_q75):.6f})")
        if not mean_coords:
            continue

        obs_coords = []
        obs_rows = c[(c["observed"] == True) & c["beregnet_seconds"].notna()]  # noqa: E712
        for _, row in obs_rows.iterrows():
            y_obs = row.get("obs_pct_baseline")
            if pd.isna(y_obs):
                continue
            obs_coords.append(f"({int(row['race_num'])},{float(y_obs):.6f})")

        cname = f"b{idx}"
        obs_plot = ""
        if obs_coords:
            obs_plot = (
                "\\addplot[only marks, mark=*, mark size=1.4pt, opacity=0.95, color=gray!70!black] "
                f"coordinates {{{' '.join(obs_coords)}}};\n"
                "\\addlegendentry{Målt}"
            )
        section = f"""
\\subsubsection*{{{_latex_escape(competitor)}}}
\\begin{{tikzpicture}}
\\begin{{axis}}[
width=0.92\\textwidth,
height=5.6cm,
xlabel=Race,
ylabel=\\% afvigelse fra race-baseline,
grid=major,
xmin=0,
xmax={x_max},
ymin={y_min:.6f},
ymax={y_max:.6f},
xtick distance=1,
legend pos=north west,
]
\\addplot[name path={cname}low, draw=none] coordinates {{{' '.join(q25_coords)}}};
\\addplot[name path={cname}high, draw=none] coordinates {{{' '.join(q75_coords)}}};
\\addplot[fill=gray!35, fill opacity=0.45, draw=none] fill between[of={cname}high and {cname}low];
\\addplot[solid, mark=none, line width=1.0pt, color=black] coordinates {{{' '.join(mean_coords)}}};
\\addlegendentry{{1-step forventning}}
{obs_plot}
\\end{{axis}}
\\end{{tikzpicture}}
"""
        sections.append(section)

    return "\n".join(sections)


def _race_tables_latex(obs: pd.DataFrame) -> str:
    if obs.empty:
        return "Ingen observerede rækker."

    cols = [
        "race",
        "race_date",
        "competitor",
        "status",
        "observed",
        "beregnet_seconds",
        "pred_cf_beregnet_seconds",
        "x_prior",
        "r_t",
        "p_prior",
        "kalman_gain",
        "error_seconds",
    ]
    frame = obs.loc[:, cols].copy()
    frame["race_num"] = frame["race"].map(_race_num)
    frame = frame.sort_values(["race_num", "race", "competitor", "race_date"]).reset_index(drop=True)

    sections: list[str] = []
    for race_label, race_frame in frame.groupby("race", sort=False):
        race_frame = race_frame.copy()
        race_frame["_measured_sort"] = race_frame["beregnet_seconds"].astype(float)
        race_frame["_measured_sort"] = race_frame["_measured_sort"].where(race_frame["_measured_sort"].notna(), float("inf"))
        race_frame = race_frame.sort_values(["_measured_sort", "competitor", "race_date"]).reset_index(drop=True)
        race_dates = sorted({str(v) for v in race_frame["race_date"].dropna().astype(str).tolist() if str(v).strip()})
        race_date_text = race_dates[0] if len(race_dates) == 1 else ", ".join(race_dates[:2]) + ("..." if len(race_dates) > 2 else "")

        lines: list[str] = []
        for _, row in race_frame.iterrows():
            observed_flag = bool(row.get("observed", False))
            measured = _format_seconds(row["beregnet_seconds"]) if (observed_flag and pd.notna(row["beregnet_seconds"])) else ""
            predicted = _format_seconds(row["pred_cf_beregnet_seconds"]) if pd.notna(row["pred_cf_beregnet_seconds"]) else ""
            status_text = str(row["status"]).strip() if pd.notna(row["status"]) else ""
            parts = [
                _latex_escape(row["competitor"]),
                _latex_escape(status_text),
                _latex_escape(measured),
                _latex_escape(predicted),
                f"{float(row['x_prior']):.4f}" if pd.notna(row["x_prior"]) else "",
                f"{float(row['r_t']):.5f}" if pd.notna(row["r_t"]) else "",
                f"{float(row['p_prior']):.5f}" if pd.notna(row["p_prior"]) else "",
                f"{float(row['kalman_gain']):.4f}" if (observed_flag and pd.notna(row["kalman_gain"])) else "",
                f"{float(row['error_seconds']):.1f}" if (observed_flag and pd.notna(row["error_seconds"])) else "",
            ]
            lines.append(" & ".join(parts) + r" \\")

        table_block = f"""
\\subsubsection*{{Race {_latex_escape(race_label)} ({_latex_escape(race_date_text)})}}
\\begingroup
\\scriptsize
\\setlength{{\\tabcolsep}}{{3.5pt}}
\\begin{{tabular}}{{llrrrrrrr}}
\\toprule
Båd & Status & Målt tid & Pred tid & x\\_prior & R\\_t & P\\_prior & Gain & Fejl(s) \\\\
\\midrule
{chr(10).join(lines)}
\\bottomrule
\\end{{tabular}}
\\endgroup
"""
        sections.append(table_block)

    return "\n".join(sections)


def _build_latex_report(
    output_path: Path,
    *,
    initial_q: float,
    fitted_q: float,
    q_objective_score: float,
    q_obs: int,
    observed_predictions: pd.DataFrame,
    race_metrics: pd.DataFrame,
) -> None:
    obs = observed_predictions.copy()
    obs_eval = obs[obs["observed"] == True].copy()  # noqa: E712
    if obs_eval.empty:
        obs_eval = obs.copy()
    active_groups = sorted(obs["group"].dropna().astype(str).unique().tolist()) if not obs.empty else []
    group_text = ", ".join(_latex_escape(name) for name in active_groups) if active_groups else "ingen"
    years = sorted(obs["year"].dropna().astype(int).unique().tolist()) if ("year" in obs.columns and not obs.empty) else []
    year_span_text = f" {years[0]}--{years[-1]}" if years else ""

    mae = float(obs_eval["abs_error_seconds"].mean()) if not obs_eval.empty else float("nan")
    rmse = float(np.sqrt(np.mean(np.square(obs_eval["error_seconds"])))) if not obs_eval.empty else float("nan")

    by_group = (
        obs_eval.groupby("group", as_index=False)
        .agg(
            n=("competitor", "count"),
            mae_seconds=("abs_error_seconds", "mean"),
            rmse_seconds=("error_seconds", lambda s: float(np.sqrt(np.mean(np.square(s))))),
        )
        .sort_values("group")
        .reset_index(drop=True)
    )

    scatter_coords = _coords_scatter(obs)
    scatter_axis_bounds = ""
    scatter_diagonal_coords = ""
    scatter_frame = obs.loc[:, ["beregnet_seconds", "pred_cf_beregnet_seconds"]].dropna()
    if not scatter_frame.empty:
        lo = float(min(scatter_frame["beregnet_seconds"].min(), scatter_frame["pred_cf_beregnet_seconds"].min()))
        hi = float(max(scatter_frame["beregnet_seconds"].max(), scatter_frame["pred_cf_beregnet_seconds"].max()))
        if math.isfinite(lo) and math.isfinite(hi):
            span = hi - lo
            pad = 0.02 * (span if span > 0 else max(abs(hi), 1.0))
            lo -= pad
            hi += pad
            scatter_axis_bounds = f"xmin={lo:.3f}, xmax={hi:.3f}, ymin={lo:.3f}, ymax={hi:.3f},"
            scatter_diagonal_coords = f"({lo:.3f},{lo:.3f}) ({hi:.3f},{hi:.3f})"
    boat_prediction_plots = _boat_prediction_plots_latex(obs)

    line_blocks: list[str] = []
    for group_name in sorted(race_metrics["group"].unique().tolist() if not race_metrics.empty else []):
        g = race_metrics[race_metrics["group"] == group_name].sort_values("race_num").copy()
        mae_coords = _coords_line(g, "mae_seconds")
        rt_coords = _coords_line(g, "r_t")
        line_blocks.append(
            f"""
\\subsection*{{{_latex_escape(group_name)}}}
\\begin{{tikzpicture}}
\\begin{{axis}}[
width=0.9\\textwidth,
height=5cm,
xlabel=Race,
ylabel=MAE (sekunder),
grid=major,
legend pos=north west,
]
\\addplot[smooth,mark=*] coordinates {{{mae_coords}}};
\\addlegendentry{{MAE pr. race}}
\\end{{axis}}
\\end{{tikzpicture}}

\\begin{{tikzpicture}}
\\begin{{axis}}[
width=0.9\\textwidth,
height=5cm,
xlabel=Race,
ylabel=$R_t$,
grid=major,
legend pos=north west,
]
\\addplot[smooth,mark=*] coordinates {{{rt_coords}}};
\\addlegendentry{{$R_t$ pr. race}}
\\end{{axis}}
\\end{{tikzpicture}}
"""
        )

    group_rows = "\n".join(
        " & ".join(
            [
                _latex_escape(row["group"]),
                str(int(row["n"])),
                f"{float(row['mae_seconds']):.2f}",
                f"{float(row['rmse_seconds']):.2f}",
            ]
        ) + r" \\"
        for _, row in by_group.iterrows()
    )

    race_tables = _race_tables_latex(observed_predictions)
    boundary_text = ""
    if fitted_q <= (Q_SEARCH_MIN * 1.0000001):
        boundary_text = " (optimum ligger ved nedre søgegrænse)."

    tex = f"""\\section{{Dataanalyse{year_span_text}}}
Dette afsnit er automatisk genereret fra samme scraper og parser-kæde som resultatberegningen.\\\\
Dataudsnit i denne rapport: {group_text}.

\\subsection*{{Modelparametre}}
Initial Q (median-estimat): {initial_q:.10f}\\\\
Fittet Q (1-step fit): {fitted_q:.3e}\\\\
Q-søgning: [{Q_SEARCH_MIN:.1e}, {Q_SEARCH_MAX:.1e}]{boundary_text}\\\\
1-step RMSE-mål (sejlende både, leave-one-out på $a_t$): {q_objective_score:.2f} sekunder over {q_obs} observationer\\\\
MAE: {mae:.2f} sekunder\\\\
RMSE: {rmse:.2f} sekunder

\\subsection*{{Målt vs predikteret beregnet tid}}
\\begin{{center}}
\\begin{{tikzpicture}}
\\begin{{axis}}[
width=0.9\\textwidth,
height=8cm,
xlabel=Målt beregnet tid (sekunder),
ylabel={{Predikteret beregnet tid (LOO, sekunder)}},
grid=major,
axis equal image,
{scatter_axis_bounds}
]
\\addplot[only marks, mark=*, mark size=1.4pt, opacity=0.6] coordinates {{{scatter_coords}}};
\\addplot[densely dashed, black, line width=0.8pt] coordinates {{{scatter_diagonal_coords}}};
\\end{{axis}}
\\end{{tikzpicture}}
\\end{{center}}

\\subsection*{{1-step prediktion pr. båd}}
Skygge er centralt 50\\%-interval (Q25--Q75) fra den lineære model, transformeret til \\% afvigelse i tid via lognormal-kortlægning.
Linjen er forventningsværdien, og punkter er målte tider.
{boat_prediction_plots}

\\subsection*{{Gruppemetrikker}}
\\begin{{tabular}}{{lrrr}}
\\toprule
Gruppe & N & MAE (sek) & RMSE (sek) \\\\
\\midrule
{group_rows}
\\bottomrule
\\end{{tabular}}

\\subsection*{{MAE og $R_t$ pr. race}}
{''.join(line_blocks)}

\\subsection*{{Detaljerede tabeller pr. race}}
Kolonner: status, målt/predikteret beregnet tid, predikteret x (x\\_prior), race-støj R\\_t, prior-varians, gain og fejl i sekunder.
Ikke-observerede både (inkl. DNS/DNF/DSQ/DNC og IKKE MED) vises med deres predikterede tid.
{race_tables}
"""

    output_path.write_text(tex, encoding="utf-8")


def main() -> int:
    output_dir = Path("analysis")
    output_dir.mkdir(parents=True, exist_ok=True)

    groups, all_data = _build_group_data()
    groups, all_data = _filter_groups(groups, all_data, TARGET_GROUP)
    if not groups:
        raise RuntimeError(f"No group data could be built for target group: {TARGET_GROUP}.")

    initial_q = _estimate_global_q(all_data)
    global_q, q_objective_score, q_obs = _fit_global_q(groups, initial_q=initial_q)

    histories: list[pd.DataFrame] = []
    for group in groups:
        history_frame, _, _ = _run_group_filter(group, global_q, collect_history=True)
        histories.append(history_frame)

    history = pd.concat(histories, ignore_index=True)
    history = history.sort_values(["group", "competitor", "race_date", "race"], ascending=[True, True, True, True]).reset_index(drop=True)

    history["y_pred"] = history["a_t_hat"] - history["x_prior"]
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

    history["baseline_beregnet_seconds"] = np.exp(history["a_t_hat"])
    history.loc[~np.isfinite(history["baseline_beregnet_seconds"]), "baseline_beregnet_seconds"] = np.nan
    history["pred_cf_rel_baseline_seconds"] = history["pred_cf_beregnet_seconds"] - history["baseline_beregnet_seconds"]
    history["obs_rel_baseline_seconds"] = history["beregnet_seconds"] - history["baseline_beregnet_seconds"]
    history["pred_cf_expect_pct_baseline"] = 100.0 * (
        history["pred_cf_expect_seconds"] / history["baseline_beregnet_seconds"] - 1.0
    )
    history["pred_cf_q25_pct_baseline"] = 100.0 * (
        history["pred_cf_q25_seconds"] / history["baseline_beregnet_seconds"] - 1.0
    )
    history["pred_cf_q75_pct_baseline"] = 100.0 * (
        history["pred_cf_q75_seconds"] / history["baseline_beregnet_seconds"] - 1.0
    )
    history["obs_pct_baseline"] = 100.0 * (history["beregnet_seconds"] / history["baseline_beregnet_seconds"] - 1.0)
    for col in [
        "pred_cf_expect_pct_baseline",
        "pred_cf_q25_pct_baseline",
        "pred_cf_q75_pct_baseline",
        "obs_pct_baseline",
        "pred_cf_rel_baseline_seconds",
        "obs_rel_baseline_seconds",
    ]:
        history.loc[~np.isfinite(history[col]), col] = np.nan
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
            a_t_hat=("a_t_hat", "first"),
            r_t=("r_t", "first"),
            boats_observed=("observed", "sum"),
            boats_total=("competitor", "count"),
        )
        .assign(race_num=lambda d: d["race"].map(_race_num))
        .sort_values(["group", "race_num", "race"])
        .reset_index(drop=True)
    )

    race_metrics = (
        observed_predictions.groupby(["group", "race", "race_date"], as_index=False)
        .agg(
            n=("competitor", "count"),
            mae_seconds=("abs_error_seconds", "mean"),
            rmse_seconds=("error_seconds", lambda s: float(np.sqrt(np.mean(np.square(s))))),
        )
        .assign(race_num=lambda d: d["race"].map(_race_num))
        .merge(per_race[["group", "race", "r_t"]], on=["group", "race"], how="left")
        .sort_values(["group", "race_num", "race"])
        .reset_index(drop=True)
    )

    history_path = output_dir / "redress_2025_state_history.csv"
    obs_path = output_dir / "redress_2025_observed_predictions.csv"
    latest_path = output_dir / "redress_2025_latest_estimates.csv"
    race_path = output_dir / "redress_2025_race_effects.csv"
    q_diag_path = output_dir / "redress_2025_q_diagnostics.csv"
    report_tex_path = Path("../redress-algorithm-paper/sections/D-2025-dataanalyse.tex")
    q_grid = np.logspace(math.log10(Q_SEARCH_MIN), math.log10(Q_SEARCH_MAX), 61, dtype=float)
    q_diag = _q_diagnostics(groups, q_grid)

    history.to_csv(history_path, index=False)
    observed_predictions.to_csv(obs_path, index=False)
    latest.to_csv(latest_path, index=False)
    per_race.to_csv(race_path, index=False)
    q_diag.to_csv(q_diag_path, index=False)

    _build_latex_report(
        report_tex_path,
        initial_q=initial_q,
        fitted_q=global_q,
        q_objective_score=q_objective_score,
        q_obs=q_obs,
        observed_predictions=all_predictions,
        race_metrics=race_metrics,
    )

    print(f"Initial Q (median-increment estimate): {initial_q:.3e}")
    print(f"Fitted Q (1-step predictive RMSE fit): {global_q:.3e}")
    print(f"1-step RMSE objective: {q_objective_score:.3f} across {q_obs} observations")
    print(f"Wrote: {history_path}")
    print(f"Wrote: {obs_path}")
    print(f"Wrote: {latest_path}")
    print(f"Wrote: {race_path}")
    print(f"Wrote: {q_diag_path}")
    print(f"Wrote: {report_tex_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
