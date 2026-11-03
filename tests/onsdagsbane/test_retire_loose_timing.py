import json
import secrets
import unittest
import test_app
from onsdagsbane import service, timing, unconfigured
from onsdagsbane.store import Store


class RetiredTimingTests(unittest.TestCase):
    setUp=test_app.WebTests.setUp
    request=test_app.WebTests.request
    race=test_app.WebTests.race
    finish=test_app.WebTests.finish

    def test_capture_http_endpoints_are_removed_but_boat_timing_works(self):
        race=self.race()
        with self.app.store.connect() as db:round_id=timing.round_id_for_race(db,race)
        for path in (f'/api/rounds/{round_id}/capture',f'/api/races/{race}/capture'):
            status,result=self.request(path,{'instant':'2026-05-06T19:30:00+02:00','request_id':secrets.token_hex(16)})
            self.assertEqual(status,410,result)
        status,result=self.request(f'/api/rounds/{round_id}/finish',{'boat_id':self.boats[0],'instant':'2026-05-06T19:30:00+02:00','request_id':secrets.token_hex(16)})
        self.assertEqual(status,200,result)

    def test_upgrade_archives_loose_times_without_changing_boat_results(self):
        race=self.race()
        self.finish(race,self.boats[0])
        with self.app.store.connect(write=True) as db:
            round_id=timing.round_id_for_race(db,race)
            old=service.capture(db,race,{'instant':'2026-05-06T19:31:00+02:00','request_id':secrets.token_hex(16)},self.user)
            pending=unconfigured.capture(db,round_id,{'instant':'2026-05-06T19:32:00+02:00','request_id':secrets.token_hex(16)},self.user)
            before=[tuple(r) for r in db.execute('SELECT * FROM finishes ORDER BY 1,2')]
            db.execute('PRAGMA user_version=4')
        Store(self.app.store.path)
        with self.app.store.connect() as db:
            self.assertEqual(before,[tuple(r) for r in db.execute('SELECT * FROM finishes ORDER BY 1,2')])
            self.assertEqual(timing.detail(db,round_id)['captures'],[])
            self.assertEqual(db.execute('SELECT instant FROM captures WHERE id=?',(old['id'],)).fetchone()[0],old['instant'])
            self.assertEqual(db.execute('SELECT instant FROM round_captures WHERE id=?',(-pending['id'],)).fetchone()[0],pending['instant'])
            payload=json.loads(db.execute("SELECT payload FROM audit WHERE action='timing.loose.retired'").fetchone()[0])
            self.assertEqual(len(payload['captures'])+len(payload['round_captures']),2)
            revisions={str(r['id']):r['revision'] for r in timing.course_races(db,round_id)}
        self.assertEqual(self.request(f'/api/rounds/{round_id}/complete',{'revisions':revisions})[0],200)
