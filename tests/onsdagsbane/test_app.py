"""Behaviour tests for scoring, saved snapshots, permissions and input safety."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import secrets
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from onsdagsbane.app import Application
from onsdagsbane import auth, service, hierarchy, calculations, timing, handicaps, fleet, planning
from onsdagsbane.domain import correction, daily, number, overall, gph
from onsdagsbane.store import get
from onsdagsbane.redress import apply_redress


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Application(db_path=Path(self.temp.name)/'test.sqlite3',initialize=False)
        with self.app.store.connect(write=True) as db:
            self.user = {'id':1,'provider':'google','subject':'owner','email':auth.OWNER,'name':'Owner','role':'admin','approved':1}
            db.execute('INSERT INTO users(id,provider,subject,email,name,role,approved) VALUES(?,?,?,?,?,?,?)',tuple(self.user.values()))
            self.series = service.create_series(db,{'name':'Test','year':2026,'discards':[3],'reference':'1000,0'},self.user)
            self.boats=[]
            for i in range(3):
                self.boats.append(service.save_boat(db,{'series_id':self.series,'class_name':f'Stor bane {1+i%2}','course':'stor','name':f'Boat {i}','skipper':f'Skipper {i}','sail_number':f'DEN {100+i}','boat_type':'X-332','ratings':{'TACIL':700+i*20,'TACIM':600+i*20},'extra_handicap':20 if i==0 else 0},self.user))

    def race(self,mode='normal',label='R1',key='TACIM'):
        with self.app.store.connect(write=True) as db:
            return service.create_race(db,{'series_id':self.series,'course':'stor','mode':mode,'rating_key':key,'label':label,'start':'2026-05-06T18:30:00+02:00','route':['A','D'],'boat_ids':self.boats},self.user)

    def finish(self,race,boat,stamp='2026-05-06T19:30:00+02:00',**extra):
        with self.app.store.connect(write=True) as db:
            return service.finish(db,race,{'boat_id':boat,'instant':stamp,'request_id':secrets.token_hex(16),**extra},self.user)

    def request(self,path,data=None,role='admin',approved=True,provider='google',csrf=True,origin=None):
        token=secrets.token_hex(32)
        with self.app.store.connect(write=True) as db:
            db.execute('UPDATE users SET role=?,approved=?,provider=? WHERE id=1',(role,int(approved),provider))
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(auth.digest(token),1,'test-csrf',time.time()+1000))
        path,_,query=path.partition('?')
        raw=json.dumps(data).encode() if data is not None else b''
        env={'REQUEST_METHOD':'POST' if data is not None else 'GET','PATH_INFO':path,'QUERY_STRING':query,'wsgi.input':io.BytesIO(raw),'CONTENT_LENGTH':str(len(raw)),'CONTENT_TYPE':'application/json','HTTP_COOKIE':'ob_session='+token,'HTTP_X_CSRF_TOKEN':'test-csrf' if csrf else ''}
        if origin:env['HTTP_ORIGIN']=origin
        status=[]
        body=b''.join(self.app(env,lambda s,h:status.append(int(s[:3]))))
        try:body=json.loads(body)
        except (ValueError,UnicodeDecodeError):pass
        return status[0],body

    def test_series_calendar_contains_rounds_courses_and_fleets_before_racing(self):
        status,created=self.request('/api/series',{'name':'Calendar','year':2027,'round_labels':'R1–R3','fleets':{'stor':['Hurtige','Familie'],'lille':['Fælles']}})
        self.assertEqual(status,200,created)
        with self.app.store.connect() as db:
            plan=hierarchy.structure(db,created['id'])
            self.assertEqual([r['label'] for r in plan],['R1','R2','R3'])
            for item in plan:
                self.assertEqual({c['course'] for c in item['courses']},{'stor','lille'})
                self.assertEqual(sum(len(c['heats']) for c in item['courses']),3)
                self.assertTrue(all(c['race_id'] is None for c in item['courses']))
                self.assertTrue(all(not h['boat_ids'] for c in item['courses'] for h in c['heats']))
            self.assertEqual(db.execute('SELECT count(*) FROM races WHERE series_id=?',(created['id'],)).fetchone()[0],0)
            self.assertEqual(service.result_view(db,created['id'],'stor')['races'],[])
        status,state=self.request(f"/api/state?series_id={created['id']}")
        self.assertEqual(status,200,state)
        self.assertEqual(len(state['rounds']),3)

    def test_round_and_fleet_input_validation_and_admin_permissions(self):
        for value in ('R18-R1','R0-R18','R1-R201',['R1','r1'],[],['']):
            with self.subTest(value=value),self.assertRaises(ValueError):hierarchy.labels(value)
        status,_=self.request('/api/rounds',{'series_id':self.series,'round_labels':'R19-R20'},role='recorder')
        self.assertEqual(status,403)
        with self.app.store.connect() as db:
            heat=hierarchy.structure(db,self.series)[0]['courses'][0]['heats'][0]
        status,_=self.request('/api/heats/members',{'heat_id':heat['id'],'boat_ids':[]},role='recorder')
        self.assertEqual(status,403)

    def planned_heat(self,db,label,course,name):
        return db.execute('SELECT h.* FROM heats h JOIN round_courses c ON c.id=h.round_course_id JOIN rounds p ON p.id=c.round_id WHERE p.series_id=? AND p.label=? AND c.course=? AND h.name=?',(self.series,label,course,name)).fetchone()

    def test_boat_switches_fleet_across_rounds_but_is_one_combined_entrant(self):
        first=self.race()
        for boat in self.boats:self.finish(first,boat)
        with self.app.store.connect(write=True) as db:
            db.execute('UPDATE races SET published=1 WHERE id=?',(first,))
            old=self.planned_heat(db,'R2','stor','Stor bane 1')
            new=self.planned_heat(db,'R2','stor','Stor bane 2')
            hierarchy.members(db,old['id'],{'boat_ids':[self.boats[2]],'scope':'round'},self.user)
            hierarchy.members(db,new['id'],{'boat_ids':[self.boats[0],self.boats[1]],'scope':'round'},self.user)
            planned=db.execute('SELECT id FROM rounds WHERE series_id=? AND label=?',(self.series,'R2')).fetchone()[0]
            second=service.create_race(db,{'series_id':self.series,'round_id':planned,'course':'stor','mode':'normal','rating_key':'TACIM','start':'2026-05-13T18:30:00+02:00','route':['A','D']},self.user)
        for boat in self.boats:self.finish(second,boat,stamp='2026-05-13T19:30:00+02:00')
        with self.app.store.connect(write=True) as db:
            db.execute('UPDATE races SET published=1 WHERE id=?',(second,))
            view=service.result_view(db,self.series,'stor')
            self.assertEqual(len(view['overall']),3)
            self.assertEqual(len([r for r in view['overall'] if r['id']==self.boats[0]]),1)
            for race in (first,second):
                self.assertEqual(len(view['daily'][race]),3)
                self.assertEqual(len({r['boat_id'] for r in view['daily'][race]}),3)
            self.assertEqual(next(r for r in view['daily'][first] if r['boat_id']==self.boats[0])['class_name'],'Stor bane 1')
            self.assertEqual(next(r for r in view['daily'][second] if r['boat_id']==self.boats[0])['class_name'],'Stor bane 2')
            self.assertEqual(len(service.result_view(db,self.series,'stor','Stor bane 1')['overall']),2)
            self.assertEqual(len(service.result_view(db,self.series,'stor','Stor bane 2')['overall']),2)

    def test_boat_on_different_courses_has_independent_course_standings(self):
        first=self.race()
        for boat in self.boats:self.finish(first,boat)
        with self.app.store.connect(write=True) as db:
            db.execute('UPDATE races SET published=1 WHERE id=?',(first,))
            old=self.planned_heat(db,'R2','stor','Stor bane 1')
            new=self.planned_heat(db,'R2','lille','Lille bane 1')
            hierarchy.members(db,old['id'],{'boat_ids':[self.boats[2]],'scope':'round'},self.user)
            hierarchy.members(db,new['id'],{'boat_ids':[self.boats[0]],'scope':'round'},self.user)
            planned=db.execute('SELECT id FROM rounds WHERE series_id=? AND label=?',(self.series,'R2')).fetchone()[0]
            second=service.create_race(db,{'series_id':self.series,'round_id':planned,'course':'lille','mode':'normal','rating_key':'TACIM','start':'2026-05-13T18:30:00+02:00','route':['A']},self.user)
        self.finish(second,self.boats[0],stamp='2026-05-13T19:00:00+02:00')
        with self.app.store.connect(write=True) as db:
            db.execute('UPDATE races SET published=1 WHERE id=?',(second,))
            stor=service.result_view(db,self.series,'stor')
            lille=service.result_view(db,self.series,'lille')
            self.assertEqual(len(stor['overall']),3)
            self.assertEqual(len(lille['overall']),1)
            self.assertEqual(lille['overall'][0]['id'],self.boats[0])
            self.assertEqual(lille['overall'][0]['course'],'lille')
            self.assertEqual(lille['overall'][0]['class_name'],'Lille bane 1')
            self.assertEqual(stor['overall'][0]['scores'][0]['race_id'],first)
            self.assertEqual(lille['overall'][0]['scores'][0]['race_id'],second)
            self.assertEqual(lille['overall'][0]['total'],1)
            self.assertEqual(len(db.execute('SELECT id FROM boats WHERE id=?',(self.boats[0],)).fetchall()),1)

    def test_roster_edit_preserves_handicap_snapshots(self):
        race=self.race()
        with self.app.store.connect(write=True) as db:
            before=next(r for r in service.rows(db,race) if r['boat_id']==self.boats[0])
            boat=next(b for b in service.boats(db,self.series) if b['id']==self.boats[0])
            service.save_boat(db,{**boat,'ratings':{**boat['ratings'],'TACIM':900},'extra_handicap':99},self.user,boat['id'])
            heat=self.planned_heat(db,'R1','stor','Stor bane 1')
            hierarchy.members(db,heat['id'],{'boat_ids':[self.boats[0]],'scope':'round'},self.user)
            after=next(r for r in service.rows(db,race) if r['boat_id']==self.boats[0])
            self.assertEqual(after['rating'],before['rating'])
            self.assertEqual(after['extra_handicap'],before['extra_handicap'])
            self.assertNotIn(self.boats[2],[r['boat_id'] for r in service.rows(db,race)])

    def test_canonical_participations_keep_results_on_reopening(self):
        race=self.race()
        self.finish(race,self.boats[0])
        with self.app.store.connect(write=True) as db:
            before=[tuple(r) for r in db.execute('SELECT * FROM finishes ORDER BY race_id,boat_id')]
            entries=[tuple(r) for r in db.execute('SELECT * FROM entries ORDER BY race_id,boat_id')]
            plan=hierarchy.structure(db,self.series)
            self.assertEqual(db.execute("SELECT type FROM sqlite_master WHERE name='entries'").fetchone()[0],'view')
            self.assertEqual(db.execute("SELECT type FROM sqlite_master WHERE name='heat_boats'").fetchone()[0],'view')
            self.assertEqual(db.execute('SELECT count(*) FROM participations WHERE race_id=?',(race,)).fetchone()[0],3)
        Application(db_path=self.app.store.path,initialize=False)
        with self.app.store.connect() as db:
            self.assertEqual(before,[tuple(r) for r in db.execute('SELECT * FROM finishes ORDER BY race_id,boat_id')])
            self.assertEqual(entries,[tuple(r) for r in db.execute('SELECT * FROM entries ORDER BY race_id,boat_id')])
            self.assertEqual(plan,hierarchy.structure(db,self.series))
            self.assertEqual({c['course'] for c in plan[0]['courses']},{'stor','lille'})
            self.assertEqual(get(db,'races',race)['round_course_id'],next(c['id'] for c in plan[0]['courses'] if c['course']=='stor'))

    def test_historical_recalculation_uses_saved_participants_not_current_boats(self):
        first=self.race()
        for boat in self.boats:self.finish(first,boat)
        second=self.race(label='R2')
        for boat in self.boats:self.finish(second,boat)
        with self.app.store.connect(write=True) as db:
            db.execute('UPDATE races SET published=1')
            old_entries=service.rows(db,first)
            later=[tuple(r) for r in db.execute('SELECT * FROM finishes WHERE race_id=? ORDER BY boat_id',(second,))]
            boat=next(b for b in service.boats(db,self.series) if b['id']==self.boats[0])
            service.save_boat(db,{**boat,'course':'lille','class_name':'Lille bane 1','ratings':{'TACIM':999,'TACIL':1001},'extra_handicap':99},self.user,boat['id'])
            data={'boat_id':boat['id'],'ratings':{'TACIM':610,'TACIL':710},'extra_handicap':20,'note':'Fejl i det gamle målebrev'}
            preview=calculations.recalculate(db,first,data,self.user)
            self.assertEqual(service.rows(db,first),old_entries)
            calculations.recalculate(db,first,{**data,'token':preview['token']},self.user,apply=True)
            after=service.rows(db,first)
            target=next(r for r in after if r['boat_id']==boat['id'])
            self.assertEqual(target['rating'],610)
            self.assertEqual(target['class_name'],'Stor bane 1')
            self.assertEqual(target['course'],'stor')
            self.assertEqual(target['handicap_set']['ratings']['TACIL'],710)
            for before in old_entries:
                row=next(r for r in after if r['boat_id']==before['boat_id'])
                self.assertEqual(row['instant'],before['instant'])
                self.assertEqual(row['start'],before['start'])
                if row['boat_id']!=boat['id']:self.assertEqual(row,before)
            self.assertEqual(later,[tuple(r) for r in db.execute('SELECT * FROM finishes WHERE race_id=? ORDER BY boat_id',(second,))])
            self.assertEqual(json.loads(get(db,'boats',boat['id'])['ratings'])['TACIM'],999)
            self.assertEqual(get(db,'boats',boat['id'])['extra_handicap'],99)
            self.assertEqual(get(db,'races',first)['published'],0)
            self.assertEqual(get(db,'races',second)['published'],1)
            self.assertTrue(db.execute("SELECT 1 FROM audit WHERE action='race.recalculate'").fetchone())

    def test_historical_wind_change_uses_original_six_ratings(self):
        race=self.race()
        self.finish(race,self.boats[0])
        with self.app.store.connect(write=True) as db:
            boat=next(b for b in service.boats(db,self.series) if b['id']==self.boats[0])
            service.save_boat(db,{**boat,'ratings':{'TACIM':999,'TACIL':1001}},self.user,boat['id'])
            preview=service.day_settings(db,{'series_id':self.series,'date':'2026-05-06','course':'stor','wind_category':'L'},self.user)
            first=next(c for c in preview['changes'][0]['updates'] if c['boat_id']==boat['id'])
            self.assertEqual(first['rating'],700)
            # Legacy imported observations only know the rating actually used that day.
            set_id=handicaps.for_values(db,boat['profile_id'],{'TACIM':600},boat['extra_handicap'])
            db.execute('UPDATE entries SET handicap_set_id=? WHERE race_id=? AND boat_id=?',(set_id,race,boat['id']))
            with self.assertRaisesRegex(ValueError,'historisk TACIL'):
                service.day_settings(db,{'series_id':self.series,'date':'2026-05-06','course':'stor','wind_category':'L'},self.user)

    def test_historical_correction_requires_preview_reason_and_admin(self):
        race=self.race();self.finish(race,self.boats[0])
        with self.app.store.connect(write=True) as db:
            with self.assertRaisesRegex(ValueError,'årsagen'):
                calculations.recalculate(db,race,{},self.user)
            with self.assertRaisesRegex(ValueError,'Forhåndsvis'):
                calculations.recalculate(db,race,{'note':'Rettelse','token':'bad'},self.user,apply=True)
            preview=calculations.recalculate(db,race,{'note':'Rettelse'},self.user)
            db.execute('UPDATE races SET revision=revision+1 WHERE id=?',(race,))
            with self.assertRaisesRegex(ValueError,'Forhåndsvis'):
                calculations.recalculate(db,race,{'note':'Rettelse','token':preview['token']},self.user,apply=True)
        status,_=self.request(f'/api/races/{race}/calculation-preview',{'note':'Rettelse'},role='recorder')
        self.assertEqual(status,403)

    def test_explicit_master_recalculation_can_target_one_old_race(self):
        first=self.race();second=self.race(label='R2')
        self.finish(first,self.boats[0]);self.finish(second,self.boats[0])
        with self.app.store.connect(write=True) as db:
            boat=next(b for b in service.boats(db,self.series) if b['id']==self.boats[0])
            data={**boat,'ratings':{**boat['ratings'],'TACIM':800},'recalculate':True,'race_ids':[first]}
            preview=service.recalculate_boat(db,boat['id'],data['ratings'],20,data,self.user)
            self.assertEqual([r['race_id'] for r in preview['changes']],[first])
            service.save_boat(db,{**data,'recalculation_token':preview['token']},self.user,boat['id'])
            self.assertEqual(next(r for r in service.rows(db,first) if r['boat_id']==boat['id'])['rating'],800)
            self.assertEqual(next(r for r in service.rows(db,second) if r['boat_id']==boat['id'])['rating'],600)

    def test_public_defaults_to_latest_series_with_results_not_unstarted_plan(self):
        race=self.race();self.finish(race,self.boats[0])
        with self.app.store.connect(write=True) as db:
            db.execute('UPDATE races SET published=1 WHERE id=?',(race,))
            service.create_series(db,{'name':'Next year','year':2027},self.user)
        status=[]
        result=json.loads(b''.join(self.app({'PATH_INFO':'/api/state','REQUEST_METHOD':'GET'},lambda s,h:status.append(s))))
        self.assertTrue(status[0].startswith('200'))
        self.assertEqual(result['series_id'],self.series)

    def test_migration_adds_calendar_to_previously_empty_series(self):
        with self.app.store.connect(write=True) as db:
            identity=db.execute("INSERT INTO series(name,year) VALUES('Old empty series',2027)").lastrowid
            hierarchy.migrate_legacy(db)
            plan=hierarchy.structure(db,identity)
            self.assertEqual([r['label'] for r in plan],[f'R{i}' for i in range(1,19)])
            self.assertTrue(all(len(r['courses'])==2 for r in plan))
            self.assertEqual(db.execute('SELECT count(*) FROM races WHERE series_id=?',(identity,)).fetchone()[0],0)

    def complete_ratings(self):
        with self.app.store.connect(write=True) as db:
            for boat in service.boats(db,self.series):
                service.save_boat(db,{**boat,'ratings':{**boat['ratings'],'TACIH':500,'TAUDL':800,'TAUDM':710,'TAUDH':660}},self.user,boat['id'])

    def test_gph_race_derives_rating_and_has_no_wind_category(self):
        self.complete_ratings()
        with self.app.store.connect(write=True) as db:
            round_id=hierarchy.structure(db,self.series)[0]['id']
            data={'series_id':self.series,'round_id':round_id,'course':'stor','mode':'normal','handicap_system':'gph','start':'2026-05-06T18:30:00+02:00','route':['A']}
            race=service.create_race(db,data,self.user)
            self.assertEqual(get(db,'races',race)['rating_key'],'GPH')
            self.assertEqual(get(db,'rounds',round_id)['handicap_system'],'gph')
            row=next(r for r in service.rows(db,race) if r['boat_id']==self.boats[0])
            self.assertEqual(row['rating'],590)
            self.assertEqual({r['wind_category'] for r in service.rows(db,race)},{''})
            with self.assertRaisesRegex(ValueError,'Vindkategori kan ikke'):
                service.day_settings(db,{'series_id':self.series,'date':'2026-05-06','course':'stor','wind_category':'L'},self.user)
            with self.assertRaisesRegex(ValueError,'Vindkategori kan ikke'):
                service.create_race(db,{**data,'wind_category':'M'},self.user)
        self.finish(race,self.boats[0])

    def test_round_system_can_change_before_results_and_is_shared_by_courses(self):
        self.complete_ratings();race=self.race()
        with self.app.store.connect(write=True) as db:
            round_id=db.execute('SELECT round_id FROM round_courses WHERE id=?',(get(db,'races',race)['round_course_id'],)).fetchone()[0]
            self.assertEqual(get(db,'rounds',round_id)['handicap_system'],'circle')
            hierarchy.set_system(db,round_id,'gph',self.user)
            self.assertEqual(get(db,'races',race)['rating_key'],'GPH')
            self.assertEqual(next(r for r in service.rows(db,race) if r['boat_id']==self.boats[0])['rating'],590)
            hierarchy.set_system(db,round_id,'updown',self.user)
            self.assertEqual(get(db,'races',race)['rating_key'],'TAUDM')
            self.assertEqual(next(r for r in service.rows(db,race) if r['boat_id']==self.boats[0])['rating'],710)
            with self.assertRaisesRegex(ValueError,'hele ræset'):
                service.create_race(db,{'series_id':self.series,'round_id':round_id,'course':'lille','mode':'normal','handicap_system':'circle','wind_category':'M','start':'2026-05-06T18:30:00+02:00','route':['A']},self.user)
        self.finish(race,self.boats[0])
        with self.app.store.connect(write=True) as db:
            with self.assertRaisesRegex(ValueError,'før resultatindtastningen'):
                hierarchy.set_system(db,round_id,'circle',self.user)

    def test_gph_cannot_use_incomplete_circle_ratings(self):
        status,result=self.request('/api/races',{'series_id':self.series,'label':'R1','course':'stor','mode':'normal','handicap_system':'gph','start':'2026-05-06T18:30:00+02:00','route':['A']})
        self.assertEqual(status,400,result)
        self.assertEqual(result['handicap_issues'][0]['required_key'],'GPH')
        with self.assertRaisesRegex(ValueError,'GPH'):
            self.race(key='GPH')

    def test_historical_gph_is_recalculated_from_historical_triplet(self):
        self.complete_ratings()
        race=self.race(key='GPH');self.finish(race,self.boats[0])
        with self.app.store.connect(write=True) as db:
            data={'boat_id':self.boats[0],'ratings':{'TACIL':710,'TACIM':620,'TACIH':505},'note':'Korrekte historiske cirkeltal'}
            preview=calculations.recalculate(db,race,data,self.user)
            calculations.recalculate(db,race,{**data,'token':preview['token']},self.user,apply=True)
            row=next(r for r in service.rows(db,race) if r['boat_id']==self.boats[0])
            self.assertEqual(row['rating'],gph(data['ratings']))
            self.assertEqual(row['wind_category'],'')
            self.assertEqual(json.loads(get(db,'boats',self.boats[0])['ratings'])['TACIM'],600)

    def test_series_settings_edit_existing_series_without_creating_calendar(self):
        with self.app.store.connect() as db:
            before={table:db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in ('series','rounds','round_courses','heats')}
        status,body=self.request('/api/series/settings',{'series_id':self.series,'name':'Updated season','year':2027,'reference':'1050,5','discards':[6,3]})
        self.assertEqual(status,200,body)
        with self.app.store.connect() as db:
            updated=get(db,'series',self.series)
            self.assertEqual(updated['name'],'Updated season')
            self.assertEqual(updated['year'],2027)
            self.assertEqual(updated['reference'],1050.5)
            self.assertEqual(json.loads(updated['discards']),[3,6])
            self.assertEqual(before,{table:db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in before})
            logged=json.loads(db.execute("SELECT payload FROM audit WHERE action='series.update'").fetchone()[0])
            self.assertEqual(logged['before']['name'],'Test')

    def test_series_settings_keep_existing_race_basis_and_dates(self):
        race=self.race();self.finish(race,self.boats[0])
        with self.app.store.connect() as db:
            basis=get(db,'races',race)
            entries=service.rows(db,race)
        status,body=self.request('/api/series/settings',{'series_id':self.series,'name':'Renamed','reference':1100,'discards':[]})
        self.assertEqual(status,200,body)
        with self.app.store.connect() as db:
            self.assertEqual(get(db,'races',race),basis)
            self.assertEqual(service.rows(db,race),entries)
        second=self.race(label='R2')
        with self.app.store.connect() as db:self.assertEqual(get(db,'races',second)['reference'],1100)
        status,body=self.request('/api/series/settings',{'series_id':self.series,'year':2027})
        self.assertEqual(status,200,body)
        with self.app.store.connect() as db:
            self.assertEqual(get(db,'series',self.series)['year'],2027)
            self.assertEqual(get(db,'races',race),basis)
            self.assertEqual(service.rows(db,race),entries)

    def test_series_settings_validate_values_and_require_admin(self):
        for data in ({'name':''},{'discards':[3,3]},{'discards':[1]},{'reference':'abc'},{'year':999}):
            with self.subTest(data=data):
                status,_=self.request('/api/series/settings',{'series_id':self.series,**data})
                self.assertEqual(status,400)
        status,_=self.request('/api/series/settings',{'series_id':self.series,'name':'Forbidden'},role='recorder')
        self.assertEqual(status,403)
        with self.app.store.connect() as db:self.assertEqual(get(db,'series',self.series)['name'],'Test')

    def both_courses(self):
        with self.app.store.connect(write=True) as db:
            little=[service.save_boat(db,{'series_id':self.series,'course':'lille','class_name':'Lille bane 1','name':f'Little {i}','skipper':f'Little skipper {i}','sail_number':f'DEN 20{i}','boat_type':'X-332','ratings':{'TACIM':600}},self.user) for i in range(2)]
        large=self.race()
        with self.app.store.connect(write=True) as db:
            small=service.create_race(db,{'series_id':self.series,'label':'R1','course':'lille','mode':'normal','rating_key':'TACIM','start':'2026-05-06T18:30:00+02:00','route':['A'],'boat_ids':little},self.user)
            round_id=timing.round_id_for_race(db,large)
        return round_id,large,small,little

    def test_complete_round_assigns_dnc_on_both_courses(self):
        round_id,large,small,little=self.both_courses()
        self.finish(large,self.boats[0]);self.finish(large,self.boats[1],stamp='',status='DNF');self.finish(small,little[0])
        with self.app.store.connect() as db:
            before=timing.detail(db,round_id)
            revisions={str(r['id']):r['revision'] for r in before['courses']}
            self.assertEqual(len(before['entries']),5)
            self.assertEqual({row['course'] for row in before['entries']},{'stor','lille'})
        status,result=self.request(f'/api/rounds/{round_id}/complete',{'revisions':revisions},role='recorder')
        self.assertEqual(status,200,result)
        self.assertEqual(len(result['dnc']),2)
        with self.app.store.connect() as db:
            after=timing.detail(db,round_id)
            self.assertTrue(after['round']['completed'])
            self.assertEqual(sum(r['status']=='DNF' for r in after['entries']),1)
            self.assertTrue(all(r['sequence'] is not None for r in after['entries']))
            for row in after['entries']:
                original=next(r for r in before['entries'] if r['boat_id']==row['boat_id'])
                if row['status']=='DNC':self.assertIsNone(row['instant']);self.assertIsNone(row['elapsed'])
                if original['instant']:self.assertEqual(row['instant'],original['instant'])
            self.assertEqual(timing.complete(db,round_id,{'revisions':{}},self.user)['dnc'],[])
        with self.assertRaisesRegex(ValueError,'afsluttet'):
            self.finish(large,self.boats[2])

    def test_reopen_round_removes_only_automatic_dnc_and_unpublishes_both(self):
        round_id,large,small,little=self.both_courses()
        self.finish(large,self.boats[0]);self.finish(large,self.boats[1],stamp='',status='DNC');self.finish(small,little[0]);self.finish(small,little[1],stamp='',status='DNF')
        with self.app.store.connect(write=True) as db:
            revisions={str(r['id']):r['revision'] for r in timing.course_races(db,round_id)}
            timing.complete(db,round_id,{'revisions':revisions},self.user)
            timing.publish(db,round_id,{'revisions':{str(r['id']):r['revision'] for r in timing.course_races(db,round_id)}},self.user)
            timing.reopen(db,round_id,{'revisions':{str(r['id']):r['revision'] for r in timing.course_races(db,round_id)}},self.user)
            state=timing.detail(db,round_id)
            self.assertFalse(state['round']['completed'])
            self.assertTrue(all(not r['published'] for r in state['courses']))
            self.assertIsNone(next(r for r in state['entries'] if r['boat_id']==self.boats[2])['sequence'])
            self.assertEqual(next(r for r in state['entries'] if r['boat_id']==self.boats[1])['status'],'DNC')
            self.assertEqual(next(r for r in state['entries'] if r['boat_id']==little[1])['status'],'DNF')
            self.assertEqual(sum(r['instant'] is not None for r in state['entries']),2)

    def test_round_completion_checks_other_course_revision_and_captures(self):
        round_id,large,small,little=self.both_courses()
        with self.app.store.connect() as db:revisions={str(r['id']):r['revision'] for r in timing.course_races(db,round_id)}
        self.finish(small,little[0])
        status,_=self.request(f'/api/rounds/{round_id}/complete',{'revisions':revisions})
        self.assertEqual(status,400)
        with self.app.store.connect(write=True) as db:
            self.assertFalse(get(db,'rounds',round_id)['completed'])
            self.assertEqual(db.execute("SELECT count(*) FROM finishes WHERE auto_dnc=1").fetchone()[0],0)
            service.capture(db,small,{'request_id':secrets.token_hex(16),'instant':'2026-05-06T19:45:00+02:00'},self.user)
            with self.assertRaisesRegex(ValueError,'tidsstempler uden båd'):
                timing.complete(db,round_id,{'revisions':{str(r['id']):r['revision'] for r in timing.course_races(db,round_id)}},self.user)

    def test_time_capture_can_be_assigned_across_courses_in_same_round(self):
        round_id,large,small,little=self.both_courses()
        with self.app.store.connect(write=True) as db:
            cap=service.capture(db,large,{'request_id':secrets.token_hex(16),'instant':'2026-05-06T19:30:00+02:00'},self.user)
            service.finish(db,small,{'boat_id':little[0],'capture_id':cap['id'],'request_id':secrets.token_hex(16)},self.user)
            row=next(r for r in service.rows(db,small) if r['boat_id']==little[0])
            self.assertEqual(row['elapsed'],3600)
            self.assertEqual(db.execute('SELECT used FROM captures WHERE id=?',(cap['id'],)).fetchone()[0],1)

    def test_completion_rejects_registered_but_unconfigured_second_course(self):
        with self.app.store.connect(write=True) as db:
            service.save_boat(db,{'series_id':self.series,'course':'lille','class_name':'Lille bane 1','name':'Waiting','skipper':'S','sail_number':'DEN 202','boat_type':'X','ratings':{'TACIM':600}},self.user)
        large=self.race()
        with self.app.store.connect(write=True) as db:
            round_id=timing.round_id_for_race(db,large)
            with self.assertRaisesRegex(ValueError,'Lille bane'):
                timing.complete(db,round_id,{'revisions':{str(large):0}},self.user)
            self.assertFalse(get(db,'rounds',round_id)['completed'])

    def test_missing_relevant_rating_blocks_setup(self):
        self.race()
        with self.assertRaisesRegex(ValueError,'TAUDH'):
            self.race(label='R2',key='TAUDH')

    def test_results_expose_course_letters_and_rounding_directions(self):
        with self.app.store.connect(write=True) as db:
            race=service.create_race(db,{'series_id':self.series,'course':'stor','mode':'normal','rating_key':'TACIM','label':'ABCD','start':'2026-05-06T18:30:00+02:00','route':['A','B','C','D'],'boat_ids':self.boats},self.user)
            view=service.result_view(db,self.series,'stor',drafts=True)
            selected=next(r for r in view['races'] if r['id']==race)
            self.assertEqual(selected['rounding_marks'],[
                {'name':'A','side':'starboard'}, {'name':'B','side':'port'},
                {'name':'C','side':'port'}, {'name':'D','side':'port'}])

    def test_forward_change_preserves_saved_rating_and_extra_handicap(self):
        race=self.race()
        self.finish(race,self.boats[0])
        with self.app.store.connect(write=True) as db:
            old=service.rows(db,race)[0]
            boat=next(b for b in service.boats(db,self.series) if b['id']==self.boats[0])
            boat.update(ratings={'TACIM':'520,5'},extra_handicap=40)
            service.save_boat(db,boat,self.user,boat['id'])
            current=service.rows(db,race)[0]
            self.assertEqual((current['rating'],current['extra_handicap'],current['corrected']),(old['rating'],old['extra_handicap'],old['corrected']))
        new=self.race(label='R2')
        with self.app.store.connect() as db:
            entry=service.rows(db,new)[0]
            self.assertEqual((entry['rating'],entry['extra_handicap']),(520.5,40))

    def test_late_entry_is_dnc_in_previous_races_in_both_views(self):
        race=self.race()
        self.finish(race,self.boats[0])
        with self.app.store.connect(write=True) as db:
            late=service.save_boat(db,{'series_id':self.series,'class_name':'Stor bane 1','course':'stor','name':'Late boat','skipper':'Late skipper','sail_number':'DEN 999','boat_type':'X-332','ratings':{'TACIM':610}},self.user)
            combined=service.result_view(db,self.series,'stor',drafts=True)
            separate=service.result_view(db,self.series,'stor','Stor bane 1',drafts=True)
            combined_late=next(r for r in combined['daily'][race] if r['boat_id']==late)
            separate_late=next(r for r in separate['daily'][race] if r['boat_id']==late)
            self.assertEqual((combined_late['status'],combined_late['points']),('DNC',5))
            self.assertEqual((separate_late['status'],separate_late['points']),('DNC',4))
            self.assertIsNone(combined_late['elapsed'])
            self.assertEqual(len(combined['daily'][race]),4)
            self.assertEqual(len(separate['daily'][race]),3)
            other=next(r for r in combined['daily'][race] if r['boat_id']==self.boats[2])
            self.assertEqual(other['points'],5)

    def test_explicit_recalculation_requires_matching_preview(self):
        race=self.race()
        self.finish(race,self.boats[0])
        with self.app.store.connect(write=True) as db:
            boat=next(b for b in service.boats(db,self.series) if b['id']==self.boats[0])
            boat.update(ratings={'TACIM':590},extra_handicap=35,recalculate=True)
            with self.assertRaisesRegex(ValueError,'forhåndsvises'):
                service.save_boat(db,boat,self.user,boat['id'])
            preview=service.recalculate_boat(db,boat['id'],boat['ratings'],35,boat,self.user)
            boat['recalculation_token']=preview['token']
            service.save_boat(db,boat,self.user,boat['id'])
            row=service.rows(db,race)[0]
            self.assertEqual(row['corrected'],correction(3600,590,row['distance'])+35)
            self.assertEqual(row['rating'],590)

    def test_extra_handicap_and_race_penalty_are_separate(self):
        race=self.race()
        self.finish(race,self.boats[0],penalty='30,0')
        with self.app.store.connect() as db:
            entry=service.rows(db,race)[0]
            boat=get(db,'boats',self.boats[0])
            self.assertEqual(boat['extra_handicap'],20)
            self.assertNotIn('penalty',boat)
            self.assertEqual(entry['penalty'],30)
            self.assertEqual(entry['extra_handicap'],20)
            self.assertEqual(entry['corrected'],correction(3600,600,entry['distance'])+20+30)
        other=self.race(label='R2')
        with self.app.store.connect() as db:
            entry=service.rows(db,other)[0]
            self.assertEqual(entry['extra_handicap'],20)
            self.assertEqual(entry['penalty'],0)

    def test_pursuit_penalty_adjusts_finish_order_and_requires_times(self):
        race=self.race(mode='pursuit')
        self.finish(race,self.boats[0],penalty=20)
        self.finish(race,self.boats[1],stamp='2026-05-06T19:30:10+02:00')
        with self.app.store.connect() as db:
            result=daily(service.rows(db,race),pursuit=True)
            self.assertEqual(result[0]['boat_id'],self.boats[1])
        with self.assertRaisesRegex(ValueError,'måltider'):
            self.finish(race,self.boats[2],stamp='')

    def test_reference_change_preserves_order_ties_and_points(self):
        race=self.race()
        for boat in self.boats:self.finish(race,boat)
        with self.app.store.connect(write=True) as db:
            before=daily(service.rows(db,race))
            service.set_reference(db,race,'1100,3',self.user,get(db,'races',race)['revision'])
            after=daily(service.rows(db,race))
            self.assertEqual([(r['boat_id'],r['points']) for r in before],[(r['boat_id'],r['points']) for r in after])
            self.assertEqual(len({b['corrected']-a['corrected'] for a,b in zip(before,after)}),1)

    def test_reference_warning_and_series_alignment(self):
        one=self.race();two=self.race(label='R2')
        with self.app.store.connect(write=True) as db:
            service.set_reference(db,one,1100,self.user,0)
            self.assertTrue(service.result_view(db,self.series,'stor',drafts=True)['reference_warning'])
            revisions={str(r['id']):r['revision'] for r in db.execute('SELECT id,revision FROM races')}
            service.series_reference(db,self.series,{'reference':1050,'apply_all':True,'revisions':revisions},self.user)
            self.assertEqual(get(db,'races',two)['reference'],1050)
            self.assertFalse(service.result_view(db,self.series,'stor',drafts=True)['reference_warning'])

    def test_pursuit_extra_handicap_delays_start_and_order_only_scores(self):
        race=self.race(mode='pursuit')
        with self.app.store.connect() as db:
            entries=service.rows(db,race)
            first=entries[0]
            from onsdagsbane.domain import rounded
            expected=rounded((max(r['rating'] for r in entries)-first['rating'])*first['distance'])+20
            self.assertEqual((service.instant(first['start'])-service.instant(get(db,'races',race)['start'])).total_seconds(),expected)
        self.finish(race,self.boats[0],stamp='')
        self.finish(race,self.boats[2],stamp='')
        with self.app.store.connect() as db:
            result=daily(service.rows(db,race),pursuit=True)
            self.assertEqual([r['boat_id'] for r in result[:2]],[self.boats[0],self.boats[2]])
            self.assertIsNone(result[0]['elapsed'])
            with self.assertRaisesRegex(ValueError,'tidsmålte'):
                apply_redress(db,race,self.boats[1],self.user,secrets.token_hex(16))

    def test_time_validation_rejects_non_times_and_wrong_dates(self):
        race=self.race()
        for value in ['hello','19.30','19,30','2026-05-06T25:99:00','2026-05-06T18:00:00','2026-05-09T19:00:00']:
            with self.subTest(value=value),self.assertRaises(ValueError):
                self.finish(race,self.boats[0],stamp=value)

    def test_duplicate_and_concurrent_finish_saved_once(self):
        race=self.race()
        def add(_):
            try:self.finish(race,self.boats[0]);return True
            except ValueError:return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(add,range(2))),[False,True])
        with self.app.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM finishes').fetchone()[0],1)

    def test_repeat_same_request_is_idempotent(self):
        race=self.race();request=secrets.token_hex(16)
        self.finish(race,self.boats[0],request_id=request)
        self.finish(race,self.boats[0],request_id=request)
        with self.app.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM finishes').fetchone()[0],1)

    def test_capture_cannot_be_used_twice_or_on_other_round(self):
        one=self.race();two=self.race(label='R2')
        with self.app.store.connect(write=True) as db:
            cap=service.capture(db,one,{'request_id':secrets.token_hex(16)},self.user,now=service.instant('2026-05-06T19:30:00'))
        self.finish(one,self.boats[0],capture_id=cap['id'])
        with self.assertRaisesRegex(ValueError,'allerede brugt'):
            self.finish(one,self.boats[1],capture_id=cap['id'])
        with self.assertRaisesRegex(ValueError,'andet ræs'):
            self.finish(two,self.boats[1],capture_id=cap['id'])

    def test_capture_keeps_click_time_and_confirmation_can_correct_it(self):
        race=self.race()
        with self.app.store.connect(write=True) as db:
            cap=service.capture(db,race,{'request_id':secrets.token_hex(16),'instant':'2026-05-06T19:00:12.250+02:00'},self.user)
            self.assertEqual(service.instant(cap['instant']).second,12)
        self.finish(race,self.boats[0],stamp='2026-05-06T19:00:15+02:00',capture_id=cap['id'])
        with self.app.store.connect() as db:
            row=next(r for r in service.rows(db,race) if r['boat_id']==self.boats[0])
            self.assertEqual(row['elapsed'],1815)
            original=get(db,'captures',cap['id'])
            self.assertEqual(service.instant(original['instant']).second,12)
            self.assertEqual(original['used'],1)

    def test_recorder_can_publish_but_cannot_create_race(self):
        race=self.race();self.finish(race,self.boats[0])
        status,_=self.request(f'/api/races/{race}/publish',{'published':True,'revision':1,'confirm_dnc':True},role='recorder')
        self.assertEqual(status,200)
        status,_=self.request('/api/races',{},role='recorder')
        self.assertEqual(status,403)

    def test_pending_user_csrf_and_foreign_origin_denied(self):
        self.assertEqual(self.request('/api/series',{},approved=False)[0],403)
        self.assertEqual(self.request('/api/series',{},csrf=False)[0],403)
        self.assertEqual(self.request('/api/series',{},origin='https://evil.example')[0],403)

    def test_temporary_preview_login_is_local_ephemeral_and_can_log_out(self):
        app=Application(db_path=self.app.store.path,initialize=False,preview_admin=True)
        def call(target,path,query='',cookie='',data=None,csrf='',remote='127.0.0.1'):
            raw=json.dumps(data).encode() if data is not None else b''
            status=[];headers=[]
            body=b''.join(target({'PATH_INFO':path,'QUERY_STRING':query,'REQUEST_METHOD':'POST' if data is not None else 'GET','REMOTE_ADDR':remote,'HTTP_COOKIE':cookie,'CONTENT_TYPE':'application/json','CONTENT_LENGTH':str(len(raw)),'wsgi.input':io.BytesIO(raw),'HTTP_X_CSRF_TOKEN':csrf},lambda s,h:(status.append(int(s[:3])),headers.extend(h))))
            return status[0],headers,body
        query='token='+app.preview_login_token
        self.assertEqual(call(app,'/auth/preview-admin',query,remote='203.0.113.2')[0],403)
        self.assertEqual(call(app,'/auth/preview-admin','token=wrong')[0],403)
        status,headers,_=call(app,'/auth/preview-admin',query)
        self.assertEqual(status,303)
        cookie=next(v for k,v in headers if k=='Set-Cookie').split(';')[0]
        self.assertIn('HttpOnly',next(v for k,v in headers if k=='Set-Cookie'))
        session=next(iter(app.preview_sessions.values()))
        # Actual admin-only API works, and a normal server never accepts this identity.
        self.assertEqual(call(app,'/api/users',cookie=cookie)[0],200)
        self.assertEqual(call(self.app,'/api/users',cookie=cookie)[0],403)
        self.assertEqual(call(app,'/api/logout',cookie=cookie,data={},csrf='wrong')[0],403)
        self.assertEqual(call(app,'/api/logout',cookie=cookie,data={},csrf=session['csrf'])[0],200)
        self.assertEqual(call(app,'/api/users',cookie=cookie)[0],403)
        app.preview_expires=0
        self.assertEqual(call(app,'/auth/preview-admin',query)[0],403)

    def test_only_google_owner_can_approve(self):
        self.assertEqual(self.request('/api/users',role='admin',provider='facebook')[0],403)
        self.assertEqual(self.request('/api/users',role='admin',provider='google')[0],200)

    def test_public_cannot_read_drafts(self):
        race=self.race()
        env={'PATH_INFO':f'/api/races/{race}','REQUEST_METHOD':'GET'}
        status=[];body=b''.join(self.app(env,lambda s,h:status.append(s)))
        self.assertTrue(status[0].startswith('403'))

    def test_public_startlist_does_not_leak_draft_finishes(self):
        race=self.race();self.finish(race,self.boats[0])
        env={'PATH_INFO':f'/api/races/{race}','QUERY_STRING':'startlist=1','REQUEST_METHOD':'GET'}
        status=[];body=json.loads(b''.join(self.app(env,lambda s,h:status.append(s))))
        self.assertTrue(status[0].startswith('200'))
        self.assertNotIn('instant',body['entries'][0])
        self.assertNotIn('corrected',body['entries'][0])
        self.assertEqual(body['captures'],[])

    def test_oauth_pending_account_owner_bootstrap_and_state_binding(self):
        env_vars={'GOOGLE_CLIENT_ID':'test-client','GOOGLE_CLIENT_SECRET':'test-secret'}
        for email,approved in [('new@example.com',False),(auth.OWNER,True)]:
            with self.subTest(email=email),patch.dict('os.environ',env_vars):
                with self.app.store.connect(write=True) as db:
                    url,header=auth.login_start(db,'google',self.app.base_url)
                    params=parse_qs(urlsplit(url).query)
                    self.assertEqual(params['code_challenge_method'],['S256'])
                    browser={'HTTP_COOKIE':header[1].split(';')[0]}
                    callback={'code':'test-code','state':params['state'][0]}
                    with self.assertRaisesRegex(ValueError,'ugyldigt'):
                        auth.login_callback(db,'google',callback,{},self.app.base_url)
                    with patch('onsdagsbane.auth.remote_json',side_effect=[{'access_token':'server-only'},{'sub':email,'email':email,'email_verified':True,'name':'User'}]):
                        headers=auth.login_callback(db,'google',callback,browser,self.app.base_url)
                    account=dict(db.execute('SELECT * FROM users WHERE subject=?',(email,)).fetchone())
                    self.assertEqual(bool(account['approved']),approved)
                    self.assertEqual(account['role'],'admin' if approved else 'recorder')
                    self.assertIn('HttpOnly',headers[0][1])
                    with self.assertRaisesRegex(ValueError,'ugyldigt'):
                        auth.login_callback(db,'google',callback,browser,self.app.base_url)

    def test_unverified_google_email_is_rejected(self):
        with patch.dict('os.environ',{'GOOGLE_CLIENT_ID':'test','GOOGLE_CLIENT_SECRET':'secret'}):
            with self.app.store.connect(write=True) as db:
                url,header=auth.login_start(db,'google',self.app.base_url)
                params=parse_qs(urlsplit(url).query)
                with patch('onsdagsbane.auth.remote_json',side_effect=[{'access_token':'server-only'},{'sub':'bad','email':auth.OWNER,'email_verified':False}]),self.assertRaisesRegex(ValueError,'bekræftet'):
                    auth.login_callback(db,'google',{'state':params['state'][0],'code':'code'},{'HTTP_COOKIE':header[1].split(';')[0]},self.app.base_url)

    def test_stale_edit_rolls_back(self):
        race=self.race();self.finish(race,self.boats[0])
        status,_=self.request(f'/api/races/{race}/edit',{'boat_id':self.boats[0],'revision':0,'status':'FIN','instant':'invalid','note':'Fix','request_id':secrets.token_hex(16)})
        self.assertEqual(status,400)
        with self.app.store.connect() as db:self.assertEqual(service.rows(db,race)[0]['elapsed'],3600)

    def test_day_warning_and_previewed_alignment(self):
        race=self.race()
        with self.app.store.connect(write=True) as db:
            db.execute('UPDATE entries SET distance=distance+1,wind_category=\'L\' WHERE race_id=? AND boat_id=?',(race,self.boats[0]))
            view=service.result_view(db,self.series,'stor',drafts=True)
            self.assertIn('forskellige distancer',view['races'][0]['warning'])
            self.assertIn('vindkategorier',view['races'][0]['warning'])
            data={'series_id':self.series,'date':'2026-05-06','course':'stor','route':['A','D'],'wind_category':'M'}
            preview=service.day_settings(db,data,self.user)
            service.day_settings(db,{**data,'apply':True,'token':preview['token']},self.user)
            entries=service.rows(db,race)
            self.assertEqual(len({r['distance'] for r in entries}),1)
            self.assertEqual({r['wind_category'] for r in entries},{'M'})

    def test_duty_can_be_marked_without_calculating_or_inventing_times(self):
        race=self.race()
        with patch('onsdagsbane.redress.run_group_filter') as model:
            self.finish(race,self.boats[2],stamp='',status='DUTY')
            with self.app.store.connect() as db:
                row=next(r for r in service.rows(db,race) if r['boat_id']==self.boats[2])
                self.assertEqual(row['status'],'DUTY')
                self.assertIsNotNone(row['sequence'])
                for field in ('instant','elapsed','corrected'):
                    self.assertIsNone(row[field])
            model.assert_not_called()
        status,body=self.request(f'/api/races/{race}/publish',{'published':True,'revision':1,'confirm_dnc':True})
        self.assertEqual(status,400)
        self.assertIn('Afslut ræset',body['error'])

    def test_recorder_can_mark_duty(self):
        race=self.race()
        status,_=self.request(f'/api/races/{race}/finish',{'boat_id':self.boats[2],'status':'DUTY','request_id':secrets.token_hex(16)},role='recorder')
        self.assertEqual(status,200)
        with self.app.store.connect() as db:
            self.assertEqual(next(r for r in service.rows(db,race) if r['boat_id']==self.boats[2])['status'],'DUTY')

    def test_redress_waits_for_every_other_result_even_with_two_times(self):
        with self.app.store.connect(write=True) as db:
            fourth=service.save_boat(db,{'series_id':self.series,'class_name':'Stor bane 1','course':'stor','name':'Still sailing','skipper':'Skipper 4','sail_number':'DEN 104','boat_type':'X-332','ratings':{'TACIM':610}},self.user)
        self.boats.append(fourth)
        race=self.race()
        self.finish(race,self.boats[2],stamp='',status='DUTY')
        for boat in self.boats[:2]:self.finish(race,boat)
        with patch('onsdagsbane.redress.run_group_filter') as model:
            status,body=self.request(f'/api/races/{race}/redress',{'boat_id':self.boats[2],'request_id':secrets.token_hex(16)})
            self.assertEqual(status,400)
            self.assertIn('Still sailing',body['error'])
            model.assert_not_called()
        # A default DNC is not an explicit final status. It must be registered first.
        with self.app.store.connect() as db:
            self.assertEqual(next(r for r in service.rows(db,race) if r['boat_id']==fourth)['status'],'DNC')
            self.assertEqual(next(r for r in service.rows(db,race) if r['boat_id']==self.boats[2])['status'],'DUTY')

    def test_redress_waits_for_unassigned_capture(self):
        race=self.race()
        self.finish(race,self.boats[2],stamp='',status='DUTY')
        for boat in self.boats[:2]:self.finish(race,boat)
        with self.app.store.connect(write=True) as db:
            service.capture(db,race,{'request_id':secrets.token_hex(16),'instant':'2026-05-06T19:45:00+02:00'},self.user)
            with patch('onsdagsbane.redress.run_group_filter') as model:
                with self.assertRaisesRegex(ValueError,'tidsstempler uden båd'):
                    apply_redress(db,race,self.boats[2],self.user,secrets.token_hex(16))
                model.assert_not_called()

    def test_multiple_duties_use_completed_observations(self):
        with self.app.store.connect(write=True) as db:
            fourth=service.save_boat(db,{'series_id':self.series,'class_name':'Stor bane 1','course':'stor','name':'Other judge','skipper':'Skipper 4','sail_number':'DEN 104','boat_type':'X-332','ratings':{'TACIM':610}},self.user)
        self.boats.append(fourth)
        first=self.race()
        for i,boat in enumerate(self.boats):self.finish(first,boat,stamp=f'2026-05-06T19:{30+i}:00+02:00')
        with self.app.store.connect(write=True) as db:
            db.execute('UPDATE races SET published=1 WHERE id=?',(first,))
        second=self.race(label='R2')
        for boat in self.boats[2:]:self.finish(second,boat,stamp='',status='DUTY')
        for i,boat in enumerate(self.boats[:2]):self.finish(second,boat,stamp=f'2026-05-06T19:{32+i}:00+02:00')
        with self.app.store.connect(write=True) as db:
            round_id=timing.round_id_for_race(db,second)
            completed=timing.complete(db,round_id,{'revisions':{str(r['id']):r['revision'] for r in timing.course_races(db,round_id)}},self.user)
            self.assertEqual(len(completed['redress']),2)
            for result in completed['redress']:
                self.assertGreater(result['corrected'],0)
            self.assertEqual(sum(r['status']=='RDG' for r in service.rows(db,second)),2)
            service.publish(db,second,{'published':True,'revision':get(db,'races',second)['revision']},self.user)
            self.assertTrue(get(db,'races',second)['published'])

    def test_actual_redress_model_and_reference_normalization(self):
        first=self.race()
        for i,boat in enumerate(self.boats):
            self.finish(first,boat,stamp=f'2026-05-06T19:{30+i*2}:00+02:00')
        second=self.race(label='R2')
        with self.app.store.connect(write=True) as db:
            # Another week supplies a real prior from the first race.
            db.execute("UPDATE races SET start='2026-05-13T18:30:00+02:00' WHERE id=?",(second,))
            db.execute("UPDATE entries SET start='2026-05-13T18:30:00+02:00' WHERE race_id=?",(second,))
            db.execute('UPDATE races SET published=1 WHERE id=?',(first,))
        self.finish(second,self.boats[2],stamp='',status='DUTY')
        for i,boat in enumerate(self.boats[:2]):
            self.finish(second,boat,stamp=f'2026-05-13T19:{31+i*2}:00+02:00')
        with self.app.store.connect(write=True) as db:
            round_id=timing.round_id_for_race(db,second)
            completed=timing.complete(db,round_id,{'revisions':{str(r['id']):r['revision'] for r in timing.course_races(db,round_id)}},self.user)
            result=completed['redress'][0]
            self.assertGreater(result['corrected'],0)
            self.assertIn('repoets redress-model',result['note'])
            service.undo(db,second,self.boats[2],self.user,get(db,'races',second)['revision'])
            service.set_reference(db,second,1100,self.user,get(db,'races',second)['revision'])
            new=apply_redress(db,second,self.boats[2],self.user,secrets.token_hex(16))
            from onsdagsbane.domain import rounded
            self.assertEqual(new['corrected']-result['corrected'],rounded(100*get(db,'races',second)['distance']))
            # Correcting a real finish retains duty but invalidates its derived time.
            timing.reopen(db,round_id,{'revisions':{str(r['id']):r['revision'] for r in timing.course_races(db,round_id)}},self.user)
            service.undo(db,second,self.boats[0],self.user,get(db,'races',second)['revision'])
            pending=next(r for r in service.rows(db,second) if r['boat_id']==self.boats[2])
            self.assertEqual(pending['status'],'DUTY')
            self.assertIsNone(pending['corrected'])
            self.assertIsNone(pending['elapsed'])
            with self.assertRaisesRegex(ValueError,'alle øvrige deltagere'):
                apply_redress(db,second,self.boats[2],self.user,secrets.token_hex(16))
            service.finish(db,second,{'boat_id':self.boats[0],'instant':'2026-05-13T19:32:00+02:00','request_id':secrets.token_hex(16)},self.user)
            completed=timing.complete(db,round_id,{'revisions':{str(r['id']):r['revision'] for r in timing.course_races(db,round_id)}},self.user)
            recalculated=completed['redress'][0]
            self.assertGreater(recalculated['corrected'],0)
            self.assertEqual(next(r for r in service.rows(db,second) if r['boat_id']==self.boats[2])['status'],'RDG')



    def test_shared_boat_and_handicap_references_survive_master_changes(self):
        first=self.race()
        with self.app.store.connect(write=True) as db:
            boat=service.boats(db,self.series)[0]
            old_set=boat['current_handicap_set_id']
            other=service.create_series(db,{'name':'Another season','year':2027},self.user)
            alias=fleet.enroll(db,other,{'profile_ids':[boat['profile_id']],'course':'stor','class_name':'Stor bane 1'},self.user)[0]
            second=service.create_race(db,{'series_id':other,'course':'stor','label':'R1','mode':'normal','start':'2027-05-05T18:30:00+02:00','route':['A'],'rating_key':'TACIM'},self.user)
            self.assertEqual(service.rows(db,second)[0]['handicap_set_id'],old_set)
            service.save_boat(db,{**boat,'ratings':{**boat['ratings'],'TACIM':650}},self.user,boat['id'])
            updated=get(db,'boats',boat['id'])
            self.assertNotEqual(updated['current_handicap_set_id'],old_set)
            self.assertEqual(get(db,'boats',alias)['current_handicap_set_id'],updated['current_handicap_set_id'])
            self.assertEqual(next(r for r in service.rows(db,first) if r['boat_id']==boat['id'])['handicap_set_id'],old_set)
            self.assertEqual(service.rows(db,second)[0]['handicap_set_id'],old_set)
            service.save_boat(db,{**boat,'handicap_set_id':old_set},self.user,boat['id'])
            self.assertEqual(get(db,'boats',boat['id'])['current_handicap_set_id'],old_set)
            self.assertEqual(len(handicaps.owned(db,boat['id'])),2)
            self.assertEqual(handicaps.for_values(db,boat['profile_id'],boat['ratings'],boat['extra_handicap']),old_set)
            for table in ('boats','entries'):
                columns={r['name'] for r in db.execute(f'PRAGMA table_info({table})')}
                self.assertFalse(columns & {'rating','ratings','ratings_snapshot','extra_handicap'})
            self.assertFalse(list(db.execute('PRAGMA foreign_key_check')))

    def test_historical_selection_reuses_owned_object_and_rejects_other_boat(self):
        race=self.race();self.finish(race,self.boats[0])
        with self.app.store.connect(write=True) as db:
            boat=get(db,'boats',self.boats[0])
            chosen=handicaps.for_values(db,boat['profile_id'],{'TACIM':650},12)
            data={'boat_id':boat['id'],'handicap_set_id':chosen,'note':'Korrekt målebrev'}
            preview=calculations.recalculate(db,race,data,self.user)
            calculations.recalculate(db,race,{**data,'token':preview['token']},self.user,apply=True)
            row=next(r for r in service.rows(db,race) if r['boat_id']==boat['id'])
            self.assertEqual((row['handicap_set_id'],row['rating'],row['extra_handicap']),(chosen,650,12))
            self.assertNotEqual(get(db,'boats',boat['id'])['current_handicap_set_id'],chosen)
            other=get(db,'boats',self.boats[1])['current_handicap_set_id']
            with self.assertRaisesRegex(ValueError,'tilhøre'):
                calculations.recalculate(db,race,{**data,'handicap_set_id':other},self.user)
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('UPDATE entries SET handicap_set_id=? WHERE race_id=? AND boat_id=?',(other,race,boat['id']))

    def test_settings_before_enrollment_and_independent_start_defaults(self):
        with self.app.store.connect(write=True) as db:
            series=service.create_series(db,{'name':'Planned','year':2026,'round_labels':'R1-R2','default_start_stor':'18.40','default_start_lille':'18,45'},self.user)
            planned=hierarchy.structure(db,series)
            self.assertEqual({c['course']:c['start_time'] for c in planned[0]['courses']},{'stor':'18:40:00','lille':'18:45:00'})
            planning.update_round(db,planned[0]['id'],{'race_date':'2026-05-06','start_times':{'stor':'18:50','lille':'18:55'}},self.user)
            race=service.create_race(db,{'series_id':series,'round_id':planned[0]['id'],'course':'stor','mode':'normal','route':['A'],'rating_key':'TACIM'},self.user)
            self.assertEqual(service.rows(db,race),[])
            self.assertIn('18:50:00',get(db,'races',race)['start'])
            boat=get(db,'boats',self.boats[0])
            alias=fleet.enroll(db,series,{'profile_ids':[boat['profile_id']],'course':'stor','class_name':'Stor bane 1'},self.user)[0]
            self.assertEqual([r['boat_id'] for r in service.rows(db,race)],[alias])
            self.assertEqual({c['course']:c['start_time'] for c in hierarchy.structure(db,series)[1]['courses']},{'stor':'18:40:00','lille':'18:45:00'})

    def test_failed_automatic_duty_calculation_rolls_back_round_closure(self):
        round_id,large,small,little=self.both_courses()
        self.finish(large,self.boats[0]);self.finish(large,self.boats[1],stamp='',status='DUTY')
        with self.app.store.connect() as db:
            before=timing.detail(db,round_id)
        status,result=self.request(f'/api/rounds/{round_id}/complete',{'revisions':{str(r['id']):r['revision'] for r in before['courses']}},role='recorder')
        self.assertEqual(status,400,result)
        self.assertIn('Dommertid',result['error'])
        with self.app.store.connect() as db:
            self.assertEqual(timing.detail(db,round_id),before)
            self.assertEqual(db.execute('SELECT count(*) FROM finishes WHERE auto_dnc=1').fetchone()[0],0)


class RuleTests(unittest.TestCase):
    def test_gph_uses_official_weights_and_requires_three_circle_ratings(self):
        self.assertEqual(gph({'TACIL':711.6,'TACIM':570.2,'TACIH':507.6}),575.8)
        self.assertEqual(gph({'TACIL':'600,0','TACIM':600,'TACIH':600.2}),600.1)
        self.assertIsNone(gph({'TACIL':700,'TACIM':600}))
        self.assertIsNone(gph({'TAUDL':700,'TAUDM':600,'TAUDH':500}))

    def test_decimal_separators_and_ambiguity(self):
        self.assertEqual(number('601,7'),number('601.7'))
        for value in ('1,2.3','NaN','Infinity','1e3','601abc','1.000,2','-2'):
            with self.subTest(value=value),self.assertRaises(ValueError):number(value)

    def test_dh_formula_half_up_and_ties(self):
        self.assertEqual(correction(3600,600,5),5600)
        self.assertEqual(correction(3600,600,5,1100),6100)
        self.assertEqual(correction(3600,999.9,5),3601)
        result=daily([{'boat_id':1,'status':'FIN','corrected':4000},{'boat_id':2,'status':'FIN','corrected':4000},{'boat_id':3,'status':'DNF'}])
        self.assertEqual([r['points'] for r in result],[1.5,1.5,4])

    def test_a8_tiebreak_and_dne_cannot_be_discarded(self):
        fleet=[{'id':1},{'id':2}];races=[{'id':i} for i in (1,2,3)]
        results={1:[{'boat_id':1,'points':1,'status':'FIN'},{'boat_id':2,'points':2,'status':'FIN'}],2:[{'boat_id':1,'points':3,'status':'DNE'},{'boat_id':2,'points':1,'status':'FIN'}],3:[{'boat_id':1,'points':2,'status':'FIN'},{'boat_id':2,'points':3,'status':'DNE'}]}
        result=overall(fleet,races,results,[3])
        self.assertEqual(result[0]['id'],1) # A8.2 compares all scores from last race, including discards.
        self.assertFalse(any(s['discarded'] and s['status']=='DNE' for r in result for s in r['scores']))


class ImportTests(unittest.TestCase):
    def test_utf8_import_and_repair_preserve_edits_and_results(self):
        from onsdagsbane.importer import seed
        read_text = Path.read_text
        # Simulate the Windows default even when running on a UTF-8 system.
        def windows_read(path, *args, **kwargs):
            if not args and 'encoding' not in kwargs:
                kwargs['encoding'] = 'cp1252'
            return read_text(path, *args, **kwargs)

        with tempfile.TemporaryDirectory() as tmp, patch.object(Path, 'read_text', windows_read):
            app = Application(db_path=Path(tmp)/'db',initialize=False)
            seed(app.store)
            with app.store.connect(write=True) as db:
                profile = db.execute('SELECT * FROM boat_profiles WHERE skipper=?', ('Oluf GRØNKJÆR',)).fetchone()
                self.assertIsNotNone(profile)
                self.assertEqual(profile['club'], 'Kaløvig Bådelaug')
                finishes = [tuple(row) for row in db.execute('SELECT * FROM finishes ORDER BY race_id,boat_id')]
                # Reproduce the old import, alongside a later manual edit.
                db.execute('UPDATE boat_profiles SET skipper=?,club=?,name=? WHERE id=?',
                           ('Oluf GRØNKJÆR'.encode('utf-8').decode('cp1252'),
                            'Kaløvig Bådelaug'.encode('utf-8').decode('cp1252'), 'Ægir – ændret', profile['id']))
                db.execute("DELETE FROM metadata WHERE key='seed_utf8'")
            seed(app.store)
            seed(app.store)
            with app.store.connect() as db:
                fixed = db.execute('SELECT * FROM boat_profiles WHERE id=?', (profile['id'],)).fetchone()
                self.assertEqual(fixed['skipper'], 'Oluf GRØNKJÆR')
                self.assertEqual(fixed['club'], 'Kaløvig Bådelaug')
                self.assertEqual(fixed['name'], 'Ægir – ændret')
                self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM finishes ORDER BY race_id,boat_id')], finishes)

    def test_real_source_results_preserved_and_repeat_safe(self):
        from onsdagsbane.importer import seed
        with tempfile.TemporaryDirectory() as tmp:
            app=Application(db_path=Path(tmp)/'db',initialize=False)
            seed(app.store)
            with app.store.connect() as db:
                self.assertEqual(db.execute('SELECT count(*) FROM boats').fetchone()[0],31)
                self.assertEqual(db.execute('SELECT count(*) FROM finishes').fetchone()[0],514)
                view=service.result_view(db,1,'stor','Stor bane 1')
                self.assertEqual(view['overall'][0]['name'],'Nordlys')
                self.assertEqual(view['overall'][0]['total'],12)
                from onsdagsbane.importer import SEED
                for path in SEED.glob('*.json'):
                    source=json.loads(path.read_text(encoding='utf-8'))
                    cls=source['RegattaName']
                    course='lille' if cls.lower().startswith('lille') else 'stor'
                    computed={r['skipper']:r for r in service.result_view(db,1,course,cls)['overall']}
                    for boat in source['EntryResults']:
                        if cls!='Lille bane 2':
                            self.assertEqual(computed[boat['TeamName']]['total'],float(boat['NetPoints']),boat['TeamName'])
                little=service.result_view(db,1,'lille','Lille bane 2')
                fly=next(r for r in little['overall'] if r['name']=='Fly')
                self.assertEqual(fly['total'],91)
                self.assertEqual([s['points'] for s in fly['scores']],[9]*14+[4,4,2])
                self.assertEqual(sum(s['discarded'] for s in fly['scores']),5)
                first=little['races'][0]['id']
                self.assertEqual(next(r for r in little['daily'][first] if r['name']=='Fly')['status'],'DNC')
                combined=service.result_view(db,1,'lille')
                self.assertEqual(next(r for r in combined['overall'] if r['name']=='Fly')['total'],176)
            Application(db_path=Path(tmp)/'db')
            with app.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM boats').fetchone()[0],31)


if __name__=='__main__':unittest.main()
