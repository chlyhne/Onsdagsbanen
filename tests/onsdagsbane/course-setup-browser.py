"""Regression: configure both courses after timing, retaining edits while switching."""
import os
from uuid import uuid4
from playwright.sync_api import sync_playwright, expect

base=os.environ['ONSDAGSBANE_TEST_BASE_URL'].rstrip('/')
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page(bypass_csp=True,viewport={'width':1440,'height':1000})
    page.set_default_timeout(10000)
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    def api(path,data=None):
        return page.evaluate("async ({path,data})=>(await import('/core/api.js')).api(path,data)",{'path':path,'data':data})
    def go(path):
        page.evaluate("async path=>(await import('/core/router.js')).navigate(path)",path)
    page.goto(base)
    page.locator('[data-action=login]').click();page.locator('.preview-login').click()
    page.locator('.app-nav').wait_for()
    series=api('/api/series',{'name':'Course switch '+uuid4().hex[:8],'year':2026,'round_labels':'R1-R2'})['id']
    workspace=api(f'/api/workspaces/series/{series}')
    first,second=workspace['rounds']
    for course in ('stor','lille'):
        boat=api('/api/boat-profiles',{'name':'Switch '+course,'skipper':'Søren','sail_number':uuid4().hex[:8],'boat_type':'X','ratings':{'TACIL':700,'TACIM':600}})['id']
        heat=next(c for c in first['courses'] if c['course']==course)['heats'][0]
        api('/api/heats/boats/move',{'heat_id':heat['id'],'scope':'series','action':'add','profile_ids':[boat]})
        registration=next(b for b in api(f'/api/workspaces/series/{series}')['registrations'] if b['profile_id']==boat)
        api(f'/api/rounds/{first["id"]}/finish',{'boat_id':registration['id'],'instant':'2026-05-06T15:00:00+02:00','request_id':uuid4().hex})
    go(f'/rounds/{first["uid"]}/timing')
    page.locator('[data-configure-course=lille]').click()
    page.locator('#race-form [name=race_date]').fill('06-05-2026')
    page.locator('#race-form [name=start_time]').fill('14:00:00')
    page.locator('#race-wind').select_option('L')
    page.locator('#race-form [name=reference]').fill('1005')
    page.locator('#race-form [data-mark=A]').click()
    page.locator('#race-course').select_option('stor')
    expect(page.locator('#race-form [name=race_date]')).to_have_value('06-05-2026')
    page.locator('#race-form [name=start_time]').fill('14:00:00')
    page.locator('#race-form [name=mode]').select_option('pursuit')
    page.locator('#race-form [data-mark=A]').click();page.locator('#race-form [data-mark=D]').click()
    page.locator('#race-course').select_option('lille')
    expect(page.locator('#race-form [name=start_time]')).to_have_value('14:00:00')
    expect(page.locator('#race-wind')).to_have_value('L')
    expect(page.locator('#race-form [name=reference]')).to_have_value('1005')
    assert page.evaluate("async()=>(await import('/core/state.js')).runtime.route")==['A']
    page.locator('#race-course').select_option('stor')
    expect(page.locator('#race-form [name=start_time]')).to_have_value('14:00:00')
    expect(page.locator('#race-form [name=mode]')).to_have_value('pursuit')
    assert page.evaluate("async()=>(await import('/core/state.js')).runtime.route")==['A','D']
    page.locator('#race-form button.primary').click();page.locator('#dialog').wait_for(state='hidden')
    page.locator('[data-configure-course=lille]').click()
    expect(page.locator('#race-form [name=start_time]')).to_have_value('14:00:00')
    expect(page.locator('#race-wind')).to_have_value('L')
    assert page.evaluate("async()=>(await import('/core/state.js')).runtime.route")==['A']
    page.locator('#race-form button.primary').click();page.locator('#dialog').wait_for(state='hidden')
    result=api(f'/api/workspaces/rounds/{first["uid"]}')
    assert len(result['courses'])==2 and all(r['start']=='2026-05-06T14:00:00+02:00' for r in result['courses'])
    assert all(e['instant']=='2026-05-06T15:00:00+02:00' and e['corrected'] is not None for e in result['entries'])
    go(f'/rounds/{second["uid"]}/timing');page.locator('[data-configure-course=lille]').click()
    expect(page.locator('#race-form [name=start_time]')).to_have_value('18:35:00')
    page.locator('#race-form [name=start_time]').fill('13:30:00')
    page.locator('#race-form [data-action=close-dialog]').click()
    page.locator('[data-configure-course=lille]').click()
    expect(page.locator('#race-form [name=start_time]')).to_have_value('18:35:00')
    assert not errors,errors
    browser.close()
    print('Course setup OK: both starts at 14:00, per-course edits survive switching and saving, finish instants preserved, other rounds and cancel isolated')
