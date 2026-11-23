"""Self-contained HTML comparison report (no network, no external assets)."""

from __future__ import annotations

import html
import json
from pathlib import Path

from .explain import winners
from .resources import Ledger, ResourceSpec

SAMPLES = 240


def _series(run: dict, specs: list[ResourceSpec], t0: int, t1: int, jobs: list[dict]) -> dict:
    ledger = Ledger(specs)
    for i, u in enumerate(run["usage"]):
        if u["resource"] in ledger.specs:
            ledger.book(u["resource"], u["t"], u["amount"], f"u{i}", u["urgent"], "")
    for es in ledger.entries.values():
        for e in es:
            e.state = "confirmed"
    ts = [t0 + (t1 - t0) * k // (SAMPLES - 1) for k in range(SAMPLES)]
    avail = {
        r.id: [r.capacity - ledger.used(r.id, t) for t in ts] for r in specs if r.window != "concurrency"
    }
    pending = []
    for t in ts:
        pending.append(
            sum(
                1
                for j in jobs
                if j["arrival"] <= t
                and (j["finished_at"] is None or j["finished_at"] > t)
                and j["status"] not in ("cancelled",)
            )
        )
    return {"t": ts, "available": avail, "pending": pending}


def build_report(run_data: dict, out_path: str | Path) -> Path:
    m = run_data["manifest"]
    runs = run_data["runs"]
    specs = [ResourceSpec(**{**r, "scope": r.get("scope") or {}}) for r in m["resources"]]
    all_t = [j["arrival"] for r in runs.values() for j in r["jobs"]]
    all_t += [a["end"] for r in runs.values() for a in r["attempts"]]
    t0, t1 = min(all_t), max(all_t)
    t1 += max(1, (t1 - t0) // 20)  # margin so pending marks stay visible
    payload = {
        "manifest": m,
        "unit": m["trace"].get("time_unit", "s"),
        "t0": t0,
        "t1": t1,
        "winners": winners({k: v["summary"] for k, v in runs.items()}),
        "runs": {
            k: {
                "summary": v["summary"],
                "jobs": v["jobs"],
                "decisions": v["decisions"],
                "attempts": [
                    {
                        "job": a["job_id"],
                        "step": a["step_id"],
                        "n": a["attempt"],
                        "start": a["start"],
                        "end": a["end"],
                        "status": a["status"],
                    }
                    for a in v["attempts"]
                ],
                "series": _series(v, specs, t0, t1, v["jobs"]),
            }
            for k, v in runs.items()
        },
        "resources": [
            {"id": r.id, "unit": r.unit, "capacity": r.capacity, "window": r.window}
            for r in specs
            if r.window != "concurrency"
        ],
    }
    data = json.dumps(payload, separators=(",", ":"), default=str).replace("</", "<\\/")
    title = f"Rubato · {m['scenario']['name']}"
    page = TEMPLATE.replace("__TITLE__", html.escape(title)).replace("__DATA__", data)
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(page, encoding="utf-8")
    return p


TEMPLATE = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{color-scheme:light;--surface:#fcfcfb;--surface-2:#f3f2ef;--ink:#0b0b0b;--ink-2:#52514e;--muted:#8a8984;--grid:#e4e3df;
--c1:#2a78d6;--c2:#eb6834;--c3:#1baf7a;--other:#9a9893;--wait:#c9c7c1;--focus:#2a78d6}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;--surface:#1a1a19;--surface-2:#252523;
--ink:#f0efec;--ink-2:#c3c2b7;--muted:#8f8e86;--grid:#363633;--c1:#3987e5;--c2:#d95926;--c3:#199e70;--other:#77766f;--wait:#4a4a46}}
:root[data-theme="dark"]{color-scheme:dark;--surface:#1a1a19;--surface-2:#252523;--ink:#f0efec;--ink-2:#c3c2b7;--muted:#8f8e86;
--grid:#363633;--c1:#3987e5;--c2:#d95926;--c3:#199e70;--other:#77766f;--wait:#4a4a46}
*{box-sizing:border-box}body{margin:0;background:var(--surface);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1180px;margin:0 auto;padding:20px 16px 60px}h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 8px}
.sub{color:var(--ink-2);margin:0 0 12px}.controls{display:flex;flex-wrap:wrap;gap:12px;align-items:end;padding:12px;background:var(--surface-2);border-radius:8px}
label{display:flex;flex-direction:column;font-size:12px;color:var(--ink-2);gap:4px}select{font:inherit;padding:4px 6px;border-radius:6px;border:1px solid var(--grid);background:var(--surface);color:var(--ink)}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--grid)}
th{font-weight:600;color:var(--ink-2);font-size:12px}td.num{text-align:right}.best{font-weight:600}.best::after{content:" ▲ mejor";font-size:11px;color:var(--ink-2);font-weight:400}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:860px){.grid2{grid-template-columns:1fr}}
.panel{background:var(--surface);border:1px solid var(--grid);border-radius:8px;padding:10px;overflow:hidden}
.panel h3{margin:0 0 6px;font-size:14px}.legend{display:flex;align-items:center;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--ink-2);margin:6px 0}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
svg{display:block;width:100%;height:auto}.tl-row{cursor:pointer}.tl-row:hover rect.hit{fill:var(--grid);fill-opacity:.5}
#tip{position:fixed;pointer-events:none;background:var(--surface);border:1px solid var(--grid);border-radius:6px;padding:6px 8px;font-size:12px;box-shadow:0 2px 8px rgba(0,0,0,.15);display:none;max-width:360px;z-index:9}
#card{white-space:pre-wrap;font:12px/1.5 ui-monospace,Consolas,monospace;background:var(--surface-2);padding:10px;border-radius:8px;min-height:60px;overflow-x:auto}
.note{font-size:12px;color:var(--ink-2)}ul.assume{margin:4px 0 0 18px;padding:0;font-size:12px;color:var(--ink-2)}
details summary{cursor:pointer;color:var(--ink-2)}.tag{font-size:11px;padding:1px 6px;border-radius:10px;background:var(--surface-2);color:var(--ink-2)}
</style></head><body><main>
<h1 id="title"></h1><p class="sub" id="subtitle"></p>
<div class="controls">
 <label>Política A<select id="pa"></select></label><label>Política B<select id="pb"></select></label>
 <label>Cliente<select id="fclient"><option value="">todos</option></select></label>
 <label>Clase<select id="fclass"><option value="">todas</option></select></label>
 <label>Recurso<select id="fres"></select></label>
</div>
<h2>Resumen por objetivo</h2><p class="note">Cada objetivo se informa por separado; no hay media ponderada. «▲ mejor» marca a la política que gana ese objetivo (empates: ambas).</p>
<table id="summary"></table>
<h2>Líneas de tiempo</h2>
<div class="legend" id="tl-legend"></div>
<div class="grid2"><div class="panel"><h3 id="ta"></h3><div id="tla"></div></div><div class="panel"><h3 id="tb"></h3><div id="tlb"></div></div></div>
<h2>Ficha de decisión</h2><p class="note">Pulsa una fila de cualquier línea de tiempo.</p><div id="card">—</div>
<h2>Cuota disponible y trabajos pendientes</h2>
<div class="grid2"><div class="panel"><h3 id="qt"></h3><div class="legend" id="q-legend"></div><div id="quota"></div></div>
<div class="panel"><h3>Trabajos pendientes (llegados y no terminados)</h3><div class="legend" id="p-legend"></div><div id="pend"></div></div></div>
<h2>Supuestos y procedencia</h2><div id="assume"></div>
<details><summary>Tabla de trabajos</summary><table id="jobs"></table></details>
</main><div id="tip"></div>
<script id="data" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const $=id=>document.getElementById(id);
const labels=Object.keys(D.runs);const tip=$('tip');
const fmt=t=>{if(t===null||t===undefined)return'—';if(D.unit==='turn')return't='+t;const d=new Date(t*1000);return d.toISOString().slice(11,19)+' UTC'};
const dur=s=>{if(s===null||s===undefined)return'—';if(D.unit==='turn')return s+' turnos';if(s<90)return s+' s';if(s<5400)return Math.round(s/60)+' min';return (s/3600).toFixed(1)+' h'};
const CLS={};const palette=['var(--c1)','var(--c2)','var(--c3)'];
const classes=[...new Set(Object.values(D.runs)[0].jobs.map(j=>j.class))];classes.forEach((c,i)=>CLS[c]=i<3?palette[i]:'var(--other)');
const clients=[...new Set(Object.values(D.runs)[0].jobs.map(j=>j.client))];
$('title').textContent='Rubato · '+D.manifest.scenario.name;
$('subtitle').textContent=(D.manifest.scenario.description||'')+' — traza '+D.manifest.trace.fingerprint+' ('+(D.manifest.trace.created_by||'?')+'), '+D.manifest.jobs+' trabajos, políticas congeladas '+(D.manifest.policies_file.frozen_at||'—');
for(const s of ['pa','pb']){labels.forEach(l=>{const o=document.createElement('option');o.value=l;o.textContent=l+' ('+D.runs[l].summary.policy+')';$(s).appendChild(o)})}
$('pa').value=labels[0];$('pb').value=labels[1]||labels[0];
clients.forEach(c=>{const o=document.createElement('option');o.value=c;o.textContent=c;$('fclient').appendChild(o)});
classes.forEach(c=>{const o=document.createElement('option');o.value=c;o.textContent=c;$('fclass').appendChild(o)});
D.resources.forEach(r=>{const o=document.createElement('option');o.value=r.id;o.textContent=r.id+' ('+r.unit+', '+r.window+')';$('fres').appendChild(o)});
$('tl-legend').innerHTML=classes.map(c=>`<span><span class="sw" style="background:${CLS[c]}"></span>${esc(c)}</span>`).join('')+'<span><span class="sw" style="background:var(--wait)"></span>espera</span><span>│ plazo</span><span>✕ no terminado</span>';
function rowsSummary(a,b){const A=D.runs[a].summary,B=D.runs[b].summary;const W=D.winners;
 const r=[['Urgencias a tiempo','urgent','done_on_time',1,'urgent_on_time',x=>x.urgent.received],['Urgencias pendientes','urgent','pending',-1],['Urgencias fallidas o cerradas','urgent','failed',-1],
 ['Fondo terminado a tiempo','background','done_on_time',1,'background_on_time',x=>x.background.received],['Fondo terminado','background','done',1,'background_done',x=>x.background.received],
 ['Fondo pendiente','background','pending',-1],['Espera fondo p50','background','wait_p50',-1,null,null,dur],['Espera fondo p95','background','wait_p95',-1,'background_wait_p95 (lower)',null,dur],
 ['Edad del pendiente más antiguo','background','oldest_pending_age',-1,null,null,dur]];
 let h=`<tr><th>Medida</th><th class="num">${esc(a)}</th><th class="num">${esc(b)}</th></tr>`;
 for(const [name,grp,key,dir,wk,den,f] of r){const va=A[grp][key],vb=B[grp][key];const show=(v,x)=>(f?f(v):v===null?'—':v)+(den?' / '+den(x):'');
  let ca='',cb='';if(wk&&W[wk]){if(W[wk].includes(a))ca='best';if(W[wk].includes(b))cb='best'}
  h+=`<tr><td>${name}</td><td class="num ${ca}">${show(va,A)}</td><td class="num ${cb}">${show(vb,B)}</td></tr>`}
 const cons=k=>Object.entries(D.runs[k].summary.consumption.consumed).map(([r,v])=>r+' '+v).join(', ');
 h+=`<tr><td>Consumo confirmado</td><td class="num">${esc(cons(a))}</td><td class="num">${esc(cons(b))}</td></tr>`;
 const dr=k=>Object.entries(D.runs[k].summary.deferrals_by_reason).map(([r,v])=>r+' '+v).join(', ')||'—';
 h+=`<tr><td>Aplazamientos por motivo</td><td class="num">${esc(dr(a))}</td><td class="num">${esc(dr(b))}</td></tr>`;
 const lat=k=>D.runs[k].summary.planner.latency_us_p95+' µs';
 h+=`<tr><td>Latencia de decisión p95</td><td class="num">${lat(a)}</td><td class="num">${lat(b)}</td></tr>`;
 $('summary').innerHTML=h}
function filtered(jobs){const c=$('fclient').value,k=$('fclass').value;return jobs.filter(j=>(!c||j.client===c)&&(!k||j.class===k))}
function timeline(el,label){const run=D.runs[label];const jobs=filtered(run.jobs).slice().sort((x,y)=>(y.urgent-x.urgent)||x.arrival-y.arrival||x.job.localeCompare(y.job));
 const W=540,L=8,R=12,top=8,rh=jobs.length>80?5:jobs.length>30?9:16;const hs=jobs.map(j=>j.urgent?Math.max(rh,16):rh);const ys=[];let acc=top;hs.forEach(h=>{ys.push(acc);acc+=h});const H=acc+22;const x=t=>L+(t-D.t0)/(Math.max(1,D.t1-D.t0))*(W-L-R);
 const att={};run.attempts.forEach(a=>{(att[a.job]=att[a.job]||[]).push(a)});
 let s=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Línea de tiempo ${esc(label)}">`;
 for(let k=0;k<=4;k++){const t=D.t0+(D.t1-D.t0)*k/4;s+=`<line x1="${x(t)}" x2="${x(t)}" y1="${top-4}" y2="${H-16}" stroke="var(--grid)"/><text x="${x(t)}" y="${H-4}" font-size="10" fill="var(--muted)" text-anchor="${k===0?'start':k===4?'end':'middle'}">${fmt(Math.round(t))}</text>`}
 jobs.forEach((j,i)=>{const y=ys[i];const bh=Math.max(2,hs[i]-2);const end=j.finished_at??D.t1;const fs=j.first_start??end;
  s+=`<g class="tl-row" data-job="${esc(j.job)}" data-run="${esc(label)}"><rect class="hit" x="0" y="${y}" width="${W}" height="${hs[i]}" fill="transparent"/>`;
  s+=`<rect x="${x(j.arrival)}" y="${y+bh/2-1}" width="${Math.max(1,x(fs)-x(j.arrival))}" height="2" fill="var(--wait)"/>`;
  (att[j.job]||[]).forEach(a=>{s+=`<rect x="${x(a.start)}" y="${y}" width="${Math.max(2,x(a.end)-x(a.start))}" height="${bh}" rx="1" fill="${CLS[j.class]}" ${a.status!=='ok'?'fill-opacity=".45"':''}/>`});
  if(j.deadline!==null)s+=`<line x1="${x(j.deadline)}" x2="${x(j.deadline)}" y1="${y-1}" y2="${y+bh+1}" stroke="var(--ink)" stroke-width="1.5"/>`;
  if(j.status!=='done')s+=`<text x="${Math.min(W-6,x(end)+2)}" y="${y+bh}" font-size="${Math.max(6,bh+1)}" fill="var(--ink-2)">✕</text>`;
  if(j.urgent)s+=`<text x="${Math.max(L,x(j.arrival)-4)}" y="${y+bh-2}" font-size="10" fill="var(--ink-2)" text-anchor="end">${esc(j.job)}</text>`;
  s+='</g>'});
 s+='</svg>';el.innerHTML=s;
 el.querySelectorAll('.tl-row').forEach(g=>{g.addEventListener('click',()=>card(g.dataset.run,g.dataset.job));
  g.addEventListener('mousemove',ev=>{const j=run.jobs.find(q=>q.job===g.dataset.job);tip.style.display='block';tip.style.left=(ev.clientX+12)+'px';tip.style.top=(ev.clientY+12)+'px';
   tip.innerHTML=`<b>${esc(j.job)}</b> · ${esc(j.client)}/${esc(j.class)}${j.urgent?' · urgente':''}<br>llegada ${fmt(j.arrival)} <span class="tag">${esc(j.arrival_provenance)}</span><br>inicio ${fmt(j.first_start)} · espera ${dur(j.first_start===null?null:j.first_start-j.arrival)}<br>fin ${fmt(j.finished_at)} · plazo ${fmt(j.deadline)}<br>estado ${esc(j.status)}${j.on_time?' a tiempo':''}${j.reason?' · '+esc(j.reason):''}`});
  g.addEventListener('mouseleave',()=>tip.style.display='none')})}
function card(label,job){const run=D.runs[label];const j=run.jobs.find(q=>q.job===job);
 const ds=run.decisions.filter(d=>d.job===job);let t=`${esc(label)} · ${esc(run.summary.policy)}\n${esc(j.job)} (${esc(j.client)}/${esc(j.class)}${j.urgent?', urgente':''})  llegada ${fmt(j.arrival)} [${esc(j.arrival_provenance)}]  plazo ${fmt(j.deadline)}\n`;
 const V={authorize:'AUTORIZA',defer:'APLAZA',close:'CIERRA'};
 ds.forEach(d=>{t+=`\n${fmt(d.t)}  ${d.step}#${d.attempt}  ${V[d.action]}  [${d.reason}]`;if(d.detail)t+=`\n      ${d.detail}`;if(d.action==='defer')t+=`\n      reconsiderar: ${d.next_t===null?'en el siguiente evento':fmt(d.next_t)}`});
 t+=`\n\nresultado: ${esc(j.status)}${j.on_time?' a tiempo':j.status==='done'?' tarde':''}  fin ${fmt(j.finished_at)}`;$('card').textContent=t}
function lineChart(el,legend,getter,title){const a=$('pa').value,b=$('pb').value;const S=[[a,'var(--ink)',''],[b,'var(--ink-2)','5 4']];
 const W=540,H=220,L=44,R=70,T=10,B=24;const ts=D.runs[a].series.t;const vals=S.flatMap(([l])=>getter(D.runs[l].series)||[]);if(!vals.length){el.innerHTML='<p class="note">sin datos</p>';return}
 const ymax=Math.max(1,...vals),ymin=Math.min(0,...vals);const x=t=>L+(t-ts[0])/Math.max(1,ts[ts.length-1]-ts[0])*(W-L-R);const y=v=>T+(1-(v-ymin)/(ymax-ymin||1))*(H-T-B);
 let s=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${title}">`;
 for(let k=0;k<=4;k++){const v=ymin+(ymax-ymin)*k/4;s+=`<line x1="${L}" x2="${W-R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--grid)"/><text x="${L-4}" y="${y(v)+3}" font-size="10" fill="var(--muted)" text-anchor="end">${Math.round(v)}</text>`}
 for(let k=0;k<=4;k++){const t=ts[0]+(ts[ts.length-1]-ts[0])*k/4;s+=`<text x="${x(t)}" y="${H-6}" font-size="10" fill="var(--muted)" text-anchor="${k===0?'start':k===4?'end':'middle'}">${fmt(Math.round(t))}</text>`}
 S.forEach(([l,c,dash])=>{const v=getter(D.runs[l].series);if(!v)return;let p='';v.forEach((q,i)=>{p+=(i?'L':'M')+x(ts[i]).toFixed(1)+','+y(q).toFixed(1)+(i<v.length-1?'H'+x(ts[i+1]).toFixed(1):'')});
  s+=`<path d="${p}" fill="none" stroke="${esc(c)}" stroke-width="2" stroke-dasharray="${dash}"/><text x="${W-R+4}" y="${y(v[v.length-1])+3}" font-size="10" fill="var(--ink-2)">${esc(l)}</text>`});
 s+=`<line id="xh" y1="${T}" y2="${H-B}" stroke="var(--muted)" visibility="hidden"/><rect x="${L}" y="${T}" width="${W-L-R}" height="${H-T-B}" fill="transparent" class="ov"/></svg>`;el.innerHTML=s;
 legend.innerHTML=S.map(([l,c,dash])=>`<span style="display:inline-flex;align-items:center;gap:5px"><svg width="22" height="8" style="width:22px;height:8px;display:inline-block"><line x1="0" x2="22" y1="4" y2="4" stroke="${esc(c)}" stroke-width="2" stroke-dasharray="${dash}"/></svg> ${esc(l)}</span>`).join('');
 const svg=el.querySelector('svg'),ov=el.querySelector('.ov'),xh=el.querySelector('#xh');
 ov.addEventListener('mousemove',ev=>{const r=svg.getBoundingClientRect();const px=(ev.clientX-r.left)/r.width*W;let i=Math.round((px-L)/(W-L-R)*(ts.length-1));i=Math.max(0,Math.min(ts.length-1,i));
  xh.setAttribute('x1',x(ts[i]));xh.setAttribute('x2',x(ts[i]));xh.setAttribute('visibility','visible');tip.style.display='block';tip.style.left=(ev.clientX+12)+'px';tip.style.top=(ev.clientY+12)+'px';
  tip.innerHTML=`<b>${fmt(ts[i])}</b><br>`+S.map(([l])=>{const v=getter(D.runs[l].series);return l+': '+(v?v[i]:'—')}).join('<br>')});
 ov.addEventListener('mouseleave',()=>{tip.style.display='none';xh.setAttribute('visibility','hidden')})}
function jobsTable(){const a=$('pa').value,b=$('pb').value;const A=D.runs[a].jobs,Bm=Object.fromEntries(D.runs[b].jobs.map(j=>[j.job,j]));
 let h=`<tr><th>Trabajo</th><th>Clase</th><th>Llegada</th><th>Procedencia</th><th>Plazo</th><th>${esc(a)}</th><th>${esc(b)}</th></tr>`;
 const st=j=>j?j.status+(j.on_time?' a tiempo':j.status==='done'?' tarde':'')+' '+fmt(j.finished_at):'—';
 filtered(A).forEach(j=>{h+=`<tr><td>${esc(j.job)}</td><td>${esc(j.class)}</td><td>${fmt(j.arrival)}</td><td>${esc(j.arrival_provenance)}</td><td>${fmt(j.deadline)}</td><td>${esc(st(j))}</td><td>${esc(st(Bm[j.job]))}</td></tr>`});$('jobs').innerHTML=h}
function render(){const a=$('pa').value,b=$('pb').value;rowsSummary(a,b);$('ta').textContent='A · '+a;$('tb').textContent='B · '+b;timeline($('tla'),a);timeline($('tlb'),b);
 const r=$('fres').value;const spec=D.resources.find(q=>q.id===r);$('qt').textContent='Disponible en '+r+' (capacidad '+(spec?spec.capacity:'?')+' '+(spec?spec.unit:'')+')';
 lineChart($('quota'),$('q-legend'),s=>s.available[r],'cuota disponible');lineChart($('pend'),$('p-legend'),s=>s.pending,'pendientes');jobsTable()}
$('assume').innerHTML='<ul class="assume">'+D.manifest.assumptions.map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul><p class="note">Commit '+(D.manifest.commit||'—')+' · rubato '+D.manifest.rubato+' · Python '+D.manifest.python+'</p>';
{const cal=D.resources.find(r=>r.window==='calendar'||r.window==='total');if(cal)$('fres').value=cal.id}
['pa','pb','fclient','fclass','fres'].forEach(id=>$(id).addEventListener('change',render));render();
const urgent=D.runs[labels[0]].jobs.find(j=>j.urgent);if(urgent)card($('pb').value,urgent.job);
</script></body></html>
"""
