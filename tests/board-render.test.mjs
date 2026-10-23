import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';
import {test} from 'node:test';
import assert from 'node:assert/strict';

const source=await readFile(new URL('../src/chessmuse/static/app.js',import.meta.url),'utf8');
function setup() {
  const ids=new Map(),motions=[];
  class Element {
    constructor(tag){this.tag=tag;this.children=[];this.style={};this.dataset={};this.attrs={};}
    setAttribute(key,value){this.attrs[key]=value;if(key==='id')ids.set(value,this);}
    append(...children){for(const child of children){child.parent=this;this.children.push(child);}}
    replaceChildren(){this.children=[];}
    remove(){this.parent.children=this.parent.children.filter(child=>child!==this);}
  }
  const board=new Element('svg');ids.set('board',board);
  const context={document:{getElementById:id=>ids.get(id),createElementNS:(ns,tag)=>new Element(tag)},
    matchMedia:()=>({matches:false}),BoardMotion:class {
      start(element,from,to,duration){motions.push({element,from,to,duration});}
      frames(element,frames,duration,cleanup){motions.push({element,frames,duration});cleanup?.();}
    }};
  const code=source.slice(0,source.lastIndexOf('start().catch')).split('\n').filter(line=>!line.startsWith('import ')).join('\n');
  runInNewContext(code+';globalThis.hooks={drawGrid,drawPosition,drawAttention};',context);
  context.hooks.drawGrid();
  return {...context.hooks,ids,motions};
}

const rook=(id,file,rank)=>({id,code:'R',file,rank});
test('restoring the two rooks preserves identity, slides survivors and fades captured pieces back in',()=>{
  const {drawPosition,ids,motions}=setup();
  const initial={fen:'4k4/9/9/9/9/9/9/9/9/R3K3R w',pieces:[rook('a0',0,9),rook('i0',8,9)]};
  const saved=JSON.stringify(initial);
  drawPosition(initial);
  const first=[...ids.get('pieces').children];
  assert.equal(JSON.stringify(initial),saved,'rendering must not put DOM elements into saved snapshots');
  drawPosition({fen:'4k4/9/9/9/9/9/9/9/9/4KR3 b',pieces:[rook('i0',5,9)]},650);
  assert.equal(motions[0].element,first[1]);
  assert.equal(motions[0].to,'translate(326px,552px)');
  drawPosition(initial,650);
  assert.ok(ids.get('pieces').children.includes(first[1]));
  assert.ok(motions.some(m=>m.element===first[1] && m.to==='translate(494px,552px)'));
  assert.ok(motions.some(m=>m.frames?.[0].opacity===0),'captured rook returns with a fade');
});

test('attention lines stay beneath pieces and are cleared when the board changes',()=>{
  const {drawAttention,ids}=setup();
  const annotation={id:'attack',fen:'position',label:'炮架',rings:['b7','e7','g7'],arrows:[{from:'b7',to:'g7'}]};
  drawAttention(annotation,{fen:'position'});
  assert.equal(ids.get('attention-lines').children.length,1);
  assert.equal(ids.get('attention').children.filter(e=>e.tag==='circle').length,3);
  const board=ids.get('board').children;
  assert.ok(board.indexOf(ids.get('attention-lines'))<board.indexOf(ids.get('pieces')));
  drawAttention(annotation,{fen:'other-position'});
  assert.equal(ids.get('attention').children.length,0);
  assert.equal(ids.get('attention-lines').children.length,0);
});
