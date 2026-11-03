"""Checks of censoring, physical-time conversion and scenario normalization."""
import importlib.util
from pathlib import Path
import unittest
import numpy as np
import pandas as pd
from scipy.special import ndtr

spec = importlib.util.spec_from_file_location('time_limits', Path(__file__).resolve().parents[1]/'scripts/analyze_time_limit_distribution.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class DistributionTests(unittest.TestCase):
    def test_shifted_lognormal_tail(self):
        mu, sigma, shift = np.array([8.]), np.array([.2]), np.array([1000.])
        self.assertAlmostEqual(m.tail_probability(np.exp(8)-1000, 1, mu, sigma, shift)[0], .5)
        self.assertAlmostEqual(m.tail_probability(2*(np.exp(8)-1000), 2, mu, sigma, shift)[0], .5)
        self.assertGreater(m.tail_probability(100, 1, mu, sigma, shift)[0],
                           m.tail_probability(200, 1, mu, sigma, shift)[0])

    def test_censored_fit_recovers_hidden_tail(self):
        rng = np.random.default_rng(42)
        times = np.exp(rng.normal(8.5, .2, 4000))
        observed = times <= np.exp(8.5)
        f = pd.DataFrame(dict(observed=observed, sailed_seconds=np.where(observed,times,np.nan),
            beregnet_seconds=np.where(observed,times,np.nan), hdcp=1000., length_nm=4.,
            x_prior=0., gamma_prior=0., p_prior=0., p_gamma_prior=0., b_t_hat=8.4, r_t=.02))
        mu, var, _, _ = m.fit_day(f, np.exp(8.5), 1)
        naive_mu, naive_var, _, _ = m.fit_day(f, np.exp(8.5), 0)
        self.assertAlmostEqual(mu, 8.5, delta=.015)
        self.assertAlmostEqual(np.sqrt(var), .2, delta=.015)
        self.assertLess(naive_mu, mu)
        self.assertLess(naive_var, var)

    def test_inconsistent_deadline_rejected(self):
        f = pd.DataFrame(dict(observed=[True], sailed_seconds=[200], hdcp=[1000], length_nm=[4]))
        with self.assertRaisesRegex(ValueError, 'after assumed deadline'):
            m.fit_day(f, 100, 1)

    def test_mean_scaling(self):
        mu, sigma, shift = np.array([8.,8.3]), np.array([.1,.2]), np.array([1000.,800.])
        means = np.exp(mu+sigma**2/2)-shift
        scale = 90*60/means.mean()
        self.assertAlmostEqual((scale*means).mean(), 5400)

    def test_proportional_extension_preserves_risk(self):
        mu, sigma, shift = np.array([8.,8.3]), np.array([.1,.2]), np.array([1000.,800.])
        scale = 90*60 / (np.exp(mu+sigma**2/2)-shift).mean()
        np.testing.assert_allclose(
            m.tail_probability(120*60, scale, mu, sigma, shift),
            m.tail_probability(140*60, scale*105/90, mu, sigma, shift))


if __name__ == '__main__':
    unittest.main()
