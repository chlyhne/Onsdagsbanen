import unittest
import test_app
from onsdagsbane import fleet, service, hierarchy
from onsdagsbane.store import get


class BoatManagementTests(unittest.TestCase):
    setUp=test_app.WebTests.setUp
    request=test_app.WebTests.request
    race=test_app.WebTests.race
    finish=test_app.WebTests.finish

    def profile_id(self):
        with self.app.store.connect() as db:return get(db,'boats',self.boats[0])['profile_id']

    def remove_from_rounds(self,identity,series_id=None):
        with self.app.store.connect(write=True) as db:
            boat=next(b for b in fleet.catalog(db,self.series) if b['id']==identity)
            for assignment in boat['assignments']:
                if series_id is None or assignment['series_id']==series_id:
                    hierarchy.move_profiles(db,assignment['heat_id'],{'scope':'round','action':'remove','profile_ids':[identity]},self.user)

    def test_edit_shared_profile_preserves_saved_results_and_handicap_sets(self):
        race=self.race();self.finish(race,self.boats[0])
        identity=self.profile_id()
        with self.app.store.connect(write=True) as db:
            other=service.create_series(db,{'name':'Other','year':2027},self.user)
            alias=fleet.enroll(db,other,{'profile_ids':[identity],'course':'stor','class_name':'Stor bane 1'},self.user)[0]
            before_entries=[tuple(r) for r in db.execute('SELECT * FROM entries')]
            before_finishes=[tuple(r) for r in db.execute('SELECT * FROM finishes')]
            before=fleet.profile(db,identity)
        status,result=self.request('/api/boat-profiles',{**before,'name':'Ægir','ratings':{**before['ratings'],'TACIM':'777,7'}})
        self.assertEqual(status,200,result)
        with self.app.store.connect() as db:
            self.assertEqual(get(db,'boats',alias)['name'],'Ægir')
            self.assertEqual(fleet.profile(db,identity)['ratings']['TACIM'],777.7)
            self.assertEqual(before_entries,[tuple(r) for r in db.execute('SELECT * FROM entries')])
            self.assertEqual(before_finishes,[tuple(r) for r in db.execute('SELECT * FROM finishes')])

    def test_profile_race_history_spans_series_and_requires_admin(self):
        first=self.race()
        identity=self.profile_id()
        with self.app.store.connect(write=True) as db:
            other=service.create_series(db,{'name':'Other','year':2027,'round_labels':'R1'},self.user)
            alias=fleet.enroll(db,other,{'profile_ids':[identity],'course':'stor','class_name':'Stor bane 1'},self.user)[0]
            second=service.create_race(db,{'series_id':other,'course':'stor','mode':'normal','label':'R1','rating_key':'TACIM','start':'2027-05-05T18:30:00+02:00','route':['A','D'],'boat_ids':[alias]},self.user)
        status,history=self.request(f'/api/boat-profiles/history?id={identity}')
        self.assertEqual(status,200,history)
        self.assertEqual([r['race_id'] for r in history],[second,first])
        self.assertEqual([r['boat_id'] for r in history],[alias,self.boats[0]])
        self.assertEqual({r['series_id'] for r in history},{self.series,other})
        self.assertEqual(self.request(f'/api/boat-profiles/history?id={identity}',role='recorder')[0],403)

    def test_catalog_distinguishes_unused_planned_and_saved_usage(self):
        identity=self.profile_id()
        with self.app.store.connect() as db:
            boat=next(b for b in fleet.catalog(db,-1) if b['id']==identity)
            self.assertEqual(boat['round_count'],18)
            self.assertEqual(boat['saved_race_count'],0)
        self.race()
        status,created=self.request('/api/boat-profiles',{'name':'Unused','skipper':'S','sail_number':'U1','boat_type':'X','ratings':{}})
        self.assertEqual(status,200,created)
        with self.app.store.connect() as db:
            catalog={b['id']:b for b in fleet.catalog(db,-1)}
            self.assertEqual(catalog[identity]['saved_race_count'],1)
            self.assertEqual(catalog[identity]['round_count'],18)
            self.assertEqual(catalog[created['id']]['round_count'],0)
            self.assertEqual(catalog[created['id']]['saved_race_count'],0)

    def test_delete_only_after_explicit_removal_from_every_round(self):
        race=self.race();identity=self.profile_id()
        with self.app.store.connect(write=True) as db:
            other=service.create_series(db,{'name':'Other','year':2027,'round_labels':'R1–R3'},self.user)
            alias=fleet.enroll(db,other,{'profile_ids':[identity],'course':'stor','class_name':'Stor bane 1'},self.user)[0]
            remaining=[tuple(r) for r in db.execute('SELECT boat_id,handicap_set_id FROM entries WHERE boat_id<>? ORDER BY boat_id',(self.boats[0],))]
        status,preview=self.request(f'/api/boat-profiles/delete-preview?id={identity}')
        self.assertEqual(status,200,preview)
        self.assertFalse(preview['can_delete'])
        self.assertEqual(len(preview['rounds']),21)
        self.assertEqual(len(preview['series']),2)
        status,_=self.request('/api/boat-profiles/delete',{'id':identity,'token':preview['token'],'confirm':True})
        self.assertEqual(status,400)
        self.remove_from_rounds(identity)
        _,preview=self.request(f'/api/boat-profiles/delete-preview?id={identity}')
        self.assertTrue(preview['can_delete'])
        self.assertEqual(preview['rounds'],[])
        status,result=self.request('/api/boat-profiles/delete',{'id':identity,'token':preview['token'],'confirm':True})
        self.assertEqual(status,200,result)
        with self.app.store.connect() as db:
            for table,column in [('boat_profiles','id'),('boats','profile_id'),('handicap_sets','profile_id')]:
                self.assertEqual(db.execute(f'SELECT count(*) FROM {table} WHERE {column}=?',(identity,)).fetchone()[0],0)
            self.assertEqual(remaining,[tuple(r) for r in db.execute('SELECT boat_id,handicap_set_id FROM entries ORDER BY boat_id')])
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertIsNone(db.execute('SELECT id FROM boats WHERE id=?',(alias,)).fetchone())

    def test_historical_boat_cannot_be_deleted(self):
        race=self.race();self.finish(race,self.boats[1])
        identity=self.profile_id()
        status,preview=self.request(f'/api/boat-profiles/delete-preview?id={identity}')
        self.assertEqual(status,200,preview)
        self.assertFalse(preview['can_delete'])
        status,result=self.request('/api/boat-profiles/delete',{'id':identity,'token':preview['token'],'confirm':True})
        self.assertEqual(status,400,result)
        with self.app.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM finishes').fetchone()[0],1)
            self.assertIsNotNone(get(db,'boats',self.boats[0]))

    def test_delete_requires_confirmation_and_current_preview(self):
        identity=self.profile_id()
        self.remove_from_rounds(identity)
        _,preview=self.request(f'/api/boat-profiles/delete-preview?id={identity}')
        status,_=self.request('/api/boat-profiles/delete',{'id':identity,'token':preview['token']})
        self.assertEqual(status,400)
        with self.app.store.connect(write=True) as db:db.execute('UPDATE boat_profiles SET club=? WHERE id=?',('Ny klub',identity))
        status,_=self.request('/api/boat-profiles/delete',{'id':identity,'token':preview['token'],'confirm':True})
        self.assertEqual(status,400)
        with self.app.store.connect() as db:self.assertEqual(fleet.profile(db,identity)['club'],'Ny klub')

    def test_planned_dateless_rounds_block_deletion_without_touching_memberships(self):
        identity=self.profile_id()
        with self.app.store.connect() as db:
            before=[tuple(r) for r in db.execute('SELECT * FROM heat_boats ORDER BY heat_id,boat_id')]
        _,preview=self.request(f'/api/boat-profiles/delete-preview?id={identity}')
        self.assertFalse(preview['can_delete'])
        self.assertEqual(len(preview['rounds']),18)
        self.assertEqual({r['label'] for r in preview['rounds']},{f'R{i}' for i in range(1,19)})
        status,_=self.request('/api/boat-profiles/delete',{'id':identity,'token':preview['token'],'confirm':True})
        self.assertEqual(status,400)
        with self.app.store.connect() as db:
            self.assertEqual(before,[tuple(r) for r in db.execute('SELECT * FROM heat_boats ORDER BY heat_id,boat_id')])

    def test_single_round_in_another_series_blocks_global_deletion(self):
        identity=self.profile_id()
        with self.app.store.connect(write=True) as db:
            other=service.create_series(db,{'name':'One remaining','year':2027,'round_labels':'R9'},self.user)
            fleet.enroll(db,other,{'profile_ids':[identity],'course':'stor','class_name':'Stor bane 1'},self.user)
        self.remove_from_rounds(identity,self.series)
        _,preview=self.request(f'/api/boat-profiles/delete-preview?id={identity}')
        self.assertFalse(preview['can_delete'])
        self.assertEqual([(r['series_name'],r['label']) for r in preview['rounds']],[('One remaining','R9')])
        self.remove_from_rounds(identity,other)
        _,preview=self.request(f'/api/boat-profiles/delete-preview?id={identity}')
        self.assertTrue(preview['can_delete'])

    def test_saved_entry_blocks_deletion_even_without_planned_membership(self):
        self.race();identity=self.profile_id()
        with self.app.store.connect(write=True) as db:
            db.execute('DELETE FROM heat_boats WHERE boat_id=?',(self.boats[0],))
        _,preview=self.request(f'/api/boat-profiles/delete-preview?id={identity}')
        self.assertFalse(preview['can_delete'])
        self.assertEqual([r['label'] for r in preview['rounds']],['R1'])

    def test_new_membership_after_preview_prevents_deletion(self):
        identity=self.profile_id();self.remove_from_rounds(identity)
        _,preview=self.request(f'/api/boat-profiles/delete-preview?id={identity}')
        self.assertTrue(preview['can_delete'])
        with self.app.store.connect(write=True) as db:
            heat=db.execute('SELECT h.id FROM heats h JOIN round_courses c ON c.id=h.round_course_id JOIN rounds p ON p.id=c.round_id WHERE p.series_id=? LIMIT 1',(self.series,)).fetchone()[0]
            hierarchy.move_profiles(db,heat,{'scope':'round','action':'add','profile_ids':[identity]},self.user)
        status,_=self.request('/api/boat-profiles/delete',{'id':identity,'token':preview['token'],'confirm':True})
        self.assertEqual(status,400)
        with self.app.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM heat_boats WHERE boat_id=?',(self.boats[0],)).fetchone()[0],1)

    def test_bulk_removal_is_not_a_shortcut_to_deletion(self):
        identity=self.profile_id()
        with self.app.store.connect() as db:
            heat=db.execute('SELECT heat_id FROM heat_boats WHERE boat_id=? LIMIT 1',(self.boats[0],)).fetchone()[0]
        for scope in ('series','forward','backward'):
            with self.subTest(scope=scope):
                status,result=self.request('/api/heats/boats/move',{'heat_id':heat,'profile_ids':[identity],'action':'remove','scope':scope})
                self.assertEqual(status,400,result)
                self.assertIn('enkeltvis',result['error'])
        with self.app.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM heat_boats WHERE boat_id=?',(self.boats[0],)).fetchone()[0],18)

    def test_edit_rejects_stale_profile(self):
        identity=self.profile_id()
        _,boat=self.request(f'/api/boat-profiles?id={identity}')
        with self.app.store.connect(write=True) as db:db.execute('UPDATE boat_profiles SET club=? WHERE id=?',('Ny klub',identity))
        status,_=self.request('/api/boat-profiles',{**boat,'club':'Gammel klub'})
        self.assertEqual(status,400)

    def test_management_requires_administrator(self):
        identity=self.profile_id()
        for path,data in [(f'/api/boat-profiles?id={identity}',None),(f'/api/boat-profiles/delete-preview?id={identity}',None),('/api/boat-profiles',{'id':identity}),('/api/boat-profiles/delete',{'id':identity})]:
            with self.subTest(path=path):
                status,_=self.request(path,data,role='recorder')
                self.assertEqual(status,403)


if __name__=='__main__':unittest.main()
