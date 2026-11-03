/* Anonymous result navigation, cookies and shared links against a normal server. */
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '/home/casper/Documents/sejlrelateret/racetimer/node_modules/playwright');
const base = process.env.ONSDAGSBANE_BASE_URL || 'http://127.0.0.1:8093';

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, timezoneId: 'Europe/Copenhagen', permissions: ['clipboard-read', 'clipboard-write'] });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  async function ready(target = page) {
    await target.getByRole('heading', { name: 'Resultater', exact: true }).waitFor();
  }
  async function selected(target, className, view, race) {
    await target.waitForFunction(({ className, view, race }) => {
      const params = new URLSearchParams(location.search);
      return document.querySelector('#class-select')?.value === className &&
        params.get('class') === (className.endsWith(':all') ? 'all' : className) &&
        params.get('view') === view && document.querySelector('#result-view')?.value === view &&
        (race === undefined || (params.get('race') === race && document.querySelector('#result-race')?.value === race));
    }, { className, view, race });
  }
  try {
    await page.goto(base);
    await ready();
    const initial = await (await page.request.get(base + '/api/state')).json();
    assert.equal(initial.user, null, 'This test requires a real anonymous visitor');
    await selected(page, 'Stor bane 1', 'overall');
    assert.equal(await page.locator('tbody tr').count(), initial.boats.filter(b => b.class_name === 'Stor bane 1').length);
    assert.equal(await page.locator('nav,.stats,.side,.course-map,.intro-aside').count(), 0);
    assert.equal(await page.getByRole('button', { name: /Startlister|Både og løbsinddeling|Administration|Måltagning/ }).count(), 0);
    await page.screenshot({ path: 'build/onsdagsbane-public-simple-desktop.png', fullPage: true });

    await page.locator('#class-select').selectOption('Lille bane 2');
    await selected(page, 'Lille bane 2', 'overall');
    await page.locator('#result-view').selectOption('daily');
    await selected(page, 'Lille bane 2', 'daily');
    const race = await page.locator('#result-race option').last().getAttribute('value');
    await page.locator('#result-race').selectOption(race);
    await selected(page, 'Lille bane 2', 'daily', race);
    const dayData = await (await page.request.get(base + `/api/results?series_id=${initial.series_id}&course=lille&class_name=Lille%20bane%202`)).json();
    const entry = dayData.daily[race][0];
    const conditions = await page.locator('.race-conditions').innerText();
    assert.ok(conditions.includes('Distance: ' + Number(entry.distance).toLocaleString('da-DK', { maximumFractionDigits: 3 }) + ' sm'));
    assert.ok(conditions.includes('Vindkategori: ' + { L: 'Let', M: 'Mellem', H: 'Hård' }[entry.wind_category]));
    const shared = page.url();
    const params = new URL(shared).searchParams;
    assert.equal(params.get('series'), String(initial.series_id));
    assert.equal(params.get('course'), 'lille');
    assert.equal(params.get('class'), 'Lille bane 2');
    assert.equal(params.get('view'), 'daily');
    assert.equal(params.get('race'), race);
    const originalTable = await page.locator('tbody').innerText();
    const preference = (await context.cookies()).find(cookie => cookie.name === 'ob_results');
    assert.ok(preference.expires > Date.now() / 1000 + 300 * 86400);
    assert.equal(JSON.parse(decodeURIComponent(preference.value)).class, 'Lille bane 2');
    await page.getByRole('button', { name: 'Kopiér link', exact: true }).click();
    await page.getByRole('status').filter({ hasText: 'Linket er kopieret.' }).waitFor();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), shared);

    await page.reload();
    await selected(page, 'Lille bane 2', 'daily', race);
    await page.goto(base);
    await selected(page, 'Lille bane 2', 'daily', race);
    assert.equal(await page.locator('tbody').innerText(), originalTable);
    await page.locator('#result-view').selectOption('overall');
    await selected(page, 'Lille bane 2', 'overall');
    await page.goBack();
    await selected(page, 'Lille bane 2', 'daily', race);
    await page.goForward();
    await selected(page, 'Lille bane 2', 'overall');

    // A shared link wins over every setting saved by its recipient.
    const recipient = await browser.newContext({ viewport: { width: 390, height: 844 }, timezoneId: 'Europe/Copenhagen' });
    await recipient.addCookies([{ name: 'ob_results', value: encodeURIComponent(JSON.stringify({ series: 99999, course: 'stor', class: 'Stor bane 1', view: 'overall', race: 99999 })), url: base }]);
    const other = await recipient.newPage();
    other.on('pageerror', error => errors.push(error.message));
    await other.goto(shared);
    await selected(other, 'Lille bane 2', 'daily', race);
    assert.equal(await other.locator('tbody').innerText(), originalTable);
    assert.ok(await other.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await other.screenshot({ path: 'build/onsdagsbane-public-simple-mobile.png', fullPage: true });

    // Combining races is an explicit choice, and that choice is remembered too.
    await other.locator('#class-select').selectOption('lille:all');
    await selected(other, 'lille:all', 'daily');
    await other.locator('#result-view').selectOption('overall');
    await selected(other, 'lille:all', 'overall');
    assert.equal(new URL(other.url()).searchParams.get('class'), 'all');
    await other.goto(base);
    await selected(other, 'lille:all', 'overall');
    assert.equal(await other.locator('tbody tr').count(), initial.boats.filter(b => b.course === 'lille').length);
    await recipient.close();

    await context.clearCookies();
    await context.addCookies([{ name: 'ob_results', value: '%invalid-json', url: base }]);
    await page.goto(base + '/?series=99999&course=invalid&class=missing&view=invalid&race=invalid');
    await selected(page, 'Stor bane 1', 'overall');
    assert.equal(new URL(page.url()).searchParams.get('series'), String(initial.series_id));
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ ok: true, anonymous: true, defaultSingleClass: true, rememberedCookie: true, sharedLinkOverridesCookie: true, history: true, clipboard: true, mobile: true, invalidPreferences: true, javascriptErrors: errors.length }));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
