import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import assert from 'node:assert/strict';

const source = await readFile(new URL('../src/chessmuse/static/motion.js', import.meta.url), 'utf8');
const { BoardMotion } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('browser animation is created with the move duration, paused, resumed and cancelled',async()=>{
  let options, keyframes, finish;
  const calls=[];
  const animation={
    finished:new Promise(resolve=>{finish=resolve;}),
    pause(){calls.push('pause');},play(){calls.push('play');},cancel(){calls.push('cancel');},
  };
  const element={animate(frames,settings){keyframes=frames;options=settings;return animation;}};
  const motion=new BoardMotion();
  motion.start(element,'translate(0px,0px)','translate(56px,0px)',550);
  assert.equal(options.duration,550);
  assert.equal(keyframes[0].transform,'translate(0px,0px)');
  motion.pause();motion.play();motion.cancel();
  assert.deepEqual(calls,['pause','play','cancel']);
  assert.equal(motion.animations.size,0);
  motion.start(element,'translate(0px,0px)','translate(56px,0px)',400);
  finish();await Promise.resolve();
  assert.equal(motion.animations.size,0);
});
