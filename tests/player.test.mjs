import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import assert from 'node:assert/strict';

const source = await readFile(new URL('../src/chessmuse/static/player.js', import.meta.url), 'utf8');
const { Player } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('return to mainline chooses the current branch restoration in a multi-branch review',()=>{
  const player=Object.create(Player.prototype);
  player.demo={events:[
    {type:'set_position',state:{branch:'first'}},
    {type:'move',state:{branch:'first'}},
    {type:'set_position',state:{branch:null}},
    {type:'speak',state:{branch:null}},
    {type:'set_position',state:{branch:'second'}},
    {type:'move',state:{branch:'second'}},
    {type:'set_position',state:{branch:null}},
    {type:'speak',state:{branch:null}},
  ]};
  player.index=1;assert.equal(player.mainlineReturnIndex(),3);
  player.index=5;assert.equal(player.mainlineReturnIndex(),7);
});

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
  const demo={paragraphs:[{id:'one',audio:'/one.wav',duration:5},{id:'move',audio:'/move.mp3',duration:2}],events:[
    {type:'speak',paragraph:'one',state:{fen:'initial'}},
    {type:'move',paragraph:'move',animation_ms:550,before_state:{fen:'initial'},state:{fen:'branch'}},
    {type:'set_position',state:{fen:'mainline'}},
  ]};
  const renders=[];
  const motion={paused:0,played:0,cancelled:0,pause(){this.paused++;},play(){this.played++;},cancel(){this.cancelled++;}};
  const player=new Player(demo,(event,index,playing,frame)=>renders.push({fen:frame.state.fen,index,playing,animate:frame.animate,annotation:frame.annotation}),()=>{},audio,clock,motion);
  return {player,audio,tick,jobs,renders,motion};
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

test('muted move pause preserves remaining announcement time; seek clears pending work',async()=>{
  const {player,tick,jobs,renders}=setup();
  player.setVoice(false);player.seek(1);await player.play();tick(400);player.pause();
  tick(10000);assert.equal(player.index,1);
  await player.play();tick(1599);assert.equal(player.index,1);
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
  tick(2000);tick(0);assert.equal(player.playing,false);
});

test('audio failure pauses instead of silently skipping commentary',async()=>{
  const {player,audio,tick}=setup();await player.play();
  audio.onerror();tick(10000);
  assert.equal(player.playing,false);assert.equal(player.index,0);
});

test('move waits for audio to actually start and finishes before optional explanation',async()=>{
  const {player,audio,renders,tick}=setup();
  player.demo.events.push({type:'speak',paragraph:'one',state:{fen:'mainline'}});
  let startAudio;
  audio.play=function(){this.calls++;return new Promise(resolve=>{startAudio=resolve;});};
  player.seek(1);
  const pending=player.play();
  assert.equal(renders.at(-1).fen,'initial');
  assert.equal(renders.filter(frame=>frame.animate).length,0);
  startAudio();await pending;
  assert.equal(renders.at(-1).fen,'branch');
  assert.equal(renders.at(-1).animate,true);
  assert.ok(player.event.animation_ms < player.paragraph.duration*1000);
  tick(10000);assert.equal(player.index,1);
  audio.play=function(){return Promise.resolve();};
  audio.onended();tick(0);await Promise.resolve();
  assert.equal(player.index,3);
  assert.equal(player.event.type,'speak');
});

test('pause, buffering and resume control animation without starting a second move',async()=>{
  const {player,audio,renders,motion}=setup();player.seek(1);await player.play();
  assert.equal(renders.filter(frame=>frame.animate).length,1);
  const pausedBefore=motion.paused;
  audio.onwaiting();assert.equal(motion.paused,pausedBefore+1);
  audio.onplaying();
  audio.currentTime=.2;player.pause();
  assert.equal(motion.paused,pausedBefore+2);
  const playedBefore=motion.played;
  await player.play();
  assert.ok(motion.played>playedBefore);
  assert.equal(audio.currentTime,.2);
  assert.equal(renders.filter(frame=>frame.animate).length,1);
});

test('seeking during pending audio blocks late playback and old ended callbacks',async()=>{
  const {player,audio,renders}=setup();let resolve;
  player.seek(1);
  audio.play=()=>new Promise(done=>{resolve=done;});
  const pending=player.play();const ended=audio.onended;
  player.seek(2);resolve();await pending;ended();
  assert.equal(player.index,2);
  assert.equal(renders.at(-1).fen,'mainline');
  assert.equal(renders.filter(frame=>frame.animate).length,0);
});

test('unavailable move audio keeps the board at its starting position',async()=>{
  const {player,audio,renders}=setup();player.seek(1);
  audio.play=()=>Promise.reject(new Error('blocked'));
  await player.play();
  assert.equal(player.playing,false);
  assert.equal(renders.at(-1).fen,'initial');
  assert.equal(renders.filter(frame=>frame.animate).length,0);
  audio.play=()=>Promise.resolve();
  await player.play();
  assert.equal(player.playing,true);
  assert.equal(renders.at(-1).fen,'branch');
  assert.equal(renders.filter(frame=>frame.animate).length,1);
});

test('a move without further explanation goes straight to the next spoken move',async()=>{
  const {player,audio,renders}=setup();
  player.demo.events[2]={type:'move',paragraph:'move',animation_ms:550,
    before_state:{fen:'branch'},state:{fen:'next-move'}};
  player.seek(1);await player.play();audio.onended();await Promise.resolve();
  assert.equal(player.index,2);
  assert.equal(player.playing,true);
  assert.equal(renders.at(-1).fen,'next-move');
  assert.equal(renders.filter(frame=>frame.animate).length,2);
});


test('annotations start with their spoken beat, survive pause and clear on seek',async()=>{
  const {player,renders,audio}=setup();
  player.demo.paragraphs[0].annotation={id:'focus'};
  player.paint();assert.equal(renders.at(-1).annotation,null);
  await player.play();assert.equal(renders.at(-1).annotation.id,'focus');
  player.pause();assert.equal(renders.at(-1).annotation.id,'focus');
  player.seek(1);assert.equal(renders.at(-1).annotation,null);
  await player.play();audio.onended();
  assert.equal(renders.at(-1).annotation,null);
});

test('return button animates from the visible branch and paused transition resumes its remaining time',async()=>{
  const {player,renders,tick}=setup();
  player.demo.events[1].state.branch='test';
  player.demo.events[2]={type:'set_position',animation_ms:650,
    before_state:{fen:'branch-end',branch:'test'},state:{fen:'mainline',branch:null}};
  player.demo.events.push({type:'speak',paragraph:'one',state:{fen:'mainline',branch:null}});
  player.seek(1);await player.play();player.pause();
  player.returnToMainline();
  assert.equal(player.index,2);
  assert.equal(renders.at(-1).animate,true);
  assert.equal(renders.at(-1).fen,'mainline');
  assert.equal(player.transitionOrigin.fen,'branch');
  tick(250);player.pause();tick(1000);
  assert.equal(player.index,2);
  await player.play();tick(399);assert.equal(player.index,2);
  tick(1);assert.equal(player.index,3);
  assert.equal(player.playing,false);
});

test('automatic restoration waits for its animation before starting the next narration',async()=>{
  const {player,audio,tick,renders}=setup();
  player.demo.events[2]={type:'set_position',animation_ms:650,
    before_state:{fen:'branch',branch:'test'},state:{fen:'mainline',branch:null}};
  player.demo.events.push({type:'speak',paragraph:'one',state:{fen:'mainline',branch:null}});
  player.seek(1);await player.play();audio.onended();
  assert.equal(player.index,2);assert.equal(renders.at(-1).animate,true);
  tick(649);assert.equal(player.index,2);
  tick(1);assert.equal(player.index,3);
});
