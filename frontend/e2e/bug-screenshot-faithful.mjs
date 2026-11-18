/**
 * The bug reporter's screenshot looks like the screen (Chromium, desktop and iPhone size).
 *
 * Shmuel's iPhone, 2026-11-18: the picture came out with the qnet bevel lines off their
 * buttons and a stray line down the middle. For each way of taking it (modern-screenshot,
 * html2canvas) on staff pages in the qnet look and a listener page, scrolled and not, the
 * capture is compared with a real screenshot of the same moment: at most 16% of pixels may
 * differ visibly (see LIMIT for the known small differences).
 * Pictures land in SHOTS_DIR (default /tmp/bug-shots) to look at.
 *
 *   cd frontend && npm run build && (npx vite preview --port 4173 &)
 *   BASE_URL=http://localhost:4173 node e2e/bug-screenshot-faithful.mjs
 */
import fs from 'node:fs';
import { chromium, devices, checker, makeStation, newPage, adminContext } from './lib/fake-station.mjs';
import { compareShots } from './lib/shot-compare.mjs';

const BASE = process.env.BASE_URL || 'http://localhost:4173';
const DIR = process.env.SHOTS_DIR || '/tmp/bug-shots';
// % of pixels. Known small differences of modern-screenshot, together up to about 15%: scroll
// bars Chromium draws into the copy (a phone shows none), placeholder text drawn dark instead of
// grey, a two-line title cut without its "...", a 2 px shift on some phone pages, text smoothing
// and JPEG. The broken pictures this is for differ by 17-55%.
const LIMIT = 16;
fs.mkdirSync(DIR, { recursive: true });
const { check, done } = checker({
  'scrolled html2canvas': 'html2canvas (the second way, used only when modern-screenshot fails) draws a scrolled page from the top on a computer and misplaces rows on a phone',
});

const browser = await chromium.launch();
const views = [['desktop', { viewport: { width: 1280, height: 800 } }], ['iphone', devices['iPhone 13']]];
const pages = [['/admin/requests', 0], ['/admin/activity', 0], ['/admin/assets', 400], ['/stations', 0]];
for (const [view, opts] of views) {
  const station = makeStation();
  const { ctx } = await adminContext(browser, opts, station);
  await ctx.routeWebSocket(/\/ws\/(live|site|listeners)/, () => {});
  const { page } = await newPage(ctx);
  for (const [path, scroll] of pages) {
    for (const way of ['modern-screenshot', 'modern-screenshot as on Safari', 'html2canvas']) {
      const tag = `${view} ${path}${scroll ? ' scrolled' : ''} ${way}`;
      try {
        await page.goto(BASE + path);
        await page.waitForTimeout(1500);
        await page.evaluate((y) => window.scrollTo(0, y), scroll);
        await page.waitForTimeout(400);
        const real = `data:image/png;base64,${(await page.screenshot()).toString('base64')}`;
        await page.evaluate((w) => { window.__kbrShotSkip = [w === 'html2canvas' ? 'modern-screenshot' : 'html2canvas']; window.__kbrShotBevels = w.includes('Safari'); }, way);
        await page.evaluate(() => window.dispatchEvent(new CustomEvent('kbr:report-bug', { detail: { delay: 0 } })));
        const img = page.locator('img[alt="Screenshot of the page"]');
        await img.waitFor({ timeout: 15000 });
        const shot = await img.getAttribute('src');
        const name = `${DIR}/${tag.replace(/[^a-z0-9]+/gi, '-')}`;
        fs.writeFileSync(`${name}-capture.jpg`, Buffer.from(shot.split(',')[1], 'base64'));
        fs.writeFileSync(`${name}-real.png`, Buffer.from(real.split(',')[1], 'base64'));
        const cmp = await compareShots(page, shot, real);
        check(`${tag}: the screenshot looks like the screen (at most ${LIMIT}% of pixels differ)`, cmp.off <= LIMIT, JSON.stringify(cmp));
        await page.keyboard.press('Escape');
        await page.waitForTimeout(200);
      } catch (e) {
        check(`${tag}: ran`, false, String(e).slice(0, 200));
      }
    }
  }
  await ctx.close();
}
await browser.close();
done();
