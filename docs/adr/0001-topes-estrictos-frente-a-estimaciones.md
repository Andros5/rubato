# ADR 0001 · Topes estrictos frente a estimaciones

**Estado:** aceptada (v1).

**Contexto.** Un proveedor cobra o limita por el consumo real, que solo se conoce al
terminar la llamada. Antes de llamar solo hay una estimación (historial) o un máximo
exigible (p. ej. `max_output_tokens` que fija el adaptador).

**Decisión.** Al admitir se reserva el **máximo exigible** si existe y se ajusta al
consumo confirmado al liquidar, en la ventana de admisión (D2). Peticiones y
concurrencia son exactas. Para tokens o dinero sin máximo, el paso se registra como
`orientative` y el aviso vuelve al cliente: el tope puede superarse (lo demuestra
`test_money_budget_in_integer_micro_units_with_versioned_tariff`, 32.000 frente a 30.000).

**Alternativas.** Reservar la estimación (menos capacidad ociosa, pero rompe los topes
estrictos); rechazar todo paso sin máximo (seguro, pero inutilizable con proveedores que
no permiten fijarlo).

**Consecuencias.** La capacidad reservada y no usada se ve en `rubato_ledger_amount` y
en el informe; los adaptadores deben declarar `maximum` para que un tope sea estricto.
