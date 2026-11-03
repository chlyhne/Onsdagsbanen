import json
import unittest
import test_app
from onsdagsbane import service, hierarchy, fleet


class CompatibilityTests(unittest.TestCase):
    setUp=test_app.WebTests.setUp
    request=test_app.WebTests.request
    race=test_app.WebTests.race
    finish=test_app.WebTests.finish

    def heat(self,label='R1'):
        with self.app.store.connect() as db:
            return db.execute("SELECT h.id,p.id round_id FROM heats h JOIN round_courses c ON c.id=h.round_course_id JOIN rounds p ON p.id=c.round_id WHERE p.series_id=? AND p.label=? AND h.name='Stor bane 1'",(self.series,label)).fetchone()

    def profile(self,ratings):
        status,result=self.request('/api/boat-profiles',{'name':'Incomplete','skipper':'S','sail_number':'DEN X','boat_type':'X','ratings':ratings})
        self.assertEqual(status,200,result)
        return result['id']

    def test_missing_relevant_handicap_rejects_add_without_partial_enrollment(self):
        profile=self.profile({'TACIL':700})
        heat=self.heat()
        status,result=self.request('/api/heats/boats/move',{'heat_id':heat['id'],'scope':'series','action':'add','profile_ids':[profile]})
        self.assertEqual(status,400,result)
        self.assertEqual(result['handicap_issues'][0]['profile_id'],profile)
        self.assertEqual(result['handicap_issues'][0]['required_fields'],['TACIM'])
        with self.app.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM boats WHERE profile_id=?',(profile,)).fetchone()[0],0)
        _,boat=self.request('/api/boat-profiles?id='+str(profile))
        status,result=self.request('/api/boat-profiles',{**boat,'id':profile,'ratings':{'TACIM':600}})
        self.assertEqual(status,200,result)
        status,result=self.request('/api/heats/boats/move',{'heat_id':heat['id'],'scope':'series','action':'add','profile_ids':[profile]})
        self.assertEqual(status,200,result)

    def test_configured_wind_uses_its_exact_rating(self):
        self.race(key='TACIL')
        profile=self.profile({'TACIM':600})
        status,result=self.request('/api/heats/boats/move',{'heat_id':self.heat()['id'],'scope':'round','action':'add','profile_ids':[profile]})
        self.assertEqual(status,400,result)
        self.assertEqual(result['handicap_issues'][0]['required_key'],'TACIL')
        self.assertEqual(self.request('/api/heats/boats/move',{'heat_id':self.heat('R2')['id'],'scope':'round','action':'add','profile_ids':[profile]})[0],200)

    def test_changing_unconfigured_round_checks_all_participants(self):
        round_id=self.heat()['round_id']
        status,result=self.request('/api/rounds/handicap-system',{'round_id':round_id,'handicap_system':'updown'})
        self.assertEqual(status,400,result)
        self.assertEqual(len(result['handicap_issues']),3)
        with self.app.store.connect() as db:self.assertEqual(db.execute('SELECT handicap_system FROM rounds WHERE id=?',(round_id,)).fetchone()[0],'circle')
        for issue in result['handicap_issues']:
            _,boat=self.request('/api/boat-profiles?id='+str(issue['profile_id']))
            self.assertEqual(self.request('/api/boat-profiles',{**boat,'id':issue['profile_id'],'ratings':{**boat['ratings'],'TAUDM':650}})[0],200)
        self.assertEqual(self.request('/api/rounds/handicap-system',{'round_id':round_id,'handicap_system':'updown'})[0],200)

    def test_historical_repair_only_fills_missing_snapshot_values(self):
        race=self.race()
        self.finish(race,self.boats[0])
        data={'series_id':self.series,'date':'2026-05-06','course':'stor','wind_category':'H'}
        status,result=self.request('/api/day-settings',data)
        self.assertEqual(status,400,result)
        for issue in result['handicap_issues']:
            _,boat=self.request('/api/boat-profiles?id='+str(issue['profile_id']))
            with self.app.store.connect() as db:before=next(r for r in service.rows(db,race) if r['boat_id']==issue['boat_id'])
            self.assertEqual(self.request('/api/boat-profiles',{**boat,'id':issue['profile_id'],'ratings':{**boat['ratings'],'TACIM':999,'TACIH':500},'repair':issue})[0],200)
            with self.app.store.connect() as db:after=next(r for r in service.rows(db,race) if r['boat_id']==issue['boat_id'])
            self.assertEqual(after['rating'],before['rating'])
            self.assertEqual(after['instant'],before['instant'])
            self.assertEqual(after['corrected'],before['corrected'])
            self.assertEqual(after['handicap_set']['ratings']['TACIH'],500)
        status,result=self.request('/api/day-settings',data)
        self.assertEqual(status,200,result)

    def test_cannot_clear_handicap_required_by_planned_participation(self):
        with self.app.store.connect() as db:profile=db.execute('SELECT profile_id FROM boats WHERE id=?',(self.boats[0],)).fetchone()[0]
        _,boat=self.request('/api/boat-profiles?id='+str(profile))
        status,result=self.request('/api/boat-profiles',{**boat,'id':profile,'ratings':{'TACIL':700}})
        self.assertEqual(status,400,result)
        self.assertEqual(result['handicap_issues'][0]['required_key'],'TACIM')

    def test_series_add_is_atomic_across_different_handicap_systems(self):
        _,new=self.request('/api/series',{'name':'Mixed','year':2026,'round_labels':'R1-R2'})
        with self.app.store.connect(write=True) as db:
            rounds=hierarchy.structure(db,new['id'])
            hierarchy.set_system(db,rounds[1]['id'],'gph',self.user)
        profile=self.profile({'TACIM':600})
        heat=rounds[0]['courses'][0]['heats'][0]
        status,result=self.request('/api/heats/boats/move',{'heat_id':heat['id'],'scope':'series','action':'add','profile_ids':[profile]})
        self.assertEqual(status,400,result)
        self.assertEqual(result['handicap_issues'][0]['required_key'],'GPH')
        with self.app.store.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM boats WHERE series_id=?',(new['id'],)).fetchone()[0],0)
