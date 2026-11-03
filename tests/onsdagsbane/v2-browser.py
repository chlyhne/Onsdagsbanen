"""V2 end-to-end workflow. Run against an isolated --preview-admin server."""
import os
from datetime import datetime,timedelta
from time import time
from urllib.parse import urlsplit,quote
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
    expect(page.locator(f'[data-profile-history="{boat["id"]}"]')).to_be_disabled()
    page.locator(f'[data-profile-usage="{boat["id"]}"]').click()
    expect(page.locator('#dialog')).to_contain_text('Båden er ikke tilmeldt nogen serie eller ræs.')
    assert page.locator('#dialog a').count()==0
    page.locator('#dialog .dialog-actions [data-action=close-dialog]').click()

    # Series creation and explicit series workspace.
    go('/series');page.locator('[data-action=new-series]').click()
    page.locator('#series-form [name=name]').fill(f'V2 sæson {int(time())}')
    page.locator('#series-form [name=year]').fill(str(datetime.now().year))
    page.locator('#series-form [name=round_labels]').fill('R1–R3')
    page.locator('#series-form button.primary').click();page.locator('#dialog').wait_for(state='hidden')
    page.locator('#series-settings-form').wait_for()
    series_id=int(page.url.split('/series/')[1].split('/')[0].split('?')[0])
    workspace=api(f'/api/workspaces/series/{series_id}')
    assert len(workspace['rounds'])==3 and not workspace['registrations']
    assert page.locator('.app-nav a[aria-current=page]').inner_text()=='Serier'
    assert page.locator('#series-select').count()==0
    assert page.locator('.workspace-tabs').count()==0
    assert page.locator('[data-series-round]').count()==3
    expect(page.get_by_role('link',name='Løbsinddeling',exact=True)).to_be_visible()
    second_settings=page.locator(f'[data-series-round="{workspace["rounds"][1]["id"]}"]')
    second_settings.locator('summary').click()
    second_settings.locator('[data-round-starts]').click()
    page.locator('#round-starts-form [name=race_date]').fill('13-05-2026')
    page.locator('#round-starts-form [name=start_stor]').fill('18:31:00')
    page.locator('#round-starts-form button.primary').click();page.locator('#dialog').wait_for(state='hidden')
    expect(second_settings.locator('summary')).to_contain_text('13-05-2026')
    assert api(f'/api/workspaces/series/{series_id}')['rounds'][1]['courses'][0]['start_time']=='18:31:00'
    page.screenshot(path='build/onsdagsbane/series-settings.png',full_page=True)
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
    alternative=api('/api/series',{'name':name+' anden serie','year':2026,'round_labels':'Andet ræs'})['id']
    other_round=api(f'/api/workspaces/series/{alternative}')['rounds'][0]
    go(f'/rounds/{round["uid"]}/timing')
    page.locator('#unmeasured-boats').wait_for()
    assert '/timing?' in page.url
    expect(page.locator('#timing-series')).to_have_value(str(series_id))
    expect(page.locator('#timing-race')).to_have_value(str(round['id']))
    assert page.get_by_role('link',name='Serier',exact=True).count()==1
    assert page.locator('.workspace-breadcrumb').count()==0
    assert page.locator('nav[aria-label="Seriens arbejdsrum"]').count()==0
    assert page.locator('nav[aria-label="Ræsets arbejdsrum"]').count()==0
    assert page.locator('[data-action=historical-calculation],[data-calculation-race]').count()==0
    assert page.locator('.app-nav a[aria-current=page]').inner_text()=='Måltagning'
    assert page.locator('nav[aria-label="Ræsets arbejdsrum"] a').filter(has_text='Resultater').count()==0
    page.locator(f'[data-pin="{registration["id"]}"]').click()
    page.locator('#boat-search').fill('No matching boat')
    expect(page.locator(f'[data-measure="{registration["id"]}"]')).to_be_visible()
    page.locator('#boat-search').fill('')
    page.locator(f'[data-measure="{registration["id"]}"]').click()
    pending=page.locator('#finish-time').input_value()
    # Series and round selections are explicit and keep independent drafts.
    def timing_selected(series,round):
        page.wait_for_function("""({series,round})=>{
            const q=new URLSearchParams(location.search);
            return location.pathname==='/timing' && q.get('series')===String(series) && q.get('round')===round.uid &&
                document.querySelector('#timing-series')?.value===String(series) &&
                document.querySelector('#timing-race')?.value===String(round.id) &&
                document.querySelector('[data-configure-round]')?.dataset.configureRound===String(round.id);
        }""",arg={'series':series,'round':round})
    page.locator('#timing-series').select_option(str(alternative))
    timing_selected(alternative,other_round)
    assert page.locator('#timing-race option').count()==1
    assert 'Andet ræs' in page.locator('#timing-race').inner_text()
    assert page.locator('#active-measure-form').count()==0
    page.reload();timing_selected(alternative,other_round)
    page.go_back();timing_selected(series_id,round)
    expect(page.locator('#finish-time')).to_have_value(pending)
    page.locator('#timing-race').select_option(str(workspace['rounds'][1]['id']))
    timing_selected(series_id,workspace['rounds'][1])
    assert page.locator('#active-measure-form').count()==0
    page.go_back();timing_selected(series_id,round)
    expect(page.locator('#finish-time')).to_have_value(pending)
    # Choosing a different series in Results does not silently change timing.
    go(f'/results?series={alternative}')
    page.locator('.app-nav').get_by_role('link',name='Måltagning',exact=True).click()
    timing_selected(series_id,round)
    expect(page.locator('#finish-time')).to_have_value(pending)
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
    assert page.get_by_role('button',name='Indstil baner',exact=True).count()==1
    assert page.locator('#timing-context button,[data-round-starts]').count()==0
    page.get_by_role('button',name='Indstil baner',exact=True).click()
    today=datetime.now(ZoneInfo('Europe/Copenhagen')).strftime('%d-%m-%Y')
    page.locator('#race-form [name=race_date]').fill(today)
    assert page.locator('#race-round').input_value()==str(round['id'])
    assert page.locator('#race-round option').count()==1
    start=datetime.now(ZoneInfo('Europe/Copenhagen'))-timedelta(minutes=10)
    page.locator('#race-form [name=start_time]').fill(start.strftime('%H:%M:%S'))
    page.locator('#race-form [data-mark=A]').click();page.locator('#race-form [data-mark=D]').click()
    expect(page.locator('#race-save-status')).to_have_text('Gemt automatisk')
    page.locator('#race-form [data-action=close-dialog]').click();page.locator('#dialog').wait_for(state='hidden')
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
    expect(page.locator('#result-view')).to_have_value('daily')
    assert '/results?' in page.url and '/rounds/' not in page.url
    assert page.locator('.workspace-context').count()==0
    assert page.locator('.app-nav a[aria-current=page]').inner_text()=='Resultater'
    assert page.locator('.daily-results tbody tr').count()==2
    page.reload();page.locator('.daily-results tbody tr').first.wait_for()
    go(f'/rounds/{round["uid"]}/history');page.locator('.audit-entry').first.wait_for()

    # The same course setup remains available after setup and timing.
    go(f'/rounds/{round["uid"]}/setup')
    assert page.locator('[data-action=day-settings]').count()==0
    page.locator('[data-configure-course=stor]').click()
    assert page.locator('#route-preview .route-token').count()==2
    page.locator('#race-form [data-action=close-dialog]').click()

    assert page.locator('[data-calculation-race]').count()==0

    # Editing shared handicap data never rewrites a historical participation.
    prior=api(f'/api/workspaces/rounds/{round["uid"]}')['entries'][0]
    go('/boats');page.locator('#registry-search').fill(name)
    page.locator(f'[data-profile-edit="{boat["id"]}"]').click()
    page.locator('#boat-profile-form [name=TACIM]').fill('650,5')
    page.locator('#boat-profile-form button[type=submit]').click();page.locator('#dialog').wait_for(state='hidden')
    current=api(f'/api/workspaces/rounds/{round["uid"]}')['entries'][0]
    assert current['handicap_set_id']==prior['handicap_set_id'] and current['corrected']==prior['corrected']
    assert api(f'/api/boat-profiles?id={boat["id"]}')['ratings']['TACIM']==650.5
    usage_url=page.url
    page.locator(f'[data-profile-usage="{boat["id"]}"]').click()
    expect(page.locator('#dialog')).to_contain_text('3 ræs i 1 serie')
    assert page.locator('#dialog a').count()==0
    assert page.locator('.boat-usage-list li').count()==3
    assert page.url==usage_url
    page.locator('#dialog .dialog-actions [data-action=close-dialog]').click()
    page.locator(f'[data-profile-delete="{boat["id"]}"]').click()
    page.locator('.boat-delete-races li').first.wait_for()
    assert page.locator('#boat-delete-form button[type=submit]').count()==0
    assert page.locator('.boat-delete-races li').count()==3
    page.locator('#boat-delete-form [data-action=close-dialog]').click()
    go(f'/series/{series_id}/boats')
    assert page.url.endswith(f'/series/{series_id}')
    assert page.locator('#series-settings-form').count()==1
    assert page.locator('[data-unregister],.workspace-tabs').count()==0

    # Direct URLs work in a fresh anonymous visitor and never expose admin workspaces.
    anonymous=browser.new_page();anonymous.goto(base+f'/results?series={series_id}&course=stor&class=all&view=overall')
    anonymous.locator('tbody tr').first.wait_for()
    assert anonymous.locator('.app-nav').count()==0
    assert name in anonymous.locator('body').inner_text()
    page.set_viewport_size({'width':390,'height':844});go('/boats')
    assert page.locator('body').evaluate('(el)=>el.scrollWidth<=innerWidth')
    page.screenshot(path='build/onsdagsbane/v2-mobile.png',full_page=True)
    # Historical handicap corrections belong to the selected boat in the registry.
    go('/boats');page.locator('#registry-search').fill(name)
    before=api(f'/api/workspaces/rounds/{round["uid"]}')
    old_by_boat={e['boat_id']:e for e in before['entries']}
    page.locator(f'[data-profile-history="{boat["id"]}"]').click()
    page.locator('#boat-history-form [name=race_id]').select_option(str(before['courses'][0]['id']))
    page.locator('#boat-history-form button[type=submit]').click()
    page.locator('#calculation-form [name=TACIM]').wait_for()
    assert urlsplit(page.url).path=='/boats'
    assert page.locator('#calculation-boat').input_value()==str(registration['id'])
    expect(page.locator('#calculation-form [name=TACIM]')).to_have_value('600')
    page.locator('#calculation-form [name=TACIM]').fill('625')
    page.locator('#calculation-form [name=note]').fill('Rettelse af historisk målebrev fra bådregisteret')
    page.locator('[data-action=historical-preview]').click()
    expect(page.locator('#calculation-form button.primary')).to_be_enabled()
    page.locator('#calculation-form button.primary').click();page.locator('#dialog').wait_for(state='hidden')
    after=api(f'/api/workspaces/rounds/{round["uid"]}')
    for row in after['entries']:
        old=old_by_boat[row['boat_id']]
        assert (row['instant'],row['start'])==(old['instant'],old['start'])
        if row['boat_id']==registration['id']:
            assert row['handicap_set']['ratings']['TACIM']==625
            assert row['corrected']!=old['corrected']
        else:
            assert row['handicap_set_id']==old['handicap_set_id'] and row['corrected']==old['corrected']
    assert api(f'/api/boat-profiles?id={boat["id"]}')['ratings']['TACIM']==650.5
    # Cleanup unused boats without leaving the inventory; filters survive reload.
    cleanup=api('/api/boat-profiles',{'name':name+' ubrugt','skipper':'S','sail_number':'U'+str(int(time())),'boat_type':'X','ratings':{}})['id']
    go('/boats?usage=unused&q='+quote(name+' ubrugt'))
    expect(page.locator(f'[data-profile-edit="{cleanup}"]')).to_be_visible()
    assert page.locator('[data-registry-row]:visible').count()==1
    page.reload();page.locator('#registry-search').wait_for()
    expect(page.locator('#registry-usage')).to_have_value('unused')
    expect(page.locator('#registry-search')).to_have_value(name+' ubrugt')
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    page.screenshot(path='build/onsdagsbane/boat-registry-mobile.png',full_page=True)
    page.locator(f'[data-profile-delete="{cleanup}"]').click()
    page.locator('#boat-delete-form button[type=submit]').click();page.locator('#dialog').wait_for(state='hidden')
    expect(page.locator('#registry-empty')).to_be_visible()
    assert all(b['id']!=cleanup for b in api('/api/boat-profiles'))
    page.locator('#registry-reset').click()
    expect(page.locator('#registry-search')).to_have_value('')
    page.locator('#registry-search').fill('aegir')
    expect(page.locator(f'[data-profile-edit="{boat["id"]}"]')).to_be_visible()
    assert not errors,errors
    browser.close()
    print('V2 browser OK: public results, global boat CRUD, series workspace, routed allocation/reload, setup, timing, completion, publication, history, snapshot preservation, mobile')
