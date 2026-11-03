"""Censored marginal likelihood using stored redress boat priors.

This is a separate sensitivity analysis, not a refit of the full correlated filter.
DNF weights are likelihood sensitivity weights, not estimated cause probabilities.
"""
from pathlib import Path
import argparse
import hashlib
import json
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import log_ndtr, ndtr

ROOT = Path(__file__).resolve().parents[1]


def parameters(frame, mu, variance):
    loading = np.sqrt(variance)
    means = mu + frame.x_prior.to_numpy() + loading * frame.gamma_prior.to_numpy()
    variances = (frame.p_prior.to_numpy() + variance *
                 (1 + frame.p_gamma_prior.to_numpy()))
    return means, np.sqrt(variances)


def fit_day(frame, limit_seconds, dnf_weight, allow_late_finishers=False):
    observed = frame.observed.to_numpy(dtype=bool)
    shift = ((1000 - frame.hdcp) * frame.length_nm).to_numpy()
    if not allow_late_finishers and np.any(frame.sailed_seconds.to_numpy()[observed] > limit_seconds + 1):
        raise ValueError('Observed finisher after assumed deadline')
    thresholds = limit_seconds + shift
    if np.any(thresholds <= 0):
        raise ValueError('Nonpositive corrected deadline')
    log_times = np.log(frame.beregnet_seconds.to_numpy()[observed])

    def objective(theta):
        means, sigmas = parameters(frame, theta[0], np.exp(theta[1]))
        z = (log_times - means[observed]) / sigmas[observed]
        nll = np.sum(np.log(sigmas[observed]) + z*z/2)
        if dnf_weight:
            z_limit = (np.log(thresholds[~observed]) - means[~observed]) / sigmas[~observed]
            nll -= dnf_weight * log_ndtr(-z_limit).sum()
        return float(nll)

    bounds = [(np.log(900), np.log(14400)), (np.log(1e-8), np.log(1000))]
    fits = [minimize(objective, [frame.b_t_hat.iloc[0], np.log(v)],
                     bounds=bounds, method='L-BFGS-B')
            for v in [max(frame.r_t.iloc[0], 1e-8), .01, .1]]
    good = [f for f in fits if f.success and np.isfinite(f.fun)]
    if not good:
        raise RuntimeError('Censored fit did not converge')
    fit = min(good, key=lambda f: f.fun)
    mu, log_variance = fit.x
    return mu, np.exp(log_variance), fit.fun, any(
        abs(x - edge) < 1e-5 for x, pair in zip(fit.x, bounds) for edge in pair)


def tail_probability(limit, scale, means, sigmas, shift):
    """P(scale * (exp(Y) - handicap_shift) > limit)."""
    threshold = np.asarray(limit / scale + shift)
    return ndtr((means - np.log(np.maximum(threshold, 1e-300))) / sigmas)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--year', type=int, default=2026)
    p.add_argument('--available-minutes', type=float, nargs='+', default=[120],
                   help='Historical deadline minus first start; assumed grid unless confirmed')
    p.add_argument('--schedule', type=Path,
                   help='CSV: race_date,group,available_minutes (actual elapsed limit per group)')
    p.add_argument('--target-minutes', type=float, nargs='+', default=[75, 90, 105],
                   help='Planned mean elapsed duration BEFORE extending the course')
    p.add_argument('--extra-minutes', type=float, default=15)
    p.add_argument('--deadline-extension', type=float, default=20)
    p.add_argument('--dnf-weights', type=float, nargs='+', default=[0, .5, 1])
    p.add_argument('--allow-late-finishers', action='store_true',
                   help='Retain observed late finishes; DNF censoring at nominal limit is an assumption')
    p.add_argument('--output-dir', type=Path, default=ROOT/'analysis/results/time_limit/distribution')
    args = p.parse_args()
    if (min(args.available_minutes) <= 5 or min(args.target_minutes) <= 0
            or not all(0 <= w <= 1 for w in args.dnf_weights)
            or args.extra_minutes < 0 or args.deadline_extension < 0):
        p.error('Invalid duration or DNF weight')
    source = ROOT/'analysis/results/redress_2025_state_history.csv'
    frame = pd.read_csv(source)
    frame = frame[frame.year.eq(args.year) & (frame.observed | frame.status.eq('DNF'))].copy()
    if frame.empty:
        p.error('No active boats for year')
    if frame.duplicated(['race_date','group','competitor']).any():
        raise ValueError('Duplicate boat observations')
    schedule = None
    if args.schedule:
        schedule = pd.read_csv(args.schedule).set_index(['race_date', 'group'])
        if not schedule.index.is_unique:
            raise ValueError('Duplicate schedule rows')
    rows, diagnostics, skipped = [], [], []
    windows = [None] if schedule is not None else args.available_minutes
    for window in windows:
        for (date, group), day in frame.groupby(['race_date','group']):
            limit_min = (float(schedule.loc[(date, group), 'available_minutes']) if schedule is not None
                         else window - (5 if group == 'Lille Bane' else 0))
            for weight in args.dnf_weights:
                key = dict(window_minutes=window if window is not None else 'schedule',
                           race_date=date, group=group, dnf_weight=weight)
                try:
                    mu, variance, nll, boundary = fit_day(
                        day, limit_min*60, weight, args.allow_late_finishers)
                except ValueError as e:
                    skipped.append(dict(**key, reason=str(e)))
                    continue
                means, sigmas = parameters(day, mu, variance)
                shift = ((1000-day.hdcp)*day.length_nm).to_numpy()
                mean_times = np.exp(means + sigmas**2/2) - shift
                if not np.isfinite(mean_times).all() or mean_times.mean() <= 0:
                    raise ValueError('Invalid fitted mean')
                negative_mass = np.where(shift > 0,
                    ndtr((np.log(np.maximum(shift, 1e-300))-means)/sigmas), 0)
                diagnostics.append(dict(**key, mu=mu, variance=variance,
                    original_variance=day.r_t.iloc[0], nll=nll, at_boundary=boundary,
                    finishes=int(day.observed.sum()), dnf=int(day.status.eq('DNF').sum()),
                    observed_after_limit=int((day.observed & day.sailed_seconds.gt(limit_min*60)).sum()),
                    fitted_mean_minutes=mean_times.mean()/60,
                    max_negative_time_probability=negative_mass.max()))
                for target in args.target_minutes:
                    base_scale = target*60/mean_times.mean()
                    new_scale = (target+args.extra_minutes)*60/mean_times.mean()
                    scenarios = [('baseline', base_scale, limit_min),
                        ('same_order', new_scale, limit_min+args.deadline_extension),
                        ('reversed', new_scale, limit_min+args.deadline_extension+
                         (5 if group == 'Lille Bane' else -5))]
                    for scenario, scale, limit in scenarios:
                        probs = tail_probability(limit*60, scale, means, sigmas, shift)
                        rows.append(dict(**key, target_minutes=target, scenario=scenario,
                            boats=len(day), expected_timeouts=probs.sum(),
                            probability=probs.mean(), length_scale=scale))
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    if skipped:
        pd.DataFrame(skipped).to_csv(out/'incompatible_deadlines.csv', index=False)
        raise ValueError('Historical finishers exceed the supplied deadlines. '
                         'No new scenario results written; see incompatible_deadlines.csv. '
                         'Any existing scenario files belong to earlier runs.')
    result = pd.DataFrame(rows)
    if result.empty:
        raise ValueError('No compatible races; check schedule')
    result.to_csv(out/'race_scenarios.csv', index=False)
    pd.DataFrame(diagnostics).to_csv(out/'fit_diagnostics.csv', index=False)
    pd.DataFrame(skipped, columns=['window_minutes','race_date','group','dnf_weight','reason']).to_csv(out/'skipped.csv', index=False)
    keys = ['window_minutes','dnf_weight','target_minutes','group','scenario']
    summary = result.groupby(keys).agg(boats=('boats','sum'),
        expected_timeouts=('expected_timeouts','sum')).reset_index()
    summary['percent'] = 100*summary.expected_timeouts/summary.boats
    summary.to_csv(out/'summary.csv', index=False)
    pivot = summary.pivot(index=['window_minutes','dnf_weight','target_minutes','group'],
                          columns='scenario', values='percent').reset_index()
    report = ['# Fordelingsbaseret følsomhedsanalyse', '',
        'Alle tal er modelberegnede tidsoverskridelser, ikke samlede DNF-prognoser.',
        'Uden en schedule-fil er tidsvinduerne antagelser. DNF-vægt 0,5 er en',
        'vægt i likelihooden, ikke en estimeret sandsynlighed for DNF-årsagen.', '',
        '| Minutter fra første start til frist | DNF-vægt | Planlagt middel før forlængelse | Bane | Før (%) | Længere, samme startorden (%) | Længere, byttet (%) |',
        '|---:|---:|---:|---|---:|---:|---:|']
    late_count = pd.DataFrame(diagnostics).groupby(
        ['window_minutes','race_date','group']).observed_after_limit.first().sum()
    report[2:2] = [f'Observerede fuldførte efter fristen: {late_count}. '
                   f'Tilladt i denne kørsel: {args.allow_late_finishers}.', '']
    for row in pivot.itertuples(index=False):
        report.append(f'| {row.window_minutes} | {row.dnf_weight:g} | {row.target_minutes:g} | '
                      f'{row.group} | {row.baseline:.2f} | {row.same_order:.2f} | {row.reversed:.2f} |')
    report += ['', f'Ekstra sejltid: {args.extra_minutes:g} min. Ekstra frist: {args.deadline_extension:g} min.',
        f'Udeladte tilpasninger pga. uforenelige tidsgrænser: {len(skipped)}.',
        'Se README.md i den overordnede mappe for metode og begrænsninger.']
    (out/'report.md').write_text('\n'.join(report)+'\n')
    manifest = {k:str(v) if isinstance(v, Path) else v for k,v in vars(args).items()}
    manifest['source_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest['schedule_sha256'] = (hashlib.sha256(args.schedule.read_bytes()).hexdigest()
                                   if args.schedule else None)
    (out/'parameters.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(summary.to_string(index=False))
    print(f'Skipped fits: {len(skipped)}. Results: {out}')


if __name__ == '__main__':
    main()
