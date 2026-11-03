import secrets
import unittest
import test_app
from onsdagsbane import timing, service, hierarchy, unconfigured
from onsdagsbane.store import Store


class UnconfiguredTimingTests(unittest.TestCase):
    setUp=test_app.WebTests.setUp
    request=test_app.WebTests.request
    race=test_app.WebTests.race

    def round_id(self):
        with self.app.store.connect() as db:
            return db.execute('SELECT id FROM rounds WHERE series_id=? ORDER BY position',(self.series,)).fetchone()[0]

    def observation(self,**extra):
        return {'boat_id':self.boats[0],'instant':'2026-05-06T19:30:00+02:00','request_id':secrets.token_hex(16),**extra}

    def legacy_capture(self,data):
        with self.app.store.connect(write=True) as db:
            return unconfigured.capture(db,self.round_id(),data,self.user)

    def test_timing_before_setup_survives_reload_and_is_calculated_later(self):
        round_id=self.round_id()
        data=self.observation(penalty=15)
        for _ in range(2):self.assertEqual(self.request(f'/api/rounds/{round_id}/finish',data)[0],200)
        Store(self.app.store.path)
        with self.app.store.connect() as db:
            state=timing.detail(db,round_id)
            entry=next(e for e in state['entries'] if e['boat_id']==self.boats[0])
            self.assertIsNone(entry['race_id'])
            self.assertIsNone(entry['corrected'])
            self.assertEqual(entry['instant'],data['instant'])
            self.assertEqual(state['captures'],[])
            self.assertEqual(state['courses'],[])
        self.assertEqual(self.request(f'/api/rounds/{round_id}/complete',{'revisions':{}})[0],400)
        race=self.race()
        with self.app.store.connect() as db:
            row=next(e for e in service.rows(db,race) if e['boat_id']==self.boats[0])
            self.assertEqual(row['instant'],data['instant'])
            self.assertEqual(row['elapsed'],3600)
            self.assertIsNotNone(row['corrected'])
            self.assertEqual(row['penalty'],15)
            self.assertEqual(db.execute('SELECT count(*) FROM pending_finishes').fetchone()[0],0)
            revisions={str(r['id']):r['revision'] for r in timing.course_races(db,round_id)}
        self.assertEqual(self.request(f'/api/rounds/{round_id}/finish',data)[0],200)
        self.assertEqual(self.request(f'/api/rounds/{round_id}/complete',{'revisions':revisions})[0],200)

    def test_edit_undo_and_stale_revision_before_setup(self):
        round_id=self.round_id()
        self.request(f'/api/rounds/{round_id}/finish',self.observation())
        with self.app.store.connect() as db:
            row=next(e for e in timing.detail(db,round_id)['entries'] if e['boat_id']==self.boats[0])
        changed=self.observation(instant='2026-05-06T19:31:00+02:00',revision=row['race_revision'])
        for _ in range(2):self.assertEqual(self.request(f'/api/rounds/{round_id}/edit',changed)[0],200)
        self.assertEqual(self.request(f'/api/rounds/{round_id}/undo',{'boat_id':self.boats[0],'revision':row['race_revision']})[0],400)
        with self.app.store.connect() as db:
            row=next(e for e in timing.detail(db,round_id)['entries'] if e['boat_id']==self.boats[0])
            self.assertEqual(row['instant'],changed['instant'])
        self.assertEqual(self.request(f'/api/rounds/{round_id}/undo',{'boat_id':self.boats[0],'revision':row['race_revision']})[0],200)
        with self.app.store.connect() as db:self.assertIsNone(timing.detail(db,round_id)['entries'][0]['sequence'])

    def test_unassigned_capture_can_be_used_after_configuration(self):
        round_id=self.round_id()
        payload={'instant':'2026-05-06T19:30:00+02:00','request_id':secrets.token_hex(16)}
        cap=self.legacy_capture(payload)
        self.assertEqual(self.legacy_capture(payload)['id'],cap['id'])
        race=self.race()
        self.assertEqual(self.request(f'/api/rounds/{round_id}/finish',self.observation(capture_id=cap['id']))[0],200)
        self.assertEqual(self.request(f'/api/rounds/{round_id}/finish',self.observation(boat_id=self.boats[1],capture_id=cap['id']))[0],400)
        with self.app.store.connect() as db:self.assertEqual(service.rows(db,race)[0]['elapsed'],3600)

    def test_round_capture_is_scoped_and_blocks_completion_until_discarded(self):
        round_id=self.round_id()
        cap=self.legacy_capture({'instant':'2026-05-06T19:30:00+02:00','request_id':secrets.token_hex(16)})
        with self.app.store.connect() as db:
            other=db.execute('SELECT id FROM rounds WHERE series_id=? AND id<>? ORDER BY position',(self.series,round_id)).fetchone()[0]
        self.assertEqual(self.request(f'/api/rounds/{other}/discard-capture',{'id':cap['id']})[0],400)
        self.assertEqual(self.request(f'/api/rounds/{other}/finish',self.observation(capture_id=cap['id']))[0],400)
        race=self.race()
        revisions={str(race):0}
        self.assertEqual(self.request(f'/api/rounds/{round_id}/complete',{'revisions':revisions})[0],400)
        self.assertEqual(self.request(f'/api/rounds/{round_id}/discard-capture',{'id':cap['id']})[0],200)
        self.assertEqual(self.request(f'/api/rounds/{round_id}/complete',{'revisions':revisions})[0],200)

    def test_failed_configuration_preserves_observations_and_can_be_corrected(self):
        round_id=self.round_id()
        self.request(f'/api/rounds/{round_id}/finish',self.observation(instant='2026-05-06T17:00:00+02:00'))
        setup={'series_id':self.series,'round_id':round_id,'course':'stor','mode':'normal','rating_key':'TACIM','start':'2026-05-06T18:30:00+02:00','route':['A','D']}
        self.assertEqual(self.request('/api/races',setup)[0],400)
        with self.app.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM races').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM pending_finishes').fetchone()[0],1)
        self.assertEqual(self.request('/api/races',{**setup,'start':'2026-05-06T16:30:00+02:00'})[0],200)

    def test_pending_measurement_follows_heat_move_and_blocks_removal(self):
        round_id=self.round_id()
        data=self.observation()
        self.request(f'/api/rounds/{round_id}/finish',data)
        with self.app.store.connect(write=True) as db:
            heat=db.execute("SELECT h.id FROM heats h JOIN round_courses c ON c.id=h.round_course_id WHERE c.round_id=? AND h.name='Stor bane 2'",(round_id,)).fetchone()[0]
            profile=db.execute('SELECT profile_id FROM boats WHERE id=?',(self.boats[0],)).fetchone()[0]
            hierarchy.move_profiles(db,heat,{'scope':'round','action':'add','profile_ids':[profile],'include_assigned':True},self.user)
            row=next(e for e in timing.detail(db,round_id)['entries'] if e['boat_id']==self.boats[0])
            self.assertEqual(row['class_name'],'Stor bane 2')
            self.assertEqual(row['instant'],data['instant'])
            with self.assertRaisesRegex(ValueError,'ikke måltaget'):
                hierarchy.move_profiles(db,heat,{'scope':'round','action':'remove','profile_ids':[profile]},self.user)

    def test_unconfigured_second_course_can_use_first_courses_capture(self):
        large=self.race()
        round_id=self.round_id()
        with self.app.store.connect(write=True) as db:
            boat=service.save_boat(db,{'series_id':self.series,'course':'lille','class_name':'Lille bane 1','name':'Small','skipper':'S','sail_number':'DEN S','boat_type':'X','ratings':{'TACIM':600}},self.user)
            cap=service.capture(db,large,{'instant':'2026-05-06T19:30:00+02:00','request_id':secrets.token_hex(16)},self.user)
        status,result=self.request(f'/api/rounds/{round_id}/finish',self.observation(boat_id=boat,capture_id=cap['id']))
        self.assertEqual(status,200,result)
        with self.app.store.connect() as db:
            row=next(e for e in timing.detail(db,round_id)['entries'] if e['boat_id']==boat)
            self.assertIsNone(row['race_id'])
            self.assertEqual(row['status'],'FIN')
            self.assertEqual(db.execute('SELECT used FROM captures WHERE id=?',(cap['id'],)).fetchone()[0],1)
        setup={'series_id':self.series,'round_id':round_id,'course':'lille','mode':'normal','rating_key':'TACIM','start':'2026-05-06T18:35:00+02:00','route':['A','D']}
        status,result=self.request('/api/races',setup)
        self.assertEqual(status,200,result)
        with self.app.store.connect() as db:
            row=next(e for e in timing.detail(db,round_id)['entries'] if e['boat_id']==boat)
            self.assertEqual(row['elapsed'],3300)
            self.assertIsNotNone(row['corrected'])
