import { chromium } from 'playwright-core';
import { mkdirSync } from 'fs';

const BASE = 'https://www.kbrlive.com';
const OUT = 'screenshots/v2-prod';
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
const errors = [];
page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));

await page.goto(`${BASE}/stations`, { waitUntil: 'networkidle' });
// Force V2 on and reload
await page.evaluate(() => localStorage.setItem('ui_version', 'v2'));
await page.reload({ waitUntil: 'networkidle' });
await page.waitForTimeout(1500);
const isV2 = await page.evaluate(() => !!document.querySelector('.v2-root'));
await page.screenshot({ path: `${OUT}/01-v2-stations-prod.png` });
console.log(`V2 active on prod stations: ${isV2}`);

const firstCard = page.locator('a[href^="/listen/"]').first();
if (await firstCard.count()) {
  await firstCard.click();
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${OUT}/02-v2-listen-prod.png` });
  console.log('V2 listen page rendered on prod');
}

console.log('JS errors:', errors.length ? errors.join('\n') : 'none');
await browser.close();
