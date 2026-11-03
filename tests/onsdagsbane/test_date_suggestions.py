"""Weekly proposals flow forwards without turning into committed dates."""
import unittest
import test_app
from onsdagsbane import hierarchy


class DateSuggestionTests(unittest.TestCase):
    setUp = test_app.WebTests.setUp
    request = test_app.WebTests.request

    def rounds(self):
        return self.request(f'/api/workspaces/series/{self.series}')[1]['rounds']

    def set_date(self, identity, day):
        status, result = self.request('/api/rounds/starts', {'round_id': identity, 'race_date': day,
            'start_times': {'stor': '18:30:00', 'lille': '18:35:00'}})
        self.assertEqual(status, 200, result)

    def test_chain_suggestions_forward_only_and_preserve_later_anchor(self):
        rounds = self.rounds()
        self.assertTrue(all(r['suggested_date'] is None for r in rounds))
        self.set_date(rounds[1]['id'], '2026-05-06')
        self.set_date(rounds[4]['id'], '2026-07-01')
        actual = self.rounds()
        self.assertEqual([r['race_date'] or r['suggested_date'] for r in actual[:6]],
            [None, '2026-05-06', '2026-05-13', '2026-05-20', '2026-07-01', '2026-07-08'])
        with self.app.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM rounds WHERE race_date IS NOT NULL').fetchone()[0], 2)
        self.set_date(rounds[1]['id'], '2026-05-13')
        self.assertEqual([r['race_date'] or r['suggested_date'] for r in self.rounds()[:6]],
            [None, '2026-05-13', '2026-05-20', '2026-05-27', '2026-07-01', '2026-07-08'])
        self.set_date(rounds[1]['id'], None)
        self.assertEqual([r['race_date'] or r['suggested_date'] for r in self.rounds()[:6]],
            [None, None, None, None, '2026-07-01', '2026-07-08'])

    def test_leap_day_and_appended_round_use_proposed_previous_date(self):
        rounds = self.rounds()
        self.set_date(rounds[-3]['id'], '2028-02-22')
        self.assertEqual(self.rounds()[-2]['suggested_date'], '2028-02-29')
        with self.app.store.connect(write=True) as db:
            hierarchy.create_rounds(db, self.series, 'R19', self.user)
        last = self.rounds()[-1]
        self.assertEqual(last['suggested_date'], '2028-03-14')
        self.assertIsNone(last['race_date'])

    def test_suggestions_cross_new_year_and_can_be_committed(self):
        rounds = self.rounds()
        self.set_date(rounds[1]['id'], '2026-12-30')
        proposed = self.rounds()
        self.assertEqual([r['race_date'] or r['suggested_date'] for r in proposed[:4]],
            [None, '2026-12-30', '2027-01-06', '2027-01-13'])
        self.set_date(rounds[2]['id'], proposed[2]['suggested_date'])
        self.assertEqual(self.rounds()[2]['race_date'], '2027-01-06')
        # A valid earlier year is also independent of the series label.
        self.set_date(rounds[0]['id'], '2025-12-31')
        self.assertEqual(self.rounds()[0]['race_date'], '2025-12-31')
