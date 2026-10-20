import { Player } from "./player.js";

const $ = id => document.getElementById(id);
const NS = "http://www.w3.org/2000/svg";
const names = {r:"車",n:"馬",b:"象",a:"士",k:"將",c:"炮",p:"卒",R:"俥",N:"傌",B:"相",A:"仕",K:"帥",C:"炮",P:"兵"};
const svg = (name, attrs, text) => {
  const el = document.createElementNS(NS, name);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
  if (text) el.textContent = text;
  return el;
};
const x = file => 46 + file * 56;
const y = rank => 48 + rank * 56;
let pieces = [];
let previousFen = null;

function drawGrid() {
  const board = $("board");
  const line = (x1,y1,x2,y2) => board.append(svg("line",{x1,y1,x2,y2,stroke:"#8d7752","stroke-width":1.3}));
  for (let row=0; row<10; row++) line(x(0),y(row),x(8),y(row));
  for (let col=0; col<9; col++) {
    if (col===0 || col===8) line(x(col),y(0),x(col),y(9));
    else {line(x(col),y(0),x(col),y(4));line(x(col),y(5),x(col),y(9));}
  }
  for (const row of [0,7]) {line(x(3),y(row),x(5),y(row+2));line(x(5),y(row),x(3),y(row+2));}
  board.append(svg("text",{x:157,y:306,"text-anchor":"middle",fill:"#9c845f","font-size":22,"letter-spacing":12},"楚河"));
  board.append(svg("text",{x:389,y:306,"text-anchor":"middle",fill:"#9c845f","font-size":22,"letter-spacing":12},"漢界"));
  for(let col=0;col<9;col++) board.append(svg("text",{x:x(col),y:582,"text-anchor":"middle",fill:"#9c845f","font-size":12},"九八七六五四三二一"[col]));
  board.append(svg("g",{id:"markers"}));
  board.append(svg("g",{id:"pieces"}));
}

function drawPosition(state) {
  const wanted = [];
  state.fen.split(" ")[0].split("/").forEach((row,rank) => {
    let file=0;
    for(const code of row) {
      if (/\d/.test(code)) file+=Number(code);
      else wanted.push({code,file:file++,rank});
    }
  });
  if(previousFen !== state.fen) {
    // Retain exact-square pieces first; move the unmatched piece for animation.
    const unused = new Set(pieces);
    for(const p of wanted) {
      const same = pieces.find(q => unused.has(q) && q.code===p.code && q.file===p.file && q.rank===p.rank);
      if(same) {p.el=same.el;unused.delete(same);}
    }
    for(const p of wanted.filter(p=>!p.el)) {
      const moved = [...unused].find(q=>q.code===p.code);
      if(moved) {p.el=moved.el;unused.delete(moved);}
      else {
        const red=p.code===p.code.toUpperCase();
        p.el=svg("g",{class:"piece"});
        p.el.append(svg("circle",{r:23,fill:"#fcf0d2",stroke:red?"#a54a36":"#465346","stroke-width":1.7}));
        p.el.append(svg("circle",{r:19.5,fill:"none",stroke:red?"#a54a3650":"#46534650","stroke-width":.7}));
        p.el.append(svg("text",{y:8,"text-anchor":"middle",fill:red?"#aa4032":"#354c3f","font-size":25,"font-family":"KaiTi, STKaiti, serif"},names[p.code]));
        $("pieces").append(p.el);
      }
      p.el.style.transform=`translate(${x(p.file)}px,${y(p.rank)}px)`;
    }
    unused.forEach(p=>p.el.remove());
    pieces=wanted;
    previousFen=state.fen;
  }
  $("markers").replaceChildren();
  if(state.last_move) {
    for(const square of [state.last_move.slice(0,2),state.last_move.slice(2)]) {
      $("markers").append(svg("circle",{cx:x(square.charCodeAt(0)-97),cy:y(9-Number(square[1])),r:26,fill:"#9d7b3520",stroke:"#b58a37","stroke-width":2}));
    }
  }
  $("board").setAttribute("aria-label",`${state.branch ? "推演变化" : "实战主线"}，第${state.ply}半回合，${state.fen.endsWith(" w") ? "红" : "黑"}方行棋`);
}

async function start() {
  const response=await fetch("/api/demo");
  if(!response.ok) throw new Error("示例加载失败");
  const demo=await response.json();
  $("title").textContent=demo.title;
  $("description").textContent=demo.description;
  drawGrid();
  for(const [i,p] of demo.paragraphs.entries()) {
    const article=document.createElement("article");
    article.className="caption";article.id=`caption-${p.id}`;
    const title=document.createElement("h3");title.textContent=`0${i+1}  ${p.title}`;
    const text=document.createElement("p");text.textContent=p.text;
    article.append(title,text);$("captions").append(article);
  }
  let caption=null;
  const chapterButtons=[];
  const render=(event,index,playing)=>{
    drawPosition(event.state);
    $("line").textContent=event.state.branch ? `推演 · ${event.state.branch}` : "实战主线";
    $("turn").textContent=`第 ${event.state.ply} 半回合 · ${event.state.fen.endsWith(" w")?"红":"黑"}方行棋`;
    $("move-label").textContent=event.notation || (event.type==="set_position" ? "已切换局面" : "听解说 · 看局面");
    $("play").textContent=playing ? "暂停" : "播放解说";
    $("mainline").disabled=!event.state.branch;
    $("progress").value=index;
    $("counter").textContent=`${index+1} / ${demo.events.length}`;
    const activeChapter=demo.events.slice(0,index+1).findLast(e=>e.chapter)?.id;
    for(const [id,button] of chapterButtons) {
      button.classList.toggle("active",id===activeChapter);
      if(id===activeChapter) button.setAttribute("aria-current","step");
      else button.removeAttribute("aria-current");
    }
    if(caption!==event.state.caption) {
      caption=event.state.caption;
      for(const p of demo.paragraphs) $("caption-"+p.id).classList.toggle("active",p.id===caption);
      if(caption) {
        const article=$("caption-"+caption);
        $("captions").scrollTop=article.offsetTop-$("captions").firstElementChild.offsetTop;
      }
    }
  };
  const player=new Player(demo,render,message=>$("status").textContent=message);
  demo.events.forEach((event,index)=>{
    if(!event.chapter) return;
    const button=document.createElement("button");button.textContent=event.chapter;
    button.onclick=()=>player.seek(index);
    chapterButtons.push([event.id,button]);$("chapters").append(button);
  });
  $("play").disabled=false;$("replay").disabled=false;
  $("progress").max=demo.events.length-1;$("progress").disabled=false;
  $("play").onclick=()=>player.playing ? player.pause() : player.play();
  $("replay").onclick=()=>{player.seek(0);player.play();};
  $("progress").oninput=e=>player.seek(Number(e.target.value));
  $("voice").onchange=e=>player.setVoice(e.target.checked);
  $("mainline").onclick=()=>player.seek(demo.events.findIndex(e=>e.id==="return"));
  window.addEventListener("pagehide",()=>player.pause());
  player.paint();
  $("status").textContent="准备就绪 · 点击播放开始";
}

start().catch(error=>{
  $("title").textContent="示例暂时无法载入";
  $("status").textContent=error.message;
});
