"""An actionable handicap error returns to the same selection or setup form."""
import os
from uuid import uuid4
from playwright.sync_api import sync_playwright, expect

base=os.environ['ONSDAGSBANE_TEST_BASE_URL'].rstrip('/')
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page(bypass_csp=True,viewport={'width':1440,'height':1000})
    page.set_default_timeout(10000)
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    def api(path,data=None):return page.evaluate("async ({path,data})=>(await import('/core/api.js')).api(path,data)",{'path':path,'data':data})
    def go(path):page.evaluate("async path=>(await import('/core/router.js')).navigate(path)",path)
    page.goto(base);page.locator('[data-action=login]').click();page.locator('.preview-login').click();page.locator('.app-nav').wait_for()
    series=api('/api/series',{'name':'Repair '+uuid4().hex[:8],'year':2026,'round_labels':'R1-R2'})['id']
    boat=api('/api/boat-profiles',{'name':'Repair boat','skipper':'Søren','sail_number':uuid4().hex[:8],'boat_type':'X','ratings':{'TACIL':700}})['id']
    w=api(f'/api/workspaces/series/{series}');first,second=w['rounds']
    go(f'/series/{series}/assignments')
    page.locator('#fleet-search').fill('Repair boat')
    page.locator(f'#fleet-right-list [data-fleet-profile="{boat}"]').check()
    before_url=page.url
    page.locator('[data-fleet-action=add]').click()
    page.locator('#assignment-error [data-handicap-fix]').click()
    page.locator('#boat-profile-form [name=TACIM]').fill('600')
    page.locator('#boat-profile-form button[type=submit]').click();page.locator('#dialog').wait_for(state='hidden')
    assert page.url==before_url and page.locator('#fleet-search').input_value()=='Repair boat'
    expect(page.locator(f'#fleet-right-list [data-fleet-profile="{boat}"]')).to_be_checked()
    page.locator('[data-fleet-action=add]').click();expect(page.locator('#fleet-save-status')).to_contain_text('Gemt')

    go(f'/rounds/{first["uid"]}/timing');page.get_by_role('button',name='Indstil baner',exact=True).click()
    page.locator('#race-form [name=race_date]').fill('06-05-2026')
    page.locator('#race-form [name=start_time]').fill('14:00:00')
    page.locator('#race-wind').select_option('H')
    page.locator('#race-form [data-mark=A]').click();page.locator('#race-form [data-mark=D]').click()
    expect(page.locator('#race-save-status')).to_contain_text('ikke gemt')
    page.locator('.dialog-error [data-handicap-fix]').click()
    page.locator('#boat-profile-form [name=TACIH]').fill('500')
    page.locator('#boat-profile-form button[type=submit]').click()
    page.locator('#race-form').wait_for()
    expect(page.locator('#race-form [name=start_time]')).to_have_value('14:00:00')
    expect(page.locator('#race-form [name=race_date]')).to_have_value('06-05-2026')
    expect(page.locator('#race-wind')).to_have_value('H')
    assert page.evaluate("async()=>(await import('/core/state.js')).runtime.route")==['A','D']
    expect(page.locator('#race-save-status')).to_have_text('Gemt automatisk')
    page.locator('#race-form [data-action=close-dialog]').click();page.locator('#dialog').wait_for(state='hidden')

    # A later system change repairs the selected historical set, not other values.
    go(f'/rounds/{first["uid"]}/setup')
    page.locator('[data-round-system]').select_option('updown')
    page.locator('#toast [data-handicap-fix]').click()
    page.locator('#boat-profile-form [name=TAUDH]').fill('650')
    page.locator('#boat-profile-form button[type=submit]').click();page.locator('#dialog').wait_for(state='hidden')
    assert page.url.endswith(f'/rounds/{first["uid"]}/setup')
    page.locator('[data-round-system]').select_option('updown')
    expect(page.locator('[data-round-system]')).to_have_value('updown')
    page.wait_for_function("document.querySelector('.course-cards').textContent.includes('TAUDH')")
    assert api(f'/api/workspaces/rounds/{first["uid"]}')['courses'][0]['rating_key']=='TAUDH'
    assert not errors,errors
    browser.close()
    print('Handicap repair OK: failed enrollment, retained marks/search, setup form/date/route restoration and explicit historical supplement')
