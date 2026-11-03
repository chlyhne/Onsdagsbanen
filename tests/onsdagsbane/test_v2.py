import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import test_app
from onsdagsbane import service, fleet
from onsdagsbane.store import SCHEMA, Store
from onsdagsbane.migrations import bootstrap_v1


class WorkspaceTests(unittest.TestCase):
    setUp=test_app.WebTests.setUp
    request=test_app.WebTests.request
    race=test_app.WebTests.race
    finish=test_app.WebTests.finish

    def test_workspace_separates_phase_and_publication(self):
        status,w=self.request(f'/api/workspaces/series/{self.series}')
        self.assertEqual(status,200,w)
        self.assertEqual(w['rounds'][0]['status']['phase'],'planned')
        race=self.race()
        status,w=self.request(f'/api/workspaces/series/{self.series}')
        self.assertEqual(w['rounds'][0]['status']['phase'],'ready')
        self.finish(race,self.boats[0])
        status,w=self.request(f'/api/workspaces/rounds/{w["rounds"][0]["uid"]}')
        self.assertEqual(w['status']['phase'],'live')
        self.assertFalse(w['status']['published'])
        with self.app.store.connect(write=True) as db:db.execute('UPDATE rounds SET completed=1 WHERE id=?',(w['round']['id'],))
        status,w=self.request(f'/api/workspaces/rounds/{w["round"]["uid"]}/history')
        self.assertEqual(w['status']['phase'],'finished')
        self.assertFalse(w['status']['published'])
        self.assertTrue(w['history'])

    def test_global_boat_creation_does_not_register_for_series(self):
        status,result=self.request('/api/boat-profiles',{'name':'Ægir','skipper':'Søren','sail_number':'DEN 42','boat_type':'X-332','ratings':{'TACIM':'600,1'}})
        self.assertEqual(status,200,result)
        with self.app.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM boats WHERE profile_id=?',(result['id'],)).fetchone()[0],0)
            self.assertEqual(fleet.profile(db,result['id'])['ratings']['TACIM'],600.1)

    def test_unregister_only_empty_series_registration_preserves_global_boat(self):
        registration=self.boats[0]
        status,_=self.request(f'/api/series/registrations/{registration}/remove',{})
        self.assertEqual(status,400)
        with self.app.store.connect(write=True) as db:
            profile=db.execute('SELECT profile_id FROM boats WHERE id=?',(registration,)).fetchone()[0]
            db.execute('DELETE FROM heat_boats WHERE boat_id=?',(registration,))
        status,result=self.request(f'/api/series/registrations/{registration}/remove',{})
        self.assertEqual(status,200,result)
        with self.app.store.connect() as db:
            self.assertIsNotNone(fleet.profile(db,profile))
            self.assertEqual(db.execute('SELECT count(*) FROM boats WHERE id=?',(registration,)).fetchone()[0],0)

    def test_deep_links_and_module_resources_are_served(self):
        for path in ('/series/1/assignments','/boats','/rounds/a-uid/setup','/core/state.js','/features/timing.js'):
            status,body=self.request(path)
            self.assertEqual(status,200,path)
        status,_=self.request('/../auth.py.js')
        self.assertEqual(status,404)

    def test_correction_uses_round_uid_even_when_rounds_share_date(self):
        first=self.race(label='R1')
        second=self.race(label='R2')
        with self.app.store.connect(write=True) as db:
            uid=db.execute('SELECT p.uid FROM rounds p JOIN round_courses c ON c.round_id=p.id JOIN races r ON r.round_course_id=c.id WHERE r.id=?',(first,)).fetchone()[0]
            data={'series_id':self.series,'round_uid':uid,'date':'2026-05-06','course':'stor','wind_category':'L'}
            preview=service.day_settings(db,data,self.user)
            self.assertEqual([c['race_id'] for c in preview['changes']],[first])
            service.day_settings(db,{**data,'apply':True,'token':preview['token']},self.user)
            self.assertEqual(db.execute('SELECT rating_key FROM races WHERE id=?',(first,)).fetchone()[0],'TACIL')
            self.assertEqual(db.execute('SELECT rating_key FROM races WHERE id=?',(second,)).fetchone()[0],'TACIM')


class V1MigrationTests(unittest.TestCase):
    def test_real_v1_tables_become_one_participation_store_without_result_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'v1.sqlite3'
            db=sqlite3.connect(path);db.row_factory=sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON');bootstrap_v1(db)
            user={'id':None}
            series=service.create_series(db,{'name':'Legacy','year':2026,'round_labels':'R1–R3'},user)
            boat=service.save_boat(db,{'series_id':series,'class_name':'Stor bane 1','course':'stor','name':'Ægir','skipper':'Søren','sail_number':'DEN 42','boat_type':'X-332','ratings':{'TACIM':600}},user)
            # A v1 fixture predates the staging tables for unconfigured timing.
            with patch('onsdagsbane.unconfigured.apply'):
                race=service.create_race(db,{'series_id':series,'course':'stor','label':'R1','mode':'normal','rating_key':'TACIM','start':'2026-05-06T18:30:00+02:00','route':['A','D']},user)
            service.finish(db,race,{'boat_id':boat,'instant':'2026-05-06T19:30:00+02:00','request_id':'test-v1-request-1234'},user)
            before={t:[tuple(r) for r in db.execute(f'SELECT * FROM {t} ORDER BY 1,2')] for t in ('entries','finishes','rounds','handicap_sets','heat_boats')}
            db.execute('PRAGMA user_version=1');db.commit();db.close()
            store=Store(path)
            with store.connect() as db:
                for table,rows in before.items():self.assertEqual(rows,[tuple(r) for r in db.execute(f'SELECT * FROM {table} ORDER BY 1,2')],table)
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],5)
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
                self.assertEqual(db.execute('SELECT count(*) FROM participations WHERE race_id=?',(race,)).fetchone()[0],1)
            Store(path)
