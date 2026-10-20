import {readFile} from 'node:fs/promises';
import {test} from 'node:test';
import assert from 'node:assert/strict';

const source=await readFile(new URL('../src/chessmuse/static/import.js',import.meta.url),'utf8');
const {prepareGame}=await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('import waits for real job completion and reports progress',async()=>{
  const steps=[{job_id:'one'}, {status:'processing',progress:35,message:'短句'},
    {status:'processing',progress:75,message:'语音'}, {status:'complete',progress:100,message:'完成',result:{id:'ready'}}];
  const progress=[],requests=[];
  const fetcher=async(url,options)=>{requests.push({url,options});return{ok:true,json:async()=>steps.shift()};};
  const result=await prepareGame({game:{moves:['h2e2']}},(value,text)=>progress.push(value),fetcher,async()=>{});
  assert.deepEqual(progress,[0,35,75,100]);
  assert.equal(result.id,'ready');assert.equal(requests[0].options.method,'POST');
  assert.ok(requests.slice(1).every(item=>item.url==='/api/jobs/one'));
});

test('failed imports never fabricate completion',async()=>{
  const steps=[{job_id:'one'},{status:'error',progress:35,message:'生成失败'}];
  const progress=[];
  await assert.rejects(prepareGame({example_id:'opening-v1'},value=>progress.push(value),
    async()=>({ok:true,json:async()=>steps.shift()}),async()=>{}),/生成失败/);
  assert.ok(!progress.includes(100));
});
