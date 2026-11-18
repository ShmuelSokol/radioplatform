/**
 * How far a bug-report screenshot is from the real screen. Runs in the page: both pictures
 * are drawn at the same small size and compared pixel by pixel. Used by
 * e2e/bug-screenshot-faithful.mjs (Chromium) and e2e/webkit/bug-screenshot-webkit.py.
 *
 * Returns { w, h, off, mean }: `off` = share of pixels whose colour differs by more than
 * 60 (sum of the three channels, 0-765), `mean` = average difference.
 */
export const COMPARE_JS = `
const [a, b, W] = arguments.length > 2 ? arguments : [arguments[0], arguments[1], 225];
const load = (src) => new Promise((ok, bad) => { const i = new Image(); i.onload = () => ok(i); i.onerror = bad; i.src = src; });
return Promise.all([load(a), load(b)]).then(([ia, ib]) => {
  const H = Math.round(W * ia.naturalHeight / ia.naturalWidth);
  const px = (img) => { const c = document.createElement('canvas'); c.width = W; c.height = H;
    const g = c.getContext('2d'); g.drawImage(img, 0, 0, W, H); return g.getImageData(0, 0, W, H).data; };
  const da = px(ia), db = px(ib);
  let off = 0, sum = 0;
  for (let i = 0; i < da.length; i += 4) {
    const d = Math.abs(da[i] - db[i]) + Math.abs(da[i + 1] - db[i + 1]) + Math.abs(da[i + 2] - db[i + 2]);
    sum += d; if (d > 60) off++;
  }
  const n = da.length / 4;
  return { w: W, h: H, off: Math.round(off / n * 1000) / 10, mean: Math.round(sum / n * 10) / 10,
           ratio: [ia.naturalWidth / ia.naturalHeight, ib.naturalWidth / ib.naturalHeight] };
});`;

/** compareShots(page, a, b): the same comparison run in a Playwright page. */
export function compareShots(page, a, b, W = 225) {
  return page.evaluate(`(async () => { ${COMPARE_JS.replace('arguments.length > 2 ? arguments : [arguments[0], arguments[1], 225]', '__ARGS__')} })()`.replace('__ARGS__', JSON.stringify([a, b, W])));
}
