# Informe de versión 1.0.0 (local, 2026-11-23)

Versión local preparada. **No publicada** en GitHub, PyPI ni registros de imágenes:
eso es una acción de distribución separada. Ningún dato ni credencial del radar está
en el paquete ni en el historial (`private/` y `deploy/k8s/secrets.env` ignorados).

Artefactos: `dist/rubato-1.0.0-py3-none-any.whl`, `dist/rubato-1.0.0.tar.gz`, imagen
local `rubato:1.0.0` (Compose), etiqueta Git `v1.0.0`.

## Probado en local (este equipo)

Windows 11 Pro 10.0.26300, Python 3.13.1, Docker Desktop 29.1.2, PostgreSQL 17-alpine.

| Qué | Cómo | Resultado |
|---|---|---|
| Simulador, entorno de tercero | rueda instalada en un venv limpio fuera del repositorio; `init-demo`, `simulate`, `compare`, `explain` | correcto; explica el aplazamiento de la candidatura |
| Suite sin servicio | `pytest -q` | ver `ESTADO.md` (F8) |
| Servicio desde clon limpio | `docker compose -p rubato-clean up --build` + `pytest -m postgres` + `demo-service` | ver `ESTADO.md` (F8) |
| Concurrencia | 20 `claims` concurrentes sobre la última petición | 1 permiso |
| Caídas | antes y después de `start`, reinicio de API y planificador | estados `expired` / `unknown` recuperables |
| Restauración | `restore_drill.py` | trazabilidad y decisiones conservadas |
| Réplicas | `ha_drill.py` (2 réplicas, 2 consumidores) | 0 duplicados, descuadre 0, reatención 6,2 s con TTL 5 s |
| Optimizador | CP-SAT = enumeración | 25/25 instancias |
| Kubernetes local (kind 0.33.0) | `k8s_up.ps1`, suite `-m postgres`, `ha_drill.py` | 16 passed; 0 duplicados, descuadre 0, reatención 5,0 s |

## Observado en los proyectos de Andrés

- Historial del radar 9–21 nov 2026 (privado, solo agregados): 3.185 llamadas,
  reconciliadas; evaluación por bloques → la reserva **empata** con la referencia en
  prueba (15/15 urgencias); la política por defecto sigue siendo `radar`.
- Adaptadores en ramas `rubato-adapter` (no fusionadas): suites completas del radar
  (518) y de CitaLab (105) en verde; `reproduce.py` de CitaLab intacto; humo de ambos
  adaptadores contra el servicio real con proveedores simulados.

## Cuándo rindió peor la reserva

- Sin urgencias, retiene capacidad: F0 gemelo (fondo a tiempo 1/3 frente a 3/3), demo
  `sin-urgencia` (p95 de espera 109 → 165 s).
- Una reserva grande (`rpd8`) dispara la espera del fondo (p95 hasta ~5 h con cuota
  reducida) sin salvar más urgencias.
- En el historial real no ganó: las urgencias perdidas no lo eran por cuota.

## Pendiente de un usuario o de tiempo

- Jornadas `shadow` reales en el radar y CitaLab, y activación progresiva en `enforce`.
- Una jornada real con ambos clientes compitiendo por el mismo límite.
- CI en Linux (no hay repositorio remoto; el flujo existe en `.github/workflows/ci.yml`).
- Piloto con un mantenedor externo (`docs/piloto.md`).
- Acuerdo sobre los umbrales del piloto (`config/piloto.json`, propuesta).
