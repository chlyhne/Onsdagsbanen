"""Local sensitivity analysis; no assumption that every DNF is a timeout.

Run: .venv/bin/python scripts/analyze_time_limit.py
The added course length targets +15 minutes for each race/group's median boat.
Finishers use actual elapsed time; DNF use the existing redress model median.
This is a constant-speed scenario, not a forecast of evening wind changes.
"""
from pathlib import Path
import argparse
import json
import pandas as pd


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--year', type=int, default=2026)
    parser.add_argument('--extra-minutes', type=float, default=15)
    parser.add_argument('--reference', choices=['median', 'fastest'], default='median')
    parser.add_argument('--first-start', help='HH:MM; constant across the selected season')
    parser.add_argument('--deadline', help='HH:MM; constant common deadline, same day')
    parser.add_argument('--deadline-extension', type=float, default=20)
    args = parser.parse_args()
    if bool(args.first_start) != bool(args.deadline):
        parser.error('--first-start and --deadline must be supplied together')
    root = Path(__file__).resolve().parents[1]
    frame = pd.read_csv(root / 'analysis/results/redress_2025_state_history.csv')
    frame = frame[frame.year.eq(args.year)].copy()
    active = frame[frame.observed | frame.status.eq('DNF')].copy()
    assert not active.duplicated(['group', 'race_local', 'competitor']).any()
    active['time_seconds'] = active.sailed_seconds.where(
        active.observed, active.pred_cf_sailed_seconds)
    assert active.time_seconds.notna().all() and active.time_seconds.gt(0).all()
    reference = active.groupby(['group', 'race_local']).time_seconds.transform(
        'median' if args.reference == 'median' else 'min')
    active['extra_sailing_minutes'] = args.extra_minutes * active.time_seconds / reference
    active['margin_change_same_order_minutes'] = args.deadline_extension - active.extra_sailing_minutes
    active['margin_change_reversed_minutes'] = (
        args.deadline_extension + active.group.map({'Lille Bane': 5, 'Stor Bane': -5})
        - active.extra_sailing_minutes)
    if args.first_start:
        def minutes(value):
            h, m = map(int, value.split(':'))
            if not (0 <= h < 24 and 0 <= m < 60):
                raise ValueError('Expected HH:MM')
            return 60 * h + m
        available = minutes(args.deadline) - minutes(args.first_start)
        if available <= 5:
            parser.error('Deadline must follow both starts on the same day')
        active['baseline_margin_minutes'] = (
            available - active.group.map({'Stor Bane': 0, 'Lille Bane': 5})
            - active.time_seconds / 60)
        for order in ['same_order', 'reversed']:
            active[f'{order}_margin_minutes'] = (active.baseline_margin_minutes
                + active[f'margin_change_{order}_minutes'])
            active[f'{order}_crosses_deadline'] = active[f'{order}_margin_minutes'].lt(0)
    out = root / 'analysis/results/time_limit'
    out.mkdir(parents=True, exist_ok=True)
    (out / f'{args.year}_parameters.json').write_text(
        json.dumps(vars(args), indent=2) + '\n')
    active.to_csv(out / f'{args.year}_boat_sensitivity.csv', index=False)
    summary = active.groupby('group').agg(
        finishes=('observed', 'sum'),
        dnf=('status', lambda x: x.eq('DNF').sum()),
        starts=('competitor', 'size'),
        max_extra_minutes=('extra_sailing_minutes', 'max'),
        worse_margin_same_order=('margin_change_same_order_minutes', lambda x: x.lt(0).sum()),
        worse_margin_reversed=('margin_change_reversed_minutes', lambda x: x.lt(0).sum()),
    )
    summary['dnf_percent'] = 100 * summary.dnf / summary.starts
    summary.to_csv(out / f'{args.year}_summary.csv')
    active.groupby(['race_date', 'race_local', 'group']).agg(
        starts=('competitor', 'size'), dnf=('status', lambda x: x.eq('DNF').sum())
    ).to_csv(out / f'{args.year}_races.csv')
    print(summary.to_string())
    print('Worse margin is NOT an additional DNF; absolute deadlines are required.')
    if args.first_start:
        print(active.groupby('group')[[
            'same_order_crosses_deadline', 'reversed_crosses_deadline']].sum().to_string())
        print('Scenario exceedances include imputed DNF times; not predicted total DNF counts.')


if __name__ == '__main__':
    main()
