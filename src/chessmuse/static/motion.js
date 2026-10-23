// Own the browser animations so pause/resume/seek affect the moving pieces too.
export class BoardMotion {
  constructor() { this.animations = new Set(); this.cleanups = new Map(); }

  start(element, from, to, duration) {
    this.frames(element, [{transform:from}, {transform:to}], duration);
  }

  frames(element, keyframes, duration, cleanup = () => {}) {
    const animation = element.animate(keyframes, {duration, easing:"ease-in-out"});
    this.animations.add(animation);
    this.cleanups.set(animation, cleanup);
    const finish = () => {
      this.animations.delete(animation);
      const callback = this.cleanups.get(animation);
      this.cleanups.delete(animation);
      callback?.();
    };
    animation.finished.then(finish, finish);
  }

  pause() { for (const animation of this.animations) animation.pause(); }
  play() { for (const animation of this.animations) animation.play(); }
  cancel() {
    for (const animation of this.animations) {
      animation.cancel();
      this.cleanups.get(animation)?.();
    }
    this.cleanups.clear();
    this.animations.clear();
  }
}
