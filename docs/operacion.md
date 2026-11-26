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
El proveedor simulado no forma parte de la pila principal: vive en el perfil `demo`, que
solo activan los proyectos aparte de entrevista y de ensayo.
`python scripts/ops/ha_drill.py` ejecuta el ensayo desde un proyecto Compose desechable.
Consulta el [laboratorio](laboratorio.md) para abrirlo desde el mando local.
El ensayo registra permisos concurrentes, recibos, saldo y tiempo hasta que la réplica
superviviente vuelve a atender trabajo. Para levantar el clúster de desarrollo sigue
`scripts/ops/k8s_up.ps1`.

## Recursos

El stack está pensado para quedarse encendido en un PC con poca memoria. Medido con
`docker stats` tras la batería `pytest -m postgres` (Linux, 4 CPU):

| | antes (1.1.1) | `compose.yaml` | con `compose.lean.yaml` |
|---|---|---|---|
| RAM de los contenedores | ~250 MiB | ~120 MiB | ~92 MiB |
| CPU en reposo (s de CPU por minuto) | 5,3 | 0,5 | 0,5 |
| Imagen `rubato` | 227 MB | 132 MB | 132 MB |
| Arranque hasta `healthy` | 7,7 s | 4,2 s | 4,2 s |
| Peticiones/s con 10 clientes (`/v1/claims` + `/v1/usage`) | 203 | 304 | |

Qué lo consigue y cómo deshacerlo si hace falta:

- **Un worker de uvicorn** (`RUBATO_API_WORKERS`, por defecto 1). Los endpoints son
  síncronos y ya se sirven desde un pool de hilos; cada worker extra cuesta ~60 MB.
- **Healthchecks cada 30 s** con `start_interval: 1s` durante el arranque (antes cada
  2-3 s, y cada sonda arrancaba un intérprete de Python: ~90 % de la CPU en reposo).
- **Bytecode precompilado** en la imagen: la imagen oficial trae la biblioteca estándar
  sin `.pyc` y el usuario sin privilegios no puede escribirlos, así que cada proceso la
  recompilaba al arrancar.
- **Imagen Alpine multi-etapa** sin pip ni caché de compilación.
- **PostgreSQL pequeño**: `shared_buffers=32MB`, `max_connections=50`, un solo
  autovacuum, sin JIT ni consultas paralelas (la base ocupa unos MB).
- **Proveedor simulado en el perfil `demo`**: solo lo usan las pruebas y las demos.
- **Registros rotados** (`local`, 3 × 5 MB por servicio) y sin el registro de acceso
  duplicado de uvicorn: la API ya registra cada petición en JSON.
- **Límites de memoria** (`mem_limit`) como red de seguridad, con margen sobre el pico
  medido; si un contenedor los supera, Docker lo reinicia.

`compose.lean.yaml` ejecuta además el planificador como un hilo dentro de la API
(`RUBATO_EMBEDDED_SCHEDULER=1`): un contenedor menos y unos 25 MB menos. Para que
`docker compose up -d` (y el arranque automático de Windows) lo use, añade a `.env`:

```text
COMPOSE_FILE=compose.yaml;compose.lean.yaml
```

Al cambiar de configuración, `docker compose up -d --remove-orphans` no borra los
contenedores de servicios que quedan fuera de los perfiles activos; elimínalos con
`docker compose rm -sf provider scheduler`.
