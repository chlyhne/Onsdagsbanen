"""Anonymous result navigation against a server containing the imported sample series."""
import json
import os
from urllib.parse import quote, unquote
from playwright.sync_api import sync_playwright, expect

base=os.environ.get('ONSDAGSBANE_TEST_BASE_URL','http://127.0.0.1:8093').rstrip('/')
series=os.environ.get('ONSDAGSBANE_TEST_SERIES_ID','1')
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    context=browser.new_context(bypass_csp=True,viewport={'width':1280,'height':900},timezone_id='Europe/Copenhagen',permissions=['clipboard-read','clipboard-write'])
    page=context.new_page();errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    def selected(target,group,view,race=None):
        target.wait_for_function("""({group,view,race})=>{
            const q=new URLSearchParams(location.search);
            return document.querySelector('#class-select')?.value===group &&
                q.get('class')===(group.endsWith(':all')?'all':group) &&
                q.get('view')===view && document.querySelector('#result-view')?.value===view &&
                (!race || (q.get('race')===race && document.querySelector('#result-race')?.value===race));
        }""",arg={'group':group,'view':view,'race':race})
    page.goto(base+'/results?series='+series)
    selected(page,'Stor bane 1','overall')
    assert page.locator('tbody tr').count()>0
    assert page.locator('.app-nav').count()==0
    assert '\ufffd' not in page.locator('body').inner_text()
    discard=page.locator('.discarded').first
    if discard.count():
        assert discard.evaluate("el=>getComputedStyle(el).textDecorationColor")=='rgb(198, 40, 40)'
    assert page.locator('.standings td.numeric').first.evaluate("el=>getComputedStyle(el).textAlign")=='center'
    page.locator('#class-select').select_option('Lille bane 2')
    page.locator('#result-view').select_option('daily')
    selected(page,'Lille bane 2','daily')
    race=page.locator('#result-race option').last.get_attribute('value')
    page.locator('#result-race').select_option(race)
    selected(page,'Lille bane 2','daily',race)
    original=page.locator('tbody').inner_text();shared=page.url
    cookie=next(c for c in context.cookies() if c['name']=='ob_results')
    assert json.loads(unquote(cookie['value']))['class']=='Lille bane 2'
    page.get_by_role('button',name='Kopiér link',exact=True).click()
    expect(page.get_by_role('status')).to_contain_text('Linket er kopieret.')
    assert page.evaluate('navigator.clipboard.readText()')==shared
    page.reload();selected(page,'Lille bane 2','daily',race)
    page.goto(base);selected(page,'Lille bane 2','daily',race)
    assert page.locator('tbody').inner_text()==original
    page.locator('#result-view').select_option('overall');selected(page,'Lille bane 2','overall')
    page.go_back();selected(page,'Lille bane 2','daily',race)
    page.go_forward();selected(page,'Lille bane 2','overall')

    recipient=browser.new_context(bypass_csp=True,viewport={'width':390,'height':844})
    recipient.add_cookies([{'name':'ob_results','value':quote(json.dumps({'series':99999,'class':'Stor bane 1','course':'stor','view':'overall'})),'url':base}])
    other=recipient.new_page();other.on('pageerror',lambda error:errors.append(str(error)))
    other.goto(shared);selected(other,'Lille bane 2','daily',race)
    assert other.locator('tbody').inner_text()==original
    assert other.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    other.locator('#class-select').select_option('lille:all');selected(other,'lille:all','daily')
    other.locator('#result-view').select_option('overall');selected(other,'lille:all','overall')
    other.goto(base);selected(other,'lille:all','overall')
    assert other.locator('tbody tr').count()>0
    assert not errors,errors
    browser.close()
    print('Public browser OK: Danish text, centered points, discards, cookie, shared links, clipboard, back/forward, combined results and mobile')
