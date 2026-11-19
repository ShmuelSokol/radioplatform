/**
 * Writes src/styles/qnet-site-colors.css: the colour half of the KBR Studio ("qnet")
 * look for the admin and sponsor pages (see wiki/qnet-site-theme.md).
 *
 * The pages are written with Tailwind colour classes for two older looks (a light one
 * and a dark navy one). Instead of rewriting every page, this reads every colour class
 * the pages use and writes one rule per class that turns it into its grey-window
 * equivalent under `.qn-site`: light text meant for dark panels becomes dark, dark
 * panels become the grey window face, dark tints become light tints, grey lines become
 * the bevel grey. `npm run build` runs it, so a class added to a page is picked up.
 *
 *   node scripts/gen-qnet-colors.mjs          write the file
 *   node scripts/gen-qnet-colors.mjs --check  exit 1 if the file is out of date
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const colors = createRequire(import.meta.url)('tailwindcss/colors');
const OUT = path.join(ROOT, 'src/styles/qnet-site-colors.css');

const GREYS = new Set(['gray', 'slate', 'zinc', 'neutral', 'stone']);
const BLUES = new Set(['cyan', 'sky', 'blue', 'indigo', 'teal']);
const NAVY = '#000080';

// Every file whose markup is shown inside .qn-site.
function walk(dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else if (/\.tsx?$/.test(e.name)) out.push(p);
  }
  return out;
}
const SKIP = ['pages/public/', 'pages/admin/controlroom/', 'pages/admin/Dashboard.tsx', 'pages/admin/Databank.tsx']; // own looks
const files = walk(path.join(ROOT, 'src')).filter(f => !SKIP.some(s => f.split(path.sep).join('/').includes(s)));
const tokens = new Set();
const TOKEN = /(?:[a-z-]+:)*(?:text|bg|border|divide|placeholder)-(?:\[#[0-9a-fA-F]{3,8}\]|[a-z]+(?:-\d{2,3})?)(?:\/\d{1,3})?/g;
for (const f of files) for (const m of fs.readFileSync(f, 'utf8').matchAll(TOKEN)) tokens.add(m[0]);

function hexToRgb(hex) {
  let h = hex.replace('#', '');
  if (h.length === 3) h = [...h].map(c => c + c).join('');
  return [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16));
}
function luminance(hex) {
  const [r, g, b] = hexToRgb(hex).map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function saturation(hex) {
  const [r, g, b] = hexToRgb(hex); const mx = Math.max(r, g, b), mn = Math.min(r, g, b);
  return mx === 0 ? 0 : (mx - mn) / mx;
}
// A navy or near-black hex written by hand on the dark pages ("#0a0a28", "#12123a", ...).
const isDarkPanelHex = hex => luminance(hex) < 0.08;

/** What a colour class means: { fam, shade, hex } for a palette colour, { hex } for a hand-written one. */
function parseColor(name) {
  const arb = name.match(/^\[(#[0-9a-fA-F]{3,8})\]$/);
  if (arb) return { hex: arb[1].toLowerCase() };
  if (name === 'white') return { fam: 'white', hex: '#ffffff' };
  if (name === 'black') return { fam: 'black', hex: '#000000' };
  const m = name.match(/^([a-z]+)-(\d{2,3})$/);
  if (!m) return null;
  if (m[1] === 'brand') return { fam: 'blue', shade: +m[2], hex: colors.blue[m[2]] ?? colors.blue[600] };
  const fam = colors[m[1]];
  if (!fam || typeof fam !== 'object' || !fam[m[2]]) return null;
  return { fam: m[1], shade: +m[2], hex: fam[m[2]] };
}

const darkOf = fam => (BLUES.has(fam) ? NAVY : colors[fam][['yellow', 'amber', 'lime'].includes(fam) ? 900 : 800]);

function textRule(c) {
  if (c.fam === 'white') return '#000';
  if (c.fam === 'black') return null;
  if (!c.fam) return luminance(c.hex) > 0.2 ? (saturation(c.hex) > 0.4 ? NAVY : '#000') : null;
  if (GREYS.has(c.fam)) return c.shade <= 500 ? '#404040' : '#000';
  return darkOf(c.fam);
}
function bgRule(c) {
  if (c.fam === 'white' || c.fam === 'black') return null; // white: panels, in qnet-site.css; black stays
  if (!c.fam) return isDarkPanelHex(c.hex) ? 'var(--qn-face)' : null;
  if (GREYS.has(c.fam)) {
    if (c.shade <= 50) return 'var(--qn-face)';
    if (c.shade === 100) return '#d4d4d4';
    if (c.shade === 200) return '#b4b4b4';
    if (c.shade === 300) return '#a0a0a0';
    if (c.shade >= 700) return 'var(--qn-face)';
    return null;
  }
  if (c.shade >= 900) return colors[c.fam][100]; // dark tint behind light text, now light tint behind dark text
  return null;
}
function borderRule(c) {
  if (c.fam === 'white' || c.fam === 'black') return null;
  if (!c.fam) return isDarkPanelHex(c.hex) || saturation(c.hex) < 0.6 ? 'var(--qn-shadow)' : null;
  if (GREYS.has(c.fam)) return 'var(--qn-shadow)';
  if (c.shade <= 300 || c.shade >= 900) return colors[c.fam][600];
  return null;
}
// Strong colours (badges, dots, bars) keep white text on them.
const isStrongBg = c => c.fam && !GREYS.has(c.fam) && c.fam !== 'white' && c.fam !== 'black' && c.shade >= 500 && c.shade <= 800;

const sel = (cls, state = '') => `.qn-site [class~="${cls.replace(/"/g, '')}"]${state}`;
const rules = [];
const strong = [];
const lightText = []; // text-*-50..300: written for dark pages, still meant to read light on a strong badge
for (const tok of [...tokens].sort()) {
  const parts = tok.split(':');
  const base = parts.pop();
  if (parts.length > 1 || (parts.length === 1 && parts[0] !== 'hover')) continue; // only plain and hover:
  const state = parts.length ? ':hover' : '';
  const m = base.match(/^(text|bg|border|divide|placeholder)-(.+?)(?:\/\d{1,3})?$/);
  if (!m) continue;
  const c = parseColor(m[2]);
  if (!c) continue;
  const kind = m[1];
  if (kind === 'text') {
    const v = textRule(c); if (v) rules.push(`${sel(tok, state)}{color:${v}}`);
    if (!state && c.fam && c.fam !== 'white' && c.fam !== 'black' && c.shade <= 300 && !base.includes('/')) lightText.push(tok);
  }
  else if (kind === 'placeholder') rules.push(`${sel(tok)}::placeholder{color:#808080}`);
  else if (kind === 'bg') {
    const v = bgRule(c);
    if (v) rules.push(`${sel(tok, state)}{background-color:${state && v === 'var(--qn-face)' ? '#d0d0d0' : v}}`);
    if (!state && isStrongBg(c) && !base.includes('/')) strong.push(tok);
  } else if (kind === 'border') { const v = borderRule(c); if (v) rules.push(`${sel(tok, state)}{border-color:${v}}`); }
  else if (kind === 'divide') { const v = borderRule(c); if (v) rules.push(`${sel(tok)}>*{border-color:${v}}`); }
}
// White text stays white on a strong colour (set on the same element, or on its parent badge/bar).
// A light text colour on a strong badge of its own (bg-blue-700 text-blue-200, the Live
// Shows status chip) is white too: dark text on a strong colour could not be read.
const lightIs = lightText.length ? `:is(${lightText.map(t => `[class~="${t}"]`).join(',')})` : '';
const keepWhite = strong.flatMap(t => [`${sel(t)}[class~="text-white"]`, `${sel(t)}:not(button):not(a) [class~="text-white"]`, ...(lightIs ? [`${sel(t)}:not(button):not(a)${lightIs}`] : [])]);

const css = `/* GENERATED by scripts/gen-qnet-colors.mjs from the colour classes the pages use. Do not edit. */\n${rules.join('\n')}\n${keepWhite.join(',\n')}{color:#fff}\n`;
if (process.argv.includes('--check')) {
  if (!fs.existsSync(OUT) || fs.readFileSync(OUT, 'utf8') !== css) { console.error('src/styles/qnet-site-colors.css is out of date: run node scripts/gen-qnet-colors.mjs'); process.exit(1); }
} else {
  fs.writeFileSync(OUT, css);
  console.log(`qnet colours: ${rules.length} rules, ${strong.length} strong backgrounds`);
}
