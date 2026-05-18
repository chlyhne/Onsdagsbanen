from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .common import abbreviate_name
from .common import display_name
from .common import format_rank_error
from .common import format_seconds_hms
from .common import format_seconds_signed_compact
from .common import latex_escape_text
from .common import latex_table_cell
from .common import race_num
from .common import slugify_filename
from .constants import EPS
from .constants import Z50


def _rank_predictions_against_actual_field(group: pd.DataFrame) -> pd.Series:
    result = pd.Series(np.nan, index=group.index, dtype=float)
    observed_group = group[group["observed"] == True].copy()  # noqa: E712
    if observed_group.empty:
        return result

    actual_times = pd.to_numeric(observed_group["beregnet_seconds"], errors="coerce").to_numpy(dtype=float)
    actual_times = actual_times[np.isfinite(actual_times)]
    if actual_times.size == 0:
        return result

    predicted_times = pd.to_numeric(observed_group["pred_cf_beregnet_seconds"], errors="coerce").to_numpy(dtype=float)
    finite_mask = np.isfinite(predicted_times)
    if not finite_mask.any():
        return result

    predicted_ranks = np.sum(actual_times[:, None] < predicted_times[finite_mask], axis=0) + 1.0
    result.loc[observed_group.index[finite_mask]] = predicted_ranks
    return result


def export_boat_plot_data(frame: pd.DataFrame, *, allowed_competitors: set[str] | None, output_dir: Path) -> Path:
    required_columns = [
        "competitor", "group", "series", "race", "race_local", "race_date", "year", "observed",
        "x_prior", "x_post", "p_prior",
        "gamma_prior", "gamma_post", "p_gamma_prior",
        "r_t", "b_t_hat", "beregnet_seconds",
    ]
    missing = [column for column in required_columns if column not in frame.columns]
    if missing:
        raise ValueError(f"Missing required columns for boat-plot export: {', '.join(missing)}")
    if frame.empty:
        raise ValueError("Cannot export boat-plot data from empty frame.")

    output_dir.mkdir(parents=True, exist_ok=True)
    work = frame.copy()
    work["race_num"] = work["race"].map(race_num)
    work = work.dropna(subset=["race_num"])
    if work.empty:
        raise ValueError("No valid race labels found after parsing race numbers.")

    competitors = sorted(work["competitor"].dropna().astype(str).unique().tolist())
    if allowed_competitors is not None:
        competitors = [name for name in competitors if name in allowed_competitors]
    if not competitors:
        raise ValueError("No competitors matched allowed_competitors for boat-plot export.")

    manifest_rows: list[dict[str, Any]] = []
    used_slug_names: set[str] = set()
    for competitor in competitors:
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
        events = c.loc[:, event_cols].drop_duplicates().sort_values(["race_date_dt", "year", "race_num", "race_local_display", "race", "group"], na_position="last").reset_index(drop=True)
        if events.empty:
            raise ValueError(f"No race events could be formed for competitor '{competitor}'.")
        events["x_pos"] = np.arange(1, len(events) + 1, dtype=int)
        c = c.merge(events.loc[:, ["year", "race", "race_date", "group", "x_pos", "race_local_display"]], on=["year", "race", "race_date", "group"], how="left")
        c = c.dropna(subset=["x_pos"]).copy()
        c["x_pos"] = c["x_pos"].astype(int)
        c = c.sort_values(["x_pos"]).reset_index(drop=True)

        # Plot in log-deviation space:
        # - Data points: raw deviation from day baseline (without gamma compensation).
        # - Priors/Posteriors: include gamma loading sqrt(r_t) * gamma.
        gamma_loading = np.sqrt(np.clip(pd.to_numeric(c["r_t"], errors="coerce"), EPS, None))
        x_prior = pd.to_numeric(c["x_prior"], errors="coerce")
        x_post = pd.to_numeric(c["x_post"], errors="coerce")
        gamma_prior = pd.to_numeric(c["gamma_prior"], errors="coerce")
        gamma_post = pd.to_numeric(c["gamma_post"], errors="coerce")
        p_prior = pd.to_numeric(c["p_prior"], errors="coerce")
        p_gamma_prior = pd.to_numeric(c["p_gamma_prior"], errors="coerce")
        b_t_hat = pd.to_numeric(c["b_t_hat"], errors="coerce")
        beregnet_seconds = pd.to_numeric(c["beregnet_seconds"], errors="coerce")
        y_obs = np.log(np.clip(beregnet_seconds, EPS, None))

        c["plot_prior"] = x_prior + gamma_loading * gamma_prior
        c["plot_post"] = x_post + gamma_loading * gamma_post
        c["plot_obs"] = y_obs - b_t_hat
        c.loc[c["observed"] != True, "plot_obs"] = np.nan  # noqa: E712

        sigma_process_total = np.sqrt(np.clip(p_prior + np.square(gamma_loading) * p_gamma_prior, EPS, None))
        sigma_total = np.sqrt(np.clip(p_prior + np.square(gamma_loading) * p_gamma_prior + pd.to_numeric(c["r_t"], errors="coerce"), EPS, None))
        c["plot_proc_q25"] = c["plot_prior"] - Z50 * sigma_process_total
        c["plot_proc_q75"] = c["plot_prior"] + Z50 * sigma_process_total
        c["plot_total_q25"] = c["plot_prior"] - Z50 * sigma_total
        c["plot_total_q75"] = c["plot_prior"] + Z50 * sigma_total

        base = events.loc[:, ["x_pos", "year", "race_local_display", "group"]].rename(columns={"race_local_display": "race_local"}).copy()
        base["race_display"] = base.apply(lambda row: f"'{int(row['year']) % 100:02d}-{str(row['race_local'])}", axis=1)
        base = base.drop(columns=["year"])
        series_values = c.loc[:, ["x_pos", "series"]].drop_duplicates(subset=["x_pos"], keep="first").copy()
        values = c.loc[:, ["x_pos", "observed", "plot_prior", "plot_post", "plot_obs", "plot_proc_q25", "plot_proc_q75", "plot_total_q25", "plot_total_q75"]].drop_duplicates(subset=["x_pos"], keep="first")
        values["plot_post"] = np.where(values["observed"].astype(bool), values["plot_post"], np.nan)
        values = values.drop(columns=["observed"])
        plot_data = base.merge(series_values, on="x_pos", how="left").merge(values, on="x_pos", how="left").sort_values("x_pos").reset_index(drop=True)
        plot_data = plot_data.rename(
            columns={
                "plot_prior": "x_prior",
                "plot_post": "x_post",
                "plot_obs": "x_obs",
                "plot_proc_q25": "x_proc_q25",
                "plot_proc_q75": "x_proc_q75",
                "plot_total_q25": "x_total_q25",
                "plot_total_q75": "x_total_q75",
            }
        )
        plot_data["x_q25"] = plot_data["x_proc_q25"]
        plot_data["x_q75"] = plot_data["x_proc_q75"]

        local_y_values = (
            plot_data["x_prior"].dropna().astype(float).tolist()
            + plot_data["x_post"].dropna().astype(float).tolist()
            + plot_data["x_proc_q25"].dropna().astype(float).tolist()
            + plot_data["x_proc_q75"].dropna().astype(float).tolist()
            + plot_data["x_total_q25"].dropna().astype(float).tolist()
            + plot_data["x_total_q75"].dropna().astype(float).tolist()
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

        base_slug = slugify_filename(competitor) or "competitor"
        slug = base_slug
        suffix = 2
        while slug in used_slug_names:
            slug = f"{base_slug}-{suffix}"
            suffix += 1
        used_slug_names.add(slug)

        data_name = f"boat_plot_{slug}.csv"
        data_path = output_dir / data_name
        plot_data.to_csv(data_path, index=False)

        percent_data = plot_data.loc[:, ["x_pos", "race_local", "race_display", "group", "series"]].copy()
        for source_col, target_col in [
            ("x_prior", "prior_pct"), ("x_post", "post_pct"), ("x_obs", "obs_pct"), ("x_proc_q25", "proc_q25_pct"), ("x_proc_q75", "proc_q75_pct"),
            ("x_total_q25", "total_q25_pct"), ("x_total_q75", "total_q75_pct"), ("x_q25", "q25_pct"), ("x_q75", "q75_pct"),
        ]:
            values_pct = 100.0 * (np.exp(pd.to_numeric(plot_data[source_col], errors="coerce")) - 1.0)
            values_pct = pd.to_numeric(values_pct, errors="coerce")
            values_pct = values_pct.where(np.isfinite(values_pct), np.nan)
            percent_data[target_col] = values_pct
        percent_name = f"boat_plot_{slug}_percent.csv"
        percent_path = output_dir / percent_name
        percent_data.to_csv(percent_path, index=False)

        group_for_competitor = str(plot_data["group"].dropna().astype(str).iloc[0]) if not plot_data["group"].dropna().empty else ""
        if not group_for_competitor:
            raise ValueError(f"Missing group values for competitor '{competitor}'.")
        manifest_rows.append(
            {
                "competitor": competitor,
                "competitor_display": display_name(competitor),
                "group": group_for_competitor,
                "group_display": display_name(group_for_competitor),
                "data_csv": data_name,
                "percent_data_csv": percent_name,
                "x_max": int(plot_data["x_pos"].max()),
                "y_min": float(local_y_min),
                "y_max": float(local_y_max),
            }
        )

    if not manifest_rows:
        raise ValueError("No competitor plot data exported.")

    manifest_path = output_dir / "boat_plot_manifest.csv"
    pd.DataFrame(manifest_rows).to_csv(manifest_path, index=False)
    return manifest_path


def export_stor_bane_x_gamma_trajectories(
    frame: pd.DataFrame,
    *,
    output_dir: Path,
    years: tuple[int, ...] = (2025, 2026),
    min_observed_races: int = 6,
    group_name: str = "Stor Bane",
) -> tuple[Path, Path, Path, Path, Path]:
    required_columns = ["group", "competitor", "race", "race_date", "year", "observed", "x_post", "gamma_post"]
    missing = [column for column in required_columns if column not in frame.columns]
    if missing:
        raise ValueError(f"Missing required columns for x-gamma trajectory export: {', '.join(missing)}")
    if frame.empty:
        raise ValueError("Cannot export x-gamma trajectories from empty frame.")

    output_dir.mkdir(parents=True, exist_ok=True)
    work = frame.copy()
    work["group"] = work["group"].astype(str)
    work["competitor"] = work["competitor"].astype(str)
    work["year_num"] = pd.to_numeric(work["year"], errors="coerce")
    work["observed_bool"] = work["observed"] == True  # noqa: E712
    work["x_post"] = pd.to_numeric(work["x_post"], errors="coerce")
    work["gamma_post"] = pd.to_numeric(work["gamma_post"], errors="coerce")
    work["race_date_dt"] = pd.to_datetime(work["race_date"], errors="coerce")
    work["race_num"] = work["race"].map(race_num)

    year_set = {int(value) for value in years}
    work = work[
        (work["group"] == str(group_name))
        & (work["year_num"].isin(year_set))
        & (work["observed_bool"])
    ].copy()
    work = work.dropna(subset=["x_post", "gamma_post", "race_date_dt", "race_num"])
    if work.empty:
        raise ValueError(f"No observed rows for group '{group_name}' in years {sorted(year_set)}.")

    counts = work.groupby("competitor", as_index=True).size()
    competitors = sorted(counts[counts > int(max(0, min_observed_races - 1))].index.tolist())
    work = work[work["competitor"].isin(competitors)].copy()
    if work.empty:
        raise ValueError(
            f"No competitors in group '{group_name}' with more than {int(min_observed_races - 1)} observed races in years {sorted(year_set)}."
        )

    work = work.sort_values(["competitor", "race_date_dt", "race_num", "race"]).reset_index(drop=True)

    detailed_rows: list[dict[str, Any]] = []
    line_rows: list[dict[str, float | str]] = []
    endpoint_rows: list[dict[str, float | str]] = []
    per_competitor_dir = output_dir / "x_gamma_trajectories"
    per_competitor_dir.mkdir(parents=True, exist_ok=True)
    manifest_rows: list[dict[str, str]] = []
    used_slugs: set[str] = set()
    for competitor, sub in work.groupby("competitor", sort=True):
        per_rows: list[dict[str, float | int | str]] = []
        for step_idx, (_, row) in enumerate(sub.iterrows(), start=1):
            x_value = float(row["x_post"])
            gamma_value = float(row["gamma_post"])
            per_rows.append({"x": x_value, "gamma": gamma_value, "step": int(step_idx)})
            detailed_rows.append(
                {
                    "competitor": competitor,
                    "step": int(step_idx),
                    "race_date": row["race_date_dt"].date().isoformat(),
                    "race": str(row["race"]),
                    "x": x_value,
                    "gamma": gamma_value,
                    "is_origin": False,
                }
            )
            line_rows.append({"x": x_value, "gamma": gamma_value, "competitor": competitor})
        # NaN row splits polylines in pgfplots with unbounded coords=jump.
        line_rows.append({"x": float("nan"), "gamma": float("nan"), "competitor": ""})
        endpoint = sub.iloc[-1]
        endpoint_rows.append(
            {
                "competitor": competitor,
                "x": float(endpoint["x_post"]),
                "gamma": float(endpoint["gamma_post"]),
            }
        )
        base_slug = slugify_filename(competitor) or "competitor"
        slug = base_slug
        suffix = 2
        while slug in used_slugs:
            slug = f"{base_slug}-{suffix}"
            suffix += 1
        used_slugs.add(slug)
        trajectory_name = f"stor_bane_x_gamma_traj_{slug}.csv"
        trajectory_path = per_competitor_dir / trajectory_name
        pd.DataFrame(per_rows).to_csv(trajectory_path, index=False)
        manifest_rows.append(
            {
                "competitor": competitor,
                "competitor_display": display_name(competitor),
                "competitor_label": latex_escape_text(display_name(competitor)),
                "trajectory_csv": f"../analysis/x_gamma_trajectories/{trajectory_name}",
            }
        )

    detailed = pd.DataFrame(detailed_rows)
    line_data = pd.DataFrame(line_rows)
    endpoints = pd.DataFrame(endpoint_rows).sort_values(["competitor"]).reset_index(drop=True)
    manifest = pd.DataFrame(manifest_rows).sort_values(["competitor_display"]).reset_index(drop=True)

    valid_line_data = line_data.dropna(subset=["x", "gamma"])
    if valid_line_data.empty:
        raise ValueError("Cannot derive x-gamma trajectory bounds from empty line data.")

    x_min = float(valid_line_data["x"].min())
    x_max = float(valid_line_data["x"].max())
    y_min = float(valid_line_data["gamma"].min())
    y_max = float(valid_line_data["gamma"].max())
    x_extent = max(abs(x_min), abs(x_max), 1e-6)
    y_extent = max(abs(y_min), abs(y_max), 1e-6)
    x_limit = max(1.18 * x_extent, 0.03)
    y_limit = max(1.18 * y_extent, 0.08)
    bounds = pd.DataFrame(
        [
            {
                "xmin": -x_limit,
                "xmax": x_limit,
                "ymin": -y_limit,
                "ymax": y_limit,
            }
        ]
    )

    detailed_path = output_dir / "stor_bane_x_gamma_trajectories_2025_2026.csv"
    line_path = output_dir / "stor_bane_x_gamma_trajectories_2025_2026_plot.csv"
    endpoints_path = output_dir / "stor_bane_x_gamma_trajectories_2025_2026_endpoints.csv"
    manifest_path = output_dir / "stor_bane_x_gamma_trajectories_2025_2026_manifest.csv"
    bounds_path = output_dir / "stor_bane_x_gamma_trajectories_2025_2026_bounds.csv"
    detailed.to_csv(detailed_path, index=False)
    line_data.to_csv(line_path, index=False)
    endpoints.to_csv(endpoints_path, index=False)
    manifest.to_csv(manifest_path, index=False)
    bounds.to_csv(bounds_path, index=False)
    return detailed_path, line_path, endpoints_path, manifest_path, bounds_path


def export_missing_race_prediction_tables(frame: pd.DataFrame, *, output_dir: Path, years: tuple[int, ...]) -> Path:
    required_columns = ["competitor", "group", "series", "race_local", "year", "observed", "sailed_seconds", "beregnet_seconds", "pred_cf_sailed_seconds", "pred_cf_beregnet_seconds"]
    missing = [column for column in required_columns if column not in frame.columns]
    if missing:
        raise ValueError(f"Missing required columns for missing-race table export: {', '.join(missing)}")

    work = frame.copy()
    work["year_num"] = pd.to_numeric(work["year"], errors="coerce")
    work = work[work["year_num"].isin([int(year) for year in years])].copy()
    if work.empty:
        raise ValueError(f"No rows found for missing-race table export for years {years}.")

    work["series_norm"] = work["series"].fillna("").astype(str).str.strip()
    observed_with_series = work[work["series_norm"] != ""].copy()
    competitor_year_series = observed_with_series.sort_values(["competitor", "year_num", "series_norm"]).drop_duplicates(subset=["competitor", "year_num"], keep="first").loc[:, ["competitor", "year_num", "series_norm"]].rename(columns={"series_norm": "series_filled"})
    work = work.merge(competitor_year_series, on=["competitor", "year_num"], how="left")
    work["series_filled"] = np.where(work["series_norm"] != "", work["series_norm"], work["series_filled"])
    work = work[work["series_filled"].notna() & (work["series_filled"].astype(str).str.strip() != "")].copy()
    if work.empty:
        raise ValueError(f"No rows with resolved series found for missing-race table export for years {years}.")

    work["race_local_norm"] = work["race_local"].astype(str).str.strip()
    work["race_num"] = work["race_local_norm"].map(race_num)
    work["sailed_cell"] = work.apply(lambda row: format_seconds_hms(row["sailed_seconds"]) if row["observed"] == True else "", axis=1)  # noqa: E712
    work["beregnet_cell"] = work.apply(lambda row: format_seconds_hms(row["beregnet_seconds"]) if row["observed"] == True else "", axis=1)  # noqa: E712
    work["cf_sailed_cell"] = work["pred_cf_sailed_seconds"].map(format_seconds_hms)
    work["cf_beregnet_cell"] = work["pred_cf_beregnet_seconds"].map(format_seconds_hms)
    work["group_display"] = work["group"].astype(str).map(display_name)
    work["series_display"] = work["series_filled"].astype(str).map(display_name)
    work["competitor_display"] = work["competitor"].astype(str).map(abbreviate_name)

    output_dir.mkdir(parents=True, exist_ok=True)
    observed_mask = work["observed"] == True  # noqa: E712
    work["actual_lane_rank"] = np.nan
    work["estimated_lane_rank"] = np.nan
    lane_rank_group_cols = ["year_num", "race_local_norm", "group", "series_filled"]
    work.loc[observed_mask, "actual_lane_rank"] = (
        work.loc[observed_mask]
        .groupby(lane_rank_group_cols)["beregnet_seconds"]
        .rank(method="min", ascending=True)
    )
    estimated_lane_rank = (
        work.groupby(lane_rank_group_cols, group_keys=False)
        .apply(_rank_predictions_against_actual_field)
    )
    work.loc[estimated_lane_rank.index, "estimated_lane_rank"] = estimated_lane_rank
    work["point_error"] = work["estimated_lane_rank"] - work["actual_lane_rank"]

    table_columns = ["Navn", "Tid", "Est. Tid", "Hdcp. Tid", "Est. Hdcp. Tid", "Fejl", "Point Fejl"]
    rendered_tables: list[tuple[str, pd.DataFrame]] = []
    race_keys = work.loc[:, ["year_num", "race_num", "race_local_norm"]].drop_duplicates().sort_values(["year_num", "race_num", "race_local_norm"]).itertuples(index=False, name=None)
    for year_num, race_num_value, race_local_norm in race_keys:
        race_work = work[(work["year_num"] == year_num) & (work["race_local_norm"].astype(str) == str(race_local_norm))].copy()
        if race_work.empty:
            continue

        highlighted_cf_sailed = race_work.apply(lambda row: f"\\cellcolor{{yellow!35}} {format_seconds_hms(row['pred_cf_sailed_seconds'])}" if row["observed"] != True and format_seconds_hms(row["pred_cf_sailed_seconds"]) else format_seconds_hms(row["pred_cf_sailed_seconds"]), axis=1)  # noqa: E712
        sailed_delta = race_work.apply(lambda row: format_seconds_signed_compact(row["sailed_seconds"] - row["pred_cf_sailed_seconds"]) if row["observed"] == True else "", axis=1)  # noqa: E712
        point_error = race_work.apply(lambda row: format_rank_error(row["point_error"]) if row["observed"] == True else "", axis=1)  # noqa: E712
        table = race_work.sort_values(["group_display", "series_display", "competitor_display"]).assign(kontrafaktisk_sejletid_highlight=highlighted_cf_sailed, tidsafvigelse=sailed_delta, pointfejl=point_error).loc[:, ["competitor_display", "sailed_cell", "kontrafaktisk_sejletid_highlight", "beregnet_cell", "cf_beregnet_cell", "tidsafvigelse", "pointfejl"]].rename(columns={"competitor_display": "Navn", "sailed_cell": "Tid", "kontrafaktisk_sejletid_highlight": "Est. Tid", "beregnet_cell": "Hdcp. Tid", "cf_beregnet_cell": "Est. Hdcp. Tid", "tidsafvigelse": "Fejl", "pointfejl": "Point Fejl"}).reset_index(drop=True)
        table = table.reindex(columns=table_columns)
        data_name = f"missing_race_predictions_{int(year_num)}_{slugify_filename(str(race_local_norm))}.csv"
        table.to_csv(output_dir / data_name, index=False)
        rendered_tables.append((f"{int(year_num)} {str(race_local_norm)}", table.copy()))

    if not rendered_tables:
        raise ValueError(f"No missing-race prediction tables were exported for years {years}.")

    tex_lines: list[str] = []
    for table_title, table in rendered_tables:
        tex_lines.extend([
            r"\clearpage",
            r"{\scriptsize",
            r"\setlength{\tabcolsep}{3pt}",
            r"\setlength{\arrayrulewidth}{0.8pt}",
            r"\renewcommand{\arraystretch}{0.95}",
            r"\rowcolors{2}{white}{gray!12}",
            r"\begin{center}",
            rf"\textbf{{{latex_escape_text(table_title)}}}\\[0.6ex]",
            r"\begin{tabular}{|>{\raggedright\arraybackslash}m{3.0cm}|>{\centering\arraybackslash}m{1.35cm}|>{\centering\arraybackslash}m{1.9cm}|>{\centering\arraybackslash}m{1.55cm}|>{\centering\arraybackslash}m{2.05cm}|>{\centering\arraybackslash}m{1.4cm}|>{\centering\arraybackslash}m{1.9cm}|}",
            r"\hline",
            r"\rule[-0.35ex]{0pt}{4.8ex}\shortstack[l]{Navn} & \rule[-0.35ex]{0pt}{4.8ex}\shortstack[c]{Tid} & \rule[-0.35ex]{0pt}{4.8ex}\shortstack[c]{Est. Tid} & \rule[-0.35ex]{0pt}{4.8ex}\shortstack[c]{Hdcp. Tid} & \rule[-0.35ex]{0pt}{4.8ex}\shortstack[c]{Est. Hdcp. Tid} & \rule[-0.35ex]{0pt}{4.8ex}\shortstack[c]{Fejl} & \rule[-0.35ex]{0pt}{4.8ex}\shortstack[c]{Point Fejl}\\[0.75ex]",
            r"\hline",
        ])
        for row in table.itertuples(index=False):
            tex_lines.append(" & ".join(latex_table_cell(value) for value in row) + r"\\")
        tex_lines.extend([r"\hline", r"\end{tabular}", r"\end{center}", r"\rowcolors{2}{}{}", r"}", ""])

    tex_path = output_dir / "missing_race_prediction_tables.tex"
    tex_path.write_text("\n".join(tex_lines), encoding="utf-8")
    return tex_path


def export_stor_bane_split_recommendation(
    *,
    manifest_path: Path,
    boat_plot_data_dir: Path,
    output_dir: Path,
    group_name: str = "Stor Bane",
    include_year: int = 2026,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    tex_path = output_dir / "stor_bane_split_recommendation.tex"

    manifest = pd.read_csv(manifest_path)
    required_manifest_cols = ["competitor_display", "percent_data_csv"]
    missing_manifest_cols = [column for column in required_manifest_cols if column not in manifest.columns]
    if missing_manifest_cols:
        raise ValueError(f"Missing required manifest columns for lane-split export: {', '.join(missing_manifest_cols)}")

    rows: list[dict[str, Any]] = []
    for row in manifest.itertuples(index=False):
        competitor_display = str(getattr(row, "competitor_display", "")).strip()
        percent_data_csv = str(getattr(row, "percent_data_csv", "")).strip()
        if not percent_data_csv:
            continue
        percent_path = boat_plot_data_dir / percent_data_csv
        if not percent_path.exists():
            raise FileNotFoundError(f"Missing boat-plot percent CSV for lane-split export: {percent_path}")

        series = pd.read_csv(percent_path)
        required_series_cols = ["group", "prior_pct", "post_pct", "obs_pct"]
        missing_series_cols = [column for column in required_series_cols if column not in series.columns]
        if missing_series_cols:
            raise ValueError(f"Missing required columns in {percent_data_csv}: {', '.join(missing_series_cols)}")

        in_group = series[series["group"].astype(str) == str(group_name)].copy()
        if in_group.empty:
            continue

        # Keep only rows for the requested year from race_display format "'YY-Rn".
        year_suffix = f"'{int(include_year) % 100:02d}-"
        if "race_display" in in_group.columns:
            in_group = in_group[in_group["race_display"].astype(str).str.startswith(year_suffix)].copy()
        if in_group.empty:
            continue

        # "Har sejlet" -> at least one observed datapoint in the requested year.
        observed_in_year = pd.to_numeric(in_group["obs_pct"], errors="coerce").notna().any()
        if not bool(observed_in_year):
            continue

        in_group = in_group.reset_index(drop=True)
        latest = in_group.iloc[-1]
        estimate_pct = pd.to_numeric(latest.get("post_pct"), errors="coerce")
        if not np.isfinite(estimate_pct):
            estimate_pct = pd.to_numeric(latest.get("prior_pct"), errors="coerce")
        if not np.isfinite(estimate_pct):
            continue

        observation_rate = float(pd.to_numeric(in_group["obs_pct"], errors="coerce").notna().mean())
        if not np.isfinite(observation_rate):
            observation_rate = 0.0

        rows.append(
            {
                "competitor_display": competitor_display,
                "estimate_pct": float(estimate_pct),
                "observation_rate": observation_rate,
            }
        )

    if len(rows) < 2:
        tex_path.write_text(
            "\n".join(
                [
                    r"\subsection*{Forslag til opdeling af Stor Bane i to løb}",
                    rf"Der er ikke tilstrækkelige data for både, der har sejlet i {int(include_year)}, til automatisk opdeling i to løb.",
                ]
            ),
            encoding="utf-8",
        )
        return tex_path

    candidates = pd.DataFrame(rows).sort_values("estimate_pct", ascending=True).reset_index(drop=True)
    n_boats = len(candidates)
    best_split: dict[str, Any] | None = None
    for split_index in range(1, n_boats):
        fast = candidates.iloc[:split_index]
        slow = candidates.iloc[split_index:]
        expected_fast = float(fast["observation_rate"].sum())
        expected_slow = float(slow["observation_rate"].sum())
        expected_diff = abs(expected_fast - expected_slow)
        n_fast = len(fast)
        n_slow = len(slow)
        tie_prefers_more_slow = 0 if n_slow > n_fast else 1
        key = (expected_diff, tie_prefers_more_slow, abs(n_fast - n_slow), split_index)
        candidate_split = {
            "split_index": split_index,
            "expected_fast": expected_fast,
            "expected_slow": expected_slow,
            "fast": fast,
            "slow": slow,
            "key": key,
        }
        if best_split is None or candidate_split["key"] < best_split["key"]:
            best_split = candidate_split

    if best_split is None:
        raise RuntimeError("Could not determine a valid lane split for Stor Bane.")

    fast = best_split["fast"]
    slow = best_split["slow"]
    expected_fast = float(best_split["expected_fast"])
    expected_slow = float(best_split["expected_slow"])

    def format_competitor_row(name: Any, estimate_pct: Any) -> str:
        name_text = latex_escape_text(str(name))
        estimate_value = float(estimate_pct)
        return f"{name_text} (\\({estimate_value:+.2f}\\%\\))"

    table_rows: list[str] = []
    max_rows = max(len(fast), len(slow))
    for i in range(max_rows):
        left = ""
        right = ""
        if i < len(fast):
            left_row = fast.iloc[i]
            left = format_competitor_row(left_row["competitor_display"], left_row["estimate_pct"])
        if i < len(slow):
            right_row = slow.iloc[i]
            right = format_competitor_row(right_row["competitor_display"], right_row["estimate_pct"])
        table_rows.append(f"{left} & {right} \\\\")

    tex_lines = [
        r"\subsection*{Forslag til opdeling af Stor Bane i to løb}",
        r"Hvis Stor Bane opdeles i to løb med de hurtigste både i løb 1 og de langsomste i løb 2, laves opdelingen automatisk fra de seneste færdighedsestimater.",
        rf"Kun både med observationer i {int(include_year)} er medtaget i denne tabel.",
        r"Her bruges seneste estimate pr. båd (posteriori ved tid $t$, ellers a priori). Splitpunktet vælges, så forventet antal startende pr. race bliver så ens som muligt baseret på historisk ikke-DNC-rate.",
        f"Der er {n_boats} både i Stor Bane, og bedste balance fås med {len(fast)} både i Stor bane 1 og {len(slow)} både i Stor bane 2 (forventet fremmøde ca. {expected_fast:.2f} mod {expected_slow:.2f} både pr. race):",
        r"",
        r"\begin{table}[H]",
        r"\centering",
        r"\begin{tabular}{ll}",
        r"\toprule",
        f"Stor bane 1 (hurtigste, {len(fast)} både) & Stor bane 2 (langsomste, {len(slow)} både) \\\\",
        r"\midrule",
        *table_rows,
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{table}",
    ]
    tex_path.write_text("\n".join(tex_lines), encoding="utf-8")
    return tex_path


def export_ml_fit_example(history: pd.DataFrame, *, output_dir: Path) -> list[Path]:
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
        race_summary_rows.append({"group": str(group_name), "race": str(race_label), "n": int(len(sub)), "p_mean": p_mean, "p_cv": p_cv})

    race_summary = pd.DataFrame(race_summary_rows)
    if race_summary.empty:
        raise ValueError("Could not summarize races for ML-fit example export.")

    eligible = race_summary[race_summary["n"] >= 10].copy()
    if eligible.empty:
        eligible = race_summary.copy()
    example_row = eligible.sort_values(["p_cv", "n", "group", "race"], ascending=[True, False, True, True]).iloc[0]
    example = observed[(observed["group"].astype(str) == str(example_row["group"])) & (observed["race"].astype(str) == str(example_row["race"]))].copy()
    if example.empty:
        raise ValueError("Selected ML-fit example race unexpectedly had no observed rows.")

    corrected_time_seconds = pd.to_numeric(example["beregnet_seconds"], errors="coerce") / np.exp(pd.to_numeric(example["x_prior"], errors="coerce"))
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


def _weighted_empirical_cdf(values: np.ndarray, weights: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    order = np.argsort(values)
    sorted_values = values[order]
    sorted_weights = weights[order]
    weight_sum = float(np.sum(sorted_weights))
    if not np.isfinite(weight_sum) or weight_sum <= 0.0:
        sorted_weights = np.ones_like(sorted_values, dtype=float)
        weight_sum = float(len(sorted_weights))
    cdf = np.cumsum(sorted_weights, dtype=float) / weight_sum
    return sorted_values, cdf


def export_race_cdf_appendix(history: pd.DataFrame, *, output_dir: Path, years: tuple[int, ...] = (2025, 2026)) -> tuple[Path, Path]:
    required_columns = ["group", "race", "race_local", "year", "observed", "beregnet_seconds", "x_prior", "p_prior", "b_t_hat", "r_t"]
    missing = [column for column in required_columns if column not in history.columns]
    if missing:
        raise ValueError(f"Missing required columns for race CDF appendix export: {', '.join(missing)}")

    work = history.copy()
    work["year_num"] = pd.to_numeric(work["year"], errors="coerce")
    work = work[work["year_num"].isin([int(year) for year in years])].copy()
    work = work[work["observed"] == True].copy()  # noqa: E712
    if work.empty:
        raise ValueError(f"No observed rows found for race CDF appendix export for years {years}.")

    work["race_local_norm"] = work["race_local"].astype(str).str.strip()
    work["race_num"] = work["race_local_norm"].map(race_num)
    work = work.dropna(subset=["race_num"]).copy()
    if work.empty:
        raise ValueError(f"No valid race labels found for race CDF appendix export for years {years}.")

    data_dir = output_dir / "race_cdf_data"
    data_dir.mkdir(parents=True, exist_ok=True)

    manifest_rows: list[dict[str, Any]] = []
    race_keys = (
        work.loc[:, ["year_num", "race_num", "race_local_norm", "group", "race"]]
        .drop_duplicates()
        .sort_values(["year_num", "race_num", "race_local_norm", "group", "race"])
        .itertuples(index=False, name=None)
    )
    for year_num, race_num_value, race_local_norm, group_name, race_label in race_keys:
        sub = work[
            (work["year_num"] == year_num)
            & (work["race_local_norm"] == race_local_norm)
            & (work["group"].astype(str) == str(group_name))
            & (work["race"].astype(str) == str(race_label))
        ].copy()
        if sub.empty:
            continue

        beregnet_seconds = pd.to_numeric(sub["beregnet_seconds"], errors="coerce")
        x_prior = pd.to_numeric(sub["x_prior"], errors="coerce")
        p_prior = pd.to_numeric(sub["p_prior"], errors="coerce")
        r_values = pd.to_numeric(sub["r_t"], errors="coerce")
        b_values = pd.to_numeric(sub["b_t_hat"], errors="coerce")
        r_finite = r_values[np.isfinite(r_values)]
        b_finite = b_values[np.isfinite(b_values)]
        if r_finite.empty or b_finite.empty:
            continue
        r_t = float(r_finite.iloc[0])
        mu_hat = float(b_finite.iloc[0])

        corrected_time_seconds = beregnet_seconds / np.exp(x_prior)
        valid_mask = (
            np.isfinite(corrected_time_seconds.to_numpy(dtype=float))
            & np.isfinite(p_prior.to_numpy(dtype=float))
            & (corrected_time_seconds.to_numpy(dtype=float) > 0.0)
            & (p_prior.to_numpy(dtype=float) >= 0.0)
        )
        if int(np.sum(valid_mask)) < 3:
            continue

        corrected = corrected_time_seconds.to_numpy(dtype=float)[valid_mask]
        p_vec = p_prior.to_numpy(dtype=float)[valid_mask]
        weight_den = np.maximum(EPS, p_vec + max(EPS, float(r_t)))
        weights = 1.0 / weight_den
        weights = np.where(np.isfinite(weights) & (weights > 0.0), weights, 0.0)
        if float(np.sum(weights)) <= 0.0:
            weights = np.ones_like(corrected, dtype=float)
        weights = weights / float(np.sum(weights))

        empirical_sorted, empirical_cdf = _weighted_empirical_cdf(corrected, weights)

        sigma_components = np.sqrt(np.maximum(EPS, p_vec + max(EPS, float(r_t))))
        sigma_ref = float(np.sqrt(np.average(np.square(sigma_components), weights=weights)))
        x_min = min(float(np.min(corrected)) * 0.92, float(np.exp(mu_hat - 4.0 * sigma_ref)))
        x_max = max(float(np.max(corrected)) * 1.08, float(np.exp(mu_hat + 4.0 * sigma_ref)))
        x_grid = np.linspace(max(EPS, x_min), x_max, 500, dtype=float)
        log_x = np.log(x_grid)
        model_cdf_parts: list[np.ndarray] = []
        for sigma_i in sigma_components:
            z = (log_x - mu_hat) / (float(sigma_i) * math.sqrt(2.0))
            cdf_i = 0.5 * (1.0 + np.array([math.erf(float(zv)) for zv in z], dtype=float))
            model_cdf_parts.append(cdf_i)
        model_cdf = np.average(np.vstack(model_cdf_parts), axis=0, weights=weights)
        model_cdf = np.clip(model_cdf, 0.0, 1.0)

        race_slug = slugify_filename(str(race_local_norm)) or f"race-{int(race_num_value)}"
        group_slug = slugify_filename(str(group_name)) or "group"
        stem = f"race_cdf_{int(year_num)}_{group_slug}_{race_slug}"
        empirical_name = f"{stem}_empirical.csv"
        model_name = f"{stem}_model.csv"
        empirical_path = data_dir / empirical_name
        model_path = data_dir / model_name

        pd.DataFrame({"time_seconds": empirical_sorted, "cdf": empirical_cdf}).to_csv(empirical_path, index=False)
        pd.DataFrame({"time_seconds": x_grid, "cdf": model_cdf}).to_csv(model_path, index=False)

        manifest_rows.append(
            {
                "year": int(year_num),
                "race_num": int(race_num_value),
                "race_local": str(race_local_norm),
                "race": str(race_label),
                "group": str(group_name),
                "group_display": display_name(str(group_name)),
                "n_observed": int(len(corrected)),
                "empirical_csv": empirical_name,
                "model_csv": model_name,
            }
        )

    if not manifest_rows:
        raise ValueError(f"No race CDF files could be exported for years {years}.")

    manifest = pd.DataFrame(manifest_rows).sort_values(["year", "race_num", "group", "race"]).reset_index(drop=True)
    manifest_path = output_dir / "race_cdf_manifest.csv"
    manifest.to_csv(manifest_path, index=False)

    tex_lines: list[str] = [
        r"\clearpage",
        r"\section{Racevise CDF-plots (2025--2026)}",
        r"Dette afsnit viser for hvert r\ae s en empirisk CDF af korrigerede tider samt model-CDF'en.",
        r"Empirisk CDF er v\ae gtet med inverse variansv\ae gte, \(w_i \propto 1/(p_{i,t}^- + \hat{\sigma}_{y,t}^2)\), normaliseret til sum 1.",
        r"",
    ]

    for row in manifest.itertuples(index=False):
        title = f"{int(row.year)} {str(row.race_local)} ({str(row.group_display)})"
        title_escaped = latex_escape_text(title)
        empirical_rel = f"../analysis/race_cdf_data/{row.empirical_csv}"
        model_rel = f"../analysis/race_cdf_data/{row.model_csv}"
        tex_lines.extend(
            [
                r"\clearpage",
                rf"\RaceCdfPlotFromCsv{{{title_escaped}}}{{{empirical_rel}}}{{{model_rel}}}{{{int(row.n_observed)}}}",
                r"",
            ]
        )

    tex_path = output_dir / "race_cdf_appendix.tex"
    tex_path.write_text("\n".join(tex_lines), encoding="utf-8")
    return manifest_path, tex_path
