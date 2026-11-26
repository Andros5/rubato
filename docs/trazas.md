# Trazas `rubato.trace/v1`

JSON Lines. La primera línea es la cabecera; después vienen registros `resource`,
`job` y `event`. El escritor es canónico (claves ordenadas, sin espacios): la misma
traza produce los mismos bytes y la misma huella.

```json
{"type":"header","schema":"rubato.trace/v1","time_unit":"s","created_by":"rubato.synth/v1","seed":7,"assumptions":["..."]}
{"type":"resource","id":"rpm","unit":"requests","capacity":6,"window":"rolling","length":60,"scope":{"model":"sim-flash"}}
{"type":"job","id":"application-00","client":"radar","class":"application","arrival":1795687200,"deadline":1795690800,
 "urgent":true,"priority":10,"provenance":{"arrival":"synthetic","deadline":"synthetic"},
 "steps":[{"id":"cv","estimate":{"requests":1,"tokens_in":14000,"tokens_out":2500},"duration_estimate":40,"max_attempts":3,
           "attempts":[{"duration":38,"status":"ok","usage":{"requests":1,"tokens_in":13210,"tokens_out":2702}}]}]}
{"type":"event","kind":"pause","t":1795690000,"until":1795690300,"scope":["model","sim-flash"]}
```

## Varios proveedores

Un paso puede llevar `"provider":"a"` y un recurso puede acotarse por proveedor y
cliente (claves de `scope`: `provider`, `model`, `client`; cualquier otra es un error):

```json
{"type":"resource","id":"a-rpd","unit":"requests","capacity":500,"window":"calendar","reset_tz":"Europe/Zurich","scope":{"provider":"a"}}
{"type":"resource","id":"a-rpd-radar","unit":"requests","capacity":200,"window":"calendar","reset_tz":"Europe/Zurich","scope":{"provider":"a","client":"radar"}}
{"type":"event","kind":"pause","t":1795690000,"until":1795690300,"scope":["provider","a"]}
{"type":"event","kind":"consume","t":1795690100,"client":"otra-app","provider":"a","usage":{"requests":3}}
```

Ver D10 en [decisiones](decisiones.md).

## Unidades e IDs

- Tiempo: entero; `time_unit` = `s` (segundos Unix UTC) o `turn` (casos de juguete).
- Uso: enteros por unidad — `requests`, `tokens_in`, `tokens_out`, `tokens_total`
  (se calcula si faltan), `cost_micro` (millonésimas de la moneda del perfil).
- IDs de trabajo estables; en exportaciones privadas son HMAC opacos con una sal local
  (`private/.salt`), así que reimportar conserva los IDs sin revelar la referencia.

## Procedencia por campo

`provenance` asigna a cada campo `observed`, `derived`, `synthetic`, `hypothetical`
o `missing`. `provenance.arrival` es obligatorio: los informes nunca mezclan llegadas
observadas, estimadas (`derived`) y sintéticas. `null` significa desconocido; `0` es
un cero observado. En `attempts[].usage`, un valor `null` se descarta (desconocido).

## Lo que ve cada parte

- La política ve `estimate`, `maximum`, `duration_estimate`, llegada, plazo y prioridad.
- El simulador revela `attempts[n]` (duración, uso, estado) solo al terminar el intento.

## Errores

El cargador indica línea, objeto y campo: `line 2: job 'j1' step 's': field 'estimate'
must be an object of non-negative integers, got {'requests': -1}`. Un esquema
distinto de `rubato.trace/v1` se rechaza.

## Importación privada del radar

`rubato import-radar --radar-dir <data> --from 2026-11-09 --to 2026-11-21 --out private`
abre `google-ai.db` y `radar.db` con `mode=ro`, copia solo campos permitidos y escribe
la traza y un informe en `private/` (ignorado por Git). El informe reconcilia leídas =
incluidas + descartadas por motivo, cuenta ceros y ausentes por campo y publica la
cobertura de procedencia y la huella. Limitaciones:

- `calls.created` se escribe al **reservar** la llamada: la llegada es `derived` (cota
  superior). `tasks.scheduled_at` puede sobrescribirse: solo se usa la prioridad.
- No hay plazos en el historial (`deadline: missing`); los escenarios pueden añadir
  plazos `hypothetical`.
- Un episodio termina en la primera llamada correcta; las llamadas fallidas previas
  son reintentos del mismo paso y las posteriores abren un trabajo nuevo.
- `output_tokens` vale 0 en 354 llamadas (la columna tiene `DEFAULT 0` y los fallos
  no devuelven salida): se conservan como cero observado y el informe los cuenta aparte.

## Captura prospectiva `rubato.capture/v1`

`CaptureLogger` añade a un JSONL los eventos `job_enqueued`, `step_ready`,
`attempt_started` y `attempt_finished` con ID de correlación. Solo acepta campos de
planificación (rechaza texto libre), nunca lanza excepciones al anfitrión y se apaga con
`RUBATO_CAPTURE=off`. `capture.to_trace` convierte la captura en una traza con
llegadas `observed`.

## Generador sintético `rubato.synth/v1`

`rubato synth --seed 7 --out examples/demo.jsonl`. Aleatoriedad anclada a
(semilla, trabajo, paso, intento): añadir un trabajo no altera los demás.
