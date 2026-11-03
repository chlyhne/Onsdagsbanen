"""V2 end-to-end workflow. Run against an isolated --preview-admin server."""
import os
from datetime import datetime,timedelta
from time import time
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright,expect

base=os.environ['ONSDAGSBANE_TEST_BASE_URL'].rstrip('/')
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page(bypass_csp=True,viewport={'width':1600,'height':1000})
    page.set_default_timeout(12000)
    errors=[];page.on('pageerror',lambda error: errors.append(str(error)))
    def api(path,data=None):
        return page.evaluate("async ({path,data})=>(await import('/core/api.js')).api(path,data)",{'path':path,'data':data})
    def go(path):
        page.evaluate("async path=>(await import('/core/router.js')).navigate(path)",path)
    def saved():
        expect(page.locator('#fleet-save-status')).to_contain_text('Gemt')
    page.goto(base)
    page.get_by_role('heading',name='Resultater',exact=True).wait_for()
    page.locator('[data-action=login]').click();page.locator('.preview-login').click()
    page.get_by_role('link',name='Bådregister',exact=True).wait_for()

    # Global creation has no implicit series registration.
    go('/boats');page.locator('[data-new-profile]').click()
    name=f'V2 Ægir {int(time())}'
    for key,value in {'name':name,'skipper':'Søren Øster','sail_number':f'DEN {int(time())}','boat_type':'X-332','TACIL':'700','TACIM':'600','TACIH':'500'}.items():
        page.locator(f'#boat-profile-form [name={key}]').fill(value)
    page.locator('#boat-profile-form button[type=submit]').click()
    page.locator('#dialog').wait_for(state='hidden')
    catalog=api('/api/boat-profiles');boat=next(b for b in catalog if b['name']==name)
    assert not boat['series'] and not boat['assignments']

    # Series creation and explicit series workspace.
    go('/series');page.locator('[data-action=new-series]').click()
    page.locator('#series-form [name=name]').fill(f'V2 sæson {int(time())}')
    page.locator('#series-form [name=year]').fill(str(datetime.now().year))
    page.locator('#series-form [name=round_labels]').fill('R1–R3')
    page.locator('#series-form button.primary').click();page.locator('#dialog').wait_for(state='hidden')
    page.locator('.workspace-table tbody tr').first.wait_for()
    series_id=int(page.url.split('/series/')[1].split('/')[0].split('?')[0])
    workspace=api(f'/api/workspaces/series/{series_id}')
    assert len(workspace['rounds'])==3 and not workspace['registrations']
    page.locator(f'a[data-nav][href="/series/{series_id}/assignments"]').click()
    page.locator('#fleet-search').fill(name)
    page.locator(f'#fleet-right-list [data-fleet-profile="{boat["id"]}"]').check()
    # Choosing the destination after selecting source boats keeps the selection.
    first_heat=next(h for c in workspace['rounds'][0]['courses'] for h in c['heats'] if h['name']=='Stor bane 1')
    other_heat=next(h for c in workspace['rounds'][0]['courses'] for h in c['heats'] if h['name']=='Stor bane 2')
    for heat in (other_heat,first_heat):
        button=page.locator(f'[data-fleet-heat="{heat["id"]}"][data-fleet-scope=series]')
        if not button.is_visible():
            page.locator('.fleet-tree-heat').filter(has=button).locator('summary').click()
        button.click()
        expect(page.locator(f'#fleet-right-list [data-fleet-profile="{boat["id"]}"]')).to_be_checked()
        expect(page.locator('[data-fleet-action=add]')).to_be_enabled()
        assert page.locator('#fleet-search').input_value()==name
    def lose_response(route):
        assert route.fetch().status==200
        route.abort()
    page.route('**/api/heats/boats/move',lose_response,times=1)
    page.locator('[data-fleet-action=add]').click()
    expect(page.locator('#fleet-save-status')).to_contain_text('ikke bekræftes')
    assert page.locator('#fleet-left-list .fleet-boat').count()==0
    page.locator('[data-fleet-action=add]').click();saved()
    assert page.locator('dialog[open]').count()==0
    before_url=page.url;page.reload();page.locator('#fleet-search').wait_for()
    assert page.locator('#fleet-search').input_value()==name and page.url==before_url
    workspace=api(f'/api/workspaces/series/{series_id}')
    assert all(r['status']['participants']==1 for r in workspace['rounds'])
    registration=workspace['registrations'][0]

    # Left selections survive switching rounds where the boat is a member,
    # but cannot remove an unrelated boat after switching to another heat.
    page.locator(f'#fleet-left-list [data-fleet-profile="{boat["id"]}"]').check()
    second_round_heat=next(h for c in workspace['rounds'][1]['courses'] for h in c['heats'] if h['name']=='Stor bane 1')
    page.locator(f'[data-fleet-heat="{second_round_heat["id"]}"][data-fleet-scope=round]').click()
    expect(page.locator(f'#fleet-left-list [data-fleet-profile="{boat["id"]}"]')).to_be_checked()
    expect(page.locator('[data-fleet-action=remove]')).to_be_enabled()

    # Allocate a move, with explicit opt-in; default pool shrinks.
    target=next(h for c in workspace['rounds'][0]['courses'] for h in c['heats'] if h['name']=='Stor bane 2')
    node=page.locator('.fleet-tree-heat').filter(has=page.locator(f'[data-fleet-heat="{target["id"]}"]'))
    node.locator('summary').click()
    page.locator(f'[data-fleet-heat="{target["id"]}"][data-fleet-scope=series]').click()
    expect(page.locator('[data-fleet-action=remove]')).to_be_disabled()
    expect(page.locator('#fleet-left-count')).to_contain_text('0 markeret')
    assert page.locator(f'#fleet-right-list [data-fleet-profile="{boat["id"]}"]').count()==0
    page.locator('#fleet-include-assigned').check()
    page.locator(f'#fleet-right-list [data-fleet-profile="{boat["id"]}"]').check()
    page.locator('[data-fleet-action=add]').click();saved()
    assert all(next(h for c in r['courses'] for h in c['heats'] if h['name']=='Stor bane 2')['boat_ids']==[registration['id']] for r in api(f'/api/workspaces/series/{series_id}')['rounds'])
    page.screenshot(path='build/onsdagsbane/v2-assignments.png')

    # A race is one workspace across setup, start lists, timing and results.
    round=workspace['rounds'][0]
    extra=api('/api/boat-profiles',{'name':name+' B','skipper':'Anden skipper','sail_number':f'DEN B{int(time())}','boat_type':'X-332','ratings':{'TACIM':610}})['id']
    extra_heat=next(h for c in round['courses'] for h in c['heats'] if h['name']=='Stor bane 2')
    api('/api/heats/boats/move',{'heat_id':extra_heat['id'],'scope':'round','action':'add','profile_ids':[extra]})
    go(f'/rounds/{round["uid"]}/timing')
    page.locator('#unmeasured-boats').wait_for()
    assert page.url.endswith('/timing')
    page.locator(f'[data-pin="{registration["id"]}"]').click()
    page.locator('#boat-search').fill('No matching boat')
    expect(page.locator(f'[data-measure="{registration["id"]}"]')).to_be_visible()
    page.locator('#boat-search').fill('')
    page.locator(f'[data-measure="{registration["id"]}"]').click()
    pending=page.locator('#finish-time').input_value()
    page.reload();page.locator('#finish-time').wait_for()
    assert page.locator('#finish-time').input_value()==pending
    page.locator('#active-measure-form button[type=submit]').click()
    page.locator(f'[data-measured="{registration["id"]}"]').wait_for()
    extra_registration=next(b for b in api(f'/api/workspaces/series/{series_id}')['registrations'] if b['profile_id']==extra)
    assert page.locator('[data-timing-action=capture],[data-timing-action=manual],#timing-captures').count()==0
    page.locator(f'[data-measure="{extra_registration["id"]}"]').click()
    page.locator('#active-measure-form button[type=submit]').click()
    page.locator(f'[data-measured="{extra_registration["id"]}"]').wait_for()
    page.reload();page.locator(f'[data-measured="{registration["id"]}"]').wait_for()
    before_setup=api(f'/api/workspaces/rounds/{round["uid"]}')
    assert not before_setup['courses'] and len(before_setup['entries'])==2
    assert all(e['instant'] and e['corrected'] is None for e in before_setup['entries'])
    page.screenshot(path='build/onsdagsbane/timing-before-setup.png')
    page.locator('[data-action=complete-round]').click()
    expect(page.locator('#toast')).to_contain_text('Indstil banerne')
    assert not api(f'/api/workspaces/rounds/{round["uid"]}')['round']['completed']

    # Configure this exact round from timing, after both observations are saved.
    page.locator('[data-round-starts]').click();page.locator('[data-action=race-today]').click()
    today=datetime.now(ZoneInfo('Europe/Copenhagen')).strftime('%d-%m-%Y')
    assert page.locator('#round-starts-form [name=race_date]').input_value()==today
    page.locator('#round-starts-form button.primary').click();page.locator('#dialog').wait_for(state='hidden')
    page.locator('[data-configure-course=stor]').click()
    assert page.locator('#race-round').input_value()==str(round['id'])
    assert page.locator('#race-round option').count()==1
    start=datetime.now(ZoneInfo('Europe/Copenhagen'))-timedelta(minutes=10)
    page.locator('#race-form [name=start_time]').fill(start.strftime('%H:%M:%S'))
    page.locator('#race-form [data-mark=A]').click();page.locator('#race-form [data-mark=D]').click()
    page.locator('#race-form button.primary').click();page.locator('#dialog').wait_for(state='hidden')
    page.locator(f'[data-measured="{registration["id"]}"]').wait_for()
    after_setup=api(f'/api/workspaces/rounds/{round["uid"]}')
    assert [e['instant'] for e in after_setup['entries']]==[e['instant'] for e in before_setup['entries']]
    assert all(e['corrected'] is not None for e in after_setup['entries'])
    page.locator('[data-action=complete-round]').click()
    page.locator('#round-completion-form button.primary').click();page.locator('#dialog').wait_for(state='hidden')
    w=api(f'/api/workspaces/rounds/{round["uid"]}')
    assert w['status']['phase']=='finished' and not w['status']['published']
    page.locator('[data-action=publish]').click();page.locator('#publish-form button.primary').click()
    page.locator('#dialog').wait_for(state='hidden')
    assert api(f'/api/workspaces/rounds/{round["uid"]}')['status']['published']
    go(f'/rounds/{round["uid"]}/results')
    page.locator('#class-select').select_option('Stor bane 2')
    page.locator('.daily-results tbody tr').first.wait_for()
    assert page.locator('#result-view').count()==0
    assert page.locator('.daily-results tbody tr').count()==2
    page.reload();page.locator('.daily-results tbody tr').first.wait_for()
    go(f'/rounds/{round["uid"]}/history');page.locator('.audit-entry').first.wait_for()

    # Corrections are reached from this round and always require a preview.
    go(f'/rounds/{round["uid"]}/setup')
    page.locator('[data-action=day-settings]').click()
    assert page.locator('#day-settings-form [name=date] option').count()==1
    page.locator('#day-settings-form [data-mark=A]').click()
    page.locator('#day-settings-form [data-mark=D]').click()
    page.locator('[data-action=day-preview]').click()
    expect(page.locator('#day-token')).not_to_have_value('')
    page.locator('#day-settings-form [data-action=close-dialog]').click()
    page.locator('[data-calculation-race]').click()
    page.locator('#calculation-form').wait_for()
    page.locator('#calculation-form [data-action=close-dialog]').click()

    # Editing shared handicap data never rewrites a historical participation.
    prior=api(f'/api/workspaces/rounds/{round["uid"]}')['entries'][0]
    go('/boats');page.locator('#registry-search').fill(name)
    page.locator(f'[data-profile-edit="{boat["id"]}"]').click()
    page.locator('#boat-profile-form [name=TACIM]').fill('650,5')
    page.locator('#boat-profile-form button[type=submit]').click();page.locator('#dialog').wait_for(state='hidden')
    current=api(f'/api/workspaces/rounds/{round["uid"]}')['entries'][0]
    assert current['handicap_set_id']==prior['handicap_set_id'] and current['corrected']==prior['corrected']
    assert api(f'/api/boat-profiles?id={boat["id"]}')['ratings']['TACIM']==650.5
    page.locator(f'[data-profile-delete="{boat["id"]}"]').click()
    page.locator('.boat-delete-races li').first.wait_for()
    assert page.locator('#boat-delete-form button[type=submit]').count()==0
    assert page.locator('.boat-delete-races li').count()==3
    page.locator('#boat-delete-form [data-action=close-dialog]').click()
    go(f'/series/{series_id}/boats')
    assert page.locator(f'[data-unregister="{registration["id"]}"]').is_disabled()

    # Direct URLs work in a fresh anonymous visitor and never expose admin workspaces.
    anonymous=browser.new_page();anonymous.goto(base+f'/results?series={series_id}&course=stor&class=all&view=overall')
    anonymous.locator('tbody tr').first.wait_for()
    assert anonymous.locator('.app-nav').count()==0
    assert name in anonymous.locator('body').inner_text()
    page.set_viewport_size({'width':390,'height':844});go('/boats')
    assert page.locator('body').evaluate('(el)=>el.scrollWidth<=innerWidth')
    page.screenshot(path='build/onsdagsbane/v2-mobile.png',full_page=True)
    assert not errors,errors
    browser.close()
    print('V2 browser OK: public results, global boat CRUD, series workspace, routed allocation/reload, setup, timing, completion, publication, history, snapshot preservation, mobile')
