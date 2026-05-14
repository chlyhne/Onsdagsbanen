from __future__ import annotations

import concurrent.futures
import json
import math
import multiprocessing
import os
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
from .constants import P_COV_CAP_FLOOR
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


_DE_WORKER_GROUPS: list[dict[str, Any]] | None = None
_DE_WORKER_OBJECTIVE: str | None = None


def _de_worker_initialize(groups: list[dict[str, Any]], objective: str) -> None:
    global _DE_WORKER_GROUPS, _DE_WORKER_OBJECTIVE
    _DE_WORKER_GROUPS = groups
    _DE_WORKER_OBJECTIVE = objective


def _de_worker_evaluate(log_q: float, x0_value: float, p0_value: float) -> tuple[float, int]:
    if _DE_WORKER_GROUPS is None or _DE_WORKER_OBJECTIVE is None:
        raise RuntimeError("DE worker is not initialized.")
    q_value = float(np.exp(float(log_q)))
    x0 = float(x0_value)
    p0 = float(max(EPS, p0_value))
    score, obs = evaluate_q_score(
        _DE_WORKER_GROUPS,
        q_value,
        _DE_WORKER_OBJECTIVE,
        initial_x0=x0,
        initial_p0=p0,
    )
    return float(score), int(obs)


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


def estimate_initial_x0_from_first_observations(group: dict[str, Any]) -> float:
    runtime = prepare_group_runtime(group)
    combined: pd.DataFrame = runtime["combined"]
    if combined.empty:
        return 0.0

    observed = combined.loc[
        combined["beregnet_seconds"].notna() & (~combined["status_upper"].isin(NON_OBS_STATUSES)),
        ["competitor", "race", "race_date", "beregnet_seconds"],
    ].copy()
    if observed.empty:
        return 0.0

    observed["competitor"] = observed["competitor"].astype(str)
    observed["y"] = np.log(observed["beregnet_seconds"].astype(float).clip(lower=EPS))
    observed["race_mean_y"] = observed.groupby("race")["y"].transform("mean")
    observed["x_centered"] = observed["y"] - observed["race_mean_y"]
    observed["race_num"] = observed["race"].map(race_num)

    observed = observed.sort_values(["competitor", "race_date", "race_num", "race"]).reset_index(drop=True)
    first_rows = observed.groupby("competitor", sort=False, as_index=False).head(1)
    if first_rows.empty:
        return 0.0

    x0_value = float(first_rows["x_centered"].mean())
    if not np.isfinite(x0_value):
        return 0.0
    return x0_value


def estimate_initial_p0_from_first_observations(group: dict[str, Any]) -> float:
    runtime = prepare_group_runtime(group)
    combined: pd.DataFrame = runtime["combined"]
    if combined.empty:
        return float(max(EPS, P_COV_CAP_FLOOR))

    observed = combined.loc[
        combined["beregnet_seconds"].notna() & (~combined["status_upper"].isin(NON_OBS_STATUSES)),
        ["competitor", "race", "race_date", "beregnet_seconds"],
    ].copy()
    if observed.empty:
        return float(max(EPS, P_COV_CAP_FLOOR))

    observed["competitor"] = observed["competitor"].astype(str)
    observed["y"] = np.log(observed["beregnet_seconds"].astype(float).clip(lower=EPS))
    observed["race_mean_y"] = observed.groupby("race")["y"].transform("mean")
    observed["x_centered"] = observed["y"] - observed["race_mean_y"]
    observed["race_num"] = observed["race"].map(race_num)

    observed = observed.sort_values(["competitor", "race_date", "race_num", "race"]).reset_index(drop=True)
    first_rows = observed.groupby("competitor", sort=False, as_index=False).head(1)
    if first_rows.empty:
        return float(max(EPS, P_COV_CAP_FLOOR))

    p0_value = float(first_rows["x_centered"].var(ddof=0))
    if not np.isfinite(p0_value) or p0_value <= 0.0:
        return float(max(EPS, P_COV_CAP_FLOOR))
    return float(max(EPS, p0_value))


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

    race_order_by_label: dict[str, int] = {}
    for idx, race_label in enumerate(group.get("selected_races", [])):
        race_order_by_label[str(race_label)] = int(idx)

    debut_order_by_competitor: dict[str, int] = {}
    for race_label in group.get("selected_races", []):
        label = str(race_label)
        race_lookup = race_lookup_by_label.get(label, {})
        race_order = race_order_by_label.get(label)
        if race_order is None:
            continue
        for competitor in race_lookup:
            existing = debut_order_by_competitor.get(str(competitor))
            if existing is None or race_order < existing:
                debut_order_by_competitor[str(competitor)] = race_order

    runtime = {
        "combined": combined,
        "competitors": competitors,
        "race_rows_by_label": race_rows_by_label,
        "race_lookup_by_label": race_lookup_by_label,
        "race_order_by_label": race_order_by_label,
        "debut_order_by_competitor": debut_order_by_competitor,
    }
    group["_runtime"] = runtime
    return runtime


def _symmetrize_and_floor_covariance(covariance: np.ndarray, *, floor: float = EPS) -> np.ndarray:
    cov = 0.5 * (np.asarray(covariance, dtype=float) + np.asarray(covariance, dtype=float).T)
    diag = np.diag(cov).copy()
    diag = np.where(np.isfinite(diag), diag, floor)
    diag = np.maximum(diag, floor)
    np.fill_diagonal(cov, diag)
    return cov


def _project_zero_sum_full_state(mean_values: np.ndarray, covariance: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mean = np.asarray(mean_values, dtype=float).copy()
    cov = _symmetrize_and_floor_covariance(covariance)
    if mean.size == 0:
        return mean, cov
    ones = np.ones(mean.shape[0], dtype=float)
    v = cov @ ones
    denom = float(ones @ v)
    if not np.isfinite(denom) or denom <= EPS:
        return mean - float(np.mean(mean)), cov
    correction = float(ones @ mean) / denom
    mean_projected = mean - v * correction
    cov_projected = cov - np.outer(v, v) / denom
    cov_projected = _symmetrize_and_floor_covariance(cov_projected)
    return mean_projected, cov_projected


def _project_active_zero_sum(mean_values: np.ndarray, covariance: np.ndarray, active_indices: list[int]) -> tuple[np.ndarray, np.ndarray]:
    mean = np.asarray(mean_values, dtype=float).copy()
    cov = _symmetrize_and_floor_covariance(covariance)
    if not active_indices:
        return mean, cov
    active = np.array(sorted(set(int(idx) for idx in active_indices)), dtype=int)
    sub_mean = mean[active]
    sub_cov = cov[np.ix_(active, active)]
    projected_mean, projected_cov = _project_zero_sum_full_state(sub_mean, sub_cov)
    mean[active] = projected_mean
    cov[np.ix_(active, active)] = projected_cov
    cov = _symmetrize_and_floor_covariance(cov)
    return mean, cov


def _profiled_day_nll_full(measurement_variance: float, centered_values: np.ndarray, prior_covariance: np.ndarray) -> tuple[float, float]:
    m = centered_values.size
    if m == 0:
        return 0.0, float("nan")
    sigma2 = float(max(EPS, measurement_variance))
    s_matrix = _symmetrize_and_floor_covariance(prior_covariance + sigma2 * np.eye(m, dtype=float))
    sign, logdet = np.linalg.slogdet(s_matrix)
    if sign <= 0 or not np.isfinite(logdet):
        return float("inf"), float("nan")
    ones = np.ones(m, dtype=float)
    inv_times_ones = np.linalg.solve(s_matrix, ones)
    inv_times_centered = np.linalg.solve(s_matrix, centered_values)
    denom = float(ones @ inv_times_ones)
    if not np.isfinite(denom) or denom <= EPS:
        return float("inf"), float("nan")
    mu_hat = float((ones @ inv_times_centered) / denom)
    inv_times_residual = inv_times_centered - mu_hat * inv_times_ones
    residual = centered_values - mu_hat * ones
    quad = float(residual @ inv_times_residual)
    nll = 0.5 * float(m * math.log(2.0 * math.pi) + logdet + quad)
    return nll, mu_hat


def fit_measurement_variance_full(centered_values: np.ndarray, prior_covariance: np.ndarray) -> float:
    if centered_values.size == 0:
        return float("nan")

    lower = EPS
    m = centered_values.size
    diag_mean = float(np.mean(np.diag(prior_covariance))) if m > 0 else EPS
    base_scale = max(EPS, float(np.var(centered_values, ddof=0)), float(np.mean(np.square(centered_values))), diag_mean)
    upper = max(lower * 16.0, base_scale)
    upper_nll, _ = _profiled_day_nll_full(upper, centered_values, prior_covariance)

    for _ in range(8):
        candidate_upper = upper * 4.0
        candidate_nll, _ = _profiled_day_nll_full(candidate_upper, centered_values, prior_covariance)
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
    fc, _ = _profiled_day_nll_full(c, centered_values, prior_covariance)
    fd, _ = _profiled_day_nll_full(d, centered_values, prior_covariance)

    for _ in range(32):
        if fc <= fd:
            right = d
            d = c
            fd = fc
            c = right - (right - left) / phi
            fc, _ = _profiled_day_nll_full(c, centered_values, prior_covariance)
        else:
            left = c
            c = d
            fc = fd
            d = left + (right - left) / phi
            fd, _ = _profiled_day_nll_full(d, centered_values, prior_covariance)

    return max(EPS, float(0.5 * (left + right)))


def fit_day_parameters_full(centered_values: np.ndarray, prior_covariance: np.ndarray) -> tuple[float, float, float]:
    if centered_values.size == 0:
        return float("nan"), float("nan"), 0.0
    r_t = fit_measurement_variance_full(centered_values, prior_covariance)
    nll_sum, mu_hat = _profiled_day_nll_full(r_t, centered_values, prior_covariance)
    return mu_hat, r_t, nll_sum


def compute_observation_step(
    observed: pd.DataFrame,
    competitors: list[str],
    competitor_index: dict[str, int],
    prior_mean: np.ndarray,
    prior_covariance: np.ndarray,
) -> dict[str, Any]:
    if observed.empty:
        return {
            "b_hat": float("nan"),
            "r_t": float("nan"),
            "innovation_by_competitor": {},
            "gain_by_competitor": {},
            "y_pred_loo_by_competitor": {},
            "post_mean": prior_mean,
            "post_covariance": prior_covariance,
            "nll_sum": 0.0,
            "observed_count": 0,
            "loo_sq_error_sum": 0.0,
            "loo_error_count": 0,
        }
    observed_competitors = observed["competitor"].astype(str).tolist()
    observed_indices = np.array([competitor_index[c] for c in observed_competitors], dtype=int)
    beregnet_seconds = observed["beregnet_seconds"].to_numpy(dtype=float)
    y_values = np.log(np.clip(beregnet_seconds, EPS, None))

    x_prior_obs = prior_mean[observed_indices]
    p_prior_obs = prior_covariance[np.ix_(observed_indices, observed_indices)]
    centered_values = y_values - x_prior_obs
    b_hat, r_t, nll_sum = fit_day_parameters_full(centered_values, p_prior_obs)

    s_matrix = _symmetrize_and_floor_covariance(p_prior_obs + float(r_t) * np.eye(observed_indices.size, dtype=float))
    s_inv = np.linalg.inv(s_matrix)
    innovations = centered_values - b_hat
    p_cross = prior_covariance[:, observed_indices]
    k_matrix = p_cross @ s_inv
    post_mean = prior_mean + (k_matrix @ innovations)
    post_covariance = prior_covariance - (k_matrix @ p_cross.T)
    post_covariance = _symmetrize_and_floor_covariance(post_covariance)

    innovation_by_competitor = {competitor: float(innovations[idx]) for idx, competitor in enumerate(observed_competitors)}
    gain_by_competitor: dict[str, float] = {}
    for local_idx, competitor in enumerate(observed_competitors):
        global_idx = int(observed_indices[local_idx])
        gain_by_competitor[competitor] = float(k_matrix[global_idx, local_idx])

    y_pred_loo_by_competitor: dict[str, float] = {}
    if len(observed_competitors) == 1:
        y_pred_loo_by_competitor[observed_competitors[0]] = float(b_hat + x_prior_obs[0])
    else:
        for local_idx, competitor in enumerate(observed_competitors):
            mask = np.ones(len(observed_competitors), dtype=bool)
            mask[local_idx] = False
            centered_values_loo = centered_values[mask]
            p_prior_loo = p_prior_obs[np.ix_(mask, mask)]
            b_hat_loo, _, _ = fit_day_parameters_full(centered_values_loo, p_prior_loo)
            y_pred_loo_by_competitor[competitor] = float(b_hat_loo + x_prior_obs[local_idx])

    loo_sq_error_sum = 0.0
    loo_error_count = 0
    for competitor, observed_seconds in zip(observed_competitors, beregnet_seconds):
        pred_seconds = float(np.exp(y_pred_loo_by_competitor[competitor]))
        if np.isfinite(pred_seconds):
            error = float(observed_seconds - pred_seconds)
            loo_sq_error_sum += error * error
            loo_error_count += 1

    return {
        "b_hat": float(b_hat),
        "r_t": float(r_t),
        "innovation_by_competitor": innovation_by_competitor,
        "gain_by_competitor": gain_by_competitor,
        "y_pred_loo_by_competitor": y_pred_loo_by_competitor,
        "post_mean": post_mean,
        "post_covariance": post_covariance,
        "nll_sum": float(nll_sum),
        "observed_count": len(observed_competitors),
        "loo_sq_error_sum": float(loo_sq_error_sum),
        "loo_error_count": int(loo_error_count),
    }


def run_group_filter(
    group: dict[str, Any],
    global_q: float,
    *,
    initial_x0: float = 0.0,
    initial_p0: float = P_COV_CAP_FLOOR,
    collect_history: bool = True,
) -> GroupFilterResult:
    runtime = prepare_group_runtime(group)
    selected_races: list[str] = list(group["selected_races"])
    race_dates: dict[str, datetime] = dict(group["race_dates"])
    group_label = str(group["group"])

    competitors: list[str] = runtime["competitors"]
    race_rows_by_label: dict[str, pd.DataFrame] = runtime["race_rows_by_label"]
    race_lookup_by_label: dict[str, dict[str, dict[str, Any]]] = runtime["race_lookup_by_label"]
    race_order_by_label: dict[str, int] = runtime.get("race_order_by_label", {})
    debut_order_by_competitor: dict[str, int] = runtime.get("debut_order_by_competitor", {})
    initial_p0 = float(max(EPS, initial_p0))
    p_cap = float(max(initial_p0, P_COV_CAP_FLOOR))
    initial_x0 = float(initial_x0)
    competitor_index = {competitor: idx for idx, competitor in enumerate(competitors)}
    n_competitors = len(competitors)
    state_mean = np.zeros(n_competitors, dtype=float)
    state_cov = np.eye(n_competitors, dtype=float) * EPS
    active_by_competitor = {competitor: False for competitor in competitors}
    last_state_date_by_competitor = {competitor: None for competitor in competitors}

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
        race_order = race_order_by_label.get(str(race_label))
        if race_order is not None:
            for competitor in competitors:
                if active_by_competitor[competitor]:
                    continue
                if debut_order_by_competitor.get(competitor) == race_order:
                    idx = competitor_index[competitor]
                    state_mean[idx] = float(initial_x0)
                    state_cov[idx, :] = 0.0
                    state_cov[:, idx] = 0.0
                    state_cov[idx, idx] = float(initial_p0)
                    last_state_date_by_competitor[competitor] = None
                    active_by_competitor[competitor] = True

        active_indices = [competitor_index[c] for c in competitors if active_by_competitor[c]]
        state_mean, state_cov = _project_active_zero_sum(state_mean, state_cov, active_indices)

        delta_days_by_competitor: dict[str, int] = {}
        process_diag = np.zeros(n_competitors, dtype=float)
        for competitor in competitors:
            if not active_by_competitor[competitor]:
                delta_days_by_competitor[competitor] = 0
                continue
            last_state_date = last_state_date_by_competitor[competitor]
            delta_days = 0 if last_state_date is None else max(0, (race_date - last_state_date).days)
            delta_days_by_competitor[competitor] = int(delta_days)
            process_diag[competitor_index[competitor]] = float(delta_days) * float(global_q)
        prior_mean = state_mean.copy()
        prior_cov = _symmetrize_and_floor_covariance(state_cov + np.diag(process_diag))
        max_diag = float(np.max(np.diag(prior_cov))) if n_competitors > 0 else 0.0
        if np.isfinite(max_diag) and max_diag > p_cap:
            prior_cov *= float(p_cap / max_diag)
            prior_cov = _symmetrize_and_floor_covariance(prior_cov)

        obs_mask = race_rows["beregnet_seconds"].notna() & (~race_rows["status_upper"].isin(NON_OBS_STATUSES))
        observed = race_rows.loc[obs_mask, ["competitor", "beregnet_seconds"]].copy()
        observed_step = compute_observation_step(observed, competitors, competitor_index, prior_mean, prior_cov)
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
        state_mean = np.asarray(observed_step["post_mean"], dtype=float).copy()
        state_cov = _symmetrize_and_floor_covariance(np.asarray(observed_step["post_covariance"], dtype=float))
        state_mean, state_cov = _project_active_zero_sum(state_mean, state_cov, active_indices)

        row_payload_by_competitor: dict[str, dict[str, Any]] = {}
        for competitor in competitors:
            if active_by_competitor[competitor]:
                last_state_date_by_competitor[competitor] = race_date

        for competitor in competitors:
            idx = competitor_index[competitor]
            is_active = bool(active_by_competitor[competitor])
            x_prior = float(prior_mean[idx]) if is_active else float("nan")
            p_prior = float(prior_cov[idx, idx]) if is_active else float("nan")
            x_post = float(state_mean[idx]) if is_active else float("nan")
            p_post = float(state_cov[idx, idx]) if is_active else float("nan")
            delta_t_days = int(delta_days_by_competitor[competitor])
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

            if is_active and competitor in observed_competitors and competitor in innovation_by_competitor and not np.isnan(r_t):
                innovation = float(innovation_by_competitor[competitor])
                gain = float(gain_by_competitor[competitor])
                observed_flag = True
                y_pred_loo = float(y_pred_loo_by_competitor.get(competitor, float("nan")))
            else:
                innovation = float("nan")
                gain = float("nan")
                observed_flag = False
                y_pred_loo = float("nan")

            row_payload_by_competitor[competitor] = {
                "x_prior": float(x_prior),
                "p_prior": float(p_prior),
                "delta_t_days": int(delta_t_days),
                "status": status,
                "sailed_seconds": sailed_seconds,
                "beregnet_seconds": beregnet_seconds,
                "hdcp": hdcp_value,
                "length_nm": length_nm_value,
                "race_local": race_local_value,
                "series": series_value,
                "innovation": float(innovation),
                "gain": float(gain),
                "observed_flag": bool(observed_flag),
                "y_pred_loo": float(y_pred_loo),
                "x_post": float(x_post),
                "p_post": float(p_post),
            }

        if collect_history:
            for competitor in competitors:
                row_payload = row_payload_by_competitor[competitor]
                history_rows.append(
                    {
                        "group": group_label,
                        "race": race_label,
                        "race_local": row_payload["race_local"],
                        "series": row_payload["series"],
                        "race_date": race_date.date().isoformat(),
                        "year": int(race_date.year),
                        "competitor": competitor,
                        "observed": row_payload["observed_flag"],
                        "status": row_payload["status"],
                        "sailed_seconds": row_payload["sailed_seconds"],
                        "beregnet_seconds": row_payload["beregnet_seconds"],
                        "hdcp": row_payload["hdcp"],
                        "length_nm": row_payload["length_nm"],
                        "b_t_hat": b_hat,
                        "delta_t_days": row_payload["delta_t_days"],
                        "global_q": global_q,
                        "x0_group": initial_x0,
                        "x_prior": row_payload["x_prior"],
                        "p_prior": row_payload["p_prior"],
                        "r_t": r_t,
                        "innovation": row_payload["innovation"],
                        "kalman_gain": row_payload["gain"],
                        "y_pred_loo": row_payload["y_pred_loo"],
                        "x_post": row_payload["x_post"],
                        "p_post": row_payload["p_post"],
                    }
                )

    history = pd.DataFrame(history_rows) if collect_history else pd.DataFrame()
    return GroupFilterResult(history=history, nll_sum=nll_sum, observed_count=observed_count, loo_sq_error_sum=loo_sq_error_sum, loo_error_count=loo_error_count)


def run_all_groups_with_transfer(
    groups: list[dict[str, Any]],
    q_by_group: dict[str, float],
    competitor_year_group: dict[tuple[str, int], str],
    *,
    initial_x0_by_group: dict[str, float] | None = None,
    initial_p0_by_group: dict[str, float] | None = None,
    collect_history: bool = True,
) -> tuple[pd.DataFrame, dict[str, float], dict[str, int]]:
    if not groups:
        return pd.DataFrame(), {}, {}

    groups_by_name = {str(group["group"]): group for group in groups}
    initial_p0_by_group = initial_p0_by_group or {}
    p_cap_by_group = {
        group_name: float(max(P_COV_CAP_FLOOR, float(max(EPS, initial_p0_by_group.get(group_name, P_COV_CAP_FLOOR)))))
        for group_name in groups_by_name
    }
    state_mean_by_group: dict[str, np.ndarray] = {}
    state_cov_by_group: dict[str, np.ndarray] = {}
    last_state_date_by_group: dict[str, dict[str, datetime | None]] = {}
    active_by_group: dict[str, dict[str, bool]] = {}
    competitors_by_group: dict[str, list[str]] = {}
    competitor_index_by_group: dict[str, dict[str, int]] = {}
    debut_order_by_group: dict[str, dict[str, int]] = {}
    race_order_by_group_label: dict[str, dict[str, int]] = {}
    events: list[dict[str, Any]] = []

    initial_x0_by_group = initial_x0_by_group or {}
    for group_name, group in groups_by_name.items():
        runtime = prepare_group_runtime(group)
        competitors = runtime["competitors"]
        competitors_by_group[group_name] = competitors
        competitor_index_by_group[group_name] = {competitor: idx for idx, competitor in enumerate(competitors)}
        debut_order_by_group[group_name] = dict(runtime.get("debut_order_by_competitor", {}))
        race_order_by_group_label[group_name] = dict(runtime.get("race_order_by_label", {}))
        initial_x0 = float(initial_x0_by_group.get(group_name, 0.0))
        initial_p0 = float(max(EPS, initial_p0_by_group.get(group_name, P_COV_CAP_FLOOR)))
        n_competitors = len(competitors)
        initial_mean = np.zeros(n_competitors, dtype=float)
        initial_cov = np.eye(n_competitors, dtype=float) * EPS
        state_mean_by_group[group_name] = initial_mean
        state_cov_by_group[group_name] = initial_cov
        last_state_date_by_group[group_name] = {competitor: None for competitor in competitors}
        active_by_group[group_name] = {competitor: False for competitor in competitors}

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
        group_mean = state_mean_by_group[group_name]
        group_cov = state_cov_by_group[group_name]
        group_last_dates = last_state_date_by_group[group_name]
        group_active = active_by_group[group_name]
        competitors = competitors_by_group[group_name]
        competitor_index = competitor_index_by_group[group_name]
        debut_order_by_competitor = debut_order_by_group[group_name]
        race_order_by_label = race_order_by_group_label[group_name]
        n_competitors = len(competitors)
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
                idx = competitor_index[competitor]
                group_mean[idx] = float(snapshot.x)
                group_cov[idx, :] = 0.0
                group_cov[:, idx] = 0.0
                group_cov[idx, idx] = float(max(EPS, snapshot.p))
                group_last_dates[competitor] = snapshot.last_state_date
                group_active[competitor] = True

        race_order = race_order_by_label.get(str(race_label))
        if race_order is not None:
            initial_x0 = float(initial_x0_by_group.get(group_name, 0.0))
            initial_p0 = float(max(EPS, float(max(EPS, initial_p0_by_group.get(group_name, P_COV_CAP_FLOOR)))))
            for competitor in competitors:
                if group_active[competitor]:
                    continue
                if debut_order_by_competitor.get(competitor) == race_order:
                    idx = competitor_index[competitor]
                    group_mean[idx] = initial_x0
                    group_cov[idx, :] = 0.0
                    group_cov[:, idx] = 0.0
                    group_cov[idx, idx] = initial_p0
                    group_last_dates[competitor] = None
                    group_active[competitor] = True

        active_indices = [competitor_index[c] for c in competitors if group_active[c]]
        group_mean, group_cov = _project_active_zero_sum(group_mean, group_cov, active_indices)
        delta_days_by_competitor: dict[str, int] = {}
        process_diag = np.zeros(n_competitors, dtype=float)
        for competitor in competitors:
            if not group_active[competitor]:
                delta_days_by_competitor[competitor] = 0
                continue
            last_state_date = group_last_dates[competitor]
            delta_days = 0 if last_state_date is None else max(0, (race_date - last_state_date).days)
            delta_days_by_competitor[competitor] = int(delta_days)
            process_diag[competitor_index[competitor]] = float(delta_days) * float(global_q)
        prior_mean = group_mean.copy()
        prior_cov = _symmetrize_and_floor_covariance(group_cov + np.diag(process_diag))
        max_diag = float(np.max(np.diag(prior_cov))) if n_competitors > 0 else 0.0
        if np.isfinite(max_diag) and max_diag > p_cap:
            prior_cov *= float(p_cap / max_diag)
            prior_cov = _symmetrize_and_floor_covariance(prior_cov)

        obs_mask = race_rows["beregnet_seconds"].notna() & (~race_rows["status_upper"].isin(NON_OBS_STATUSES))
        observed = race_rows.loc[obs_mask, ["competitor", "beregnet_seconds"]].copy()
        observed_step = compute_observation_step(observed, competitors, competitor_index, prior_mean, prior_cov)
        b_hat = float(observed_step["b_hat"])
        r_t = float(observed_step["r_t"])
        innovation_by_competitor = observed_step["innovation_by_competitor"]
        gain_by_competitor = observed_step["gain_by_competitor"]
        y_pred_loo_by_competitor = observed_step["y_pred_loo_by_competitor"]
        nll_by_group[group_name] += float(observed_step["nll_sum"])
        obs_by_group[group_name] += int(observed_step["observed_count"])
        observed_competitors = set(observed["competitor"].astype(str).tolist()) if not observed.empty else set()
        group_mean = np.asarray(observed_step["post_mean"], dtype=float).copy()
        group_cov = _symmetrize_and_floor_covariance(np.asarray(observed_step["post_covariance"], dtype=float))
        group_mean, group_cov = _project_active_zero_sum(group_mean, group_cov, active_indices)
        state_mean_by_group[group_name] = group_mean
        state_cov_by_group[group_name] = group_cov

        row_payload_by_competitor: dict[str, dict[str, Any]] = {}
        for competitor in competitors:
            if group_active[competitor]:
                group_last_dates[competitor] = race_date

        for competitor in competitors:
            idx = competitor_index[competitor]
            is_active = bool(group_active[competitor])
            x_prior = float(prior_mean[idx]) if is_active else float("nan")
            p_prior = float(prior_cov[idx, idx]) if is_active else float("nan")
            x_post = float(group_mean[idx]) if is_active else float("nan")
            p_post = float(group_cov[idx, idx]) if is_active else float("nan")
            delta_t_days = int(delta_days_by_competitor[competitor])
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

            if is_active and competitor in observed_competitors and competitor in innovation_by_competitor and not np.isnan(r_t):
                innovation = float(innovation_by_competitor[competitor])
                gain = float(gain_by_competitor[competitor])
                observed_flag = True
                y_pred_loo = float(y_pred_loo_by_competitor.get(competitor, float("nan")))
            else:
                innovation = float("nan")
                gain = float("nan")
                observed_flag = False
                y_pred_loo = float("nan")

            row_payload_by_competitor[competitor] = {
                "x_prior": float(x_prior),
                "p_prior": float(p_prior),
                "delta_t_days": int(delta_t_days),
                "status": status,
                "sailed_seconds": sailed_seconds,
                "beregnet_seconds": beregnet_seconds,
                "hdcp": hdcp_value,
                "length_nm": length_nm_value,
                "race_local": race_local_value,
                "series": series_value,
                "innovation": float(innovation),
                "gain": float(gain),
                "observed_flag": bool(observed_flag),
                "y_pred_loo": float(y_pred_loo),
                "x_post": float(x_post),
                "p_post": float(p_post),
            }

        if collect_history:
            for competitor in competitors:
                row_payload = row_payload_by_competitor[competitor]
                history_rows.append(
                    {
                        "group": group_name,
                        "race": race_label,
                        "race_local": row_payload["race_local"],
                        "series": row_payload["series"],
                        "race_date": race_date.date().isoformat(),
                        "year": int(year),
                        "competitor": competitor,
                        "observed": row_payload["observed_flag"],
                        "status": row_payload["status"],
                        "sailed_seconds": row_payload["sailed_seconds"],
                        "beregnet_seconds": row_payload["beregnet_seconds"],
                        "hdcp": row_payload["hdcp"],
                        "length_nm": row_payload["length_nm"],
                        "b_t_hat": b_hat,
                        "delta_t_days": row_payload["delta_t_days"],
                        "global_q": global_q,
                        "x0_group": float(initial_x0_by_group.get(group_name, 0.0)),
                        "p0_group": float(max(EPS, initial_p0_by_group.get(group_name, P_COV_CAP_FLOOR))),
                        "x_prior": row_payload["x_prior"],
                        "p_prior": row_payload["p_prior"],
                        "r_t": r_t,
                        "innovation": row_payload["innovation"],
                        "kalman_gain": row_payload["gain"],
                        "y_pred_loo": row_payload["y_pred_loo"],
                        "x_post": row_payload["x_post"],
                        "p_post": row_payload["p_post"],
                    }
                )

        if (group_name, year, race_label) in last_race_by_group_year:
            for competitor in competitors:
                if not group_active[competitor]:
                    continue
                idx = competitor_index[competitor]
                end_of_year_state[(group_name, year, competitor)] = BoatState(
                    x=float(group_mean[idx]),
                    p=float(group_cov[idx, idx]),
                    last_state_date=group_last_dates[competitor],
                )

    history = pd.DataFrame(history_rows) if collect_history else pd.DataFrame()
    return history, nll_by_group, obs_by_group


def q_objective(
    groups: list[dict[str, Any]],
    q_value: float,
    *,
    initial_x0: float = 0.0,
    initial_p0: float = P_COV_CAP_FLOOR,
) -> tuple[float, int]:
    sq_error_sum = 0.0
    obs_count = 0
    for group in groups:
        result = run_group_filter(group, q_value, initial_x0=initial_x0, initial_p0=initial_p0, collect_history=False)
        sq_error_sum += float(result.loo_sq_error_sum)
        obs_count += int(result.loo_error_count)
    if obs_count == 0:
        return float("inf"), 0
    return float(np.sqrt(sq_error_sum / obs_count)), obs_count


def q_objective_mle(
    groups: list[dict[str, Any]],
    q_value: float,
    *,
    initial_x0: float = 0.0,
    initial_p0: float = P_COV_CAP_FLOOR,
) -> tuple[float, int]:
    nll_sum = 0.0
    obs_count = 0
    for group in groups:
        result = run_group_filter(group, q_value, initial_x0=initial_x0, initial_p0=initial_p0, collect_history=False)
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


def evaluate_q_score(
    groups: list[dict[str, Any]],
    q_value: float,
    objective: str,
    *,
    initial_x0: float = 0.0,
    initial_p0: float = P_COV_CAP_FLOOR,
    score_cache: dict[float, tuple[float, int]] | None = None,
) -> tuple[float, int]:
    objective = resolve_q_objective(objective)
    key = float(q_value)
    if score_cache is not None and key in score_cache:
        return score_cache[key]

    result = (
        q_objective(groups, key, initial_x0=initial_x0, initial_p0=initial_p0)
        if objective == "rmse"
        else q_objective_mle(groups, key, initial_x0=initial_x0, initial_p0=initial_p0)
    )
    if score_cache is not None:
        score_cache[key] = result
    return result


def q_diagnostics(
    groups: list[dict[str, Any]],
    q_values: np.ndarray,
    *,
    initial_x0: float = 0.0,
    initial_p0: float = P_COV_CAP_FLOOR,
    rmse_cache: dict[float, tuple[float, int]] | None = None,
    mle_cache: dict[float, tuple[float, int]] | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, float | int]] = []
    for q_value in q_values:
        q_float = float(q_value)
        rmse_score, rmse_obs = evaluate_q_score(
            groups, q_float, "rmse", initial_x0=initial_x0, initial_p0=initial_p0, score_cache=rmse_cache
        )
        mle_score, mle_obs = evaluate_q_score(
            groups, q_float, "mle", initial_x0=initial_x0, initial_p0=initial_p0, score_cache=mle_cache
        )
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


def fit_global_q(
    groups: list[dict[str, Any]],
    initial_q: float,
    objective: str,
    *,
    initial_x0: float = 0.0,
    initial_p0: float = P_COV_CAP_FLOOR,
    score_cache: dict[float, tuple[float, int]] | None = None,
    n_workers: int | None = None,
    progress_label: str | None = None,
    progress_every: int = 5,
) -> tuple[float, float, int]:
    objective = resolve_q_objective(objective)
    x0_fixed = float(initial_x0)
    p0_fixed = float(max(EPS, initial_p0))
    progress_every = max(1, int(progress_every))

    if n_workers is None:
        env_workers = os.getenv("REDRESS_Q_WORKERS")
        if env_workers is not None and str(env_workers).strip() != "":
            try:
                n_workers = int(env_workers)
            except ValueError:
                n_workers = None
        if n_workers is None:
            n_workers = max(1, (os.cpu_count() or 1) - 1)
    n_workers = max(1, int(n_workers))
    use_parallel = n_workers > 1

    executor: concurrent.futures.ProcessPoolExecutor | None = None
    parallel_cache: dict[float, tuple[float, int]] = {}
    evaluated_scores: dict[float, tuple[float, int]] = {}
    if use_parallel:
        mp_context = multiprocessing.get_context("fork")
        executor = concurrent.futures.ProcessPoolExecutor(
            max_workers=n_workers,
            mp_context=mp_context,
            initializer=_de_worker_initialize,
            initargs=(groups, objective),
        )

    def evaluate_many(q_values: list[float], *, stage_label: str) -> dict[float, tuple[float, int]]:
        unique_q_values = [float(q) for q in q_values]
        results_by_q: dict[float, tuple[float, int]] = {}
        missing: list[float] = []

        for q_value in unique_q_values:
            if score_cache is not None and q_value in score_cache:
                results_by_q[q_value] = score_cache[q_value]
            elif q_value in parallel_cache:
                results_by_q[q_value] = parallel_cache[q_value]
            else:
                missing.append(q_value)

        if progress_label:
            cached_count = len(unique_q_values) - len(missing)
            if cached_count > 0:
                print(
                    f"[QFIT {progress_label}] {stage_label} reused {cached_count}/{len(unique_q_values)} cached evaluations",
                    flush=True,
                )

        if missing:
            if executor is None:
                computed: list[tuple[float, int]] = []
                for idx, q_value in enumerate(missing, start=1):
                    result = evaluate_q_score(
                        groups,
                        q_value,
                        objective,
                        initial_x0=x0_fixed,
                        initial_p0=p0_fixed,
                        score_cache=score_cache,
                    )
                    computed.append(result)
                    if progress_label and (idx % progress_every == 0 or idx == len(missing)):
                        probe = results_by_q | {qv: (float(rv[0]), int(rv[1])) for qv, rv in zip(missing[:idx], computed)}
                        best_q_local, best_res_local = min(probe.items(), key=lambda kv: kv[1][0])
                        print(
                            f"[QFIT {progress_label}] {stage_label} completed {len(probe)}/{len(unique_q_values)} "
                            f"last q={q_value:.3e} obj={float(result[0]):.6f} "
                            f"best objective={float(best_res_local[0]):.6f} q={best_q_local:.3e} obs={int(best_res_local[1])}",
                            flush=True,
                        )
            else:
                future_to_q = {
                    executor.submit(_de_worker_evaluate, float(math.log(q_value)), x0_fixed, p0_fixed): q_value
                    for q_value in missing
                }
                computed_by_q: dict[float, tuple[float, int]] = {}
                completed = 0
                for future in concurrent.futures.as_completed(future_to_q):
                    q_value = future_to_q[future]
                    value = future.result()
                    computed_by_q[q_value] = (float(value[0]), int(value[1]))
                    completed += 1
                    if progress_label and (completed % progress_every == 0 or completed == len(missing)):
                        probe = results_by_q | computed_by_q
                        best_q_local, best_res_local = min(probe.items(), key=lambda kv: kv[1][0])
                        print(
                            f"[QFIT {progress_label}] {stage_label} completed {len(probe)}/{len(unique_q_values)} "
                            f"last q={q_value:.3e} obj={float(value[0]):.6f} "
                            f"best objective={float(best_res_local[0]):.6f} q={best_q_local:.3e} obs={int(best_res_local[1])}",
                            flush=True,
                        )
                computed = [computed_by_q[q_value] for q_value in missing]

            for q_value, value in zip(missing, computed):
                result = (float(value[0]), int(value[1]))
                parallel_cache[q_value] = result
                if score_cache is not None:
                    score_cache[q_value] = result
                results_by_q[q_value] = result

        for q_value, result in results_by_q.items():
            evaluated_scores[float(q_value)] = (float(result[0]), int(result[1]))

        return results_by_q

    search_min = Q_SEARCH_MIN
    candidates = np.logspace(math.log10(search_min), math.log10(Q_SEARCH_MAX), 61, dtype=float)
    candidate_values = sorted(set(float(max(search_min, min(Q_SEARCH_MAX, value))) for value in candidates) | {float(max(search_min, min(Q_SEARCH_MAX, initial_q)))})

    best_q = candidate_values[0]
    best_score = float("inf")
    best_obs = 0
    if progress_label:
        worker_text = f"{n_workers} workers" if use_parallel else "single worker"
        print(
            f"[QFIT {progress_label}] start objective={objective} x0={x0_fixed:+.5f} p0={p0_fixed:.3e} ({worker_text}, unordered completion in parallel mode)",
            flush=True,
        )
    try:
        candidate_scores = evaluate_many(candidate_values, stage_label="grid")
        for q_value in candidate_values:
            score, obs = candidate_scores[q_value]
            if score < best_score:
                best_q = q_value
                best_score = score
                best_obs = obs

        for local_round in range(2):
            local = sorted(set(max(search_min, min(Q_SEARCH_MAX, float(best_q * factor))) for factor in np.logspace(-0.7, 0.7, 21)))
            if progress_label:
                new_count = sum(1 for q in local if q not in evaluated_scores)
                print(
                    f"[QFIT {progress_label}] local-{local_round + 1} candidates={len(local)} new={new_count} "
                    f"range=[{local[0]:.3e}, {local[-1]:.3e}]",
                    flush=True,
                )
            local_scores = evaluate_many(local, stage_label=f"local-{local_round + 1}")
            for q_value in local:
                score, obs = local_scores[q_value]
                if score < best_score:
                    best_q = q_value
                    best_score = score
                    best_obs = obs
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)

    if evaluated_scores:
        final_q, final_res = min(evaluated_scores.items(), key=lambda kv: kv[1][0])
        best_q = float(final_q)
        best_score = float(final_res[0])
        best_obs = int(final_res[1])

    if progress_label:
        at_lower = abs(best_q - float(Q_SEARCH_MIN)) <= max(1e-20, abs(Q_SEARCH_MIN) * 1e-9)
        at_upper = abs(best_q - float(Q_SEARCH_MAX)) <= max(1e-20, abs(Q_SEARCH_MAX) * 1e-9)
        boundary_note = " [BOUNDARY]" if (at_lower or at_upper) else ""
        print(
            f"[QFIT {progress_label}] done best objective={best_score:.6f} q={best_q:.3e} obs={best_obs}{boundary_note}",
            flush=True,
        )

    return best_q, best_score, best_obs
