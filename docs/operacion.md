# Operación (F7)

## Señales

- Registros JSON en la salida estándar (`msg`, `corr`, `method`, `path`, `status`, `ms`).
  El cliente puede enviar `X-Correlation-Id`; el servicio lo devuelve y lo registra.
- `GET /healthz` (proceso vivo) y `GET /readyz` (base de datos y versión del esquema).
- `GET /metrics` (texto Prometheus), sin etiquetas por trabajo:
  `rubato_steps{client,state}`, `rubato_queue_oldest_ready_age_seconds{client}`,
  `rubato_attempts{state}` (incluye `unknown` = conciliaciones pendientes),
  `rubato_grant_to_start_seconds_avg`, `rubato_decisions{action,reason}`,
  `rubato_ledger_amount{resource,state}` (reservado / confirmado / incierto),
  `rubato_http_requests{route,method,status}` y latencias por ruta.

## Diagnóstico rápido

```powershell
curl.exe -s http://127.0.0.1:8000/readyz
curl.exe -s http://127.0.0.1:8000/metrics | Select-String 'rubato_attempts|rubato_ledger|unknown'
curl.exe -s -H "Authorization: Bearer <admin>" "http://127.0.0.1:8000/v1/admin/attempts?state=unknown"
curl.exe -s -H "Authorization: Bearer <admin>" http://127.0.0.1:8000/v1/usage
docker compose logs api --tail 200 | Select-String '"status": 5'
```

## Situaciones y qué hacer

| Situación | Cómo se ve | Acción | Criterio para reanudar |
|---|---|---|---|
| Intento incierto (el cliente cayó tras `start`) | `rubato_attempts{state="unknown"}` > 0; `usage.unknown` > 0 | Buscar el recibo del proveedor con la clave del intento (radar: fila de `calls`; proveedor simulado: `GET /v1/receipts/{attempt}`). Con recibo: `reconcile {"consumed": true, "usage": …, "receipt": …}`. Sin recibo y sin llamada registrada: `reconcile {"consumed": false, "evidence": "…"}` | `unknown` = 0 y saldo coherente con los recibos |
| Proveedor en pausa (429) | aplazamientos `provider_pause`; fila en `pauses` | Esperar a `until`; si el proveedor indica cuota diaria, bajar la capacidad del recurso hasta el reinicio | `pauses.until` vencido y `claims` devuelve permisos |
| Trabajo bloqueado | `rubato_queue_oldest_ready_age_seconds` crece; `GET /v1/decisions?job=…` repite el mismo motivo | Leer `last_reason` y `next_reconsider`: `never_fits` → subir límite o máximo del paso; `deadline_reserve` → revisar la reserva o la liberación; `client_unavailable` → el cliente no declara ejecutor | El motivo cambia o el paso recibe permiso |
| Reserva sin liquidar | `rubato_ledger_amount{state="reserved"}` estable con permisos viejos | Los permisos `granted` caducan solos (`RUBATO_GRANT_TTL`); los `started` pasan a `unknown` (`RUBATO_ATTEMPT_TIMEOUT`) y se concilian | sin `reserved` más antiguos que el TTL |
| Desviación contable o trabajo perdido | saldo ≠ recibos del proveedor | Detener admisiones (`PUT /v1/admin/resources` con capacidad 0), conciliar, poner los clientes en `off` | saldo = recibos; volver a `shadow` antes de `enforce` |

## Copia y restauración

`python scripts/ops/restore_drill.py` hace `pg_dump -Fc` de la base de Compose,
restaura en una instancia PostgreSQL desechable, levanta una API nueva sobre la copia
y comprueba: decisiones conservadas, intento incierto visible, saldo idéntico, trabajo
terminado intacto, decisiones que se reanudan y conciliación posible. Las migraciones
se aplican al arrancar con un candado consultivo (varias réplicas a la vez son seguras).

## Réplicas

`docker compose --profile ha up -d` añade una segunda réplica de la API (`:8001`).
`python scripts/ops/ha_drill.py` ejecuta dos consumidores, mata una réplica durante una
reserva y la otra durante una llamada simulada, y mide reatención, permisos duplicados,
descuadre de saldo y latencia. En Kubernetes: `scripts/ops/k8s_up.ps1` y el mismo
script con `--kill "kubectl:rubato/app=api#0,…"` (ver `deploy/`).
