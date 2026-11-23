# Plan de implementación de Rubato para la entrevista

Andrés quiere abrir Rubato desde su portátil Windows, saber si está funcionando y demostrar en cinco minutos por qué importa la política de admisión. La entrega combina un planificador con dos errores de reserva corregidos, un mando local para operar los contenedores y una demostración que cualquier entrevistador pueda explorar con datos sintéticos. El servicio que usa Swiss Job Radar y el entorno de entrevista deben poder funcionar a la vez.

## Punto de partida

Comprobación local del 24 de noviembre de 2026 sobre la copia de trabajo de Rubato. Docker Compose mostraba cuatro servicios en ejecución. La API y PostgreSQL pasaban sus controles de salud. El planificador y el proveedor simulado figuraban en ejecución. El panel respondió con HTTP 200 y la API pudo consultar la base de datos.

| Para qué sirve | Qué hay hoy | Qué aporta esta entrega |
|---|---|---|
| Administrar Rubato | El panel web en `/panel` muestra cuotas, colas, decisiones, conciliaciones y modos por cliente. Se abre con el token de administrador. | El mando del portátil lo abre en el entorno seleccionado. |
| Operar los contenedores | Docker Desktop agrupa los servicios de `rubato`, permite iniciarlos y detenerlos y muestra sus registros. | Una ventana que sigue disponible cuando Rubato está parado y distingue un proceso en marcha de un planificador que está trabajando. [Documentación de Docker Desktop](https://docs.docker.com/desktop/use-desktop/container/) |
| Demostrar resultados | El simulador produce HTML autónomo con políticas, cronologías y decisiones. El repositorio incluye pruebas de concurrencia y recuperación. | Un recorrido guiado que une una decisión concreta, su coste y el resultado de una prueba de fallo repetible. |

## 1. Corregir dos errores de la política de reserva

Los siguientes casos se reprodujeron llamando a `simulate` con trabajos sintéticos que llegan en el instante 0. Empezar con dos pruebas que fallen en la revisión actual. La política afectada es `reserva`.

| Caso reproducible | Comportamiento observado | Criterio de aceptación |
|---|---|---|
| Cuota total 3, reserva 2. Llegan una petición urgente y dos de fondo, de una unidad cada una. | Rubato autoriza las tres en la primera ronda. La consulta del consumo urgente ya incluye la asignación tentativa y la política vuelve a sumarla. | Se admiten la urgencia y una petición de fondo. La otra espera y su decisión explica que queda una unidad reservada. Una urgencia posterior puede utilizarla. |
| Modelos A y B con cuotas independientes de una petición. La reserva especulativa es cero para ambas. Llega una urgencia de A que dura tres unidades de tiempo y un trabajo de fondo de B. | B espera hasta el instante 3 con `deadline_reserve` pese a tener libre su cuota. La demanda urgente se agrega por unidad de consumo y pierde el modelo del paso. | Ambos empiezan en el instante 0. Si los dos compiten por una cuota común, la demanda conocida sí queda protegida. |

Revisar `policies.py`, `core.py`, `resources.py` y la construcción de demanda en `sim.py` y `service/store.py`, bajo `src/rubato/`. Conservar la atribución por paso, modelo y cliente, incluidas las dependencias de un trabajo que cambia de modelo. Aplicar una sola regla de cálculo al simulador y al servicio. Cubrir también cuotas por cliente, cuotas globales y permisos de rondas anteriores. Ejecutar el mismo caso por la API con PostgreSQL en el entorno de prueba.

**Aceptación.** Las pruebas nuevas distinguen el comportamiento anterior del corregido. Repetir los siete escenarios y comparar decisiones, trabajos y métricas con la línea base. Registrar los cambios de forma explícita y versionar la política si cambia la semántica. Los informes antiguos deben seguir abriéndose con sus resultados originales.

## 2. Crear el mando local de Windows

Implementar una ventana pequeña con Python y Tkinter, abierto desde un lanzador de doble clic. Tk 8.6 está disponible en el entorno Python de este portátil. La ventana se ejecuta en Windows, fuera de Compose. Usar la CLI de Docker con argumentos fijos y el archivo Compose del repositorio. Reservar la configuración de políticas y la conciliación al panel web existente. Docker documenta que el acceso a su motor otorga privilegios amplios. Mantener el control en la sesión local del usuario evita exponer una nueva API de administración. [Seguridad del motor Docker](https://docs.docker.com/engine/security/)

1. **Elegir el entorno.** «Entrevista» se abre por defecto. «Rubato habitual» identifica el servicio que usa Radar. Dar al primero proyecto Compose, puertos locales, volúmenes, etiqueta de imagen y credenciales de demostración propios. Resolver los destinos desde el proyecto seleccionado y comprobar contexto local, etiquetas y puertos antes de operar. Si un puerto está ocupado, explicar cuál y conservar el otro entorno. [Nombres de proyecto en Compose](https://docs.docker.com/compose/how-tos/project-name/)
2. **Ver el estado.** Mostrar motor Docker, API, PostgreSQL, planificador y proveedor con texto y hora de comprobación. Consultar `compose ps --all --format json`, `/readyz` y la salud del proveedor simulado. Añadir una marca periódica de la última ronda completada por el planificador, con reloj real y caducidad acorde a su intervalo. Una cola vacía también produce marcas. Si la marca caduca, indicar «planificador sin actividad reciente» aunque el proceso siga en ejecución. [Estados que expone Compose](https://docs.docker.com/reference/cli/docker/compose/ps/)
3. **Ofrecer cuatro acciones.** «Iniciar», «Detener», «Abrir panel» y «Ver registros». En la primera instalación de la demo, crear sus servicios. Al detener, conservar los volúmenes. Durante una transición, mostrar el progreso, impedir dobles clics y comprobar el estado final. Indicar el entorno y el efecto antes de detener el Rubato habitual. Abrir Docker Desktop o explicar cómo iniciarlo cuando el motor esté apagado. Mantener las credenciales fuera de los enlaces, registros y capturas.

**Aceptación.** Una persona inicia la demo desde la ventana, abre el panel, consulta registros, la detiene y vuelve a arrancarla conservando sus trabajos. Cubrir Docker ausente o apagado, primer arranque, puerto ocupado, API disponible con planificador detenido y clics rápidos repetidos. Verificar las acciones de escritura en un proyecto desechable y comprobar que el proyecto habitual conserva sus contenedores, modos y datos.

## 3. Dar al entrevistador una decisión que tomar

Añadir «Explorar una jornada» al mando. Debe funcionar con Docker apagado. Partir del generador de informes existente, `src/rubato/report.py`, y de `run_scenario`. Ofrecer jornada sin urgencia, candidatura tardía y cuota reducida. Dejar que el entrevistador elija una política de referencia y reservas de 0, 2, 4 u 8 peticiones. Cada combinación se calcula con el motor de Rubato, por adelantado o bajo demanda. Mostrar los parámetros de las políticas, ya que el orden de prioridad también puede cambiar entre ellas.

La primera vista debe mostrar qué pasó con la candidatura, cuántos trabajos de fondo terminaron y cuánto esperaron. Mostrar la hora de llegada, el plazo y la decisión de cada política al seleccionar un trabajo. Etiquetar el origen de la carga. Exportar una vista HTML que se pueda abrir sin conexión y conservar en el resultado la semilla, la huella de la traza, las versiones y los parámetros necesarios para reproducirlo.

| Evidencia de partida | Referencia Radar | Reserva | Interpretación para la entrevista |
|---|---:|---:|---|
| Candidatura tardía sintética, reserva 4 | 0/1 a tiempo, 111/120 de fondo terminados | 1/1 a tiempo, 109/120 de fondo terminados | Llega a las 17:15 UTC. Con reserva termina a las 17:16:15, antes de las 18:00. Dos trabajos de fondo quedan pendientes adicionales. |
| Jornada sintética sin urgencia, reserva 4 | 111 terminados, espera p95 de 109 s | 111 terminados, espera p95 de 165 s | El mismo volumen terminado puede ocultar un aumento de la espera. El p95 es el tiempo de espera que cubre aproximadamente al 95 % de los casos medidos. |
| Bloque de prueba histórico documentado, 71 trabajos y plazos hipotéticos, variante de reserva 2 | 15/15 urgencias a tiempo y 54/56 de fondo | 15/15 urgencias a tiempo y 54/56 de fondo | El empate explica por qué Radar sigue siendo la política por defecto. [Evaluación y supuestos](../evaluacion.md) |

**Aceptación.** Las dos primeras filas se reprodujeron con la copia de trabajo previa a la corrección. Tras la fase 1, usar la versión del motor y los resultados de esa ejecución en la demo. Verificar en navegador el cambio de escenario, los selectores de reserva, la ficha de decisión y la apertura del HTML desconectado. Mostrar «sin urgencias» cuando el denominador sea cero. Los ejemplos distribuibles solo incluyen datos sintéticos y agregados históricos ya documentados.

## 4. Demostrar qué ocurre cuando falla un cliente o una réplica

Reutilizar las pruebas de `tests/test_f5_service.py` que hacen competir 20 solicitudes por el último permiso y recuperan un intento incierto mediante su recibo. El ensayo de dos réplicas en `scripts/ops/ha_drill.py` prueba la continuidad del servicio entre caídas. Hoy asume nombres de contenedor del proyecto habitual y llama a un endpoint de reinicio de datos. Parametrizar los destinos y verificar que API, proveedor, réplicas y volumen pertenecen al proyecto de entrevista **antes** de habilitar «Ejecutar ensayo». Usar únicamente recibos del proveedor simulado. Restablecer las réplicas de la demo al finalizar, también si se interrumpe el ensayo.

Mostrar dos momentos. Primero, varios solicitantes piden el último permiso y solo uno lo obtiene. Después, una llamada queda incierta tras una caída y Rubato conserva la reserva hasta conciliar el recibo. La cronología debe mostrar las solicitudes al proveedor, los duplicados por paso, el saldo frente a recibos, los intentos inciertos y los segundos hasta volver a atender trabajo. Diferenciar una ejecución archivada de una ejecución en vivo. Identificar las llamadas mediante claves de intento estables permite explicar por qué un reintento podría duplicar un efecto y cómo se comprueba lo ocurrido. [AWS Builders’ Library sobre idempotencia](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/)

**Aceptación.** En el ensayo sintético con una llamada esperada por paso, comprobar 1 permiso de 20 solicitantes y, en la prueba de caída, un único efecto por paso a partir de los recibos individuales, saldo conciliado y resolución explícita de los intentos inciertos. Un descuadre deliberado en una prueba debe marcar el resultado como fallido. Guardar entorno, versión, parámetros, fecha y resultado en JSON. El tiempo de recuperación se mide en cada ejecución.

## Orden de entrega y verificación

1. Guardar la línea base y completar la fase 1. Usar una rama de trabajo y conservar los cambios que ya haya en el repositorio. Mantener aislados los datos de las pruebas con PostgreSQL.
2. Entregar el mando y la demo interactiva de las fases 2 y 3. Constituyen la versión mínima para la entrevista. Probar desde el escritorio de Windows con Docker encendido y apagado. Conservar una copia del HTML para presentar sin servicios.
3. Añadir el ensayo de la fase 4 y un trabajo de integración en CI con PostgreSQL y el proveedor simulado. El flujo actual ejecuta Pytest en Windows y Linux, pero las pruebas HTTP requieren una pila explícita. Verificar cuántas pruebas de integración se ejecutaron y publicar el resultado sintético como artefacto del propio flujo cuando se ejecute en CI.
4. Si hay tiempo después de la demo, medir cómo crece la espera con 1, 5 y 20 clientes, con igual traza y entorno. Informar peticiones por segundo, latencia p50 y p95 de admisión, espera por cliente, rechazos y errores. Medir desde el cliente de prueba o agregar histogramas de todos los procesos de la API. Hoy los contadores HTTP viven en memoria de cada proceso y la suma y el recuento de latencias solo permiten calcular una media. [Monitorización de sistemas distribuidos de Google SRE](https://sre.google/sre-book/monitoring-distributed-systems/) · [Histogramas en Prometheus](https://prometheus.io/docs/practices/histograms/)

Para cerrar cada fase, ejecutar los tests correspondientes, anotar el comando, el resultado y las limitaciones en el registro del proyecto, y proporcionar a Andrés un paso de comprobación visible. Usar la suite contra el entorno de demostración cuando necesite mover el reloj, reiniciar servicios o limpiar datos.

## Experimento posterior con los reintentos

En el bloque histórico de diseño, ocho urgencias acabaron después del plazo hipotético de una hora debido a sus duraciones y reintentos. Otras cinco agotaron sus intentos por errores o salidas inválidas. Ninguna de las trece esperó por cuota. La siguiente pregunta de producto es si Rubato puede detectar antes que un trabajo corre riesgo de incumplir el plazo. [Evaluación del historial de Radar](../evaluacion.md)

Probar primero un aviso en el simulador que use solo información disponible al decidir, como el tiempo restante, los pasos pendientes y las duraciones de intentos anteriores. Fijar el umbral en un período y evaluar en otro cuántos incumplimientos anticipa, con cuánto margen y cuántas falsas alarmas habría mostrado. Una alerta acertada demuestra capacidad de anticipación. Para medir cuántas candidaturas salvaría una intervención hacen falta resultados de esa intervención. Introducir cambios de reintento únicamente después de medir su efecto en llamadas, plazos y trabajo de fondo. [AWS Builders’ Library sobre tiempos de espera y reintentos](https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/)

## Guion de cinco minutos

- **0:00–0:40.** Abrir la jornada sintética. «Quedan 45 minutos para enviar un CV y una carta. ¿Guardarías cuatro peticiones para una urgencia que todavía no ha llegado?»
- **0:40–2:10.** Comparar la misma jornada con ambas políticas. Abrir la candidatura y una tarea de fondo. Mostrar el motivo, el plazo y el trabajo que se aplaza. Cambiar a la jornada sin urgencia.
- **2:10–3:10.** Enseñar el empate del bloque histórico. Explicar cómo influyó en la política por defecto y por qué se observa el tráfico con `shadow` antes de aplicar cambios.
- **3:10–4:30.** Desde el mando, abrir el panel de entrevista y enseñar el ensayo de fallo. Seguir un permiso hasta el recibo y la conciliación. Usar el último resultado guardado si se presenta sin Docker.
- **4:30–5:00.** Volver a la pregunta inicial. Pedir al entrevistador que elija otro tamaño de reserva y mostrar qué cambia para cada cliente.

## Encargo para GPT-6.2 Sol en Alto

> Trabaja en el repositorio Rubato. Lee este plan, las instrucciones del proyecto y su estado de Git. Implementa en orden las cuatro fases, empezando por reproducir los dos errores con pruebas. Usa el proyecto de demostración para toda prueba que modifique servicios. Conserva los cambios de trabajo existentes. Comprueba los resultados contra la línea base, prueba el recorrido en Windows y entrega el lanzador, el informe HTML y las instrucciones para repetir la entrevista. Distingue los resultados medidos de los objetivos pendientes. El benchmark de carga y el experimento sobre reintentos quedan como trabajo posterior.
