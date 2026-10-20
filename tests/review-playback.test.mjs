// Integration against the shipped famous-game script, using real Player logic.
import {readFile} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {test} from 'node:test';
import assert from 'node:assert/strict';

const source = await readFile(new URL('../src/chessmuse/static/player.js',import.meta.url),'utf8');
const {Player} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('complete famous review plays all speech, branches and final mainline without stale state',async()=>{
  const result=spawnSync('uv',['run','python','-c',
    "import json; from chessmuse.library import GameLibrary; from chessmuse.demo import compile_demo; l=GameLibrary(); print(json.dumps(compile_demo(l.example('qima-13'),require_audio=False,narration_path=l.narration_path('qima-13'))))"],{encoding:'utf8'});
  assert.equal(result.status,0,result.stderr);
  const demo=JSON.parse(result.stdout);
  // Offline CI verifies event sequencing without requiring local MP3s.
  for(const paragraph of demo.paragraphs) {paragraph.audio=`/${paragraph.id}.mp3`;paragraph.duration=1;}
  const jobs=[];
  const audio={pause(){},play(){return Promise.resolve();},load(){},removeAttribute(){}};
  const clock={performance:{now:()=>0},setTimeout(fn){jobs.push(fn);return fn;},clearTimeout(fn){const i=jobs.indexOf(fn);if(i>=0)jobs.splice(i,1);}};
  const visited=[];let lastState;let status;
  const player=new Player(demo,(event,index,playing,frame)=>{
    lastState=frame.state;
    if(playing)visited.push(event.id);
  },message=>status=message,audio,clock);
  await player.play();
  for(let n=0;n<demo.events.length+5 && player.playing;n++) {
    if(player.paragraph) audio.onended();
    else jobs.shift()();
    await Promise.resolve();
  }
  assert.equal(player.index,demo.events.length-1);
  assert.equal(player.playing,false);
  assert.match(status,/播放结束/);
  assert.equal(new Set(visited).size,demo.events.length);
  assert.equal(lastState.ply,25);
  assert.equal(lastState.branch,null);
  assert.equal(lastState.fen,demo.events.at(-1).state.fen);
  player.seek(0);
  assert.equal(lastState.ply,0);
  assert.equal(lastState.branch,null);
});
