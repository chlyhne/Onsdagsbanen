"""Synchronize the reference clock while preserving all manual times."""
import os
from uuid import uuid4
from datetime import datetime
from playwright.sync_api import sync_playwright,expect

base=os.environ['ONSDAGSBANE_TEST_BASE_URL'].rstrip('/')
def epoch(value):return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page(bypass_csp=True,timezone_id='Europe/Copenhagen',viewport={'width':1600,'height':1000})
    page.set_default_timeout(10000)
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    def api(path,data=None):return page.evaluate("async ({path,data})=>(await import('/core/api.js')).api(path,data)",{'path':path,'data':data})
    def detail():return api(f'/api/workspaces/rounds/{round["uid"]}')
    def clock(action):
        with page.expect_response('**/api/rounds/*/clock') as response:page.locator(f'[data-timing-action=clock-{action}]').click()
        assert response.value.status==200
        expect(page.locator('.timing-page')).not_to_have_attribute('inert','')
    page.goto(base);page.locator('[data-action=login]').click();page.locator('.preview-login').click();page.locator('.app-nav').wait_for()
    series=api('/api/series',{'name':'Clock '+uuid4().hex[:8],'year':2026,'round_labels':'R1'})['id']
    round=api(f'/api/workspaces/series/{series}')['rounds'][0]
    heat=round['courses'][0]['heats'][0]
    for i in range(3):
        boat=api('/api/boat-profiles',{'name':f'Clock {i}','skipper':'S','sail_number':uuid4().hex[:8],'boat_type':'X','ratings':{'TACIM':600}})['id']
        api('/api/heats/boats/move',{'heat_id':heat['id'],'scope':'round','action':'add','profile_ids':[boat]})
    registrations=api(f'/api/workspaces/series/{series}')['registrations']
    page.evaluate("async path=>(await import('/core/router.js')).navigate(path)",f'/rounds/{round["uid"]}/timing')
    # A deterministic display twenty seconds before the next minute.
    page.evaluate("async()=>{const {state}=await import('/core/state.js');const instant=Date.parse('2026-05-06T14:20:40+02:00');Date.now=()=>instant;state.session.offset=0;}")
    page.locator(f'[data-measure="{registrations[0]["id"]}"]').click()
    page.locator('#active-measure-form button[type=submit]').click();page.locator(f'[data-measured="{registrations[0]["id"]}"]').wait_for()
    page.locator(f'[data-measure="{registrations[1]["id"]}"]').click()
    page.locator('#finish-time').fill('14:20:10')
    page.locator('#active-measure-form button[type=submit]').click();page.locator(f'[data-measured="{registrations[1]["id"]}"]').wait_for()
    page.locator(f'[data-measure="{registrations[2]["id"]}"]').click()
    before=detail();auto=before['entries'][0]['instant'];manual=before['entries'][1]['instant']
    assert before['entries'][0]['clock_automatic'] and not before['entries'][1]['clock_automatic']
    clock('snap')
    after=detail();delta=after['round']['clock_offset']
    assert 15<delta<25
    assert abs(epoch(after['entries'][0]['instant'])-epoch(auto)-delta)<0.01
    assert after['entries'][1]['instant']==manual
    expect(page.locator('#finish-time')).to_have_value('14:21:00')
    clock('plus');expect(page.locator('#finish-time')).to_have_value('14:22:00')
    clock('minus');expect(page.locator('#finish-time')).to_have_value('14:21:00')
    # Explicit manual input is manual even when it matches the displayed digits.
    page.locator('#finish-time').fill('14:21:00')
    clock('plus');expect(page.locator('#finish-time')).to_have_value('14:21:00')
    page.locator('#finish-time').fill('14:30:00')
    clock('plus');expect(page.locator('#finish-time')).to_have_value('14:30:00')
    page.locator('#active-measure-form button[type=submit]').click();page.locator(f'[data-measured="{registrations[2]["id"]}"]').wait_for()
    clock('plus')
    assert detail()['entries'][2]['instant']=='2026-05-06T14:30:00+02:00'
    assert page.locator('[data-timing-action=capture],[data-timing-action=manual],#timing-captures').count()==0
    offset=detail()['round']['clock_offset']
    page.reload();page.locator('#live-clock').wait_for()
    assert detail()['round']['clock_offset']==offset
    assert detail()['entries'][1]['instant']==manual
    assert detail()['entries'][2]['instant']=='2026-05-06T14:30:00+02:00'
    page.screenshot(path='build/onsdagsbane/reference-clock.png',full_page=True)
    assert not errors,errors
    browser.close()
    print('Clock UI OK: nearest minute, plus/minus, saved and active boat times, manual immunity, removed loose timing and reload')
