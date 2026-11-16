// Emulate Safari: sound starts only inside a tap, on every page load
(() => {
  const active = () => navigator.userActivation && navigator.userActivation.isActive;
  const Orig = window.AudioContext;
  window.AudioContext = class extends Orig {
    constructor(...a) { super(...a); if (!active()) Orig.prototype.suspend.call(this); }
    resume() { return active() ? super.resume() : new Promise(() => {}); }
  };
  const play = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function () {
    return active() ? play.call(this) : Promise.reject(new DOMException('needs a tap', 'NotAllowedError'));
  };
})();
