import { chromium } from 'playwright-core';
import { mkdirSync } from 'fs';

const BASE = 'http://localhost:3000';
const OUT = 'screenshots/v2-smoke';
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
const errors = [];
page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
page.on('console', (m) => { if (m.type() === 'error') errors.push(`console: ${m.text()}`); });

// 1. V1 stations
await page.goto(`${BASE}/stations`, { waitUntil: 'networkidle' });
await page.screenshot({ path: `${OUT}/01-v1-stations.png` });
console.log('01 v1 stations OK');

// 2. Toggle to V2 via the navbar switch
const toggle = page.locator('nav button:has-text("V2")').first();
await toggle.click();
await page.waitForTimeout(1200);
await page.screenshot({ path: `${OUT}/02-v2-stations.png` });
console.log('02 v2 stations OK');

// 3. Click first station card -> V2 listen page
const firstCard = page.locator('a[href^="/listen/"]').first();
if (await firstCard.count()) {
  await firstCard.click();
  await page.waitForTimeout(2000);
  await page.screenshot({ path: `${OUT}/03-v2-listen.png` });
  console.log('03 v2 listen OK');

  // 4. Press play
  const playBtn = page.locator('button[title="Listen Live"]');
  if (await playBtn.count()) {
    await playBtn.click();
    await page.waitForTimeout(2500);
    await page.screenshot({ path: `${OUT}/04-v2-listen-playing.png` });
    console.log('04 v2 playing OK');
  }
} else {
  console.log('no stations found — skipping listen test');
}

// 5. Toggle back to V1 (persistence check via reload)
await page.locator('nav button:has-text("V1")').first().click();
await page.waitForTimeout(800);
await page.screenshot({ path: `${OUT}/05-back-to-v1.png` });
const stored = await page.evaluate(() => localStorage.getItem('ui_version'));
console.log(`05 back to v1 OK (localStorage=${stored})`);

// 6. Reload — verify persistence
await page.evaluate(() => localStorage.setItem('ui_version', 'v2'));
await page.reload({ waitUntil: 'networkidle' });
await page.waitForTimeout(800);
const isDark = await page.evaluate(() => !!document.querySelector('.v2-root'));
console.log(`06 v2 persists after reload: ${isDark}`);
await page.screenshot({ path: `${OUT}/06-v2-persisted.png` });

console.log('\nJS errors:', errors.length ? errors.join('\n') : 'none');
await browser.close();
