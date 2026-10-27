import unittest

import pandas as pd

from m2s_combiner.redress.collect import complete_season_rosters


class SeasonRosterTests(unittest.TestCase):
    def test_missing_entries_are_absent_in_the_correct_races(self):
        rows = []
        for year, race, local, date, competitor, length in [
            (2025, 'R51', 'R17', '2025-09-24', 'Other', 6),
            (2026, 'R52', 'R1', '2026-05-06', 'Other', 4),
            (2026, 'R53', 'R2', '2026-05-13', 'Other', 5),
            (2026, 'R53', 'R2', '2026-05-13', 'Harpelunde Senior', 5),
            (2026, 'R54', 'R3', '2026-05-20', 'Other', 7),
        ]:
            rows.append(dict(year=year, race=race, race_local=local,
                             race_date=pd.Timestamp(date), competitor=competitor,
                             group='Lille Bane', series='Lille bane 2',
                             hdcp=735.2, length_nm=length, sailed_seconds=3600.,
                             beregnet_seconds=4000., race_points=1.,
                             race_rank_raw=1., race_status_code=''))
        source = pd.DataFrame(rows)
        result = complete_season_rosters(source)
        boat = result[result.competitor == 'Harpelunde Senior'].sort_values('race_date')
        self.assertEqual(boat.year.tolist(), [2026, 2026, 2026])
        self.assertEqual(boat.race_local.tolist(), ['R1', 'R2', 'R3'])
        absent = boat[boat.race_local != 'R2']
        self.assertEqual(absent.race_status_code.tolist(), ['DNC', 'DNC'])
        self.assertTrue(absent[['sailed_seconds', 'beregnet_seconds', 'race_points', 'race_rank_raw']].isna().all().all())
        self.assertEqual(absent.length_nm.tolist(), [4, 7])
        self.assertEqual(absent.hdcp.tolist(), [735.2, 735.2])
        pd.testing.assert_frame_equal(result.iloc[:len(source)].reset_index(drop=True), source)
        pd.testing.assert_frame_equal(complete_season_rosters(result), result)


if __name__ == '__main__':
    unittest.main()
