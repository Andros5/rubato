"""Live panel served by the API at ``/panel``. Self-contained: no external assets.

The page asks for the administrator token (kept in sessionStorage only), polls
``/v1/admin/overview`` and offers the operator actions: client mode, policy and the
reconciliation of uncertain attempts. Every server-provided text is escaped.
"""

PANEL_HTML = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rubato · panel</title>
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
button{cursor:pointer}button.primary{background:var(--c1);color:#fff;border-color:var(--c1)}
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
 <div id="alerts"></div>
 <div class="grid">
  <div class="card"><h2>Clientes y modo</h2><table id="clients"></table><p class="sub">off: sin Rubato · shadow: observa sin bloquear · enforce: exige permiso.</p></div>
  <div class="card"><h2>Cuota</h2><div id="resources"></div><div class="legend"><span class="sw" style="background:var(--c1)"></span>fondo<span class="sw" style="background:var(--c2)"></span>urgente<span class="sw" style="background:var(--warn)"></span>incierto</div></div>
  <div class="card"><h2>Política</h2><div id="policy"></div>
   <form id="pf"><p><select id="pname"><option value="radar">radar (referencia)</option><option value="fifo">fifo</option><option value="reserva">reserva</option></select>
   <input id="preserve" placeholder='reserva, p. ej. {"rpd": 4}' style="width:190px"> <input id="prelease" placeholder="liberar (s desde reinicio)" style="width:170px"></p>
   <p><button class="primary">Aplicar política</button> <span id="pmsg"></span></p></form></div>
  <div class="card"><h2>Cola</h2><table id="queue"></table><h2 style="margin-top:12px">Motivos de espera</h2><table id="reasons"></table></div>
 </div>
 <div class="card" style="margin-top:14px"><h2>Intentos inciertos</h2><table id="unknown"></table></div>
 <div class="card" style="margin-top:14px"><h2>Pasos en espera</h2><table id="waiting"></table></div>
 <div class="card" style="margin-top:14px"><h2>Últimas decisiones</h2><table id="decisions"></table></div>
</section></main>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const hhmm=t=>t?new Date(t*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'}):'—';
const ago=(now,t)=>{if(!t)return'—';const s=now-t;return s<90?s+' s':s<5400?Math.round(s/60)+' min':(s/3600).toFixed(1)+' h'};
const REASON={fifo_order:'orden de llegada',priority_order:'prioridad',urgent_first:'urgente primero',backfill:'adelanta a otro',deadline_reserve:'reservado para urgencias',capacity_exhausted:'cuota agotada',rate_limited:'límite por minuto',concurrency_limit:'sin hueco de ejecución',provider_pause:'pausa del proveedor',depends_on_step:'espera otro paso',dependency_failed:'falló un paso previo',never_fits:'nunca cabe',deadline_impossible:'plazo imposible',max_attempts:'intentos agotados',cancelled:'cancelado',client_unavailable:'cliente sin ejecutor'};
const why=r=>esc(REASON[r]||r||'—');
let token=sessionStorage.getItem('rubato-admin')||'';let timer=null;
async function api(method,path,body){const r=await fetch(path,{method,headers:{'Authorization':'Bearer '+token,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});
 const data=await r.json().catch(()=>({}));if(!r.ok)throw new Error(data.message||('HTTP '+r.status));return data}
function show(logged){$('login').hidden=logged;$('app').hidden=!logged}
async function load(){let d;try{d=await api('GET','/v1/admin/overview')}catch(e){if(/token|401|403/.test(e.message)){show(false);$('lerr').textContent=e.message}else{$('meta').innerHTML='<span class="err">'+esc(e.message)+'</span>'}return}
 show(true);const now=d.t;$('meta').textContent='Actualizado '+hhmm(now)+' · política '+d.policy.label+(d.policy.version?' (versión '+d.policy.version+')':'')+' · trabajos 24 h: '+Object.entries(d.jobs_last_24h).map(([k,v])=>k+' '+v).join(', ');
 const al=[];if(d.unknown_attempts.length)al.push(d.unknown_attempts.length+' intento(s) incierto(s) pendientes de conciliar.');d.pauses.forEach(p=>al.push('Proveedor en pausa ('+p.scope_kind+' '+p.scope_value+') hasta '+hhmm(p.until)+'.'));
 d.resources.filter(r=>r.window!=='concurrency'&&r.capacity&&r.available/r.capacity<0.1).forEach(r=>al.push('Cuota casi agotada en '+r.id+': quedan '+r.available+' de '+r.capacity+'.'));
 $('alerts').innerHTML=al.map(a=>'<div class="alert">'+esc(a)+'</div>').join('');
 $('clients').innerHTML='<tr><th>Cliente</th><th>Modo</th><th>Ejecutor</th><th class="num">Ranuras</th></tr>'+d.clients.map(c=>'<tr><td>'+esc(c.id)+'</td><td><select data-client="'+esc(c.id)+'">'+['off','shadow','enforce'].map(m=>'<option'+(m===c.mode?' selected':'')+'>'+m+'</option>').join('')+'</select></td><td>'+(c.available_until&&c.available_until>=now?'<span class="ok">disponible</span>':'<span class="sub">inactivo</span>')+'</td><td class="num">'+esc(c.slots)+'</td></tr>').join('');
 document.querySelectorAll('select[data-client]').forEach(s=>s.onchange=async()=>{const m=s.value,c=s.dataset.client;
  if(m==='enforce'&&!confirm('Pasar '+c+' a enforce: sus llamadas necesitarán permiso de Rubato. ¿Continuar?')){load();return}
  try{await api('PUT','/v1/admin/clients/'+encodeURIComponent(c)+'/mode',{mode:m})}catch(e){alert(e.message)}load()});
 $('resources').innerHTML=d.resources.map(r=>{const cap=Math.max(1,r.capacity),u=Math.max(0,r.used-r.used_urgent-r.unknown),pct=x=>Math.min(100,100*x/cap);
  return '<div><b>'+esc(r.id)+'</b> <span class="sub">'+esc(r.unit)+' · '+esc(r.window)+(Object.keys(r.scope||{}).length?' · '+esc(JSON.stringify(r.scope)):'')+'</span><span style="float:right">'+esc(r.used)+' / '+esc(r.capacity)+' · quedan <b>'+esc(r.available)+'</b>'+(r.resets_at?' · reinicio '+hhmm(r.resets_at):'')+'</span>'+
  '<div class="bar" role="img" aria-label="'+esc(r.id)+' usado '+esc(r.used)+' de '+esc(r.capacity)+'"><span class="u" style="width:'+pct(u)+'%"></span><span class="g" style="left:'+pct(u)+'%;width:'+pct(r.used_urgent)+'%"></span><span class="x" style="left:'+pct(u+r.used_urgent)+'%;width:'+pct(r.unknown)+'%"></span></div></div>'}).join('')||'<p class="sub">Sin recursos configurados.</p>';
 const cfg=d.policy.config;const pf=$('pf');if(!pf.contains(document.activeElement)){$('pname').value=cfg.name;$('preserve').value=cfg.reserve&&Object.keys(cfg.reserve).length?JSON.stringify(cfg.reserve):'';$('prelease').value=cfg.release_offset??''}$('policy').innerHTML='<p>Activa: <b>'+esc(d.policy.label)+'</b>'+(cfg.reserve?' · reserva '+esc(JSON.stringify(cfg.reserve)):'')+(cfg.release_offset!=null?' · libera a '+esc(cfg.release_offset)+' s del reinicio':'')+'</p>';
 const byc={};d.queue.forEach(q=>{(byc[q.client_id]=byc[q.client_id]||{})[q.state]=q.n;if(q.state==='ready')byc[q.client_id].oldest=q.oldest});
 const st=['ready','waiting','granted','running','unknown'];$('queue').innerHTML='<tr><th>Cliente</th>'+st.map(s=>'<th class="num">'+s+'</th>').join('')+'<th>Más antiguo</th></tr>'+Object.entries(byc).map(([c,v])=>'<tr><td>'+esc(c)+'</td>'+st.map(s=>'<td class="num">'+(v[s]||0)+'</td>').join('')+'<td>'+ago(now,v.oldest)+'</td></tr>').join('');
 $('reasons').innerHTML='<tr><th>Cliente</th><th>Motivo</th><th class="num">Pasos</th><th>Próxima revisión</th></tr>'+d.reasons.map(r=>'<tr><td>'+esc(r.client_id)+'</td><td>'+why(r.last_reason)+'</td><td class="num">'+r.n+'</td><td>'+hhmm(r.next)+'</td></tr>').join('');
 $('unknown').innerHTML=d.unknown_attempts.length?'<tr><th>Intento</th><th>Trabajo / paso</th><th>Cliente</th><th>Iniciado</th><th>Reservado</th><th>Resolver</th></tr>'+d.unknown_attempts.map(a=>'<tr><td>'+esc(a.id.slice(0,8))+'</td><td>'+esc(a.job_id)+' / '+esc(a.step_id)+'</td><td>'+esc(a.client_id)+'</td><td>'+hhmm(a.started_at)+'</td><td>'+esc(JSON.stringify(a.admitted))+'</td><td><button data-rec="'+esc(a.id)+'" data-c="1">Sí consumió</button> <button data-rec="'+esc(a.id)+'" data-c="0">No consumió</button></td></tr>').join(''):'<tr><td class="sub">Ninguno. Todo liquidado.</td></tr>';
 document.querySelectorAll('button[data-rec]').forEach(b=>b.onclick=async()=>{const consumed=b.dataset.c==='1';
  if(!confirm(consumed?'Confirmar que el proveedor procesó la llamada (se mantiene el consumo reservado).':'Confirmar que el proveedor NO la procesó: se libera la reserva y el paso vuelve a la cola.'))return;
  try{await api('POST','/v1/attempts/'+encodeURIComponent(b.dataset.rec)+'/reconcile',{consumed,evidence:'panel'})}catch(e){alert(e.message)}load()});
 $('waiting').innerHTML='<tr><th>Trabajo</th><th>Paso</th><th>Cliente</th><th>Espera</th><th>Motivo</th><th>Revisión</th><th>Plazo</th></tr>'+d.waiting.map(w=>'<tr><td>'+esc(w.job_id)+(w.urgent?' <span class="tag">urgente</span>':'')+'</td><td>'+esc(w.step_id)+'</td><td>'+esc(w.client_id)+'</td><td>'+ago(now,w.ready_since)+'</td><td>'+why(w.last_reason)+'</td><td>'+hhmm(w.next_reconsider)+'</td><td>'+hhmm(w.deadline)+'</td></tr>').join('');
 $('decisions').innerHTML='<tr><th>Hora</th><th>Trabajo / paso</th><th>Decisión</th><th>Motivo</th><th>Detalle</th></tr>'+d.decisions.map(x=>'<tr><td>'+hhmm(x.t)+'</td><td>'+esc(x.job_id)+' / '+esc(x.step_id)+'#'+esc(x.attempt)+'</td><td><span class="tag '+esc(x.action)+'">'+({authorize:'autoriza',defer:'aplaza',close:'cierra'}[x.action]||esc(x.action))+'</span></td><td>'+why(x.reason)+'</td><td class="sub">'+esc(x.detail)+(x.next_t?' · revisar '+hhmm(x.next_t):'')+'</td></tr>').join('');}
$('lf').onsubmit=e=>{e.preventDefault();token=$('tok').value.trim();sessionStorage.setItem('rubato-admin',token);load()};
$('logout').onclick=()=>{sessionStorage.removeItem('rubato-admin');token='';show(false)};$('refresh').onclick=load;
$('pf').onsubmit=async e=>{e.preventDefault();const body={name:$('pname').value};try{if($('preserve').value.trim())body.reserve=JSON.parse($('preserve').value);
 if($('prelease').value.trim())body.release_offset=parseInt($('prelease').value,10);await api('PUT','/v1/admin/policy',body);$('pmsg').innerHTML='<span class="ok">aplicada</span>'}catch(err){$('pmsg').innerHTML='<span class="err">'+esc(err.message)+'</span>'}load()};
function tick(){clearInterval(timer);if($('auto').checked)timer=setInterval(()=>{if(!document.hidden&&token)load()},5000)}
$('auto').onchange=tick;tick();if(token)load();else show(false);
</script></body></html>
"""
