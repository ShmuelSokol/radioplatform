(() => {
  window.__ctxs = []; window.__media = new Set();
  const O = window.AudioContext;
  window.AudioContext = class extends O { constructor(...a) { super(...a); window.__ctxs.push(this); } };
  const play = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function () { window.__media.add(this); return play.call(this); };
})();
