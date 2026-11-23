# Estado de Rubato

| Fase | Estado |
|---|---|
| F0 · Repositorio y caso mínimo | Completa (CI Linux pendiente de evidencia) |
| F1 · Trazas | Completa (jornadas prospectivas pendientes de evidencia) |
| F2 · Simulador | Completa |
| F3 · Políticas | Completa |
| F4 · Evaluación y demo | Pendiente |
| F5 · Servicio | Pendiente |
| F6 · Integraciones | Pendiente |
| F7 · Operación y Kubernetes | Pendiente |
| F8 · Distribución | Pendiente |

**Siguiente tarea.** F4: escenarios versionados, CLI `simulate`/`compare`, informe HTML y evaluación por bloques de fechas.

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
