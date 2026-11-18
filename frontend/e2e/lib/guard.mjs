/**
 * The "look at the screen" guard for every browser test (Shmuel, 2026-11-17: moving a
 * song worked, but the screen showed a warning from another station's rule; the test
 * only asked whether the move succeeded).
 *
 * A guarded page records everything a person would call "something went wrong":
 *   - a warning or error toast (amber or red), any role="alert" text, the error page
 *   - a console error, an uncaught error, an unhandled promise rejection
 *   - a request that failed or that the server answered with 4xx / 5xx
 * Anything recorded that the test did not say it expects is a finding, even when the
 * action itself worked.
 *
 * Use:
 *   const guard = guardPage(page, 'control room');
 *   guard.allow(/favicon/, 'no icon on the test server');          // for the whole test
 *   ... act ...
 *   await guard.expectOnly('move a song', [/expects story/]);        // this step may show these
 *   // -> returns the unexpected findings since the last call ([] = clean) and remembers them
 *   guard.findings()   // everything unexpected so far, for the test's final verdict
 *
 * A step that expects a message should list it in expectOnly; a message listed but not
 * seen is NOT a failure here (assert its presence in the test itself).
 */

const IN_PAGE = () => {
  if (window.__kbrGuardOn) return;
  window.__kbrGuardOn = true;
  const seen = new WeakMap();
  const report = (kind, text) => {
    try { window.__kbrGuardReport && window.__kbrGuardReport({ kind, text: String(text).slice(0, 400), url: location.pathname }); } catch { /* page closing */ }
  };
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0';
  };
  const look = () => {
    const found = [];
    for (const el of document.querySelectorAll('[data-testid="toast"], [role="status"]')) {
      const c = el.className || '';
      const kind = /bg-red|text-red|border-red/.test(c) ? 'error toast' : /bg-amber|bg-yellow|text-amber/.test(c) ? 'warning toast' : null;
      if (kind) found.push([el, kind]);
    }
    for (const el of document.querySelectorAll('[role="alert"]')) found.push([el, 'alert']);
    for (const el of document.querySelectorAll('[data-testid^="error-boundary"], [data-testid="request-error"], [data-testid="pfl-error"], [data-testid="save-warnings"]')) found.push([el, 'error shown']);
    for (const [el, kind] of found) {
      const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').replace(/✕$/, '').trim();
      if (!text || !visible(el)) continue;
      if (seen.get(el) === text) continue;
      seen.set(el, text);
      report(kind, text);
    }
  };
  const start = () => {
    new MutationObserver(look).observe(document.documentElement, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['class', 'role', 'hidden'] });
    look();
  };
  if (document.documentElement) start(); else document.addEventListener('DOMContentLoaded', start);
  window.addEventListener('unhandledrejection', (e) => report('unhandled rejection', (e.reason && (e.reason.message || e.reason)) || 'unknown'));
};

// Noise every test server has; real sites do not get these excuses by default
const DEFAULT_ALLOW = [
  [/net::ERR_ABORTED/, 'a request the page itself cancelled (navigation, a newer request)'],
  [/Failed to load resource: net::ERR_ABORTED/, 'same'],
  [(rec) => rec.kind === 'uncaught error' && /about:blank/.test(rec.url || ''), "a test's start-up script running on the blank page before the site loads"],
];

// Every guard made in this process, and what the whole test expects, for guardReport()
const EVERY = [];
const EVERYWHERE = [];

/** The whole test expects this (a step that takes the server down, a refused action it
 *  provokes on purpose). Say why: the reason is the record of what was excused. */
export function allowEverywhere(pattern, reason = '') { EVERYWHERE.push([pattern, reason]); }

export function guardPage(page, name = 'page', { allow = [] } = {}) {
  const all = [];          // every record, in order
  const unexpected = [];   // those nothing allowed
  const allowed = [...DEFAULT_ALLOW, ...allow.map(a => (Array.isArray(a) ? a : [a, '']))];
  let mark = 0;
  const push = (kind, text, url) => all.push({ kind, text, url: url || '', at: Date.now(), page: name });

  page.exposeBinding('__kbrGuardReport', (_src, rec) => push(rec.kind, rec.text, rec.url)).catch(() => {});
  page.addInitScript(IN_PAGE).catch(() => {});
  page.evaluate(IN_PAGE).catch(() => {});
  page.on('console', (m) => {
    if (m.type() !== 'error') return;
    const from = m.location()?.url || '';
    push('console error', from && !m.text().includes(from) ? `${m.text()} (${from})` : m.text(), page.url());
  });
  page.on('pageerror', (e) => push('uncaught error', e.message, page.url()));
  page.on('requestfailed', (r) => push('request failed', `${r.method()} ${r.url()} ${r.failure()?.errorText || ''}`, page.url()));
  page.on('response', (r) => {
    const s = r.status();
    if (s >= 400) push(`HTTP ${s}`, `${r.request().method()} ${r.url()}`, page.url());
  });

  const matches = (rec, pats) => pats.some((p) => {
    const pat = Array.isArray(p) ? p[0] : p;
    const hay = `${rec.kind}: ${rec.text}`;
    return typeof pat === 'function' ? pat(rec) : pat.test(hay);
  });

  const self = {
    name,
    allow(pattern, reason = '') { allowed.push([pattern, reason]); return this; },
    /** Records since the last call that neither the test-wide allow list nor `expected` covers. */
    async expectOnly(step, expected = []) {
      await page.waitForTimeout(60).catch(() => {});   // let a toast that is rendering land
      const fresh = all.slice(mark);
      mark = all.length;
      const bad = fresh.filter((rec) => !matches(rec, allowed) && !matches(rec, expected));
      // A failed fetch shows three ways (HTTP status, console line, toast): keep them all,
      // the reader wants to see what the person saw
      for (const rec of bad) unexpected.push({ ...rec, step });
      return bad;
    },
    /** Everything seen since the last expectOnly, without judging it (for assertions on expected messages). */
    peek() { return all.slice(mark); },
    all() { return all.slice(); },
    findings() { return unexpected.slice(); },
    format(recs = unexpected) {
      return recs.map((r) => `[${r.page}${r.step ? ' / ' + r.step : ''}] ${r.kind}: ${r.text}`).join('\n');
    },
    /** Everything recorded that neither this guard, a step, nor the whole test allowed. */
    leftOver() {
      const fresh = all.slice(mark).filter((rec) => !matches(rec, allowed));
      return [...unexpected, ...fresh].filter((rec) => !matches(rec, EVERYWHERE));
    },
  };
  EVERY.push(self);
  return self;
}

/** Guard every page of every context this browser opens (existing tests need no change:
 *  e2e/lib/fake-station.mjs hands out browsers already guarded). */
export function guardBrowser(browser, name = 'test') {
  const watch = (ctx) => { ctx.pages().forEach((p) => guardPage(p, name)); ctx.on('page', (p) => guardPage(p, name)); return ctx; };
  const newContext = browser.newContext.bind(browser);
  browser.newContext = async (...a) => watch(await newContext(...a));
  const newPage = browser.newPage.bind(browser);
  browser.newPage = async (...a) => { const p = await newPage(...a); guardPage(p, name); return p; };
  return browser;
}

/** What the whole run showed that nothing expected: one line per distinct thing with how
 *  often it was seen (ids and numbers folded together). [] = the screen stayed clean. */
export function guardReport() {
  const seen = new Map();
  for (const g of EVERY) {
    for (const rec of g.leftOver()) {
      const key = `${rec.kind}: ${rec.text}`.replace(/[0-9a-f]{8}-[0-9a-f-]{27}/g, '<id>').replace(/\d+/g, 'N');
      const e = seen.get(key) || { ...rec, count: 0 };
      e.count++;
      seen.set(key, e);
    }
  }
  return [...seen.values()];
}

/** Guard every page a context opens. Returns { guards, findings() }. */
export function guardContext(context, name = 'context', opts = {}) {
  const guards = [];
  const add = (page) => { const g = guardPage(page, `${name} #${guards.length + 1}`, opts); guards.push(g); return g; };
  context.pages().forEach(add);
  context.on('page', add);
  return {
    guards,
    async sweep(step, expected = []) {
      const out = [];
      for (const g of guards) out.push(...await g.expectOnly(step, expected));
      return out;
    },
    findings: () => guards.flatMap((g) => g.findings()),
    format: () => guards.map((g) => g.format()).filter(Boolean).join('\n'),
  };
}

/** For a test with its own verdict: print what nothing expected and return how many
 *  (0 with GUARD=report or GUARD=off). `process.exit(failures || guardVerdict() ? 1 : 0)`. */
export function guardVerdict() {
  if (process.env.GUARD === 'off') return 0;
  const odd = guardReport();
  for (const o of odd) console.log(`LOOK   ${o.kind}: ${o.text}${o.count > 1 ? `  (x${o.count})` : ''}  [on ${o.url || o.page}]`);
  if (odd.length) console.log(`\n${odd.length} thing(s) the screen, console or network showed that no check expected. Fix them, or say why they are expected with allowEverywhere(pattern, reason).`);
  return process.env.GUARD === 'report' ? 0 : odd.length;
}
