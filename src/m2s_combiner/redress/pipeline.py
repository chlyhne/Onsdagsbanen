from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .collect import build_competitor_year_group_map
from .collect import build_group_data
from .common import predict_sailed_seconds_from_corrected
from .common import race_num
from .constants import EPS
from .constants import NON_OBS_STATUSES
from .constants import PLOT_ACTIVE_YEAR
from .constants import Q_SEARCH_MAX
from .constants import Q_SEARCH_MIN
from .constants import Z50
from .exports import export_boat_plot_data
from .exports import export_latest_race_table
from .exports import export_missing_race_prediction_tables
from .exports import export_ml_fit_example
from .model import estimate_global_q
from .model import evaluate_q_score
from .model import fit_global_q
from .model import load_group_q_cache
from .model import q_cache_path_for_objective
from .model import q_diagnostics
from .model import resolve_q_objective
from .model import run_all_groups_with_transfer
from .model import save_group_q_cache


def _fit_group_qs(groups: list[dict[str, Any]], *, output_dir: Path, q_objective: str) -> tuple[dict[str, float], list[dict[str, Any]], pd.DataFrame, bool, Path]:
    q_grid = np.logspace(math.log10(Q_SEARCH_MIN), math.log10(Q_SEARCH_MAX), 61, dtype=float)
    q_cache_path = q_cache_path_for_objective(output_dir, q_objective)
    cached_q_map = load_group_q_cache(q_cache_path)
    missing_q_groups = [str(group["group"]) for group in groups if str(group["group"]) not in cached_q_map]
    using_cached_q = bool(cached_q_map) and not missing_q_groups

    q_by_group: dict[str, float] = {}
    q_fit_rows: list[dict[str, Any]] = []
    q_diag_frames: list[pd.DataFrame] = []

    if using_cached_q:
        for group in groups:
            group_name = str(group["group"])
            initial_q = estimate_global_q(group["combined"])
            group_q = float(cached_q_map[group_name])
            rmse_score, rmse_obs = evaluate_q_score([group], group_q, "rmse")
            mle_score, mle_obs = evaluate_q_score([group], group_q, "mle")
            fit_score, fit_obs = (mle_score, mle_obs) if q_objective == "mle" else (rmse_score, rmse_obs)
            q_by_group[group_name] = group_q
            q_fit_rows.append(
                {
                    "group": group_name,
                    "initial_q": float(initial_q),
                    "fitted_q": group_q,
                    "fit_objective": q_objective,
                    "fit_score": float(fit_score),
                    "fit_obs": int(fit_obs),
                    "one_step_rmse_seconds": float(rmse_score),
                    "rmse_observations": int(rmse_obs),
                    "negative_log_likelihood": float(mle_score),
                    "nll_observations": int(mle_obs),
                    "q_source": "cache",
                }
            )
            q_diag_frames.append(
                pd.DataFrame(
                    [
                        {
                            "group": group_name,
                            "q_value": group_q,
                            "fit_objective": q_objective,
                            "one_step_rmse_seconds": float(rmse_score),
                            "rmse_observations": int(rmse_obs),
                            "negative_log_likelihood": float(mle_score),
                            "nll_observations": int(mle_obs),
                            "observations": int(rmse_obs),
                            "source": "cache",
                        }
                    ]
                )
            )
        return q_by_group, q_fit_rows, pd.concat(q_diag_frames, ignore_index=True), using_cached_q, q_cache_path

    for group in groups:
        group_name = str(group["group"])
        initial_q = estimate_global_q(group["combined"])
        rmse_score_cache: dict[float, tuple[float, int]] = {}
        mle_score_cache: dict[float, tuple[float, int]] = {}
        fit_score_cache = mle_score_cache if q_objective == "mle" else rmse_score_cache
        group_q, fit_score, fit_obs = fit_global_q([group], initial_q=initial_q, objective=q_objective, score_cache=fit_score_cache)
        rmse_score, rmse_obs = evaluate_q_score([group], group_q, "rmse", score_cache=rmse_score_cache)
        mle_score, mle_obs = evaluate_q_score([group], group_q, "mle", score_cache=mle_score_cache)
        q_by_group[group_name] = float(group_q)
        q_fit_rows.append(
            {
                "group": group_name,
                "initial_q": float(initial_q),
                "fitted_q": float(group_q),
                "fit_objective": q_objective,
                "fit_score": float(fit_score),
                "fit_obs": int(fit_obs),
                "one_step_rmse_seconds": float(rmse_score),
                "rmse_observations": int(rmse_obs),
                "negative_log_likelihood": float(mle_score),
                "nll_observations": int(mle_obs),
                "q_source": "fitted",
            }
        )
        group_diag = q_diagnostics([group], q_grid, rmse_cache=rmse_score_cache, mle_cache=mle_score_cache)
        group_diag["group"] = group_name
        group_diag["fit_objective"] = q_objective
        group_diag["source"] = "fitted"
        q_diag_frames.append(group_diag)

    save_group_q_cache(q_cache_path, q_by_group)
    q_diag = pd.concat(q_diag_frames, ignore_index=True) if q_diag_frames else pd.DataFrame()
    return q_by_group, q_fit_rows, q_diag, using_cached_q, q_cache_path


def _enrich_history(history: pd.DataFrame, competitor_year_group: dict[tuple[str, int], str]) -> pd.DataFrame:
    if history.empty:
        raise RuntimeError("State history is empty after running the redress filter.")

    scoped = history.copy()
    scope_mask = [
        competitor_year_group.get((str(competitor), int(year))) == str(group)
        for competitor, year, group in zip(scoped["competitor"], scoped["year"], scoped["group"])
    ]
    scoped = scoped.loc[scope_mask].copy().reset_index(drop=True)
    if scoped.empty:
        raise RuntimeError("State history became empty after applying competitor-to-group scoping.")

    scoped["y_pred"] = scoped["b_t_hat"] + scoped["x_prior"]
    scoped["pred_beregnet_seconds"] = np.exp(scoped["y_pred"])
    scoped.loc[~np.isfinite(scoped["pred_beregnet_seconds"]), "pred_beregnet_seconds"] = np.nan

    scoped["y_pred_cf"] = scoped["y_pred"]
    observed_with_loo = (scoped["observed"] == True) & scoped["y_pred_loo"].notna()  # noqa: E712
    scoped.loc[observed_with_loo, "y_pred_cf"] = scoped.loc[observed_with_loo, "y_pred_loo"]
    scoped["pred_cf_beregnet_seconds"] = np.exp(scoped["y_pred_cf"])
    scoped.loc[~np.isfinite(scoped["pred_cf_beregnet_seconds"]), "pred_cf_beregnet_seconds"] = np.nan

    scoped["pred_cf_sailed_seconds"] = scoped.apply(
        lambda row: predict_sailed_seconds_from_corrected(row.get("pred_cf_beregnet_seconds"), row.get("hdcp"), row.get("length_nm")),
        axis=1,
    )
    scoped["pred_cf_sailed_seconds"] = pd.to_numeric(scoped["pred_cf_sailed_seconds"], errors="coerce")
    scoped.loc[~np.isfinite(scoped["pred_cf_sailed_seconds"]), "pred_cf_sailed_seconds"] = np.nan

    scoped["s_pred"] = scoped["p_prior"] + scoped["r_t"]
    scoped["sigma_pred"] = np.sqrt(scoped["s_pred"].clip(lower=EPS))
    scoped["pred_cf_expect_seconds"] = np.exp(scoped["y_pred_cf"] + 0.5 * np.square(scoped["sigma_pred"]))
    scoped["pred_cf_q25_seconds"] = np.exp(scoped["y_pred_cf"] - Z50 * scoped["sigma_pred"])
    scoped["pred_cf_q75_seconds"] = np.exp(scoped["y_pred_cf"] + Z50 * scoped["sigma_pred"])
    for column in ["pred_cf_expect_seconds", "pred_cf_q25_seconds", "pred_cf_q75_seconds"]:
        scoped.loc[~np.isfinite(scoped[column]), column] = np.nan

    scoped["x_obs"] = scoped["x_prior"] + scoped["innovation"]
    scoped.loc[scoped["observed"] != True, "x_obs"] = np.nan  # noqa: E712
    scoped["sigma_process"] = np.sqrt(scoped["p_prior"].clip(lower=EPS))
    scoped["sigma_total"] = np.sqrt(scoped["s_pred"].clip(lower=EPS))
    scoped["x_proc_q25"] = scoped["x_prior"] - Z50 * scoped["sigma_process"]
    scoped["x_proc_q75"] = scoped["x_prior"] + Z50 * scoped["sigma_process"]
    scoped["x_total_q25"] = scoped["x_prior"] - Z50 * scoped["sigma_total"]
    scoped["x_total_q75"] = scoped["x_prior"] + Z50 * scoped["sigma_total"]
    scoped["x_q25"] = scoped["x_proc_q25"]
    scoped["x_q75"] = scoped["x_proc_q75"]
    scoped.loc[~np.isfinite(scoped["x_obs"]), "x_obs"] = np.nan
    for column in ["x_proc_q25", "x_proc_q75", "x_total_q25", "x_total_q75", "x_q25", "x_q75"]:
        scoped.loc[~np.isfinite(scoped[column]), column] = np.nan

    scoped["z_innovation"] = scoped["innovation"] / np.sqrt(scoped["s_pred"].clip(lower=EPS))
    scoped["error_seconds"] = scoped["beregnet_seconds"] - scoped["pred_cf_beregnet_seconds"]
    scoped["abs_error_seconds"] = scoped["error_seconds"].abs()
    return scoped


def _write_core_outputs(history: pd.DataFrame, q_diag: pd.DataFrame, *, output_dir: Path) -> dict[str, Path]:
    observed_predictions = history[history["observed"] == True].copy()  # noqa: E712
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
        .agg(b_t_hat=("b_t_hat", "first"), r_t=("r_t", "first"), boats_observed=("observed", "sum"), boats_total=("competitor", "count"))
        .assign(race_num=lambda frame: frame["race"].map(race_num))
        .sort_values(["group", "race_num", "race"])
        .reset_index(drop=True)
    )

    paths = {
        "history": output_dir / "redress_2025_state_history.csv",
        "observed": output_dir / "redress_2025_observed_predictions.csv",
        "latest": output_dir / "redress_2025_latest_estimates.csv",
        "race": output_dir / "redress_2025_race_effects.csv",
        "q_diag": output_dir / "redress_2025_q_diagnostics.csv",
    }
    history.to_csv(paths["history"], index=False)
    observed_predictions.to_csv(paths["observed"], index=False)
    latest.to_csv(paths["latest"], index=False)
    per_race.to_csv(paths["race"], index=False)
    q_diag.to_csv(paths["q_diag"], index=False)
    return paths


def run_pipeline(*, output_dir: Path, q_objective: str) -> int:
    q_objective = resolve_q_objective(q_objective)
    output_dir.mkdir(parents=True, exist_ok=True)

    groups, all_data = build_group_data()
    if not groups or all_data.empty:
        raise RuntimeError("No group data could be built.")

    q_by_group, q_fit_rows, q_diag, using_cached_q, q_cache_path = _fit_group_qs(groups, output_dir=output_dir, q_objective=q_objective)
    competitor_year_group = build_competitor_year_group_map(all_data, NON_OBS_STATUSES)
    history, _, _ = run_all_groups_with_transfer(groups, q_by_group, competitor_year_group, collect_history=True)
    history = history.sort_values(["group", "competitor", "race_date", "race"], ascending=[True, True, True, True]).reset_index(drop=True)
    history = _enrich_history(history, competitor_year_group)

    core_paths = _write_core_outputs(history, q_diag, output_dir=output_dir)
    active_2026_competitors = {competitor for (competitor, year), _group in competitor_year_group.items() if int(year) == int(PLOT_ACTIVE_YEAR)}

    all_predictions = history.copy()
    manifest_path = export_boat_plot_data(all_predictions, allowed_competitors=active_2026_competitors, output_dir=output_dir / "boat_plot_data")
    latest_race_paths = [
        export_latest_race_table(all_predictions, output_path=output_dir / "latest_race_2026_r2_stor_bane1_table.csv", year=int(PLOT_ACTIVE_YEAR), race_local="R2", group_filter="Stor Bane", series_filter="Stor bane 1"),
        export_latest_race_table(all_predictions, output_path=output_dir / "latest_race_2026_r2_stor_bane2_table.csv", year=int(PLOT_ACTIVE_YEAR), race_local="R2", group_filter="Stor Bane", series_filter="Stor bane 2"),
        export_latest_race_table(all_predictions, output_path=output_dir / "latest_race_2026_r2_lille_bane1_table.csv", year=int(PLOT_ACTIVE_YEAR), race_local="R2", group_filter="Lille Bane", series_filter="Lille bane 1"),
        export_latest_race_table(all_predictions, output_path=output_dir / "latest_race_2026_r2_lille_bane2_table.csv", year=int(PLOT_ACTIVE_YEAR), race_local="R2", group_filter="Lille Bane", series_filter="Lille bane 2"),
    ]
    missing_race_prediction_tables_tex_path = export_missing_race_prediction_tables(history, output_dir=output_dir, years=(2026,))
    ml_fit_example_paths = export_ml_fit_example(history, output_dir=output_dir)

    if using_cached_q:
        print(f"Loaded group Q from cache: {q_cache_path} (fit objective={q_objective})")
    else:
        print(f"Saved fitted group Q to cache: {q_cache_path} (fit objective={q_objective})")

    for row in q_fit_rows:
        metric_text = f"NLL={float(row['fit_score']):.3f}" if str(row["fit_objective"]) == "mle" else f"1-step RMSE={float(row['fit_score']):.3f}"
        print(
            f"{row['group']}: initial Q={row['initial_q']:.3e}, "
            f"fitted Q={row['fitted_q']:.3e}, "
            f"fit-objective={row['fit_objective']}, {metric_text} over {int(row['fit_obs'])} observations, "
            f"RMSE@Q={float(row['one_step_rmse_seconds']):.3f}, "
            f"NLL@Q={float(row['negative_log_likelihood']):.3f} "
            f"({row['q_source']})"
        )

    for path in [core_paths["history"], core_paths["observed"], core_paths["latest"], core_paths["race"], core_paths["q_diag"], manifest_path, *latest_race_paths, missing_race_prediction_tables_tex_path, *ml_fit_example_paths]:
        print(f"Wrote: {path}")
    print("Note: This pipeline exports analysis artifacts and TeX fragments, but does not build the PDF report.")
    return 0
