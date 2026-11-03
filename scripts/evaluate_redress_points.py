"""Offline masked-race validation of redress points, conditional on finishing.

Fit time-model parameters on 2025 only; freeze them for 2026. Each target's
current result is excluded from the day's time fit and ranking likelihood.
The existing ranking prior (chosen on 2025) is frozen. No network or publishing.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
from scipy.optimize import differential_evolution, minimize
from scipy.special import expit, logsumexp, ndtr, ndtri
from scipy.stats import qmc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))
from m2s_combiner.redress import model as time_model
from ranking_odds import fit as fit_ranking, ranking_orders

METHODS = ['time_point', 'time_expected', 'ranking_expected']
LABELS = {
    'time_point': 'Single predicted time',
    'time_expected': 'Time distribution → expected points',
    'ranking_expected': 'Ranking model → expected points',
    'time_point_reset': 'Single predicted time (season reset)',
    'time_expected_reset': 'Time distribution (season reset)',
}
RAW_FIELDS = ['year', 'group', 'race_date', 'race_local', 'competitor',
              'observed', 'status', 'beregnet_seconds']
MINIMUM_HISTORY = 3


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_results(path):
    frame = pd.read_csv(path, usecols=RAW_FIELDS, keep_default_na=False)
    frame = frame[frame.year.isin([2025, 2026])].copy()
    frame['observed'] = frame.observed.astype(str).str.lower().eq('true')
    frame['beregnet_seconds'] = pd.to_numeric(frame.beregnet_seconds, errors='coerce')
    if frame.duplicated(['year', 'group', 'race_date', 'competitor']).any():
        raise ValueError('Multiple rows per boat/course/day: review race grouping')
    values = frame.loc[frame.observed, 'beregnet_seconds'].to_numpy()
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError('Invalid observed corrected times')
    frame.loc[~frame.observed, 'beregnet_seconds'] = np.nan
    return frame.sort_values(['group', 'race_date', 'competitor']).reset_index(drop=True)


def make_group(frame):
    frame = frame.copy()
    frame['race'] = frame.race_date.astype(str)
    frame['race_date'] = pd.to_datetime(frame.race_date)
    frame['race_status_code'] = np.where(frame.observed, '', 'DNC')
    return {'group': str(frame.group.iloc[0]), 'combined': frame,
            'selected_races': sorted(frame.race.unique()),
            'race_dates': dict(zip(frame.race, frame.race_date))}


def fit_time_parameters(frame):
    """Same filter likelihood, with blind initial states and no terminal replay.

    This deliberately does not read the production parameter/state cache, which
    contains data outside the training period. Bounds match the production fit.
    """
    group = make_group(frame)
    label = group['group']
    bounds = np.log([(1e-12, 1e-2), (1e-9, 1e-2), (1e-3, 1e6)])
    evaluations = 0

    def objective(theta):
        nonlocal evaluations
        evaluations += 1
        q, q_gamma, k = np.exp(theta)
        value, _ = time_model.q_objective_mle(
            [group], q, q_gamma=q_gamma, initial_x0=0,
            initial_p0=max(time_model.EPS, q*k))
        return value if np.isfinite(value) else 1e100

    generation = 0

    def progress(_x, convergence):
        nonlocal generation
        generation += 1
        if generation % 10 == 0:
            print(f'Tuning {label}: generation {generation}, convergence {convergence:.3g}', flush=True)

    result = differential_evolution(objective, bounds, seed=7391, popsize=8,
                                    maxiter=80, tol=1e-5, polish=False, callback=progress)
    local = minimize(objective, result.x, method='Powell', bounds=bounds,
                     options={'maxiter':100, 'xtol':1e-6, 'ftol':1e-8})
    best = local if local.fun < result.fun else result
    q, q_gamma, k = np.exp(best.x)
    return {'q':float(q), 'q_gamma':float(q_gamma), 'k':float(k),
            'p0':float(max(time_model.EPS, q*k)), 'initial_x0':0.,
            'training_nll':float(best.fun), 'evaluations':evaluations,
            'global_converged':bool(result.success), 'local_converged':bool(local.success),
            'global_message':str(result.message), 'local_message':str(local.message)}


def collect_time_priors(frame, parameters):
    result = time_model.run_group_filter(make_group(frame), parameters['q'],
        q_gamma=parameters['q_gamma'], initial_x0=parameters['initial_x0'],
        initial_p0=parameters['p0'], collect_history=False, collect_priors=True)
    return {prior.race:prior for prior in result.priors}


def time_prediction(prior, target, opponents, opponent_times):
    """The target's actual time cannot enter this function."""
    lookup = {name:i for i, name in enumerate(prior.competitors)}
    ids = np.array([lookup[name] for name in opponents], dtype=int)
    i = lookup[target]
    mu_day, variance_day, _ = time_model.fit_day_parameters_full(
        np.log(opponent_times)-prior.mean[ids], prior.covariance[np.ix_(ids, ids)])
    mu = mu_day + prior.mean[i] + np.sqrt(variance_day)*prior.gamma_mean[i]
    variance = (prior.covariance[i, i] + variance_day*prior.gamma_covariance[i, i]
                + variance_day)
    if not np.isfinite(mu) or not np.isfinite(variance) or variance <= 0:
        raise ValueError('Invalid masked time prediction')
    thresholds = np.sort(np.log(opponent_times))
    # Low-point position = 1 + the number of other finishers with a lower time.
    # Repeated thresholds correctly put zero mass in gaps caused by tied rivals.
    cdf = ndtr((thresholds-mu)/np.sqrt(variance))
    pmf = np.diff(np.r_[0., cdf, 1.])
    point = float(1 + np.count_nonzero(thresholds < mu))
    return point, pmf


def pl_log_probability(samples, order):
    z = samples[:, order]
    if z.shape[1] == 0:
        return np.zeros(len(samples))
    remaining = np.logaddexp.accumulate(z[:, ::-1], axis=1)[:, ::-1]
    return np.sum(z-remaining, axis=1)


def insertion_probabilities(samples, target, order):
    """P(target position | strengths, observed order of the other finishers).

    Dividing each full-order PL probability by the probability of the observed
    sub-order gives a sequence of insertion hazards. This retains the observed
    race order rather than drawing a new, unrelated race for the competitors.
    """
    if len(order) == 0:
        return np.ones((len(samples), 1))
    z = samples[:, order]
    remaining = np.logaddexp.accumulate(z[:, ::-1], axis=1)[:, ::-1]
    hazards = expit(samples[:, target, None]-remaining)
    survival = np.cumprod(1-hazards, axis=1)
    before = np.column_stack([np.ones(len(samples)), survival[:, :-1]])
    return np.column_stack([before*hazards, survival[:, -1]])


@lru_cache(maxsize=16)
def normal_samples(n, power):
    u = qmc.Sobol(d=n, scramble=True, seed=92417).random_base2(power)
    return ndtri(np.clip(u, 1e-12, 1-1e-12))


def conditional_ranking_pmf(mean, covariance, target, other_orders, power=12):
    """mean/covariance already condition on the rivals' partial ranking."""
    samples = mean + normal_samples(len(mean), power) @ np.linalg.cholesky(covariance).T
    log_weights = np.column_stack([pl_log_probability(samples, order) for order in other_orders])
    weights = np.exp(log_weights-logsumexp(log_weights, axis=1)[:, None])
    pmf = np.zeros((len(samples), len(other_orders[0])+1))
    for j, order in enumerate(other_orders):
        pmf += weights[:, j, None]*insertion_probabilities(samples, target, order)
    result = pmf.mean(axis=0)
    return result/result.sum()


def predict_masked_ranking(past, n, prior_sd, start, target, other_ids, other_times, power):
    orders = ranking_orders(other_ids, other_times)
    conditional_mean, conditional_covariance = fit_ranking(past+[orders], n, prior_sd, start)
    return conditional_ranking_pmf(conditional_mean, conditional_covariance, target, orders, power)


def prediction_row(metadata, method, pmf, point=None):
    pmf = np.asarray(pmf)
    if (pmf < -1e-12).any() or not np.isclose(pmf.sum(), 1):
        raise ValueError('Invalid position probabilities')
    positions = np.arange(1, len(pmf)+1)
    expected = float(pmf @ positions)
    prediction = expected if point is None else float(point)
    error = prediction-metadata['actual_points']
    cdf = np.cumsum(pmf)
    return dict(metadata, method=method, predicted_points=prediction,
                error=error, abs_error=abs(error), squared_error=error**2,
                interval_low=int(np.searchsorted(cdf, .1)+1),
                interval_high=int(np.searchsorted(cdf, .9)+1),
                position_probabilities=json.dumps(pmf.tolist(), separators=(',', ':')))


def evaluate(frame, parameters, ranking_sd, power, output, *, early=False):
    rows = []
    for group, all_seasons in frame.groupby('group', sort=True):
        continuous_priors = collect_time_priors(all_seasons, parameters[group])
        for year, season in all_seasons.groupby('year', sort=True):
            reset_priors = collect_time_priors(season, parameters[group])
            names = sorted(season.competitor.unique())
            lookup = {name:i for i, name in enumerate(names)}
            n = len(names)
            past, mean, counts = [], np.zeros(n), np.zeros(n, dtype=int)
            for date, day in season.groupby('race_date', sort=True):
                finishers = day[day.observed]
                ids = np.array([lookup[name] for name in finishers.competitor], dtype=int)
                times = finishers.beregnet_seconds.to_numpy(dtype=float)
                for local, identity in enumerate(ids):
                    established = counts[identity] >= MINIMUM_HISTORY
                    if established == early or len(ids) < 3:
                        continue
                    other = np.arange(len(ids)) != local
                    other_ids, other_times = ids[other], times[other]
                    target = names[identity]
                    opponents = [names[i] for i in other_ids]
                    meta = dict(year=int(year), phase='evaluation' if year == 2026 else 'development',
                        group=group, date=date, race=day.race_local.iloc[0], competitor=target,
                        prior_finishes=int(counts[identity]), finishers=len(ids),
                        actual_points=int(1+np.count_nonzero(other_times < times[local])),
                        tied_target=bool(np.any(other_times == times[local])))
                    for suffix, prior in [('', continuous_priors[date]), ('_reset', reset_priors[date])]:
                        point, pmf = time_prediction(prior, target, opponents, other_times)
                        rows.append(prediction_row(meta, 'time_point'+suffix, pmf, point))
                        rows.append(prediction_row(meta, 'time_expected'+suffix, pmf))
                    pmf = predict_masked_ranking(past, n, ranking_sd, mean, identity,
                                                other_ids, other_times, power)
                    rows.append(prediction_row(meta, 'ranking_expected', pmf))
                # Reveal all actual results only after every masked case for this race.
                if len(ids) >= 2:
                    past.append(ranking_orders(ids, times))
                    mean, _ = fit_ranking(past, n, ranking_sd, mean)
                counts[ids] += 1
                print(f'Evaluated {"early " if early else ""}{group} {year} {day.race_local.iloc[0]}', flush=True)
            pd.DataFrame(rows).to_csv(output/('early_predictions.csv' if early else 'predictions.csv'), index=False)
    return pd.DataFrame(rows)


def summarize(predictions, keys):
    race = predictions.groupby(keys+['date'], dropna=False).agg(
        mae=('abs_error', 'mean'), mse=('squared_error', 'mean'), bias=('error', 'mean'),
        boats=('competitor', 'size')).reset_index()
    summary = race.groupby(keys, dropna=False).agg(
        races=('date', 'size'), cases=('boats', 'sum'), mae=('mae', 'mean'),
        mse=('mse', 'mean'), bias=('bias', 'mean')).reset_index()
    summary['rmse'] = np.sqrt(summary.mse)
    return summary


def audit_predictions(frame, predictions, early):
    keys = ['year', 'group', 'date', 'competitor']
    combined = pd.concat([predictions, early], ignore_index=True)
    if combined.duplicated(keys+['method']).any():
        raise ValueError('Repeated evaluation case')
    if not combined.groupby(keys).method.nunique().eq(len(LABELS)).all():
        raise ValueError('Methods did not receive identical evaluation cases')
    if (predictions.prior_finishes < MINIMUM_HISTORY).any() or (early.prior_finishes >= MINIMUM_HISTORY).any():
        raise ValueError('Invalid established / sparse-history split')
    actual = frame[frame.observed].rename(columns={'race_date':'date'}).copy()
    actual['actual_points'] = actual.groupby(['year', 'group', 'date']).beregnet_seconds.rank(method='min')
    sizes = actual.groupby(['year', 'group', 'date']).competitor.transform('size')
    actual = actual[sizes >= 3].set_index(keys).actual_points.sort_index()
    scored = combined[combined.method.eq('ranking_expected')].set_index(keys).actual_points.sort_index()
    pd.testing.assert_series_equal(scored.astype(float), actual.astype(float))
    if not combined.predicted_points.between(1-1e-10, combined.finishers+1e-10).all():
        raise ValueError('Predicted points outside the finishing positions')
    return {'cases':len(scored), 'evaluation_cases':int((scored.index.get_level_values('year') == 2026).sum()),
            'same_cases_all_methods':True, 'all_eligible_finishers_accounted_for':True,
            'actual_points_match_combined_scoring':True}


def paired_bootstrap(frame, resamples=20000):
    """Resample evenings, keeping both courses and all boats together."""
    race = frame.groupby(['date', 'group', 'method']).agg(
        mae=('abs_error', 'mean'), mse=('squared_error', 'mean')).reset_index()
    rng = np.random.default_rng(8471)
    rows = []
    for method, baseline in [('ranking_expected', 'time_point'),
                             ('ranking_expected', 'time_expected'),
                             ('time_expected', 'time_point'),
                             ('ranking_expected', 'time_expected_reset')]:
        for metric in ['mae', 'mse']:
            wide = race.pivot(index=['date', 'group'], columns='method', values=metric)
            pairs = wide[[method, baseline]].dropna()
            sums = pairs.groupby('date').sum()
            sizes = pairs.groupby('date').size().to_numpy()
            indices = rng.integers(0, len(sums), size=(resamples, len(sums)))
            a = sums[method].to_numpy()[indices].sum(axis=1)/sizes[indices].sum(axis=1)
            b = sums[baseline].to_numpy()[indices].sum(axis=1)/sizes[indices].sum(axis=1)
            point_a, point_b = pairs[method].mean(), pairs[baseline].mean()
            if metric == 'mse':
                a, b, point_a, point_b = np.sqrt(a), np.sqrt(b), np.sqrt(point_a), np.sqrt(point_b)
            delta = a-b
            rows.append(dict(method=method, baseline=baseline,
                metric='rmse' if metric == 'mse' else metric,
                difference=float(point_a-point_b), low=float(np.quantile(delta, .025)),
                high=float(np.quantile(delta, .975)), evenings=len(sums)))
    return pd.DataFrame(rows)


def table(frame, methods=METHODS):
    lines = ['| Method | MAE (points) | RMSE (points) | Signed error |',
             '|---|---:|---:|---:|']
    for method in methods:
        row = frame[frame.method.eq(method)].iloc[0]
        lines.append(f'| {LABELS[method]} | {row.mae:.3f} | {row.rmse:.3f} | {row.bias:+.3f} |')
    return '\n'.join(lines)


def write_report(predictions, output, manifest, early=None):
    held = predictions[predictions.year.eq(2026)]
    per_course = summarize(predictions, ['year', 'group', 'method'])
    per_course.to_csv(output/'course_summary.csv', index=False)
    # Equal weight per course-race, then equal weight across those units.
    races = predictions.groupby(['year', 'date', 'group', 'method']).agg(
        mae=('abs_error', 'mean'), mse=('squared_error', 'mean'), bias=('error', 'mean'),
        cases=('competitor', 'size')).reset_index()
    overall = races.groupby(['year', 'method']).agg(races=('date', 'size'), cases=('cases', 'sum'),
        mae=('mae', 'mean'), mse=('mse', 'mean'), bias=('bias', 'mean')).reset_index()
    overall['rmse'] = np.sqrt(overall.mse)
    overall.to_csv(output/'summary.csv', index=False)
    intervals = paired_bootstrap(held)
    intervals.to_csv(output/'paired_comparisons.csv', index=False)
    by_boat = held.groupby(['group', 'competitor', 'method']).agg(
        cases=('error', 'size'), mae=('abs_error', 'mean'), mse=('squared_error', 'mean'),
        bias=('error', 'mean')).reset_index()
    by_boat['rmse'] = np.sqrt(by_boat.mse)
    by_boat.to_csv(output/'boat_summary.csv', index=False)
    examples = held[held.method.isin(METHODS)].pivot(
        index=['group', 'date', 'race', 'competitor', 'actual_points'], columns='method',
        values='predicted_points').reset_index()
    examples.to_csv(output/'comparisons.csv', index=False)
    cases = held[held.method.eq('ranking_expected')]
    text = ['# Redress points, conditional on finishing', '',
        f'Main comparison (at least three earlier finishes): **{len(cases)} hidden-boat cases** in 2026, '
        f'**{len(cases.groupby(["group", "date"]))} course-races**, '
        f'**{cases.date.nunique()} evenings**. Lower MAE/RMSE is better. '
        'Negative signed error awards too few points (generous redress); positive error awards too many.', '',
        '## 2026 evaluation with established history', '', table(overall[overall.year.eq(2026)]), '',
        'Each course-race has equal weight; boats within it share that weight. '
        'RMSE is the square root of mean squared point error. Squared error directly targets '
        'the conditional mean; MAE is also reported but instead favours the conditional median.', '']
    for group in sorted(cases.group.unique()):
        text += [f'### {group}', '', table(per_course[per_course.year.eq(2026)&per_course.group.eq(group)]), '']
    text += ['## Paired uncertainty', '',
        '20,000 date-block bootstrap resamples; both courses and all hidden boats on the same '
        'evening stay together. Differences are candidate minus baseline; negative is better. '
        'These intervals do not capture all dependence across evenings from shared boats and fitted history.', '',
        '| Candidate − baseline | Metric | Difference | Approx. 95% interval |',
        '|---|---|---:|---:|']
    for row in intervals.itertuples():
        text.append(f'| {LABELS[row.method]} − {LABELS[row.baseline]} | {row.metric.upper()} | '
                    f'{row.difference:+.3f} | [{row.low:+.3f}, {row.high:+.3f}] |')
    if early is not None:
        early_races = early.groupby(['year', 'date', 'group', 'method']).agg(
            mae=('abs_error', 'mean'), mse=('squared_error', 'mean'), bias=('error', 'mean'),
            cases=('competitor', 'size')).reset_index()
        early_summary = early_races.groupby(['year', 'method']).agg(
            races=('date', 'size'), cases=('cases', 'sum'), mae=('mae', 'mean'),
            mse=('mse', 'mean'), bias=('bias', 'mean')).reset_index()
        early_summary['rmse'] = np.sqrt(early_summary.mse)
        early_summary.to_csv(output/'early_summary.csv', index=False)
        early_held = early[early.year.eq(2026)&early.method.eq('ranking_expected')]
        all_predictions = pd.concat([predictions, early], ignore_index=True)
        all_races = all_predictions.groupby(['year', 'date', 'group', 'method']).agg(
            mae=('abs_error', 'mean'), mse=('squared_error', 'mean'), bias=('error', 'mean'),
            cases=('competitor', 'size')).reset_index()
        all_summary = all_races.groupby(['year', 'method']).agg(
            races=('date', 'size'), cases=('cases', 'sum'), mae=('mae', 'mean'),
            mse=('mse', 'mean'), bias=('bias', 'mean')).reset_index()
        all_summary['rmse'] = np.sqrt(all_summary.mse)
        all_summary.to_csv(output/'all_summary.csv', index=False)
        all_intervals = paired_bootstrap(all_predictions[all_predictions.year.eq(2026)])
        all_intervals.to_csv(output/'all_paired_comparisons.csv', index=False)
        text[4:4] = ['## All 2026 finishing cases', '',
            f'Combining established and sparse-history cases gives **{len(cases)+len(early_held)} '
            'simulated duties**. Each boat is hidden separately. All methods receive the same cases.', '',
            table(all_summary[all_summary.year.eq(2026)]), '',
            'The aggregate time-distribution estimate has the smallest squared error; the '
            'single-time estimate has the smallest absolute error. This is not evidence '
            'that either dominates for every boat or stage of the season. Paired uncertainty '
            'for this combined sample is in `all_paired_comparisons.csv`.', '']
        text += ['', '## Early-season / sparse-history cases', '',
            f'An additional **{len(early_held)} 2026 cases** have fewer than '
            f'{MINIMUM_HISTORY} previous finishes that season. These are evaluated separately '
            'because the dashboard normally hides such estimates. The ranking model has '
            'no previous-season boat strengths; the primary time model retains 2025 history. '
            'These cases matter for duty assignments near the season start.', '',
            table(early_summary[early_summary.year.eq(2026)]), '']
    text += ['', '## History sensitivity', '',
        'The existing time model retains 2025 boat history in 2026; the dashboard ranking model '
        'starts each season fresh. The following additional time-model results reset boat states '
        'at the season boundary, so the state history matches the ranking model.', '',
        table(overall[overall.year.eq(2026)], ['time_point_reset', 'time_expected_reset', 'ranking_expected']), '',
        '## Boat-level calibration check', '',
        'Low aggregate bias can conceal opposite errors for different boats. This diagnostic '
        'shows the five lowest observed average scores among boats with at least five established-history '
        'test cases. Selection is retrospective; it is not used to tune or correct predictions. '
        'Larger predicted points than observed points mean less favourable compensation.', '',
        '| Boat | Cases | Actual average | Single time | Time distribution | Ranking |',
        '|---|---:|---:|---:|---:|---:|']
    strongest = cases.groupby(['group', 'competitor']).agg(
        n=('actual_points', 'size'), actual=('actual_points', 'mean')).reset_index()
    predicted = held.groupby(['group', 'competitor', 'method']).predicted_points.mean().unstack('method')
    for row in strongest[strongest.n.ge(5)].sort_values('actual').head(5).itertuples():
        p = predicted.loc[(row.group, row.competitor)]
        text.append(f'| {row.competitor} | {row.n} | {row.actual:.3f} | '
                    f'{p.time_point:.3f} | {p.time_expected:.3f} | {p.ranking_expected:.3f} |')
    text += ['', 'Full boat-level errors are in `boat_summary.csv`. An aggregate improvement '
        'does not establish calibrated or neutral compensation for every boat.', '',
        '## Protocol', '',
        '- Only raw 2025–2026 corrected times/statuses enter. Saved fitted redress states, '
        '2023/2024 results and the production parameter cache are never read.',
        '- The time model uses the existing filter and likelihood. Global q, q_gamma and '
        'initial variance are fitted on 2025 only, with zero initial boat means, then frozen. '
        'There is no replay from terminal states. Course histories stay separate.',
        f'- Ranking prior SD is frozen at {manifest["ranking_prior_sd"]:g}, previously chosen on 2025. '
        'It is not retuned on these 2026 redress outcomes. 2026 was already explored in the odds '
        'project, so this is a chronological retrospective test, not a pristine external validation.',
        f'- The established-history comparison requires {MINIMUM_HISTORY} earlier finishes '
        'in the current season; the separate sparse-history comparison covers the remaining boats. '
        'At least two other finishers must remain. All methods use exactly the same cases.',
        '- Hide one actual finisher at a time. Its time and position are excluded from all '
        'current-race fitting; reveal it only for scoring and subsequent-race updates.',
        '- The time model re-estimates both the day mean and day variance with the target removed. '
        'Its single-time method ranks exp(predicted log-time); its distribution method integrates '
        'the existing predictive lognormal across the rivals’ actual corrected times.',
        '- Ranking conditions on the other finishers’ observed order, including an update to '
        'the joint strength posterior. Conditional insertion probabilities are then averaged '
        f'over {2**manifest["sobol_power"]:,} fixed Sobol draws from its Laplace approximation. '
        'It does not sum independent simulated head-to-head outcomes.',
        '- Both methods assume the missing boat finishes. Other non-finishers score behind it '
        'and do not contribute thresholds. No DNF penalty or attendance probability is fitted.',
        '- Points follow the current combined-course low-point implementation: '
        '1 + the number of strictly faster finishers (minimum rank for recorded ties). '
        'Ranking treats observed ties as unresolved orders; it has no generative tie model. '
        f'{int(cases.tied_target.sum())} held-out 2026 targets have a tied recorded time.',
        '- Only one boat is hidden at a time. This assumes its absence would not materially '
        'change the others’ performances. Multiple simultaneous duty boats need joint treatment.',
        '- This evaluates per-race points before season discards. Fractional expected points '
        'are estimates; the existing publishing pipeline still inserts a time and uses integer points. '
        'No live scoring rule, duty assignment or published result is changed.', '',
        '## Limitations', '',
        'The time-distribution version preserves the existing variance formula: it plugs in '
        'the estimated day parameters, rather than integrating their estimation uncertainty, '
        'and uses the prior boat state as the current estimator does. The ranking version '
        'assumes constant within-season strengths and discards time margins. Both posterior '
        'approximations can be wrong. Conclusions are conditional on the eligible finishers; '
        'they do not establish accuracy for actual absentees or non-finishers. Sparse-history '
        'cases are shown separately when available.', '',
        'Per-boat signed errors are in `boat_summary.csv`; they are diagnostic, not separately '
        'validated boat-specific corrections. The sample contains few independent evenings.', '',
        '## 2025 development data', '', table(overall[overall.year.eq(2025)]), '',
        'These scores use parameters selected on 2025 and are not held-out evidence.', '',
        '## Reproduce', '', 'From the Onsdagsbanen directory:', '', '```bash',
        'OPENBLAS_NUM_THREADS=1 .venv/bin/python scripts/evaluate_redress_points.py',
        '.venv/bin/python -m unittest discover -s tests -p "test_redress_points.py"', '```', '',
        'The script uses local saved results, makes no network requests, and writes only this '
        'analysis directory. Training settings are reused only when the 2025 data and time-model '
        'code hashes match. Use `--refit` to repeat optimization or `--sobol-power 14` for finer '
        'integration. Input/configuration/code hashes and optimizer diagnostics are in `manifest.json`.']
    (output/'README.md').write_text('\n'.join(text)+'\n')
    print('\n'+table(overall[overall.year.eq(2026)]), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT/'data/odds_results_2025_2026.csv')
    parser.add_argument('--output-dir', type=Path, default=ROOT/'analysis/results/redress_points_2025_2026')
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--sobol-power', type=int, default=12)
    parser.add_argument('--refit', action='store_true')
    parser.add_argument('--fit-only', action='store_true')
    args = parser.parse_args()
    frame = load_results(args.source)
    if set(frame.year) != {2025, 2026}:
        raise ValueError('Both 2025 training and 2026 evaluation data are required')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    training = frame[frame.year.eq(2025)]
    fingerprint = dict(schema=1, data=hashlib.sha256(training.to_csv(index=False).encode()).hexdigest(),
                       model=sha(time_model.__file__))
    cache_path = args.output_dir/'training_parameters.json'
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    parameters = cache.get('parameters', {}) if cache.get('fingerprint') == fingerprint and not args.refit else {}
    pending = [(group, data) for group, data in training.groupby('group') if group not in parameters]
    if pending:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            jobs = {pool.submit(fit_time_parameters, data):group for group, data in pending}
            for future in as_completed(jobs):
                group = jobs[future]
                parameters[group] = future.result()
                cache_path.write_text(json.dumps(dict(fingerprint=fingerprint, parameters=parameters), indent=2)+'\n')
                print(f'Fitted {group}: {parameters[group]}', flush=True)
    if args.fit_only:
        return
    config_path = ROOT/'analysis/models/ranking_odds_config.json'
    config = json.loads(config_path.read_text())
    if config['tuning_years'] != [2025]:
        raise ValueError('Ranking prior must be selected using 2025 only')
    manifest = dict(source=str(args.source.resolve()), source_sha256=sha(args.source),
        time_model_sha256=sha(time_model.__file__), evaluator_sha256=sha(__file__),
        ranking_model_sha256=sha(ROOT/'scripts/ranking_odds.py'), ranking_config_sha256=sha(config_path),
        training_years=[2025], evaluation_years=[2026], assumed_finish=True,
        ranking_prior_sd=config['prior_sd'], sobol_power=args.sobol_power,
        minimum_prior_finishes=MINIMUM_HISTORY, time_parameters=parameters)
    (args.output_dir/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    predictions = evaluate(frame, parameters, config['prior_sd'], args.sobol_power, args.output_dir)
    early = evaluate(frame, parameters, config['prior_sd'], args.sobol_power, args.output_dir, early=True)
    audit = audit_predictions(frame, predictions, early)
    (args.output_dir/'audit.json').write_text(json.dumps(audit, indent=2)+'\n')
    write_report(predictions, args.output_dir, manifest, early)


if __name__ == '__main__':
    main()
