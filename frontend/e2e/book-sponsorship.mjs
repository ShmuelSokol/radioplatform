import { chromium, devices } from 'playwright';
/**
 * The "Book a sponsorship" window against a real backend: from a client page, the four steps
 * with how full each slot is, the sentence before saving, saving, the new contract on the page;
 * desktop and iPhone; the menu's way in with a client picker; Esc closes.
 *   BASE_URL=http://localhost:3000 API=http://localhost:8000 KBR_ADMIN_PASSWORD=... node e2e/book-sponsorship.mjs
 */
const BASE=process.env.BASE_URL||'http://localhost:3000', API=`${process.env.API||'http://localhost:8000'}/api/v1`, OUT=process.env.SHOTS_DIR||'/tmp/book-sponsorship';
import { mkdirSync } from 'fs'; mkdirSync(OUT,{recursive:true});
const login = await (await fetch(`${API}/auth/login`,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({email:process.env.KBR_ADMIN_EMAIL||'admin',password:process.env.KBR_ADMIN_PASSWORD})})).json();
const H={Authorization:`Bearer ${login.access_token}`};
const clients = await (await fetch(`${API}/sponsors/clients`,{headers:H})).json();
const pita = clients.find(c=>c.name.includes('Pita')) ?? clients[0];
const stations = (await (await fetch(`${API}/stations`,{headers:H})).json()).stations;
const station = stations.find(s=>/Jerusalem/.test(s.name)) ?? stations[0];
const fails=[]; const ok=(c,m)=>{ if(!c){fails.push(m);console.log('FAIL',m);} };
const b = await chromium.launch();
for (const [name, dev] of [['desktop',{viewport:{width:1440,height:900}}],['iphone',devices['iPhone 13']]]) {
  const ctx = await b.newContext({...dev, ...(name==='desktop'?{deviceScaleFactor:2}:{})});
  await ctx.addInitScript(([a,r,s])=>{localStorage.setItem('access_token',a);localStorage.setItem('refresh_token',r);localStorage.setItem('radio_last_station',s);},[login.access_token,login.refresh_token,station.id]);
  const p = await ctx.newPage();
  const errs=[]; p.on('pageerror',e=>errs.push(String(e))); p.on('console',m=>{ if(m.type()==='error') errs.push(m.text()); });
  await p.goto(`${BASE}/admin/sponsors/${pita.id}`);
  await p.getByTestId('book-open').click();
  const dlg = p.getByTestId('book-sponsorship'); await dlg.waitFor();
  await p.waitForTimeout(500);
  await p.screenshot({path:`${OUT}/${name}-1-spot.png`});
  await dlg.getByTestId('step-spot').getByRole('button').first().click();
  await dlg.getByRole('button',{name:'Next ›'}).click();
  await dlg.getByLabel('Station').selectOption({label:station.name});
  await p.screenshot({path:`${OUT}/${name}-2-days.png`});
  await dlg.getByRole('button',{name:'Next ›'}).click();
  await dlg.getByTestId('slot-room').first().waitFor();
  await p.waitForTimeout(300);
  await p.screenshot({path:`${OUT}/${name}-3-slots.png`});
  const roomText = await dlg.getByTestId('slot-room').first().textContent();
  ok(/of \d+ places taken/.test(roomText), `${name}: room text ${roomText}`);
  await dlg.getByRole('button',{name:'Next ›'}).click();
  await dlg.getByTestId('booking-sentence').waitFor();
  const sentence = await dlg.getByTestId('booking-sentence').textContent();
  ok(sentence.includes(station.name) && /times a day/.test(sentence), `${name}: sentence ${sentence}`);
  await dlg.getByPlaceholder('For example: Winter sale').fill(`Booking check ${name}`);
  await p.screenshot({path:`${OUT}/${name}-4-check.png`});
  // tap target sizes on phone
  if (name==='iphone') for (const btn of await dlg.getByRole('button').all()) { const bb=await btn.boundingBox(); if (bb && bb.height<40) ok(false, `small button ${await btn.textContent()} ${bb.height}`); }
  const overflow = await p.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth+1);
  ok(!overflow, `${name}: sideways scroll`);
  await dlg.getByRole('button',{name:'Save and plan the plays'}).click();
  await dlg.getByTestId('booking-saved').waitFor();
  await p.screenshot({path:`${OUT}/${name}-5-saved.png`});
  await dlg.getByRole('button',{name:'Done'}).click();
  await p.getByRole('tab',{name:/Contracts/}).click();
  ok(await p.getByText(`Booking check ${name}`).count() > 0, `${name}: new contract not on the client page`);
  ok(!errs.length, `${name}: page errors ${errs}`);
  await ctx.close();
}
// menu entry opens the window with a client picker
const ctx = await b.newContext({viewport:{width:1280,height:800}});
await ctx.addInitScript(([a,r])=>{localStorage.setItem('access_token',a);localStorage.setItem('refresh_token',r);},[login.access_token,login.refresh_token]);
const p = await ctx.newPage();
await p.goto(`${BASE}/admin/sponsors?book=1`);
await p.getByTestId('book-sponsorship').getByLabel('Client').waitFor();
await p.keyboard.press('Escape'); await p.waitForTimeout(300);
ok(!(await p.getByTestId('book-sponsorship').count()), 'Esc did not close');
ok(!p.url().includes('book='), 'url keeps book= after close');
// Record Spots: "Book this spot" on an approved recording opens the window with that spot chosen
await p.goto(`${BASE}/admin/spot-recordings`);
const bookThis = p.getByRole('link', { name: 'Book this spot' }).first();
if (await bookThis.waitFor({ timeout: 8000 }).then(() => true, () => false)) {
  await bookThis.click();
  const dlg = p.getByTestId('book-sponsorship'); await dlg.waitFor();
  const chosen = dlg.getByTestId('step-spot').locator('button[aria-pressed=true]');
  ok(await chosen.first().waitFor({ timeout: 8000 }).then(() => true, () => false), 'Book this spot: the spot is not chosen in the window');
  await dlg.getByRole('button', { name: 'Next ›' }).click();
  ok(await dlg.getByTestId('step-days').waitFor({ timeout: 3000 }).then(() => true, () => false), 'Book this spot: Next did not move on');
  await dlg.getByRole('button', { name: 'Close' }).click();
  ok(!/book=|asset=/.test(p.url()), `Book this spot: address keeps the booking after close (${p.url()})`);
} else console.log('note: no approved recording here, Book this spot not checked');
await b.close(); console.log(fails.length? fails : 'all ok'); process.exit(fails.length?1:0);
