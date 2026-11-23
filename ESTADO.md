# Estado de Rubato

| Fase | Estado |
|---|---|
| F0 · Repositorio y caso mínimo | Completa (CI Linux pendiente de evidencia) |
| F1 · Trazas | Completa (jornadas prospectivas pendientes de evidencia) |
| F2 · Simulador | Completa |
| F3 · Políticas | Completa |
| F4 · Evaluación y demo | Completa (umbrales del piloto pendientes de acuerdo; jornadas prospectivas pendientes) |
| F5 · Servicio | Completa |
| F6 · Integraciones | En curso: adaptadores y demo completos; jornadas `shadow` y activación real pendientes de evidencia |
| F7 · Operación y Kubernetes | Completa (Compose y clúster local kind) |
| F8 · Distribución | v1.0.0 local preparada; piloto externo pendiente de evidencia |

**Siguiente tarea.** Criterios abiertos, por orden: (1) F6: `RADAR_RUBATO_MODE=shadow` en el radar durante varias jornadas con el servicio levantado y revisar `data/integrations/rubato-shadow.jsonl`; (2) F4/F1: con esas jornadas, ampliar el bloque de prueba con llegadas `observed`; (3) F8: piloto externo (`docs/piloto.md`); (4) confirmar los umbrales de `config/piloto.json`; (5) CI en Linux al crear el remoto.

**Pendiente de evidencia (no sustituible por datos sintéticos):** jornadas `shadow` reales, activación `enforce` progresiva, jornada real con ambos clientes compitiendo, CI Linux, piloto externo, acuerdo de umbrales.

## Evidencia por fase

### F0 · Repositorio, vocabulario y primer caso
- Estado: completa (la ejecución en CI Linux queda pendiente de evidencia: el repositorio no tiene remoto; el flujo está en `.github/workflows/ci.yml`).
- Fecha: 2026-11-23. Commit `d4dbd28`.
- Cambio entregado: `pyproject.toml`, paquete `src/rubato` (`model`, `resources`, `core.decidir`, `policies`, `sim`, `scenarios`), `tests/test_f0.py`, `.gitignore` con entornos, bases, volcados y credenciales, `docs/decisiones.md` (D1 «terminado», D2 ventana de imputación, D3 orden de eventos, D4 horizonte, D5 reserva, D6 motivo vinculante), ruff como comprobación de estilo.
- Pruebas ejecutadas, en un clon limpio (`git clone` a un directorio temporal, sin `.venv` ni claves):
  - `py -3.13 -m venv .venv` → OK
  - `.\.venv\Scripts\python.exe -m pip install -q -e '.[dev]'` → OK
  - `.\.venv\Scripts\python.exe -m pytest -q` → `5 passed in 0.13s`
  - `ruff check src tests` → `All checks passed!`
- Evidencia reproducible: `tests/test_f0.py`, escenario `rubato.scenarios.f0_*`:
  - Orden de llegada: 3 análisis a tiempo, 0 urgencias (la candidatura queda `pending`, motivo `capacity_exhausted`).
  - Reserva de 2 peticiones (liberación en el turno 4): 1 análisis a tiempo + 1 urgencia a tiempo (fin en t=5). El primer aplazamiento de `analisis-2` es `deadline_reserve` con reconsideración en t=3 y el detalle «2 held for urgent work».
  - Día gemelo sin urgencia: liberar en el turno 4 termina `analisis-2` en t=4 y `analisis-3` en t=5, ambos fuera de plazo.
  - HTTP 200 con CV inválido: consume la petición, la candidatura queda `failed`, la carta se cierra con `dependency_failed` y la capacidad liberada pasa a `analisis-2`.
  - Sin variables de credenciales el resultado es idéntico.
- Supuesto o limitación: unidades de tiempo abstractas (turnos) en el caso mínimo; los pasos de la candidatura son secuenciales (la carta depende del CV).
- Próxima tarea concreta: F1.

### F1 · Trazas, procedencia e importación privada
- Estado: completa. Pendiente de evidencia: jornadas prospectivas con llegadas `observed` (el registrador está listo; se conecta al radar en F6 en modo `shadow`).
- Fecha: 2026-11-23.
- Cambio entregado: `trace.py` (esquema `rubato.trace/v1`, cargador con errores por línea y campo, escritor canónico, cobertura), `importers/radar.py` (solo lectura `mode=ro`, campos permitidos, IDs HMAC opacos, episodios y reintentos, reconciliación), `capture.py` (`rubato.capture/v1`, a prueba de fallos, sin texto libre, `to_trace` con llegadas observadas), `synth.py` (generador con semilla anclada a trabajo/paso/intento), CLI `rubato import-radar` y `rubato synth`, `docs/trazas.md`.
- Pruebas ejecutadas:
  - `.\.venv\Scripts\python.exe -m pytest -q` → `23 passed, 1 skipped` (la omitida es la prueba privada).
  - `RUBATO_RADAR_DIR=..\swiss-job-radar\data pytest -q -m private` → `1 passed`: control 3.185 llamadas, 2.880 `match`, 3.085 duraciones positivas, 3.157 tokens de entrada positivos (coincide con el plan).
  - `rubato import-radar --radar-dir ..\swiss-job-radar\data --from 2026-11-09 --to 2026-11-21 --out private` dos veces → mismos bytes (`cmp` idéntico), huella `1dc1507a806621a5`.
- Evidencia reproducible (privada, fuera de Git): `private/radar-2026-11-09-2026-11-21.report.json`. Resumen: leídas 3.185 = incluidas 3.031 + descartadas 154 (`no_ref` 104, `test_ref` 9, `empty_ref` 5, `unsupported_ref:job/ask` 34, `unsupported_ref:profile/profile` 2). 2.299 trabajos: 2.186 `match`, 16 `match_batch`, 32 `application`, 65 `application_regen`. Estados normalizados: ok 2.322, invalid 355, error 348, rate_limited 6. Cobertura: llegada `derived` 2.299/2.299, plazo `missing` 2.299/2.299, duración ausente 4, tokens de salida a cero 354 (cero observado, contado aparte).
- Pruebas de comportamiento: reimportación idéntica, reconciliación por motivo, rechazo de esquema incompatible, cero frente a ausente, trabajo imposible de formar (sin referencia), base de origen intacta (mismo `mtime` y bytes), ausencia de texto libre y referencias en la salida, generador determinista y anclado, captura que no rompe al anfitrión y rechaza campos no permitidos.
- Supuesto o limitación: la llegada histórica es una cota superior (`calls.created` al reservar); no hay plazos históricos; los episodios son una heurística documentada en `docs/trazas.md`. Las preguntas `ask` con referencia `job:` se descartan con motivo propio.
- Próxima tarea concreta: F2.

### F2 · Simulador determinista
- Estado: completa.
- Fecha: 2026-11-23.
- Cambio entregado: cola de eventos con orden documentado (D3), ventanas `rolling`/`calendar` (zona explícita, días de 23/25 h)/`concurrency`/`total`, peticiones, tokens y dinero en enteros (`tariffs.py`, `cost_micro`, tarifa versionada), dependencias, resultados inválidos, errores, 429 con pausa y `Retry-After` (D8), cancelación (D9), caída y vuelta del cliente, consumo real distinto de la estimación con liquidación en la ventana de admisión (D2), horizonte (D4), `metrics.py` con objetivos separados. La política solo recibe estimaciones; el simulador revela el intento al terminar.
- Pruebas ejecutadas: `.\.venv\Scripts\python.exe -m pytest -q` → `36 passed, 1 skipped`; `ruff check` limpio.
- Evidencia reproducible: `tests/test_f2_sim.py` — llegadas simultáneas (orden de traza, mismo resultado al repetir), medianoche en Europe/Zurich (aplazamiento `capacity_exhausted` con reconsideración exacta a las 00:00 locales; las llamadas que cruzan la medianoche cuentan en el día anterior), día de cambio de hora de 23 h, tokens insuficientes (`rate_limited`, disponible 2000, reconsideración t=60) y paso que nunca cabe (`never_fits` con acción sugerida), cancelación (paso en curso consume, paso futuro `cancelled`), dependencia fallida tras `max_attempts`, doble límite (rpm libera en 60, tpm en 90; motivo vinculante tpm y arranque en 90), sin bucle cuando nada es elegible, reinicio de cliente, 429 con pausa hasta t=31, consumo real superior/inferior a la estimación, presupuesto monetario entero, misma semilla → misma secuencia de decisiones y mismas métricas.
- Supuesto o limitación: el aplazamiento por pausa de cliente no tiene hora de reconsideración (depende de un evento externo). Una pausa por 429 se aplica al modelo completo.
- Próxima tarea concreta: F3.

### F3 · Políticas y explicación
- Estado: completa.
- Fecha: 2026-11-23.
- Cambio entregado: `RadarPolicy` (prioridad desc., elegibilidad) con diferencias frente a `Workspace.claim` en `docs/referencia-radar.md`; `ReservePolicy` con reserva por recurso, liberación absoluta o por desfase en la ventana, antigüedad (`aging_limit`), política de plazos imposibles (`run`/`background`/`drop`) y protección de la demanda urgente conocida; relleno (`backfill`) con el trabajo adelantado en el detalle; `optimize.py` (enumeración exhaustiva y CP-SAT, extra `rubato[opt]`, OR-Tools 9.15); `explain.py` (`explain_job`, `what_if` que vuelve a simular, `winners` por objetivo sin media ponderada).
- Pruebas ejecutadas: `.\.venv\Scripts\python.exe -m pytest -q` → `69 passed, 1 skipped`; `ruff check` limpio.
- Evidencia reproducible (`tests/test_f3_policies.py`):
  - Día en que reservar mejora: urgencias a tiempo 1 (reserva) frente a 0 (llegada).
  - Día en que perjudica: sin urgencia, fondo a tiempo 3 (llegada) frente a 1 (reserva); `winners` declara ganadora a `fifo@1` en fondo y muestra el empate en urgencias; `what_if` lista `analisis-2` y `analisis-3` como perjudicados.
  - Día en que da igual: cuota 10, mismos resultados.
  - Óptimo con información perfecta: F0 con urgencia (1 urgente, 1 fondo, 2 terminados) y sin urgencia (0, 3, 3), igual a la resolución a mano; ninguna política en línea lo supera. CP-SAT = enumeración en 25 instancias aleatorias pequeñas (semillas 0–24, con ventanas móviles, cuota total, concurrencia, dependencias e intentos inválidos).
  - Antigüedad: con `aging_limit=30` los tres trabajos de fondo terminan (0, 30, 30); sin ella, uno.
  - Plazo imposible: `drop` cierra con `deadline_impossible` y el instante más temprano de fin (10 > 5); `background` lo ejecuta tarde.
  - Relleno: `small` adelanta a `big` con motivo `backfill`; la explicación de `big` dice `DEFERRED [rate_limited] ... reconsider t=60`.
- Supuesto o limitación: el optimizador trabaja en tiempo discreto, toma el primer intento de la traza y no admite ventanas de calendario con zona horaria; es una cota bajo las hipótesis del escenario, no una política.
- Próxima tarea concreta: F4.

### F4 · Evaluación y primera demo utilizable
- Estado: completa. Pendiente de evidencia: acuerdo de Andrés sobre los umbrales del piloto (`config/piloto.json`, marcados como propuesta) y jornadas prospectivas observadas para ampliar el bloque de prueba.
- Fecha: 2026-11-23.
- Cambio entregado: `scenario.py` (escenarios versionados `rubato.scenarios/v1` con urgencia inyectada `hypothetical`, pico, cuota reducida, factor de consumo, consumo ajeno, día sin urgencias), `runs.py` (manifiesto con huella de traza, commit, supuestos y configuración congelada), `report.py` (HTML autocontenido: dos líneas de tiempo, filtros por cliente/clase/recurso, ficha de decisión, resumen por objetivo con ganador visible, cuota disponible y pendientes, tabla de trabajos, modo oscuro), `evaluate.py` (bloques diseño/ajuste/prueba, regla de selección fija, veredicto con umbrales del piloto, resultados por día con denominadores), CLI `simulate`, `compare`, `explain`, `evaluate`; `examples/demo.jsonl` (semilla 7), `examples/scenarios.json`, `examples/policies.json` y `examples/policies-radar.json` (congeladas 2026-11-23), perfil `examples/profiles/radar-declared-2026-11-09.json`, resultados versionados en `examples/resultados/urgencia-tardia/`, `docs/evaluacion.md`, `docs/escenarios-sinteticos.txt`.
- Pruebas ejecutadas: `.\.venv\Scripts\python.exe -m pytest -q` → `74 passed, 1 skipped`; `ruff check` limpio. Revisión visual del informe en el navegador integrado (líneas de tiempo, ficha de la candidatura con `rpd: needs 1, 0 available; reconsiderar 08:00:00 UTC`, gráficos de cuota y pendientes).
- Evidencia reproducible:
  - `rubato simulate --trace examples/demo.jsonl --scenario urgencia-tardia --policies radar,reserva --out runs/demo` → radar: urgentes 0/1, fondo 111/120; reserva: urgentes 1/1, fondo 109/120.
  - `rubato compare --run runs/demo --report informe.html` → HTML sin red (la prueba comprueba que no hay URL, `<script src>` ni `<link href>`).
  - Siete escenarios sintéticos en `docs/evaluacion.md`: la reserva gana la urgencia en 5, da igual en `urgencia-temprana` y en `sin-urgencia` solo empeora la espera p95 del fondo (109 → 165 s).
  - Historial privado (agregados): prueba 19–21 nov, radar 15/15 urgentes y 54/56 fondo; `reserva-rpd2` idéntico → **veredicto: política por defecto `radar`** (empate). Diseño 69/82 urgentes con ambas políticas.
- Supuesto o limitación: reordenar llamadas históricas no muestra qué habría respondido el proveedor; plazos urgentes hipotéticos (60 min); el bloque de ajuste no contiene urgencias; el perfil de límites es el declarado por el radar, no observado; 2 trabajadores concurrentes supuestos.
- Próxima tarea concreta: F5.

### F5 · Servicio de admisión con contabilidad duradera
- Estado: completa.
- Fecha: 2026-11-23.
- Cambio entregado: `src/rubato/service/` — migración `0001_init.sql` (clientes, configuración versionada, recursos, trabajos, pasos, intentos, libro, pausas, decisiones, eventos, idempotencia; índices de pasos listos, reconsideración, permisos por caducar y un intento vivo por paso), `store.py` (ronda de admisión con `core.decidir`, todas las dimensiones o ninguna, caducidad de permisos, intentos `unknown`, conciliación, 429, cancelación, observaciones `shadow`), `app.py` (FastAPI, autenticación por token, idempotencia, ID de correlación, registros JSON), `scheduler.py`, `provider_sim.py` (recibos persistentes), `sdk.py` (`RubatoClient` y `Gate` con modos `off`/`shadow`/`enforce`), `Dockerfile`, `compose.yaml`, `docs/servicio.md` (flujo y D10).
- Pruebas ejecutadas (PostgreSQL 17 real en Docker 29.1.2, 2 workers de API + planificador):
  - `docker compose down -v; docker compose up -d --build` → entorno limpio, `/readyz` = `{"ok":true,"schema":"0001_init.sql"}`.
  - `RUBATO_TEST_API=http://127.0.0.1:8000 RUBATO_TEST_COMPOSE=1 .\.venv\Scripts\python.exe -m pytest -q -m postgres` → `10 passed in 18.09s`.
  - Las pruebas de concurrencia y caídas, repetidas 5 veces seguidas → `4 passed` cada vez.
  - Suite local sin servicio → `74 passed, 11 skipped`.
- Evidencia reproducible (`tests/test_f5_service.py`): con capacidad 1, 20 `claims` concurrentes de dos clientes → exactamente 1 permiso y saldo 1/0; alta, inicio y fin repetidos → misma respuesta y consumo 1 (parámetros distintos con la misma clave → 409); caída antes del inicio → permiso caducado a los 61 s, reasignado, `start` del viejo → `409 permit_expired`; caída después del inicio → `unknown` a los 601 s, saldo usado 1 con 1 incierto, sin reintento a ciegas, conciliación con el recibo del proveedor simulado → `done`; sin recibo → liberado y reasignado; aislamiento entre clientes y límites solo para el administrador; 429 → `provider_pause` hasta `retry_after`; tope de tokens sin máximo → `orientative`; la política `reserva` funciona en el servicio con el mismo núcleo; `docker compose restart api scheduler` conserva trabajo, intento iniciado y auditoría.
- Supuesto o limitación: todas las transacciones mutantes se serializan (D10); el reloj de prueba (`RUBATO_TEST_CLOCK=1`) está activo en el Compose local para poder ensayar caducidades.
- Próxima tarea concreta: F6.

### F6 · Integraciones con Swiss Job Radar y CitaLab
- Estado: en curso. Completo lo que no depende del tiempo ni de Andrés; **pendiente de evidencia**: `shadow` durante varias jornadas con tráfico real, activación progresiva en `enforce` y una jornada real con ambos clientes compitiendo.
- Fecha: 2026-11-23.
- Cambio entregado: rama `rubato-adapter` en swiss-job-radar (`d6d9c2e`, `30a0803`) y en citalab (`8ecfc9b`), sin fusionar; en Rubato: modo `shadow` del servicio (`claims` con `shadow`, `/v1/observations`), `claims` filtradas por paso sin conceder otros pasos del cliente, ids de trabajo con `:` y `/`, `demo_service.py` (`rubato demo-service`), `tests/test_f6_two_clients.py`, `scripts/integracion/`, `docs/integraciones.md` con guía de reversión.
- Pruebas ejecutadas:
  - swiss-job-radar, rama `rubato-adapter`: `.venv\Scripts\python.exe -m pytest -q` → `518 passed, 3 skipped` (7 pruebas nuevas del adaptador).
  - citalab, rama `rubato-adapter`: `pytest -q` → `105 passed`; `python scripts/reproduce.py` → «todo coincide con lo publicado».
  - Rubato: `pytest -q` → `74 passed, 14 skipped`; contra Compose `pytest -q -m postgres` → `13 passed` (incluida la demo de dos clientes, ids con `:`/`/` y petición filtrada).
  - Humo de los adaptadores reales contra el servicio de Compose (`scripts/integracion/*.py`): radar `shadow` registra la divergencia `defer/deadline_reserve` sin bloquear; radar `enforce` aplaza el segundo análisis y deja pasar el CV urgente sin reservas locales huérfanas; CitaLab `enforce` pausa la cola con `rubato-deferred (deadline_reserve)`.
- Evidencia reproducible: `docs/integraciones.md`. Demo sintética (cuota 24): `reserva` → candidatura urgente terminada, fondo 22/24, 1 episodio de CitaLab pendiente; `radar` → fondo 24/24, candidatura en `capacity_exhausted`; `off` → 3 llamadas por la vía habitual y 0 decisiones nuevas en Rubato.
- Supuesto o limitación: la competencia entre clientes se demuestra con carga sintética y proveedor simulado; las llamadas Codex del radar no están coordinadas; en CitaLab cada intento de inferencia es un trabajo de un paso.
- Próxima tarea concreta: F7. En paralelo, cuando Andrés lo apruebe: `RADAR_RUBATO_MODE=shadow` en su radar con el servicio de Compose levantado.

### F7 · Operación, concurrencia y Kubernetes
- Estado: en curso. Compose: completo. **Pendiente de evidencia**: la misma suite y el ensayo en un clúster local; `kind` no está instalado, `kubectl` no tiene contexto y el Kubernetes de Docker Desktop está desactivado. Instalar `kind` implica descargar un binario: queda a decisión de Andrés.
- Fecha: 2026-11-23.
- Cambio entregado: métricas Prometheus sin etiquetas por trabajo (`/metrics`), registros JSON con `X-Correlation-Id`, `/healthz` y `/readyz` con versión de esquema, migración `0002_meta_and_audit_idx.sql` (metadatos de planificación e índices de auditoría), réplica `api2` (perfil `ha`), `scripts/ops/ha_drill.py`, `scripts/ops/restore_drill.py`, `docs/operacion.md` (runbook: intento incierto, proveedor en pausa, trabajo bloqueado, reserva sin liquidar, desviación contable; diagnóstico y criterios para reanudar), manifiestos `deploy/k8s/` (namespace, PostgreSQL con volumen persistente, API 2 réplicas, planificador, proveedor simulado, ConfigMap, Secret generado desde `secrets.env` ignorado por Git, límites de recursos, sondas, usuario no root, sistema de ficheros de solo lectura), `deploy/kind/kind-config.yaml`, `scripts/ops/k8s_up.ps1`.
- Pruebas ejecutadas:
  - `RUBATO_TEST_API=http://127.0.0.1:8000 RUBATO_TEST_COMPOSE=1 RUBATO_TEST_DSN=postgresql://rubato:rubato-local-only@127.0.0.1:55432/rubato pytest -q` → `91 passed, 1 skipped` (la omitida es la prueba privada del radar).
  - `tests/test_f7_ops.py`: migración desde 0001 con datos → 0002 aplicada, datos intactos, idempotente; una base actualizada sigue planificando trabajos antiguos; métricas sin IDs de trabajo; eco del ID de correlación.
  - `python scripts/ops/restore_drill.py` → todas las comprobaciones `true`: volcado 0,61 s (28 kB), restauración 0,43 s en un PostgreSQL desechable; decisiones, saldo e intento `unknown` idénticos; trabajo terminado intacto; la API nueva concede un permiso y concilia el intento incierto.
  - `RUBATO_GRANT_TTL=5 docker compose --profile ha up -d; python scripts/ops/ha_drill.py` → 2 réplicas, 2 consumidores, 80 trabajos; réplica B muerta durante una reserva (t=18,7 s) y réplica A durante una llamada (t=37,0 s); el paso interrumpido vuelve a atenderse a los 6,2 s (TTL 5 s); 0 permisos duplicados; 80 peticiones confirmadas = 80 recibos del proveedor (descuadre 0); 0 intentos inciertos; latencia de `claims` vista por el cliente p50 276 ms / p95 353 ms (conexión nueva por petición, conmutaciones y reenvío de puertos de Docker Desktop, ~44 ms por petición incluso en `/healthz`); en el servidor `claims` tarda ~8,5 ms según los registros.
  - `kubectl kustomize deploy/k8s` → 11 objetos válidos.
- Entorno: Windows 11 Pro 10.0.26300, Docker Desktop 29.1.2 (8 CPU, 3,7 GiB), PostgreSQL 17-alpine, Python 3.13.1.
- Supuesto o limitación: números medidos en un portátil con Docker Desktop, no extrapolables a producción. Todas las transacciones mutantes se serializan (D10).
- Próxima tarea concreta: con `kind` disponible, `scripts/ops/k8s_up.ps1`, después `RUBATO_TEST_API=http://127.0.0.1:18000 RUBATO_TEST_PROVIDER=http://127.0.0.1:18100 RUBATO_TEST_*_TOKEN=<de secrets.env> pytest -q -m postgres` y `ha_drill.py --a http://127.0.0.1:18000 --b http://127.0.0.1:18000 --kill "kubectl:rubato/app=api#0,kubectl:rubato/app=api#0" --provider http://127.0.0.1:18100`.

### F8 · Distribución, documentación y demo de la v1
- Estado: versión local `v1.0.0` preparada. **Pendiente de evidencia**: piloto con un mantenedor externo (protocolo en `docs/piloto.md`); no publicada (publicar es una acción separada).
- Fecha: 2026-11-23.
- Cambio entregado: versión 1.0.0 (`pyproject.toml`, `rubato --version`), datos de ejemplo empaquetados y `rubato init-demo`, `explain` con horas legibles, `demo-service` en la CLI, `README.md` en inglés, `docs/guia.md`, `docs/referencia.md` (CLI y API), ADR `docs/adr/0001-0003` (topes estrictos frente a estimaciones, política en línea frente a óptimo, recuperación de intentos inciertos; incluye cuándo la reserva rindió peor), `docs/demo.md` y `scripts/demo.ps1` (demo narrada de ~2 min), `docs/piloto.md`, `CHANGELOG.md`, `docs/release-v1.0.0.md` (probado en local / observado en los proyectos de Andrés / pendiente), `LICENSE` (MIT, «Rubato contributors»).
- Pruebas ejecutadas:
  - `python -m build` → `rubato-1.0.0-py3-none-any.whl` (SHA-256 `cfcdad30…ea30`) y `rubato-1.0.0.tar.gz` (`6978ec96…9176`); sin `private/`, `runs/`, bases ni secretos dentro.
  - Vía del simulador para un tercero: venv limpio fuera del repositorio, `pip install rubato-1.0.0-py3-none-any.whl`, `rubato init-demo .`, `simulate … urgencia-tardia`, `compare`, `explain --policy radar --job candidatura-urgente` → «17:15:00 UTC cv#1 DEFER [capacity_exhausted] rpd: needs 1, 0 available; reconsider 08:00:00 UTC».
  - Vía del servicio desde un clon limpio: `docker compose -p rubato-clean up --build -d` (21,6 s) → `/readyz` con esquema 0002; `pytest -q` con el servicio → `91 passed, 2 skipped` (privada y reinicio con control de Compose); `rubato demo-service` → candidatura terminada con `reserva`, 2 peticiones cedidas por el fondo, 1 episodio de CitaLab pendiente; con `radar` la candidatura no pasa; `off` → 3 llamadas, 0 decisiones nuevas.
  - Repositorio: `pytest -q` → `75 passed, 18 skipped` sin servicio; `ruff check` limpio.
- Evidencia reproducible: `docs/release-v1.0.0.md`, `docs/demo.md`, `scripts/demo.ps1`.
- Supuesto o limitación: imagen local `rubato:1.0.0` sin registro; la CI Linux no se ha ejecutado.
- Próxima tarea concreta: la lista «Siguiente tarea» del principio de este fichero.

### F7 · Cierre en clúster local (kind)
- Estado: completa. Fecha: 2026-11-23.
- Cambio entregado: `kind` 0.33.0 instalado con scoop (con permiso de Andrés); `scripts/ops/k8s_up.ps1` se detiene ante cualquier fallo de un comando nativo y carga la imagen como archivo de una sola plataforma (`kind load docker-image` falla con el almacén containerd de Docker: «content digest … not found»); `ha_drill.py` lee los tokens de `RUBATO_TEST_*_TOKEN`.
- Incidencia de entorno: Docker Desktop no arrancaba (sockets `dockerInference` y `userAnalyticsOtlpHttp.sock` corruptos en la carpeta `run` de Docker bajo `%LOCALAPPDATA%`, imposibles de borrar). Se renombró la carpeta a `run.stale-20261123` y Docker la recreó; no se usó «Reset to factory defaults».
- Pruebas ejecutadas:
  - `powershell -File scripts/ops/k8s_up.ps1` → clúster `kind-rubato`, PostgreSQL con volumen persistente, API 2 réplicas, planificador y proveedor simulado en `Running`; `/readyz` en `127.0.0.1:18000` con esquema 0002. Cada réplica de la API se reinició una vez al arrancar antes que PostgreSQL (la sonda la recupera).
  - Misma suite de comportamiento que en Compose: `RUBATO_TEST_API=http://127.0.0.1:18000 RUBATO_TEST_PROVIDER=http://127.0.0.1:18100 RUBATO_TEST_*_TOKEN=<secrets.env> RUBATO_TEST_DSN=<port-forward> pytest -q -m postgres` → **16 passed, 1 skipped** (la omitida controla Docker Compose).
  - `ha_drill.py --a/--b http://127.0.0.1:18000 --kill "kubectl:rubato/app=api#0,kubectl:rubato/app=api#0"` → 80 trabajos de 2 consumidores; réplica muerta durante una reserva (t=20,1 s) y otra durante una llamada simulada (t=43,1 s), ambas sustituidas por Kubernetes; trabajo interrumpido reatendido a los **5,0 s** (TTL 5 s); **0 permisos duplicados**; 80 peticiones confirmadas = 80 recibos (**descuadre 0**); 0 intentos inciertos; latencia de `claims` en el servidor p50 13,6 ms / p95 25,5 ms (registros de las réplicas supervivientes, 50 peticiones); vista por el cliente del ensayo p50 384 ms / p95 447 ms (un cliente HTTP nuevo por petición en Windows; `/healthz` con curl tarda 4 ms).
- Entorno: Windows 11 Pro 10.0.26300, Docker Desktop 29.1.2, kind 0.33.0 (un nodo), PostgreSQL 17-alpine. Números de portátil, no extrapolables.
- Próxima tarea concreta: la lista «Siguiente tarea» del principio.

### Auditoría y versión 1.0.1 (2026-11-23)
- Estado: completa. Etiqueta local `v1.0.1`; no publicada.
- Limpieza: borrados el clúster kind (nodo, volumen, red e imagen `kindest/node`), la pila de Compose y sus volúmenes de prueba, un volumen huérfano del ensayo de restauración, la caché de compilación, los secretos del clúster eliminado, `runs/` y el scratchpad antiguo con los clones de verificación (440 MB). Solo queda instalada una versión de Docker Desktop (4.100.1). No se tocaron los contenedores, imágenes ni volúmenes de otros proyectos (`tarea3`, `ubuntu-ra2025`).
- Hallazgos corregidos, cada uno con prueba en `tests/test_audit.py` o `tests/test_f5_service.py`:
  - Rendimiento: el libro consultaba ventanas recorriendo todo el historial y convirtiendo la zona horaria de cada entrada. Ahora usa entradas ordenadas con búsqueda binaria, un índice por intento y una capa provisional por ronda, y el simulador solo recorre los trabajos activos. Una semana del radar (2.133 trabajos) pasa de 17,2 s a 1,45 s (p95 por ronda de 7,5 ms a 0,54 ms) con decisiones y métricas idénticas (huellas iguales); la evaluación por bloques pasa de unos 34 s a 3,7 s con resultados idénticos. Prueba de equivalencia frente a la implementación anterior en 20 semillas aleatorias (ventanas móviles, de calendario con zona horaria, con desfase y totales).
  - Dependencias circulares entre pasos: ahora las rechazan el cargador de trazas, el simulador y el servicio (antes dejaban el trabajo esperando para siempre sin motivo).
  - Servicio: validación estricta del alta (tipos, rangos, como máximo 100 pasos) y de `slots`, con 422 en vez de error interno; los huecos de concurrencia ocupados cuentan aunque superen la ventana de consulta de 3 días; los valores de las etiquetas de métricas van escapados.
  - Informe HTML: se escapa todo texto de la traza que se inserta como HTML; comprobado en el navegador con un id malicioso (sin `<img>` inyectada y sin ejecución).
  - SDK: si falla la llamada y Rubato no puede liquidar, se propaga el error de la aplicación.
  - Compose arranca en modo normal; `reset` y el reloj solo existen con `RUBATO_TEST_CLOCK=1` (en modo normal `reset` devuelve 403).
  - `deploy/k8s/secrets.env.example` nunca entró en Git por la regla `*secret*`; ahora se versiona.
- Pruebas ejecutadas: `RUBATO_TEST_CLOCK=1 docker compose up -d --build` y después `pytest -q` con servicio, control de Compose, DSN y datos privados → **132 passed, 0 skipped**; sin servicio → `113 passed, 19 skipped`; `ruff check` y `ruff format --check` limpios; `python -m build` → `rubato-1.0.1-py3-none-any.whl` (SHA-256 `b453f44a…3e52`) y `rubato-1.0.1.tar.gz` (`26d0fd61…1333`).
- Próxima tarea concreta: la lista «Siguiente tarea» del principio.

### Versión 1.1.0: panel en vivo y modos desde el servicio (2026-11-23)
- Estado: completa. Etiqueta local `v1.1.0`; no publicada.
- Cambio entregado:
  - Panel `/panel`: cuota por recurso (fondo, urgente e incierto), cola y motivos de espera por cliente, últimas decisiones, intentos inciertos con conciliación en un clic, editor de política y avisos (inciertos, pausas, cuota por debajo del 10 %). Es autocontenido, escapa todo el texto y funciona en móvil.
  - Migración `0003_client_mode.sql`, `GET /v1/mode`, `PUT /v1/admin/clients/{id}/mode` y `GET /v1/admin/overview`.
  - Modo `auto` en el SDK y en los adaptadores: radar `65a9672`, CitaLab `0f98803`, ambos en sus ramas `rubato-adapter`.
  - `rubato shadow-report`.
  - Corrección: una petición filtrada por paso libera los permisos especulativos del planificador para otros pasos del mismo cliente (antes, con una ranura, esperaba hasta 60 s).
- Pruebas ejecutadas:
  - Pila limpia de Compose (`down -v`, `RUBATO_TEST_CLOCK=1 up --build`, esquema `0003_client_mode.sql`) y `pytest -q` con servicio, control de Compose, DSN y datos privados → **140 passed**.
  - `ruff check` y `ruff format --check` limpios.
  - Radar, rama `rubato-adapter`: 109 pruebas de las rutas afectadas en verde; contra el servicio real, tras poner `enforce` en el panel, el adaptador en modo `auto` devolvió `enforce`.
  - CitaLab, rama `rubato-adapter`: suite completa en verde y `reproduce.py` → «todo coincide con lo publicado».
  - Panel revisado en el navegador integrado con datos de demostración, en escritorio y en móvil (375 px).
- Próxima tarea concreta: activar `RADAR_RUBATO_MODE=auto` en el radar con el servicio levantado, poner `shadow` desde el panel y, tras al menos 3 jornadas, ejecutar `rubato shadow-report data/integrations/rubato-shadow.jsonl`.
- Añadido después de etiquetar: `rubato status` (probado contra una pila real: modos, cuota, esperas con próxima revisión), `scripts/ops/arranque_windows.ps1` (no registrado; decide Andrés) y comprobación de formato en la CI. `pytest -q` → 119 passed, 22 skipped sin servicio.
- Mantenimiento: el planificador borra cada hora las claves de idempotencia de más de 7 días (también al arrancar); se corrigió que sus registros nunca se imprimían. Comprobado en el contenedor (`{"msg": "maintenance", "idempotency_deleted": 0}`) y con prueba en PostgreSQL. Suite con servicio → 141 passed, 1 skipped (privada).
- Política de reserva: con `impossible` = `background` o `drop`, la demanda urgente que ya no puede cumplir su plazo deja de retener cuota (prueba `test_reserve_does_not_protect_urgent_work_that_already_missed_its_deadline`); los resultados versionados de la demo no cambian. Suite completa con servicio y datos privados → **143 passed**.
- `rubato why radar:<id>`: explica en la consola un trabajo real del servicio (pasos, intentos y decisiones con motivo); probado contra la pila. Suite con servicio → 142 passed, 2 skipped (privada y reinicio de Compose, sin sus variables en esa ejecución).
