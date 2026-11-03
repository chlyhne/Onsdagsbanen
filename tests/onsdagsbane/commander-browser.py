"""Run only against an isolated preview database; creates its own test series.

Set ONSDAGSBANE_TEST_BASE_URL to the isolated --preview-admin server.
Uses Python Playwright and writes screenshots under build/onsdagsbane/.
"""
import os
from pathlib import Path
from time import time
from playwright.sync_api import sync_playwright, expect

base=os.environ['ONSDAGSBANE_TEST_BASE_URL'].rstrip('/')
artifacts=Path('build/onsdagsbane');artifacts.mkdir(parents=True,exist_ok=True)
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page(bypass_csp=True,viewport={'width':1440,'height':1000})
    errors=[]
    page.on('pageerror',lambda error: errors.append(str(error)))
    page.goto(base)
    page.locator('tbody tr').first.wait_for()
    page.locator('[data-action=login]').click();page.locator('.preview-login').click()
    page.wait_for_function('()=>state.data?.user?.approved')
    source=page.evaluate("()=>api('/api/state?series_id=1')")
    boats=[next(b for b in source['boats'] if b['skipper'].startswith(name)) for name in ('Mikkel','Oluf')]
    target=page.evaluate("data=>api('/api/series',data)",{'name':f'Commander test {time()}','year':2027,'round_labels':'R1–R4'})['id']
    page.evaluate('async id=>{await selectSeries(id);state.page="series";await render()}',target)
    page.locator('.series-page [data-action=catalog]').click()
    page.locator('#fleet-commander-form').wait_for()
    page.locator('#fleet-source').select_option('1')
    assert page.locator('#fleet-scope').input_value()=='series'
    assert page.get_by_role('button',name='Gem løbsinddeling',exact=True).count()==0
    assert page.get_by_role('button',name='Forhåndsvis løbsinddeling',exact=True).count()==0

    def saved():
        page.wait_for_function('()=>!fleetWork.loading&&fleetWork.status.startsWith("Gemt")')

    # Multiple search words, case folding and Danish letter normalization;
    # marks survive a second search and clearing the query.
    page.locator('#fleet-search').fill('MIKKEL den')
    assert page.locator('#fleet-right-list .fleet-boat').count()==1
    page.locator(f'#fleet-right-list [data-fleet-profile="{boats[0]["profile_id"]}"]').check()
    page.locator('#fleet-search').fill('oluf gronkjaer')
    assert page.locator('#fleet-right-list .fleet-boat').count()==1
    page.locator(f'#fleet-right-list [data-fleet-profile="{boats[1]["profile_id"]}"]').check()
    assert '2 markeret' in page.locator('#fleet-right-count').inner_text()
    page.locator('#fleet-search').fill('')
    # Lose the response after the server saves. The UI must not claim success;
    # retrying the arrow must recover the saved state without duplicates.
    def lose_response(route):
        assert route.fetch().status==200
        route.abort()
    page.route('**/api/heats/boats/move',lose_response,times=1)
    page.locator('[data-fleet-action=add]').click()
    page.wait_for_function('()=>!fleetWork.loading&&fleetWork.status.includes("ikke bekræftes")')
    assert page.locator('#fleet-left-list .fleet-boat').count()==0
    assert '2 markeret' in page.locator('#fleet-right-count').inner_text()
    page.locator('[data-fleet-action=add]').click()
    saved()
    stored=page.evaluate('id=>api(`/api/state?series_id=${id}`)',target)
    assert len(stored['boats'])==2 and not stored['races']
    assert {b['profile_id'] for b in stored['boats']}=={b['profile_id'] for b in boats}
    assert all(len(h['boat_ids'])==2 for r in stored['rounds'] for c in r['courses'] for h in c['heats'] if h['name']=='Stor bane 1')

    # Allocation mode hides boats assigned anywhere in the selected range.
    def choose_heat(name):
        heat=next(h for c in stored['rounds'][0]['courses'] for h in c['heats'] if h['name']==name)
        node=page.locator('.fleet-tree-heat').filter(has=page.locator(f'[data-fleet-heat="{heat["id"]}"]'))
        if not node.evaluate('(el)=>el.open'):node.locator('summary').click()
        page.locator(f'[data-fleet-heat="{heat["id"]}"][data-fleet-scope=series]').click()

    choose_heat('Stor bane 2')
    selected=f'#fleet-right-list [data-fleet-profile="{boats[0]["profile_id"]}"]'
    assert page.locator(selected).count()==0
    page.locator('#fleet-include-assigned').check()
    page.locator(selected).check()
    # Turning the option off also drops now-ineligible marks.
    page.locator('#fleet-include-assigned').uncheck()
    assert page.locator('[data-fleet-action=add]').is_disabled()
    page.locator('#fleet-include-assigned').check()
    page.locator(selected).check()
    page.locator('[data-fleet-action=add]').click();saved()
    moved=page.evaluate('id=>api(`/api/state?series_id=${id}`)',target)
    for round in moved['rounds']:
        heats={h['name']:h for c in round['courses'] for h in c['heats']}
        assert len(heats['Stor bane 1']['boat_ids'])==1
        assert len(heats['Stor bane 2']['boat_ids'])==1
    choose_heat('Stor bane 1')
    page.locator(selected).check()
    page.locator('[data-fleet-action=add]').click();saved()
    page.locator('#fleet-include-assigned').uncheck()

    # Navigate the native tree to a single race and move one member back right.
    second=next(h for c in stored['rounds'][1]['courses'] for h in c['heats'] if h['name']=='Stor bane 1')
    page.locator(f'[data-fleet-heat="{second["id"]}"][data-fleet-scope=round]').click()
    assert page.locator('#fleet-scope').input_value()=='round'
    page.locator('#fleet-member-search').fill('Mikkel')
    page.locator('#fleet-left-list input').check()
    page.locator('[data-fleet-action=remove]').click()
    saved()
    # A boat free in R2 is still occupied elsewhere when selecting the whole series.
    assert page.locator(selected).count()==1
    page.locator('#fleet-scope').select_option('series')
    assert page.locator(selected).count()==0
    page.locator('#fleet-scope').select_option('round')
    assert page.locator(selected).count()==1
    page.locator('#fleet-member-search').fill('')
    page.locator('#dialog').evaluate('(el)=>el.scrollTop=0')
    expect(page.locator('[data-fleet-action=add]')).to_be_in_viewport()
    page.screenshot(path=str(artifacts/'commander-desktop.png'),full_page=True)
    stored=page.evaluate('id=>api(`/api/state?series_id=${id}`)',target)
    for index,round in enumerate(stored['rounds']):
        heat=next(h for c in round['courses'] for h in c['heats'] if h['name']=='Stor bane 1')
        assert len(heat['boat_ids'])==(1 if index==1 else 2)

    # Historical removal is rejected without showing it as saved or losing marks.
    page.locator('[data-fleet-series="1"]').click()
    page.wait_for_function('()=>!fleetWork.loading&&fleetWork.data.series_id===1')
    heat=next(h for c in source['rounds'][0]['courses'] for h in c['heats'] if h['name']=='Stor bane 2')
    # Expand the heat disclosure with its native summary control.
    page.locator('.fleet-tree-heat').filter(has=page.locator(f'[data-fleet-heat="{heat["id"]}"]')).locator('summary').click()
    page.locator(f'[data-fleet-heat="{heat["id"]}"][data-fleet-scope=round]').click()
    before=page.locator('#fleet-left-list .fleet-boat').count()
    page.locator('#fleet-left-list input').first.check()
    page.locator('[data-fleet-action=remove]').click()
    page.wait_for_function('()=>!fleetWork.loading&&fleetWork.status.includes("ikke bekræftes")')
    assert page.locator('#fleet-left-list .fleet-boat').count()==before
    assert page.locator('#fleet-left-list input:checked').count()==1
    assert 'historik' in page.locator('.dialog-error').inner_text()
    page.set_viewport_size({'width':390,'height':844})
    assert page.locator('#fleet-commander-form').evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
    page.screenshot(path=str(artifacts/'commander-mobile.png'),full_page=True)
    unchanged=page.evaluate("()=>api('/api/state?series_id=1')")
    assert unchanged['boats']==source['boats'] and unchanged['races']==source['races']
    assert not errors,errors
    browser.close()
    print('Commander browser OK: shrinking free pool, explicit reassignment, hidden-mark protection, dateless round IDs, autosave/retry, search, reuse, mobile')
