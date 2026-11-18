/**
 * 🐞 works while a dialog, panel or sheet is open, and the picture shows that dialog.
 *
 * Shmuel's iPhone, 2026-11-18: with the control room's Song panel open, the 🐞 in the top
 * bar sat under the panel's dimmed backdrop; a tap closed nothing and opened nothing he
 * wanted. For each kind of dialog: open it, tap where the 🐞 shows (a real tap that lands on
 * the backdrop), and the report opens over it; the dialog is still open behind; the
 * screenshot has the dialog in it; the screenshot opens full screen and zooms; sending works;
 * closing the report leaves the dialog open.
 *
 *   cd frontend && npm run build && (npx vite preview --port 4173 &)
 *   BASE_URL=http://localhost:4173 node e2e/bug-over-dialogs.mjs
 */
import { chromium, devices, checker, sleep, makeStation, adminContext, newPage } from './lib/fake-station.mjs';
import fs from 'node:fs';
import { compareShots } from './lib/shot-compare.mjs';

const BASE = process.env.BASE_URL || 'http://localhost:4173';
const { check, done } = checker();
const DESKTOP = { viewport: { width: 1440, height: 900 } };
const PHONE = { ...devices['iPhone 13'] };

/** The kinds of dialog on the site: how to open each, and what marks it open. */
const DIALOGS = [
  { name: 'control room Song panel', path: '/admin/dashboard', ready: (p) => p.getByTestId('queue-row').first(),
    open: async (p, phone) => {
      if (phone) { await p.getByTestId('queue-row').nth(2).locator('[draggable=true]').tap(); await p.getByTestId('row-song-touch').tap(); }
      else await p.getByTestId('now-song').click();
    },
    dialog: (p) => p.getByTestId('song-panel') },
  { name: 'New Archive', path: '/admin/archives', ready: (p) => p.getByRole('button', { name: /New Archive/ }),
    open: (p) => p.getByRole('button', { name: /New Archive/ }).click(), dialog: (p) => p.getByRole('dialog', { name: 'New Archive' }) },
  { name: 'New Show', path: '/admin/live', ready: (p) => p.getByRole('button', { name: /New Show/ }),
    open: (p) => p.getByRole('button', { name: /New Show/ }).click(), dialog: (p) => p.getByText(/Schedule New Show|New Show|Create Show/).last() },
];

const browser = await chromium.launch();
for (const [view, opts] of [['desktop', DESKTOP], ['iphone', PHONE]]) {
  for (const d of DIALOGS) {
    const tag = `${view} ${d.name}`;
    const { ctx } = await adminContext(browser, opts, makeStation({ queueSize: 8 }));
    await ctx.routeWebSocket(/\/ws\/(live|site|listeners)/, () => {});
    const { page } = await newPage(ctx);
    let posted = null;
    await ctx.route('**/bug-reports', async (r) => {
      if (r.request().method() !== 'POST') return r.fallback();
      posted = JSON.parse(r.request().postData() || '{}');
      await r.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ number: 77, screenshot_saved: true }) });
    });
    try {
      await page.goto(BASE + d.path);
      await d.ready(page).waitFor({ timeout: 15000 });
      await sleep(500);
      await d.open(page, view === 'iphone');
      await d.dialog(page).waitFor({ timeout: 5000 });
      // The 🐞 this screen shows: the top bar's on a phone, the control room's bar or the floating one on a computer
      const bug = page.locator('[data-bug-trigger]:visible').first();
      const box = await bug.boundingBox();
      check(`${tag}: a 🐞 shows while the dialog is open`, !!box);
      if (!box) continue;
      const x = box.x + box.width / 2, y = box.y + box.height / 2;
      const real = `data:image/png;base64,${(await page.screenshot()).toString('base64')}`;
      const onTop = await page.evaluate(([x, y]) => !!document.elementFromPoint(x, y)?.closest('[data-bug-trigger]'), [x, y]);
      if (view === 'iphone') await page.touchscreen.tap(x, y); else await page.mouse.click(x, y);
      const report = page.getByRole('dialog', { name: 'Report a bug' });
      const opened = await report.waitFor({ timeout: 10000 }).then(() => true, () => false);
      check(`${tag}: tapping the 🐞 opens the report${onTop ? '' : ' (the 🐞 is under the dialog\'s backdrop)'}`, opened);
      check(`${tag}: the dialog stays open behind the report`, await d.dialog(page).isVisible());
      const src = opened ? await report.locator('img[alt="Screenshot of the page"]').getAttribute('src').catch(() => null) : null;
      check(`${tag}: the report has a screenshot`, !!src);
      if (src) {
        // The dialog is in the picture: the capture matches the screen as it was before the tap
        if (process.env.SHOTS_DIR) {
          const n = `${process.env.SHOTS_DIR}/${tag.replace(/[^a-z0-9]+/gi, '-')}`;
          fs.writeFileSync(`${n}-capture.jpg`, Buffer.from(src.split(',')[1], 'base64'));
          fs.writeFileSync(`${n}-real.png`, Buffer.from(real.split(',')[1], 'base64'));
        }
        const cmp = await compareShots(page, src, real);
        check(`${tag}: the screenshot shows the dialog as it was on screen (at most 16% of pixels differ; see LIMIT in bug-screenshot-faithful.mjs)`, cmp.off <= 16, JSON.stringify(cmp));
      }
      if (opened && src) {
        // The picture opens full size on a tap, zooms on a second tap, and closes back to the report
        await report.getByTestId('bug-shot-open').click();
        const viewer = page.getByTestId('bug-shot-viewer');
        const shown = await viewer.waitFor({ timeout: 3000 }).then(() => true, () => false);
        const fit = shown ? await viewer.locator('img').boundingBox() : null;
        const vw = page.viewportSize().width;
        check(`${tag}: tapping the screenshot shows it full screen`, !!fit && fit.width >= vw * 0.6, fit ? `${Math.round(fit.width)} of ${vw} px wide` : '');
        if (shown) {
          await viewer.locator('img').click();
          const big = await viewer.locator('img').boundingBox();
          check(`${tag}: a tap on the full-screen picture zooms in`, !!big && big.width > vw * 1.5, big ? `${Math.round(big.width)} px wide` : '');
          await page.getByRole('button', { name: 'Close the screenshot' }).click();
          check(`${tag}: Close goes back to the report`, !(await viewer.isVisible()) && await report.isVisible());
        }
      }
      if (opened) {
        await report.locator('#bug-what').fill('report filed over a dialog');
        await report.getByRole('button', { name: 'Send report' }).click();
        await page.getByText('Thanks, bug 77 was sent').waitFor({ timeout: 8000 });
        check(`${tag}: the report is sent with its screenshot`, !!posted?.screenshot);
        await report.getByRole('button', { name: 'Close' }).last().click();
        await sleep(300);
        check(`${tag}: after the report, the dialog is still open`, await d.dialog(page).isVisible());
      }
    } catch (e) {
      check(`${tag}: ran`, false, String(e).split('\n')[0].slice(0, 200));
    } finally {
      await ctx.close();
    }
  }
}
await browser.close();
done();
