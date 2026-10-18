'use strict';
// Static site: everything below renders from data/*.json exported by build_data.py.
const COLORS={Component:'#f2c14e',Person:'#7bdff2',Project:'#cdb4db',Issue:'#90e0ef',DocPage:'#b8f2a1',DocSection:'#88c070',
  Thread:'#5c6b65',Comment:'#8aa39a',PullRequest:'#f28482',Review:'#f5cac3',Branch:'#ff9f1c',Commit:'#e9c46a',Repository:'#f4a261',
  Directory:'#c9ada7',Decision:'#d8f26a',Question:'#a0c4ff',Argument:'#bdb2ff',Rationale:'#ffc6ff',Action:'#ff6b6b',Topic:'#4f6d64'};
const PATH='#ffb347';
const $=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pct=v=>v==null?'–':(v*100).toFixed(1);
let D={};

async function load(){
  const names=['graph','algorithms','bench','facts','truth'];
  const parts=await Promise.all(names.map(n=>fetch(`data/${n}.json`).then(r=>r.json())));
  names.forEach((n,i)=>D[n]=parts[i]);
  D.byId=Object.assign({},D.bench.nodes); D.graph.nodes.forEach(n=>D.byId[n.id]=n);
}

// ---------------------------------------------------------------- hero
function hero(){
  const s=D.bench.summary,un=D.bench.questions-D.bench.answerable;
  const rows=[['graph','Graph tools over FalkorDB','Typed relationships, walked through the MCP tools the agent uses',true],
    ['vector',`Vector search (${D.bench.embedding_model.split('/').pop()})`,'Top 5 by similarity over the same records',false],
    ['bm25','BM25 full-text','Top 5 by keyword relevance over the same records',false]];
  $('bars').innerHTML=rows.map(([k,name,sub,lead])=>`<div class="bar ${lead?'lead':''}"><div class="num">${pct(s[k].answerable_f1)}</div>
    <div><div class="what"><b>${esc(name)}</b> · unanswerable correctly refused: ${Math.round(s[k].unanswerable_correct*un)} of ${un}</div>
    <div class="track"><div class="fill" style="width:${s[k].answerable_f1*100}%"></div></div><div class="sub">${esc(sub)}</div></div></div>`).join('');
  $('bars-note').textContent=`Answer F1 ×100 on ${D.bench.answerable} answerable questions of ${D.bench.questions}. The questions and gold answers were committed before any run; gold comes from the Plane, Slack and GitHub sources, not from the graph.`;
}

// ---------------------------------------------------------------- walk an answer
function name(id){const n=D.byId[id];return n?(n.label==='Issue'||n.label==='PullRequest'||n.label==='Branch'||n.label==='Commit'?id:`${n.name||id}`):id;}
function walk(qid){
  const w=D.bench.walks.find(x=>x.id===qid); if(!w)return;
  document.querySelectorAll('.starter').forEach(b=>b.classList.toggle('on',b.dataset.q===qid));
  $('walk-type').textContent=`${w.id} · ${w.type.replace('_',' ')}${w.answerable?'':' · unanswerable'}`;
  $('walk-question').textContent=w.question;
  // Columns: what read_cypher found, then each hop's targets one column to the right of its source.
  const col={},edges=[];
  w.calls.forEach(c=>{
    if(c.found) c.found.forEach(id=>{if(!(id in col))col[id]=0;});
    if(c.edges) c.edges.forEach(e=>{const from=c.args.from,other=e.s===from?e.t:e.s; if(!(from in col))col[from]=0; if(!(other in col))col[other]=col[from]+1; edges.push(e);});
  });
  drawWalk(col,edges,new Set(w.graph_answer),new Set(w.gold));
  $('walk-calls').innerHTML=w.calls.length?w.calls.map(c=>c.tool==='read_cypher'
    ?`<li><span class="tool">read_cypher</span> <span class="note">find the anchor</span><br><code>${esc(c.args.query)}</code><br><code>${esc(JSON.stringify(c.args.parameters))}</code><br><span class="note">${c.found.length?'found '+esc(c.found.join(', ')):'0 rows: nothing to start from'}</span></li>`
    :`<li><span class="tool">get_neighbors</span> <code>${esc(c.args.from)} · ${esc(c.args.direction)} · ${esc((c.args.types||[]).join('|'))}</code><br><span class="note">${c.edges.length} relationship${c.edges.length===1?'':'s'}: ${esc(c.edges.slice(0,6).map(e=>`${e.s} -${e.type}-> ${e.t}`).join('; '))}${c.edges.length>6?' …':''}</span></li>`).join('')
    :'<li class="note">No tool calls.</li>';
  const gold=new Set(w.gold),g=w.scores.graph;
  $('walk-answer').innerHTML=(w.graph_answer.length?w.graph_answer.map(id=>`<div class="answer"><span class="${gold.has(id)?'ok':'no'}">${gold.has(id)?'✓':'✗'}</span>${esc(id)} <span class="note">${esc(D.byId[id]?.name||'')}</span></div>`).join('')
      :`<div class="answer"><span class="${w.answerable?'no':'ok'}">${w.answerable?'✗':'✓'}</span>Nothing found${w.answerable?'':', which is the correct answer'}</div>`)
    +(w.answerable&&w.gold.filter(id=>!w.graph_answer.includes(id)).length?`<div class="note">Missed: ${esc(w.gold.filter(id=>!w.graph_answer.includes(id)).join(', '))}</div>`:'')
    +`<div class="note">${w.answerable?'F1 '+pct(g.f1):g.correct?'Correctly refused':'Should have returned nothing'}</div>`;
  $('walk-others').innerHTML=['vector','bm25'].map(k=>{const v=w.scores[k];return `<div class="other"><b>${k==='bm25'?'BM25':'Vector'}</b> ${w.answerable?'F1 '+pct(v.f1):'returned 5 records (no refusal)'}<div class="mono">${esc(v.predicted.slice(0,5).join(' · '))}</div></div>`;}).join('');
}
function drawWalk(col,edges,answer,gold){
  const svg=$('walk-svg'),W=Math.max(720,svg.clientWidth||900),H=300,ids=Object.keys(col);
  if(!ids.length){svg.innerHTML=`<text x="20" y="40" fill="currentColor" opacity=".6">No entity matched the question's anchor, so the graph returns nothing.</text>`;return;}
  const cols={};ids.forEach(id=>(cols[col[id]]=cols[col[id]]||[]).push(id));
  const nc=Object.keys(cols).length,pos={};
  Object.entries(cols).forEach(([c,list])=>{const shown=list.slice(0,9);shown.forEach((id,i)=>pos[id]=[110+(+c)*(W-220)/Math.max(1,nc-1||1),((H-40)/(shown.length+1))*(i+1)]);});
  if(nc===1)ids.forEach(id=>pos[id]&&(pos[id][0]=W/2));
  const line=edges.filter(e=>pos[e.s]&&pos[e.t]).map(e=>{const[a,b]=[pos[e.s],pos[e.t]];return `<line x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}" stroke="${PATH}" stroke-width="2" opacity=".75"/><text x="${(a[0]+b[0])/2}" y="${(a[1]+b[1])/2-6}" font-size="10" fill="currentColor" opacity=".55" text-anchor="middle">${esc(e.type)}</text>`;}).join('');
  const dots=Object.entries(pos).map(([id,[x,y]])=>{const n=D.byId[id]||{},c=COLORS[n.label]||'#999';
    return `<g><circle cx="${x}" cy="${y}" r="9" fill="${c}"/>${answer.has(id)?`<circle cx="${x}" cy="${y}" r="14" fill="none" stroke="${PATH}" stroke-width="2.5"/>`:''}
    <text x="${x}" y="${y+30}" font-size="12.5" fill="currentColor" text-anchor="middle">${gold.has(id)?'✓ ':''}${esc(id)}</text>
    <text x="${x}" y="${y+44}" font-size="10.5" fill="currentColor" opacity=".55" text-anchor="middle">${esc((n.label||'')+(n.name&&n.name!==id?' · '+n.name.slice(0,30):''))}</text></g>`;}).join('');
  svg.setAttribute('viewBox',`0 0 ${W} ${H}`);svg.innerHTML=line+dots;
}
function starters(){
  const picks=[['q37','Slack → PR → branch','A Slack announcement, the pull request it links, and the branch that PR came from: three hops across two tools.'],
    ['q32','A task’s pull requests','PRs linked from the Plane task and PRs whose text names its key, found by following both directions.'],
    ['q11','Five dependencies','The tasks the demo-recording task waits on, read straight off DEPENDS_ON.'],
    ['q30','The commits in a PR','Pull request #4’s commits, joined from GitHub’s own history.'],
    ['q44','A correct refusal','Nobody said “we migrated the payroll database”, so the graph finds no anchor and answers nothing. The text baselines still return five records.'],
    ['q35','The one it got wrong','ADR-001 states the decision, but extraction never produced it, so the graph has nothing to follow. Shown, not hidden.']];
  $('starters').innerHTML=picks.map(([q,t,d])=>`<button class="starter" data-q="${q}"><b>${esc(t)}</b><span>${esc(d)}</span></button>`).join('');
  $('starters').onclick=e=>{const b=e.target.closest('.starter');if(b)walk(b.dataset.q);};
}

// ---------------------------------------------------------------- capabilities
function caps(){
  const f=D.facts,s=f.sources,lag=f.freshness.stages.flatMap(x=>x.seconds);
  const cards=[
    [`${s.plane_items+s.plane_pages+s.plane_comments+s.slack_messages+s.pull_requests+s.branches+s.commits}`,'Live records from three tools',`Plane: ${s.plane_items} tasks, ${s.plane_pages} pages, ${s.plane_comments} comments. Slack: ${s.slack_messages} messages. GitHub: ${s.pull_requests} pull requests, ${s.branches} branches, ${s.commits} commits. Read through the real APIs, keeping real authorship and timestamps.`],
    [`${f.links.EXACT_REFERENCE+f.links.EXACT_IDENTIFIER}`,'Links with evidence only',`${f.links.EXACT_REFERENCE} links from URLs and ${f.links.EXACT_IDENTIFIER} from Plane keys (like FALKORDBTE-15) found in task, message, PR, commit and branch text. No fuzzy name matching. A link retires when its text changes.`],
    [`${f.freshness.passed}/${f.freshness.checks}`,'Stays current on its own',`Tasks and messages created, edited and deleted in real Plane and Slack reached the graph in ${Math.min(...lag).toFixed(0)}–${Math.max(...lag).toFixed(0)} s, with only the background watcher running.`],
    [`${f.history.source_revisions}`,'History is kept, not overwritten',`Every source text version is retained. ${f.history.retired_relationships} relationships are closed history beside ${f.history.current_relationships} current ones. Editing a source retires the interpretations quoted from it.`],
    [esc(f.action.task),'Acts, with a person’s approval',`The agent drafted “${esc(f.action.title)}”. A person approved its exact payload hash (${esc(f.action.hash.slice(0,12))}…) in a terminal, and only then was the Plane task created. A second execute returned the same task.`],
    ['4','Native graph algorithms as agent tools','PageRank, betweenness, connected components and shortest path, run by FalkorDB over a temporary projection of current records only. Try them in the explorer below.'],
    [`${Object.values(f.claims).reduce((a,b)=>a+b,0)}`,'Interpretations tied to quotes',`${Object.entries(f.claims).map(([k,v])=>`${v} ${k.toLowerCase()}${v>1?'s':''}`).join(', ')}. Each records its exact quote and source revision, for example “${esc(f.action.decision.evidence_quote)}”.`],
    ['MCP','Every step is visible','The agent uses 28 read tools and 2 action tools over MCP. A live graph view highlights each tool call’s entities and route next to the agent while it works.']];
  $('caps').innerHTML=cards.map(([big,t,p])=>`<div class="cap"><div class="big">${big}</div><h3>${t}</h3><p>${p}</p></div>`).join('');
}

// ---------------------------------------------------------------- stays true
const VERDICT={VERIFIED:'good',RECITE:'warn',OUT_OF_DATE:'bad',REMOVED:'bad',DISPUTED:'warn',UNSUPPORTED:'bad'};
const chip=r=>r?`<span class="chip" title="${esc(r.name)}"><i style="background:${COLORS[r.label]||'#999'}"></i>${esc(r.id)}</span>`:'';
function truth(){
  const T=D.truth; if(!T) return;
  $('verify').innerHTML=T.verify.map(v=>`<div class="vrow"><span class="verdict ${VERDICT[v.status]||''}">${esc(v.status.replace(/_/g,' '))}</span>
    <div><div>${esc(v.text)}</div><div class="cites">${v.evidence.length?'cites '+v.evidence.map(chip).join(' '):'no evidence cited'}
    ${v.replaced_by.length?' · cite instead '+v.replaced_by.map(chip).join(' '):''}
    ${v.missing_quotes.length?` · quote not in the source: “${esc(v.missing_quotes[0])}”`:''}</div></div></div>`).join('');
  const s=T.changes.summary;
  $('changes').innerHTML=`<div class="tnum">${s.created+s.edited+s.retired}</div><p>records changed in the 24 hours before this export: ${s.created} new, ${s.edited} edited, ${s.retired} retired; ${s.links_made} links made and ${s.links_broken} broken.</p>
    <div class="cites">${T.changes.examples.map(x=>`${chip(x)} <span class="note">${esc(x.change)}</span>`).join(' ')}</div>`;
  $('stale').innerHTML=`<div class="tnum">${T.stale.filter(x=>x.status!=='CURRENT').length}/${T.stale.length}</div><p>pages had code merged or tasks changed after their last edit.</p>`+
    T.stale.slice(0,3).map(x=>`<div class="srow"><b>${esc(x.page.name)}</b><div class="cites">since then: ${x.since.map(chip).join(' ')}</div></div>`).join('');
  $('risk').innerHTML=T.risk.map(r=>`<div class="srow"><b>${esc(r.component.name)}</b> <span class="verdict ${r.risk==='HIGH'?'bad':r.risk==='MEDIUM'?'warn':'good'}">bus factor ${r.bus_factor}</span>
    <div class="cites">${r.holders.map(h=>`${esc(h.name)} ${(h.share*100).toFixed(0)}% (${Object.entries(h.roles).map(([k,v])=>`${v} ${k.replace('_',' ')}`).join(', ')})`).join('; ')}</div></div>`).join('')+
    (T.action?`<p class="note">The approved Plane action: ${esc(T.action.verdict.replace(/_/g,' ').toLowerCase())}, ${T.action.evidence_changed} cited records changed since approval.</p>`:'');
}

// ---------------------------------------------------------------- graph explorer
const G={nodes:[],byId:new Map(),edges:[],view:{x:0,y:0,k:1},hl:new Map(),hlEdges:new Set(),sel:null,hover:null,mode:'overview',alpha:1};
const cv=$('graph'),ctx=cv.getContext('2d');
function setupGraph(){
  const labels=[...new Set(D.graph.nodes.map(n=>n.label))].sort();
  D.graph.nodes.forEach(n=>{const i=labels.indexOf(n.label),a=i/labels.length*Math.PI*2;const node={...n,x:Math.cos(a)*260+(Math.random()-.5)*60,y:Math.sin(a)*260+(Math.random()-.5)*60,vx:0,vy:0,deg:0};G.nodes.push(node);G.byId.set(n.id,node);});
  D.graph.edges.forEach(e=>{const a=G.byId.get(e.s),b=G.byId.get(e.t);if(a&&b&&a!==b){G.edges.push({a,b,type:e.type});a.deg++;b.deg++;}});
  const counts={};G.nodes.forEach(n=>counts[n.label]=(counts[n.label]||0)+1);
  $('legend').innerHTML=Object.keys(counts).sort().map(l=>`<span><i style="background:${COLORS[l]||'#999'}"></i>${l} ${counts[l]}</span>`).join('');
  for(let i=0;i<300;i++)tick();
  resize();fit();requestAnimationFrame(draw);bindGraph();
}
function tick(){
  if(G.alpha<.003)return;const k=G.alpha,n=G.nodes;
  for(let i=0;i<n.length;i++)for(let j=i+1;j<n.length;j++){const a=n[i],b=n[j];let dx=b.x-a.x,dy=b.y-a.y,d=dx*dx+dy*dy||.01;if(d>90000)continue;const f=900/d*k;dx*=f;dy*=f;a.vx-=dx;a.vy-=dy;b.vx+=dx;b.vy+=dy;}
  for(const e of G.edges){const dx=e.b.x-e.a.x,dy=e.b.y-e.a.y,d=Math.hypot(dx,dy)||1,f=(d-60)/d*.04*k;e.a.vx+=dx*f;e.a.vy+=dy*f;e.b.vx-=dx*f;e.b.vy-=dy*f;}
  for(const a of n){a.vx-=a.x*.004*k;a.vy-=a.y*.004*k;a.vx*=.6;a.vy*=.6;a.x+=a.vx;a.y+=a.vy;}G.alpha*=.985;
}
const R=n=>(3.5+Math.min(9,Math.sqrt(n.deg)*1.4))*(G.hl.has(n.id)&&G.mode==='pagerank'?1.6:1);
const S=n=>[n.x*G.view.k+G.view.x,n.y*G.view.k+G.view.y];
function resize(){const r=cv.getBoundingClientRect(),d=devicePixelRatio||1;cv.width=r.width*d;cv.height=r.height*d;ctx.setTransform(d,0,0,d,0,0);}
function fit(ids){const pts=ids&&ids.length?ids.map(i=>G.byId.get(i)).filter(Boolean):G.nodes;if(!pts.length)return;const w=cv.clientWidth,h=cv.clientHeight;
  const xs=pts.map(p=>p.x),ys=pts.map(p=>p.y),x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys);
  G.view.k=Math.min(2.4,Math.min(w/((x1-x0)||1),h/((y1-y0)||1))*.75);G.view.x=w/2-(x0+x1)/2*G.view.k;G.view.y=h/2-(y0+y1)/2*G.view.k;}
function draw(){
  tick();const w=cv.clientWidth,h=cv.clientHeight,active=G.hl.size>0,text=getComputedStyle(document.body).color;ctx.clearRect(0,0,w,h);
  for(const e of G.edges){const key=e.a.id+'>'+e.b.id,hot=G.hlEdges.has(key)||G.hlEdges.has(e.b.id+'>'+e.a.id);const[ax,ay]=S(e.a),[bx,by]=S(e.b);
    ctx.strokeStyle=hot?PATH:active?'rgba(128,140,134,.08)':'rgba(128,140,134,.22)';ctx.lineWidth=hot?3:1;ctx.beginPath();ctx.moveTo(ax,ay);ctx.lineTo(bx,by);ctx.stroke();}
  for(const n of G.nodes){const[x,y]=S(n),r=R(n)*Math.max(.7,G.view.k),on=G.hl.has(n.id);ctx.globalAlpha=active&&!on?.25:1;
    ctx.fillStyle=G.mode==='components'&&G.hl.has(n.id)?G.hl.get(n.id):COLORS[n.label]||'#999';ctx.beginPath();ctx.arc(x,y,r,0,Math.PI*2);ctx.fill();
    if(on&&G.mode!=='components'){ctx.strokeStyle=PATH;ctx.lineWidth=2.5;ctx.beginPath();ctx.arc(x,y,r+3,0,Math.PI*2);ctx.stroke();}
    if(n===G.sel){ctx.strokeStyle=text;ctx.lineWidth=2;ctx.beginPath();ctx.arc(x,y,r+6,0,Math.PI*2);ctx.stroke();}
    ctx.globalAlpha=1;if((on&&G.mode!=='components')||n===G.hover||n===G.sel||n.label==='Component'){ctx.fillStyle=text;ctx.font='12px system-ui';ctx.fillText(n.id,x+r+4,y+4);}}
  requestAnimationFrame(draw);
}
function pick(px,py){let best=null,bd=1e9;for(const n of G.nodes){const[x,y]=S(n),r=R(n)*Math.max(.7,G.view.k)+4,d=(x-px)**2+(y-py)**2;if(d<r*r&&d<bd){best=n;bd=d;}}return best;}
function bindGraph(){
  let drag=null;
  cv.onmousedown=e=>{drag={x:e.offsetX,y:e.offsetY,vx:G.view.x,vy:G.view.y,moved:false};};
  window.addEventListener('mouseup',e=>{if(drag&&!drag.moved){const n=pick(e.offsetX,e.offsetY);if(n){G.sel=n;detail(n);}}drag=null;});
  cv.onmousemove=e=>{if(drag){drag.moved=drag.moved||Math.abs(e.offsetX-drag.x)+Math.abs(e.offsetY-drag.y)>3;G.view.x=drag.vx+e.offsetX-drag.x;G.view.y=drag.vy+e.offsetY-drag.y;return;}
    const n=pick(e.offsetX,e.offsetY);G.hover=n;const t=$('tip');if(n){t.hidden=false;t.style.left=e.offsetX+12+'px';t.style.top=e.offsetY+12+'px';t.innerHTML=`<b>${esc(n.label)}</b> ${esc(n.id)}<br>${esc(n.name)}`;}else t.hidden=true;};
  cv.addEventListener('wheel',e=>{e.preventDefault();const f=e.deltaY<0?1.12:1/1.12,wx=(e.offsetX-G.view.x)/G.view.k,wy=(e.offsetY-G.view.y)/G.view.k;G.view.k=Math.min(6,Math.max(.15,G.view.k*f));G.view.x=e.offsetX-wx*G.view.k;G.view.y=e.offsetY-wy*G.view.k;},{passive:false});
  addEventListener('resize',()=>{resize();});
}
function detail(n){
  const nbrs=G.edges.filter(e=>e.a===n||e.b===n);
  $('side-title').textContent=n.label;
  $('side').innerHTML=`<h3>${esc(n.id)}</h3><p>${esc(n.name)}</p>${n.state?`<p>State: ${esc(n.state)}</p>`:''}${n.quote?`<p>“${esc(n.quote)}”</p>`:''}${n.snippet?`<p class="mono">${esc(n.snippet)}</p>`:''}
    <p>Source: ${esc(n.source||'derived')}${n.url?` · <a href="${esc(n.url)}" target="_blank" rel="noopener">open on GitHub</a>`:''}</p>
    <div class="label">${nbrs.length} relationships</div>${nbrs.slice(0,40).map(e=>`<div class="row" data-id="${esc(e.a===n?e.b.id:e.a.id)}"><span>${esc(e.a===n?'→ '+e.b.id:'← '+e.a.id)}</span><span>${esc(e.type)}</span></div>`).join('')}`;
  $('side').querySelectorAll('.row').forEach(r=>r.onclick=()=>{const m=G.byId.get(r.dataset.id);if(m){G.sel=m;detail(m);}});
}
const MODES={
  overview:['Overview',()=>{G.hl=new Map();G.hlEdges=new Set();fit();$('side-title').textContent='Overview';
    $('side').innerHTML=`<p>${D.graph.nodes.length} current records and ${D.graph.edges.length} current relationships. Retired history is left out, exactly as the algorithms see it.</p><p>Drag to pan, scroll to zoom, and click a node to inspect it and its relationships. Pick an algorithm above to overlay its result.</p>`;}],
  pagerank:['PageRank',()=>rank(D.algorithms.pagerank,'PageRank','Which records the rest of the graph points to most. Edge direction counts, so directories and components that many records belong to rank high. It measures structure, not importance.')],
  betweenness:['Brokers (betweenness)',()=>rank(D.algorithms.betweenness,'Betweenness over work records','Which tasks, PRs, commits and branches sit on the most shortest paths between others. These are the records that connect Plane, Slack and GitHub.')],
  components:['Connected components',()=>{const c=D.algorithms.components,palette=['#9ad46a','#7bdff2','#ffb347','#f28482','#bdb2ff','#f2c14e'];G.hl=new Map();G.hlEdges=new Set();
    c.groups.forEach((g,i)=>g.members.forEach(m=>G.hl.set(m,palette[i%palette.length])));
    $('side-title').textContent='Connected components';$('side').innerHTML=`<p>${c.count} groups, ${c.singletons} of them single isolated records. The largest group connects ${c.groups[0]?.size||0} records across all three tools. Isolated records are work nothing links to yet.</p>`;}],
  paths:['Shortest paths',()=>{$('side-title').textContent='Shortest paths';
    $('side').innerHTML=D.algorithms.paths.map((p,i)=>`<div class="row" data-p="${i}"><span>${esc(p.title)}</span><span>${p.edges.length} hops</span></div>`).join('')+'<p>Click a path to trace it. These are the fewest current relationships between the two records.</p>';
    $('side').querySelectorAll('.row').forEach(r=>r.onclick=()=>path(+r.dataset.p));path(0);}],
};
function rank(items,title,text){G.hl=new Map(items.map((x,i)=>[x.id,i]));G.hlEdges=new Set();fit(items.map(x=>x.id));$('side-title').textContent=title;
  $('side').innerHTML=`<p>${text}</p>`+items.map((x,i)=>`<div class="row" data-id="${esc(x.id)}"><span>${i+1}. ${esc(x.id)} <span class="note">${esc(x.label)}</span></span><span>${x.score>=10?Math.round(x.score):x.score.toFixed(3)}</span></div>`).join('');
  $('side').querySelectorAll('.row').forEach(r=>r.onclick=()=>{const m=G.byId.get(r.dataset.id);if(m){G.sel=m;detail(m);}});}
function path(i){const p=D.algorithms.paths[i];if(!p)return;G.hl=new Map(p.nodes.map(n=>[n,1]));G.hlEdges=new Set(p.edges.map(e=>e.s+'>'+e.t));fit(p.nodes);
  $('side').querySelectorAll('.row').forEach((r,j)=>r.style.fontWeight=j===i?'700':'400');
  const old=$('side').querySelector('.trace');if(old)old.remove();
  $('side').insertAdjacentHTML('beforeend',`<p class="trace mono">${p.nodes.map((n,k)=>esc(n)+(p.edges[k]?` -${esc(p.edges[k].type)}-`:'')).join(' ')}</p>`);}
function algoButtons(){
  $('algo-buttons').innerHTML=Object.entries(MODES).map(([k,[t]])=>`<button data-m="${k}" class="${k==='overview'?'on':''}">${t}</button>`).join('');
  $('algo-buttons').onclick=e=>{const b=e.target.closest('button');if(!b)return;G.mode=b.dataset.m;$('algo-buttons').querySelectorAll('button').forEach(x=>x.classList.toggle('on',x===b));MODES[G.mode][1]();};
  MODES.overview[1]();
}

// ---------------------------------------------------------------- benchmark table
function bench(){
  const b=D.bench,types=[...new Set(b.walks.map(w=>w.type))];
  $('bench-intro').textContent=`${b.questions} questions (${b.answerable} answerable, ${b.questions-b.answerable} unanswerable), committed before any strategy ran. Gold answers come from the sources: the authored Plane/Slack content, the GitHub API and the Plane API. Click a row to walk it. Run ${b.run_at.slice(0,10)}.`;
  const filters=[['all','All'],['answerable','Answerable'],['unanswerable','Unanswerable'],['miss','Graph misses'],...types.map(t=>[t,t.replace('_',' ')])];
  $('filters').innerHTML=filters.map(([k,t],i)=>`<button data-f="${k}" class="${i?'':'on'}">${t}</button>`).join('');
  const render=f=>{$('qrows').innerHTML=b.walks.filter(w=>f==='all'||(f==='answerable'&&w.answerable)||(f==='unanswerable'&&!w.answerable)||(f==='miss'&&!w.scores.graph.correct)||w.type===f)
    .map(w=>`<tr data-q="${w.id}"><td>${w.id}</td><td>${esc(w.type.replace('_',' '))}</td><td>${esc(w.question)}</td><td>${w.scores.graph.correct?'<span class="ok">✓</span>':'<span class="no">✗</span>'}</td>
      <td>${w.answerable?pct(w.scores.vector.f1):'✗'}</td><td>${w.answerable?pct(w.scores.bm25.f1):'✗'}</td></tr>`).join('');};
  $('filters').onclick=e=>{const btn=e.target.closest('button');if(!btn)return;$('filters').querySelectorAll('button').forEach(x=>x.classList.toggle('on',x===btn));render(btn.dataset.f);};
  $('qrows').onclick=e=>{const tr=e.target.closest('tr');if(tr){walk(tr.dataset.q);$('walk').scrollIntoView();}};
  render('all');
}

function theme(){const root=document.documentElement,btn=$('theme');
  const set=l=>{root.classList.toggle('light',l);btn.textContent=l?'Dark theme':'Light theme';};
  set(matchMedia('(prefers-color-scheme: light)').matches);btn.onclick=()=>set(!root.classList.contains('light'));}

load().then(()=>{theme();hero();starters();walk('q37');caps();truth();setupGraph();algoButtons();bench();})
  .catch(err=>{document.querySelector('main').insertAdjacentHTML('afterbegin',`<p class="note">Could not load data: ${esc(err.message)}</p>`);});
