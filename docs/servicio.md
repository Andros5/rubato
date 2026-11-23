# Servicio de admisión (F5)

`docker compose up --build` levanta PostgreSQL 17, la API (FastAPI, 2 workers), el
planificador en segundo plano y un proveedor simulado con recibos. Todo local, sin
claves reales. Tokens de ejemplo en `compose.yaml` (`radar-local-token`,
`citalab-local-token`, `admin-local-token`): son solo para el entorno local.

## Flujo

1. `POST /v1/jobs` registra trabajo, pasos, estimaciones, máximos y dependencias.
   Clave idempotente: cabecera `Idempotency-Key` (por defecto `job:<id>`).
2. `POST /v1/claims {"slots": n, "job"?, "step"?}` declara ejecutores disponibles,
   ejecuta una ronda de admisión (la misma `core.decidir` que el simulador) y devuelve
   un permiso propio o `retry_after` y el último motivo.
3. `POST /v1/attempts/{id}/start` **antes** de llamar al proveedor. Si el permiso
   caducó, responde `409 permit_expired` y el cliente no llama.
4. `POST /v1/attempts/{id}/finish {status, usage, receipt, retry_after?}` liquida.
5. `POST /v1/attempts/{id}/reconcile {consumed, usage?, receipt?}` resuelve un intento
   `unknown` con evidencia.
6. `GET /v1/jobs/{id}`, `POST /v1/jobs/{id}/cancel`, `GET /v1/decisions`, `GET /v1/usage`.
7. Modo `shadow`: `POST /v1/claims {"shadow": true, job, step}` devuelve la decisión que
   Rubato tomaría sin reservar nada; `POST /v1/observations` reserva el uso real.
8. Administración (rol `admin`): `PUT /v1/admin/resources`, `PUT /v1/admin/policy`,
   `POST /v1/admin/clients`, `GET /v1/admin/attempts?state=unknown`. Solo en modo de
   prueba (`RUBATO_TEST_CLOCK=1`): `POST /v1/admin/clock`, `POST /v1/admin/reset`.
9. Operación: `/healthz`, `/readyz` (BD y versión del esquema), `/metrics`.

## Contabilidad y concurrencia

- Cada transacción que muta estado toma primero un candado consultivo y después las
  filas de `resources` en orden de id (D10). La ronda reconstruye el libro, decide y
  reserva **todas las dimensiones o ninguna** por permiso.
- Índice único parcial: como mucho un intento vivo (`granted`, `started`, `unknown`)
  por paso. Un permiso se entrega a una sola petición de `claims` (`delivered`).
- Permiso no iniciado en `RUBATO_GRANT_TTL` (60 s) → `expired`, reserva liberada y el
  paso vuelve a `ready`. Intento iniciado sin liquidar en `RUBATO_ATTEMPT_TIMEOUT`
  (600 s) → `unknown`: la reserva queda contada (conservadora) y el paso **no** se
  reintenta hasta conciliar.
- Topes estrictos: `requests` y `concurrent` son exactos; para tokens o dinero el paso
  necesita `maximum`, si no se registra como `orientative` con aviso (D7).
- 429: `finish` con `status=rate_limited` y `retry_after` pausa el modelo (D8).
- Política por defecto: `radar@1` (veredicto de F4). El administrador puede activar
  `reserva` con `PUT /v1/admin/policy`; cada cambio queda versionado en `config_versions`.

## D10 · Serializar las transacciones de admisión

Alternativa descartada: bloquear solo los recursos afectados por cada paso. La
reserva depende de la demanda urgente de todos los clientes y la ronda evalúa todos
los pasos listos, así que el conjunto afectado es casi siempre «todos». Serializar es
más simple, evita ciclos de bloqueo entre filas de pasos e intentos y la ronda tarda
milisegundos. Si el volumen crece, la vía es particionar por grupo de recursos.
