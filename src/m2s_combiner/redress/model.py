from __future__ import annotations

import json
import math
import os
from multiprocessing import Pool
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import differential_evolution
from scipy.optimize import minimize

from .common import race_num
from .constants import EPS
from .constants import Q_GAMMA_SEARCH_MAX
from .constants import Q_GAMMA_SEARCH_MIN
from .constants import GROUP_Q_CACHE_FILENAME
from .constants import MAX_DELTA_T_DAYS
from .constants import MU_Y_LOG_MAX
from .constants import MU_Y_LOG_MIN
from .constants import NON_OBS_STATUSES
from .constants import P_COV_CAP_FLOOR
from .constants import P0_SCALE_SEARCH_MAX
from .constants import P0_SCALE_SEARCH_MIN
from .constants import Q_OBJECTIVE_CHOICES
from .constants import Q_SEARCH_MAX
from .constants import Q_SEARCH_MIN
from .constants import R_T_SEARCH_MAX
from .constants import R_T_SEARCH_MIN

LOG_EXP_MAX = math.log(np.finfo(float).max)
LOG_EXP_MIN = math.log(np.nextafter(0.0, 1.0))


def _safe_exp_scalar(log_value: float) -> float:
    value = float(log_value)
    if not np.isfinite(value):
        return float("nan")
    clipped = float(min(LOG_EXP_MAX, max(LOG_EXP_MIN, value)))
    return float(math.exp(clipped))


def _gamma_loading_from_r_t(r_t: float) -> float:
    safe_r_t = float(max(EPS, float(r_t)))
    return float(math.sqrt(safe_r_t))


@dataclass
class BoatState:
    x: float
    p: float
    gamma: float
    p_gamma: float
    last_state_date: datetime | None


@dataclass
class GroupFilterResult:
    history: pd.DataFrame
    nll_sum: float
    observed_count: int
    informed_sq_error_sum: float
    informed_error_count: int
    loo_sq_error_sum: float
    loo_error_count: int
    terminal_state_by_competitor: dict[str, BoatState]


@dataclass
class _DEObjectiveContext:
    groups: list[dict[str, Any]]
    objective: str
    log_q_min: float
    log_q_max: float
    q_gamma_min: float
    q_gamma_max: float
    log_k_min: float
    log_k_max: float
    x0_fixed: float
    initial_state_by_group: dict[str, dict[str, BoatState]]


_DE_CONTEXT: _DEObjectiveContext | None = None


def _de_worker_init(context: _DEObjectiveContext) -> None:
    global _DE_CONTEXT
    _DE_CONTEXT = context


def _de_worker_objective(theta: np.ndarray) -> float:
    context = _DE_CONTEXT
    if context is None:
        return 1e300
    try:
        theta_vec = np.asarray(theta, dtype=float)
        log_q = float(max(context.log_q_min, min(context.log_q_max, float(theta_vec[0]))))
        log_q_gamma = float(max(context.q_gamma_min, min(context.q_gamma_max, float(theta_vec[1]))))
        log_k = float(max(context.log_k_min, min(context.log_k_max, float(theta_vec[2]))))
        q_value = float(math.exp(log_q))
        q_gamma = float(math.exp(log_q_gamma))
        k_value = float(math.exp(log_k))
        p0_for_q = float(max(EPS, k_value * q_value))
        score, _obs = evaluate_q_score(
            context.groups,
            q_value,
            context.objective,
            q_gamma=q_gamma,
            initial_x0=context.x0_fixed,
            initial_p0=p0_for_q,
            initial_state_by_group=context.initial_state_by_group,
            score_cache=None,
        )
    except Exception:
        return 1e300
    if not np.isfinite(float(score)):
        return 1e300
    return float(score)


class _DEProcessMap:
    def __init__(self, pool: Pool) -> None:
        self.pool = pool

    def __call__(self, _func: Any, iterable: Any) -> list[float]:
        theta_list = [np.asarray(theta, dtype=float) for theta in iterable]
        return self.pool.map(_de_worker_objective, theta_list)


def _state_cache_key(initial_state_by_group: dict[str, dict[str, BoatState]] | None) -> tuple[Any, ...]:
    if not initial_state_by_group:
        return ()
    return tuple(
        (
            str(group_name),
            tuple(
                (
                    str(competitor),
                    float(state.x),
                    float(state.p),
                    float(state.gamma),
                    float(state.p_gamma),
                )
                for competitor, state in sorted(group_states.items())
            ),
        )
        for group_name, group_states in sorted(initial_state_by_group.items())
    )


def _compute_terminal_states_by_group(
    groups: list[dict[str, Any]],
    q_value: float,
    *,
    q_gamma: float,
    initial_x0: float,
    initial_p0: float,
    initial_state_by_group: dict[str, dict[str, BoatState]] | None = None,
) -> dict[str, dict[str, BoatState]]:
    terminal_states_by_group: dict[str, dict[str, BoatState]] = {}
    initial_state_by_group = initial_state_by_group or {}
    for group in groups:
        group_name = str(group["group"])
        result = run_group_filter(
            group,
            q_value,
            q_gamma=q_gamma,
            initial_x0=initial_x0,
            initial_p0=initial_p0,
            initial_state_by_competitor=initial_state_by_group.get(group_name),
            compute_loo_predictions=False,
            collect_history=False,
        )
        terminal_states_by_group[group_name] = dict(result.terminal_state_by_competitor)
    return terminal_states_by_group


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


def load_group_qa_cache(path: Path) -> tuple[dict[str, float], dict[str, float], dict[str, float], dict[str, dict[str, BoatState]]]:
    if not path.exists():
        return {}, {}, {}, {}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"Could not read Q cache file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid Q cache JSON in {path}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"Invalid Q cache payload in {path}")
    if not isinstance(payload.get("group_q"), dict) or not isinstance(payload.get("group_q_gamma"), dict) or not isinstance(payload.get("group_k"), dict):
        return {}, {}, {}, {}
    q_source = payload["group_q"]
    q_gamma_source = payload["group_q_gamma"]
    k_source = payload["group_k"]
    state_source = payload.get("group_initial_state") if isinstance(payload.get("group_initial_state"), dict) else {}

    q_map: dict[str, float] = {}
    for key, value in q_source.items():
        q_val = float(value)
        if np.isfinite(q_val) and q_val > 0.0:
            q_map[str(key)] = q_val
    q_gamma_map: dict[str, float] = {}
    for key, value in q_gamma_source.items():
        q_gamma_val = float(value)
        if np.isfinite(q_gamma_val):
            q_gamma_map[str(key)] = float(q_gamma_val)
    k_map: dict[str, float] = {}
    for key, value in k_source.items():
        k_val = float(value)
        if np.isfinite(k_val) and k_val > 0.0:
            k_map[str(key)] = float(k_val)
    initial_state_by_group: dict[str, dict[str, BoatState]] = {}
    for group_name, group_payload in state_source.items():
        if not isinstance(group_payload, dict):
            continue
        group_states: dict[str, BoatState] = {}
        for competitor, state_payload in group_payload.items():
            if not isinstance(state_payload, dict):
                continue
            try:
                state = BoatState(
                    x=float(state_payload.get("x", 0.0)),
                    p=float(max(EPS, float(state_payload.get("p", P_COV_CAP_FLOOR)))),
                    gamma=float(state_payload.get("gamma", 0.0)),
                    p_gamma=float(max(EPS, float(state_payload.get("p_gamma", P_COV_CAP_FLOOR)))),
                    last_state_date=None,
                )
            except (TypeError, ValueError):
                continue
            group_states[str(competitor)] = state
        if group_states:
            initial_state_by_group[str(group_name)] = group_states
    return q_map, q_gamma_map, k_map, initial_state_by_group


def load_group_q_cache(path: Path) -> dict[str, float]:
    q_map, _, _, _ = load_group_qa_cache(path)
    return q_map


def save_group_qa_cache(
    path: Path,
    q_by_group: dict[str, float],
    q_gamma_by_group: dict[str, float] | None = None,
    k_by_group: dict[str, float] | None = None,
    initial_state_by_group: dict[str, dict[str, BoatState]] | None = None,
) -> None:
    q_gamma_by_group = q_gamma_by_group or {}
    k_by_group = k_by_group or {}
    initial_state_by_group = initial_state_by_group or {}
    payload = {
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "group_q": {str(k): float(v) for k, v in sorted(q_by_group.items())},
        "group_q_gamma": {str(k): float(v) for k, v in sorted(q_gamma_by_group.items()) if np.isfinite(float(v))},
        "group_k": {str(k): float(v) for k, v in sorted(k_by_group.items()) if np.isfinite(float(v)) and float(v) > 0.0},
        "group_initial_state": {
            str(group_name): {
                str(competitor): {
                    "x": float(state.x),
                    "p": float(max(EPS, state.p)),
                    "gamma": float(state.gamma),
                    "p_gamma": float(max(EPS, state.p_gamma)),
                }
                for competitor, state in sorted(group_states.items())
            }
            for group_name, group_states in sorted(initial_state_by_group.items())
        },
    }
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(serialized, encoding="utf-8")
    tmp_path.replace(path)


def save_group_q_cache(path: Path, q_by_group: dict[str, float]) -> None:
    save_group_qa_cache(path, q_by_group, {}, {}, {})


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

    race_dates_by_label: dict[str, datetime] = {}
    for race_label in group.get("selected_races", []):
        label = str(race_label)
        race_date = group.get("race_dates", {}).get(race_label)
        if race_date is None:
            race_date = group.get("race_dates", {}).get(label)
        if race_date is not None:
            race_dates_by_label[label] = race_date

    debut_order_by_competitor: dict[str, int] = {}
    debut_date_by_competitor: dict[str, datetime] = {}
    for race_label in group.get("selected_races", []):
        label = str(race_label)
        race_lookup = race_lookup_by_label.get(label, {})
        race_order = race_order_by_label.get(label)
        race_date = race_dates_by_label.get(label)
        if race_order is None:
            continue
        for competitor in race_lookup:
            existing = debut_order_by_competitor.get(str(competitor))
            if existing is None or race_order < existing:
                debut_order_by_competitor[str(competitor)] = race_order
            if race_date is not None:
                existing_date = debut_date_by_competitor.get(str(competitor))
                if existing_date is None or race_date < existing_date:
                    debut_date_by_competitor[str(competitor)] = race_date

    runtime = {
        "combined": combined,
        "competitors": competitors,
        "race_rows_by_label": race_rows_by_label,
        "race_lookup_by_label": race_lookup_by_label,
        "race_order_by_label": race_order_by_label,
        "debut_order_by_competitor": debut_order_by_competitor,
        "debut_date_by_competitor": debut_date_by_competitor,
    }
    group["_runtime"] = runtime
    return runtime


def _symmetrize_and_floor_covariance(covariance: np.ndarray, *, floor: float = EPS) -> np.ndarray:
    cov = 0.5 * (np.asarray(covariance, dtype=float) + np.asarray(covariance, dtype=float).T)
    if floor > 0.0:
        cov = cov + float(floor) * np.eye(cov.shape[0], dtype=float)
    return cov


def _invert_covariance_with_jitter(
    covariance: np.ndarray,
    *,
    floor: float = EPS,
    max_attempts: int = 8,
) -> tuple[np.ndarray, np.ndarray]:
    matrix = _symmetrize_and_floor_covariance(covariance, floor=floor)
    n = matrix.shape[0]
    if n == 0:
        return matrix, matrix.copy()
    eye = np.eye(n, dtype=float)
    diag_mean = float(np.mean(np.diag(matrix))) if n > 0 else 1.0
    jitter_base = float(max(floor, EPS, 1e-12 * max(1.0, abs(diag_mean))))
    attempt_matrix = matrix

    for attempt in range(max_attempts):
        try:
            inv = np.linalg.inv(attempt_matrix)
            if np.all(np.isfinite(inv)):
                return attempt_matrix, inv
        except np.linalg.LinAlgError:
            pass
        jitter = float(jitter_base * (10.0 ** attempt))
        attempt_matrix = _symmetrize_and_floor_covariance(matrix + jitter * eye, floor=0.0)

    inv = np.linalg.pinv(attempt_matrix, rcond=1e-12)
    if not np.all(np.isfinite(inv)):
        raise np.linalg.LinAlgError("Failed to invert covariance matrix after jitter and pseudo-inverse fallback.")
    return attempt_matrix, inv


def _apply_state_transition(
    state_mean: np.ndarray,
    state_cov: np.ndarray,
    *,
    process_diag: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    prior_mean = np.asarray(state_mean, dtype=float).copy()
    prior_cov = np.asarray(state_cov, dtype=float) + np.diag(np.asarray(process_diag, dtype=float))
    prior_cov = _symmetrize_and_floor_covariance(prior_cov)
    return prior_mean, prior_cov


def _active_indices_from_flags(
    competitors: list[str],
    competitor_index: dict[str, int],
    active_by_competitor: dict[str, bool],
) -> np.ndarray:
    active_indices = [competitor_index[c] for c in competitors if bool(active_by_competitor.get(c, False))]
    return np.asarray(active_indices, dtype=int)


def _project_active_vector_zero_sum(values: np.ndarray, active_indices: np.ndarray) -> np.ndarray:
    projected = np.asarray(values, dtype=float).copy()
    if active_indices.size <= 1:
        return projected
    active_vals = projected[active_indices]
    projected[active_indices] = active_vals - float(np.mean(active_vals))
    return projected


def _project_active_covariance_zero_sum(matrix: np.ndarray, active_indices: np.ndarray) -> np.ndarray:
    projected = np.asarray(matrix, dtype=float).copy()
    if active_indices.size <= 1:
        return projected
    n = projected.shape[0]
    active = np.asarray(active_indices, dtype=int)
    active_set = set(int(x) for x in active.tolist())
    other = np.asarray([idx for idx in range(n) if idx not in active_set], dtype=int)

    aa = projected[np.ix_(active, active)]
    row_mean = aa.mean(axis=1, keepdims=True)
    col_mean = aa.mean(axis=0, keepdims=True)
    grand_mean = float(aa.mean())
    projected[np.ix_(active, active)] = aa - row_mean - col_mean + grand_mean

    if other.size > 0:
        ao = projected[np.ix_(active, other)]
        projected[np.ix_(active, other)] = ao - ao.mean(axis=0, keepdims=True)
        oa = projected[np.ix_(other, active)]
        projected[np.ix_(other, active)] = oa - oa.mean(axis=1, keepdims=True)

    projected = 0.5 * (projected + projected.T)
    return projected


def _profiled_day_nll_and_grad_sigma2(
    measurement_variance: float,
    centered_values: np.ndarray,
    prior_covariance: np.ndarray,
) -> tuple[float, float, float]:
    m = centered_values.size
    if m == 0:
        return 0.0, float("nan"), 0.0
    sigma2 = float(max(EPS, measurement_variance))
    try:
        s_matrix, s_inv = _invert_covariance_with_jitter(prior_covariance + sigma2 * np.eye(m, dtype=float))
    except np.linalg.LinAlgError:
        return float("inf"), float("nan"), 0.0
    sign, logdet = np.linalg.slogdet(s_matrix)
    if sign <= 0 or not np.isfinite(logdet):
        return float("inf"), float("nan"), 0.0
    ones = np.ones(m, dtype=float)
    inv_times_ones = s_inv @ ones
    inv_times_centered = s_inv @ centered_values
    denom = float(ones @ inv_times_ones)
    if not np.isfinite(denom) or denom <= EPS:
        return float("inf"), float("nan"), 0.0
    mu_hat_unclipped = float((ones @ inv_times_centered) / denom)
    mu_hat = float(min(MU_Y_LOG_MAX, max(MU_Y_LOG_MIN, mu_hat_unclipped)))
    residual = centered_values - mu_hat * ones
    inv_times_residual = s_inv @ residual
    quad = float(residual @ inv_times_residual)
    nll = 0.5 * float(m * math.log(2.0 * math.pi) + logdet + quad)
    trace_s_inv = float(np.trace(s_inv))
    grad_sigma2 = 0.5 * (trace_s_inv - float(inv_times_residual @ inv_times_residual))
    if not np.isfinite(grad_sigma2):
        grad_sigma2 = 0.0
    return nll, mu_hat, float(grad_sigma2)


def _profiled_day_nll_full(measurement_variance: float, centered_values: np.ndarray, prior_covariance: np.ndarray) -> tuple[float, float]:
    nll, mu_hat, _ = _profiled_day_nll_and_grad_sigma2(measurement_variance, centered_values, prior_covariance)
    return float(nll), float(mu_hat)


def fit_measurement_variance_full(
    centered_values: np.ndarray,
    prior_covariance: np.ndarray,
    *,
    warm_start_sigma2: float | None = None,
    warm_start_only: bool = False,
) -> float:
    if centered_values.size == 0:
        return float("nan")

    lower = float(max(EPS, R_T_SEARCH_MIN))
    upper_bound = float(max(lower * (1.0 + 1e-12), R_T_SEARCH_MAX))
    m = centered_values.size
    diag_mean = float(np.mean(np.diag(prior_covariance))) if m > 0 else lower
    base_scale_raw = max(lower, float(np.var(centered_values, ddof=0)), float(np.mean(np.square(centered_values))), diag_mean)
    base_scale = float(max(lower, min(upper_bound, base_scale_raw)))
    upper = float(max(lower * 16.0, base_scale))
    upper = float(min(upper_bound, upper))
    upper_nll, _, _ = _profiled_day_nll_and_grad_sigma2(upper, centered_values, prior_covariance)
    for _ in range(10):
        if upper >= upper_bound:
            break
        candidate_upper = float(min(upper_bound, upper * 4.0))
        if candidate_upper <= upper:
            break
        candidate_nll, _, _ = _profiled_day_nll_and_grad_sigma2(candidate_upper, centered_values, prior_covariance)
        if candidate_nll < upper_nll:
            upper = candidate_upper
            upper_nll = candidate_nll
        else:
            break
    log_lower = float(math.log(lower))
    log_upper = float(math.log(max(lower * (1.0 + 1e-12), upper)))
    log_initial = float(math.log(max(lower, min(upper, base_scale))))

    def _objective_and_grad_log_sigma2(theta: np.ndarray) -> tuple[float, np.ndarray]:
        log_sigma2 = float(max(log_lower, min(log_upper, float(theta[0]))))
        sigma2 = float(math.exp(log_sigma2))
        nll, _mu_hat, grad_sigma2 = _profiled_day_nll_and_grad_sigma2(sigma2, centered_values, prior_covariance)
        if not np.isfinite(nll):
            return 1e300, np.array([0.0], dtype=float)
        grad_log_sigma2 = float(grad_sigma2 * sigma2)
        if not np.isfinite(grad_log_sigma2):
            grad_log_sigma2 = 0.0
        return float(nll), np.array([grad_log_sigma2], dtype=float)

    candidate_logs = np.linspace(log_lower, log_upper, num=9, dtype=float)
    extra_logs = [log_initial]
    if warm_start_sigma2 is not None and np.isfinite(float(warm_start_sigma2)) and float(warm_start_sigma2) > 0.0:
        warm_log = float(math.log(max(lower, min(upper, float(warm_start_sigma2)))))
        extra_logs.append(warm_log)
    candidate_logs = np.unique(np.concatenate([candidate_logs, np.asarray(extra_logs, dtype=float)]))
    if warm_start_only:
        candidate_logs = np.asarray([float(extra_logs[-1])], dtype=float)

    best_nll = float("inf")
    best_log_sigma2 = float(log_initial)

    for start_log_sigma2 in candidate_logs:
        theta0 = np.array([float(start_log_sigma2)], dtype=float)
        start_nll, _ = _objective_and_grad_log_sigma2(theta0)
        if np.isfinite(start_nll) and start_nll < best_nll:
            best_nll = float(start_nll)
            best_log_sigma2 = float(start_log_sigma2)

    result = minimize(
        lambda t: _objective_and_grad_log_sigma2(t)[0],
        x0=np.array([best_log_sigma2], dtype=float),
        method="L-BFGS-B",
        jac=lambda t: _objective_and_grad_log_sigma2(t)[1],
        bounds=[(log_lower, log_upper)],
        options={"maxiter": 80, "ftol": 1e-12},
    )

    result_log_sigma2 = float(max(log_lower, min(log_upper, float(result.x[0]))))
    result_nll, _ = _objective_and_grad_log_sigma2(np.array([result_log_sigma2], dtype=float))
    if np.isfinite(result_nll) and result_nll < best_nll:
        best_nll = float(result_nll)
        best_log_sigma2 = float(result_log_sigma2)

    sigma2_star = float(max(EPS, math.exp(best_log_sigma2)))
    return sigma2_star


def fit_day_parameters_full(
    centered_values: np.ndarray,
    prior_covariance: np.ndarray,
    *,
    warm_start_r_t: float | None = None,
    warm_start_only: bool = False,
) -> tuple[float, float, float]:
    if centered_values.size == 0:
        return float("nan"), float("nan"), 0.0
    r_t = fit_measurement_variance_full(
        centered_values,
        prior_covariance,
        warm_start_sigma2=warm_start_r_t,
        warm_start_only=warm_start_only,
    )
    nll_sum, mu_hat = _profiled_day_nll_full(r_t, centered_values, prior_covariance)
    return mu_hat, r_t, nll_sum


def compute_observation_step(
    observed: pd.DataFrame,
    competitors: list[str],
    competitor_index: dict[str, int],
    prior_mean: np.ndarray,
    prior_covariance: np.ndarray,
    prior_gamma_mean: np.ndarray,
    prior_gamma_covariance: np.ndarray,
    *,
    compute_loo_predictions: bool = False,
) -> dict[str, Any]:
    if observed.empty:
        return {
            "b_hat": float("nan"),
            "r_t": float("nan"),
            "innovation_by_competitor": {},
            "gain_by_competitor": {},
            "gain_gamma_by_competitor": {},
            "y_pred_loo_by_competitor": {},
            "post_mean": prior_mean,
            "post_covariance": prior_covariance,
            "post_gamma_mean": prior_gamma_mean,
            "post_gamma_covariance": prior_gamma_covariance,
            "nll_sum": 0.0,
            "observed_count": 0,
            "informed_sq_error_sum": 0.0,
            "informed_error_count": 0,
            "loo_sq_error_sum": 0.0,
            "loo_error_count": 0,
        }
    observed_competitors = observed["competitor"].astype(str).tolist()
    observed_indices = np.array([competitor_index[c] for c in observed_competitors], dtype=int)
    beregnet_seconds = observed["beregnet_seconds"].to_numpy(dtype=float)
    y_values = np.log(np.clip(beregnet_seconds, EPS, None))

    x_prior_obs = prior_mean[observed_indices]
    p_prior_obs = prior_covariance[np.ix_(observed_indices, observed_indices)]
    centered_values_base = y_values - x_prior_obs
    b_hat, r_t, nll_sum = fit_day_parameters_full(centered_values_base, p_prior_obs)
    if not np.isfinite(float(b_hat)) or not np.isfinite(float(r_t)):
        return {
            "b_hat": float("nan"),
            "r_t": float("nan"),
            "innovation_by_competitor": {},
            "gain_by_competitor": {},
            "gain_gamma_by_competitor": {},
            "y_pred_loo_by_competitor": {},
            "post_mean": prior_mean,
            "post_covariance": prior_covariance,
            "post_gamma_mean": prior_gamma_mean,
            "post_gamma_covariance": prior_gamma_covariance,
            "nll_sum": float("inf"),
            "observed_count": len(observed_competitors),
            "informed_sq_error_sum": 0.0,
            "informed_error_count": 0,
            "loo_sq_error_sum": 0.0,
            "loo_error_count": 0,
        }

    gamma_prior_obs = prior_gamma_mean[observed_indices]
    p_gamma_prior_obs = prior_gamma_covariance[np.ix_(observed_indices, observed_indices)]
    h = _gamma_loading_from_r_t(r_t)
    p_meas = p_prior_obs + (h * h) * p_gamma_prior_obs
    centered_values = y_values - x_prior_obs - h * gamma_prior_obs
    innovations = centered_values - b_hat

    try:
        s_matrix, s_inv = _invert_covariance_with_jitter(p_meas + float(r_t) * np.eye(observed_indices.size, dtype=float))
    except np.linalg.LinAlgError:
        return {
            "b_hat": float("nan"),
            "r_t": float("nan"),
            "innovation_by_competitor": {},
            "gain_by_competitor": {},
            "gain_gamma_by_competitor": {},
            "y_pred_loo_by_competitor": {},
            "post_mean": prior_mean,
            "post_covariance": prior_covariance,
            "post_gamma_mean": prior_gamma_mean,
            "post_gamma_covariance": prior_gamma_covariance,
            "nll_sum": float("inf"),
            "observed_count": len(observed_competitors),
            "informed_sq_error_sum": 0.0,
            "informed_error_count": 0,
            "loo_sq_error_sum": 0.0,
            "loo_error_count": 0,
        }
    p_cross = prior_covariance[:, observed_indices]
    p_gamma_cross = prior_gamma_covariance[:, observed_indices]
    k_matrix_x = p_cross @ s_inv
    k_matrix_gamma = (h * p_gamma_cross) @ s_inv
    sign, logdet = np.linalg.slogdet(s_matrix)
    if sign <= 0 or not np.isfinite(logdet):
        return {
            "b_hat": float("nan"),
            "r_t": float("nan"),
            "innovation_by_competitor": {},
            "gain_by_competitor": {},
            "gain_gamma_by_competitor": {},
            "y_pred_loo_by_competitor": {},
            "post_mean": prior_mean,
            "post_covariance": prior_covariance,
            "post_gamma_mean": prior_gamma_mean,
            "post_gamma_covariance": prior_gamma_covariance,
            "nll_sum": float("inf"),
            "observed_count": len(observed_competitors),
            "informed_sq_error_sum": 0.0,
            "informed_error_count": 0,
            "loo_sq_error_sum": 0.0,
            "loo_error_count": 0,
        }
    inv_times_residual = s_inv @ innovations
    quad = float(innovations @ inv_times_residual)
    nll_sum = 0.5 * float(observed_indices.size * math.log(2.0 * math.pi) + logdet + quad)
    post_mean = prior_mean + (k_matrix_x @ innovations)
    post_covariance = prior_covariance - (k_matrix_x @ p_cross.T)
    post_gamma_mean = prior_gamma_mean + (k_matrix_gamma @ innovations)
    post_gamma_covariance = prior_gamma_covariance - (k_matrix_gamma @ (h * p_gamma_cross.T))
    post_covariance = _symmetrize_and_floor_covariance(post_covariance)
    post_gamma_covariance = _symmetrize_and_floor_covariance(post_gamma_covariance)

    innovation_by_competitor = {competitor: float(innovations[idx]) for idx, competitor in enumerate(observed_competitors)}
    gain_by_competitor: dict[str, float] = {}
    gain_gamma_by_competitor: dict[str, float] = {}
    for local_idx, competitor in enumerate(observed_competitors):
        global_idx = int(observed_indices[local_idx])
        gain_by_competitor[competitor] = float(k_matrix_x[global_idx, local_idx])
        gain_gamma_by_competitor[competitor] = float(k_matrix_gamma[global_idx, local_idx])

    y_pred_loo_by_competitor: dict[str, float] = {}
    if compute_loo_predictions:
        if len(observed_competitors) == 1:
            # No valid leave-one-out estimate exists with zero boats left after holdout.
            y_pred_loo_by_competitor[observed_competitors[0]] = float("nan")
        else:
            for local_idx, competitor in enumerate(observed_competitors):
                mask = np.ones(len(observed_competitors), dtype=bool)
                mask[local_idx] = False
                x_prior_loo = x_prior_obs[mask]
                gamma_prior_loo = gamma_prior_obs[mask]
                p_prior_loo = p_prior_obs[np.ix_(mask, mask)]
                centered_values_loo = y_values[mask] - x_prior_loo
                b_hat_loo, r_t_loo, _ = fit_day_parameters_full(
                    centered_values_loo,
                    p_prior_loo,
                    warm_start_r_t=r_t,
                    warm_start_only=False,
                )
                if not np.isfinite(float(b_hat_loo)) or not np.isfinite(float(r_t_loo)):
                    y_pred_loo_by_competitor[competitor] = float("nan")
                    continue
                h_loo = _gamma_loading_from_r_t(r_t_loo)
                y_pred_loo_by_competitor[competitor] = float(
                    b_hat_loo + x_prior_obs[local_idx] + h_loo * gamma_prior_obs[local_idx]
                )

    loo_sq_error_sum = 0.0
    loo_error_count = 0
    informed_sq_error_sum = 0.0
    informed_error_count = 0
    y_pred_full_obs = b_hat + x_prior_obs + h * gamma_prior_obs
    for observed_seconds, y_pred_full in zip(beregnet_seconds, y_pred_full_obs):
        pred_seconds = _safe_exp_scalar(float(y_pred_full))
        if np.isfinite(pred_seconds):
            error = float(observed_seconds - pred_seconds)
            informed_sq_error_sum += error * error
            informed_error_count += 1
    if compute_loo_predictions:
        for competitor, observed_seconds in zip(observed_competitors, beregnet_seconds):
            pred_seconds = _safe_exp_scalar(y_pred_loo_by_competitor[competitor])
            if np.isfinite(pred_seconds):
                error = float(observed_seconds - pred_seconds)
                loo_sq_error_sum += error * error
                loo_error_count += 1

    return {
        "b_hat": float(b_hat),
        "r_t": float(r_t),
        "innovation_by_competitor": innovation_by_competitor,
        "gain_by_competitor": gain_by_competitor,
        "gain_gamma_by_competitor": gain_gamma_by_competitor,
        "y_pred_loo_by_competitor": y_pred_loo_by_competitor,
        "post_mean": post_mean,
        "post_covariance": post_covariance,
        "post_gamma_mean": post_gamma_mean,
        "post_gamma_covariance": post_gamma_covariance,
        "nll_sum": float(nll_sum),
        "observed_count": len(observed_competitors),
        "informed_sq_error_sum": float(informed_sq_error_sum),
        "informed_error_count": int(informed_error_count),
        "loo_sq_error_sum": float(loo_sq_error_sum),
        "loo_error_count": int(loo_error_count),
    }


def run_group_filter(
    group: dict[str, Any],
    global_q: float,
    *,
    q_gamma: float = 1e-6,
    initial_x0: float = 0.0,
    initial_p0: float = P_COV_CAP_FLOOR,
    initial_state_by_competitor: dict[str, BoatState] | None = None,
    compute_loo_predictions: bool = False,
    collect_history: bool = True,
) -> GroupFilterResult:
    runtime = prepare_group_runtime(group)
    selected_races: list[str] = list(group["selected_races"])
    race_dates: dict[str, datetime] = dict(group["race_dates"])
    group_label = str(group["group"])
    q_gamma_value = float(max(EPS, q_gamma))

    competitors: list[str] = runtime["competitors"]
    race_rows_by_label: dict[str, pd.DataFrame] = runtime["race_rows_by_label"]
    race_lookup_by_label: dict[str, dict[str, dict[str, Any]]] = runtime["race_lookup_by_label"]
    debut_date_by_competitor: dict[str, datetime] = runtime.get("debut_date_by_competitor", {})
    initial_p0 = float(max(EPS, initial_p0))
    initial_state_by_competitor = initial_state_by_competitor or {}
    competitor_index = {competitor: idx for idx, competitor in enumerate(competitors)}
    n_competitors = len(competitors)
    state_mean = np.zeros(n_competitors, dtype=float)
    state_cov = np.eye(n_competitors, dtype=float) * EPS
    state_gamma_mean = np.zeros(n_competitors, dtype=float)
    state_gamma_cov = np.eye(n_competitors, dtype=float) * EPS
    active_by_competitor = {competitor: False for competitor in competitors}
    last_state_date_by_competitor = {competitor: None for competitor in competitors}

    history_rows: list[dict[str, Any]] = []
    nll_sum = 0.0
    observed_count = 0
    informed_sq_error_sum = 0.0
    informed_error_count = 0
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
        debut_indices_this_race: set[int] = set()
        for competitor in competitors:
            if debut_date_by_competitor.get(competitor) != race_date:
                continue
            if competitor not in race_lookup:
                continue
            idx = competitor_index[competitor]
            debut_indices_this_race.add(int(idx))
            if not active_by_competitor[competitor]:
                initial_state = initial_state_by_competitor.get(competitor)
                state_mean[idx] = float(initial_x0 if initial_state is None else initial_state.x)
                state_cov[idx, :] = 0.0
                state_cov[:, idx] = 0.0
                state_cov[idx, idx] = float(initial_p0 if initial_state is None else max(EPS, initial_state.p))
                state_gamma_mean[idx] = 0.0 if initial_state is None else float(initial_state.gamma)
                state_gamma_cov[idx, :] = 0.0
                state_gamma_cov[:, idx] = 0.0
                state_gamma_cov[idx, idx] = float(initial_p0 if initial_state is None else max(EPS, initial_state.p_gamma))
                last_state_date_by_competitor[competitor] = None
                active_by_competitor[competitor] = True

        delta_days_by_competitor: dict[str, int] = {}
        process_diag = np.zeros(n_competitors, dtype=float)
        process_diag_gamma = np.zeros(n_competitors, dtype=float)
        for competitor in competitors:
            if not active_by_competitor[competitor]:
                delta_days_by_competitor[competitor] = 0
                continue
            last_state_date = last_state_date_by_competitor[competitor]
            delta_days = 0 if last_state_date is None else min(MAX_DELTA_T_DAYS, max(0, (race_date - last_state_date).days))
            delta_days_by_competitor[competitor] = int(delta_days)
            process_diag[competitor_index[competitor]] = float(delta_days) * float(global_q)
            process_diag_gamma[competitor_index[competitor]] = float(delta_days) * float(q_gamma_value)
        active_indices = _active_indices_from_flags(competitors, competitor_index, active_by_competitor)
        prior_mean, prior_cov = _apply_state_transition(
            state_mean,
            state_cov,
            process_diag=process_diag,
        )
        prior_gamma_mean, prior_gamma_cov = _apply_state_transition(
            state_gamma_mean,
            state_gamma_cov,
            process_diag=process_diag_gamma,
        )
        prior_mean = _project_active_vector_zero_sum(prior_mean, active_indices)
        prior_cov = _symmetrize_and_floor_covariance(_project_active_covariance_zero_sum(prior_cov, active_indices))
        prior_gamma_mean = np.asarray(prior_gamma_mean, dtype=float).copy()
        prior_gamma_cov = _symmetrize_and_floor_covariance(prior_gamma_cov)
        for idx in debut_indices_this_race:
            competitor = competitors[int(idx)]
            initial_state = initial_state_by_competitor.get(competitor)
            prior_mean[int(idx)] = float(initial_x0 if initial_state is None else initial_state.x)
            if initial_state is not None:
                prior_gamma_mean[int(idx)] = float(initial_state.gamma)
        obs_mask = race_rows["beregnet_seconds"].notna() & (~race_rows["status_upper"].isin(NON_OBS_STATUSES))
        observed = race_rows.loc[obs_mask, ["competitor", "beregnet_seconds"]].copy()
        observed_step = compute_observation_step(
            observed,
            competitors,
            competitor_index,
            prior_mean,
            prior_cov,
            prior_gamma_mean,
            prior_gamma_cov,
            compute_loo_predictions=compute_loo_predictions,
        )
        b_hat = float(observed_step["b_hat"])
        r_t = float(observed_step["r_t"])
        innovation_by_competitor = observed_step["innovation_by_competitor"]
        gain_by_competitor = observed_step["gain_by_competitor"]
        gain_gamma_by_competitor = observed_step["gain_gamma_by_competitor"]
        y_pred_loo_by_competitor = observed_step["y_pred_loo_by_competitor"]
        nll_sum += float(observed_step["nll_sum"])
        observed_count += int(observed_step["observed_count"])
        informed_sq_error_sum += float(observed_step["informed_sq_error_sum"])
        informed_error_count += int(observed_step["informed_error_count"])
        loo_sq_error_sum += float(observed_step["loo_sq_error_sum"])
        loo_error_count += int(observed_step["loo_error_count"])
        observed_competitors = set(observed["competitor"].astype(str).tolist()) if not observed.empty else set()
        state_mean = np.asarray(observed_step["post_mean"], dtype=float).copy()
        state_cov = _symmetrize_and_floor_covariance(np.asarray(observed_step["post_covariance"], dtype=float))
        state_gamma_mean = np.asarray(observed_step["post_gamma_mean"], dtype=float).copy()
        state_gamma_cov = _symmetrize_and_floor_covariance(np.asarray(observed_step["post_gamma_covariance"], dtype=float))
        state_mean = _project_active_vector_zero_sum(state_mean, active_indices)
        state_cov = _symmetrize_and_floor_covariance(_project_active_covariance_zero_sum(state_cov, active_indices))
        state_gamma_mean = np.asarray(state_gamma_mean, dtype=float).copy()
        state_gamma_cov = _symmetrize_and_floor_covariance(state_gamma_cov)

        row_payload_by_competitor: dict[str, dict[str, Any]] = {}
        for competitor in competitors:
            if active_by_competitor[competitor]:
                last_state_date_by_competitor[competitor] = race_date

        for competitor in competitors:
            idx = competitor_index[competitor]
            is_active = bool(active_by_competitor[competitor])
            x_prior = float(prior_mean[idx]) if is_active else float("nan")
            p_prior = float(prior_cov[idx, idx]) if is_active else float("nan")
            gamma_prior = float(prior_gamma_mean[idx]) if is_active else float("nan")
            p_gamma_prior = float(prior_gamma_cov[idx, idx]) if is_active else float("nan")
            x_post = float(state_mean[idx]) if is_active else float("nan")
            p_post = float(state_cov[idx, idx]) if is_active else float("nan")
            gamma_post = float(state_gamma_mean[idx]) if is_active else float("nan")
            p_gamma_post = float(state_gamma_cov[idx, idx]) if is_active else float("nan")
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
                gain_gamma = float(gain_gamma_by_competitor[competitor])
                observed_flag = True
                y_pred_loo = float(y_pred_loo_by_competitor.get(competitor, float("nan")))
            else:
                innovation = float("nan")
                gain = float("nan")
                gain_gamma = float("nan")
                observed_flag = False
                y_pred_loo = float("nan")

            row_payload_by_competitor[competitor] = {
                "x_prior": float(x_prior),
                "p_prior": float(p_prior),
                "gamma_prior": float(gamma_prior),
                "p_gamma_prior": float(p_gamma_prior),
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
                "gain_gamma": float(gain_gamma),
                "observed_flag": bool(observed_flag),
                "y_pred_loo": float(y_pred_loo),
                "x_post": float(x_post),
                "p_post": float(p_post),
                "gamma_post": float(gamma_post),
                "p_gamma_post": float(p_gamma_post),
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
                        "global_q_gamma": q_gamma_value,
                        "x0_group": 0.0,
                        "x_prior": row_payload["x_prior"],
                        "p_prior": row_payload["p_prior"],
                        "gamma_prior": row_payload["gamma_prior"],
                        "p_gamma_prior": row_payload["p_gamma_prior"],
                        "r_t": r_t,
                        "innovation": row_payload["innovation"],
                        "kalman_gain": row_payload["gain"],
                        "kalman_gain_gamma": row_payload["gain_gamma"],
                        "y_pred_loo": row_payload["y_pred_loo"],
                        "x_post": row_payload["x_post"],
                        "p_post": row_payload["p_post"],
                        "gamma_post": row_payload["gamma_post"],
                        "p_gamma_post": row_payload["p_gamma_post"],
                    }
                )

    history = pd.DataFrame(history_rows) if collect_history else pd.DataFrame()
    terminal_state_by_competitor: dict[str, BoatState] = {}
    for competitor in competitors:
        if not active_by_competitor[competitor]:
            continue
        idx = competitor_index[competitor]
        terminal_state_by_competitor[competitor] = BoatState(
            x=float(state_mean[idx]),
            p=float(max(EPS, state_cov[idx, idx])),
            gamma=float(state_gamma_mean[idx]),
            p_gamma=float(max(EPS, state_gamma_cov[idx, idx])),
            last_state_date=last_state_date_by_competitor[competitor],
        )
    return GroupFilterResult(
        history=history,
        nll_sum=nll_sum,
        observed_count=observed_count,
        informed_sq_error_sum=informed_sq_error_sum,
        informed_error_count=informed_error_count,
        loo_sq_error_sum=loo_sq_error_sum,
        loo_error_count=loo_error_count,
        terminal_state_by_competitor=terminal_state_by_competitor,
    )


def run_all_groups_with_transfer(
    groups: list[dict[str, Any]],
    q_by_group: dict[str, float],
    competitor_year_group: dict[tuple[str, int], str],
    *,
    q_gamma_by_group: dict[str, float] | None = None,
    initial_x0_by_group: dict[str, float] | None = None,
    initial_p0_by_group: dict[str, float] | None = None,
    initial_state_by_group: dict[str, dict[str, BoatState]] | None = None,
    compute_loo_predictions: bool = False,
    collect_history: bool = True,
) -> tuple[pd.DataFrame, dict[str, float], dict[str, int]]:
    if not groups:
        return pd.DataFrame(), {}, {}

    groups_by_name = {str(group["group"]): group for group in groups}
    initial_p0_by_group = initial_p0_by_group or {}
    initial_state_by_group = initial_state_by_group or {}
    state_mean_by_group: dict[str, np.ndarray] = {}
    state_cov_by_group: dict[str, np.ndarray] = {}
    state_gamma_mean_by_group: dict[str, np.ndarray] = {}
    state_gamma_cov_by_group: dict[str, np.ndarray] = {}
    last_state_date_by_group: dict[str, dict[str, datetime | None]] = {}
    active_by_group: dict[str, dict[str, bool]] = {}
    competitors_by_group: dict[str, list[str]] = {}
    competitor_index_by_group: dict[str, dict[str, int]] = {}
    debut_date_by_group: dict[str, dict[str, datetime]] = {}
    events: list[dict[str, Any]] = []

    initial_x0_by_group = initial_x0_by_group or {}
    q_gamma_by_group = q_gamma_by_group or {}
    for group_name, group in groups_by_name.items():
        runtime = prepare_group_runtime(group)
        competitors = runtime["competitors"]
        competitors_by_group[group_name] = competitors
        competitor_index_by_group[group_name] = {competitor: idx for idx, competitor in enumerate(competitors)}
        debut_date_by_group[group_name] = dict(runtime.get("debut_date_by_competitor", {}))
        initial_p0 = float(max(EPS, initial_p0_by_group.get(group_name, P_COV_CAP_FLOOR)))
        n_competitors = len(competitors)
        initial_mean = np.zeros(n_competitors, dtype=float)
        initial_cov = np.eye(n_competitors, dtype=float) * EPS
        initial_gamma_mean = np.zeros(n_competitors, dtype=float)
        initial_gamma_cov = np.eye(n_competitors, dtype=float) * EPS
        state_mean_by_group[group_name] = initial_mean
        state_cov_by_group[group_name] = initial_cov
        state_gamma_mean_by_group[group_name] = initial_gamma_mean
        state_gamma_cov_by_group[group_name] = initial_gamma_cov
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
        q_gamma_value = float(max(EPS, q_gamma_by_group.get(group_name, 1e-6)))
        group_mean = state_mean_by_group[group_name]
        group_cov = state_cov_by_group[group_name]
        group_gamma_mean = state_gamma_mean_by_group[group_name]
        group_gamma_cov = state_gamma_cov_by_group[group_name]
        group_last_dates = last_state_date_by_group[group_name]
        group_active = active_by_group[group_name]
        competitors = competitors_by_group[group_name]
        competitor_index = competitor_index_by_group[group_name]
        debut_date_by_competitor = debut_date_by_group[group_name]
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
                group_gamma_mean[idx] = float(snapshot.gamma)
                group_gamma_cov[idx, :] = 0.0
                group_gamma_cov[:, idx] = 0.0
                group_gamma_cov[idx, idx] = float(max(EPS, snapshot.p_gamma))
                group_last_dates[competitor] = snapshot.last_state_date
                group_active[competitor] = True

        debut_indices_this_race: set[int] = set()
        initial_p0 = float(max(EPS, float(max(EPS, initial_p0_by_group.get(group_name, P_COV_CAP_FLOOR)))))
        for competitor in competitors:
            if debut_date_by_competitor.get(competitor) != race_date:
                continue
            if competitor not in race_lookup:
                continue
            idx = competitor_index[competitor]
            debut_indices_this_race.add(int(idx))
            if not group_active[competitor]:
                initial_state = initial_state_by_group.get(group_name, {}).get(competitor)
                group_mean[idx] = float(initial_x0_by_group.get(group_name, 0.0) if initial_state is None else initial_state.x)
                group_cov[idx, :] = 0.0
                group_cov[:, idx] = 0.0
                group_cov[idx, idx] = float(initial_p0 if initial_state is None else max(EPS, initial_state.p))
                group_gamma_mean[idx] = 0.0 if initial_state is None else float(initial_state.gamma)
                group_gamma_cov[idx, :] = 0.0
                group_gamma_cov[:, idx] = 0.0
                group_gamma_cov[idx, idx] = float(initial_p0 if initial_state is None else max(EPS, initial_state.p_gamma))
                group_last_dates[competitor] = None
                group_active[competitor] = True

        delta_days_by_competitor: dict[str, int] = {}
        process_diag = np.zeros(n_competitors, dtype=float)
        process_diag_gamma = np.zeros(n_competitors, dtype=float)
        for competitor in competitors:
            if not group_active[competitor]:
                delta_days_by_competitor[competitor] = 0
                continue
            last_state_date = group_last_dates[competitor]
            delta_days = 0 if last_state_date is None else min(MAX_DELTA_T_DAYS, max(0, (race_date - last_state_date).days))
            delta_days_by_competitor[competitor] = int(delta_days)
            process_diag[competitor_index[competitor]] = float(delta_days) * float(global_q)
            process_diag_gamma[competitor_index[competitor]] = float(delta_days) * float(q_gamma_value)
        active_indices = _active_indices_from_flags(competitors, competitor_index, group_active)
        prior_mean, prior_cov = _apply_state_transition(
            group_mean,
            group_cov,
            process_diag=process_diag,
        )
        prior_gamma_mean, prior_gamma_cov = _apply_state_transition(
            group_gamma_mean,
            group_gamma_cov,
            process_diag=process_diag_gamma,
        )
        prior_mean = _project_active_vector_zero_sum(prior_mean, active_indices)
        prior_cov = _symmetrize_and_floor_covariance(_project_active_covariance_zero_sum(prior_cov, active_indices))
        prior_gamma_mean = np.asarray(prior_gamma_mean, dtype=float).copy()
        prior_gamma_cov = _symmetrize_and_floor_covariance(prior_gamma_cov)
        for idx in debut_indices_this_race:
            competitor = competitors[int(idx)]
            initial_state = initial_state_by_group.get(group_name, {}).get(competitor)
            prior_mean[int(idx)] = float(initial_x0_by_group.get(group_name, 0.0) if initial_state is None else initial_state.x)
            if initial_state is not None:
                prior_gamma_mean[int(idx)] = float(initial_state.gamma)
        obs_mask = race_rows["beregnet_seconds"].notna() & (~race_rows["status_upper"].isin(NON_OBS_STATUSES))
        observed = race_rows.loc[obs_mask, ["competitor", "beregnet_seconds"]].copy()
        observed_step = compute_observation_step(
            observed,
            competitors,
            competitor_index,
            prior_mean,
            prior_cov,
            prior_gamma_mean,
            prior_gamma_cov,
            compute_loo_predictions=compute_loo_predictions,
        )
        b_hat = float(observed_step["b_hat"])
        r_t = float(observed_step["r_t"])
        innovation_by_competitor = observed_step["innovation_by_competitor"]
        gain_by_competitor = observed_step["gain_by_competitor"]
        gain_gamma_by_competitor = observed_step["gain_gamma_by_competitor"]
        y_pred_loo_by_competitor = observed_step["y_pred_loo_by_competitor"]
        nll_by_group[group_name] += float(observed_step["nll_sum"])
        obs_by_group[group_name] += int(observed_step["observed_count"])
        observed_competitors = set(observed["competitor"].astype(str).tolist()) if not observed.empty else set()
        group_mean = np.asarray(observed_step["post_mean"], dtype=float).copy()
        group_cov = _symmetrize_and_floor_covariance(np.asarray(observed_step["post_covariance"], dtype=float))
        group_gamma_mean = np.asarray(observed_step["post_gamma_mean"], dtype=float).copy()
        group_gamma_cov = _symmetrize_and_floor_covariance(np.asarray(observed_step["post_gamma_covariance"], dtype=float))
        group_mean = _project_active_vector_zero_sum(group_mean, active_indices)
        group_cov = _symmetrize_and_floor_covariance(_project_active_covariance_zero_sum(group_cov, active_indices))
        group_gamma_mean = np.asarray(group_gamma_mean, dtype=float).copy()
        group_gamma_cov = _symmetrize_and_floor_covariance(group_gamma_cov)
        state_mean_by_group[group_name] = group_mean
        state_cov_by_group[group_name] = group_cov
        state_gamma_mean_by_group[group_name] = group_gamma_mean
        state_gamma_cov_by_group[group_name] = group_gamma_cov

        row_payload_by_competitor: dict[str, dict[str, Any]] = {}
        for competitor in competitors:
            if group_active[competitor]:
                group_last_dates[competitor] = race_date

        for competitor in competitors:
            idx = competitor_index[competitor]
            is_active = bool(group_active[competitor])
            x_prior = float(prior_mean[idx]) if is_active else float("nan")
            p_prior = float(prior_cov[idx, idx]) if is_active else float("nan")
            gamma_prior = float(prior_gamma_mean[idx]) if is_active else float("nan")
            p_gamma_prior = float(prior_gamma_cov[idx, idx]) if is_active else float("nan")
            x_post = float(group_mean[idx]) if is_active else float("nan")
            p_post = float(group_cov[idx, idx]) if is_active else float("nan")
            gamma_post = float(group_gamma_mean[idx]) if is_active else float("nan")
            p_gamma_post = float(group_gamma_cov[idx, idx]) if is_active else float("nan")
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
                gain_gamma = float(gain_gamma_by_competitor[competitor])
                observed_flag = True
                y_pred_loo = float(y_pred_loo_by_competitor.get(competitor, float("nan")))
            else:
                innovation = float("nan")
                gain = float("nan")
                gain_gamma = float("nan")
                observed_flag = False
                y_pred_loo = float("nan")

            row_payload_by_competitor[competitor] = {
                "x_prior": float(x_prior),
                "p_prior": float(p_prior),
                "gamma_prior": float(gamma_prior),
                "p_gamma_prior": float(p_gamma_prior),
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
                "gain_gamma": float(gain_gamma),
                "observed_flag": bool(observed_flag),
                "y_pred_loo": float(y_pred_loo),
                "x_post": float(x_post),
                "p_post": float(p_post),
                "gamma_post": float(gamma_post),
                "p_gamma_post": float(p_gamma_post),
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
                        "global_q_gamma": q_gamma_value,
                        "x0_group": float(
                            initial_x0_by_group.get(group_name, 0.0)
                            if initial_state_by_group.get(group_name, {}).get(competitor) is None
                            else initial_state_by_group[group_name][competitor].x
                        ),
                        "p0_group": float(max(EPS, initial_p0_by_group.get(group_name, P_COV_CAP_FLOOR))),
                        "x_prior": row_payload["x_prior"],
                        "p_prior": row_payload["p_prior"],
                        "gamma_prior": row_payload["gamma_prior"],
                        "p_gamma_prior": row_payload["p_gamma_prior"],
                        "r_t": r_t,
                        "innovation": row_payload["innovation"],
                        "kalman_gain": row_payload["gain"],
                        "kalman_gain_gamma": row_payload["gain_gamma"],
                        "y_pred_loo": row_payload["y_pred_loo"],
                        "x_post": row_payload["x_post"],
                        "p_post": row_payload["p_post"],
                        "gamma_post": row_payload["gamma_post"],
                        "p_gamma_post": row_payload["p_gamma_post"],
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
                    gamma=float(group_gamma_mean[idx]),
                    p_gamma=float(group_gamma_cov[idx, idx]),
                    last_state_date=group_last_dates[competitor],
                )

    history = pd.DataFrame(history_rows) if collect_history else pd.DataFrame()
    return history, nll_by_group, obs_by_group


def q_objective_informed(
    groups: list[dict[str, Any]],
    q_value: float,
    *,
    q_gamma: float = 0.0,
    initial_x0: float = 0.0,
    initial_p0: float = P_COV_CAP_FLOOR,
    initial_state_by_group: dict[str, dict[str, BoatState]] | None = None,
) -> tuple[float, int]:
    sq_error_sum = 0.0
    obs_count = 0
    for group in groups:
        result = run_group_filter(
            group,
            q_value,
            q_gamma=q_gamma,
            initial_x0=initial_x0,
            initial_p0=initial_p0,
            initial_state_by_competitor=(initial_state_by_group or {}).get(str(group["group"])),
            compute_loo_predictions=False,
            collect_history=False,
        )
        sq_error_sum += float(result.informed_sq_error_sum)
        obs_count += int(result.informed_error_count)
    if obs_count == 0:
        return float("inf"), 0
    return float(np.sqrt(sq_error_sum / obs_count)), obs_count


def q_objective_loo(
    groups: list[dict[str, Any]],
    q_value: float,
    *,
    q_gamma: float = 0.0,
    initial_x0: float = 0.0,
    initial_p0: float = P_COV_CAP_FLOOR,
    initial_state_by_group: dict[str, dict[str, BoatState]] | None = None,
) -> tuple[float, int]:
    sq_error_sum = 0.0
    obs_count = 0
    for group in groups:
        result = run_group_filter(
            group,
            q_value,
            q_gamma=q_gamma,
            initial_x0=initial_x0,
            initial_p0=initial_p0,
            initial_state_by_competitor=(initial_state_by_group or {}).get(str(group["group"])),
            compute_loo_predictions=True,
            collect_history=False,
        )
        sq_error_sum += float(result.loo_sq_error_sum)
        obs_count += int(result.loo_error_count)
    if obs_count == 0:
        return float("inf"), 0
    return float(np.sqrt(sq_error_sum / obs_count)), obs_count


def q_objective_mle(
    groups: list[dict[str, Any]],
    q_value: float,
    *,
    q_gamma: float = 0.0,
    initial_x0: float = 0.0,
    initial_p0: float = P_COV_CAP_FLOOR,
    initial_state_by_group: dict[str, dict[str, BoatState]] | None = None,
) -> tuple[float, int]:
    nll_sum = 0.0
    obs_count = 0
    for group in groups:
        result = run_group_filter(
            group,
            q_value,
            q_gamma=q_gamma,
            initial_x0=initial_x0,
            initial_p0=initial_p0,
            initial_state_by_competitor=(initial_state_by_group or {}).get(str(group["group"])),
            compute_loo_predictions=False,
            collect_history=False,
        )
        nll_sum += float(result.nll_sum)
        obs_count += int(result.observed_count)
    if obs_count == 0:
        return float("inf"), 0
    return nll_sum, obs_count


def resolve_q_objective(value: str) -> str:
    objective = str(value or "").strip().lower()
    aliases = {
        "rmse": "rmse_loo",
        "rmse-loo": "rmse_loo",
        "one_step_rmse_loo": "rmse_loo",
        "one-step-rmse-loo": "rmse_loo",
        "rmse-informed": "rmse_informed",
        "one_step_rmse": "rmse_informed",
        "one-step-rmse": "rmse_informed",
    }
    objective = aliases.get(objective, objective)
    allowed_choices = (*Q_OBJECTIVE_CHOICES, "rmse_informed")
    if objective not in allowed_choices:
        choices = ", ".join(Q_OBJECTIVE_CHOICES)
        raise ValueError(f"Unsupported q objective '{value}'. Choose one of: {choices}")
    return objective


def evaluate_q_score(
    groups: list[dict[str, Any]],
    q_value: float,
    objective: str,
    *,
    q_gamma: float = 0.0,
    initial_x0: float = 0.0,
    initial_p0: float = P_COV_CAP_FLOOR,
    initial_state_by_group: dict[str, dict[str, BoatState]] | None = None,
    score_cache: dict[tuple[Any, ...], tuple[float, int]] | None = None,
) -> tuple[float, int]:
    objective = resolve_q_objective(objective)
    q_key = float(q_value)
    q_gamma_key = float(q_gamma)
    key = (q_key, q_gamma_key, float(initial_x0), float(initial_p0), _state_cache_key(initial_state_by_group))
    if score_cache is not None and key in score_cache:
        return score_cache[key]

    result = (
        q_objective_loo(
            groups,
            q_key,
            q_gamma=q_gamma_key,
            initial_x0=initial_x0,
            initial_p0=initial_p0,
            initial_state_by_group=initial_state_by_group,
        )
        if objective == "rmse_loo"
        else (
            q_objective_informed(
                groups,
                q_key,
                q_gamma=q_gamma_key,
                initial_x0=initial_x0,
                initial_p0=initial_p0,
                initial_state_by_group=initial_state_by_group,
            )
            if objective == "rmse_informed"
            else q_objective_mle(
                groups,
                q_key,
                q_gamma=q_gamma_key,
                initial_x0=initial_x0,
                initial_p0=initial_p0,
                initial_state_by_group=initial_state_by_group,
            )
        )
    )
    if score_cache is not None:
        score_cache[key] = result
    return result


def q_diagnostics(
    groups: list[dict[str, Any]],
    q_values: np.ndarray,
    *,
    q_gamma: float = 0.0,
    initial_x0: float = 0.0,
    initial_p0: float = P_COV_CAP_FLOOR,
    initial_state_by_group: dict[str, dict[str, BoatState]] | None = None,
    p0_from_q_scale: float | None = None,
    rmse_cache: dict[tuple[Any, ...], tuple[float, int]] | None = None,
    mle_cache: dict[tuple[Any, ...], tuple[float, int]] | None = None,
    progress_label: str | None = None,
    progress_every: int = 10,
) -> pd.DataFrame:
    progress_every = max(1, int(progress_every))
    p0_scale = float(p0_from_q_scale) if p0_from_q_scale is not None else None
    p0_fixed = float(max(EPS, initial_p0))
    rows: list[dict[str, float | int]] = []
    for idx, q_value in enumerate(q_values, start=1):
        q_float = float(q_value)
        p0_for_q = float(max(EPS, p0_scale * q_float)) if p0_scale is not None else p0_fixed
        rmse_score, rmse_obs = evaluate_q_score(
            groups,
            q_float,
            "rmse",
            q_gamma=q_gamma,
            initial_x0=initial_x0,
            initial_p0=p0_for_q,
            initial_state_by_group=initial_state_by_group,
            score_cache=rmse_cache,
        )
        mle_score, mle_obs = evaluate_q_score(
            groups,
            q_float,
            "mle",
            q_gamma=q_gamma,
            initial_x0=initial_x0,
            initial_p0=p0_for_q,
            initial_state_by_group=initial_state_by_group,
            score_cache=mle_cache,
        )
        rows.append(
            {
                "q_value": q_float,
                "p0_value": p0_for_q,
                "one_step_rmse_seconds": float(rmse_score),
                "rmse_observations": int(rmse_obs),
                "negative_log_likelihood": float(mle_score),
                "nll_observations": int(mle_obs),
                "observations": int(rmse_obs),
            }
        )
        if progress_label and (idx % progress_every == 0 or idx == len(q_values)):
            print(
                f"[QDIAG {progress_label}] completed {idx}/{len(q_values)} q={q_float:.3e} "
                f"rmse={float(rmse_score):.3f} nll={float(mle_score):.3f}",
                flush=True,
            )
    return pd.DataFrame(rows).sort_values("q_value").reset_index(drop=True)


def _fit_global_q_single(
    groups: list[dict[str, Any]],
    initial_q: float,
    objective: str,
    *,
    initial_q_gamma: float = 1e-6,
    initial_k: float = 30.0,
    initial_x0: float = 0.0,
    initial_p0: float = P_COV_CAP_FLOOR,
    initial_state_by_group: dict[str, dict[str, BoatState]] | None = None,
    p0_from_q_scale: float | None = None,
    score_cache: dict[tuple[Any, ...], tuple[float, int]] | None = None,
    n_workers: int | None = None,
    progress_label: str | None = None,
    progress_every: int = 5,
) -> tuple[float, float, float, float, int]:
    objective = resolve_q_objective(objective)
    log_label = progress_label if progress_label else "GLOBAL"
    de_popsize = max(15, int(os.cpu_count() or 1))
    requested_workers = 1 if n_workers is not None and int(n_workers) <= 1 else (int(n_workers) if n_workers is not None else de_popsize)
    de_workers = max(1, min(de_popsize, requested_workers))
    de_updating = "deferred" if de_workers > 1 else "immediate"
    de_workers_map = None
    de_pool: Pool | None = None
    if de_workers > 1:
        de_context = _DEObjectiveContext(
            groups=groups,
            objective=objective,
            log_q_min=float(math.log(float(Q_SEARCH_MIN))),
            log_q_max=float(math.log(float(Q_SEARCH_MAX))),
            q_gamma_min=float(math.log(float(max(EPS, Q_GAMMA_SEARCH_MIN)))),
            q_gamma_max=float(math.log(float(max(EPS, Q_GAMMA_SEARCH_MAX)))),
            log_k_min=float(math.log(float(max(EPS, P0_SCALE_SEARCH_MIN)))),
            log_k_max=float(math.log(float(max(EPS, P0_SCALE_SEARCH_MAX)))),
            x0_fixed=float(initial_x0),
            initial_state_by_group=initial_state_by_group or {},
        )
        de_pool = Pool(processes=de_workers, initializer=_de_worker_init, initargs=(de_context,))
        de_workers_map = _DEProcessMap(de_pool)
    x0_fixed = float(initial_x0)
    progress_every = max(1, int(progress_every))
    q_min = float(Q_SEARCH_MIN)
    q_max = float(Q_SEARCH_MAX)
    q_gamma_min = float(Q_GAMMA_SEARCH_MIN)
    q_gamma_max = float(Q_GAMMA_SEARCH_MAX)
    k_min = float(P0_SCALE_SEARCH_MIN)
    k_max = float(P0_SCALE_SEARCH_MAX)
    log_q_min = float(math.log(q_min))
    log_q_max = float(math.log(q_max))
    log_q_gamma_min = float(math.log(max(EPS, q_gamma_min)))
    log_q_gamma_max = float(math.log(max(EPS, q_gamma_max)))
    log_k_min = float(math.log(max(EPS, k_min)))
    log_k_max = float(math.log(max(EPS, k_max)))
    initial_q_clamped = float(max(q_min, min(q_max, float(initial_q))))
    initial_q_gamma_clamped = float(max(q_gamma_min, min(q_gamma_max, float(initial_q_gamma))))
    if p0_from_q_scale is not None:
        initial_k = float(p0_from_q_scale)
    initial_k_clamped = float(max(k_min, min(k_max, float(initial_k))))
    de_maxiter = 200
    de_tol = 1e-5
    de_atol = 0.0

    evaluations = 0
    best_q = initial_q_clamped
    best_q_gamma = initial_q_gamma_clamped
    best_k = initial_k_clamped
    best_score = float("inf")
    best_obs = 0

    def _boundary_flags(q_value: float, q_gamma_value: float, k_value: float) -> tuple[bool, bool, bool]:
        q_on_boundary = (
            abs(q_value - q_min) <= max(1e-20, abs(q_min) * 1e-9)
            or abs(q_value - q_max) <= max(1e-20, abs(q_max) * 1e-9)
        )
        q_gamma_on_boundary = (
            abs(q_gamma_value - q_gamma_min) <= max(1e-20, abs(q_gamma_min) * 1e-9)
            or abs(q_gamma_value - q_gamma_max) <= max(1e-20, abs(q_gamma_max) * 1e-9)
        )
        k_on_boundary = (
            abs(k_value - k_min) <= max(1e-20, abs(k_min) * 1e-9)
            or abs(k_value - k_max) <= max(1e-20, abs(k_max) * 1e-9)
        )
        return bool(q_on_boundary), bool(q_gamma_on_boundary), bool(k_on_boundary)

    if progress_label:
        worker_text = f"{de_workers} workers" if de_workers > 1 else "1 worker"
        requested_workers_text = f", requested n_workers={int(n_workers)}" if n_workers is not None else ""
        print(
            f"[QFIT {progress_label}] start objective={objective} x0={x0_fixed:+.5f} p0=k*q k0={initial_k_clamped:.3e} "
            f"q_gamma0={initial_q_gamma_clamped:.3e} "
            f"(optimizer=DE(maxiter={de_maxiter}, popsize={de_popsize}), parallel={worker_text}{requested_workers_text})",
            flush=True,
        )
        print(
            f"[QFIT {progress_label}] crit: stdE<=atol+tol*|meanE| "
            f"(tol={de_tol:.3g}, atol={de_atol:.3g}, stop:c>1, maxit={de_maxiter})",
            flush=True,
        )

    def _register(score: float, obs: int, q_value: float, q_gamma_value: float, k_value: float) -> None:
        nonlocal evaluations, best_q, best_q_gamma, best_k, best_score, best_obs
        evaluations += 1
        if score < best_score:
            best_q = float(q_value)
            best_q_gamma = float(q_gamma_value)
            best_k = float(k_value)
            best_score = float(score)
            best_obs = int(obs)
        if progress_label and (evaluations % progress_every == 0 or evaluations == 1):
            print(
                f"[QFIT {progress_label}] ev={evaluations} q={q_value:.2e} qg={q_gamma_value:.2e} k={k_value:.2e} "
                f"obj={score:.5g} best={best_score:.5g} bq={best_q:.2e} bqg={best_q_gamma:.2e} bk={best_k:.2e} obs={best_obs}",
                flush=True,
            )

    def _score_for(q_value: float, q_gamma_value: float, k_value: float) -> tuple[float, int]:
        p0_for_q = float(max(EPS, k_value * q_value))
        score, obs = evaluate_q_score(
            groups,
            float(q_value),
            objective,
            q_gamma=float(q_gamma_value),
            initial_x0=x0_fixed,
            initial_p0=p0_for_q,
            initial_state_by_group=initial_state_by_group,
            score_cache=score_cache,
        )
        return float(score), int(obs)

    def _objective(theta: np.ndarray) -> float:
        log_q = float(theta[0])
        log_q_gamma = float(theta[1])
        log_k = float(theta[2])
        log_q = float(max(log_q_min, min(log_q_max, log_q)))
        log_q_gamma = float(max(log_q_gamma_min, min(log_q_gamma_max, log_q_gamma)))
        log_k = float(max(log_k_min, min(log_k_max, log_k)))
        q_value = float(math.exp(log_q))
        q_gamma_value = float(math.exp(log_q_gamma))
        k_value = float(math.exp(log_k))
        score, obs = _score_for(q_value, q_gamma_value, k_value)
        _register(float(score), int(obs), q_value, q_gamma_value, k_value)
        if not np.isfinite(float(score)):
            return 1e300
        return float(score)

    # Seed from initial guess.
    _objective(
        np.array(
            [math.log(initial_q_clamped), math.log(max(EPS, initial_q_gamma_clamped)), math.log(max(EPS, initial_k_clamped))],
            dtype=float,
        )
    )

    de_gen = 0

    def _de_callback(_xk: np.ndarray, convergence: float) -> bool:
        nonlocal de_gen, best_q, best_q_gamma, best_k, best_score, best_obs
        de_gen += 1
        if progress_label:
            try:
                theta = np.asarray(_xk, dtype=float)
                log_q_cb = float(max(log_q_min, min(log_q_max, float(theta[0]))))
                log_qg_cb = float(max(log_q_gamma_min, min(log_q_gamma_max, float(theta[1]))))
                log_k_cb = float(max(log_k_min, min(log_k_max, float(theta[2]))))
                q_cb = float(math.exp(log_q_cb))
                qg_cb = float(math.exp(log_qg_cb))
                k_cb = float(math.exp(log_k_cb))
                score_cb, obs_cb = _score_for(q_cb, qg_cb, k_cb)
                if np.isfinite(float(score_cb)) and (float(score_cb) < best_score or de_gen == 1):
                    best_q = float(q_cb)
                    best_q_gamma = float(qg_cb)
                    best_k = float(k_cb)
                    best_score = float(score_cb)
                    best_obs = int(obs_cb)
            except Exception:
                pass
            q_boundary, q_gamma_boundary, k_boundary = _boundary_flags(best_q, best_q_gamma, best_k)
            if objective == "mle":
                best_nll = float(best_score)
            else:
                best_nll, _ = evaluate_q_score(
                    groups,
                    best_q,
                    "mle",
                    q_gamma=best_q_gamma,
                    initial_x0=x0_fixed,
                    initial_p0=float(max(EPS, best_k * best_q)),
                    initial_state_by_group=initial_state_by_group,
                    score_cache=None,
                )
            nll_text = f"{float(best_nll):.6f}" if np.isfinite(float(best_nll)) else "nan"
            stop_ready = bool(float(convergence) > 1.0)
            print(
                f"[QFIT {log_label}] g={de_gen}/{de_maxiter} c={float(convergence):.5g} stop={stop_ready} "
                f"q={best_q:.2e} qg={best_q_gamma:.2e} k={best_k:.2e} obj={best_score:.5g} nll={nll_text} "
                f"b=(q:{int(q_boundary)},qg:{int(q_gamma_boundary)},k:{int(k_boundary)})",
                flush=True,
            )
        return False

    try:
        de_result = differential_evolution(
            _objective,
            bounds=[(log_q_min, log_q_max), (log_q_gamma_min, log_q_gamma_max), (log_k_min, log_k_max)],
            maxiter=de_maxiter,
            popsize=de_popsize,
            polish=False,
            updating=de_updating,
            workers=de_workers_map if de_workers_map is not None else de_workers,
            seed=42,
            tol=de_tol,
            atol=de_atol,
            callback=_de_callback,
        )
        _objective(np.asarray(de_result.x, dtype=float))
    finally:
        if de_pool is not None:
            de_pool.close()
            de_pool.join()

    if progress_label:
        q_boundary, q_gamma_boundary, k_boundary = _boundary_flags(best_q, best_q_gamma, best_k)
        boundary_note = " [BOUNDARY]" if (q_boundary or q_gamma_boundary or k_boundary) else ""
        if objective == "mle":
            best_nll = float(best_score)
        else:
            best_nll, _ = evaluate_q_score(
                groups,
                best_q,
                "mle",
                q_gamma=best_q_gamma,
                initial_x0=x0_fixed,
                initial_p0=float(max(EPS, best_k * best_q)),
                initial_state_by_group=initial_state_by_group,
                score_cache=None,
            )
        print(
            f"[QFIT {progress_label}] done obj={best_score:.5g} nll={float(best_nll):.6f} "
            f"q={best_q:.2e} qg={best_q_gamma:.2e} k={best_k:.2e} obs={best_obs} ev={evaluations} "
            f"nit={int(de_result.nit)} ok={bool(de_result.success)} b=(q:{int(q_boundary)},qg:{int(q_gamma_boundary)},k:{int(k_boundary)}) "
            f"msg={str(de_result.message)!r}{boundary_note}",
            flush=True,
        )

    return float(best_q), float(best_q_gamma), float(best_k), float(best_score), int(best_obs)


def fit_global_q(
    groups: list[dict[str, Any]],
    initial_q: float,
    objective: str,
    *,
    initial_q_gamma: float = 1e-6,
    initial_k: float = 30.0,
    initial_x0: float = 0.0,
    initial_p0: float = P_COV_CAP_FLOOR,
    initial_state_by_group: dict[str, dict[str, BoatState]] | None = None,
    p0_from_q_scale: float | None = None,
    score_cache: dict[tuple[Any, ...], tuple[float, int]] | None = None,
    n_workers: int | None = None,
    progress_label: str | None = None,
    progress_every: int = 5,
) -> tuple[float, float, float, dict[str, dict[str, BoatState]], float, int]:
    first_label = f"{progress_label}:pass1" if progress_label else None
    first_q, first_q_gamma, first_k, first_score, first_obs = _fit_global_q_single(
        groups,
        initial_q,
        objective,
        initial_q_gamma=initial_q_gamma,
        initial_k=initial_k,
        initial_x0=initial_x0,
        initial_p0=initial_p0,
        initial_state_by_group=initial_state_by_group,
        p0_from_q_scale=p0_from_q_scale,
        score_cache=score_cache,
        n_workers=n_workers,
        progress_label=first_label,
        progress_every=progress_every,
    )
    first_p0 = float(max(EPS, first_k * first_q))
    warm_start_state_by_group = _compute_terminal_states_by_group(
        groups,
        first_q,
        q_gamma=first_q_gamma,
        initial_x0=initial_x0,
        initial_p0=first_p0,
        initial_state_by_group=initial_state_by_group,
    )
    second_label = f"{progress_label}:pass2" if progress_label else None
    final_q, final_q_gamma, final_k, final_score, final_obs = _fit_global_q_single(
        groups,
        first_q,
        objective,
        initial_q_gamma=first_q_gamma,
        initial_k=first_k,
        initial_x0=initial_x0,
        initial_p0=first_p0,
        initial_state_by_group=warm_start_state_by_group,
        p0_from_q_scale=p0_from_q_scale,
        score_cache=None,
        n_workers=n_workers,
        progress_label=second_label,
        progress_every=progress_every,
    )
    return float(final_q), float(final_q_gamma), float(final_k), warm_start_state_by_group, float(final_score), int(final_obs)
