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
    def open_course(course):
        page.get_by_role('button',name='Indstil baner',exact=True).click()
        page.locator('#race-course').select_option(course)
        page.wait_for_function("course=>document.querySelector('#race-form')?.dataset.course===course && !document.querySelector('#race-form').inert",arg=course)
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
    assert page.get_by_role('button',name='Indstil baner',exact=True).count()==1
    assert page.locator('#timing-context button,[data-round-starts]').count()==0
    open_course('lille')
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
    expect(page.locator('#race-save-status')).to_have_text('Gemt automatisk')
    page.locator('#race-form [data-action=close-dialog]').click();page.locator('#dialog').wait_for(state='hidden')
    open_course('lille')
    expect(page.locator('#race-form [name=start_time]')).to_have_value('14:00:00')
    expect(page.locator('#race-wind')).to_have_value('L')
    assert page.evaluate("async()=>(await import('/core/state.js')).runtime.route")==['A']
    expect(page.locator('#race-save-status')).to_have_text('Gemt automatisk')
    page.locator('#race-form [data-action=close-dialog]').click();page.locator('#dialog').wait_for(state='hidden')
    result=api(f'/api/workspaces/rounds/{first["uid"]}')
    assert len(result['courses'])==2 and all(r['start']=='2026-05-06T14:00:00+02:00' for r in result['courses'])
    assert all(e['instant']=='2026-05-06T15:00:00+02:00' and e['corrected'] is not None for e in result['entries'])
    original_instants={e['boat_id']:e['instant'] for e in result['entries']}
    assert page.locator('[data-timing-course=stor] .course-mark').all_text_contents()==['A','D']
    assert page.locator('[data-timing-course=lille] .course-mark').all_text_contents()==['A']
    assert page.locator('#timing-context sub').count()==0
    expect(page.locator('#timing-rounding-warning')).not_to_be_visible()
    # Warn before and after autosave when the shared A changes rounding side.
    open_course('lille')
    page.locator('#race-form [data-mark=B]').click()
    expect(page.locator('#race-rounding-warning')).to_be_visible()
    expect(page.locator('#race-rounding-warning')).to_contain_text('A: Stor bane bagbord; lille bane styrbord.')
    expect(page.locator('#race-save-status')).to_have_text('Gemt automatisk')
    page.locator('#race-form [data-action=close-dialog]').click()
    expect(page.locator('#timing-rounding-warning')).to_be_visible()
    page.reload()
    expect(page.locator('#timing-rounding-warning')).to_contain_text('A: Stor bane bagbord; lille bane styrbord.')
    page.screenshot(path='build/onsdagsbane/rounding-warning.png',full_page=True)
    open_course('lille')
    page.locator('#race-form [data-remove-mark="1"]').click()
    expect(page.locator('#race-rounding-warning')).not_to_be_visible()
    expect(page.locator('#race-save-status')).to_have_text('Gemt automatisk')
    page.locator('#race-form [data-action=close-dialog]').click()
    expect(page.locator('#timing-rounding-warning')).not_to_be_visible()
    assert page.locator('.app-nav').get_by_role('link',name='Måltagning',exact=True).count()==1
    assert page.locator('[data-action=day-settings]').count()==0
    open_course('stor')
    assert page.locator('#race-form button[type=submit],#race-form button.primary').count()==0
    page.locator('#race-wind').select_option('L')
    page.locator('#race-form [data-mark=B]').click()
    expect(page.locator('#race-save-status')).to_have_text('Gemt automatisk')
    page.locator('#race-form [data-action=close-dialog]').click()
    edited=api(f'/api/workspaces/rounds/{first["uid"]}')
    assert {e['boat_id']:e['instant'] for e in edited['entries']}==original_instants
    assert next(r for r in edited['courses'] if r['course']=='stor')['rating_key']=='TACIL'
    assert next(r for r in edited['courses'] if r['course']=='lille')['route']=='["A"]'
    # Manual distance clears the route; selecting marks takes over again.
    open_course('stor')
    page.locator('#race-form [name=distance]').fill('6,125')
    expect(page.locator('#route-preview')).to_have_text('N/A')
    expect(page.locator('#race-save-status')).to_have_text('Gemt automatisk')
    page.locator('#race-form [data-action=close-dialog]').click()
    expect(page.locator('[data-timing-course=stor]')).to_contain_text('N/A')
    manual=api(f'/api/workspaces/rounds/{first["uid"]}')
    course=next(r for r in manual['courses'] if r['course']=='stor')
    assert course['route']=='[]' and course['distance']==6.125
    assert {e['boat_id']:e['instant'] for e in manual['entries']}==original_instants
    page.reload()
    expect(page.locator('[data-timing-course=stor]')).to_contain_text('N/A')
    open_course('stor')
    expect(page.locator('#race-form [name=distance]')).to_have_value('6.125')
    page.locator('#race-form [data-mark=A]').click()
    expect(page.locator('#route-preview')).to_contain_text('A')
    expect(page.locator('#race-save-status')).to_have_text('Gemt automatisk')
    geometry=api('/api/geometry',{'route':['A']})['distance']
    assert abs(float(page.locator('#race-form [name=distance]').input_value())-geometry)<.00051
    page.locator('#race-form [data-action=close-dialog]').click()
    restored=api(f'/api/workspaces/rounds/{first["uid"]}')
    course=next(r for r in restored['courses'] if r['course']=='stor')
    assert course['route']=='["A"]' and course['distance']==geometry
    assert {e['boat_id']:e['instant'] for e in restored['entries']}==original_instants
    # A lost save response can be retried by closing; it never creates a duplicate.
    open_course('stor')
    def lose_response(route):
        assert route.fetch().status==200
        route.abort()
    page.route('**/api/course-settings',lose_response,times=1)
    page.locator('#race-form [data-mark=D]').click()
    expect(page.locator('#race-save-status')).to_contain_text('ikke gemt')
    page.locator('#race-form [data-action=close-dialog]').click()
    page.locator('#dialog').wait_for(state='hidden')
    assert len(api(f'/api/workspaces/rounds/{first["uid"]}')['courses'])==2
    page.get_by_role('link',name='Se resultater',exact=True).click()
    page.locator('.course-mark').first.wait_for()
    assert '/results?' in page.url and '/rounds/' not in page.url
    expect(page.locator('#draft-toggle')).to_be_checked()
    page.reload();page.locator('.course-mark').first.wait_for()
    expect(page.locator('#draft-toggle')).to_be_checked()
    assert page.locator('.course-mark sub').count()==0
    assert page.locator('.course-mark[aria-label]').count()>0
    go(f'/rounds/{second["uid"]}/timing');open_course('lille')
    expect(page.locator('#race-form [name=start_time]')).to_have_value('18:35:00')
    page.locator('#race-form [name=start_time]').fill('13:30:00')
    page.locator('#race-form [data-action=close-dialog]').click()
    open_course('lille')
    expect(page.locator('#race-form [name=start_time]')).to_have_value('18:35:00')
    assert not errors,errors
    browser.close()
    print('Course setup OK: automatic create/edit, switching, finish preservation, lost-response retry, no duplicate workflow or mark subscripts, other rounds isolated')
