"""CRUD checks against an isolated --preview-admin server only."""
import os
from time import time
from playwright.sync_api import sync_playwright, expect

base=os.environ['ONSDAGSBANE_TEST_BASE_URL'].rstrip('/')
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page(bypass_csp=True,viewport={'width':1440,'height':1000})
    errors=[];page.on('pageerror',lambda error: errors.append(str(error)))
    page.goto(base);page.locator('tbody tr').first.wait_for()
    page.locator('[data-action=login]').click();page.locator('.preview-login').click()
    page.wait_for_function('()=>state.data?.user?.approved')
    source=page.evaluate("data=>api('/api/series',data)",{'name':f'CRUD {time()}','year':2027,'round_labels':'R1–R3'})['id']
    page.evaluate('async id=>{await selectSeries(id);state.page="boats";await render()}',source)
    expect(page.get_by_role('button',name='Både og løbsinddeling',exact=True)).to_be_visible()
    page.locator('[data-action=new-boat]').click()
    for key,value in {'name':'CRUD båd','skipper':'Søren Ærø','sail_number':'DEN 987654','boat_type':'X-332','TACIL':'700','TACIM':'600','TACIH':'500'}.items():
        page.locator(f'#boat-form [name={key}]').fill(value)
    page.locator('#boat-form button.primary').click()
    page.locator('#boat-form').wait_for(state='hidden')
    page.locator('.boat-actions [data-edit-boat]').click()
    page.locator('#boat-form [name=name]').fill('Ægir CRUD')
    page.locator('#boat-form button.primary').click()
    page.locator('#boat-form').wait_for(state='hidden')
    expect(page.locator('.boatname').filter(has_text='Ægir CRUD')).to_be_visible()
    boat=page.evaluate('()=>state.data.boats[0]');profile=boat['profile_id']
    page.locator('.boat-actions [data-profile-delete]').click()
    assert page.locator('#boat-delete-form button[type=submit]').count()==0
    assert page.locator('.boat-delete-races li').count()==3
    page.locator('#boat-delete-form [data-action=close-dialog]').click()
    page.locator('#boat-delete-form').wait_for(state='hidden')

    # Edit directly in the common pool, without registering in the target series.
    target=page.evaluate("data=>api('/api/series',data)",{'name':f'CRUD target {time()}','year':2027,'round_labels':'R1–R3'})['id']
    page.evaluate('async id=>{await selectSeries(id);state.page="boats";await render()}',target)
    page.locator('[data-action=catalog]').click()
    page.locator('#fleet-search').fill('Ægir CRUD')
    page.locator(f'#fleet-right-list [data-profile-edit="{profile}"]').click()
    page.locator('#boat-profile-form [name=name]').fill('Ørn CRUD')
    page.locator('#boat-profile-form [name=TACIM]').fill('612,3')
    page.locator('#boat-profile-form button[type=submit]').click()
    page.locator('#fleet-commander-form').wait_for()
    assert page.locator('#fleet-search').input_value()=='Ægir CRUD'
    page.locator('#fleet-search').fill('Ørn CRUD')
    expect(page.locator(f'#fleet-right-list [data-profile-edit="{profile}"]')).to_be_visible()
    saved=page.evaluate('id=>api(`/api/boat-profiles?id=${id}`)',profile)
    assert saved['ratings']['TACIM']==612.3
    assert page.evaluate('id=>api(`/api/state?series_id=${id}`).then(s=>s.boats.length)',target)==0
    page.locator(f'#fleet-right-list [data-profile-delete="{profile}"]').click()
    expect(page.locator('#boat-delete-form')).to_contain_text('CRUD')
    assert page.locator('#boat-delete-form button[type=submit]').count()==0
    assert page.locator('.boat-delete-races li').count()==3
    assert page.locator('.boat-delete-races button,.boat-delete-races a').count()==0
    page.locator('#boat-delete-form [data-action=close-dialog]').click()
    page.locator('#fleet-commander-form').wait_for()

    # Navigate manually to the source series and remove each race individually.
    page.locator(f'[data-fleet-series="{source}"]').click()
    page.wait_for_function('(id)=>!fleetWork.loading&&fleetWork.data.series_id===id',arg=source)
    source_state=page.evaluate('()=>fleetWork.data')
    for index,round in enumerate(source_state['rounds']):
        heat=next(h for c in round['courses'] for h in c['heats'] if h['name']=='Stor bane 1')
        page.locator(f'[data-fleet-heat="{heat["id"]}"][data-fleet-scope=round]').click()
        page.locator(f'#fleet-left-list [data-fleet-profile="{profile}"]').check()
        page.locator('[data-fleet-action=remove]').click()
        page.wait_for_function('()=>!fleetWork.loading&&fleetWork.status.startsWith("Gemt")')
        preview=page.evaluate('id=>api(`/api/boat-profiles/delete-preview?id=${id}`)',profile)
        assert len(preview['rounds'])==2-index
        assert preview['can_delete']==(index==2)
    page.locator(f'#fleet-right-list [data-profile-delete="{profile}"]').click()
    expect(page.locator('#boat-delete-form')).to_contain_text('hele systemets fælles bådliste')
    page.locator('#boat-delete-form button[type=submit]').click()
    page.locator('#fleet-commander-form').wait_for()
    assert page.locator(f'[data-profile-edit="{profile}"]').count()==0
    assert page.evaluate('id=>api(`/api/state?series_id=${id}`).then(s=>s.boats.length)',source)==0
    page.locator('.dialog-head [data-action=close-dialog]').click()
    page.locator('#dialog').wait_for(state='hidden')

    # A historical boat gets an explanation and no destructive submit control.
    page.evaluate('()=>openFleetCommander(null,1)')
    page.locator('#fleet-left-list [data-profile-delete]').first.click()
    expect(page.locator('#boat-delete-form [role=alert]')).to_contain_text('alle ræs')
    assert page.locator('.boat-delete-races li').count()>0
    assert page.locator('#boat-delete-form button[type=submit]').count()==0
    page.locator('#boat-delete-form [data-action=close-dialog]').click()
    page.locator('#fleet-commander-form').wait_for()
    assert not errors,errors
    browser.close()
    print('Boat management browser OK: blocked deletion with race list, no removal shortcuts, individual race removal, global deletion only when unused, edit and history protection')
