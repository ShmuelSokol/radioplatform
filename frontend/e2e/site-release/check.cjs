const { chromium } = require('playwright');
const B = 'http://127.0.0.1:4310';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let fails = 0;
const check = (n, ok, d = '') => { console.log(`${ok ? 'PASS' : 'FAIL'}  ${n}${d ? '  (' + d + ')' : ''}`); if (!ok) fails++; };
const post = (p, b) => fetch(B + p, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(b) });
const tabs = async () => (await fetch(B + '/__ctl/tabs')).json();
const tabOf = async (page) => { const id = await page.evaluate(() => sessionStorage.getItem('kbr_tab_id')); return (await tabs()).find((t) => t.client_id === id); };
const sound = (page) => page.evaluate(() => (window.__ctxs || []).some((c) => c.state === 'running') && [...(window.__media || [])].some((m) => !m.paused && m.currentTime > 0.5));
const iso = () => new Date().toISOString();
async function reset() {
  await post('/__ctl/publish', { version: 'v1' });
  await post('/__ctl/policy', { mode: 'manual', push: null });
  await post('/__ctl/item', { id: 'a', next: 'b' });
}
async function listen(policyArg) {
  const browser = await chromium.launch({ args: [`--autoplay-policy=${policyArg}`] });
  const page = await (await browser.newContext()).newPage();
  await page.addInitScript({ path: __dirname + '/track.js' });
  if (policyArg === 'user-gesture-required') await page.addInitScript({ path: __dirname + '/safari.js' });
  let loads = 0; page.on('load', () => loads++);
  await page.goto(B + '/listen/s1');
  await page.getByText('Listen Live').click();
  await sleep(3000);
  return { browser, page, loads: () => loads };
}

(async () => {
  // 1. Manual mode: a silent tab ignores a new release, then takes it at once on a push
  await reset();
  {
    const browser = await chromium.launch();
    const page = await browser.newPage();
    let loads = 0; page.on('load', () => loads++);
    await page.goto(B + '/stations'); await sleep(2500);
    check('silent tab reports v1', (await tabOf(page))?.version === 'v1');
    await post('/__ctl/publish', { version: 'v2' });
    await page.evaluate(() => window.dispatchEvent(new Event('online'))); // makes it check now
    await sleep(36000); // past the 30 s idle rule
    check('manual mode: no reload on its own', loads === 1, `loads=${loads}`);
    check('tab says it waits for a push', (await tabOf(page))?.waiting === 'admin push', JSON.stringify(await tabOf(page)));
    await post('/__ctl/policy', { mode: 'manual', push: { id: 'p1', version: 'v2', at: iso(), include_playing: false, pushed_at: iso() } });
    await sleep(6000);
    check('push now: silent tab reloaded', loads === 2, `loads=${loads}`);
    check('silent tab now on v2', (await tabOf(page))?.version === 'v2');
    await browser.close();
  }

  // 2. Playing tab, push without "include playing": keeps playing on v1
  for (const [label, ap] of [['Chrome-like', 'no-user-gesture-required'], ['Safari-like', 'user-gesture-required']]) {
    await reset();
    const { browser, page, loads } = await listen(ap);
    let t = await tabOf(page);
    check(`${label}: listener playing on v1`, t?.playing === true && t?.version === 'v1' && await sound(page), JSON.stringify(t));
    await post('/__ctl/publish', { version: 'v2' });
    await post('/__ctl/policy', { mode: 'manual', push: { id: 'p2', version: 'v2', at: iso(), include_playing: false, pushed_at: iso() } });
    await sleep(8000);
    t = await tabOf(page);
    check(`${label}: push without playing tabs leaves the listener alone`, loads() === 1 && t.playing && await sound(page), `loads=${loads()} waiting=${t.waiting}`);
    // 3. Push including playing tabs: waits for the next item, reloads, plays on
    await post('/__ctl/policy', { mode: 'manual', push: { id: 'p3', version: 'v2', at: iso(), include_playing: true, pushed_at: iso() } });
    await sleep(7000);
    t = await tabOf(page);
    check(`${label}: waits for the next item`, loads() === 1 && t.waiting === 'next item', `loads=${loads()} waiting=${t.waiting}`);
    await post('/__ctl/item', { id: 'b', next: 'c' });
    await sleep(9000);
    check(`${label}: reloaded after the item change`, loads() === 2, `loads=${loads()}`);
    t = await tabOf(page);
    const prompt = await page.locator('[data-testid=resume-after-release]').count();
    if (ap === 'no-user-gesture-required') {
      check(`${label}: plays on by itself on v2`, t?.version === 'v2' && await sound(page) && prompt === 0, JSON.stringify({ ...t, prompt }));
    } else {
      check(`${label}: shows "Tap to keep listening" (no sound yet)`, t?.version === 'v2' && prompt === 1 && !(await sound(page)), JSON.stringify({ ...t, prompt }));
      await page.locator('[data-testid=resume-after-release]').click();
      await sleep(3000);
      t = await tabOf(page);
      check(`${label}: one tap and it plays again`, await sound(page) && (await page.locator('[data-testid=resume-after-release]').count()) === 0, JSON.stringify(t));
    }
    await sleep(12000);
    check(`${label}: no second reload`, loads() === 2, `loads=${loads()}`);
    await browser.close();
  }

  // 4. Scheduled push: nothing before the time, reload after
  await reset();
  {
    const browser = await chromium.launch();
    const page = await browser.newPage();
    let loads = 0; page.on('load', () => loads++);
    await page.goto(B + '/stations'); await sleep(2000);
    await post('/__ctl/publish', { version: 'v2' });
    const at = new Date(Date.now() + 15000).toISOString();
    await post('/__ctl/policy', { mode: 'manual', push: { id: 'p4', version: 'v2', at, include_playing: false, pushed_at: iso() } });
    await sleep(9000);
    check('scheduled push: no reload before its time', loads === 1, `loads=${loads}`);
    await sleep(14000);
    check('scheduled push: reloaded after its time', loads === 2, `loads=${loads}`);
    await browser.close();
  }
  // 5. Listening, then opening a screen whose code file is gone (a release came out
  // meanwhile): leaving Listen stops the sound, so the tab reloads onto the new
  // release instead of showing "The site was updated" (bug report 3, 2026-11-16)
  await reset();
  {
    const { browser, page, loads } = await listen('no-user-gesture-required');
    check('stale screen: listener playing', await sound(page));
    let failed = 0;
    await page.route(/\/assets\/Archives-[^/]*\.js$/, (route) => {
      if (failed++ === 0) return route.fulfill({ status: 404, body: 'gone' });
      return route.continue();
    });
    await page.locator('nav a[href="/archives"]').first().click();
    await sleep(4000);
    const box = await page.locator('[data-testid=error-boundary]').count();
    check('stale screen: the missing file was asked for', failed >= 1, `requests=${failed}`);
    check('stale screen: reloaded onto the new release', loads() === 2, `loads=${loads()}`);
    check('stale screen: Archives shows, no error box', box === 0 && page.url().endsWith('/archives'), `box=${box} url=${page.url()}`);
    await browser.close();
  }

  console.log(fails ? `${fails} FAILED` : 'ALL PASSED');
  process.exit(fails ? 1 : 0);
})();
