import contextlib
import io
from itertools import permutations
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd
from scipy.special import ndtr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from evaluate_redress_points import (
    collect_time_priors, conditional_ranking_pmf, evaluate, insertion_probabilities,
    make_group, pl_log_probability, time_model, time_prediction,
)
from ranking_odds import probabilities


PARAMETERS = {'q':1e-6, 'q_gamma':1e-6, 'p0':.05, 'initial_x0':0.}


def races():
    rows = []
    for race, date in enumerate(['2026-05-06', '2026-05-13', '2026-05-20', '2026-05-27'], 1):
        for boat, time in [('A', 3000.), ('B', 3200.), ('C', 3500.)]:
            rows.append(dict(year=2026, group='Test', race_date=date, race_local=f'R{race}',
                competitor=boat, observed=True, status='', beregnet_seconds=time+race*15))
    return pd.DataFrame(rows)


class RedressPointTests(unittest.TestCase):
    def test_insertion_matches_enumerated_full_rankings(self):
        # Compare with the definition, not another implementation of the hazards.
        strengths = np.log(np.array([[.3, 1., 2., 5.], [20., .5, 4., 2.]]))
        target = 0
        for order in permutations([1, 2, 3]):
            order = np.array(order)
            probabilities = insertion_probabilities(strengths, target, order)
            full = [np.exp(pl_log_probability(strengths, np.insert(order, k, target)))
                    for k in range(4)]
            expected = np.column_stack(full)
            expected /= np.exp(pl_log_probability(strengths, order))[:, None]
            np.testing.assert_allclose(probabilities, expected, atol=1e-14)
            np.testing.assert_allclose(probabilities.sum(axis=1), 1., atol=1e-14)

    def test_equal_strengths_have_uniform_insertion_positions(self):
        probabilities = insertion_probabilities(np.zeros((2, 5)), 4, np.array([3, 0, 2, 1]))
        np.testing.assert_allclose(probabilities, np.full((2, 5), .2))

    def test_conditioning_on_rivals_order_changes_expected_position(self):
        samples = np.log(np.array([[1., 10., .1]]))
        ordinary = insertion_probabilities(samples, 0, np.array([1, 2]))
        upset = insertion_probabilities(samples, 0, np.array([2, 1]))
        self.assertGreater(float(upset[0] @ np.arange(1, 4)), float(ordinary[0] @ np.arange(1, 4)))

    def test_tied_rivals_are_marginalized_not_ordered_arbitrarily(self):
        mean = np.log(np.array([2., 1., 5.]))
        covariance = np.eye(3)*1e-12
        orders = [np.array([1, 2]), np.array([2, 1])]
        result = conditional_ranking_pmf(mean, covariance, 0, orders, power=8)
        weights = np.array([np.exp(pl_log_probability(mean[None, :], order)[0]) for order in orders])
        expected = sum(w*insertion_probabilities(mean[None, :], 0, order)[0]
                       for w, order in zip(weights, orders))/weights.sum()
        np.testing.assert_allclose(result, expected, atol=2e-7)

    def test_joint_integration_agrees_with_analytic_expected_rank_when_order_unknown(self):
        mean = np.array([.8, -.5, .2])
        covariance = np.array([[1., .3, -.1], [.3, .8, .15], [-.1, .15, .6]])
        # Both possible rival orders represent no information about their order.
        pmf = conditional_ranking_pmf(mean, covariance, 0,
            [np.array([1, 2]), np.array([2, 1])], power=14)
        # Gauss-Hermite pair marginals independently give the expected rank.
        expected = 1+sum(probabilities(mean, covariance)[j, 0] for j in [1, 2])
        self.assertAlmostEqual(float(pmf @ np.arange(1, 4)), float(expected), delta=3e-4)

    def test_expected_time_points_agree_with_analytic_pair_sum(self):
        frame = races()
        prior = collect_time_priors(frame, PARAMETERS)['2026-05-27']
        other_times = np.array([3250., 3550.])
        _, pmf = time_prediction(prior, 'A', ['B', 'C'], other_times)
        # Recover the time model's parameters independently and apply E[sum I].
        ids = [prior.competitors.index(name) for name in ['B', 'C']]
        i = prior.competitors.index('A')
        day_mean, day_variance, _ = time_model.fit_day_parameters_full(
            np.log(other_times)-prior.mean[ids], prior.covariance[np.ix_(ids, ids)])
        mean = day_mean+prior.mean[i]+np.sqrt(day_variance)*prior.gamma_mean[i]
        variance = prior.covariance[i, i]+day_variance*(1+prior.gamma_covariance[i, i])
        expected = 1+ndtr((mean-np.log(other_times))/np.sqrt(variance)).sum()
        self.assertAlmostEqual(float(pmf @ np.arange(1, 4)), float(expected), places=12)
        # Equal rival times leave no possible strict rank between those rivals.
        _, ties = time_prediction(prior, 'A', ['B', 'C'], np.array([3300., 3300.]))
        self.assertEqual(ties[1], 0.)

    def test_collecting_priors_does_not_change_production_filter_results(self):
        group = make_group(races())
        ordinary = time_model.run_group_filter(group, PARAMETERS['q'],
            q_gamma=PARAMETERS['q_gamma'], initial_p0=PARAMETERS['p0'])
        collected = time_model.run_group_filter(group, PARAMETERS['q'],
            q_gamma=PARAMETERS['q_gamma'], initial_p0=PARAMETERS['p0'], collect_priors=True)
        pd.testing.assert_frame_equal(ordinary.history, collected.history)
        self.assertEqual(ordinary.nll_sum, collected.nll_sum)
        self.assertEqual(ordinary.priors, [])
        self.assertEqual(len(collected.priors), 4)
        for prior in collected.priors:
            for name, value in zip(prior.competitors, prior.mean):
                row = collected.history[(collected.history.race == prior.race)
                                        & (collected.history.competitor == name)].iloc[0]
                self.assertEqual(row.x_prior, value)

    def test_hidden_current_result_cannot_change_its_own_prediction(self):
        original = races()
        changed = original.copy()
        changed.loc[changed.competitor.eq('A') & changed.race_local.eq('R4'), 'beregnet_seconds'] = 9000.
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
            a = evaluate(original, {'Test':PARAMETERS}, 2., 8, Path(d))
            b = evaluate(changed, {'Test':PARAMETERS}, 2., 8, Path(d))
        a = a[a.competitor.eq('A')].set_index('method').sort_index()
        b = b[b.competitor.eq('A')].set_index('method').sort_index()
        np.testing.assert_array_equal(a.predicted_points, b.predicted_points)
        self.assertEqual(a.actual_points.iloc[0], 1)
        self.assertEqual(b.actual_points.iloc[0], 3)

    def test_future_results_do_not_enter_time_priors(self):
        original = races()
        changed = original.copy()
        changed.loc[changed.race_local.eq('R4'), 'beregnet_seconds'] *= 2
        a, b = collect_time_priors(original, PARAMETERS), collect_time_priors(changed, PARAMETERS)
        for date in a:
            np.testing.assert_array_equal(a[date].mean, b[date].mean)
            np.testing.assert_array_equal(a[date].covariance, b[date].covariance)


if __name__ == '__main__':
    unittest.main()
