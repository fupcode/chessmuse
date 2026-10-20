// Own the browser animations so pause/resume/seek affect the moving pieces too.
export class BoardMotion {
  constructor() { this.animations = new Set(); }

  start(element, from, to, duration) {
    const animation = element.animate([{transform:from}, {transform:to}], {
      duration, easing:"ease-in-out",
    });
    this.animations.add(animation);
    animation.finished.then(
      () => this.animations.delete(animation),
      () => this.animations.delete(animation),
    );
  }

  pause() { for (const animation of this.animations) animation.pause(); }
  play() { for (const animation of this.animations) animation.play(); }
  cancel() {
    for (const animation of this.animations) animation.cancel();
    this.animations.clear();
  }
}
