import math
import unittest

import pandas as pd

from m2s_combiner.redress.model import estimate_global_q


class EstimateGlobalQTests(unittest.TestCase):
    def test_multiple_competitors_use_consecutive_race_dates(self):
        results = pd.DataFrame(
            [
                ("R2", "2026-05-13", "Boat B", 100.0),
                ("R1", "2026-05-06", "Boat A", 100.0),
                ("R1", "2026-05-06", "Boat B", 200.0),
                ("R2", "2026-05-13", "Boat A", 200.0),
            ],
            columns=["race", "race_date", "competitor", "beregnet_seconds"],
        )
        results["group"] = "Stor Bane"
        results["race_date"] = pd.to_datetime(results["race_date"])

        self.assertAlmostEqual(estimate_global_q(results), math.log(2) ** 2 / 7)


if __name__ == "__main__":
    unittest.main()
