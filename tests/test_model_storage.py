"""Persist fitted parameters independently of generated analysis tables."""
from pathlib import Path
import tempfile
import unittest

from m2s_combiner.redress.model import (load_group_qa_cache, q_cache_path_for_objective,
                                       save_group_qa_cache)


class ModelStorageTests(unittest.TestCase):
    def test_new_model_folder_round_trips_fitted_parameters(self):
        with tempfile.TemporaryDirectory() as folder:
            results = Path(folder) / 'analysis' / 'results'
            results.mkdir(parents=True)
            cache = q_cache_path_for_objective(results, 'mle')
            save_group_qa_cache(cache, {'Stor Bane': .2}, {'Stor Bane': .01}, {'Stor Bane': 2.})
            q, gamma, k, _ = load_group_qa_cache(cache)
            self.assertEqual(q, {'Stor Bane': .2})
            self.assertEqual(gamma, {'Stor Bane': .01})
            self.assertEqual(k, {'Stor Bane': 2.})
            self.assertNotIn(results, cache.parents)
            self.assertEqual(list(results.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
