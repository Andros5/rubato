# Decisiones de diseño

Cada decisión registra la alternativa descartada y el motivo.

## D1 · Qué significa «terminado» (F0)

Un trabajo termina cuando **todos** sus pasos producen una salida **válida**. Una
candidatura termina con un CV válido y una carta válida. Un HTTP 200 con salida
inválida (`status=invalid`) consume recursos y cuenta como intento, pero no completa
el paso; si se agotan los intentos el paso falla y los pasos que dependen de él se
cierran con `dependency_failed`. «A tiempo» significa terminado con `fin <= plazo`.
Un trabajo urgente cuenta una vez aunque requiera varias llamadas.

*Alternativa descartada:* contar llamadas con respuesta 200. Mide actividad del
proveedor, no trabajo útil, y premiaría a la política que más reintenta.

## D2 · Ventana a la que se imputa el consumo (F0/F2)

El consumo se reserva en la ventana del instante de **admisión** y, al terminar, se
ajusta al uso confirmado **en esa misma ventana**. Una llamada que termina después
del reinicio del contador no consume del nuevo periodo.

*Alternativa descartada:* imputar al instante de fin. Haría que el saldo de una
ventana ya cerrada cambiara retroactivamente y que una ventana nueva empezara con
consumo de llamadas que el proveedor contó antes del reinicio.

## D3 · Orden de eventos en el mismo instante (F2)

Dentro de un instante se aplican, en este orden: fin de intento, fin de pausa,
cancelación, cambio de disponibilidad del cliente o consumo externo, llegada y
despertador. Después se ejecuta **una** ronda de `decidir`. Los reinicios de ventana
no son eventos: la disponibilidad es función del tiempo. Si terminan una llamada y
una ventana a la vez, la llamada se liquida en su ventana de admisión (D2) y la
ronda posterior ya ve la ventana nueva. Empates dentro de una clase: orden de la traza.

## D4 · Horizonte

En el instante del horizonte se procesan los fines de intento pero no se admite
trabajo nuevo. Lo no terminado queda `pending` con su último motivo.

## D5 · Reserva para urgencias

La reserva especulativa retiene `R` unidades por ventana para trabajo urgente aún
no conocido; el consumo urgente de la ventana la descuenta. La demanda urgente
**conocida** (pasos pendientes de urgencias registradas) siempre se protege en los
recursos reservados, aunque la reserva especulativa se haya liberado. El motivo de
aplazamiento es `deadline_reserve` e incluye necesidad, disponible y retenido.

## D6 · Motivo vinculante

Si un paso choca con varios límites, el motivo publicado es el del límite que se
libera **más tarde** (el que realmente fija la espera); `value.blocks` conserva todos.

## D7 · Topes estrictos frente a estimaciones

Al admitir se reserva `maximum` (p. ej. el máximo de tokens de salida que fija el
adaptador) si existe; si solo hay `estimate`, el tope es **orientativo**: el consumo
real puede superarlo (prueba `test_money_budget_in_integer_micro_units_with_versioned_tariff`).
El servicio (F5) rechaza la admisión estricta sin máximo exigible o la etiqueta como
orientativa. El dinero se guarda en enteros `cost_micro` con tarifas versionadas.

## D8 · Pausas del proveedor

Un intento con estado `rate_limited` (HTTP 429) pausa el ámbito `("model", m)` hasta
`fin + retry_after` (1 unidad si no hay cabecera). Si el paso no declara modelo pero sí
proveedor, se pausa `("provider", p)`: el 429 de un proveedor no frena a los demás.
Mientras dura, los pasos afectados se aplazan con `provider_pause` y hora de
reconsideración = fin de la pausa.

## D9 · Cancelación

Cancelar cierra los pasos futuros (`cancelled`). Un intento ya iniciado sigue la
semántica del proveedor: termina y su consumo se liquida.

## D10 · Varios proveedores con límites por cliente

Cada paso puede declarar `provider`. Un recurso con `scope` se aplica solo a los pasos
que cumplen **todas** sus claves (`provider`, `model`, `client`), así que se combinan:

| `scope` | Significado |
|---|---|
| `{"provider": "a"}` | Cuota total del proveedor `a`, compartida por todos los clientes |
| `{"provider": "a", "client": "radar"}` | Parte de `a` que puede usar el radar |
| `{"provider": "a", "model": "m"}` | Límite de un modelo concreto de `a` |

Un paso del radar en `a` reserva a la vez en la cuota de `a` y en la parte del radar
(todo o nada); agotar su parte no frena a otros clientes en `a` ni al radar en `b`. Un
paso sin `provider` no consume recursos con ámbito de proveedor. Una clave de `scope`
desconocida se rechaza: antes se ignoraba y el límite se aplicaba a todo.

La demanda urgente conocida que protege la reserva (D5) se cuenta por recurso, solo en
los recursos que aplican a cada paso pendiente: un trabajo urgente en `b` no retiene
capacidad de `a`. Con un único proveedor el resultado es el mismo que antes.
