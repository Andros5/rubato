# Estado de Rubato

| Fase | Estado |
|---|---|
| F0 · Repositorio y caso mínimo | Completa (CI Linux pendiente de evidencia) |
| F1 · Trazas | Completa (jornadas prospectivas pendientes de evidencia) |
| F2 · Simulador | Pendiente |
| F3 · Políticas | Pendiente |
| F4 · Evaluación y demo | Pendiente |
| F5 · Servicio | Pendiente |
| F6 · Integraciones | Pendiente |
| F7 · Operación y Kubernetes | Pendiente |
| F8 · Distribución | Pendiente |

**Siguiente tarea.** F2: casos manuales del simulador (llegadas simultáneas, medianoche, tokens, cancelación, dependencia, doble límite).

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
