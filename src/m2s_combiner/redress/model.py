from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .common import race_num
from .constants import EPS
from .constants import GROUP_Q_CACHE_FILENAME
from .constants import NON_OBS_STATUSES
from .constants import P_CAP_FACTOR
from .constants import Q_OBJECTIVE_CHOICES
from .constants import Q_SEARCH_MAX
from .constants import Q_SEARCH_MIN


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


def estimate_global_q(all_data: pd.DataFrame) -> float:
    if all_data.empty:
        return 1e-5

    working = all_data.copy()
    status_series = working["race_status_code"] if "race_status_code" in working.columns else pd.Series("", index=working.index, dtype="object")
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
    return max(float(np.median(np.array(q_samples, dtype=float))), 1e-8)


def load_group_q_cache(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"Could not read Q cache file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid Q cache JSON in {path}") from exc

    if isinstance(payload, dict) and isinstance(payload.get("group_q"), dict):
        source = payload["group_q"]
    elif isinstance(payload, dict):
        source = payload
    else:
        raise ValueError(f"Invalid Q cache payload in {path}")

    q_map: dict[str, float] = {}
    for key, value in source.items():
        q_val = float(value)
        if np.isfinite(q_val) and q_val > 0.0:
            q_map[str(key)] = q_val
    return q_map


def save_group_q_cache(path: Path, q_by_group: dict[str, float]) -> None:
    payload = {
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "group_q": {str(k): float(v) for k, v in sorted(q_by_group.items())},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def q_cache_path_for_objective(output_dir: Path, objective: str) -> Path:
    suffix = str(objective).strip().lower()
    stem = Path(GROUP_Q_CACHE_FILENAME).stem
    return output_dir / f"{stem}_{suffix}.json"


def prepare_group_runtime(group: dict[str, Any]) -> dict[str, Any]:
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
        race_lookup_by_label[label] = race_frame.drop_duplicates(subset=["competitor"], keep="first").set_index("competitor", drop=False).to_dict("index")

    runtime = {
        "combined": combined,
        "competitors": competitors,
        "race_rows_by_label": race_rows_by_label,
        "race_lookup_by_label": race_lookup_by_label,
    }
    group["_runtime"] = runtime
    return runtime


def mu_hat_given_measurement_variance(measurement_variance: float, centered_values: np.ndarray, p_priors: np.ndarray) -> float:
    s_values = np.maximum(EPS, p_priors + measurement_variance)
    weights = 1.0 / s_values
    return float(np.sum(weights * centered_values) / np.sum(weights))


def measurement_nll(measurement_variance: float, centered_values: np.ndarray, p_priors: np.ndarray) -> float:
    s_values = np.maximum(EPS, p_priors + measurement_variance)
    mu_hat = mu_hat_given_measurement_variance(measurement_variance, centered_values, p_priors)
    return 0.5 * float(np.sum(np.log(2.0 * math.pi * s_values) + np.square(centered_values - mu_hat) / s_values))


def fit_measurement_variance(centered_values: np.ndarray, p_priors: np.ndarray) -> float:
    if centered_values.size == 0:
        return float("nan")

    lower = EPS
    base_scale = max(EPS, float(np.var(centered_values, ddof=0)), float(np.mean(np.square(centered_values))), float(np.mean(p_priors)))
    upper = max(lower * 16.0, base_scale)
    upper_nll = measurement_nll(upper, centered_values, p_priors)

    for _ in range(8):
        candidate_upper = upper * 4.0
        candidate_nll = measurement_nll(candidate_upper, centered_values, p_priors)
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
    fc = measurement_nll(c, centered_values, p_priors)
    fd = measurement_nll(d, centered_values, p_priors)

    for _ in range(32):
        if fc <= fd:
            right = d
            d = c
            fd = fc
            c = right - (right - left) / phi
            fc = measurement_nll(c, centered_values, p_priors)
        else:
            left = c
            c = d
            fc = fd
            d = left + (right - left) / phi
            fd = measurement_nll(d, centered_values, p_priors)

    return max(EPS, float(0.5 * (left + right)))


def fit_day_parameters(centered_values: np.ndarray, p_priors: np.ndarray) -> tuple[float, float, float]:
    if centered_values.size == 0:
        return float("nan"), float("nan"), 0.0

    r_t = fit_measurement_variance(centered_values, p_priors)
    mu_hat = mu_hat_given_measurement_variance(r_t, centered_values, p_priors)
    nll_sum = measurement_nll(r_t, centered_values, p_priors)
    return mu_hat, r_t, nll_sum


def compute_observation_step(observed: pd.DataFrame, prior_by_competitor: dict[str, tuple[float, float, int]]) -> dict[str, Any]:
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
    b_hat, r_t, nll_sum = fit_day_parameters(centered_values, p_priors)

    y_pred_loo_by_competitor: dict[str, float] = {}
    if len(competitors) == 1:
        y_pred_loo_by_competitor[competitors[0]] = float(b_hat + x_priors[0])
    else:
        for idx, competitor in enumerate(competitors):
            centered_values_loo = np.delete(centered_values, idx)
            p_priors_loo = np.delete(p_priors, idx)
            b_hat_loo, _, _ = fit_day_parameters(centered_values_loo, p_priors_loo)
            y_pred_loo_by_competitor[competitor] = float(b_hat_loo + x_priors[idx])

    innovations = centered_values - b_hat
    gains = p_priors / (p_priors + r_t)
    innovation_by_competitor = {competitor: float(innovation) for competitor, innovation in zip(competitors, innovations)}
    gain_by_competitor = {competitor: float(gain) for competitor, gain in zip(competitors, gains)}

    loo_sq_error_sum = 0.0
    loo_error_count = 0
    for competitor, observed_seconds in zip(competitors, beregnet_seconds):
        pred_seconds = float(np.exp(y_pred_loo_by_competitor[competitor]))
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


def run_group_filter(group: dict[str, Any], global_q: float, *, collect_history: bool = True) -> GroupFilterResult:
    runtime = prepare_group_runtime(group)
    selected_races: list[str] = list(group["selected_races"])
    race_dates: dict[str, datetime] = dict(group["race_dates"])
    group_label = str(group["group"])

    competitors: list[str] = runtime["competitors"]
    race_rows_by_label: dict[str, pd.DataFrame] = runtime["race_rows_by_label"]
    race_lookup_by_label: dict[str, dict[str, dict[str, Any]]] = runtime["race_lookup_by_label"]
    p_cap = float(P_CAP_FACTOR * global_q)
    states = {competitor: BoatState(x=0.0, p=p_cap, last_state_date=None) for competitor in competitors}

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
            delta_days = 0 if state.last_state_date is None else max(0, (race_date - state.last_state_date).days)
            prior_by_competitor[competitor] = (state.x, min(p_cap, state.p + delta_days * global_q), delta_days)

        obs_mask = race_rows["beregnet_seconds"].notna() & (~race_rows["status_upper"].isin(NON_OBS_STATUSES))
        observed = race_rows.loc[obs_mask, ["competitor", "beregnet_seconds"]].copy()
        observed_step = compute_observation_step(observed, prior_by_competitor)
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
            x_prior, p_prior, delta_t_days = prior_by_competitor[competitor]
            status = ""
            sailed_seconds: float | None = None
            beregnet_seconds: float | None = None
            hdcp_value: float | None = None
            length_nm_value: float | None = None
            race_local_value = race_label
            series_value = ""
            if collect_history:
                status_row = race_lookup.get(competitor)
                if status_row is None:
                    status = "IKKE MED"
                else:
                    status = str(status_row.get("race_status_code") or "").strip()
                    raw_sailed = status_row.get("sailed_seconds")
                    raw_beregnet = status_row.get("beregnet_seconds")
                    raw_hdcp = status_row.get("hdcp")
                    raw_length_nm = status_row.get("length_nm")
                    raw_race_local = status_row.get("race_local")
                    raw_series = status_row.get("series")
                    sailed_seconds = float(raw_sailed) if pd.notna(raw_sailed) else None
                    beregnet_seconds = float(raw_beregnet) if pd.notna(raw_beregnet) else None
                    hdcp_value = float(raw_hdcp) if pd.notna(raw_hdcp) else None
                    length_nm_value = float(raw_length_nm) if pd.notna(raw_length_nm) else None
                    if pd.notna(raw_race_local):
                        race_local_value = str(raw_race_local).strip() or race_label
                    if pd.notna(raw_series):
                        series_value = str(raw_series).strip()

            if competitor in observed_competitors and competitor in innovation_by_competitor and not np.isnan(r_t):
                innovation = float(innovation_by_competitor[competitor])
                gain = float(gain_by_competitor[competitor])
                x_post = float(x_prior + gain * innovation)
                p_post = float((1.0 - gain) * p_prior)
                observed_flag = True
                y_pred_loo = float(y_pred_loo_by_competitor.get(competitor, float("nan")))
            else:
                innovation = float("nan")
                gain = float("nan")
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
                        "hdcp": hdcp_value,
                        "length_nm": length_nm_value,
                        "b_t_hat": b_hat,
                        "delta_t_days": delta_t_days,
                        "global_q": global_q,
                        "x_prior": x_prior,
                        "p_prior": p_prior,
                        "r_t": r_t,
                        "innovation": innovation,
                        "kalman_gain": gain,
                        "y_pred_loo": y_pred_loo,
                        "x_post": x_post,
                        "p_post": p_post,
                    }
                )

    history = pd.DataFrame(history_rows) if collect_history else pd.DataFrame()
    return GroupFilterResult(history=history, nll_sum=nll_sum, observed_count=observed_count, loo_sq_error_sum=loo_sq_error_sum, loo_error_count=loo_error_count)


def run_all_groups_with_transfer(groups: list[dict[str, Any]], q_by_group: dict[str, float], competitor_year_group: dict[tuple[str, int], str], *, collect_history: bool = True) -> tuple[pd.DataFrame, dict[str, float], dict[str, int]]:
    if not groups:
        return pd.DataFrame(), {}, {}

    groups_by_name = {str(group["group"]): group for group in groups}
    p_cap_by_group = {group_name: float(P_CAP_FACTOR * q_by_group[group_name]) for group_name in groups_by_name}
    states: dict[str, dict[str, BoatState]] = {}
    competitors_by_group: dict[str, list[str]] = {}
    events: list[dict[str, Any]] = []

    for group_name, group in groups_by_name.items():
        runtime = prepare_group_runtime(group)
        competitors = runtime["competitors"]
        competitors_by_group[group_name] = competitors
        states[group_name] = {competitor: BoatState(x=0.0, p=p_cap_by_group[group_name], last_state_date=None) for competitor in competitors}

        for race_label in group["selected_races"]:
            race_date = group["race_dates"].get(race_label)
            if race_date is not None:
                events.append({"group": group_name, "race": race_label, "race_date": race_date, "year": int(race_date.year), "race_num": race_num(race_label)})

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
    nll_by_group = {group_name: 0.0 for group_name in groups_by_name}
    obs_by_group = {group_name: 0 for group_name in groups_by_name}
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
        runtime = prepare_group_runtime(groups_by_name[group_name])
        race_rows = runtime["race_rows_by_label"].get(race_label)
        if race_rows is None:
            continue
        race_lookup = runtime["race_lookup_by_label"].get(race_label, {})

        if (group_name, year, race_label) in first_race_by_group_year:
            for competitor in competitors:
                assigned_group = competitor_year_group.get((competitor, year))
                prev_group = competitor_year_group.get((competitor, year - 1))
                if assigned_group != group_name or not prev_group or prev_group == group_name:
                    continue
                snapshot = end_of_year_state.get((prev_group, year - 1, competitor))
                if snapshot is None:
                    continue
                group_states[competitor] = BoatState(x=float(snapshot.x), p=float(snapshot.p), last_state_date=snapshot.last_state_date)

        prior_by_competitor: dict[str, tuple[float, float, int]] = {}
        for competitor in competitors:
            state = group_states[competitor]
            delta_days = 0 if state.last_state_date is None else max(0, (race_date - state.last_state_date).days)
            prior_by_competitor[competitor] = (state.x, min(p_cap, state.p + delta_days * global_q), delta_days)

        obs_mask = race_rows["beregnet_seconds"].notna() & (~race_rows["status_upper"].isin(NON_OBS_STATUSES))
        observed = race_rows.loc[obs_mask, ["competitor", "beregnet_seconds"]].copy()
        observed_step = compute_observation_step(observed, prior_by_competitor)
        b_hat = float(observed_step["b_hat"])
        r_t = float(observed_step["r_t"])
        innovation_by_competitor = observed_step["innovation_by_competitor"]
        gain_by_competitor = observed_step["gain_by_competitor"]
        y_pred_loo_by_competitor = observed_step["y_pred_loo_by_competitor"]
        nll_by_group[group_name] += float(observed_step["nll_sum"])
        obs_by_group[group_name] += int(observed_step["observed_count"])
        observed_competitors = set(observed["competitor"].astype(str).tolist()) if not observed.empty else set()

        for competitor in competitors:
            x_prior, p_prior, delta_t_days = prior_by_competitor[competitor]
            status = ""
            sailed_seconds: float | None = None
            beregnet_seconds: float | None = None
            hdcp_value: float | None = None
            length_nm_value: float | None = None
            race_local_value = race_label
            series_value = ""
            if collect_history:
                status_row = race_lookup.get(competitor)
                if status_row is None:
                    status = "IKKE MED"
                else:
                    status = str(status_row.get("race_status_code") or "").strip()
                    raw_sailed = status_row.get("sailed_seconds")
                    raw_beregnet = status_row.get("beregnet_seconds")
                    raw_hdcp = status_row.get("hdcp")
                    raw_length_nm = status_row.get("length_nm")
                    raw_race_local = status_row.get("race_local")
                    raw_series = status_row.get("series")
                    sailed_seconds = float(raw_sailed) if pd.notna(raw_sailed) else None
                    beregnet_seconds = float(raw_beregnet) if pd.notna(raw_beregnet) else None
                    hdcp_value = float(raw_hdcp) if pd.notna(raw_hdcp) else None
                    length_nm_value = float(raw_length_nm) if pd.notna(raw_length_nm) else None
                    if pd.notna(raw_race_local):
                        race_local_value = str(raw_race_local).strip() or race_label
                    if pd.notna(raw_series):
                        series_value = str(raw_series).strip()

            if competitor in observed_competitors and competitor in innovation_by_competitor and not np.isnan(r_t):
                innovation = float(innovation_by_competitor[competitor])
                gain = float(gain_by_competitor[competitor])
                x_post = float(x_prior + gain * innovation)
                p_post = float((1.0 - gain) * p_prior)
                observed_flag = True
                y_pred_loo = float(y_pred_loo_by_competitor.get(competitor, float("nan")))
            else:
                innovation = float("nan")
                gain = float("nan")
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
                        "hdcp": hdcp_value,
                        "length_nm": length_nm_value,
                        "b_t_hat": b_hat,
                        "delta_t_days": delta_t_days,
                        "global_q": global_q,
                        "x_prior": x_prior,
                        "p_prior": p_prior,
                        "r_t": r_t,
                        "innovation": innovation,
                        "kalman_gain": gain,
                        "y_pred_loo": y_pred_loo,
                        "x_post": x_post,
                        "p_post": p_post,
                    }
                )

        if (group_name, year, race_label) in last_race_by_group_year:
            for competitor, state in group_states.items():
                end_of_year_state[(group_name, year, competitor)] = BoatState(x=float(state.x), p=float(state.p), last_state_date=state.last_state_date)

    history = pd.DataFrame(history_rows) if collect_history else pd.DataFrame()
    return history, nll_by_group, obs_by_group


def q_objective(groups: list[dict[str, Any]], q_value: float) -> tuple[float, int]:
    sq_error_sum = 0.0
    obs_count = 0
    for group in groups:
        result = run_group_filter(group, q_value, collect_history=False)
        sq_error_sum += float(result.loo_sq_error_sum)
        obs_count += int(result.loo_error_count)
    if obs_count == 0:
        return float("inf"), 0
    return float(np.sqrt(sq_error_sum / obs_count)), obs_count


def q_objective_mle(groups: list[dict[str, Any]], q_value: float) -> tuple[float, int]:
    nll_sum = 0.0
    obs_count = 0
    for group in groups:
        result = run_group_filter(group, q_value, collect_history=False)
        nll_sum += float(result.nll_sum)
        obs_count += int(result.observed_count)
    if obs_count == 0:
        return float("inf"), 0
    return nll_sum, obs_count


def resolve_q_objective(value: str) -> str:
    objective = str(value or "").strip().lower()
    if objective not in Q_OBJECTIVE_CHOICES:
        choices = ", ".join(Q_OBJECTIVE_CHOICES)
        raise ValueError(f"Unsupported q objective '{value}'. Choose one of: {choices}")
    return objective


def evaluate_q_score(groups: list[dict[str, Any]], q_value: float, objective: str, score_cache: dict[float, tuple[float, int]] | None = None) -> tuple[float, int]:
    objective = resolve_q_objective(objective)
    key = float(q_value)
    if score_cache is not None and key in score_cache:
        return score_cache[key]

    result = q_objective(groups, key) if objective == "rmse" else q_objective_mle(groups, key)
    if score_cache is not None:
        score_cache[key] = result
    return result


def q_diagnostics(groups: list[dict[str, Any]], q_values: np.ndarray, rmse_cache: dict[float, tuple[float, int]] | None = None, mle_cache: dict[float, tuple[float, int]] | None = None) -> pd.DataFrame:
    rows: list[dict[str, float | int]] = []
    for q_value in q_values:
        q_float = float(q_value)
        rmse_score, rmse_obs = evaluate_q_score(groups, q_float, "rmse", score_cache=rmse_cache)
        mle_score, mle_obs = evaluate_q_score(groups, q_float, "mle", score_cache=mle_cache)
        rows.append(
            {
                "q_value": q_float,
                "one_step_rmse_seconds": float(rmse_score),
                "rmse_observations": int(rmse_obs),
                "negative_log_likelihood": float(mle_score),
                "nll_observations": int(mle_obs),
                "observations": int(rmse_obs),
            }
        )
    return pd.DataFrame(rows).sort_values("q_value").reset_index(drop=True)


def fit_global_q(groups: list[dict[str, Any]], initial_q: float, objective: str, *, score_cache: dict[float, tuple[float, int]] | None = None) -> tuple[float, float, int]:
    objective = resolve_q_objective(objective)
    search_min = Q_SEARCH_MIN
    candidates = np.logspace(math.log10(search_min), math.log10(Q_SEARCH_MAX), 61, dtype=float)
    candidate_values = sorted(set(float(max(search_min, min(Q_SEARCH_MAX, value))) for value in candidates) | {float(max(search_min, min(Q_SEARCH_MAX, initial_q)))})

    best_q = candidate_values[0]
    best_score = float("inf")
    best_obs = 0
    for q_value in candidate_values:
        score, obs = evaluate_q_score(groups, q_value, objective, score_cache=score_cache)
        if score < best_score:
            best_q = q_value
            best_score = score
            best_obs = obs

    for _ in range(2):
        local = sorted(set(max(search_min, min(Q_SEARCH_MAX, float(best_q * factor))) for factor in np.logspace(-0.7, 0.7, 21)))
        for q_value in local:
            score, obs = evaluate_q_score(groups, q_value, objective, score_cache=score_cache)
            if score < best_score:
                best_q = q_value
                best_score = score
                best_obs = obs

    return best_q, best_score, best_obs
