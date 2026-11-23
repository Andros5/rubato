# Política de referencia Radar frente a `Workspace.claim`

`RadarPolicy` reproduce el orden de `Workspace.claim` de swiss-job-radar
(`radar/workspace.py`):

```sql
SELECT * FROM tasks WHERE state='queued' AND kind NOT LIKE 'browser:%' AND scheduled_at<=?
ORDER BY COALESCE(priority,0) DESC, scheduled_at LIMIT 1
```

En Rubato: elegibles = pasos listos (`ready_since <= ahora`); orden = prioridad
descendente, después `ready_since` (papel de `scheduled_at`), después orden de traza.

## Diferencias documentadas (subconjunto comparable)

| Aspecto | Radar | Rubato `radar@1` | Efecto |
|---|---|---|---|
| Agrupación | `claim_analysis_companions` reclama varias filas `analyze_job` y las manda en un lote | cada análisis es un trabajo; no simula lotes | Rubato cuenta una petición por análisis; un lote real reparte una petición entre varios |
| Trabajadores | un `claim` por vuelta del bucle; concurrencia implícita = nº de trabajadores | autoriza en la misma ronda todo lo que cabe; la concurrencia se modela con un recurso `concurrency` | para comparar, declarar `concurrency` = nº de trabajadores |
| Arrendamiento | `lease_until` de 30 min; las filas caducadas vuelven a `queued` | no hay arrendamiento en el simulador (el servicio F5 sí caduca permisos) | ninguno en trazas sin caídas |
| Tareas de navegador | excluidas (`browser:%`) | no se importan | — |
| Reprogramación | `scheduled_at` se sobrescribe al reprogramar | `ready_since` es el instante en que el paso quedó listo en la simulación | el historial no conserva la llegada original (ver `docs/trazas.md`) |
| Límites del proveedor | comprobaciones locales en `google_ai.py` (reservas en `calls`, `cooldowns`) | recursos declarados en el perfil del escenario | el escenario debe declarar los mismos topes |
| Pausas | `paused` por la aplicación | `client_down`/`pause` como eventos | — |

Con información histórica incompleta (llegadas `derived`, sin plazos), la comparación
entre políticas sobre el historial solo puede reordenar llamadas registradas: no dice
qué habría respondido el proveedor en otro instante. Cada escenario lo declara.
