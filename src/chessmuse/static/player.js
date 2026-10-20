// One scheduler owns the timer, audio, cursor, and pause state.
export class Player {
  constructor(demo, render, status, audio = new Audio(), clock = globalThis) {
    this.demo = demo;
    this.render = render;
    this.status = status;
    this.audio = audio;
    this.clock = clock;
    this.index = 0;
    this.playing = false;
    this.voice = true;
    this.timer = null;
    this.remaining = null;
    this.readyAudio = false;
    this.revision = 0;
    audio.onended = () => { if (this.playing) this.advance(); };
    audio.onerror = () => {
      if (this.playing && this.voice) this.fail("语音加载失败，请重试或关闭中文语音");
    };
    this.paint();
  }

  get event() { return this.demo.events[this.index]; }
  now() { return this.clock.performance.now(); }

  clearTimer() {
    if (this.timer !== null) this.clock.clearTimeout(this.timer);
    this.timer = null;
  }

  paint() { this.render(this.event, this.index, this.playing); }

  pause() {
    if (this.timer !== null) this.remaining = Math.max(0, this.deadline - this.now());
    this.clearTimer();
    this.audio.pause();
    this.playing = false;
    this.revision++;
    this.paint();
    this.status("已暂停");
  }

  seek(index) {
    this.pause();
    this.index = Math.max(0, Math.min(index, this.demo.events.length - 1));
    this.audio.removeAttribute("src");
    this.audio.load();
    this.readyAudio = false;
    this.remaining = null;
    this.paint();
    this.status("已定位，点击播放继续");
  }

  setVoice(enabled) {
    const resume = this.playing;
    this.seek(this.index);
    this.voice = enabled;
    if (resume) this.play();
  }

  async play() {
    if (this.playing) return;
    this.playing = true;
    this.paint();
    this.status(this.voice ? "播放中 · 中文语音" : "播放中 · 静音字幕");
    if (this.event.type === "speak" && this.voice) {
      const paragraph = this.demo.paragraphs.find(p => p.id === this.event.paragraph);
      if (!this.readyAudio) {
        this.audio.src = paragraph.audio;
        this.audio.load();
        this.readyAudio = true;
      }
      const revision = this.revision;
      try {
        await this.audio.play();
      } catch {
        // A seek or pause can intentionally interrupt a pending play() request.
        if (this.revision === revision && this.playing) {
          this.fail("语音未能播放，请点击播放重试，或关闭中文语音");
        }
      }
    } else {
      let delay = 850;
      if (this.event.type === "speak") {
        const paragraph = this.demo.paragraphs.find(p => p.id === this.event.paragraph);
        delay = paragraph.duration * 1000;
      }
      delay = this.remaining ?? delay;
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
    this.play();
  }

  fail(message) {
    this.pause();
    this.readyAudio = false;
    this.status(message);
  }
}
