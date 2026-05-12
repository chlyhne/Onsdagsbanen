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
from .common import format_seconds_signed
from .common import latex_escape_text
from .common import latex_table_cell
from .common import predict_sailed_seconds_from_corrected
from .common import race_num
from .common import slugify_filename
from .constants import EPS
from .constants import Z50


def export_boat_plot_data(frame: pd.DataFrame, *, allowed_competitors: set[str] | None, output_dir: Path) -> Path:
    required_columns = [
        "competitor", "group", "series", "race", "race_local", "race_date", "year", "observed",
        "x_prior", "x_post", "x_obs", "x_proc_q25", "x_proc_q75", "x_total_q25", "x_total_q75", "x_q25", "x_q75",
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
        events = c.loc[:, event_cols].drop_duplicates().sort_values(["race_date_dt", "year", "race_num", "race_local_display", "race", "group"], na_position="last").reset_index(drop=True)
        if events.empty:
            raise ValueError(f"No race events could be formed for competitor '{competitor}'.")
        events["x_pos"] = np.arange(1, len(events) + 1, dtype=int)
        c = c.merge(events.loc[:, ["year", "race", "race_date", "group", "x_pos", "race_local_display"]], on=["year", "race", "race_date", "group"], how="left")
        c = c.dropna(subset=["x_pos"]).copy()
        c["x_pos"] = c["x_pos"].astype(int)
        c = c.sort_values(["x_pos"]).reset_index(drop=True)

        base = events.loc[:, ["x_pos", "year", "race_local_display", "group"]].rename(columns={"race_local_display": "race_local"}).copy()
        base["race_display"] = base.apply(lambda row: f"'{int(row['year']) % 100:02d}-{str(row['race_local'])}", axis=1)
        base = base.drop(columns=["year"])
        series_values = c.loc[:, ["x_pos", "series"]].drop_duplicates(subset=["x_pos"], keep="first").copy()
        values = c.loc[:, ["x_pos", "observed", "x_prior", "x_post", "x_obs", "x_proc_q25", "x_proc_q75", "x_total_q25", "x_total_q75", "x_q25", "x_q75"]].drop_duplicates(subset=["x_pos"], keep="first")
        values["x_post"] = np.where(values["observed"].astype(bool), values["x_post"], np.nan)
        values = values.drop(columns=["observed"])
        plot_data = base.merge(series_values, on="x_pos", how="left").merge(values, on="x_pos", how="left").sort_values("x_pos").reset_index(drop=True)

        local_y_values = (
            plot_data["x_prior"].dropna().astype(float).tolist()
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

        slug = slugify_filename(competitor)
        data_name = f"boat_plot_{idx:02d}_{slug}.csv"
        data_path = output_dir / data_name
        plot_data.to_csv(data_path, index=False)

        percent_data = plot_data.loc[:, ["x_pos", "race_local", "race_display", "group", "series"]].copy()
        for source_col, target_col in [
            ("x_prior", "prior_pct"), ("x_obs", "obs_pct"), ("x_proc_q25", "proc_q25_pct"), ("x_proc_q75", "proc_q75_pct"),
            ("x_total_q25", "total_q25_pct"), ("x_total_q75", "total_q75_pct"), ("x_q25", "q25_pct"), ("x_q75", "q75_pct"),
        ]:
            values_pct = 100.0 * (np.exp(pd.to_numeric(plot_data[source_col], errors="coerce")) - 1.0)
            values_pct = pd.to_numeric(values_pct, errors="coerce")
            values_pct = values_pct.where(np.isfinite(values_pct), np.nan)
            percent_data[target_col] = values_pct
        percent_path = output_dir / f"boat_plot_{idx:02d}_{slug}_percent.csv"
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
    work.loc[observed_mask, "estimated_lane_rank"] = (
        work.loc[observed_mask]
        .groupby(lane_rank_group_cols)["pred_cf_beregnet_seconds"]
        .rank(method="min", ascending=True)
    )
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
            rf"\subsubsection*{{{latex_escape_text(table_title)}}}",
            r"{\scriptsize",
            r"\setlength{\tabcolsep}{3pt}",
            r"\setlength{\arrayrulewidth}{0.8pt}",
            r"\renewcommand{\arraystretch}{0.95}",
            r"\rowcolors{2}{white}{gray!12}",
            r"\begin{center}",
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
