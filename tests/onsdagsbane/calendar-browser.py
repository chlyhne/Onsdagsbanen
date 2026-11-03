"""Calendar grid, proposals, and selecting a manual date during timing."""
import os
from uuid import uuid4
from playwright.sync_api import sync_playwright, expect

base = os.environ['ONSDAGSBANE_TEST_BASE_URL'].rstrip('/')
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    page.set_default_timeout(10000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    def api(path, data=None):
        return page.evaluate("async ({path,data})=>(await import('/core/api.js')).api(path,data)", {'path': path, 'data': data})
    def go(path):
        page.evaluate("async path=>(await import('/core/router.js')).navigate(path)", path)
    def edit_round(round):
        details = page.locator(f'[data-series-round="{round["id"]}"]')
        if details.get_attribute('open') is None:
            details.locator('summary').click()
        details.locator('[data-round-starts]').click()
    def choose(day):
        page.locator('select[data-calendar-year]').select_option(day[:4])
        page.locator('[data-calendar-month]').select_option(str(int(day[5:7])-1))
        page.locator(f'[data-calendar-date="{day}"]').click()
    page.goto(base)
    page.locator('[data-action=login]').click()
    page.locator('.preview-login').click()
    page.locator('.app-nav').wait_for()
    series = api('/api/series', {'name': 'Calendar '+uuid4().hex[:8], 'year': 2026, 'round_labels': 'R1-R6'})['id']
    rounds = api(f'/api/workspaces/series/{series}')['rounds']
    go(f'/series/{series}')
    edit_round(rounds[1])
    field = page.locator('#round-starts-form [name=race_date]')
    expect(field).to_have_value('')
    field.click()
    expect(page.locator('#date-calendar')).to_be_visible()
    assert page.locator('.calendar-grid th').all_text_contents() == ['Man', 'Tir', 'Ons', 'Tor', 'Fre', 'Lør', 'Søn']
    page.locator('select[data-calendar-year]').select_option('2028')
    page.locator('[data-calendar-month]').select_option('1')
    leap = page.locator('[data-calendar-date="2028-02-29"]')
    leap.focus()
    leap.press('ArrowRight')
    expect(page.locator('[data-calendar-date="2028-03-01"]')).to_be_focused()
    page.keyboard.press('PageUp')
    expect(page.locator('[data-calendar-date="2028-02-01"]')).to_be_focused()
    page.keyboard.press('Escape')
    expect(page.locator('#date-calendar')).to_have_count(0)
    expect(page.locator('#dialog')).to_be_visible()
    field.click()
    page.locator('[data-calendar-month]').select_option('4')
    day = page.locator('[data-calendar-date="2026-05-06"]')
    assert day.evaluate('(el)=>el.parentElement.cellIndex') == 2  # Wednesday
    day.click()
    expect(field).to_have_value('06-05-2026')
    page.locator('#round-starts-form button.primary').click()
    expect(page.locator('#dialog')).not_to_be_visible()
    expect(page.locator(f'[data-series-round="{rounds[2]["id"]}"] summary')).to_contain_text('13-05-2026 (forslag)')
    expect(page.locator(f'[data-series-round="{rounds[5]["id"]}"] summary')).to_contain_text('03-06-2026 (forslag)')
    assert api(f'/api/workspaces/series/{series}')['rounds'][0]['suggested_date'] is None
    edit_round(rounds[2])
    expect(field).to_have_value('13-05-2026')
    # Optional date can be cleared, and dismissing leaves the suggestion uncommitted.
    field.click()
    page.locator('[data-calendar-clear]').click()
    expect(field).to_have_value('')
    page.locator('#round-starts-form [data-action=close-dialog]').click()
    assert api(f'/api/workspaces/series/{series}')['rounds'][2]['race_date'] is None
    edit_round(rounds[3])
    field.click()
    choose('2026-07-01')
    page.locator('#round-starts-form button.primary').click()
    expect(page.locator('#dialog')).not_to_be_visible()
    expect(page.locator(f'[data-series-round="{rounds[4]["id"]}"] summary')).to_contain_text('08-07-2026 (forslag)')
    edit_round(rounds[1])
    for width in (390, 320):
        page.set_viewport_size({'width': width, 'height': 760})
        field.click()
        panel = page.locator('#date-calendar')
        expect(panel).to_be_visible()
        page.wait_for_timeout(100)
        bounds = panel.bounding_box()
        assert bounds['x'] >= 0 and bounds['x']+bounds['width'] <= width, bounds
        assert panel.evaluate('(el)=>el.scrollWidth<=el.clientWidth'), panel.evaluate('(el)=>[el.scrollWidth,el.clientWidth]')
        page.screenshot(path=f'build/onsdagsbane/calendar-{width}.png')
        page.keyboard.press('Escape')
    page.set_viewport_size({'width': 1440, 'height': 1000})
    field.click()
    page.screenshot(path='build/onsdagsbane/calendar-desktop.png')
    page.keyboard.press('Escape')
    page.locator('#round-starts-form [data-action=close-dialog]').click()
    # A calendar date inside course setup works with distance-only autosave.
    profile = api('/api/boat-profiles', {'name': 'Calendar boat', 'skipper': 'Søren', 'sail_number': uuid4().hex[:8], 'boat_type': 'X', 'ratings': {'TACIM': 600}})['id']
    heat = next(c for c in rounds[1]['courses'] if c['course']=='stor')['heats'][0]
    api('/api/heats/boats/move', {'heat_id': heat['id'], 'scope': 'series', 'action': 'add', 'profile_ids': [profile]})
    boat = api(f'/api/workspaces/series/{series}')['registrations'][0]['id']
    go(f'/timing?series={series}&round={rounds[1]["uid"]}')
    page.get_by_role('button', name='Indstil baner', exact=True).click()
    page.locator('#race-course').select_option('stor')
    page.wait_for_function("document.querySelector('#race-form')?.dataset.course==='stor' && !document.querySelector('#race-form').inert")
    page.locator('#race-form [name=race_date]').click()
    choose('2026-05-06')
    page.locator('#race-form [name=distance]').fill('4,25')
    expect(page.locator('#race-save-status')).to_have_text('Gemt automatisk')
    page.locator('#race-form [data-action=close-dialog]').click()
    expect(page.locator('[data-timing-course=stor]')).to_contain_text('N/A')
    page.locator(f'[data-measure="{boat}"]').click()
    page.locator('#finish-date').click()
    choose('2026-05-06')
    expect(page.locator('#finish-date')).to_have_value('06-05-2026')
    before = page.locator('#finish-time').input_value()
    with page.expect_response('**/api/rounds/*/clock') as response:
        page.locator('[data-timing-action=clock-plus]').click()
    assert response.value.status == 200
    expect(page.locator('#finish-date')).to_have_value('06-05-2026')
    expect(page.locator('#finish-time')).to_have_value(before)
    # A winter series spans New Year: proposals can be saved and used for courses.
    winter = api('/api/series', {'name': 'Winter '+uuid4().hex[:8], 'year': 2026, 'round_labels': 'R1-R3'})['id']
    winter_rounds = api(f'/api/workspaces/series/{winter}')['rounds']
    go(f'/series/{winter}')
    edit_round(winter_rounds[0])
    field.click()
    choose('2026-12-30')
    page.locator('#round-starts-form button.primary').click()
    expect(page.locator('#dialog')).not_to_be_visible()
    expect(page.locator(f'[data-series-round="{winter_rounds[1]["id"]}"] summary')).to_contain_text('06-01-2027 (forslag)')
    edit_round(winter_rounds[1])
    expect(field).to_have_value('06-01-2027')
    page.locator('#round-starts-form button.primary').click()
    expect(page.locator('#dialog')).not_to_be_visible()
    expect(page.locator(f'[data-series-round="{winter_rounds[1]["id"]}"] summary')).to_contain_text('06-01-2027 ·')
    go(f'/timing?series={winter}&round={winter_rounds[2]["uid"]}')
    page.get_by_role('button', name='Indstil baner', exact=True).click()
    expect(page.locator('#race-form [name=race_date]')).to_have_value('13-01-2027')
    page.locator('#race-form [name=distance]').fill('4.5')
    expect(page.locator('#race-save-status')).to_have_text('Gemt automatisk')
    page.locator('#race-form [data-action=close-dialog]').click()
    expect(page.locator('#dialog')).not_to_be_visible()
    go(f'/series/{winter}')
    year = page.locator('#series-settings-form [name=year]')
    expect(year).to_be_enabled()
    year.fill('2027')
    with page.expect_response('**/api/series/settings') as response:
        page.locator('#series-settings-form button.primary').click()
    assert response.value.status == 200
    page.reload()
    expect(year).to_have_value('2027')
    saved = api(f'/api/workspaces/series/{winter}')
    assert [r['race_date'] for r in saved['rounds']] == ['2026-12-30', '2027-01-06', '2027-01-13']
    assert not errors, errors
    browser.close()
    print('Calendar OK: weekday columns, keyboard, leap day, modal, mobile, forward proposals and manual timing date')
