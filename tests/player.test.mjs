import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import assert from 'node:assert/strict';

const source = await readFile(new URL('../src/chessmuse/static/player.js', import.meta.url), 'utf8');
const { Player } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

function setup() {
  let now = 0;
  const jobs = new Map();
  const audio = {
    currentTime: 0, playing: false, calls: 0,
    pause() { this.playing=false; },
    play() { this.playing=true; this.calls++; return Promise.resolve(); },
    load() { this.currentTime=0; },
    removeAttribute() { this.src=''; },
  };
  const clock = {
    performance: {now:()=>now},
    setTimeout(fn,ms) { const id=Symbol();jobs.set(id,{fn,at:now+ms});return id; },
    clearTimeout(id) { jobs.delete(id); },
  };
  const tick=ms=>{
    now+=ms;
    for (const [id,job] of [...jobs]) if(job.at<=now) {jobs.delete(id);job.fn();}
  };
  const demo={paragraphs:[{id:'one',audio:'/one.wav',duration:5}],events:[
    {type:'speak',paragraph:'one',state:{fen:'initial'}},
    {type:'move',state:{fen:'branch'}},
    {type:'set_position',state:{fen:'mainline'}},
  ]};
  const renders=[];
  const player=new Player(demo,(event,index,playing)=>renders.push({fen:event.state.fen,index,playing}),()=>{},audio,clock);
  return {player,audio,tick,jobs,renders};
}

test('speech completion gates subsequent moves; pause resumes same audio',async()=>{
  const {player,audio,tick}=setup();
  await player.play();tick(10000);
  assert.equal(player.index,0);
  audio.currentTime=2;player.pause();tick(10000);
  assert.equal(player.index,0);
  await player.play();assert.equal(audio.currentTime,2);
  audio.onended();assert.equal(player.index,1);
});

test('pause preserves remaining move delay and seek clears pending work',async()=>{
  const {player,tick,jobs,renders}=setup();
  player.seek(1);await player.play();tick(400);player.pause();
  tick(10000);assert.equal(player.index,1);
  await player.play();tick(449);assert.equal(player.index,1);
  tick(1);assert.equal(player.index,2);
  player.seek(0);tick(10000);
  assert.equal(player.index,0);assert.equal(jobs.size,0);
  assert.equal(renders.at(-1).fen,'initial');
});

test('seeking from branch restores mainline and replay resets audio',async()=>{
  const {player,audio,renders}=setup();
  player.seek(1);player.seek(2);
  assert.equal(renders.at(-1).fen,'mainline');
  player.seek(0);await player.play();audio.currentTime=3;
  player.seek(0);await player.play();
  assert.equal(audio.currentTime,0);
});

test('muted paragraphs use cached duration and complete without audio',async()=>{
  const {player,audio,tick}=setup();player.setVoice(false);
  await player.play();tick(4999);assert.equal(player.index,0);
  tick(1);assert.equal(player.index,1);assert.equal(audio.calls,0);
  tick(850);tick(850);assert.equal(player.playing,false);
});

test('audio failure pauses instead of silently skipping commentary',async()=>{
  const {player,audio,tick}=setup();await player.play();
  audio.onerror();tick(10000);
  assert.equal(player.playing,false);assert.equal(player.index,0);
});
