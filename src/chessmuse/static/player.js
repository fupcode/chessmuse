// A move is one event: its short announcement and animation run together.
export class Player {
  constructor(demo, render, status, audio = new Audio(), clock = globalThis,
              motion = {pause(){}, play(){}, cancel(){}}) {
    this.demo = demo;
    this.render = render;
    this.status = status;
    this.audio = audio;
    this.clock = clock;
    this.motion = motion;
    this.index = 0;
    this.playing = false;
    this.voice = true;
    this.timer = null;
    this.remaining = null;
    this.readyAudio = false;
    this.started = false;
    this.failed = false;
    this.revision = 0;
    audio.onerror = () => {
      if (this.playing && this.voice) this.fail("语音加载失败，请重试或关闭中文语音");
    };
    this.paint();
  }

  get event() { return this.demo.events[this.index]; }
  get paragraph() { return this.demo.paragraphs.find(p => p.id === this.event.paragraph); }
  mainlineReturnIndex() {
    // A review can contain several independent branches. Return to this
    // branch's next restoration, not the first restoration in the whole game.
    const restore = this.demo.events.findIndex((event, index) => index > this.index
      && event.type === "set_position" && !event.state.branch);
    if (restore < 0) return this.index;
    return this.demo.events[restore + 1]?.type === "speak" ? restore + 1 : restore;
  }
  now() { return this.clock.performance.now(); }

  clearTimer() {
    if (this.timer !== null) this.clock.clearTimeout(this.timer);
    this.timer = null;
  }

  displayState() {
    if (!this.started && this.event.type === "move")
      return {...this.event.before_state, caption:this.event.paragraph};
    if (!this.started && this.event.type === "set_position" && this.event.before_state)
      return this.transitionOrigin || this.event.before_state;
    return this.event.state;
  }

  paint(animate = false) {
    this.render(this.event, this.index, this.playing, {
      state:this.displayState(), animate,
      annotation:this.started && this.event.type === "speak" ? this.paragraph?.annotation : null,
    });
  }

  startMove() {
    if (!this.started) {
      this.started = true;
      this.paint(this.event.type === "move" || this.event.type === "set_position");
    }
    this.motion.play();
  }

  pause() {
    if (this.timer !== null) this.remaining = Math.max(0, this.deadline - this.now());
    this.clearTimer();
    this.audio.pause();
    this.motion.pause();
    this.playing = false;
    this.revision++;
    this.paint();
    this.status("已暂停");
  }

  seek(index, {origin = null} = {}) {
    this.pause();
    this.motion.cancel();
    this.index = Math.max(0, Math.min(index, this.demo.events.length - 1));
    this.audio.onended = this.audio.onplaying = this.audio.onwaiting = null;
    this.audio.removeAttribute("src");
    this.audio.load();
    this.readyAudio = false;
    this.started = false;
    this.transitionOrigin = origin;
    this.transitionOnly = false;
    this.failed = false;
    this.remaining = null;
    this.paint();
    this.status("已定位，点击播放继续");
  }

  returnToMainline() {
    const target = this.mainlineReturnIndex();
    const restore = this.demo.events[target]?.type === "set_position" ? target : target - 1;
    if (!this.displayState().branch || this.demo.events[restore]?.type !== "set_position") return;
    const resume = this.playing;
    const origin = this.displayState();
    this.seek(restore, {origin});
    this.transitionOnly = !resume;
    this.play();
  }

  setVoice(enabled) {
    const resume = this.playing;
    this.seek(this.index);
    this.voice = enabled;
    if (resume) this.play();
  }

  async play() {
    if (this.playing) return;
    if (this.failed) this.seek(this.index);
    this.playing = true;
    this.paint();
    this.status(this.voice ? "播放中 · 中文语音" : "播放中 · 静音字幕");
    if (this.paragraph && this.voice) {
      if (!this.readyAudio) {
        this.audio.src = this.paragraph.audio;
        this.audio.load();
        this.readyAudio = true;
      }
      const revision = this.revision;
      const active = () => this.revision === revision && this.playing;
      this.audio.onplaying = () => { if (active()) this.startMove(); };
      this.audio.onwaiting = () => { if (active()) this.motion.pause(); };
      this.audio.onended = () => { if (active()) this.advance(); };
      try {
        await this.audio.play();
        // Resolve only when audio starts, including after buffering.
        if (active()) this.startMove();
      } catch {
        // A seek or pause can intentionally interrupt a pending play() request.
        if (active()) {
          this.fail("语音未能播放，请点击播放重试，或关闭中文语音");
        }
      }
    } else {
      this.startMove();
      const delay = this.remaining ?? (this.paragraph ? this.paragraph.duration * 1000 : (this.event.animation_ms || 0));
      this.deadline = this.now() + delay;
      this.timer = this.clock.setTimeout(() => {
        this.timer = null;
        this.advance();
      }, delay);
    }
  }

  advance() {
    this.clearTimer();
    this.audio.pause();
    this.motion.cancel();
    this.playing = false;
    this.revision++;
    this.remaining = null;
    this.readyAudio = false;
    if (this.index === this.demo.events.length - 1) {
      this.paint();
      this.status("播放结束 · 可重播或跳转节点");
      return;
    }
    this.index++;
    this.started = false;
    this.transitionOrigin = null;
    if (this.transitionOnly) {
      this.transitionOnly = false;
      this.paint();
      this.status("已回到棋谱，点击播放继续");
    } else this.play();
  }

  fail(message) {
    this.pause();
    this.failed = true;
    this.status(message);
  }
}
