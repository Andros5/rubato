# Evaluación (F4)

Parámetros congelados antes de evaluar: `examples/policies.json` (sintético) y
`examples/policies-radar.json` (privado), ambos `frozen_at: 2026-11-23`; umbrales
del piloto en `config/piloto.json` (**propuesta pendiente de acuerdo con Andrés**:
+1 urgencia a tiempo y como máximo 30 min más de espera p95 del fondo).

## 1. Jornadas sintéticas (`examples/demo.jsonl`, semilla 7, 120 trabajos)

Todo es sintético; la candidatura urgente es **hipotética** en cada escenario.
Cuota diaria `rpd` = 150 peticiones (reinicio a medianoche del Pacífico = 08:00 UTC),
`rpm` = 6, `tpm` = 120.000 tokens de entrada. Salida completa en
`docs/escenarios-sinteticos.txt`; resultados versionados de la demo en
`examples/resultados/urgencia-tardia/`.

| Escenario | radar: urgentes / fondo terminado | reserva (4 rpd): urgentes / fondo terminado | Lectura |
|---|---|---|---|
| urgencia-tardia (17:15 UTC) | 0/1 · 111/120 | **1/1** · 109/120 | la reserva salva la candidatura a costa de 2 trabajos de fondo |
| urgencia-temprana (11:00 UTC) | 1/1 · 109/120 | 1/1 · 109/120 | da igual: hay cuota cuando llega |
| sin-urgencia | 0/0 · 111/120 | 0/0 · 111/120 | la liberación a las 18:30 UTC devuelve la cuota; solo sube la espera p95 (109 → 165 s) |
| pico-demanda (×2 fondo) | 0/1 · 105/240 | **1/1** · 104/240 | |
| cuota-reducida (70 %) | 0/1 · 72/120 | **1/1** · 70/120 | |
| estimaciones-erroneas (tokens ×2,5) | 0/1 · 111/120 | **1/1** · 109/120 | el tope de tokens no llega a limitar con esta carga |
| consumo-ajeno (40 peticiones a las 12:00) | 0/1 · 76/120 | **1/1** · 75/120 | |

Variantes precalculadas de la reserva (rejilla `rpd` 0 / 2 / 8): 0 equivale a no
reservar; 2 basta para CV + carta sin reintentos; 8 dispara la espera p95 del fondo
(hasta ~5 h en `cuota-reducida`) sin salvar más urgencias.

## 2. Historial privado del radar (agregados, sin datos personales)

Traza `private/radar-2026-11-09-2026-11-21.jsonl` (huella `1dc1507a806621a5`, fuera de
Git), perfil declarado `examples/profiles/radar-declared-2026-11-09.json`, plazo
urgente **hipotético** = llegada + 60 min, días del proveedor (America/Los_Angeles,
igual que `calls.day`). Llegadas `derived` (cota superior). Comando:
`rubato evaluate --trace private/radar-2026-11-09-2026-11-21.jsonl --profile examples/profiles/radar-declared-2026-11-09.json`.

| Bloque | Días | Trabajos | radar: urgentes a tiempo · fondo terminado | reserva: ídem |
|---|---|---:|---|---|
| diseño | 9–15 nov | 2.133 | 69/82 · 2.051/2.051 (p95 10 s) | 69/82 · 2.051/2.051 (p95 11 s) |
| ajuste | 16–18 nov | 95 | 0/0 · 94/95 | 0/0 · 94/95 (todas las variantes) |
| prueba | 19–21 nov | 71 | 15/15 · 54/56 | 15/15 · 54/56 (`reserva-rpd2`) |

**Veredicto:** la reserva empata con la referencia en el bloque de prueba (ganancia
urgente 0). Según el plan, **la política por defecto es `radar`** y el simulador sigue
siendo el producto. Motivo observable: en el historial las urgencias que no terminan a
tiempo fallan por salidas inválidas o errores del proveedor, no por cuota; y el bloque
de ajuste no tiene ninguna urgencia, así que la elección de variante no está
informada. Con 71 trabajos en prueba el resultado es indicativo.

## 3. Coste del planificador

Latencia de decisión p95 en la demo (Windows 11, Python 3.13.1, cola ≤ 24 pasos
listos): 4,3–8,6 ms por ronda según la política. Historial privado: 7,7 ms (radar) y
8,5 ms (reserva) en el bloque de diseño (2.133 trabajos), 0,16–0,18 ms en el de prueba.
El coste crece con el número de entradas del libro porque `Ledger` recorre las
entradas vivas en cada consulta. Son medidas locales, no extrapolables a producción.
