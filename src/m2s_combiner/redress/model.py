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
from .constants import GROUP_Q_CACHE_FILENAME
from .constants import MAX_DELTA_T_DAYS
from .constants import MU_Y_LOG_MAX
from .constants import MU_Y_LOG_MIN
from .constants import NON_OBS_STATUSES
from .constants import P_COV_CAP_FLOOR
from .constants import Q_OBJECTIVE_CHOICES
from .constants import Q_SEARCH_MAX
from .constants import Q_SEARCH_MIN
from .constants import R_T_SEARCH_MAX
from .constants import R_T_SEARCH_MIN


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


@dataclass
class _DEObjectiveContext:
    groups: list[dict[str, Any]]
    objective: str
    log_q_min: float
    log_q_max: float
    x0_fixed: float
    p0_fixed: float
    p0_scale: float | None


_DE_CONTEXT: _DEObjectiveContext | None = None


def _de_worker_init(context: _DEObjectiveContext) -> None:
    global _DE_CONTEXT
    _DE_CONTEXT = context


def _de_worker_objective(theta: np.ndarray) -> float:
    context = _DE_CONTEXT
    if context is None:
        return 1e300
    theta_vec = np.asarray(theta, dtype=float)
    log_q = float(max(context.log_q_min, min(context.log_q_max, float(theta_vec[0]))))
    ar_a = float(min(1.0, max(0.0, float(theta_vec[1]))))
    q_value = float(math.exp(log_q))
    p0_for_q = float(max(EPS, context.p0_scale * q_value)) if context.p0_scale is not None else float(context.p0_fixed)
    score, _obs = evaluate_q_score(
        context.groups,
        q_value,
        context.objective,
        ar_a=ar_a,
        initial_x0=context.x0_fixed,
        initial_p0=p0_for_q,
        score_cache=None,
    )
    if not np.isfinite(float(score)):
        return 1e300
    return float(score)


class _DEProcessMap:
    def __init__(self, pool: Pool) -> None:
        self.pool = pool

    def __call__(self, _func: Any, iterable: Any) -> list[float]:
        theta_list = [np.asarray(theta, dtype=float) for theta in iterable]
        return self.pool.map(_de_worker_objective, theta_list)


@dataclass
class _RefineTask:
    idx: int
    rank: int
    theta0: tuple[float, float]
    objective: str
    log_q_min: float
    log_q_max: float
    maxiter: int = 120
    ftol: float = 1e-12


@dataclass
class _RefineResult:
    idx: int
    rank: int
    q_value: float
    ar_a: float
    score: float
    obs: int
    success: bool
    message: str


def _score_for_context(context: _DEObjectiveContext, q_value: float, ar_a_value: float) -> tuple[float, int]:
    p0_for_q = float(max(EPS, context.p0_scale * q_value)) if context.p0_scale is not None else float(context.p0_fixed)
    score, obs = evaluate_q_score(
        context.groups,
        float(q_value),
        context.objective,
        ar_a=float(min(1.0, max(0.0, ar_a_value))),
        initial_x0=float(context.x0_fixed),
        initial_p0=float(p0_for_q),
        score_cache=None,
    )
    return float(score), int(obs)


def _mle_objective_and_gradient_for_context(context: _DEObjectiveContext, theta: np.ndarray) -> tuple[float, np.ndarray, int, float, float]:
    log_q = float(max(context.log_q_min, min(context.log_q_max, float(theta[0]))))
    ar_a = float(min(1.0, max(0.0, float(theta[1]))))
    q_value = float(math.exp(log_q))
    p0_for_q = float(max(EPS, context.p0_scale * q_value)) if context.p0_scale is not None else float(context.p0_fixed)
    score, obs, grad_q, grad_a = q_objective_mle_with_gradient(
        context.groups,
        q_value,
        ar_a=ar_a,
        initial_x0=float(context.x0_fixed),
        initial_p0=float(p0_for_q),
    )
    if not np.isfinite(score):
        return 1e300, np.array([0.0, 0.0], dtype=float), int(obs), q_value, ar_a
    grad_log_q = float(grad_q * q_value)
    return float(score), np.array([grad_log_q, float(grad_a)], dtype=float), int(obs), q_value, ar_a


def _refine_candidate_worker(task: _RefineTask) -> _RefineResult:
    context = _DE_CONTEXT
    if context is None:
        return _RefineResult(
            idx=int(task.idx),
            rank=int(task.rank),
            q_value=float("nan"),
            ar_a=float("nan"),
            score=1e300,
            obs=0,
            success=False,
            message="missing worker context",
        )

    theta0 = np.asarray(task.theta0, dtype=float).copy()
    theta0[0] = float(max(task.log_q_min, min(task.log_q_max, float(theta0[0]))))
    theta0[1] = float(min(1.0, max(0.0, float(theta0[1]))))

    if task.objective == "mle":
        last_theta: np.ndarray | None = None
        last_score = float("inf")
        last_grad = np.array([0.0, 0.0], dtype=float)

        def _eval(theta: np.ndarray) -> tuple[float, np.ndarray]:
            nonlocal last_theta, last_score, last_grad
            theta_vec = np.asarray(theta, dtype=float)
            if last_theta is not None and np.array_equal(theta_vec, last_theta):
                return float(last_score), np.asarray(last_grad, dtype=float)
            score, grad, _obs, _qv, _av = _mle_objective_and_gradient_for_context(context, theta_vec)
            last_theta = theta_vec.copy()
            last_score = float(score)
            last_grad = np.asarray(grad, dtype=float).copy()
            return float(last_score), np.asarray(last_grad, dtype=float)

        result = minimize(
            lambda t: _eval(t)[0],
            x0=theta0,
            method="L-BFGS-B",
            jac=lambda t: _eval(t)[1],
            bounds=[(task.log_q_min, task.log_q_max), (0.0, 1.0)],
            options={"maxiter": int(task.maxiter), "ftol": float(task.ftol)},
        )
    else:
        result = minimize(
            _de_worker_objective,
            x0=theta0,
            method="L-BFGS-B",
            bounds=[(task.log_q_min, task.log_q_max), (0.0, 1.0)],
            options={"maxiter": int(task.maxiter), "ftol": float(task.ftol)},
        )

    theta_star = np.asarray(result.x, dtype=float).copy()
    theta_star[0] = float(max(task.log_q_min, min(task.log_q_max, float(theta_star[0]))))
    theta_star[1] = float(min(1.0, max(0.0, float(theta_star[1]))))
    q_star = float(math.exp(float(theta_star[0])))
    a_star = float(theta_star[1])
    score_star, obs_star = _score_for_context(context, q_star, a_star)
    return _RefineResult(
        idx=int(task.idx),
        rank=int(task.rank),
        q_value=float(q_star),
        ar_a=float(a_star),
        score=float(score_star),
        obs=int(obs_star),
        success=bool(result.success),
        message=str(result.message),
    )


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


def load_group_qa_cache(path: Path) -> tuple[dict[str, float], dict[str, float]]:
    if not path.exists():
        return {}, {}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"Could not read Q cache file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid Q cache JSON in {path}") from exc

    if isinstance(payload, dict) and isinstance(payload.get("group_q"), dict):
        q_source = payload["group_q"]
        a_source = payload.get("group_a", {})
    elif isinstance(payload, dict):
        q_source = payload
        a_source = {}
    else:
        raise ValueError(f"Invalid Q cache payload in {path}")

    q_map: dict[str, float] = {}
    for key, value in q_source.items():
        q_val = float(value)
        if np.isfinite(q_val) and q_val > 0.0:
            q_map[str(key)] = q_val
    a_map: dict[str, float] = {}
    if isinstance(a_source, dict):
        for key, value in a_source.items():
            a_val = float(value)
            if np.isfinite(a_val):
                a_map[str(key)] = float(min(1.0, max(0.0, a_val)))
    return q_map, a_map


def load_group_q_cache(path: Path) -> dict[str, float]:
    q_map, _ = load_group_qa_cache(path)
    return q_map


def save_group_qa_cache(path: Path, q_by_group: dict[str, float], a_by_group: dict[str, float] | None = None) -> None:
    a_by_group = a_by_group or {}
    payload = {
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "group_q": {str(k): float(v) for k, v in sorted(q_by_group.items())},
        "group_a": {str(k): float(min(1.0, max(0.0, v))) for k, v in sorted(a_by_group.items()) if np.isfinite(float(v))},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def save_group_q_cache(path: Path, q_by_group: dict[str, float]) -> None:
    save_group_qa_cache(path, q_by_group, {})


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
    if floor > 0.0:
        cov = cov + float(floor) * np.eye(cov.shape[0], dtype=float)
    return cov


def _apply_state_transition(
    state_mean: np.ndarray,
    state_cov: np.ndarray,
    *,
    ar_a: float,
    process_diag: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    a = float(min(1.0, max(0.0, ar_a)))
    coeff = 1.0 - a
    prior_mean = coeff * np.asarray(state_mean, dtype=float)
    prior_cov = (coeff * coeff) * np.asarray(state_cov, dtype=float) + np.diag(np.asarray(process_diag, dtype=float))
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
    s_matrix = _symmetrize_and_floor_covariance(prior_covariance + sigma2 * np.eye(m, dtype=float))
    sign, logdet = np.linalg.slogdet(s_matrix)
    if sign <= 0 or not np.isfinite(logdet):
        return float("inf"), float("nan"), 0.0
    ones = np.ones(m, dtype=float)
    inv_times_ones = np.linalg.solve(s_matrix, ones)
    inv_times_centered = np.linalg.solve(s_matrix, centered_values)
    denom = float(ones @ inv_times_ones)
    if not np.isfinite(denom) or denom <= EPS:
        return float("inf"), float("nan"), 0.0
    mu_hat_unclipped = float((ones @ inv_times_centered) / denom)
    mu_hat = float(min(MU_Y_LOG_MAX, max(MU_Y_LOG_MIN, mu_hat_unclipped)))
    residual = centered_values - mu_hat * ones
    inv_times_residual = np.linalg.solve(s_matrix, residual)
    quad = float(residual @ inv_times_residual)
    nll = 0.5 * float(m * math.log(2.0 * math.pi) + logdet + quad)
    s_inv = np.linalg.inv(s_matrix)
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
            b_hat_loo, _, _ = fit_day_parameters_full(
                centered_values_loo,
                p_prior_loo,
                warm_start_r_t=r_t,
                warm_start_only=True,
            )
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
    ar_a: float = 0.0,
    initial_x0: float = 0.0,
    initial_p0: float = P_COV_CAP_FLOOR,
    collect_history: bool = True,
) -> GroupFilterResult:
    runtime = prepare_group_runtime(group)
    selected_races: list[str] = list(group["selected_races"])
    race_dates: dict[str, datetime] = dict(group["race_dates"])
    group_label = str(group["group"])
    ar_a = float(min(1.0, max(0.0, ar_a)))

    competitors: list[str] = runtime["competitors"]
    race_rows_by_label: dict[str, pd.DataFrame] = runtime["race_rows_by_label"]
    race_lookup_by_label: dict[str, dict[str, dict[str, Any]]] = runtime["race_lookup_by_label"]
    race_order_by_label: dict[str, int] = runtime.get("race_order_by_label", {})
    debut_order_by_competitor: dict[str, int] = runtime.get("debut_order_by_competitor", {})
    initial_p0 = float(max(EPS, initial_p0))
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

        delta_days_by_competitor: dict[str, int] = {}
        process_diag = np.zeros(n_competitors, dtype=float)
        for competitor in competitors:
            if not active_by_competitor[competitor]:
                delta_days_by_competitor[competitor] = 0
                continue
            last_state_date = last_state_date_by_competitor[competitor]
            delta_days = 0 if last_state_date is None else min(MAX_DELTA_T_DAYS, max(0, (race_date - last_state_date).days))
            delta_days_by_competitor[competitor] = int(delta_days)
            process_diag[competitor_index[competitor]] = float(delta_days) * float(global_q)
        active_indices = _active_indices_from_flags(competitors, competitor_index, active_by_competitor)
        prior_mean, prior_cov = _apply_state_transition(
            state_mean,
            state_cov,
            ar_a=ar_a,
            process_diag=process_diag,
        )
        prior_mean = _project_active_vector_zero_sum(prior_mean, active_indices)
        prior_cov = _symmetrize_and_floor_covariance(_project_active_covariance_zero_sum(prior_cov, active_indices))
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
        state_mean = _project_active_vector_zero_sum(state_mean, active_indices)
        state_cov = _symmetrize_and_floor_covariance(_project_active_covariance_zero_sum(state_cov, active_indices))

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
                        "global_ar_a": ar_a,
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
    ar_a_by_group: dict[str, float] | None = None,
    initial_x0_by_group: dict[str, float] | None = None,
    initial_p0_by_group: dict[str, float] | None = None,
    collect_history: bool = True,
) -> tuple[pd.DataFrame, dict[str, float], dict[str, int]]:
    if not groups:
        return pd.DataFrame(), {}, {}

    groups_by_name = {str(group["group"]): group for group in groups}
    initial_p0_by_group = initial_p0_by_group or {}
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
    ar_a_by_group = ar_a_by_group or {}
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
        ar_a = float(min(1.0, max(0.0, ar_a_by_group.get(group_name, 0.0))))
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

        delta_days_by_competitor: dict[str, int] = {}
        process_diag = np.zeros(n_competitors, dtype=float)
        for competitor in competitors:
            if not group_active[competitor]:
                delta_days_by_competitor[competitor] = 0
                continue
            last_state_date = group_last_dates[competitor]
            delta_days = 0 if last_state_date is None else min(MAX_DELTA_T_DAYS, max(0, (race_date - last_state_date).days))
            delta_days_by_competitor[competitor] = int(delta_days)
            process_diag[competitor_index[competitor]] = float(delta_days) * float(global_q)
        active_indices = _active_indices_from_flags(competitors, competitor_index, group_active)
        prior_mean, prior_cov = _apply_state_transition(
            group_mean,
            group_cov,
            ar_a=ar_a,
            process_diag=process_diag,
        )
        prior_mean = _project_active_vector_zero_sum(prior_mean, active_indices)
        prior_cov = _symmetrize_and_floor_covariance(_project_active_covariance_zero_sum(prior_cov, active_indices))
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
        group_mean = _project_active_vector_zero_sum(group_mean, active_indices)
        group_cov = _symmetrize_and_floor_covariance(_project_active_covariance_zero_sum(group_cov, active_indices))
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
                        "global_ar_a": ar_a,
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


def _run_group_filter_mle_with_gradient(
    group: dict[str, Any],
    global_q: float,
    *,
    ar_a: float,
    initial_x0: float,
    initial_p0: float,
) -> tuple[float, int, float, float]:
    runtime = prepare_group_runtime(group)
    selected_races: list[str] = list(group["selected_races"])
    race_dates: dict[str, datetime] = dict(group["race_dates"])
    ar_a = float(min(1.0, max(0.0, ar_a)))
    q_value = float(max(EPS, global_q))
    c = 1.0 - ar_a

    competitors: list[str] = runtime["competitors"]
    race_rows_by_label: dict[str, pd.DataFrame] = runtime["race_rows_by_label"]
    race_order_by_label: dict[str, int] = runtime.get("race_order_by_label", {})
    debut_order_by_competitor: dict[str, int] = runtime.get("debut_order_by_competitor", {})
    initial_p0 = float(max(EPS, initial_p0))
    initial_x0 = float(initial_x0)
    competitor_index = {competitor: idx for idx, competitor in enumerate(competitors)}
    n_competitors = len(competitors)

    state_mean = np.zeros(n_competitors, dtype=float)
    state_cov = np.eye(n_competitors, dtype=float) * EPS
    dmean_dq = np.zeros(n_competitors, dtype=float)
    dmean_da = np.zeros(n_competitors, dtype=float)
    dcov_dq = np.zeros((n_competitors, n_competitors), dtype=float)
    dcov_da = np.zeros((n_competitors, n_competitors), dtype=float)

    active_by_competitor = {competitor: False for competitor in competitors}
    last_state_date_by_competitor = {competitor: None for competitor in competitors}

    nll_sum = 0.0
    observed_count = 0
    grad_q = 0.0
    grad_a = 0.0

    for race_label in selected_races:
        race_date = race_dates.get(race_label)
        if race_date is None:
            continue

        race_rows = race_rows_by_label.get(race_label)
        if race_rows is None:
            continue

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
                    dmean_dq[idx] = 0.0
                    dmean_da[idx] = 0.0
                    dcov_dq[idx, :] = 0.0
                    dcov_dq[:, idx] = 0.0
                    dcov_da[idx, :] = 0.0
                    dcov_da[:, idx] = 0.0
                    last_state_date_by_competitor[competitor] = None
                    active_by_competitor[competitor] = True

        delta_days_diag = np.zeros(n_competitors, dtype=float)
        for competitor in competitors:
            if not active_by_competitor[competitor]:
                continue
            last_state_date = last_state_date_by_competitor[competitor]
            delta_days = 0 if last_state_date is None else min(MAX_DELTA_T_DAYS, max(0, (race_date - last_state_date).days))
            delta_days_diag[competitor_index[competitor]] = float(delta_days)
        active_indices = _active_indices_from_flags(competitors, competitor_index, active_by_competitor)

        process_diag = delta_days_diag * q_value
        prior_mean = c * state_mean
        prior_cov = (c * c) * state_cov + np.diag(process_diag)
        dprior_mean_dq = c * dmean_dq
        dprior_mean_da = c * dmean_da - state_mean
        dprior_cov_dq = (c * c) * dcov_dq + np.diag(delta_days_diag)
        dprior_cov_da = (c * c) * dcov_da - (2.0 * c) * state_cov

        prior_cov = _symmetrize_and_floor_covariance(prior_cov)
        dprior_cov_dq = 0.5 * (dprior_cov_dq + dprior_cov_dq.T)
        dprior_cov_da = 0.5 * (dprior_cov_da + dprior_cov_da.T)
        prior_mean = _project_active_vector_zero_sum(prior_mean, active_indices)
        prior_cov = _symmetrize_and_floor_covariance(_project_active_covariance_zero_sum(prior_cov, active_indices))
        dprior_mean_dq = _project_active_vector_zero_sum(dprior_mean_dq, active_indices)
        dprior_mean_da = _project_active_vector_zero_sum(dprior_mean_da, active_indices)
        dprior_cov_dq = _project_active_covariance_zero_sum(dprior_cov_dq, active_indices)
        dprior_cov_da = _project_active_covariance_zero_sum(dprior_cov_da, active_indices)
        dprior_cov_dq = 0.5 * (dprior_cov_dq + dprior_cov_dq.T)
        dprior_cov_da = 0.5 * (dprior_cov_da + dprior_cov_da.T)

        obs_mask = race_rows["beregnet_seconds"].notna() & (~race_rows["status_upper"].isin(NON_OBS_STATUSES))
        observed = race_rows.loc[obs_mask, ["competitor", "beregnet_seconds"]].copy()
        if observed.empty:
            state_mean = prior_mean
            state_cov = prior_cov
            dmean_dq = dprior_mean_dq
            dmean_da = dprior_mean_da
            dcov_dq = dprior_cov_dq
            dcov_da = dprior_cov_da
            for competitor in competitors:
                if active_by_competitor[competitor]:
                    last_state_date_by_competitor[competitor] = race_date
            continue

        observed_competitors = observed["competitor"].astype(str).tolist()
        observed_indices = np.array([competitor_index[cx] for cx in observed_competitors], dtype=int)
        y_values = np.log(np.clip(observed["beregnet_seconds"].to_numpy(dtype=float), EPS, None))
        x_prior_obs = prior_mean[observed_indices]
        p_prior_obs = prior_cov[np.ix_(observed_indices, observed_indices)]
        centered_values = y_values - x_prior_obs
        b_hat, r_t, race_nll = fit_day_parameters_full(centered_values, p_prior_obs)
        nll_sum += float(race_nll)
        observed_count += int(observed_indices.size)

        s_matrix = _symmetrize_and_floor_covariance(p_prior_obs + float(r_t) * np.eye(observed_indices.size, dtype=float))
        s_inv = np.linalg.inv(s_matrix)
        residual = centered_values - float(b_hat)
        s_inv_res = s_inv @ residual

        dL_dx_obs = -s_inv_res
        dL_dP_obs = 0.5 * (s_inv - np.outer(s_inv_res, s_inv_res))

        dprior_obs_mean_dq = dprior_mean_dq[observed_indices]
        dprior_obs_mean_da = dprior_mean_da[observed_indices]
        dprior_obs_cov_dq = dprior_cov_dq[np.ix_(observed_indices, observed_indices)]
        dprior_obs_cov_da = dprior_cov_da[np.ix_(observed_indices, observed_indices)]

        grad_q += float(dL_dx_obs @ dprior_obs_mean_dq) + float(np.sum(dL_dP_obs * dprior_obs_cov_dq))
        grad_a += float(dL_dx_obs @ dprior_obs_mean_da) + float(np.sum(dL_dP_obs * dprior_obs_cov_da))

        p_cross = prior_cov[:, observed_indices]
        dp_cross_dq = dprior_cov_dq[:, observed_indices]
        dp_cross_da = dprior_cov_da[:, observed_indices]
        k_matrix = p_cross @ s_inv

        dS_dq = dprior_obs_cov_dq
        dS_da = dprior_obs_cov_da
        dS_inv_dq = -s_inv @ dS_dq @ s_inv
        dS_inv_da = -s_inv @ dS_da @ s_inv
        dres_dq = -dprior_obs_mean_dq
        dres_da = -dprior_obs_mean_da

        dk_dq = dp_cross_dq @ s_inv + p_cross @ dS_inv_dq
        dk_da = dp_cross_da @ s_inv + p_cross @ dS_inv_da

        post_mean = prior_mean + (k_matrix @ residual)
        post_cov = prior_cov - (k_matrix @ p_cross.T)
        dpost_mean_dq = dprior_mean_dq + (dk_dq @ residual) + (k_matrix @ dres_dq)
        dpost_mean_da = dprior_mean_da + (dk_da @ residual) + (k_matrix @ dres_da)
        dpost_cov_dq = dprior_cov_dq - (dk_dq @ p_cross.T) - (k_matrix @ dp_cross_dq.T)
        dpost_cov_da = dprior_cov_da - (dk_da @ p_cross.T) - (k_matrix @ dp_cross_da.T)

        state_mean = np.asarray(post_mean, dtype=float).copy()
        state_cov = _symmetrize_and_floor_covariance(np.asarray(post_cov, dtype=float))
        dmean_dq = np.asarray(dpost_mean_dq, dtype=float)
        dmean_da = np.asarray(dpost_mean_da, dtype=float)
        dcov_dq = 0.5 * (np.asarray(dpost_cov_dq, dtype=float) + np.asarray(dpost_cov_dq, dtype=float).T)
        dcov_da = 0.5 * (np.asarray(dpost_cov_da, dtype=float) + np.asarray(dpost_cov_da, dtype=float).T)
        state_mean = _project_active_vector_zero_sum(state_mean, active_indices)
        state_cov = _symmetrize_and_floor_covariance(_project_active_covariance_zero_sum(state_cov, active_indices))
        dmean_dq = _project_active_vector_zero_sum(dmean_dq, active_indices)
        dmean_da = _project_active_vector_zero_sum(dmean_da, active_indices)
        dcov_dq = _project_active_covariance_zero_sum(dcov_dq, active_indices)
        dcov_da = _project_active_covariance_zero_sum(dcov_da, active_indices)
        dcov_dq = 0.5 * (dcov_dq + dcov_dq.T)
        dcov_da = 0.5 * (dcov_da + dcov_da.T)
        for competitor in competitors:
            if active_by_competitor[competitor]:
                last_state_date_by_competitor[competitor] = race_date

    return float(nll_sum), int(observed_count), float(grad_q), float(grad_a)


def q_objective_mle_with_gradient(
    groups: list[dict[str, Any]],
    q_value: float,
    *,
    ar_a: float = 0.0,
    initial_x0: float = 0.0,
    initial_p0: float = P_COV_CAP_FLOOR,
) -> tuple[float, int, float, float]:
    nll_sum = 0.0
    obs_count = 0
    grad_q = 0.0
    grad_a = 0.0
    for group in groups:
        gnll, gobs, gq, ga = _run_group_filter_mle_with_gradient(
            group,
            float(q_value),
            ar_a=float(ar_a),
            initial_x0=float(initial_x0),
            initial_p0=float(initial_p0),
        )
        nll_sum += float(gnll)
        obs_count += int(gobs)
        grad_q += float(gq)
        grad_a += float(ga)
    if obs_count == 0:
        return float("inf"), 0, 0.0, 0.0
    return float(nll_sum), int(obs_count), float(grad_q), float(grad_a)


def q_objective(
    groups: list[dict[str, Any]],
    q_value: float,
    *,
    ar_a: float = 0.0,
    initial_x0: float = 0.0,
    initial_p0: float = P_COV_CAP_FLOOR,
) -> tuple[float, int]:
    sq_error_sum = 0.0
    obs_count = 0
    for group in groups:
        result = run_group_filter(
            group,
            q_value,
            ar_a=ar_a,
            initial_x0=initial_x0,
            initial_p0=initial_p0,
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
    ar_a: float = 0.0,
    initial_x0: float = 0.0,
    initial_p0: float = P_COV_CAP_FLOOR,
) -> tuple[float, int]:
    nll_sum = 0.0
    obs_count = 0
    for group in groups:
        result = run_group_filter(
            group,
            q_value,
            ar_a=ar_a,
            initial_x0=initial_x0,
            initial_p0=initial_p0,
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
    }
    objective = aliases.get(objective, objective)
    if objective not in Q_OBJECTIVE_CHOICES:
        choices = ", ".join(Q_OBJECTIVE_CHOICES)
        raise ValueError(f"Unsupported q objective '{value}'. Choose one of: {choices}")
    return objective


def evaluate_q_score(
    groups: list[dict[str, Any]],
    q_value: float,
    objective: str,
    *,
    ar_a: float = 0.0,
    initial_x0: float = 0.0,
    initial_p0: float = P_COV_CAP_FLOOR,
    score_cache: dict[tuple[float, float], tuple[float, int]] | None = None,
) -> tuple[float, int]:
    objective = resolve_q_objective(objective)
    q_key = float(q_value)
    a_key = float(min(1.0, max(0.0, ar_a)))
    key = (q_key, a_key)
    if score_cache is not None and key in score_cache:
        return score_cache[key]

    result = (
        q_objective(groups, q_key, ar_a=a_key, initial_x0=initial_x0, initial_p0=initial_p0)
        if objective == "rmse_loo"
        else q_objective_mle(groups, q_key, ar_a=a_key, initial_x0=initial_x0, initial_p0=initial_p0)
    )
    if score_cache is not None:
        score_cache[key] = result
    return result


def q_diagnostics(
    groups: list[dict[str, Any]],
    q_values: np.ndarray,
    *,
    ar_a: float = 0.0,
    initial_x0: float = 0.0,
    initial_p0: float = P_COV_CAP_FLOOR,
    p0_from_q_scale: float | None = None,
    rmse_cache: dict[tuple[float, float], tuple[float, int]] | None = None,
    mle_cache: dict[tuple[float, float], tuple[float, int]] | None = None,
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
            groups, q_float, "rmse", ar_a=ar_a, initial_x0=initial_x0, initial_p0=p0_for_q, score_cache=rmse_cache
        )
        mle_score, mle_obs = evaluate_q_score(
            groups, q_float, "mle", ar_a=ar_a, initial_x0=initial_x0, initial_p0=p0_for_q, score_cache=mle_cache
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


def fit_global_q(
    groups: list[dict[str, Any]],
    initial_q: float,
    objective: str,
    *,
    initial_ar_a: float = 0.0,
    initial_x0: float = 0.0,
    initial_p0: float = P_COV_CAP_FLOOR,
    p0_from_q_scale: float | None = None,
    score_cache: dict[tuple[float, float], tuple[float, int]] | None = None,
    n_workers: int | None = None,
    progress_label: str | None = None,
    progress_every: int = 5,
) -> tuple[float, float, float, int]:
    objective = resolve_q_objective(objective)
    log_label = progress_label if progress_label else "GLOBAL"
    de_popsize = max(15, int(os.cpu_count() or 1))
    de_workers = de_popsize
    de_updating = "deferred" if de_workers > 1 else "immediate"
    de_workers_map = None
    de_pool: Pool | None = None
    if de_workers > 1:
        de_context = _DEObjectiveContext(
            groups=groups,
            objective=objective,
            log_q_min=float(math.log(float(Q_SEARCH_MIN))),
            log_q_max=float(math.log(float(Q_SEARCH_MAX))),
            x0_fixed=float(initial_x0),
            p0_fixed=float(max(EPS, initial_p0)),
            p0_scale=float(p0_from_q_scale) if p0_from_q_scale is not None else None,
        )
        de_pool = Pool(processes=de_workers, initializer=_de_worker_init, initargs=(de_context,))
        de_workers_map = _DEProcessMap(de_pool)
    x0_fixed = float(initial_x0)
    p0_fixed = float(max(EPS, initial_p0))
    p0_scale = float(p0_from_q_scale) if p0_from_q_scale is not None else None
    progress_every = max(1, int(progress_every))
    q_min = float(Q_SEARCH_MIN)
    q_max = float(Q_SEARCH_MAX)
    log_q_min = float(math.log(q_min))
    log_q_max = float(math.log(q_max))
    initial_q_clamped = float(max(q_min, min(q_max, float(initial_q))))
    initial_a_clamped = float(min(1.0, max(0.0, float(initial_ar_a))))

    evaluations = 0
    best_q = initial_q_clamped
    best_a = initial_a_clamped
    best_score = float("inf")
    best_obs = 0

    if progress_label:
        p0_text = f"{p0_scale:.1f}*q" if p0_scale is not None else f"{p0_fixed:.3e}"
        worker_text = f"{de_workers} workers (DE phases)" if de_workers > 1 else "1 worker (DE phases)"
        requested_workers = f", requested n_workers={int(n_workers)} (ignored; using workers=popsize={de_popsize})" if n_workers is not None else ""
        print(
            f"[QFIT {progress_label}] start objective={objective} x0={x0_fixed:+.5f} p0={p0_text} "
            f"a0={initial_a_clamped:.3e} "
            f"(optimizer=DE(1)+GD+DE(19)+GD, parallel={worker_text}{requested_workers})",
            flush=True,
        )

    def _register(score: float, obs: int, q_value: float, ar_a_value: float) -> None:
        nonlocal evaluations, best_q, best_a, best_score, best_obs
        evaluations += 1
        if score < best_score:
            best_q = float(q_value)
            best_a = float(ar_a_value)
            best_score = float(score)
            best_obs = int(obs)
        if progress_label and (evaluations % progress_every == 0 or evaluations == 1):
            print(
                f"[QFIT {progress_label}] eval {evaluations} q={q_value:.3e} a={ar_a_value:.3e} obj={score:.6f} "
                f"best objective={best_score:.6f} q={best_q:.3e} a={best_a:.3e} obs={best_obs}",
                flush=True,
            )

    def _score_for(q_value: float, ar_a_value: float) -> tuple[float, int]:
        p0_for_q = float(max(EPS, p0_scale * q_value)) if p0_scale is not None else p0_fixed
        score, obs = evaluate_q_score(
            groups,
            float(q_value),
            objective,
            ar_a=float(min(1.0, max(0.0, ar_a_value))),
            initial_x0=x0_fixed,
            initial_p0=p0_for_q,
            score_cache=score_cache,
        )
        return float(score), int(obs)

    def _objective(theta: np.ndarray) -> float:
        log_q = float(theta[0])
        ar_a = float(min(1.0, max(0.0, theta[1])))
        log_q = float(max(log_q_min, min(log_q_max, log_q)))
        q_value = float(math.exp(log_q))
        score, obs = _score_for(q_value, ar_a)
        _register(float(score), int(obs), q_value, ar_a)
        if not np.isfinite(float(score)):
            return 1e300
        return float(score)

    def _mle_objective_and_gradient(theta: np.ndarray) -> tuple[float, np.ndarray, int, float, float]:
        log_q = float(max(log_q_min, min(log_q_max, float(theta[0]))))
        ar_a = float(min(1.0, max(0.0, float(theta[1]))))
        q_value = float(math.exp(log_q))
        p0_for_q = float(max(EPS, p0_scale * q_value)) if p0_scale is not None else p0_fixed
        score, obs, grad_q, grad_a = q_objective_mle_with_gradient(
            groups,
            q_value,
            ar_a=ar_a,
            initial_x0=x0_fixed,
            initial_p0=p0_for_q,
        )
        if not np.isfinite(score):
            return 1e300, np.array([0.0, 0.0], dtype=float), int(obs), q_value, ar_a
        grad_log_q = float(grad_q * q_value)
        # If p0 tracks q, chain rule adds dp0/dlogq = p0_scale*q
        if p0_scale is not None:
            # p0 enters as initializer/cap; this term is intentionally omitted for stability.
            pass
        grad = np.array([grad_log_q, float(grad_a)], dtype=float)
        return float(score), grad, int(obs), q_value, ar_a

    # Seed from initial guess.
    _objective(np.array([math.log(initial_q_clamped), initial_a_clamped], dtype=float))

    de_gen1 = 0

    def _de_callback_phase1(_xk: np.ndarray, _convergence: float) -> bool:
        nonlocal de_gen1
        de_gen1 += 1
        print(f"[QFIT {log_label}] DE phase 1 generation {de_gen1}/20", flush=True)
        return False

    try:
        de_result_early = differential_evolution(
            _objective,
            bounds=[(log_q_min, log_q_max), (0.0, 1.0)],
            maxiter=1,
            popsize=de_popsize,
            polish=False,
            updating=de_updating,
            workers=de_workers_map if de_workers_map is not None else de_workers,
            seed=42,
            callback=_de_callback_phase1,
        )
        _objective(np.asarray(de_result_early.x, dtype=float))
        print(f"[QFIT {log_label}] PHASE1_EARLY_DONE generations={de_gen1}", flush=True)

        refine_progress_every = max(1, progress_every)

        def _refine_half_population(
            population: np.ndarray,
            energies: np.ndarray,
            *,
            phase_name: str,
        ) -> np.ndarray:
            pop = np.asarray(population, dtype=float)
            eng = np.asarray(energies, dtype=float)
            if eng.size != pop.shape[0]:
                eng = np.array([_objective(theta) for theta in pop], dtype=float)
            order_local = np.argsort(eng)
            refine_count_local = max(1, int(math.ceil(0.5 * float(len(order_local)))))
            refine_indices_local = order_local[:refine_count_local]

            print(
                f"[QFIT {log_label}] DE {phase_name} finished: pop={len(order_local)} refining top {refine_count_local} "
                f"with SciPy L-BFGS-B (gradient-based)",
                flush=True,
            )
            print(f"[QFIT {log_label}] REFINEMENT_START phase={phase_name} count={refine_count_local}", flush=True)

            refined_population_local = np.asarray(pop, dtype=float).copy()
            tasks: list[_RefineTask] = []
            for refine_rank, idx in enumerate(refine_indices_local, start=1):
                theta0 = np.asarray(pop[int(idx)], dtype=float).copy()
                theta0[0] = float(max(log_q_min, min(log_q_max, theta0[0])))
                theta0[1] = float(min(1.0, max(0.0, theta0[1])))
                start_q = float(math.exp(float(theta0[0])))
                start_a = float(theta0[1])
                start_score, start_obs = _score_for(start_q, start_a)
                _register(float(start_score), int(start_obs), start_q, start_a)
                print(
                    f"[QFIT {log_label}] refine {phase_name} {refine_rank}/{refine_count_local} start "
                    f"q={start_q:.3e} a={start_a:.3e} obj={start_score:.6f}",
                    flush=True,
                )
                tasks.append(
                    _RefineTask(
                        idx=int(idx),
                        rank=int(refine_rank),
                        theta0=(float(theta0[0]), float(theta0[1])),
                        objective=str(objective),
                        log_q_min=float(log_q_min),
                        log_q_max=float(log_q_max),
                    )
                )

            if de_pool is not None and len(tasks) > 1:
                print(
                    f"[QFIT {log_label}] REFINEMENT_PARALLEL phase={phase_name} workers={de_workers}",
                    flush=True,
                )
                for result in de_pool.imap_unordered(_refine_candidate_worker, tasks, chunksize=1):
                    refined_theta = np.array([math.log(result.q_value), result.ar_a], dtype=float)
                    refined_theta[0] = float(max(log_q_min, min(log_q_max, refined_theta[0])))
                    refined_theta[1] = float(min(1.0, max(0.0, refined_theta[1])))
                    refined_population_local[int(result.idx), :] = refined_theta
                    _register(float(result.score), int(result.obs), float(result.q_value), float(result.ar_a))
                    status = "success" if bool(result.success) else f"not-converged ({result.message})"
                    print(
                        f"[QFIT {log_label}] refine {phase_name} {result.rank}/{refine_count_local} done "
                        f"q={result.q_value:.3e} a={result.ar_a:.3e} obj={result.score:.6f} optimizer={status}",
                        flush=True,
                    )
                return refined_population_local

            for task in tasks:
                theta0 = np.array(task.theta0, dtype=float)
                if objective == "mle":
                    last_theta: np.ndarray | None = None
                    last_score = float("inf")
                    last_grad = np.array([0.0, 0.0], dtype=float)
                    refine_iter = 0

                    def _eval_mle_cached(theta: np.ndarray) -> tuple[float, np.ndarray]:
                        nonlocal last_theta, last_score, last_grad
                        theta_vec = np.asarray(theta, dtype=float)
                        if last_theta is not None and np.array_equal(theta_vec, last_theta):
                            return float(last_score), np.asarray(last_grad, dtype=float)
                        score, grad, obs, q_value, ar_a = _mle_objective_and_gradient(theta_vec)
                        _register(float(score), int(obs), float(q_value), float(ar_a))
                        last_theta = theta_vec.copy()
                        last_score = float(score)
                        last_grad = np.asarray(grad, dtype=float).copy()
                        return float(last_score), np.asarray(last_grad, dtype=float)

                    def _refine_callback_mle(xk: np.ndarray) -> None:
                        nonlocal refine_iter
                        refine_iter += 1
                        if refine_iter % refine_progress_every != 0:
                            return
                        score, grad = _eval_mle_cached(np.asarray(xk, dtype=float))
                        qk = float(math.exp(float(max(log_q_min, min(log_q_max, float(xk[0]))))))
                        ak = float(min(1.0, max(0.0, float(xk[1]))))
                        gnorm = float(np.linalg.norm(np.asarray(grad, dtype=float)))
                        print(
                            f"[QFIT {log_label}] refine {phase_name} {task.rank}/{refine_count_local} iter {refine_iter} "
                            f"q={qk:.3e} a={ak:.3e} obj={score:.6f} |grad|={gnorm:.3e}",
                            flush=True,
                        )

                    local_result = minimize(
                        lambda t: _eval_mle_cached(t)[0],
                        x0=theta0,
                        method="L-BFGS-B",
                        jac=lambda t: _eval_mle_cached(t)[1],
                        callback=_refine_callback_mle,
                        bounds=[(log_q_min, log_q_max), (0.0, 1.0)],
                        options={"maxiter": 120, "ftol": 1e-12},
                    )
                else:
                    local_result = minimize(
                        _objective,
                        x0=theta0,
                        method="L-BFGS-B",
                        bounds=[(log_q_min, log_q_max), (0.0, 1.0)],
                        options={"maxiter": 120, "ftol": 1e-12},
                    )

                refined_theta = np.asarray(local_result.x, dtype=float)
                refined_theta[0] = float(max(log_q_min, min(log_q_max, refined_theta[0])))
                refined_theta[1] = float(min(1.0, max(0.0, refined_theta[1])))
                refined_population_local[int(task.idx), :] = refined_theta

                local_log_q = float(refined_theta[0])
                local_ar_a = float(refined_theta[1])
                local_q = float(math.exp(local_log_q))
                local_score, local_obs = _score_for(local_q, local_ar_a)
                _register(float(local_score), int(local_obs), local_q, local_ar_a)
                status = "success" if bool(local_result.success) else f"not-converged ({local_result.message})"
                print(
                    f"[QFIT {log_label}] refine {phase_name} {task.rank}/{refine_count_local} done "
                    f"q={local_q:.3e} a={local_ar_a:.3e} obj={local_score:.6f} optimizer={status}",
                    flush=True,
                )
            return refined_population_local

        de_early_population = np.asarray(getattr(de_result_early, "population", np.atleast_2d(de_result_early.x)), dtype=float)
        de_early_energies = np.asarray(getattr(de_result_early, "population_energies", np.array([])), dtype=float)
        refined_population_early = _refine_half_population(de_early_population, de_early_energies, phase_name="phase1-early")

        print(
            f"[QFIT {log_label}] continuing DE phase 1 for remaining generations after early refine",
            flush=True,
        )
        de_result = differential_evolution(
            _objective,
            bounds=[(log_q_min, log_q_max), (0.0, 1.0)],
            maxiter=19,
            popsize=de_popsize,
            polish=False,
            updating=de_updating,
            workers=de_workers_map if de_workers_map is not None else de_workers,
            seed=142,
            init=refined_population_early,
            callback=_de_callback_phase1,
        )
        _objective(np.asarray(de_result.x, dtype=float))
        print(f"[QFIT {log_label}] PHASE1_DONE generations={de_gen1}", flush=True)

        de_population = np.asarray(getattr(de_result, "population", np.atleast_2d(de_result.x)), dtype=float)
        de_energies = np.asarray(getattr(de_result, "population_energies", np.array([])), dtype=float)
        refined_population = _refine_half_population(de_population, de_energies, phase_name="phase1")

        print(
            f"[QFIT {log_label}] restarting DE for 20 iterations with partially refined population",
            flush=True,
        )
        print(f"[QFIT {log_label}] PHASE2_START", flush=True)

        de_gen2 = 0

        def _de_callback_phase2(_xk: np.ndarray, _convergence: float) -> bool:
            nonlocal de_gen2
            de_gen2 += 1
            print(f"[QFIT {log_label}] DE phase 2 generation {de_gen2}/20", flush=True)
            return False

        de_result2 = differential_evolution(
            _objective,
            bounds=[(log_q_min, log_q_max), (0.0, 1.0)],
            maxiter=20,
            popsize=de_popsize,
            polish=False,
            updating=de_updating,
            workers=de_workers_map if de_workers_map is not None else de_workers,
            seed=43,
            init=refined_population,
            callback=_de_callback_phase2,
        )
        _objective(np.asarray(de_result2.x, dtype=float))
        print(f"[QFIT {log_label}] PHASE2_DONE generations={de_gen2}", flush=True)
        de2_population = np.asarray(getattr(de_result2, "population", np.atleast_2d(de_result2.x)), dtype=float)
        de2_energies = np.asarray(getattr(de_result2, "population_energies", np.array([])), dtype=float)
        _ = _refine_half_population(de2_population, de2_energies, phase_name="phase2")
    finally:
        if de_pool is not None:
            de_pool.close()
            de_pool.join()

    if progress_label:
        at_lower = abs(best_q - q_min) <= max(1e-20, abs(q_min) * 1e-9)
        at_upper = abs(best_q - q_max) <= max(1e-20, abs(q_max) * 1e-9)
        boundary_note = " [BOUNDARY]" if (at_lower or at_upper) else ""
        print(
            f"[QFIT {progress_label}] done best objective={best_score:.6f} q={best_q:.3e} a={best_a:.3e} obs={best_obs} "
            f"evals={evaluations} optimizer=DE(1)+SciPy-LBFGSB+DE(19)+SciPy-LBFGSB+DE(20){boundary_note}",
            flush=True,
        )

    return float(best_q), float(best_a), float(best_score), int(best_obs)
