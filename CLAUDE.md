# Instrucciones de proyecto

Rubato es el planificador de trabajos de IA descrito en docs/desarrollo/PLAN_RUBATO.md. Al iniciar una sesión, lee ese plan y docs/desarrollo/ESTADO.md, comprueba el estado de Git y continúa por el primer criterio de aceptación abierto.

- Implementa en porciones que incluyan una prueba de comportamiento. Registra el comando exacto, su resultado, la evidencia y la siguiente tarea en docs/desarrollo/ESTADO.md antes de cerrar una fase.
- Empieza por el simulador sin credenciales. Mantén separadas las llegadas observadas, las estimadas y las sintéticas. Usa una misma función de decisión para simulación y servicio.
- Usa trazas sintéticas en los ejemplos distribuidos. Las bases, prompts, documentos y credenciales del radar son privados. Sigue las instrucciones de cada repositorio al integrar sus adaptadores.
- La demo usa un proveedor simulado. Las pruebas deben poder ejecutarse en local. Conserva los modos off, shadow y enforce definidos en el plan.
- Si falta un piloto humano o tráfico futuro, registra el criterio pendiente y continúa con el trabajo que no dependa de él.
