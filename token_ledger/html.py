"""Self-contained HTML dashboard: data embedded as JSON, charts are inline SVG drawn by plain JS."""
import datetime as dt
import json
import os

from . import prices as pricing
from .report import TOK, active_project


def build_data(conn, cfg):
    dims = {k: [] for k in ("project", "account", "model", "kind", "feature", "machine", "source")}
    idx = {k: {} for k in dims}

    def ix(k, v):
        v = v or "?"
        if v not in idx[k]:
            idx[k][v] = len(dims[k])
            dims[k].append(v)
        return idx[k][v]

    rows = []
    q = (f"SELECT day, project, account, model, kind, feature, machine, source, COUNT(*), SUM(input), "
         f"SUM(cache_write_5m + cache_write_1h), SUM(cache_read), SUM(output), SUM(est_cost_usd) FROM calls "
         f"GROUP BY day, project, account, model, kind, feature, machine, source")
    for r in conn.execute(q):
        rows.append([r[0], ix("project", r[1]), ix("account", r[2]), ix("model", r[3]), ix("kind", r[4]),
                     ix("feature", r[5]), ix("machine", r[6]), ix("source", r[7]), r[8], r[9] or 0, r[10] or 0,
                     r[11] or 0, r[12] or 0, round(r[13] or 0, 6)])
    top = []
    for r in conn.execute(f"SELECT ts, project, model, kind, feature, {TOK}, est_cost_usd FROM calls "
                          f"ORDER BY est_cost_usd DESC LIMIT 50"):
        t = dt.datetime.fromisoformat(r[0].replace("Z", "+00:00")).astimezone(cfg.tz)
        top.append([t.strftime("%Y-%m-%d %H:%M"), r[1], r[2].replace("claude-", ""), r[3], r[4], r[5], round(r[6] or 0, 4)])
    # projects: most recent first
    plist = [r[0] for r in conn.execute("SELECT project FROM calls GROUP BY project ORDER BY MAX(ts) DESC")]
    rng = conn.execute("SELECT MIN(day), MAX(day) FROM calls").fetchone()
    files = conn.execute("SELECT count(*) FROM ingested_files").fetchone()[0]
    act = active_project(conn)
    p = pricing.load(cfg.price_file)
    return {
        "generated": dt.datetime.now(cfg.tz).strftime("%Y-%m-%d %H:%M"), "tz": cfg.timezone,
        "today": dt.datetime.now(cfg.tz).date().isoformat(), "range": list(rng), "files": files,
        "machine": cfg.machine, "prices_url": p.get("source_url"), "prices_date": p.get("verified"),
        "active": act[0] if act else None, "projects": plist, "dims": dims, "rows": rows, "top": top,
    }


def write(conn, cfg):
    data = build_data(conn, cfg)
    os.makedirs(cfg.data_dir, exist_ok=True)
    with open(cfg.html_path, "w", encoding="utf-8") as f:
        f.write(TEMPLATE.replace("__DATA__", json.dumps(data, separators=(",", ":")).replace("</", "<\\/")))
    return cfg.html_path, os.path.getsize(cfg.html_path)


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Token usage</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{--indigo:#4f46e5;--indigo-d:#3730a3;--slate:#334155;--slate-l:#64748b;--bg:#f8fafc;--line:#e2e8f0}
*{box-sizing:border-box}
body{margin:0;font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:var(--slate);background:var(--bg)}
header{background:#fff;border-bottom:1px solid var(--line);padding:18px 28px}
h1{margin:0;font-size:30px;color:var(--indigo-d)} h1 small{font-size:15px;color:var(--slate-l);font-weight:400;margin-left:10px}
h2{font-size:18px;margin:30px 0 10px;color:var(--slate)}
main{padding:0 28px 40px;max-width:1300px;margin:auto}
.bar{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin-top:14px}
select,button{font:inherit;padding:6px 10px;border:1px solid #cbd5e1;border-radius:6px;background:#fff;color:var(--slate)}
button{cursor:pointer} button.on{background:var(--indigo);color:#fff;border-color:var(--indigo)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:14px;margin-top:20px}
.tile{background:#fff;border:1px solid var(--line);border-radius:10px;padding:14px 18px}
.tile .t{color:var(--slate-l);font-size:14px;text-transform:uppercase;letter-spacing:.04em}
.tile .v{font-size:28px;font-weight:600;color:var(--indigo-d)} .tile .c{font-size:18px}
.up{color:#b45309}.down{color:#047857}
.card{background:#fff;border:1px solid var(--line);border-radius:10px;padding:16px;position:relative}
table{border-collapse:collapse;width:100%;background:#fff;font-size:14px}
th,td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{background:#eef2ff;color:var(--indigo-d);position:sticky;top:0}
th:first-child,td:first-child,td.l,th.l{text-align:left}
tr.feat{cursor:pointer;background:#f1f5f9;font-weight:600} tr.sub td{font-weight:600;border-top:1px solid #cbd5e1}
tr.grand td{font-weight:700;background:#eef2ff;color:var(--indigo-d)}
tr.kind td:first-child{padding-left:30px}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;margin-top:8px;font-size:14px}
.legend i{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:6px;vertical-align:-1px}
#tip{position:fixed;pointer-events:none;background:#0f172a;color:#fff;padding:8px 10px;border-radius:6px;font-size:13px;display:none;max-width:340px;z-index:9}
.note{color:var(--slate-l);font-size:14px} .scroll{overflow-x:auto}
footer{margin-top:30px;color:var(--slate-l);font-size:14px;border-top:1px solid var(--line);padding-top:12px}
</style></head><body>
<header><h1 id="proj"></h1><div class="note" id="accts"></div>
<div class="bar">
 <label>Project <select id="fp"></select></label>
 <label>Account <select id="fa"></select></label>
 <span id="ranges"></span>
</div></header>
<main>
<div class="tiles" id="tiles"></div>
<h2>Over time</h2>
<div class="card">
 <div class="bar" style="margin:0 0 10px">
  <span>Group by <span id="gran"></span></span><span>Split by <span id="stack"></span></span><span>Show <span id="metric"></span></span>
 </div>
 <div id="chart"></div><div class="legend" id="legend"></div>
</div>
<h2>Features and what kind of work they were (click a feature to open it)</h2>
<div class="scroll"><table id="feat"></table></div>
<h2>By model</h2><div class="scroll"><table id="models"></table></div>
<h2>By task kind</h2><div class="scroll"><table id="kinds"></table></div>
<h2>By account</h2><div class="scroll"><table id="accounts"></table></div>
<h2>50 costliest single calls</h2><div class="scroll"><table id="top"></table></div>
<footer id="foot"></footer>
</main><div id="tip"></div>
<script>
const D=__DATA__;
const $=id=>document.getElementById(id);
const PAL=['#4f46e5','#0ea5e9','#14b8a6','#f59e0b','#ef4444','#8b5cf6','#64748b','#84cc16','#ec4899','#06b6d4','#a16207','#475569'];
const st={project:D.active||'__all',account:'__all',from:null,to:null,range:'30',gran:'day',stack:'model',metric:'cost'};
const nf=n=>n>=1e9?(n/1e9).toFixed(1)+'B':n>=1e6?(n/1e6).toFixed(n>=1e8?0:1)+'M':n>=1e3?(n/1e3).toFixed(n>=1e5?0:1)+'k':String(Math.round(n));
const money=c=>'$'+c.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
const tot=r=>r[9]+r[10]+r[11]+r[12];
const dmod=(k,i)=>D.dims[k][i];
function dd(s){return new Date(s+'T00:00:00Z')} function ds(d){return d.toISOString().slice(0,10)}
function addDays(s,n){const d=dd(s);d.setUTCDate(d.getUTCDate()+n);return ds(d)}
function weekStart(s){const d=dd(s);const w=(d.getUTCDay()+6)%7;d.setUTCDate(d.getUTCDate()-w);return ds(d)}
function base(r){ // project/account filter only
  if(st.project!=='__all'&&dmod('project',r[1])!==st.project)return false;
  if(st.account!=='__all'&&dmod('account',r[2])!==st.account)return false;return true}
function inRange(r){return (!st.from||r[0]>=st.from)&&(!st.to||r[0]<=st.to)}
function sumRows(rows){const s=[0,0,0,0,0,0];rows.forEach(r=>{s[0]+=r[8];s[1]+=r[9];s[2]+=r[10];s[3]+=r[11];s[4]+=r[12];s[5]+=r[13]});return s}
function setRange(k){
  st.range=k;const t=D.today;
  if(k==='today'){st.from=t;st.to=t}
  else if(k==='7'){st.from=addDays(t,-6);st.to=t}
  else if(k==='30'){st.from=addDays(t,-29);st.to=t}
  else if(k==='month'){st.from=t.slice(0,8)+'01';st.to=t}
  else if(k==='year'){st.from=t.slice(0,4)+'-01-01';st.to=t}
  else{st.from=null;st.to=null}
  render()}
function btns(el,opts,key,cb){const e=$(el);e.innerHTML='';opts.forEach(([v,l])=>{const b=document.createElement('button');b.textContent=l;
  if(st[key]===v)b.className='on';b.onclick=()=>{st[key]=v;cb?cb(v):render()};e.appendChild(b);e.append(' ')})}
function periodTiles(){
  const rows=D.rows.filter(base),t=D.today,out=[];
  const wk=weekStart(t),m0=t.slice(0,8)+'01';
  const pm=(()=>{const d=dd(m0);d.setUTCMonth(d.getUTCMonth()-1);return ds(d)})();
  const defs=[['Today',t,t,addDays(t,-1),addDays(t,-1)],['This week',wk,t,addDays(wk,-7),addDays(wk,-1)],
   ['This month',m0,t,pm,addDays(m0,-1)],['This year',t.slice(0,4)+'-01-01',t,(+t.slice(0,4)-1)+'-01-01',(+t.slice(0,4)-1)+t.slice(4)]];
  defs.forEach(([n,a,b,pa,pb])=>{
    const c=sumRows(rows.filter(r=>r[0]>=a&&r[0]<=b)),p=sumRows(rows.filter(r=>r[0]>=pa&&r[0]<=pb));
    const T=c[1]+c[2]+c[3]+c[4],PT=p[1]+p[2]+p[3]+p[4];
    const ch=(x,y)=>y?((x-y)/y*100):null,pc=ch(c[5],p[5]);
    const cls=pc===null?'':pc>0?'up':'down';
    out.push(`<div class="tile"><div class="t">${n}</div><div class="v">${nf(T)} tokens</div><div class="c">${money(c[5])} est.</div>
     <div class="note">vs previous: <span class="${cls}">${pc===null?'no earlier data':(pc>0?'+':'')+pc.toFixed(0)+'% cost'}</span>${PT?' ('+nf(PT)+' tokens)':''}</div></div>`)});
  $('tiles').innerHTML=out.join('')}
function bucket(day){return st.gran==='day'?day:st.gran==='week'?weekStart(day):day.slice(0,7)}
function chart(){
  const rows=D.rows.filter(r=>base(r)&&inRange(r)),key=st.stack==='model'?3:4,dim=st.stack==='model'?'model':'kind';
  const val=r=>st.metric==='cost'?r[13]:tot(r);
  const B={},S={};
  rows.forEach(r=>{const b=bucket(r[0]),s=dmod(dim,r[key]);B[b]=B[b]||{};B[b][s]=(B[b][s]||0)+val(r);S[s]=(S[s]||0)+val(r)});
  const keys=Object.keys(S).sort((a,b)=>S[b]-S[a]),col={};keys.forEach((k,i)=>col[k]=PAL[i%PAL.length]);
  const bs=Object.keys(B).sort(),W=1180,H=320,L=64,Bm=56,T=10,pw=W-L-10,ph=H-Bm-T;
  const mx=Math.max(1e-9,...bs.map(b=>Object.values(B[b]).reduce((x,y)=>x+y,0)));
  const fmt=v=>st.metric==='cost'?money(v):nf(v);
  let svg=`<svg viewBox="0 0 ${W} ${H}" width="100%" role="img">`;
  for(let i=0;i<=4;i++){const y=T+ph-ph*i/4;svg+=`<line x1="${L}" x2="${W-10}" y1="${y}" y2="${y}" stroke="#e2e8f0"/><text x="${L-6}" y="${y+4}" text-anchor="end" font-size="12" fill="#64748b">${fmt(mx*i/4)}</text>`}
  const bw=bs.length?pw/bs.length:0;
  bs.forEach((b,i)=>{let y=T+ph;const x=L+i*bw+bw*.1;
    keys.forEach(k=>{const v=B[b][k]||0;if(!v)return;const h=ph*v/mx;y-=h;
      svg+=`<rect x="${x}" y="${y}" width="${bw*.8}" height="${h}" fill="${col[k]}" data-b="${b}" data-k="${k}"></rect>`});
    if(bs.length<=45||i%Math.ceil(bs.length/30)===0)svg+=`<text x="${x+bw*.4}" y="${H-Bm+14}" font-size="11" fill="#64748b" text-anchor="end" transform="rotate(-45 ${x+bw*.4} ${H-Bm+14})">${b}</text>`});
  svg+='</svg>';$('chart').innerHTML=bs.length?svg:'<p class="note">No data in this selection.</p>';
  $('legend').innerHTML=keys.map(k=>`<span><i style="background:${col[k]}"></i>${k} (${fmt(S[k])})</span>`).join('');
  const tip=$('tip');
  $('chart').querySelectorAll('rect').forEach(e=>{
    e.onmousemove=ev=>{const b=e.dataset.b,tt=Object.values(B[b]).reduce((x,y)=>x+y,0);
      tip.innerHTML=`<b>${b}</b> total ${fmt(tt)}<br>`+keys.filter(k=>B[b][k]).map(k=>`<span style="color:${col[k]}">&#9632;</span> ${k}: ${fmt(B[b][k])}`).join('<br>');
      tip.style.display='block';tip.style.left=Math.min(ev.clientX+14,innerWidth-360)+'px';tip.style.top=ev.clientY+14+'px'};
    e.onmouseleave=()=>tip.style.display='none'})}
const hdr=['Fresh input','Cache write','Cache read','Output','Total','Est. cost'];
function agg(rows,keyfn){const m={};rows.forEach(r=>{const k=keyfn(r);(m[k]=m[k]||[]).push(r)});return m}
function cells(s,calls){const T=s[1]+s[2]+s[3]+s[4];return `<td>${calls?s[0].toLocaleString():''}</td><td>${nf(s[1])}</td><td>${nf(s[2])}</td><td>${nf(s[3])}</td><td>${nf(s[4])}</td><td>${nf(T)}</td><td>${money(s[5])}</td>`}
function simple(el,label,dim,idxn){
  const rows=D.rows.filter(r=>base(r)&&inRange(r)),g=agg(rows,r=>dmod(dim,r[idxn])),G=sumRows(rows),GT=G[1]+G[2]+G[3]+G[4];
  const ent=Object.entries(g).map(([k,v])=>[k,sumRows(v)]).sort((a,b)=>(b[1][1]+b[1][2]+b[1][3]+b[1][4])-(a[1][1]+a[1][2]+a[1][3]+a[1][4]));
  $(el).innerHTML=`<tr><th class="l">${label}</th><th>Calls</th>${hdr.map(h=>`<th>${h}</th>`).join('')}<th>Share of tokens</th></tr>`+
   ent.map(([k,s])=>{const T=s[1]+s[2]+s[3]+s[4];return `<tr><td>${k}</td>${cells(s,1)}<td>${GT?(T/GT*100).toFixed(1):0}%</td></tr>`}).join('')+
   `<tr class="grand"><td>GRAND TOTAL</td>${cells(G,1)}<td>100%</td></tr>`}
const open={};
function features(){
  const rows=D.rows.filter(r=>base(r)&&inRange(r)),g=agg(rows,r=>dmod('feature',r[5]));
  const list=Object.entries(g).map(([f,v])=>[f,v,sumRows(v)]).sort((a,b)=>(b[2][1]+b[2][2]+b[2][3]+b[2][4])-(a[2][1]+a[2][2]+a[2][3]+a[2][4]));
  let h=`<tr><th class="l">Feature / task kind</th><th class="l">Model(s)</th><th>Calls</th>${hdr.map(x=>`<th>${x}</th>`).join('')}</tr>`;
  list.forEach(([f,v,s],i)=>{
    h+=`<tr class="feat" data-f="${i}"><td>${open[f]?'&#9662;':'&#9656;'} ${f}</td><td class="l"></td>${cells(s,1)}</tr>`;
    if(open[f]){const kg=agg(v,r=>dmod('kind',r[4]));
      Object.entries(kg).map(([k,kv])=>[k,kv,sumRows(kv)]).sort((a,b)=>(b[2][1]+b[2][2]+b[2][3]+b[2][4])-(a[2][1]+a[2][2]+a[2][3]+a[2][4])).forEach(([k,kv,ks])=>{
        const ms=[...new Set(kv.map(r=>dmod('model',r[3]).replace('claude-','')))].join(', ');
        h+=`<tr class="kind"><td>${k}</td><td class="l">${ms}</td>${cells(ks,1)}</tr>`});
      h+=`<tr class="sub"><td>&nbsp;&nbsp;&nbsp;&nbsp;Subtotal</td><td></td>${cells(s,1)}</tr>`}});
  h+=`<tr class="grand"><td>GRAND TOTAL</td><td></td>${cells(sumRows(rows),1)}</tr>`;
  $('feat').innerHTML=h;
  $('feat').querySelectorAll('tr.feat').forEach(tr=>tr.onclick=()=>{const f=list[+tr.dataset.f][0];open[f]=!open[f];features()})}
function topCalls(){
  $('top').innerHTML='<tr><th class="l">Time</th><th class="l">Project</th><th class="l">Model</th><th class="l">Task kind</th><th class="l">Feature</th><th>Tokens</th><th>Est. cost</th></tr>'+
   D.top.filter(t=>st.project==='__all'||t[1]===st.project).map(t=>`<tr><td>${t[0]}</td><td class="l">${t[1]}</td><td class="l">${t[2]}</td><td class="l">${t[3]}</td><td class="l">${t[4]}</td><td>${nf(t[5])}</td><td>${money(t[6])}</td></tr>`).join('')}
function render(){
  $('proj').innerHTML=(st.project==='__all'?'All projects':st.project)+'<small>'+(st.project===D.active?'active project':'')+'</small>';
  $('accts').textContent='Accounts: '+D.dims.account.join(', ')+' | machine: '+D.machine;
  btns('ranges',[['today','Today'],['7','7 days'],['30','30 days'],['month','This month'],['year','This year'],['all','All']],'range',setRange);
  btns('gran',[['day','Day'],['week','Week'],['month','Month']],'gran');
  btns('stack',[['model','Model'],['kind','Task kind']],'stack');
  btns('metric',[['cost','Est. cost'],['tokens','Tokens']],'metric');
  periodTiles();chart();features();simple('models','Model','model',3);simple('kinds','Task kind','kind',4);simple('accounts','Account','account',2);topCalls()}
function opts(el,vals,cur,all){$(el).innerHTML=`<option value="__all">${all}</option>`+vals.map(v=>`<option${v===cur?' selected':''}>${v}</option>`).join('')}
opts('fp',D.projects,st.project,'All projects');opts('fa',D.dims.account,'__all','All accounts');
$('fp').onchange=e=>{st.project=e.target.value;render()};$('fa').onchange=e=>{st.account=e.target.value;render()};
$('foot').innerHTML=`Generated ${D.generated} (${D.tz}). Data range ${D.range[0]} to ${D.range[1]}. ${D.files} log files read.<br>
 Cache read = context re-used (cheap). Est. cost = list price &times; tokens, not your bill. Prices checked ${D.prices_date}: ${D.prices_url}`;
setRange('30');
</script></body></html>
"""
