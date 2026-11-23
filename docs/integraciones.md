# Integraciones con Swiss Job Radar y CitaLab (F6)

Cada adaptador vive en una rama aislada de su repositorio, **sin fusionar**:

| Repositorio | Rama | Commits | Enganche | Ficheros |
|---|---|---|---|---|
| swiss-job-radar | `rubato-adapter` | `d6d9c2e`, `30a0803` | `GoogleAI._reserve` (tras la reserva local) y `GoogleAI._finish` (liquidación única) | `radar/rubato_adapter.py`, 2 enganches en `radar/google_ai.py`, `tests/test_rubato_adapter.py`, `docs/rubato.md` |
| citalab | `rubato-adapter` | `8ecfc9b` | punto de entrada nuevo `python -m citalab.evaluation.rubato_runner`, que envuelve en memoria `ChatClient._post`, `RequestGate.enter` y `TraceSink.add` | `src/citalab/llm/rubato.py`, `src/citalab/evaluation/rubato_runner.py`, `tests/test_rubato.py`, `docs/rubato.md` |

En CitaLab no se toca el código evaluado: `scripts/reproduce.py` sigue respondiendo
«todo coincide con lo publicado». Una primera versión que modificaba `client.py` y
`runner.py` rompía esa comprobación (2 ficheros distintos) y se descartó.

## Modo `auto` (1.1.0)

Ambos adaptadores aceptan `auto` (radar `65a9672`, CitaLab `0f98803`): leen
`GET /v1/mode` con caché de 30 s y obedecen el interruptor del panel. Si Rubato no
responde mantienen el último modo conocido (`off` antes de la primera respuesta).
Probado con transporte simulado (radar 109 pruebas de las rutas afectadas, CitaLab suite
completa y `reproduce.py` intacto) y, en el radar, contra el servicio real: tras poner
`enforce` en el panel, el adaptador devolvió `enforce`.

## Modos

| Modo | Radar (`RADAR_RUBATO_MODE`) | CitaLab (`CITALAB_RUBATO_MODE`) |
|---|---|---|
| `off` (defecto) | sin cambios, no contacta con Rubato | idéntico al runner habitual |
| `shadow` | decide con sus límites; Rubato registra, opina (`data/integrations/rubato-shadow.jsonl`) y contabiliza el uso real; un fallo de Rubato se ignora | igual, por intento de inferencia |
| `enforce` | sin permiso deshace la reserva local y espera; con Rubato caído la tarea queda pendiente | sin permiso pausa la cola de la campaña (`rubato-deferred`); el episodio queda reintentable |

Clases urgentes del radar: `cv`, `letter`. CitaLab entra como clase `eval` (fondo).
Solo viajan metadatos de planificación: nunca prompts, ofertas, transcripciones ni perfiles.

## Evidencia

- Pruebas con transporte simulado: radar 7 nuevas, suite completa **518 passed, 3 skipped**
  en la rama; CitaLab 5 nuevas, suite completa **105 passed**, `reproduce.py` OK.
- Contra el servicio real de Compose (proveedor de Google/Groq simulado; scripts en
  `scripts/integracion/`), política `reserva` con cuota 3 y reserva 2:
  - Radar `shadow`: las tres llamadas se ejecutan; Rubato opina `authorize/fifo_order`,
    `defer/deadline_reserve` (divergencia registrada) y `authorize/urgent_first`.
  - Radar `enforce`: primer análisis ejecutado, segundo aplazado por la reserva, CV
    urgente ejecutado; 5 filas locales `ok`, ninguna reserva huérfana, 5 llamadas al
    proveedor (una por liquidación).
  - CitaLab `shadow`: 2 llamadas ejecutadas, uso 2 contabilizado. `enforce`: primera
    ejecutada, segunda `rubato-deferred (deadline_reserve)`, cola pausada, 1 llamada.
- Demo de dos clientes (`rubato demo-service`, prueba `tests/test_f6_two_clients.py`,
  carga **sintética**, proveedor simulado, cuota 24): con `reserva` la candidatura
  urgente (CV + carta) termina, el fondo cede exactamente 2 peticiones y queda 1 episodio
  de CitaLab pendiente; con `radar` el fondo usa las 24 y la candidatura queda en
  `capacity_exhausted`. Al pasar a `off`, 3 trabajos posteriores llegan al proveedor por
  la vía habitual sin ninguna decisión nueva en Rubato.
- Fallos encontrados al integrar y corregidos: ids de llamada reutilizados por SQLite
  tras borrar una reserva rechazada (radar); ids con `:` y `/` (servicio); una petición
  filtrada por paso podía conceder otro paso del mismo cliente y ocupar su ranura.

## Pendiente de evidencia

- `shadow` durante varias jornadas con tráfico real: latencia añadida, coincidencias y
  divergencias. Requiere que Andrés active el modo en su radar y en una campaña.
- Activación progresiva (`enforce` en un lote de prueba, después una clase reversible
  del radar, después ambos clientes). Requiere tráfico real y su aprobación.
- Una jornada real en la que ambos clientes compitan por el mismo límite.

## Volver atrás

`RADAR_RUBATO_MODE=off` / `CITALAB_RUBATO_MODE=off` (o el runner habitual) devuelve
el comportamiento anterior de inmediato. Si hubo permisos abiertos en `enforce`,
caducan solos; los intentos iniciados sin liquidar aparecen como `unknown` en
`GET /v1/admin/attempts?state=unknown` y se concilian con la tabla `calls` del radar
o las trazas de CitaLab. Para retirar el código basta con no fusionar (o revertir) la
rama `rubato-adapter` de cada repositorio. Ante desviaciones contables o trabajos
perdidos: detener admisiones (`PUT /v1/admin/resources` con capacidad 0), liquidar
reservas abiertas y volver a `off`.
