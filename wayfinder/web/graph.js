'use strict';
// Force-directed canvas view of the graph, highlighting the entities each MCP tool call touched.
const COLORS={Component:'#f2c14e',Person:'#7bdff2',Team:'#7bdff2',Project:'#cdb4db',Issue:'#90e0ef',DocPage:'#b8f2a1',
  Thread:'#5c6b65',Comment:'#8aa39a',PullRequest:'#f28482',Review:'#f5cac3',Branch:'#ff9f1c',Commit:'#e9c46a',
  Repository:'#f4a261',Directory:'#c9ada7',Decision:'#d8f26a',Question:'#a0c4ff',Argument:'#bdb2ff',Rationale:'#ffc6ff',
  Action:'#ff6b6b',Topic:'#4f6d64',Answer:'#ffd6a5'};
const PATH='#ffb347';
const canvas=document.getElementById('canvas'),ctx=canvas.getContext('2d');
const S={nodes:[],byId:new Map(),edges:[],adj:new Map(),view:{x:0,y:0,k:1},alpha:1,hidden:new Set(),hover:null,selected:null,
  session:'',entries:[],seq:0,visited:new Map(),current:-1,follow:true,allLabels:false,replaying:false,pulse:0};
const el=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

async function getJSON(url){const r=await fetch(url);if(!r.ok)throw new Error(url+': '+r.status);return r.json();}

function resize(){const r=canvas.getBoundingClientRect(),d=window.devicePixelRatio||1;canvas.width=r.width*d;canvas.height=r.height*d;ctx.setTransform(d,0,0,d,0,0);}

function addNode(n,near){
  if(S.byId.has(n.id))return S.byId.get(n.id);
  const anchor=near&&S.byId.get(near),labels=Object.keys(COLORS),i=Math.max(0,labels.indexOf(n.label));
  const angle=i/labels.length*Math.PI*2,r=anchor?20:260;
  const node={...n,x:(anchor?anchor.x:Math.cos(angle)*r)+(Math.random()-.5)*40,y:(anchor?anchor.y:Math.sin(angle)*r)+(Math.random()-.5)*40,vx:0,vy:0,deg:0};
  S.nodes.push(node);S.byId.set(n.id,node);return node;
}
function addEdge(source,type,target){
  const a=S.byId.get(source),b=S.byId.get(target);if(!a||!b||a===b)return;
  const key=source+'|'+type+'|'+target;if(S.adj.has(key))return;
  const e={a,b,type,key};S.edges.push(e);S.adj.set(key,e);a.deg++;b.deg++;
}
function edgeBetween(x,y){for(const e of S.edges){if((e.a.id===x&&e.b.id===y)||(e.a.id===y&&e.b.id===x))return e;}return null;}
const radius=n=>4+Math.min(10,Math.sqrt(n.deg)*1.6);
const visible=n=>!S.hidden.has(n.label)||S.visited.has(n.id);

function tick(){
  if(S.alpha<0.004)return;
  const nodes=S.nodes.filter(visible),k=S.alpha;
  for(let i=0;i<nodes.length;i++){const a=nodes[i];
    for(let j=i+1;j<nodes.length;j++){const b=nodes[j];let dx=b.x-a.x,dy=b.y-a.y,d2=dx*dx+dy*dy||0.01;
      if(d2>90000)continue;const f=900/d2*k;dx*=f;dy*=f;a.vx-=dx;a.vy-=dy;b.vx+=dx;b.vy+=dy;}}
  for(const e of S.edges){if(!visible(e.a)||!visible(e.b))continue;const dx=e.b.x-e.a.x,dy=e.b.y-e.a.y,d=Math.sqrt(dx*dx+dy*dy)||1,f=(d-70)/d*0.04*k;
    e.a.vx+=dx*f;e.a.vy+=dy*f;e.b.vx-=dx*f;e.b.vy-=dy*f;}
  for(const n of nodes){n.vx-=n.x*0.004*k;n.vy-=n.y*0.004*k;n.vx*=0.6;n.vy*=0.6;if(n!==S.drag){n.x+=n.vx;n.y+=n.vy;}}
  S.alpha*=0.985;
}

function toScreen(n){return[(n.x*S.view.k)+S.view.x,(n.y*S.view.k)+S.view.y];}
function toWorld(px,py){return[(px-S.view.x)/S.view.k,(py-S.view.y)/S.view.k];}

function pathFocus(){
  // The agent's route: the first entity each successful step focused on, in order.
  const route=[];
  for(const e of S.entries.slice(0,S.current+1)){const f=(e.focus&&e.focus.find(i=>S.byId.has(i)))||e.nodes.find(i=>S.byId.has(i));
    if(f&&route[route.length-1]!==f)route.push(f);}
  return route;
}

function draw(){
  tick();S.pulse=(S.pulse+0.05)%(Math.PI*2);
  const w=canvas.clientWidth,h=canvas.clientHeight;ctx.clearRect(0,0,w,h);
  const active=S.visited.size>0,step=S.entries[S.current],currentIds=new Set(step?step.nodes:[]);
  const stepEdges=new Set((step?step.edges:[]).map(e=>e.join('|')));
  ctx.lineWidth=1;
  for(const e of S.edges){if(!visible(e.a)||!visible(e.b))continue;
    const both=S.visited.has(e.a.id)&&S.visited.has(e.b.id),hot=stepEdges.has(e.key);
    ctx.strokeStyle=hot?PATH:both?'rgba(255,179,71,.55)':active?'rgba(120,140,130,.10)':'rgba(120,140,130,.28)';
    ctx.lineWidth=hot?2.5:both?1.6:1;const[ax,ay]=toScreen(e.a),[bx,by]=toScreen(e.b);
    ctx.beginPath();ctx.moveTo(ax,ay);ctx.lineTo(bx,by);ctx.stroke();}
  // Route between consecutive focus entities: solid along a real edge, dashed where the agent jumped (search, Cypher).
  const route=pathFocus();ctx.lineWidth=3;
  for(let i=1;i<route.length;i++){const a=S.byId.get(route[i-1]),b=S.byId.get(route[i]);if(!a||!b)continue;
    const[ax,ay]=toScreen(a),[bx,by]=toScreen(b);ctx.setLineDash(edgeBetween(a.id,b.id)?[]:[7,6]);ctx.strokeStyle=PATH;
    ctx.beginPath();ctx.moveTo(ax,ay);ctx.lineTo(bx,by);ctx.stroke();
    const ang=Math.atan2(by-ay,bx-ax),r=radius(b)*Math.max(.6,S.view.k)+4;ctx.setLineDash([]);ctx.fillStyle=PATH;ctx.beginPath();
    ctx.moveTo(bx-Math.cos(ang)*r,by-Math.sin(ang)*r);ctx.lineTo(bx-Math.cos(ang)*r-Math.cos(ang-.4)*10,by-Math.sin(ang)*r-Math.sin(ang-.4)*10);
    ctx.lineTo(bx-Math.cos(ang)*r-Math.cos(ang+.4)*10,by-Math.sin(ang)*r-Math.sin(ang+.4)*10);ctx.fill();}
  ctx.setLineDash([]);
  for(const n of S.nodes){if(!visible(n))continue;const[x,y]=toScreen(n),r=radius(n)*Math.max(.6,S.view.k);
    const seen=S.visited.has(n.id),now=currentIds.has(n.id);
    ctx.globalAlpha=active&&!seen?0.28:1;ctx.fillStyle=COLORS[n.label]||'#999';ctx.beginPath();ctx.arc(x,y,r,0,Math.PI*2);ctx.fill();
    if(seen){ctx.lineWidth=now?3:2;ctx.strokeStyle=PATH;ctx.beginPath();ctx.arc(x,y,r+(now?4+2*Math.sin(S.pulse):2),0,Math.PI*2);ctx.stroke();}
    if(n===S.selected){ctx.lineWidth=2;ctx.strokeStyle='#fff';ctx.beginPath();ctx.arc(x,y,r+7,0,Math.PI*2);ctx.stroke();}
    ctx.globalAlpha=1;
    if(S.allLabels||seen||n===S.hover||n===S.selected||(n.label==='Component')){
      ctx.font=(now?'600 ':'')+'12px system-ui';ctx.fillStyle=seen?'#fff':'#cfd8d3';
      const text=(seen?S.visited.get(n.id)+' · ':'')+String(n.name).slice(0,42);ctx.fillText(text,x+r+5,y+4);}}
  requestAnimationFrame(draw);
}

function fit(ids){
  const pts=(ids&&ids.length?ids.map(i=>S.byId.get(i)).filter(Boolean):S.nodes.filter(visible));if(!pts.length)return;
  const xs=pts.map(p=>p.x),ys=pts.map(p=>p.y),w=canvas.clientWidth,h=canvas.clientHeight;
  const minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys);
  const k=Math.min(2.2,Math.min(w/((maxX-minX)||1),h/((maxY-minY)||1))*0.7);
  S.view.k=k;S.view.x=w/2-(minX+maxX)/2*k;S.view.y=h/2-(minY+maxY)/2*k;
}

function legend(){
  // Built through the DOM: the page's Content-Security-Policy blocks inline style attributes.
  const counts={};for(const n of S.nodes)counts[n.label]=(counts[n.label]||0)+1;
  const box=el('legend');box.replaceChildren();
  for(const l of Object.keys(counts).sort()){
    const b=document.createElement('button'),dot=document.createElement('i');
    b.dataset.label=l;if(S.hidden.has(l))b.className='off';dot.style.background=COLORS[l]||'#999';
    b.append(dot,document.createTextNode(`${l} ${counts[l]}`));box.append(b);}
}

function stepText(e){
  const a=e.arguments||{},keys=Object.keys(a).filter(k=>a[k]!==null&&a[k]!==undefined&&k!=='brief');
  return keys.map(k=>k+'='+(typeof a[k]==='string'?a[k]:JSON.stringify(a[k]))).join('  ').slice(0,180);
}
function renderSteps(){
  el('steps').innerHTML=S.entries.map((e,i)=>`<li data-i="${i}" class="${i===S.current?'current':''} ${e.ok?'':'error'}">
    <span class="n">${e.seq}</span><span class="tool">${esc(e.tool)}</span>
    <div class="args">${esc(stepText(e))}</div><div class="count">${e.ok?`${e.nodes.length} entities · ${e.edges.length} relationships`:esc(e.error)}</div></li>`).join('');
  const cur=el('steps').querySelector('.current');if(cur)cur.scrollIntoView({block:'nearest'});
}

function applyStep(i){
  S.current=i;const e=S.entries[i];if(!e)return;
  for(const id of e.nodes){if(S.byId.has(id)&&!S.visited.has(id))S.visited.set(id,e.seq);}
  if(S.follow&&e.nodes.length)fit(e.nodes.filter(id=>S.byId.has(id)).slice(0,40));
  renderSteps();
}
function rebuild(upTo){S.visited=new Map();for(let i=0;i<=upTo;i++){const e=S.entries[i];for(const id of e.nodes)if(S.byId.has(id)&&!S.visited.has(id))S.visited.set(id,e.seq);}applyStep(upTo);}

function ingest(data){
  // Entities the agent touched but the overview omits (files, revisions) are added next to a known neighbor.
  for(const e of data.entries){
    for(const id of e.nodes){const info=data.nodes[id];if(info&&!S.byId.has(id)){const near=e.nodes.find(x=>S.byId.has(x));addNode(info,near);S.alpha=Math.max(S.alpha,.3);}}
    for(const [a,t,b] of e.edges)addEdge(a,t,b);
  }
}

async function poll(){
  try{
    const data=await getJSON('/api/trace?'+new URLSearchParams({session:S.session,after:String(S.seq)}));
    const sessions=data.sessions||[],select=el('session');
    const options=['<option value="">Latest (live)</option>'].concat(sessions.map(s=>`<option value="${esc(s.session)}">${esc(s.session)} · ${s.calls} calls · ${esc(s.started.slice(11,19))}</option>`));
    if(select.options.length!==options.length){const keep=select.value;select.innerHTML=options.join('');select.value=keep;}
    if(!S.session&&data.session&&S.entries.length&&S.entries[0].session!==data.session){S.entries=[];S.seq=0;S.visited=new Map();S.current=-1;return poll();}
    if(data.entries.length&&!S.replaying){ingest(data);legend();S.entries.push(...data.entries);S.seq=S.entries[S.entries.length-1].seq;rebuild(S.entries.length-1);}
    el('status').textContent=S.entries.length?`Session ${S.entries[0].session} · ${S.entries.length} tool calls · ${S.visited.size} entities visited`:'Waiting for MCP tool calls on this graph…';
  }catch(err){el('status').textContent='Trace unavailable: '+err.message;}
  setTimeout(poll,1000);
}

async function replay(){
  if(!S.entries.length)return;S.replaying=true;S.visited=new Map();
  for(let i=0;i<S.entries.length;i++){if(!S.replaying)break;applyStep(i);await new Promise(r=>setTimeout(r,1300));}
  S.replaying=false;
}

function pick(px,py){let best=null,bd=Infinity;for(const n of S.nodes){if(!visible(n))continue;const[x,y]=toScreen(n),r=radius(n)*Math.max(.6,S.view.k)+4,d=(x-px)**2+(y-py)**2;if(d<r*r&&d<bd){best=n;bd=d;}}return best;}
function showDetail(n){
  const degree=S.edges.filter(e=>e.a===n||e.b===n).length,step=S.visited.get(n.id);
  el('detail').innerHTML=`<div class="label">${esc(n.label)}</div><div><b>${esc(n.name)}</b></div>
    <div class="muted">${esc(n.id)}</div><div class="muted">${esc(n.source||'')} · ${degree} relationships${step?` · first touched in step ${step}`:''}</div>
    ${n.url&&/^https?:\/\//.test(n.url)?`<a href="${esc(n.url)}" target="_blank" rel="noopener">Open source</a>`:''}`;
}

function bind(){
  let drag=null,moved=false;
  canvas.addEventListener('mousedown',ev=>{const n=pick(ev.offsetX,ev.offsetY);drag={x:ev.offsetX,y:ev.offsetY,vx:S.view.x,vy:S.view.y,node:n};S.drag=n;moved=false;canvas.classList.add('dragging');});
  window.addEventListener('mouseup',()=>{if(drag&&!moved&&drag.node){S.selected=drag.node;showDetail(drag.node);}drag=null;S.drag=null;canvas.classList.remove('dragging');});
  canvas.addEventListener('mousemove',ev=>{
    if(drag){moved=moved||Math.abs(ev.offsetX-drag.x)+Math.abs(ev.offsetY-drag.y)>3;
      if(drag.node){const[wx,wy]=toWorld(ev.offsetX,ev.offsetY);drag.node.x=wx;drag.node.y=wy;S.alpha=Math.max(S.alpha,.1);}
      else{S.view.x=drag.vx+ev.offsetX-drag.x;S.view.y=drag.vy+ev.offsetY-drag.y;}return;}
    const n=pick(ev.offsetX,ev.offsetY);S.hover=n;const tip=el('tooltip');
    if(n){tip.hidden=false;tip.style.left=(ev.offsetX+14)+'px';tip.style.top=(ev.offsetY+14)+'px';tip.innerHTML=`<b>${esc(n.label)}</b> ${esc(n.name)}`;}else tip.hidden=true;});
  canvas.addEventListener('wheel',ev=>{ev.preventDefault();const f=ev.deltaY<0?1.12:1/1.12,[wx,wy]=toWorld(ev.offsetX,ev.offsetY);
    S.view.k=Math.min(6,Math.max(.15,S.view.k*f));S.view.x=ev.offsetX-wx*S.view.k;S.view.y=ev.offsetY-wy*S.view.k;},{passive:false});
  el('legend').addEventListener('click',ev=>{const b=ev.target.closest('button');if(!b)return;const l=b.dataset.label;S.hidden.has(l)?S.hidden.delete(l):S.hidden.add(l);S.alpha=Math.max(S.alpha,.2);legend();});
  el('steps').addEventListener('click',ev=>{const li=ev.target.closest('li');if(li){S.replaying=false;rebuild(Number(li.dataset.i));}});
  el('fit').onclick=()=>fit();el('follow').onchange=ev=>S.follow=ev.target.checked;el('labels').onchange=ev=>S.allLabels=ev.target.checked;
  el('replay').onclick=()=>replay();
  el('clear').onclick=()=>{S.replaying=false;S.visited=new Map();S.current=-1;renderSteps();fit();};
  el('session').onchange=ev=>{S.session=ev.target.value;S.entries=[];S.seq=0;S.visited=new Map();S.current=-1;renderSteps();};
  window.addEventListener('resize',resize);
}

async function main(){
  resize();bind();
  const data=await getJSON('/api/graph');
  el('graph-name').textContent=data.graph+(data.truncated?' · truncated':'');
  for(const n of data.nodes)addNode(n);for(const e of data.edges)addEdge(e.source,e.type,e.target);
  // Settle the layout before the first frame so the recording starts on a stable picture.
  for(let i=0;i<260;i++)tick();fit();legend();requestAnimationFrame(draw);poll();
}
main().catch(err=>{el('status').textContent='Graph unavailable: '+err.message;});
