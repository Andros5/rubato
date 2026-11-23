# Referencia de CLI y API

## CLI (`rubato`)

| Comando | Uso |
|---|---|
| `init-demo [DIR]` | Copia el ejemplo sintético (`demo.jsonl`, `scenarios.json`, `policies.json`) a `DIR/examples`. |
| `synth --seed N --out F [--urgent K]` | Genera una traza sintética `rubato.trace/v1`. |
| `simulate --trace F --scenario S --policies a,b --out DIR [--scenarios F] [--policies-file F] [--no-grid]` | Simula el escenario con cada política (y la rejilla de reservas precalculadas). Escribe `manifest.json` y por política `summary.json`, `decisions.jsonl`, `jobs.jsonl`, `attempts.jsonl`, `usage.jsonl`. |
| `compare --run DIR --report F.html` | Informe HTML autocontenido, sin red. |
| `explain --run DIR --job ID [--policy P]` | Todas las decisiones sobre un trabajo. |
| `evaluate --trace F --profile F [--policies-file F] [--pilot F] [--deadline-minutes N] [--out DIR]` | Bloques diseño/ajuste/prueba con veredicto. |
| `import-radar --radar-dir D --from YYYY-MM-DD --to YYYY-MM-DD [--out private]` | Importación en solo lectura del historial del radar (privado). |
| `shadow-report FILE [--json]` | Coincidencias, divergencias por motivo, latencia añadida y si hay evidencia suficiente para `enforce`. |
| `demo-service [--api URL --provider URL --*-token T]` | Dos clientes comparten cuota en un servicio en marcha. |
| `--version` | Versión. |

Políticas: `fifo` (orden de llegada), `radar` (prioridad y elegibilidad, referencia),
`reserva` (`reserve` por recurso o patrón `rpd:*`, `release_at` / `release_offset`,
`aging_limit`, `impossible` = `run` | `background` | `drop`).

## API HTTP (token `Authorization: Bearer …`)

| Método y ruta | Efecto |
|---|---|
| `POST /v1/jobs` | Registra `{id, class, urgent, priority, deadline?, meta?, steps:[{id, model, estimate, maximum?, depends_on?, duration_estimate?, max_attempts?}]}`. Idempotente por `Idempotency-Key` (por defecto `job:<id>`); misma clave con otro cuerpo → 409. Avisa si un tope estricto queda `orientative`. |
| `GET /v1/jobs/{id}` | Estado del trabajo, pasos e intentos (solo los propios). |
| `POST /v1/jobs/{id}/cancel` | Cierra pasos futuros; libera permisos no iniciados. |
| `POST /v1/claims` | `{slots?, job?, step?, shadow?}` → `{permit:{attempt, job, step, model, admitted, start_before}}` o `{permit:null, retry_after, next_reconsider, step:{state,last_reason}}`. Con `shadow:true` devuelve la decisión que tomaría sin reservar. |
| `POST /v1/attempts/{id}/start` | Obligatorio antes de llamar al proveedor. `409 permit_expired` si caducó. Idempotente. |
| `POST /v1/attempts/{id}/finish` | `{status: ok|invalid|rate_limited|error, usage, receipt?, retry_after?}`. Repetir con el mismo cuerpo devuelve lo mismo; con otro, 409. |
| `POST /v1/attempts/{id}/reconcile` | `{consumed, usage?, receipt?, evidence?}` para intentos `unknown`. |
| `POST /v1/observations` | Modo `shadow`: `{job, step, status, usage}` reserva el uso real de una llamada ya hecha. |
| `GET /v1/decisions?job=&limit=` | Decisiones accesibles al cliente. |
| `GET /v1/usage` | Saldo por recurso (usado, disponible, incierto) y pasos pendientes. |
| `PUT /v1/admin/resources` | (admin) Límites compartidos; versión en `config_versions`. |
| `PUT /v1/admin/policy` | (admin) Política activa; por defecto `radar`. |
| `POST /v1/admin/clients` | (admin) Alta de cliente y token. |
| `GET /v1/admin/attempts?state=` | (admin) Intentos por estado, p. ej. `unknown`. |
| `POST /v1/admin/schedule` | (admin) Fuerza una ronda. |
| `POST /v1/admin/clock`, `POST /v1/admin/reset` | Solo con `RUBATO_TEST_CLOCK=1` (entornos de prueba). |
| `GET /v1/mode` | Modo que el administrador fijó para el cliente que llama (`off`, `shadow`, `enforce`). |
| `PUT /v1/admin/clients/{id}/mode` | (admin) `{mode}`; queda en `events`. |
| `GET /v1/admin/overview` | (admin) Todo lo que muestra el panel en una lectura. |
| `GET /panel` | Panel en vivo (página autocontenida; pide el token de administración). |
| `GET /healthz`, `GET /readyz`, `GET /metrics` | Operación. |

Variables: `RUBATO_DSN`, `RUBATO_CLIENTS` (`id:token:rol:ranuras,…`),
`RUBATO_GRANT_TTL` (60), `RUBATO_ATTEMPT_TIMEOUT` (600), `RUBATO_EXECUTOR_TTL` (120),
`RUBATO_LEDGER_LOOKBACK` (3 días), `RUBATO_MIGRATE` (1), `RUBATO_POOL` (10),
`RUBATO_SCHEDULER_INTERVAL` (2 s).
