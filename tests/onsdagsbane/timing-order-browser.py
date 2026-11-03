"""Newest finish first across both courses, edits, midnight, reload and clock changes."""
import os
from uuid import uuid4
from playwright.sync_api import sync_playwright

base=os.environ['ONSDAGSBANE_TEST_BASE_URL'].rstrip('/')
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page(bypass_csp=True,viewport={'width':1440,'height':1000})
    page.set_default_timeout(10000)
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    def api(path,data=None):return page.evaluate("async ({path,data})=>(await import('/core/api.js')).api(path,data)",{'path':path,'data':data})
    def order(expected):
        page.wait_for_function("expected=>JSON.stringify([...document.querySelectorAll('#measured-boats [data-measured]')].map(el=>Number(el.dataset.measured)))===JSON.stringify(expected)",arg=expected)
    page.goto(base);page.locator('[data-action=login]').click();page.locator('.preview-login').click();page.locator('.app-nav').wait_for()
    series=api('/api/series',{'name':'Timing order '+uuid4().hex[:8],'year':2026,'round_labels':'R1'})['id']
    round=api(f'/api/workspaces/series/{series}')['rounds'][0]
    ids=[]
    for index in range(4):
        course='lille' if index%2 else 'stor'
        heat=next(c for c in round['courses'] if c['course']==course)['heats'][0]
        profile=api('/api/boat-profiles',{'name':['OrderAlpha','OrderBravo','OrderCharlie','OrderDelta'][index],'skipper':'S','sail_number':uuid4().hex[:8],'boat_type':'X','ratings':{'TACIM':600}})['id']
        api('/api/heats/boats/move',{'heat_id':heat['id'],'scope':'round','action':'add','profile_ids':[profile]})
        ids.append(next(b['id'] for b in api(f'/api/workspaces/series/{series}')['registrations'] if b['profile_id']==profile))
    for index,instant in enumerate(('2026-05-06T23:58:00+02:00','2026-05-06T22:01:00Z','2026-05-06T23:59:00+02:00',None)):
        api(f'/api/rounds/{round["id"]}/finish',{'boat_id':ids[index],'status':'FIN' if instant else 'DNC','instant':instant,'automatic':index==2,'request_id':uuid4().hex})
    page.evaluate("async path=>(await import('/core/router.js')).navigate(path)",f'/rounds/{round["uid"]}/timing')
    order([ids[1],ids[2],ids[0],ids[3]])
    page.locator(f'[data-edit-finish="{ids[0]}"]').click()
    page.locator('#finish-time').fill('00:02:00');page.locator('#finish-date').fill('07-05-2026')
    page.locator('#active-measure-form button[type=submit]').click()
    order([ids[0],ids[1],ids[2],ids[3]])
    page.locator('#boat-search').fill('OrderBravo');order([ids[1]])
    page.locator('#boat-search').fill('');order([ids[0],ids[1],ids[2],ids[3]])
    page.reload();order([ids[0],ids[1],ids[2],ids[3]])
    api(f'/api/rounds/{round["id"]}/clock',{'offset':240,'previous_offset':0})
    page.reload();order([ids[2],ids[0],ids[1],ids[3]])
    for course in ('stor','lille'):
        api('/api/races',{'series_id':series,'round_id':round['id'],'course':course,'mode':'normal','rating_key':'TACIM','start':'2026-05-06T18:30:00+02:00','route':['A','D']})
    page.reload();order([ids[2],ids[0],ids[1],ids[3]])
    assert not errors,errors
    browser.close()
    print('Timing order OK: newest first across courses/timezones/midnight, after edits, filtering, reload, clock adjustment and course setup; untimed statuses last')
