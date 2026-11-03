import unittest
import secrets
import test_app
from onsdagsbane import service, timing, unconfigured
from onsdagsbane.store import Store


class ReferenceClockTests(unittest.TestCase):
    setUp=test_app.WebTests.setUp
    request=test_app.WebTests.request
    race=test_app.WebTests.race

    def round_id(self):
        with self.app.store.connect() as db:return db.execute('SELECT id FROM rounds WHERE series_id=? ORDER BY position',(self.series,)).fetchone()[0]

    def finish(self,boat,automatic,stamp='2026-05-06T19:30:00+02:00',**extra):
        return self.request(f'/api/rounds/{self.round_id()}/finish',{'boat_id':boat,'instant':stamp,'automatic':automatic,'clock_offset':0,'request_id':secrets.token_hex(16),**extra})

    def sync(self,desired,previous=0):
        return self.request(f'/api/rounds/{self.round_id()}/clock',{'offset':desired,'previous_offset':previous})

    def legacy_capture(self,data):
        with self.app.store.connect(write=True) as db:
            return unconfigured.capture(db,self.round_id(),data,self.user)

    def test_saved_automatic_times_shift_but_manual_times_do_not(self):
        race=self.race()
        self.assertEqual(self.finish(self.boats[0],True)[0],200)
        self.assertEqual(self.finish(self.boats[1],False)[0],200)
        with self.app.store.connect() as db:before={r['boat_id']:r for r in service.rows(db,race)}
        self.assertEqual(self.sync(60)[0],200)
        with self.app.store.connect() as db:after={r['boat_id']:r for r in service.rows(db,race)}
        self.assertEqual(after[self.boats[0]]['instant'],'2026-05-06T19:31:00+02:00')
        self.assertEqual(after[self.boats[0]]['corrected'],before[self.boats[0]]['corrected']+60)
        self.assertEqual(after[self.boats[1]]['instant'],before[self.boats[1]]['instant'])
        self.assertEqual(after[self.boats[1]]['corrected'],before[self.boats[1]]['corrected'])
        self.assertEqual(self.sync(-60,60)[0],200)
        with self.app.store.connect() as db:self.assertEqual(service.rows(db,race)[0]['instant'],'2026-05-06T19:29:00+02:00')

    def test_unconfigured_observations_and_captures_shift_and_later_calculate(self):
        round_id=self.round_id()
        self.finish(self.boats[0],True)
        self.finish(self.boats[1],False)
        cap=self.legacy_capture({'instant':'2026-05-06T19:30:00+02:00','automatic':True,'request_id':secrets.token_hex(16)})
        self.assertEqual(self.sync(60)[0],200)
        with self.app.store.connect() as db:
            state=timing.detail(db,round_id)
            self.assertEqual(state['round']['clock_offset'],60)
            self.assertEqual(state['captures'][0]['instant'],'2026-05-06T19:31:00+02:00')
            self.assertEqual(state['entries'][0]['instant'],'2026-05-06T19:31:00+02:00')
            self.assertEqual(state['entries'][1]['instant'],'2026-05-06T19:30:00+02:00')
        race=self.race()
        with self.app.store.connect() as db:self.assertEqual(service.rows(db,race)[0]['elapsed'],3660)

    def test_late_automatic_request_uses_latest_offset_once(self):
        self.assertEqual(self.sync(60)[0],200)
        self.assertEqual(self.finish(self.boats[0],True)[0],200)
        with self.app.store.connect() as db:self.assertEqual(timing.detail(db,self.round_id())['entries'][0]['instant'],'2026-05-06T19:31:00+02:00')

    def test_automatic_capture_assigned_after_sync_is_not_shifted_twice(self):
        round_id=self.round_id()
        cap=self.legacy_capture({'instant':'2026-05-06T19:30:00+02:00','automatic':True,'request_id':secrets.token_hex(16)})
        self.sync(60)
        self.race()
        status,result=self.request(f'/api/rounds/{round_id}/finish',{'boat_id':self.boats[0],'capture_id':cap['id'],'request_id':secrets.token_hex(16)})
        self.assertEqual(status,200,result)
        with self.app.store.connect() as db:self.assertEqual(timing.detail(db,round_id)['entries'][0]['instant'],'2026-05-06T19:31:00+02:00')
        self.sync(120,60)
        with self.app.store.connect() as db:self.assertEqual(timing.detail(db,round_id)['entries'][0]['instant'],'2026-05-06T19:32:00+02:00')

    def test_invalid_shift_and_stale_clock_update_are_atomic(self):
        race=self.race()
        self.finish(self.boats[0],True,stamp='2026-05-06T18:30:30+02:00')
        self.assertEqual(self.sync(-60)[0],400)
        with self.app.store.connect() as db:self.assertEqual(timing.detail(db,self.round_id())['round']['clock_offset'],0)
        self.assertEqual(self.sync(60)[0],200)
        self.assertEqual(self.sync(120,0)[0],400)

    def test_manually_edited_automatic_time_no_longer_shifts(self):
        race=self.race()
        self.finish(self.boats[0],True)
        with self.app.store.connect() as db:revision=db.execute('SELECT revision FROM races WHERE id=?',(race,)).fetchone()[0]
        self.assertEqual(self.request(f'/api/rounds/{self.round_id()}/edit',{'boat_id':self.boats[0],'instant':'2026-05-06T19:35:00+02:00','automatic':False,'revision':revision,'request_id':secrets.token_hex(16)})[0],200)
        self.sync(60)
        with self.app.store.connect() as db:self.assertEqual(service.rows(db,race)[0]['instant'],'2026-05-06T19:35:00+02:00')

    def test_upgrade_recognizes_earlier_ui_clicks_without_changing_times(self):
        race=self.race()
        self.finish(self.boats[0],False,stamp='2026-05-06T19:30:00.123000+02:00')
        self.finish(self.boats[1],False)
        with self.app.store.connect(write=True) as db:
            before=[tuple(r) for r in db.execute('SELECT * FROM finishes ORDER BY boat_id')]
            for table in ('clock_sources','clock_capture_sources','round_clocks'):db.execute(f'DROP TABLE {table}')
            db.execute('PRAGMA user_version=3')
        Store(self.app.store.path)
        with self.app.store.connect() as db:
            after=[tuple(r) for r in db.execute('SELECT * FROM finishes ORDER BY boat_id')]
            self.assertEqual(before,after)
            entries=timing.detail(db,self.round_id())['entries']
            self.assertTrue(entries[0]['clock_automatic'])
            self.assertFalse(entries[1]['clock_automatic'])
