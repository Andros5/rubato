# ADR 0002 · Política en línea frente a óptimo con información perfecta

**Estado:** aceptada (v1).

**Contexto.** El mejor plan para una jornada depende de llegadas futuras que la
política no conoce cuando decide.

**Decisión.** Las políticas distribuidas son **en línea**: `decidir` solo ve el estado
en `ahora`. El óptimo con información perfecta (`optimize.py`: enumeración y CP-SAT,
idénticos en 25 instancias pequeñas) se usa como **cota** en el simulador para saber
cuánto se pierde por no conocer el futuro, nunca como política.

**Alternativas.** Planificación de horizonte móvil con previsión de llegadas: aplazada
hasta tener jornadas prospectivas con llegadas `observed` (hoy las llegadas históricas
son `derived`).

**Consecuencias.** Las comparaciones dicen «la reserva alcanza el óptimo en F0» o «queda
a X del óptimo» en vez de prometer optimalidad. La reserva puede rendir peor que la
referencia: un día sin urgencias retiene capacidad que el fondo habría usado (F0 gemelo:
fondo a tiempo 1/3 frente a 3/3; demo `sin-urgencia`: p95 de espera 109 → 165 s; rejilla
`rpd8`: p95 hasta ~5 h con cuota reducida). Con el historial real empató.
