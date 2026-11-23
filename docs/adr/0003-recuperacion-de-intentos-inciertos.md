# ADR 0003 · Recuperación de intentos inciertos

**Estado:** aceptada (v1).

**Contexto.** Un cliente puede caer entre `start` y `finish`. El proveedor quizá
procesó la llamada (consumió cuota) o quizá no.

**Decisión.** Se separan caducidad del permiso y cierre contable. Un permiso no
iniciado caduca (`RUBATO_GRANT_TTL`) y se libera sin coste. Un intento iniciado sin
liquidar pasa a `unknown` tras `RUBATO_ATTEMPT_TIMEOUT`: su reserva sigue contada
(conservador), el paso **no** se reintenta a ciegas y queda visible en
`/v1/admin/attempts?state=unknown` y en métricas. `reconcile` lo resuelve con evidencia:
recibo del proveedor (consumido, con uso real) o ausencia comprobada (liberado y paso
de nuevo listo). La decisión y el ajuste quedan en `events`.

**Alternativas.** Liberar automáticamente al caducar (riesgo de doble gasto si el
proveedor sí procesó); reintentar automáticamente (riesgo de efectos duplicados).

**Consecuencias.** Hace falta un identificador o recibo del proveedor para conciliar sin
intervención; el proveedor simulado lo ofrece (`/v1/receipts/{clave}`) y el radar
conserva la fila `calls` con el mismo id. Probado en Compose (`test_crash_after_start…`,
`restore_drill.py`) y en el ensayo de réplicas (0 inciertos, 0 duplicados).
