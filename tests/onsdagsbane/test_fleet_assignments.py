"""Series-wide assignments and explicit historical fleet changes."""
import unittest
import sqlite3
import tempfile
from pathlib import Path
from uuid import UUID
import test_app
from onsdagsbane import hierarchy, service, fleet
from onsdagsbane.store import get
from onsdagsbane.store import Store, SCHEMA


class AssignmentTests(unittest.TestCase):
    setUp = test_app.WebTests.setUp
    race = test_app.WebTests.race
    finish = test_app.WebTests.finish
    planned_heat = test_app.WebTests.planned_heat
    request = test_app.WebTests.request

    def members(self, db, label, name):
        heat=self.planned_heat(db,label,'stor',name)
        return {r[0] for r in db.execute('SELECT boat_id FROM heat_boats WHERE heat_id=?',(heat['id'],))}

    def test_default_whole_series_moves_boats_without_duplicates_and_new_rounds_inherit(self):
        with self.app.store.connect(write=True) as db:
            heat=self.planned_heat(db,'R4','stor','Stor bane 2')
            hierarchy.members(db,heat['id'],{'boat_ids':self.boats},self.user)
            hierarchy.create_rounds(db,self.series,'R19–R20',self.user)
            for label in ('R1','R4','R18','R19','R20'):
                self.assertEqual(self.members(db,label,'Stor bane 2'),set(self.boats))
                self.assertEqual(self.members(db,label,'Stor bane 1'),set())

    def test_forward_backward_and_single_round_ranges(self):
        for scope,expected in [('forward',set(range(4,19))),('backward',set(range(1,5))),('round',{4})]:
            with self.subTest(scope=scope),self.app.store.connect(write=True) as db:
                db.execute('SAVEPOINT scenario')
                heat=self.planned_heat(db,'R4','stor','Stor bane 2')
                hierarchy.members(db,heat['id'],{'boat_ids':[self.boats[0],self.boats[1]],'scope':scope},self.user)
                for index in range(1,19):
                    self.assertEqual(self.boats[0] in self.members(db,f'R{index}','Stor bane 2'),index in expected)
                db.execute('ROLLBACK TO scenario');db.execute('RELEASE scenario')

    def test_historical_move_requires_preview_preserves_observations_and_other_series(self):
        race=self.race()
        for boat in self.boats:self.finish(race,boat)
        with self.app.store.connect(write=True) as db:
            db.execute('UPDATE races SET published=1 WHERE id=?',(race,))
            db.execute('UPDATE rounds SET completed=1 WHERE id=(SELECT round_id FROM round_courses WHERE id=(SELECT round_course_id FROM races WHERE id=?))',(race,))
            before=[tuple(r) for r in db.execute('SELECT * FROM finishes ORDER BY boat_id')]
            snapshots=[tuple(r) for r in db.execute('SELECT race_id,boat_id,start,distance,wind_category,handicap_set_id FROM entries ORDER BY boat_id')]
            other=service.create_series(db,{'name':'Other','year':2027},self.user)
            profile=get(db,'boats',self.boats[0])['profile_id']
            alias=fleet.enroll(db,other,{'profile_ids':[profile],'course':'stor','class_name':'Stor bane 1'},self.user)[0]
            heat=self.planned_heat(db,'R1','stor','Stor bane 2')
            data={'boat_ids':self.boats,'scope':'backward'}
            plan=hierarchy.member_plan(db,heat['id'],data)
            self.assertEqual(len(plan['changes']),1)
            self.assertEqual(plan['changes'][0]['label'],'R1')
            self.assertEqual(plan['changes'][0]['added'],2)
            self.assertTrue(plan['changes'][0]['historical'])
            with self.assertRaisesRegex(ValueError,'Forhåndsvis'):
                hierarchy.members(db,heat['id'],data,self.user)
            hierarchy.members(db,heat['id'],{**data,'token':plan['token']},self.user)
            self.assertEqual(before,[tuple(r) for r in db.execute('SELECT * FROM finishes ORDER BY boat_id')])
            self.assertEqual(snapshots,[tuple(r) for r in db.execute('SELECT race_id,boat_id,start,distance,wind_category,handicap_set_id FROM entries ORDER BY boat_id')])
            self.assertEqual({r[0] for r in db.execute('SELECT class_name FROM entries WHERE race_id=?',(race,))},{'Stor bane 2'})
            self.assertEqual(get(db,'races',race)['published'],0)
            self.assertEqual(get(db,'boats',alias)['class_name'],'Stor bane 1')
            view=service.result_view(db,self.series,'stor','Stor bane 2',drafts=True)
            self.assertEqual(len(view['daily'][race]),3)

    def test_stale_preview_rejected_and_no_partial_changes(self):
        with self.app.store.connect(write=True) as db:
            heat=self.planned_heat(db,'R4','stor','Stor bane 2')
            data={'boat_ids':self.boats,'scope':'forward'}
            plan=hierarchy.member_plan(db,heat['id'],data)
            db.execute("UPDATE rounds SET race_date='2026-06-01' WHERE series_id=?",(self.series,))
            before=[tuple(r) for r in db.execute('SELECT * FROM heat_boats ORDER BY heat_id,boat_id')]
            with self.assertRaisesRegex(ValueError,'Forhåndsvis'):
                hierarchy.members(db,heat['id'],{**data,'token':plan['token']},self.user)
            self.assertEqual(before,[tuple(r) for r in db.execute('SELECT * FROM heat_boats ORDER BY heat_id,boat_id')])

    def test_cross_course_historical_move_and_foreign_boats_are_rejected(self):
        race=self.race();self.finish(race,self.boats[0])
        with self.app.store.connect(write=True) as db:
            heat=self.planned_heat(db,'R1','lille','Lille bane 1')
            with self.assertRaisesRegex(ValueError,'Baneskift'):
                hierarchy.member_plan(db,heat['id'],{'boat_ids':[self.boats[0]],'scope':'round'})
            other=service.create_series(db,{'name':'Other','year':2027},self.user)
            boat=get(db,'boats',self.boats[0])
            alias=fleet.enroll(db,other,{'profile_ids':[boat['profile_id']],'course':'stor','class_name':'Stor bane 1'},self.user)[0]
            with self.assertRaisesRegex(ValueError,'denne serie'):
                hierarchy.member_plan(db,heat['id'],{'boat_ids':[alias]})
            self.assertEqual(get(db,'boats',alias)['profile_id'],boat['profile_id'])
            self.assertEqual(db.execute('SELECT count(*) FROM entries e JOIN races r ON r.id=e.race_id WHERE r.series_id=?',(other,)).fetchone()[0],0)

    def test_preview_api_requires_administrator(self):
        with self.app.store.connect() as db:heat=self.planned_heat(db,'R4','stor','Stor bane 2')
        data={'heat_id':heat['id'],'boat_ids':self.boats,'scope':'series'}
        status,_=self.request('/api/heats/members/preview',data,role='recorder')
        self.assertEqual(status,403)
        status,preview=self.request('/api/heats/members/preview',data)
        self.assertEqual(status,200,preview)
        self.assertEqual(len(preview['changes']),18)
        status,result=self.request('/api/heats/members',{**data,'token':preview['token']})
        self.assertEqual(status,200,result)

    def test_commander_reuses_boat_only_in_chosen_round_and_preview_has_no_writes(self):
        with self.app.store.connect(write=True) as db:
            target=service.create_series(db,{'name':'Commander','year':2027,'round_labels':'R1–R3'},self.user)
            heat=db.execute("SELECT h.id FROM heats h JOIN round_courses c ON c.id=h.round_course_id JOIN rounds r ON r.id=c.round_id WHERE r.series_id=? AND r.label='R2' AND c.course='stor' AND h.name='Stor bane 2'",(target,)).fetchone()[0]
            profile=get(db,'boats',self.boats[0])['profile_id']
            before_audit=db.execute('SELECT count(*) FROM audit').fetchone()[0]
            data={'heat_id':heat,'profile_ids':[profile],'scope':'round'}
            preview=hierarchy.preview_profile_members(db,heat,data,self.user)
            self.assertEqual(preview['reused'],1)
            self.assertEqual(db.execute('SELECT count(*) FROM boats WHERE series_id=?',(target,)).fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM audit').fetchone()[0],before_audit)
        status,result=self.request('/api/heats/boats',{**data,'token':preview['token']})
        self.assertEqual(status,200,result)
        with self.app.store.connect() as db:
            registration=db.execute('SELECT * FROM boats WHERE series_id=?',(target,)).fetchone()
            self.assertEqual(registration['profile_id'],profile)
            memberships=db.execute('SELECT h.id FROM heat_boats m JOIN heats h ON h.id=m.heat_id WHERE m.boat_id=?',(registration['id'],)).fetchall()
            self.assertEqual([r[0] for r in memberships],[heat])
            self.assertEqual(db.execute('SELECT count(*) FROM boat_profiles').fetchone()[0],3)
            self.assertEqual(get(db,'boats',self.boats[0])['class_name'],'Stor bane 1')

    def test_commander_stale_save_rolls_back_new_registration(self):
        with self.app.store.connect(write=True) as db:
            target=service.create_series(db,{'name':'Commander','year':2027},self.user)
            heat=db.execute('SELECT h.id FROM heats h JOIN round_courses c ON c.id=h.round_course_id JOIN rounds r ON r.id=c.round_id WHERE r.series_id=? LIMIT 1',(target,)).fetchone()[0]
            profile=get(db,'boats',self.boats[0])['profile_id']
        data={'heat_id':heat,'profile_ids':[profile],'scope':'series'}
        status,preview=self.request('/api/heats/boats/preview',data)
        self.assertEqual(status,200,preview)
        with self.app.store.connect(write=True) as db:
            db.execute("UPDATE rounds SET race_date='2027-05-01' WHERE series_id=?",(target,))
        status,result=self.request('/api/heats/boats',{**data,'token':preview['token']})
        self.assertEqual(status,400,result)
        with self.app.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM boats WHERE series_id=?',(target,)).fetchone()[0],0)
        status,_=self.request('/api/heats/boats/preview',data,role='recorder')
        self.assertEqual(status,403)

    def test_commander_removes_unstarted_members_but_retains_series_registration(self):
        with self.app.store.connect() as db:
            heat=self.planned_heat(db,'R2','stor','Stor bane 1')
            profiles=[get(db,'boats',self.boats[0])['profile_id']]
        data={'heat_id':heat['id'],'profile_ids':profiles,'scope':'round'}
        status,preview=self.request('/api/heats/boats/preview',data)
        self.assertEqual(status,200,preview)
        self.assertEqual(preview['changes'][0]['removed'],1)
        status,result=self.request('/api/heats/boats',{**data,'token':preview['token']})
        self.assertEqual(status,200,result)
        with self.app.store.connect() as db:
            self.assertEqual(self.members(db,'R2','Stor bane 1'),{self.boats[0]})
            self.assertEqual(self.members(db,'R1','Stor bane 1'),{self.boats[0],self.boats[2]})
            self.assertEqual(db.execute('SELECT count(*) FROM boats').fetchone()[0],3)

    def test_arrow_saves_without_token_and_preserves_other_round_members(self):
        with self.app.store.connect(write=True) as db:
            heat=self.planned_heat(db,'R2','stor','Stor bane 2')
            hierarchy.members(db,heat['id'],{'scope':'round','boat_ids':[self.boats[1],self.boats[2]]},self.user)
            profile=get(db,'boats',self.boats[0])['profile_id']
        data={'heat_id':heat['id'],'scope':'series','action':'add','profile_ids':[profile],'include_assigned':True}
        status,result=self.request('/api/heats/boats/move',data)
        self.assertEqual(status,200,result)
        self.assertEqual(len(result['changes']),18)
        with self.app.store.connect() as db:
            self.assertEqual(self.members(db,'R1','Stor bane 2'),{self.boats[0],self.boats[1]})
            self.assertEqual(self.members(db,'R2','Stor bane 2'),set(self.boats))
        # A retry after a lost response is harmless and reports the saved state.
        status,repeated=self.request('/api/heats/boats/move',data)
        self.assertEqual(status,200,repeated)
        self.assertEqual(repeated['changes'],[])
        self.assertEqual(repeated['data'],result['data'])
        status,_=self.request('/api/heats/boats/move',data,role='recorder')
        self.assertEqual(status,403)

    def test_arrow_failure_rolls_back_all_rounds(self):
        race=self.race(label='R2');self.finish(race,self.boats[1])
        with self.app.store.connect() as db:
            heat=self.planned_heat(db,'R1','lille','Lille bane 1')
            profile=get(db,'boats',self.boats[1])['profile_id']
            before=[tuple(r) for r in db.execute('SELECT * FROM heat_boats ORDER BY heat_id,boat_id')]
            finishes=[tuple(r) for r in db.execute('SELECT * FROM finishes')]
        status,result=self.request('/api/heats/boats/move',{'heat_id':heat['id'],'scope':'series','action':'add','profile_ids':[profile],'include_assigned':True})
        self.assertEqual(status,400,result)
        with self.app.store.connect() as db:
            self.assertEqual(before,[tuple(r) for r in db.execute('SELECT * FROM heat_boats ORDER BY heat_id,boat_id')])
            self.assertEqual(finishes,[tuple(r) for r in db.execute('SELECT * FROM finishes')])

    def test_arrow_historical_move_saves_immediately_and_preserves_times(self):
        race=self.race()
        for boat in self.boats:self.finish(race,boat)
        with self.app.store.connect(write=True) as db:
            db.execute('UPDATE races SET published=1 WHERE id=?',(race,))
            heat=self.planned_heat(db,'R1','stor','Stor bane 2')
            profile=get(db,'boats',self.boats[0])['profile_id']
            finishes=[tuple(r) for r in db.execute('SELECT * FROM finishes ORDER BY boat_id')]
            snapshots=[tuple(r) for r in db.execute('SELECT race_id,boat_id,start,distance,wind_category,handicap_set_id FROM entries ORDER BY boat_id')]
        status,result=self.request('/api/heats/boats/move',{'heat_id':heat['id'],'scope':'round','action':'add','profile_ids':[profile],'include_assigned':True})
        self.assertEqual(status,200,result)
        self.assertTrue(result['changes'][0]['historical'])
        with self.app.store.connect() as db:
            self.assertEqual(get(db,'races',race)['published'],0)
            self.assertEqual(finishes,[tuple(r) for r in db.execute('SELECT * FROM finishes ORDER BY boat_id')])
            self.assertEqual(snapshots,[tuple(r) for r in db.execute('SELECT race_id,boat_id,start,distance,wind_category,handicap_set_id FROM entries ORDER BY boat_id')])
            self.assertEqual(db.execute('SELECT class_name FROM entries WHERE race_id=? AND boat_id=?',(race,self.boats[0])).fetchone()[0],'Stor bane 2')

    def test_arrow_reuses_boat_and_saves_only_the_selected_round(self):
        with self.app.store.connect(write=True) as db:
            target=service.create_series(db,{'name':'Autosave','year':2027,'round_labels':'R1–R3'},self.user)
            heat=db.execute("SELECT h.id FROM heats h JOIN round_courses c ON c.id=h.round_course_id JOIN rounds r ON r.id=c.round_id WHERE r.series_id=? AND r.label='R2' AND c.course='stor' LIMIT 1",(target,)).fetchone()[0]
            profile=get(db,'boats',self.boats[0])['profile_id']
        data={'heat_id':heat,'scope':'round','action':'add','profile_ids':[profile]}
        status,result=self.request('/api/heats/boats/move',data)
        self.assertEqual(status,200,result)
        self.assertEqual(result['reused'],1)
        self.assertEqual([c['label'] for c in result['changes']],['R2'])
        with self.app.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM boat_profiles').fetchone()[0],3)
        status,result=self.request('/api/heats/boats/move',{**data,'action':'remove'})
        self.assertEqual(status,200,result)
        self.assertEqual([c['label'] for c in result['changes']],['R2'])
        self.assertEqual(sum(len(h['boat_ids']) for r in result['data']['rounds'] for c in r['courses'] for h in c['heats']),0)

    def test_already_assigned_boat_requires_explicit_move_option(self):
        with self.app.store.connect() as db:
            heat=self.planned_heat(db,'R1','stor','Stor bane 2')
            profile=get(db,'boats',self.boats[0])['profile_id']
        data={'heat_id':heat['id'],'scope':'series','action':'add','profile_ids':[profile]}
        status,result=self.request('/api/heats/boats/move',data)
        self.assertEqual(status,400,result)
        self.assertIn('Medtag allerede fordelte både',result['error'])
        with self.app.store.connect() as db:
            self.assertEqual(self.members(db,'R1','Stor bane 1'),{self.boats[0],self.boats[2]})
        status,result=self.request('/api/heats/boats/move',{**data,'include_assigned':True})
        self.assertEqual(status,200,result)
        with self.app.store.connect() as db:
            for label in ('R1','R18'):
                self.assertEqual(self.members(db,label,'Stor bane 1'),{self.boats[2]})
                self.assertEqual(self.members(db,label,'Stor bane 2'),{self.boats[0],self.boats[1]})

    def test_single_dateless_assignment_blocks_series_but_not_other_round_ids(self):
        with self.app.store.connect(write=True) as db:
            profile=get(db,'boats',self.boats[0])['profile_id']
            assigned=self.planned_heat(db,'R2','stor','Stor bane 1')
            db.execute('DELETE FROM heat_boats WHERE boat_id=? AND heat_id<>?',(self.boats[0],assigned['id']))
            target=self.planned_heat(db,'R1','stor','Stor bane 2')
            self.assertEqual(db.execute('SELECT count(*) FROM rounds WHERE race_date IS NOT NULL').fetchone()[0],0)
        data={'heat_id':target['id'],'scope':'series','action':'add','profile_ids':[profile]}
        status,_=self.request('/api/heats/boats/move',data)
        self.assertEqual(status,400)
        status,result=self.request('/api/heats/boats/move',{**data,'scope':'round'})
        self.assertEqual(status,200,result)
        with self.app.store.connect() as db:
            self.assertIn(self.boats[0],self.members(db,'R2','Stor bane 1'))
            self.assertIn(self.boats[0],self.members(db,'R1','Stor bane 2'))

    def test_catalog_exposes_assignment_ids_and_same_label_is_not_shared_identity(self):
        with self.app.store.connect(write=True) as db:
            other=service.create_series(db,{'name':'Other','year':2026,'round_labels':'R1–R3'},self.user)
            target=db.execute("SELECT h.id FROM heats h JOIN round_courses c ON c.id=h.round_course_id JOIN rounds r ON r.id=c.round_id WHERE r.series_id=? AND r.label='R1' AND c.course='stor' LIMIT 1",(other,)).fetchone()[0]
            profile=get(db,'boats',self.boats[0])['profile_id']
            original=next(b for b in fleet.catalog(db,self.series) if b['id']==profile)
            self.assertEqual(len(original['assignments']),18)
            self.assertEqual({a['series_id'] for a in original['assignments']},{self.series})
        status,result=self.request('/api/heats/boats/move',{'heat_id':target,'scope':'series','action':'add','profile_ids':[profile]})
        self.assertEqual(status,200,result)
        catalog_boat=next(b for b in result['catalog'] if b['id']==profile)
        first={a['round_id'] for a in catalog_boat['assignments'] if a['series_id']==self.series}
        second={a['round_id'] for a in catalog_boat['assignments'] if a['series_id']==other}
        self.assertFalse(first&second)
        self.assertEqual(len(second),3)

    def test_database_prevents_two_heats_in_one_round(self):
        with self.app.store.connect(write=True) as db:
            target=self.planned_heat(db,'R1','stor','Stor bane 2')
            with self.assertRaisesRegex(sqlite3.IntegrityError,'ét løb'):
                db.execute('INSERT INTO heat_boats(heat_id,boat_id) VALUES(?,?)',(target['id'],self.boats[0]))
            with self.assertRaisesRegex(sqlite3.IntegrityError,'ét løb'):
                db.execute('UPDATE heat_boats SET boat_id=? WHERE heat_id=? AND boat_id=?',(self.boats[0],target['id'],self.boats[1]))
            self.assertEqual(self.members(db,'R1','Stor bane 2'),{self.boats[1]})

    def test_legacy_round_gets_permanent_backend_uid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'legacy.sqlite3'
            with sqlite3.connect(path) as db:
                db.executescript(SCHEMA.replace('uid TEXT NOT NULL UNIQUE, ',''))
                db.execute("INSERT INTO series(id,name,year) VALUES(1,'Legacy',2026)")
                db.execute("INSERT INTO rounds(id,series_id,label,position) VALUES(1,1,'R1',1)")
            db.close()
            store=Store(path)
            with store.connect(write=True) as db:
                uid=db.execute('SELECT uid FROM rounds WHERE id=1').fetchone()[0]
                self.assertEqual(UUID(uid).version,4)
                db.execute("UPDATE rounds SET label='New label',race_date='2026-06-01' WHERE id=1")
                with self.assertRaisesRegex(sqlite3.IntegrityError,'UID'):
                    db.execute('UPDATE rounds SET uid=? WHERE id=1',(str(UUID(int=1)),))
            Store(path)
            with store.connect() as db:
                self.assertEqual(db.execute('SELECT uid FROM rounds WHERE id=1').fetchone()[0],uid)


if __name__=='__main__':unittest.main()
