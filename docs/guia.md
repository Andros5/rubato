# Guía breve

Rubato reparte un presupuesto común (peticiones, tokens o dinero) entre aplicaciones
de IA. Para cada paso de trabajo dice si lo **autoriza**, lo **aplaza** o lo **cierra**,
con un motivo estable, el número que lo decidió y la hora en que lo reconsiderará.

## 1. Ensayar sin proveedor

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\rubato.exe simulate --trace examples/demo.jsonl --scenario urgencia-tardia --policies radar,reserva --out runs/demo
.\.venv\Scripts\rubato.exe compare --run runs/demo --report informe.html
```

Abre `informe.html`: arriba, cada objetivo por separado (urgencias a tiempo, fondo
terminado, espera); en medio, las dos líneas de tiempo; pulsa una fila para ver su ficha
de decisión. Escenarios disponibles en `examples/scenarios.json`: `urgencia-tardia`,
`urgencia-temprana`, `sin-urgencia`, `pico-demanda`, `cuota-reducida`,
`estimaciones-erroneas`, `consumo-ajeno`.

## 2. Leer un motivo

| Motivo | Significa | Qué hacer |
|---|---|---|
| `deadline_reserve` | cabe, pero esa capacidad está retenida para urgencias | esperar a la liberación o reducir la reserva |
| `capacity_exhausted` | la ventana diaria o total está agotada | esperar al reinicio indicado |
| `rate_limited` | ventana móvil (por minuto) llena | se reconsidera en segundos |
| `concurrency_limit` | no hay hueco de ejecución | se reconsidera al terminar otro intento |
| `provider_pause` | el proveedor respondió 429 | se respeta `Retry-After` |
| `never_fits` | el paso pide más que el límite completo | subir el límite, bajar el máximo o partir el paso |
| `deadline_impossible` | ya no puede terminar a tiempo (política `drop`) | revisar el plazo |
| `backfill` | adelanta a otro que no cabía | — |

## 3. Usar tu propia carga

Escribe una traza `rubato.trace/v1` (ver `docs/trazas.md`) o genera una con
`rubato synth --seed 1 --out mi.jsonl`. Marca la procedencia de cada llegada:
`observed`, `derived`, `synthetic` o `hypothetical`. El informe nunca las mezcla.

## 4. Coordinar aplicaciones

`docker compose up --build -d` levanta el servicio. Cada aplicación usa el SDK:

```python
from rubato.sdk import Gate, Outcome, RubatoClient
gate = Gate(RubatoClient("http://127.0.0.1:8000", "<token>"), mode="shadow")  # off | shadow | enforce
gate.register({"id": "job-1", "class": "match", "steps": [{"id": "s", "estimate": {"requests": 1}, "maximum": {"requests": 1}}]})
result = gate.run("job-1", "s", lambda: (valor, Outcome("ok", {"requests": 1, "tokens_in": 900})))
```

Empieza en `shadow` (observa sin bloquear) y pasa a `enforce` solo cuando las
divergencias y la latencia añadida sean aceptables. Volver a `off` restaura el
comportamiento anterior al instante.
