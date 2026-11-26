"""Live panel served by the API at ``/panel``. Self-contained: no external assets.

The page asks for the administrator token (kept in sessionStorage only), polls
``/v1/admin/overview`` and offers the operator actions: client mode, policy and the
reconciliation of uncertain attempts. Every server-provided text is escaped.
"""

PANEL_HTML = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rubato · panel</title><link rel="icon" href="data:,">
<style>
:root{color-scheme:light;--bg:#fcfcfb;--card:#ffffff;--soft:#f3f2ef;--ink:#0b0b0b;--ink2:#52514e;--muted:#8a8984;--line:#e4e3df;
--c1:#2a78d6;--c2:#eb6834;--warn:#b86e00;--bad:#c4332f;--good:#1a7f37}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;--bg:#1a1a19;--card:#222221;--soft:#2a2a28;
--ink:#f0efec;--ink2:#c3c2b7;--muted:#8f8e86;--line:#363633;--c1:#3987e5;--c2:#d95926;--warn:#e0a03a;--bad:#e66767;--good:#4fb36a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1200px;margin:0 auto;padding:16px}h1{font-size:20px;margin:0}h2{font-size:15px;margin:0 0 8px}
.top{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;margin-bottom:14px}
.sub{color:var(--ink2);font-size:12px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(340px,100%),1fr));gap:14px}
.card{min-width:0;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;overflow:auto}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:13px}th,td{text-align:left;padding:5px 6px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--ink2);font-weight:600;font-size:12px}.num{text-align:right}
button,select,input{font:inherit;border:1px solid var(--line);border-radius:6px;padding:4px 8px;background:var(--soft);color:var(--ink)}
button{cursor:pointer}:focus-visible{outline:2px solid var(--c1);outline-offset:2px}
nav{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}
nav button[aria-current=true]{border-color:var(--c1);font-weight:700}.row{display:flex;flex-wrap:wrap;gap:8px;align-items:end}
.row label{display:grid;gap:3px}.row label.inline{display:flex;align-items:center;gap:6px}.row input,.row select{max-width:100%}pre{overflow-wrap:anywhere;white-space:pre-wrap}button.primary{background:var(--c1);color:#fff;border-color:var(--c1)}
.bar{position:relative;height:10px;background:var(--soft);border-radius:5px;overflow:hidden;margin:4px 0}
.bar span{position:absolute;top:0;bottom:0;left:0;border-radius:5px}.bar .u{background:var(--c1)}.bar .g{background:var(--c2)}.bar .x{background:var(--warn)}
.tag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:10px;background:var(--soft);color:var(--ink2)}
.tag.authorize{color:var(--good)}.tag.defer{color:var(--warn)}.tag.close{color:var(--bad)}
.alert{border-left:4px solid var(--bad);padding:8px 10px;background:var(--soft);border-radius:6px;margin-bottom:12px}
.ok{color:var(--good)}.err{color:var(--bad)}#login{max-width:420px;margin:80px auto}.legend{font-size:12px;color:var(--ink2)}
.sw{display:inline-block;width:9px;height:9px;border-radius:2px;margin:0 4px 0 10px}
</style></head><body><main>
<section id="login" class="card" hidden>
 <h1>Rubato · panel</h1><p class="sub">Token de administración (se guarda solo en esta pestaña).</p>
 <form id="lf"><input id="tok" type="password" autocomplete="off" style="width:100%" placeholder="token admin"><p><button class="primary">Entrar</button> <span id="lerr" class="err"></span></p></form>
</section>
<section id="app" hidden>
 <div class="top"><div><h1>Rubato · panel en vivo</h1><div class="sub" id="meta"></div></div>
  <div><label class="sub"><input type="checkbox" id="auto" checked> actualizar cada 5 s</label> <button id="refresh">Actualizar</button> <button id="logout">Salir</button></div></div>
 <nav aria-label="Vistas"><button data-tab="summary" aria-current="true">Resumen</button><button data-tab="jobs">Trabajos</button><button data-tab="cost">Consumo</button><button data-tab="policies">Políticas</button></nav>
 <div id="alerts" aria-live="polite"></div>
 <div class="card" data-view="summary"><h2>Atención</h2><div class="row">
 <button id="risk">Próximo plazo en riesgo</button><button id="old">Espera más antigua</button>
 <button id="pending">Conciliaciones pendientes</button><button id="yesterday">Desde ayer</button></div>
 <div id="digest" aria-live="polite"></div></div>
 <div class="card" data-view="jobs" hidden><h2>Trabajos</h2><form id="search" class="row">
 <label>Trabajo o intento<input id="jq" type="search"></label><label>Cliente<input id="jc"></label>
 <label>Motivo<input id="jr"></label><button>Buscar</button><button type="button" id="resetFilters">Borrar filtros</button></form>
 <p id="jobStatus" class="sub" aria-live="polite"></p>
 <details id="captureCatalogue"><summary>Reproducción de incidentes</summary>
 <div class="row"><button id="saveCheckpoint">Iniciar captura de 5 min</button><button id="stopCheckpoint" disabled>Finalizar captura</button>
 <label>Capturas recientes<select id="recentCheckpoints"><option value="">Selecciona una captura</option></select></label>
 <button id="downloadCheckpoint" disabled>Exportar JSON</button></div>
 <p id="checkpointStatus" class="sub" aria-live="polite"></p><p id="checkpointNotice" class="sub" aria-live="polite" hidden></p>
 <details><summary>Buscar por ID</summary><div class="row"><label>ID de captura<input id="checkpointId" type="number" min="1" step="1"></label><button id="openCheckpoint">Abrir captura</button></div></details>
 </details><table id="jobList"></table>
 <p><button id="prevJobs">Anterior</button> <button id="nextJobs">Siguiente</button></p>
 <h2>Incidencias por causa, cliente y recurso</h2><p class="sub">Últimas 24 h</p><table id="incidents"></table>
 <button id="moreIncidents">Más grupos</button></div>
 <div class="card" data-view="cost" hidden><h2>Coste por trabajo terminado</h2>
 <form id="costFilter" class="row"><label>Cliente<input id="costOwner"></label><label>Modelo<input id="costModel"></label>
 <label>Clase<input id="costClass"></label><label>Período<select id="costPeriod"><option value="86400">24 horas</option><option value="2592000">30 días</option><option value="">Todo</option></select></label><button>Consultar</button></form>
 <p class="sub">Importes en millonésimas de la moneda indicada. Incluye los intentos fallidos de la cohorte. El denominador es el número de trabajos válidos terminados.</p><table id="costRows"></table></div>
 <div class="grid">
  <div class="card" data-view="summary"><h2>Clientes y modo</h2><table id="clients"></table><p class="sub">off: sin Rubato · shadow: observa sin bloquear · enforce: exige permiso.</p></div>
  <div class="card" data-view="cost" hidden><h2>Cuota</h2><div id="resources"></div><div class="legend"><span class="sw" style="background:var(--c1)"></span>fondo<span class="sw" style="background:var(--c2)"></span>urgente<span class="sw" style="background:var(--warn)"></span>incierto</div></div>
  <div class="card" data-view="policies" hidden><h2>Política</h2><div id="policy"></div>
   <form id="pf"><div class="row"><label>Política<select id="pname"><option value="radar">Radar</option><option value="fifo">FIFO</option><option value="reserva">Reserva</option><option value="fair">Turno ponderado</option></select></label>
   <label>Recurso<select id="pres"></select></label><label>Reserva<input id="preserve" type="number" min="0" step="1"></label>
   <label>Hora de liberación<input id="prelease" type="time"></label></div>
   <p id="punit" class="sub"></p>
   <details><summary>JSON avanzado</summary><label>Parámetros<textarea id="pjson" rows="5" cols="32"></textarea></label>
   <button type="button" id="pexport">Exportar JSON</button></details>
   <p><button class="primary">Guardar borrador</button> <span id="pmsg" aria-live="polite"></span></p></form>
   <div class="row"><label>Traza para la evaluación<input type="file" id="previewTrace" accept=".jsonl"></label>
   <button id="previewPolicy">Evaluar</button><label>Motivo del cambio<input id="policyReason" maxlength="500"></label><button id="applyPolicy">Aplicar</button></div>
   <pre id="previewResult" aria-live="polite"></pre><h2>Historial de revisiones</h2><table id="policyHistory"></table></div>
  <div class="card" data-view="summary"><h2>Cola</h2><table id="queue"></table><h2 style="margin-top:12px">Motivos de espera</h2><table id="reasons"></table></div>
 </div>
 <div data-view="summary"><div class="card" id="fairCard" hidden style="margin-top:14px"><h2>Turnos entregados por cliente</h2><p id="fairScope" class="sub"></p><table id="fairTurns"></table></div></div>
 <div class="card" data-view="policies" hidden style="margin-top:14px"><h2>Fase shadow (24 h)</h2><table id="shadow"></table><table id="shadowEvidence"></table><p class="sub">Qué habría decidido Rubato en las llamadas que los clientes en shadow hicieron por su cuenta. «Aplazaría» = divergencia.</p></div>
 <div class="card" data-view="summary" style="margin-top:14px"><h2>Intentos inciertos</h2><table id="unknown"></table></div>
 <div class="card" data-view="summary" style="margin-top:14px"><h2>Pasos en espera</h2><p class="sub">Pulsa una fila para ver su ficha.</p><table id="waiting"></table></div>
 <div class="card" style="margin-top:14px" id="cardbox" hidden><h2>Ficha del trabajo <button id="cardclose" style="float:right">Cerrar</button></h2>
 <div id="replayActions" class="row" hidden><button id="reviewCheckpoint">Reproducir incidente</button><label class="inline"><input type="checkbox" id="compareCheckpoint"> Comparar política del formulario</label></div>
 <details id="replayResult" hidden><summary id="replayTitle" aria-live="polite"></summary><pre id="checkpointPreview"></pre></details>
 <pre id="card" style="white-space:pre-wrap;font:12px/1.5 ui-monospace,Consolas,monospace;margin:0"></pre></div>
 <div class="card" data-view="jobs" hidden style="margin-top:14px"><h2>Últimas decisiones</h2><p><select id="fcl"><option value="">todos los clientes</option></select> <label class="sub"><input type="checkbox" id="fdef"> solo aplazamientos y cierres</label></p><p id="decisionStatus" class="sub" aria-live="polite"></p><table id="decisions"></table><p><button id="moreDecisions">Más decisiones</button></p></div>
</section></main>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const hhmm=t=>t?new Date(t*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit',timeZoneName:'short'})+' · '+new Date(t*1000).toISOString().slice(11,19)+' UTC':'—';
const ago=(now,t)=>{if(!t)return'—';const s=now-t;return s<90?s+' s':s<5400?Math.round(s/60)+' min':(s/3600).toFixed(1)+' h'};
const REASON={fifo_order:'orden de llegada',priority_order:'prioridad',urgent_first:'urgente primero',backfill:'adelanta a otro',deadline_reserve:'reservado para urgencias',capacity_exhausted:'cuota agotada',rate_limited:'límite por minuto',concurrency_limit:'sin hueco de ejecución',provider_pause:'pausa del proveedor',depends_on_step:'espera otro paso',dependency_failed:'falló un paso previo',never_fits:'nunca cabe',deadline_impossible:'plazo imposible',max_attempts:'intentos agotados',cancelled:'cancelado',client_unavailable:'cliente sin ejecutor',usage_unknown:'coste de admisión desconocido',fair_turn:'turno ponderado'};
const why=r=>esc(REASON[r]||r||'—');
let token=sessionStorage.getItem('rubato-admin')||'';{const m=location.hash.match(/token=([^&]+)/);if(m){token=decodeURIComponent(m[1]);sessionStorage.setItem('rubato-admin',token);history.replaceState(null,'',location.pathname)}}let timer=null;
async function api(method,path,body,headers={}){const r=await fetch(path,{method,headers:{'Authorization':'Bearer '+token,'Content-Type':'application/json',...headers},body:body?JSON.stringify(body):undefined});
 const data=await r.json().catch(()=>({}));if(!r.ok)throw new Error(data.message||('HTTP '+r.status));return data}
function show(logged){$('login').hidden=logged;$('app').hidden=!logged}
async function load(){let d;try{d=await api('GET','/v1/admin/overview')}catch(e){if(/token|401|403/.test(e.message)){show(false);$('lerr').textContent=e.message}else{$('meta').innerHTML='<span class="err">'+esc(e.message)+'</span>'}return}
 show(true);const now=d.t;
 if(!signals||now-signals.as_of>=30){try{signals=await api('GET','/v1/admin/digest')}catch{}}
 $('meta').textContent=(d.environment==='habitual'?'Rubato habitual':'Entrevista')+' · actualizado '+hhmm(now)+' · política '+d.policy.label+(d.policy.version?' (versión '+d.policy.version+')':'')+' · trabajos 24 h: '+Object.entries(d.jobs_last_24h).map(([k,v])=>k+' '+v).join(', ');
 const al=[];if(d.example_tokens&&d.example_tokens.length)al.push('Tokens de ejemplo en uso ('+d.example_tokens.join(', ')+'): cámbialos con RUBATO_CLIENTS antes de exponer el servicio fuera de este equipo.');if(d.unknown_attempts.length)al.push(d.unknown_attempts.length+' intento(s) incierto(s) pendientes de conciliar.');d.pauses.forEach(p=>al.push('Proveedor en pausa ('+p.scope_kind+' '+p.scope_value+') hasta '+hhmm(p.until)+'.'));
 d.resources.filter(r=>r.window!=='concurrency'&&r.capacity&&100*r.used/r.capacity>=(r.alert_at??90)).forEach(r=>al.push('Cuota casi agotada en '+r.id+': quedan '+r.available+' de '+r.capacity+'.'));
 $('alerts').innerHTML=al.map(a=>'<div class="alert">'+esc(a)+'</div>').join('');
 $('clients').innerHTML='<tr><th>Cliente</th><th>Modo</th><th>Ejecutor</th><th class="num">Ranuras</th></tr>'+d.clients.map(c=>'<tr><td>'+esc(c.id)+'</td><td><select data-client="'+esc(c.id)+'">'+['off','shadow','enforce'].map(m=>'<option'+(m===c.mode?' selected':'')+'>'+m+'</option>').join('')+'</select></td><td>'+(c.available_until&&c.available_until>=now?'<span class="ok">disponible</span>':'<span class="sub">inactivo</span>')+'</td><td class="num">'+esc(c.slots)+'</td></tr>').join('');
 showFairTurns(d.fair_turns);
 document.querySelectorAll('select[data-client]').forEach(s=>s.onchange=async()=>{const m=s.value,c=s.dataset.client;
  if(m==='enforce'&&!confirm('Pasar '+c+' a enforce: sus llamadas necesitarán permiso de Rubato. ¿Continuar?')){load();return}
  try{await api('PUT','/v1/admin/clients/'+encodeURIComponent(c)+'/mode',{mode:m})}catch(e){alert(e.message)}load()});
 $('resources').innerHTML=d.resources.map(r=>{const cap=Math.max(1,r.capacity),u=Math.max(0,r.used-r.used_urgent-r.unknown),pct=x=>Math.min(100,100*x/cap);
  return '<div><b>'+esc(r.id)+'</b> <span class="sub">'+esc(r.unit)+' · '+esc(r.window)+(Object.keys(r.scope||{}).length?' · '+esc(JSON.stringify(r.scope)):'')+'</span><span style="float:right">'+esc(r.used)+' / '+esc(r.capacity)+' · quedan <b>'+esc(r.available)+'</b>'+(r.resets_at?' · reinicio '+hhmm(r.resets_at):'')+'</span>'+
  '<div class="bar" role="img" aria-label="'+esc(r.id)+' usado '+esc(r.used)+' de '+esc(r.capacity)+'"><span class="u" style="width:'+pct(u)+'%"></span><span class="g" style="left:'+pct(u)+'%;width:'+pct(r.used_urgent)+'%"></span><span class="x" style="left:'+pct(u+r.used_urgent)+'%;width:'+pct(r.unknown)+'%"></span></div></div>'}).join('')||'<p class="sub">Sin recursos configurados.</p>';

 current=d;const cfg=d.policy.config;const wanted=$('pres').value;
 if(!$('pres').options.length){for(const r of d.resources){const o=document.createElement('option');o.value=r.id;o.textContent=r.id;$('pres').append(o)}}
 if(wanted)$('pres').value=wanted;
 if(!editing){$('pname').value=cfg.name;showUnits(cfg)}
 $('policy').textContent='Activa: '+d.policy.label+' · revisión '+(d.policy.version||0);
 const oldest=[...d.waiting].sort((a,b)=>a.ready_since-b.ready_since)[0];
 $('old').textContent=oldest?'Espera más antigua · '+ago(now,oldest.ready_since):'Sin trabajos en espera';
 $('pending').textContent='Conciliaciones pendientes · '+d.unknown_attempts.length;
 $('risk').textContent=signals?.risk?.length?'Plazo en riesgo · '+hhmm(signals.risk[0].deadline):'Consultar plazos en riesgo';

 const byc=Object.create(null);d.queue.forEach(q=>{(byc[q.client_id]=byc[q.client_id]||{})[q.state]=q.n;if(q.state==='ready')byc[q.client_id].oldest=q.oldest});
 const st=['ready','waiting','granted','running','unknown'];$('queue').innerHTML='<tr><th>Cliente</th>'+st.map(s=>'<th class="num">'+s+'</th>').join('')+'<th>Más antiguo</th></tr>'+Object.entries(byc).map(([c,v])=>'<tr><td>'+esc(c)+'</td>'+st.map(s=>'<td class="num">'+(v[s]||0)+'</td>').join('')+'<td>'+ago(now,v.oldest)+'</td></tr>').join('');
 $('reasons').innerHTML='<tr><th>Cliente</th><th>Motivo</th><th class="num">Pasos</th><th>Próxima revisión</th></tr>'+d.reasons.map(r=>'<tr><td>'+esc(r.client_id)+'</td><td>'+why(r.last_reason)+'</td><td class="num">'+r.n+'</td><td>'+hhmm(r.next)+'</td></tr>').join('');
 const sh=Object.create(null);d.shadow_24h.forEach(x=>{const c=sh[x.client_id]=sh[x.client_id]||{agree:0,diverge:0,reasons:{}};if(x.action==='authorize')c.agree+=x.n;else{c.diverge+=x.n;c.reasons[x.reason]=(c.reasons[x.reason]||0)+x.n}});
 $('shadow').innerHTML=Object.keys(sh).length?'<tr><th>Cliente</th><th class="num">Coincide</th><th class="num">Aplazaría</th><th>Motivos de divergencia</th></tr>'+Object.entries(sh).map(([c,v])=>'<tr><td>'+esc(c)+'</td><td class="num">'+v.agree+'</td><td class="num">'+v.diverge+'</td><td>'+Object.entries(v.reasons).map(([r,n])=>why(r)+' × '+n).join(', ')+'</td></tr>').join(''):'<tr><td class="sub">Sin llamadas en shadow en las últimas 24 h.</td></tr>';
 $('unknown').innerHTML=d.unknown_attempts.length?'<tr><th>Intento</th><th>Trabajo / paso</th><th>Cliente</th><th>Iniciado</th><th>Reservado</th><th>Resolver</th></tr>'+d.unknown_attempts.map(a=>'<tr><td>'+esc(a.id.slice(0,8))+'</td><td>'+esc(a.job_id)+' / '+esc(a.step_id)+'</td><td>'+esc(a.client_id)+'</td><td>'+hhmm(a.started_at)+'</td><td>'+esc(JSON.stringify(a.admitted))+'</td><td><button data-rec="'+esc(a.id)+'" data-c="1">Sí consumió</button> <button data-rec="'+esc(a.id)+'" data-c="0">No consumió</button></td></tr>').join(''):'<tr><td class="sub">Ninguno. Todo liquidado.</td></tr>';
 document.querySelectorAll('button[data-rec]').forEach(b=>b.onclick=async()=>{const consumed=b.dataset.c==='1';
  const evidence=prompt(consumed?'Referencia verificable del consumo (recibo, registro o solicitud).':'Justificación verificable de que no hubo consumo.');
  if(!evidence?.trim())return;
  try{await api('POST','/v1/attempts/'+encodeURIComponent(b.dataset.rec)+'/reconcile',{consumed,evidence:evidence.trim()})}catch(e){alert(e.message)}load()});
 const ev=$('shadowEvidence');ev.innerHTML='<tr><th>Cliente</th><th>Observaciones</th><th>Decisiones</th><th>p50 / p95 del motor (ms)</th><th>Evidencia de adopción</th></tr>';
 for(const row of (d.shadow_evidence||[])){const tr=ev.insertRow();for(const v of [row.client_id,row.observed,row.decisions,(row.p50??'—')+' / '+(row.p95??'—'),'Falta medir la latencia desde el cliente'])tr.insertCell().textContent=v}
 const fc=$('fcl');d.clients.forEach(c=>{if(![...fc.options].some(o=>o.value===c.id)){const o=document.createElement('option');o.value=c.id;o.textContent=c.id;fc.appendChild(o)}});
 $('waiting').innerHTML='<tr><th>Trabajo</th><th>Paso</th><th>Cliente</th><th>Espera</th><th>Motivo</th><th>Revisión</th><th>Plazo</th></tr>'+d.waiting.map(w=>'<tr><td>'+esc(w.job_id)+(w.urgent?' <span class="tag">urgente</span>':'')+'</td><td>'+esc(w.step_id)+'</td><td>'+esc(w.client_id)+'</td><td>'+ago(now,w.ready_since)+'</td><td>'+why(w.last_reason)+'</td><td>'+hhmm(w.next_reconsider)+'</td><td>'+hhmm(w.deadline)+'</td></tr>').join('');
 $('waiting').querySelectorAll('tr').forEach((tr,i)=>{if(i===0)return;tr.style.cursor='pointer';tr.tabIndex=0;tr.onclick=()=>card(d.waiting[i-1].job_id);tr.onkeydown=e=>{if(e.key==='Enter')card(d.waiting[i-1].job_id)}});
 if(mode==='jobs'&&!decisionCursor)listDecisions();}

async function card(job){try{const ds=await api('GET','/v1/decisions?limit=500&job='+encodeURIComponent(job));const j=await api('GET','/v1/jobs/'+encodeURIComponent(job));
 const NL=String.fromCharCode(10);
 let t=j.id+' ('+j.cls+(j.urgent?', urgente':'')+') · estado '+j.state+' · llegada '+hhmm(j.arrival)+(j.deadline?' · plazo '+hhmm(j.deadline):'')+NL;
 j.steps.forEach(s=>{t+='  paso '+s.step_id+': '+s.state+', intentos '+s.attempts+', motivo '+(REASON[s.last_reason]||s.last_reason||'—')+(s.next_reconsider?', revisión '+hhmm(s.next_reconsider):'')+NL});
 if(j.deadline_risk){const r=j.deadline_risk;t+=NL+'Plazo '+hhmm(r.deadline)+' · fin estimado '+hhmm(r.expected_finish)+' · próxima revisión '+hhmm(r.next_reconsider)+NL+'Camino crítico '+r.critical_path.join(' → ')+' · '+r.critical_path_s+' s de camino · '+r.work_s+' s de trabajo'+NL}
 j.steps.filter(s=>s.depends_on?.length).forEach(s=>{t+='  '+s.step_id+' depende de '+s.depends_on.join(', ')+NL});
 j.attempts.forEach(a=>{t+='  '+a.id+' · permiso '+hhmm(a.granted_at)+' · inicio '+hhmm(a.started_at)+' · liquidación '+hhmm(a.finished_at)+' · '+a.state+(a.receipt?' · recibo '+a.receipt:'')+(a.tariff_quote?' · tarifa '+(a.tariff_quote.version||'explícita')+' '+a.tariff_quote.currency:'')+NL});
 t+=NL;ds.forEach(x=>{t+=hhmm(x.t)+'  '+x.step_id+'#'+x.attempt+'  '+({authorize:'AUTORIZA',defer:'APLAZA',close:'CIERRA'}[x.action]||x.action)+'  ['+(REASON[x.reason]||x.reason)+']'+(x.detail?NL+'      '+x.detail:'')+(x.action==='defer'&&x.next_t?NL+'      revisar '+hhmm(x.next_t):'')+NL});
 activeJob=j.id;$('replayActions').hidden=!checkpoint;$('replayResult').hidden=true;$('card').textContent=t;$('cardbox').hidden=false;$('cardbox').scrollIntoView({behavior:'smooth',block:'nearest'})}catch(e){alert(e.message)}}
$('cardclose').onclick=()=>{$('cardbox').hidden=true};$('fcl').onchange=$('fdef').onchange=()=>listDecisions();
$('lf').onsubmit=e=>{e.preventDefault();token=$('tok').value.trim();sessionStorage.setItem('rubato-admin',token);load()};
$('logout').onclick=()=>{sessionStorage.removeItem('rubato-admin');token='';show(false)};
$('refresh').onclick=()=>{load();if(capturesVisible()&&!captureBusy)refreshCaptures()};
let current=null,signals=null,editing=false,draft=null,mode='summary',filter={},pages=[null],page=0,nextJob=null,nextIncident=null;
let checkpoint=null,activeJob=null;
let recentCaptures=[],captureRequest=0,captureBusy=false,captureOptions='';
const capturesUrl='/v1/admin/replay-checkpoints';
const capturePath=id=>capturesUrl+'/'+id;
const captureStatus={complete:'Captura finalizada',recording:'Grabando',partial:'Captura parcial'};
const captureState={recording:'Grabando',elapsed:'Cerrada por tiempo',manual:'Finalizada',capacity_limit:'Cerrada por límite de captura',snapshot:'Solo estado inicial'};
const capturesVisible=()=>mode==='jobs'&&$('captureCatalogue').open;
const captureLabel=row=>'ID '+row.id+' · '+captureState[row.state]+' · '+new Date(row.captured_at*1000).toLocaleDateString();
function captureNotice(message){
 const element=$('checkpointNotice');if(element.textContent!==message)element.textContent=message;element.hidden=!message;
}
function updateCaptureControls(){
 $('saveCheckpoint').disabled=captureBusy||checkpoint?.state==='recording'||recentCaptures.some(row=>row.state==='recording');
 $('stopCheckpoint').disabled=captureBusy||checkpoint?.state!=='recording';
 $('downloadCheckpoint').disabled=!checkpoint;
 $('recentCheckpoints').disabled=captureBusy||!recentCaptures.length;$('openCheckpoint').disabled=captureBusy;
}
function selectCheckpoint(row){
 if(checkpoint?.id!==row?.id){$('replayResult').hidden=true;$('checkpointId').value=row?.id??''}
 checkpoint=row||null;
 if(row)sessionStorage.setItem('rubato-checkpoint',row.id);else sessionStorage.removeItem('rubato-checkpoint');
 $('recentCheckpoints').value=row?.id??'';$('replayActions').hidden=!row||!activeJob;
 const status=row?captureLabel(row)+' · '+hhmm(row.captured_at)+
 (row.state==='recording'?' · hasta '+hhmm(Date.parse(row.record_until)/1000):''):'No hay ninguna captura seleccionada.';
 if($('checkpointStatus').textContent!==status)$('checkpointStatus').textContent=status;
 updateCaptureControls();
}
async function refreshCaptures(include=checkpoint?.id||Number(sessionStorage.getItem('rubato-checkpoint')),explicit=false){
 const request=++captureRequest,auth=token;
 try{
  const d=await api('GET',capturesUrl+(include?'?include='+include:''));
  if(request!==captureRequest||auth!==token)return;
  recentCaptures=d.items;const labels=recentCaptures.map(captureLabel),signature=labels.join('|');
  if(signature!==captureOptions){
   $('recentCheckpoints').replaceChildren(new Option('Selecciona una captura',''),...recentCaptures.map((r,i)=>new Option(labels[i],r.id)));
   captureOptions=signature;
  }
  const row=recentCaptures.find(r=>r.id===include);
  if(explicit&&!row){captureNotice('No se encontró la captura '+include+'.');updateCaptureControls();return}
  selectCheckpoint(row||recentCaptures.find(r=>r.state==='recording'));captureNotice('');
 }catch(e){if(request===captureRequest&&auth===token)captureNotice(e.message)}
}
async function changeCapture(action){
 const auth=token;captureBusy=true;++captureRequest;updateCaptureControls();
 try{const row=await action();if(auth===token){selectCheckpoint(row);await refreshCaptures(row.id)}}
 catch(e){if(auth===token){await refreshCaptures();captureNotice(e.message)}}
 finally{captureBusy=false;updateCaptureControls()}
}
$('captureCatalogue').ontoggle=()=>{if(capturesVisible()&&token&&!captureBusy)refreshCaptures()};
$('recentCheckpoints').onchange=()=>{++captureRequest;selectCheckpoint(recentCaptures.find(r=>String(r.id)===$('recentCheckpoints').value));captureNotice('')};
$('saveCheckpoint').onclick=()=>changeCapture(async()=>({...await api('POST',capturesUrl,{record_seconds:300}),state:'recording'}));
$('openCheckpoint').onclick=async()=>{
 const id=$('checkpointId').valueAsNumber;
 if(!Number.isSafeInteger(id)||id<1){captureNotice('Introduce un ID de captura válido.');return}
 captureBusy=true;updateCaptureControls();
 try{await refreshCaptures(id,true)}finally{captureBusy=false;updateCaptureControls()}
};
$('stopCheckpoint').onclick=()=>changeCapture(async()=>{
 const row=checkpoint,d=await api('POST',capturePath(row.id)+'/stop');return {...row,state:d.stop_reason,stopped_at:d.stopped_at};
});
$('downloadCheckpoint').onclick=async()=>{
 try{const data=await api('GET',capturePath(checkpoint.id));
 const u=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));
 const a=document.createElement('a');a.href=u;a.download='captura-rubato-'+checkpoint.id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);
 }catch(e){captureNotice(e.message)}
};
$('reviewCheckpoint').onclick=async()=>{
 const button=$('reviewCheckpoint'),job=activeJob,id=checkpoint.id;button.disabled=true;
 const report=$('replayResult'),title=$('replayTitle'),content=$('checkpointPreview');
 report.hidden=false;report.open=true;title.textContent='Reproduciendo…';content.textContent='';
 try{
  const body={job};if($('compareCheckpoint').checked)body.policy=policyValues();
  const d=await api('POST',capturePath(id)+'/replay',body);
  if(activeJob!==job||checkpoint?.id!==id)return;
  title.textContent=captureStatus[d.coverage.status]+' · '+d.matched_rounds+' de '+d.coverage.rounds+' rondas coinciden · '+d.rounds.length+' de este trabajo';
  const format=x=>x.step+' · '+({authorize:'autoriza',defer:'aplaza',close:'cierra'}[x.action]||x.action)+' · '+(REASON[x.reason]||x.reason)+(x.resource?' · '+x.resource:'');
  const lines=d.rounds.flatMap(r=>[
   hhmm(r.t)+' · ronda '+r.ordinal+' · '+r.policy+(r.revision?' · revisión '+r.revision:'')+(r.matched?'':' · diferencia detectada'),
   ...r.observed.map(x=>'  Registrada · '+format(x)),
   ...(!r.matched?r.reference.map(x=>'  Reproducida · '+format(x)):[]),
   ...(r.candidate?r.candidate.map(x=>'  Alternativa · '+format(x)):[])
  ]);
  const eventNames={granted:'Permiso reservado',permit_delivered:'Permiso entregado',attempt_started:'Intento iniciado',attempt_finished:'Intento liquidado',attempt_unknown:'Intento incierto',attempt_reconciled:'Intento conciliado',grant_expired:'Permiso caducado',job_cancelled:'Trabajo cancelado',job_registered:'Trabajo registrado'};
  if(body.policy)lines.unshift('La alternativa se calcula sobre los datos observados en cada ronda.');
  if(!d.rounds.length)lines.push('Sin rondas de este trabajo en la captura.');
  if(d.events.length)lines.push('','Eventos del trabajo',...d.events.map(e=>hhmm(e.t)+' · '+(eventNames[e.kind]||e.kind)+(e.step_id?' · '+e.step_id:'')+(e.outcome?.status?' · '+e.outcome.status:'')));
  content.textContent=lines.join(String.fromCharCode(10));
 }catch(e){title.textContent=e.message}finally{button.disabled=false}
};
function showFairTurns(report){
 $('fairCard').hidden=!report;if(!report)return;
 $('fairScope').textContent='Últimas 24 h · revisión '+report.revision+' · '+report.resource+' · espera desde que el paso quedó listo hasta que el cliente recibió el permiso.'+
 (report.legacy_deliveries_without_timestamp?' '+report.legacy_deliveries_without_timestamp+' entregas anteriores a la medición.':'');
 const table=$('fairTurns');table.innerHTML='<tr><th>Cliente</th><th>Peso</th><th>Solicitudes</th><th>Turnos entregados</th><th>Espera p95</th></tr>';
 for(const c of report.clients){const tr=table.insertRow();const state=c.requested_slots?'Solicitó huecos':c.requested?'Consultó con 0 huecos':'Sin solicitudes observadas';
  for(const value of [c.client_id,c.weight,state,c.delivered,c.wait_p95_s==null?'—':c.wait_p95_s+' s ('+c.wait_samples+')'])tr.insertCell().textContent=value}
}
function getQuery(values){return new URLSearchParams(Object.entries(values).filter(([,v])=>v!=null&&v!=='')).toString()}
function showView(view){mode=view;document.querySelectorAll('[data-view]').forEach(e=>e.hidden=e.dataset.view!==view);document.querySelectorAll('[data-tab]').forEach(e=>e.setAttribute('aria-current',String(e.dataset.tab===view)));if(capturesVisible()&&!captureBusy)refreshCaptures()}
function buttonRow(table,label,fn,values){const row=table.insertRow();const b=document.createElement('button');b.textContent=label;b.onclick=fn;row.insertCell().append(b);for(const v of values)row.insertCell().textContent=v??'—'}
async function listJobs(){
 const table=$('jobList');
 try{const d=await api('GET','/v1/investigation/jobs?'+getQuery({...filter,cursor:pages[page]}));
  table.replaceChildren();nextJob=d.next_cursor;
  if(d.items.length){table.innerHTML='<tr><th>Trabajo</th><th>Cliente</th><th>Estado</th><th>Plazo</th></tr>';
   d.items.forEach(j=>buttonRow(table,j.id,()=>card(j.id),[j.client_id,j.state,hhmm(j.deadline)]))}
  $('jobStatus').textContent=d.items.length?'Página '+(page+1)+' · '+d.items.length+' trabajos':'Sin trabajos para estos filtros';
  $('prevJobs').disabled=page===0;$('nextJobs').disabled=!nextJob;
 }catch(e){$('jobStatus').textContent=e.message}
}
let decisionCursor=null,decisionRequest=0;
async function listDecisions(more=false){
 const request=++decisionRequest,table=$('decisions'),action={authorize:'autoriza',defer:'aplaza',close:'cierra'};
 try{
  const d=await api('GET','/v1/investigation/decisions?'+getQuery({owner:$('fcl').value,deferred:$('fdef').checked,cursor:more?decisionCursor:null}));
  if(request!==decisionRequest)return;
  if(!more)table.innerHTML='<tr><th>Trabajo</th><th>Hora</th><th>Paso</th><th>Decisión</th><th>Motivo</th><th>Recurso</th></tr>';
  d.items.forEach(x=>buttonRow(table,x.job_id,()=>card(x.job_id),[hhmm(x.t),x.step_id+' #'+x.attempt,action[x.action]||x.action,REASON[x.reason]||x.reason,x.resource]));
  decisionCursor=d.next_cursor;$('moreDecisions').disabled=!decisionCursor;
  $('decisionStatus').textContent=d.items.length?'Decisiones desde el servicio':'Sin decisiones para estos filtros';
 }catch(e){if(request===decisionRequest)$('decisionStatus').textContent=e.message}
}
$('moreDecisions').onclick=()=>listDecisions(true);
async function groupIncidents(more=false){
 const table=$('incidents');
 try{const d=await api('GET','/v1/investigation/incidents?'+getQuery({cursor:more?nextIncident:null}));
  if(!more)table.innerHTML='<tr><th>Causa</th><th>Cliente</th><th>Recurso</th><th>Trabajos / rondas</th><th>Apertura</th><th>Última observación</th><th>Resolución</th></tr>';
  d.items.forEach(i=>buttonRow(table,REASON[i.reason]||i.reason,()=>jump({owner:i.client_id,reason:i.reason,resource:i.resource}),
   [i.client_id,i.resource,i.jobs+' / '+i.rounds,hhmm(i.opened),hhmm(i.last_seen),i.open?'Abierta':i.resolved_at?hhmm(i.resolved_at):'Cerrada · hora sin registrar']));
  if(!more&&!d.items.length)table.insertRow().insertCell().textContent='Sin incidencias registradas';
  nextIncident=d.next_cursor;$('moreIncidents').disabled=!nextIncident;
 }catch(e){table.textContent=e.message}
}
function jump(values={}){filter=values;pages=[null];page=0;$('jq').value=values.q||'';$('jc').value=values.owner||'';$('jr').value=values.reason||'';showView('jobs');listJobs();groupIncidents();listDecisions()}
$('search').onsubmit=e=>{e.preventDefault();jump({q:$('jq').value,owner:$('jc').value,reason:$('jr').value})};
$('resetFilters').onclick=()=>jump();$('nextJobs').onclick=()=>{if(nextJob){pages[++page]=nextJob;listJobs()}};
$('prevJobs').onclick=()=>{if(page){page--;listJobs()}};$('moreIncidents').onclick=()=>groupIncidents(true);
document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{showView(b.dataset.tab);if(mode==='jobs'){listJobs();groupIncidents();listDecisions()}if(mode==='cost')costs();if(mode==='policies')history()});
$('old').onclick=()=>{const oldest=current?.waiting?.slice().sort((a,b)=>a.ready_since-b.ready_since)[0];jump({q:oldest?.job_id,filter_by:'waiting'})};
$('pending').onclick=()=>jump({filter_by:'unknown'});
$('risk').onclick=()=>{const first=signals?.risk?.[0];if(first){jump({q:first.job});card(first.job)}else $('digest').textContent='Sin avisos de plazo en los trabajos observados.'};
$('yesterday').onclick=async()=>{
 const box=$('digest');
 try{signals=await api('GET','/v1/admin/digest');const d=signals;
  box.replaceChildren();const intro=document.createElement('p');intro.textContent='Desde '+hhmm(d.since)+' · '+d.policy_changes.length+' cambios de política · '+Math.round(100*d.coverage.observed_s/d.coverage.requested_s)+' % con actividad observada.';box.append(intro);
  const x=document.createElement('button');x.textContent=d.missed_urgent_jobs+' urgencias vencidas';x.onclick=()=>jump({filter_by:'missed',since:d.since});box.append(x);
  const money=Object.entries(d.uncertain_cost_by_currency||{});if(money.length){const p=document.createElement('p');p.textContent='Gasto incierto (millonésimas): '+money.map(([c,v])=>v+' '+c).join(' · ');box.append(p)}
  if(d.top_issue){const y=document.createElement('button');y.textContent=(REASON[d.top_issue.reason]||d.top_issue.reason)+' · '+d.top_issue.jobs+' trabajos';y.onclick=()=>jump({reason:d.top_issue.reason,since:d.since});box.append(y)}
 }catch(e){box.textContent=e.message}
};
async function costs(){
 const table=$('costRows'),period=$('costPeriod').value;
 try{const d=await api('GET','/v1/consumption?'+getQuery({owner:$('costOwner').value,model:$('costModel').value,cls:$('costClass').value,since:period?Math.floor(Date.now()/1000)-Number(period):null}));
 table.innerHTML='<tr><th>Cliente</th><th>Moneda</th><th>Confirmado</th><th>Reservado</th><th>Incierto</th><th>Sin importe</th><th>Terminados</th><th>Coste por trabajo</th></tr>';
 for(const x of d.items){const row=table.insertRow();for(const value of [x.client_id,x.currency,x.confirmed,x.reserved,x.uncertain,x.unknown_attempts,x.completed_jobs,x.cost_per_completed_job_micro])row.insertCell().textContent=value??'—'}
 if(!d.items.length)table.insertRow().insertCell().textContent='Sin importes para estos filtros';
 }catch(e){table.textContent=e.message}
}
$('costFilter').onsubmit=e=>{e.preventDefault();costs()};
function showUnits(cfg){
 const r=current?.resources.find(r=>r.id===$('pres').value);$('punit').textContent=r?r.capacity+' '+r.unit+' · '+r.window+(r.reset_tz?' · '+r.reset_tz:''):'Selecciona un recurso';
 if(cfg){$('preserve').value=cfg.reserve?.[$('pres').value]??'';$('prelease').value=cfg.release_offset==null?'':[Math.floor(cfg.release_offset/3600),Math.floor(cfg.release_offset/60)%60].map(x=>String(x).padStart(2,'0')).join(':')}
}
$('pres').onchange=()=>showUnits(current?.policy.config);
$('pf').oninput=()=>{editing=true};
function policyValues(){
 if($('pjson').value.trim())return JSON.parse($('pjson').value);
 const name=$('pname').value;
 if(name==='radar'||name==='fifo')return {name};
 const old=current.policy.config;
 const cfg=old.name===name?{...old}:{name};
 if(name==='fair'){cfg.resource=$('pres').value;cfg.weights=old.weights||{}}
 cfg.reserve={...(cfg.reserve||{})};
 if($('preserve').value!=='')cfg.reserve[$('pres').value]=$('preserve').valueAsNumber;
 const time=$('prelease').value;
 cfg.release_offset=time?Number(time.slice(0,2))*3600+Number(time.slice(3))*60:null;
 return cfg;
}
$('pexport').onclick=()=>{$('pjson').value=JSON.stringify(policyValues(),null,2)};
$('pf').onsubmit=async e=>{e.preventDefault();try{
 draft=await api('POST','/v1/admin/policy-drafts',{policy:policyValues(),base_revision:current.policy.version||0});
 $('pmsg').textContent='Borrador '+draft.id+' guardado';$('previewResult').textContent='';
 }catch(err){$('pmsg').textContent=err.message}};
$('previewPolicy').onclick=async()=>{
 const file=$('previewTrace').files[0];if(!draft||!file){$('pmsg').textContent='Guarda un borrador y elige una traza.';return}
 try{const d=await api('POST','/v1/admin/policy-drafts/'+draft.id+'/preview',{trace:await file.text()});
 const changes=Object.keys(d.after.by_client).map(c=>({cliente:c,referencia:d.before.by_client[c],borrador:d.after.by_client[c]}));
 $('previewResult').textContent='Traza '+d.trace+' · '+d.jobs+' trabajos · '+d.arrival_provenance.join(', ')+String.fromCharCode(10)+JSON.stringify(changes,null,2);
 }catch(e){$('pmsg').textContent=e.message}
};
async function changePolicy(path,revision){
 const reason=$('policyReason').value;if(!reason.trim()){$('pmsg').textContent='Indica el motivo del cambio.';return}
 try{const d=await api('POST',path,{reason},{'If-Match':'"'+revision+'"','Idempotency-Key':path+'-'+revision});
  $('pmsg').textContent='Revisión '+d.previous_revision+' → '+d.revision;draft=null;editing=false;$('pjson').value='';await load();history();
 }catch(e){$('pmsg').textContent=e.message;await load()}
}
$('applyPolicy').onclick=async()=>{if(!draft){$('pmsg').textContent='Guarda un borrador primero.';return}
 $('applyPolicy').disabled=true;try{await changePolicy('/v1/admin/policy-drafts/'+draft.id+'/activate',draft.base_revision)}finally{$('applyPolicy').disabled=false}
};
async function history(){
 const table=$('policyHistory');
 try{const data=await api('GET','/v1/admin/policy-history');table.innerHTML='<tr><th>Acción</th><th>Revisión</th><th>Política</th><th>Motivo</th></tr>';
 data.revisions.forEach(r=>buttonRow(table,'Volver a esta versión',()=>changePolicy('/v1/admin/policy-history/'+r.id+'/revert',data.active_revision),[r.id,r.body.name,r.reason]));
 }catch(e){table.textContent=e.message}
}
function tick(){clearInterval(timer);if($('auto').checked)timer=setInterval(()=>{
 if(document.hidden||!token)return;
 if(capturesVisible()&&!captureBusy)refreshCaptures();
 if(!document.activeElement.matches('button,input,textarea,select'))load();
},5000)}
$('auto').onchange=tick;tick();if(token)load();else show(false);
</script></body></html>
"""
