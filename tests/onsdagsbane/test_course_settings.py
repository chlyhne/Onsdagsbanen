"""Course autosave keeps finish observations and scopes changes to one course."""
import json
import secrets
import unittest
import test_app
from onsdagsbane import service


class CourseSettingsTests(unittest.TestCase):
    setUp = test_app.WebTests.setUp
    request = test_app.WebTests.request
    race = test_app.WebTests.race
    finish = test_app.WebTests.finish
    def setup_data(self):
        with self.app.store.connect() as db:
            round_id = db.execute('SELECT id FROM rounds WHERE series_id=? ORDER BY position', (self.series,)).fetchone()[0]
        return {'series_id': self.series, 'round_id': round_id, 'course': 'stor',
                'mode': 'normal', 'handicap_system': 'circle', 'wind_category': 'M',
                'start': '2026-05-06T18:30:00', 'reference': 1000, 'route': ['A', 'D'], 'revision': None}

    def test_create_update_retry_and_stale_write(self):
        data = self.setup_data()
        status, result = self.request('/api/course-settings', data)
        self.assertEqual(status, 200, result)
        race = result['race']
        self.assertEqual(self.request('/api/course-settings', data)[1]['race']['id'], race['id'])
        self.finish(race['id'], self.boats[0])
        with self.app.store.connect() as db:
            before = service.rows(db, race['id'])[0]
            revision = db.execute('SELECT revision FROM races WHERE id=?', (race['id'],)).fetchone()[0]
        edit = {**data, 'revision': revision, 'route': ['A', 'D', 'A'], 'wind_category': 'L'}
        status, result = self.request('/api/course-settings', edit)
        self.assertEqual(status, 200, result)
        self.assertEqual(result['race']['rating_key'], 'TACIL')
        self.assertEqual(json.loads(result['race']['route']), edit['route'])
        self.assertEqual(self.request('/api/course-settings', edit)[1]['race']['revision'], result['race']['revision'])
        self.assertEqual(self.request('/api/course-settings', {**edit, 'route': ['D']})[0], 400)
        with self.app.store.connect() as db:
            after = service.rows(db, race['id'])[0]
            self.assertEqual(after['instant'], before['instant'])
            self.assertEqual(after['start'], before['start'])
            self.assertEqual(after['handicap_set_id'], before['handicap_set_id'])
            self.assertNotEqual(after['corrected'], before['corrected'])

    def test_changes_do_not_touch_other_course_or_same_day_round(self):
        data = self.setup_data()
        race = self.request('/api/course-settings', data)[1]['race']
        other_course = self.request('/api/course-settings', {**data, 'course': 'lille'})[1]['race']
        other_round = self.race(label='R2')
        edit = {**data, 'revision': race['revision'], 'wind_category': 'L', 'route': ['D']}
        self.assertEqual(self.request('/api/course-settings', edit)[0], 200)
        with self.app.store.connect() as db:
            for identity in (other_course['id'], other_round):
                row = db.execute('SELECT * FROM races WHERE id=?', (identity,)).fetchone()
                self.assertEqual(row['rating_key'], 'TACIM')
                self.assertEqual(json.loads(row['route']), ['A', 'D'])

    def test_failed_wind_change_is_atomic_and_recorder_cannot_save(self):
        data = self.setup_data()
        race = self.request('/api/course-settings', data)[1]['race']
        edit = {**data, 'revision': race['revision'], 'wind_category': 'H', 'route': ['D']}
        status, result = self.request('/api/course-settings', edit)
        self.assertEqual(status, 400, result)
        self.assertTrue(result['handicap_issues'])
        with self.app.store.connect() as db:
            row = db.execute('SELECT * FROM races WHERE id=?', (race['id'],)).fetchone()
            self.assertEqual(json.loads(row['route']), ['A', 'D'])
            self.assertEqual(row['rating_key'], 'TACIM')
        self.assertEqual(self.request('/api/course-settings', edit, role='recorder')[0], 403)

    def test_pending_finish_survives_automatic_creation(self):
        data = self.setup_data()
        stamp = '2026-05-06T19:30:00+02:00'
        self.assertEqual(self.request(f'/api/rounds/{data["round_id"]}/finish', {'boat_id': self.boats[0], 'instant': stamp, 'request_id': secrets.token_hex(16)})[0], 200)
        status, result = self.request('/api/course-settings', data)
        self.assertEqual(status, 200, result)
        with self.app.store.connect() as db:
            row = next(r for r in service.rows(db, result['race']['id']) if r['boat_id'] == self.boats[0])
            self.assertEqual(row['instant'], stamp)
            self.assertIsNotNone(row['corrected'])

    def test_empty_route_and_completed_round_do_not_overwrite(self):
        data = self.setup_data()
        self.assertEqual(self.request('/api/course-settings', {**data, 'route': []})[0], 400)
        race = self.request('/api/course-settings', data)[1]['race']
        with self.app.store.connect(write=True) as db:
            db.execute('UPDATE rounds SET completed=1 WHERE id=?', (data['round_id'],))
        self.assertEqual(self.request('/api/course-settings', {**data, 'revision': race['revision'], 'route': ['D']})[0], 400)

    def test_manual_distance_and_route_take_turns_preserving_finish_observations(self):
        data = {**self.setup_data(), 'route': [], 'distance': '4,25'}
        status, result = self.request('/api/course-settings', data)
        self.assertEqual(status, 200, result)
        race = result['race']
        self.assertEqual((json.loads(race['route']), race['distance']), ([], 4.25))
        self.finish(race['id'], self.boats[0])
        with self.app.store.connect() as db:
            before = service.rows(db, race['id'])[0]
        for route, submitted, expected in ((['A', 'D'], 999, service.geometry(['A', 'D'])['distance']),
                                           ([], '6,125', 6.125), (['D'], 999, service.geometry(['D'])['distance'])):
            with self.subTest(route=route):
                with self.app.store.connect() as db:
                    revision = db.execute('SELECT revision FROM races WHERE id=?', (race['id'],)).fetchone()[0]
                edit = {**data, 'route': route, 'distance': submitted, 'revision': revision}
                status, result = self.request('/api/course-settings', edit)
                self.assertEqual(status, 200, result)
                self.assertEqual(json.loads(result['race']['route']), route)
                self.assertEqual(result['race']['distance'], expected)
                # Retrying after a lost response remains idempotent.
                self.assertEqual(self.request('/api/course-settings', edit)[1]['race']['revision'], result['race']['revision'])
                with self.app.store.connect() as db:
                    after = service.rows(db, race['id'])[0]
                self.assertEqual(after['distance'], expected)
                for field in ('instant', 'start', 'handicap_set_id'):
                    self.assertEqual(after[field], before[field])
        with self.app.store.connect() as db:
            revision = db.execute('SELECT revision FROM races WHERE id=?', (race['id'],)).fetchone()[0]
        self.assertEqual(self.request('/api/course-settings', {**data, 'distance': 0, 'revision': revision})[0], 400)
        with self.app.store.connect() as db:
            self.assertEqual(json.loads(db.execute('SELECT route FROM races WHERE id=?', (race['id'],)).fetchone()[0]), ['D'])

    def test_manual_distance_requires_positive_finite_value(self):
        for distance in (None, '', '0', '-1', 'NaN', 'Infinity', '1e1000'):
            with self.subTest(distance=distance):
                self.assertEqual(self.request('/api/course-settings', {**self.setup_data(), 'route': [], 'distance': distance})[0], 400)

    def test_courses_and_planned_starts_can_use_dates_outside_series_year(self):
        data = self.setup_data()
        rounds = self.request(f'/api/workspaces/series/{self.series}')[1]['rounds']
        for planned, day in zip(rounds, ('2026-12-30', '2027-01-06', '2025-12-31')):
            with self.subTest(day=day):
                status, result = self.request('/api/course-settings', {**data, 'round_id': planned['id'], 'start': day+'T18:30:00'})
                self.assertEqual(status, 200, result)
                self.assertEqual(result['race']['start'][:10], day)
        # Moving a configured race across years updates its planned starts too.
        status, result = self.request('/api/rounds/starts', {'round_id': rounds[1]['id'], 'race_date': '2028-01-05'})
        self.assertEqual(status, 200, result)
        with self.app.store.connect() as db:
            race = db.execute('SELECT r.id,r.start FROM races r JOIN round_courses c ON c.id=r.round_course_id WHERE c.round_id=?', (rounds[1]['id'],)).fetchone()
            self.assertEqual(race['start'][:10], '2028-01-05')
            self.assertTrue(all(entry['start'][:10]=='2028-01-05' for entry in service.rows(db, race['id'])))
        # Calendar validity remains enforced independently of the series year.
        self.assertEqual(self.request('/api/rounds/starts', {'round_id': rounds[1]['id'], 'race_date': '2027-02-29'})[0], 400)

    def test_rounding_warning_in_preview_and_saved_timing_is_scoped_to_round(self):
        data=self.setup_data()
        self.assertEqual(self.request('/api/course-settings',data)[0],200)
        status,preview=self.request('/api/geometry',{'round_id':data['round_id'],'course':'lille','route':['D','A']})
        self.assertEqual(status,200,preview)
        expected=[{'name':name,'stor':['port'],'lille':['starboard']} for name in ('A','D')]
        self.assertEqual(preview['rounding_conflicts'],expected)
        with self.app.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM races').fetchone()[0],1)
            other=db.execute('SELECT id FROM rounds WHERE series_id=? AND id<>? ORDER BY position',(self.series,data['round_id'])).fetchone()[0]
        status,result=self.request('/api/course-settings',{**data,'course':'lille','route':['D','A']})
        self.assertEqual(status,200,result)
        self.assertEqual(self.request(f'/api/rounds/{data["round_id"]}/timing')[1]['rounding_conflicts'],expected)
        self.assertEqual(self.request('/api/course-settings',{**data,'round_id':other,'course':'lille','route':['D','A']})[0],200)
        status,preview=self.request('/api/geometry',{'round_id':data['round_id'],'course':'lille','route':['A','D']})
        self.assertEqual(status,200,preview)
        self.assertEqual(preview['rounding_conflicts'],[])
        self.assertEqual(self.request('/api/course-settings',{**data,'course':'lille','revision':result['race']['revision']})[0],200)
        self.assertEqual(self.request(f'/api/rounds/{data["round_id"]}/timing')[1]['rounding_conflicts'],[])
        self.assertEqual(self.request(f'/api/rounds/{other}/timing')[1]['rounding_conflicts'],[])

    def test_shared_rounding_check_keeps_all_visits_and_ignores_unknown_routes(self):
        same=[{'course':course,'rounding_marks':[{'name':'A','side':'port'}]} for course in ('stor','lille')]
        self.assertEqual(service.rounding_conflicts(same),[])
        self.assertEqual(service.rounding_conflicts(same[:1]),[])
        self.assertEqual(service.rounding_conflicts([same[0],{'course':'lille','rounding_marks':[]}]),[])
        self.assertEqual(service.rounding_conflicts([same[0],{'course':'lille','rounding_marks':[{'name':'D','side':'starboard'}]}]),[])
        repeated=[{'course':'stor','rounding_marks':service.geometry(['A','D','A'])['rounding_marks']},same[1]]
        self.assertEqual(service.rounding_conflicts(repeated),[{'name':'A','stor':['port','starboard'],'lille':['port']}])
