"""No loose timing UI; upgrade keeps boat drafts and the normal completion flow."""
import os
import json
from uuid import uuid4
from playwright.sync_api import sync_playwright,expect

base=os.environ['ONSDAGSBANE_TEST_BASE_URL'].rstrip('/')
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page(bypass_csp=True,timezone_id='Europe/Copenhagen',viewport={'width':1440,'height':1000})
    page.set_default_timeout(10000)
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    def api(path,data=None):return page.evaluate("async ({path,data})=>(await import('/core/api.js')).api(path,data)",{'path':path,'data':data})
    page.goto(base);page.locator('[data-action=login]').click();page.locator('.preview-login').click();page.locator('.app-nav').wait_for()
    series=api('/api/series',{'name':'Boat only '+uuid4().hex[:8],'year':2026,'round_labels':'R1'})['id']
    round=api(f'/api/workspaces/series/{series}')['rounds'][0]
    ids=[]
    for i in range(2):
        boat=api('/api/boat-profiles',{'name':f'Boat only {i}','skipper':'S','sail_number':uuid4().hex[:8],'boat_type':'X','ratings':{'TACIM':600}})['id']
        api('/api/heats/boats/move',{'heat_id':round['courses'][0]['heats'][0]['id'],'scope':'round','action':'add','profile_ids':[boat]})
        ids.append(next(b['id'] for b in api(f'/api/workspaces/series/{series}')['registrations'] if b['profile_id']==boat))
    key=f'ob_timing_round:{series}:{round["id"]}'
    draft={'key':f'boat:{ids[0]}','boatId':ids[0],'mode':'new','instant':'2026-05-06T15:00:00+02:00','automatic':True,'clockOffset':0,'request_id':uuid4().hex,'status':'FIN','penalty':0,'capture_id':-999,'capturing':True}
    orphan={'key':'loose:legacy','boatId':None,'instant':'2026-05-06T14:59:00+02:00','request_id':uuid4().hex}
    page.evaluate('({key,value})=>localStorage.setItem(key,JSON.stringify(value))',{'key':key,'value':{'drafts':{draft['key']:draft,orphan['key']:orphan},'pins':[ids[0]],'activeKey':draft['key']}})
    page.evaluate("async path=>(await import('/core/router.js')).navigate(path)",f'/rounds/{round["uid"]}/timing')
    expect(page.locator('#finish-time')).to_have_value('15:00:00')
    assert page.locator('[data-timing-action=capture],[data-timing-action=manual],#timing-captures').count()==0
    assert 'Tid uden båd' not in page.locator('body').inner_text()
    cached=page.evaluate('key=>JSON.parse(localStorage.getItem(key))',key)
    assert list(cached['drafts'])==[draft['key']] and 'capture_id' not in cached['drafts'][draft['key']]
    assert page.evaluate('key=>JSON.parse(localStorage.getItem(key))["loose:legacy"].instant',key+':retired-loose')==orphan['instant']
    page.locator('#active-measure-form button[type=submit]').click();page.locator(f'[data-measured="{ids[0]}"]').wait_for()
    page.locator(f'[data-measure="{ids[1]}"]').click()
    page.locator('#finish-time').fill('15:05:00');page.locator('#finish-date').fill('06-05-2026')
    page.locator('#active-measure-form button[type=submit]').click();page.locator(f'[data-measured="{ids[1]}"]').wait_for()
    page.reload();expect(page.locator('#measured-boats [data-measured]')).to_have_count(2)
    page.locator('[data-timing-action=clock-plus]').click()
    page.wait_for_function("document.querySelector('.timing-page') && !document.querySelector('.timing-page').inert")
    data=api(f'/api/workspaces/rounds/{round["uid"]}')
    assert data['entries'][0]['instant']=='2026-05-06T15:01:00+02:00'
    assert data['entries'][1]['instant']=='2026-05-06T15:05:00+02:00'
    page.locator('[data-configure-course=stor]').click()
    page.locator('#race-form [name=race_date]').fill('06-05-2026');page.locator('#race-form [name=start_time]').fill('14:00:00')
    page.locator('#race-form [data-mark=A]').click();page.locator('#race-form [data-mark=D]').click()
    page.locator('#race-form button.primary').click();page.locator('#dialog').wait_for(state='hidden')
    page.locator('[data-action=complete-round]').click();page.locator('#round-completion-form button.primary').click();page.locator('#dialog').wait_for(state='hidden')
    assert api(f'/api/workspaces/rounds/{round["uid"]}')['round']['completed']
    assert not errors,errors
    browser.close()
    print('Boat-only timing OK: no loose controls, legacy draft recovery, direct automatic/manual timing, reload, clock correction, setup and completion')
