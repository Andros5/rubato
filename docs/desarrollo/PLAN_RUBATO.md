# Rubato · Plan de implementación

**Objetivo.** Construir un planificador que permita a varias aplicaciones de IA compartir un presupuesto de peticiones, tokens o dinero. Rubato debe poder explicar qué trabajo autoriza, cuál aplaza, cuándo lo reconsiderará y qué se sacrifica si cambia la política. La primera entrega será un simulador que responda estas preguntas antes de conectar un proveedor.

**Caso de uso de Andrés.** Swiss Job Radar analiza ofertas en segundo plano. Si aparece una candidatura urgente, puede hacer falta capacidad para preparar un CV y una carta. CitaLab será el segundo cliente, con evaluaciones que pueden esperar. La demo permitirá introducir una urgencia a mitad de una jornada y comparar dos decisiones sobre la misma carga.

**Ejecutor previsto.** Claude Code con Opus 5.5 en Windows y PowerShell. Este archivo contiene la especificación, el orden de trabajo, las pruebas y los criterios de cierre. Las rutas y los comandos de arranque corresponden al equipo de Andrés el 23 de noviembre de 2026.

## 1. Abrir el proyecto en tu equipo

La carpeta de trabajo es `C:\Users\andre\Projects\rubato`, al mismo nivel que `swiss-job-radar`, `citalab` y `reprolab`. El repositorio local se entrega preparado con este plan, `CLAUDE.md` y `ESTADO.md`. **Para empezar ahora, ejecuta solamente este bloque.**

```powershell
Set-Location -LiteralPath 'C:\Users\andre\Projects\rubato'
claude --model claude-opus-5-5
```

Dentro de Claude Code, `/status` permite comprobar el modelo y la cuenta activa. El identificador completo fija la versión, mientras que el alias `opus` puede cambiar. Se ha comprobado localmente Claude Code 2.1.318 y la opción `--model`. La disponibilidad del modelo depende de tu cuenta y proveedor. [Configuración oficial de modelos](https://code.claude.com/docs/en/model-config)

**Primer mensaje para pegar en Claude Code**

> Ejecuta el proyecto Rubato según PLAN_RUBATO.md. Lee primero CLAUDE.md y ESTADO.md. Empieza por la primera fase pendiente y trabaja en orden hasta completar la v1. Implementa y ejecuta las pruebas de cada criterio de cierre. Antes de avanzar, registra en ESTADO.md los comandos, los resultados y la siguiente tarea concreta. Si una fase requiere observar tráfico futuro, deja la captura lista, avanza con lo independiente y mantén ese criterio abierto. Usa el proveedor simulado por defecto y los datos sintéticos en la demo. Las integraciones deben permitir volver al comportamiento anterior. Empieza implementando F0, no redactando otro plan.

Para retomar una sesión desde la misma carpeta, ejecuta `claude --continue --model claude-opus-5-5`. Para elegir una sesión concreta usa `claude --resume`. También puedes iniciar una sesión nueva con el comando de arriba y pedirle que lea `ESTADO.md`. El repositorio tiene que bastar para reanudar el proyecto. [Referencia de la CLI](https://code.claude.com/docs/en/cli-reference) · [Instrucciones de proyecto con CLAUDE.md](https://code.claude.com/docs/en/memory)

<details>
<summary>Cómo crear la carpeta de nuevo desde PowerShell</summary>

Este bloque sirve para una instalación nueva. Conserva los archivos que ya existan. La variable `$planOrigen` apunta a la copia de entrega de este equipo. Si descargas el Markdown en otro sitio, cambia únicamente esa ruta.

```powershell
$proyectoRubato = 'C:\Users\andre\Projects\rubato'
$planOrigen = 'C:\Users\andre\Documents\Codex\2026-11-23\he\outputs\PLAN_RUBATO.md'
New-Item -ItemType Directory -Force -Path $proyectoRubato | Out-Null
Set-Location -LiteralPath $proyectoRubato
if (-not (Test-Path -LiteralPath '.git')) { git init -b main }
if (-not (Test-Path -LiteralPath 'PLAN_RUBATO.md')) {
    Copy-Item -LiteralPath $planOrigen -Destination 'PLAN_RUBATO.md' -ErrorAction Stop
}
if (-not (Test-Path -LiteralPath 'CLAUDE.md')) {
    Set-Content -Encoding utf8 -LiteralPath 'CLAUDE.md' -Value 'Lee PLAN_RUBATO.md y ESTADO.md. Ejecuta las fases en orden y registra las pruebas de cierre.'
}
if (-not (Test-Path -LiteralPath 'ESTADO.md')) {
    Set-Content -Encoding utf8 -LiteralPath 'ESTADO.md' -Value '# Estado de Rubato','', 'F0 pendiente. Siguiente tarea: construir el escenario mínimo de dos clases.'
}
git status --short --branch
```

</details>

**Herramientas.** En el equipo están disponibles Python 3.13 mediante `py -3.13`, Git, Docker CLI, Docker Compose y `kubectl`. El simulador solo necesita Python. Comprobar el motor de Docker al llegar a F5. Para F7, usar un clúster local de pruebas disponible o instalar `kind` si se escoge ese camino.

## 2. Hitos y esfuerzo

| Fase | Entrega verificable | Depende de | Esfuerzo orientativo |
|---|---|---|---:|
| F0 | Repositorio ejecutable y primer caso de aceptación | Arranque | 6–10 h |
| F1 | Importador, trazas sintéticas y procedencia por campo | F0 | 14–22 h |
| F2 | Simulador determinista de recursos y fallos | F1, puede arrancar con datos sintéticos | 22–36 h |
| F3 | Referencias, política de reserva y explicación de decisiones | F2 | 20–32 h |
| F4 | Evaluación, CLI e informe visual comparativo | F2–F3 | 18–30 h |
| F5 | Servicio transaccional, API, cliente Python y Docker Compose | F3–F4 | 36–56 h |
| F6 | Integraciones con Radar y CitaLab, observación y piloto | F5 | 24–40 h |
| F7 | Operación concurrente y ejercicio de recuperación en Kubernetes | F5 y adaptadores de F6 | 20–34 h |
| F8 | Distribución, documentación y demo reproducible de la v1 | F6–F7 | 12–20 h |

**Tres puntos de entrega.** F4 entrega un simulador utilizable por sí solo. F6 entrega un servicio que coordina dos aplicaciones. F8 cierra la v1 de portfolio. Las **172–280 horas** son una estimación de esfuerzo individual equivalente, no una predicción de horas facturadas ni de duración con Opus. A 10 horas humanas semanales serían unas 18–28 semanas sin solapar trabajo. Con Claude Code conviene recalibrar después de F2 usando horas reales de revisión, incidencias y espera de datos.

## 3. Arquitectura y contrato compartido

```text
Trazas o escenario → reloj virtual → política → decisión → informe HTML/JSON
                                        ↑
Radar / CitaLab → API → trabajos listos + capacidad → admisión transaccional
                     ↕                                    ↓
                 PostgreSQL                     permiso al cliente
                                                          ↓
                               cliente llama al proveedor y declara resultado
```

### Responsabilidades

- **Núcleo Python.** Una función pura del tipo `decidir(ahora, trabajos_listos, capacidad, politica)` produce una decisión con motivo. La política ve exclusivamente los datos disponibles en ese instante. Simulador y servicio importan el mismo núcleo.
- **Almacenamiento.** SQLite y JSONL para importar, simular y compartir ejemplos. PostgreSQL para el servicio concurrente, sus migraciones y su libro de reservas. Evitar mantener dos implementaciones transaccionales del servicio.
- **Aplicaciones.** Radar y CitaLab conservan prompts, claves, llamadas al modelo y validadores. Rubato recibe metadatos de planificación y consumo. Cada conector declara qué modelos puede usar y cómo contabiliza sus límites.
- **Superficie de producto.** CLI para ensayar y comparar, informe HTML local con dos líneas de tiempo, API HTTP para operar y SDK Python pequeño para integrar. La pantalla en vivo puede reutilizar la vista de decisiones de la demo.

| Concepto | Datos que deben quedar definidos |
|---|---|
| Trabajo | ID estable, cliente, clase, llegada, prioridad, plazo opcional, pasos requeridos y definición de éxito. Un trabajo urgente cuenta una vez aunque requiera varias llamadas. |
| Paso e intento | Dependencias, modelo permitido, estimación disponible antes de ejecutar, máximo admitido, número de intento e ID único. Un reintento conserva el trabajo de origen. |
| Recurso | Ámbito de proyecto, modelo o cliente, unidad, capacidad, tipo de ventana, zona horaria del reinicio y versión. Distinguir tokens de entrada, salida y totales según el proveedor. |
| Reserva | Cantidades por recurso, instante, titular, vigencia de la autorización, uso confirmado y estado de conciliación. Eventos posteriores permiten reconstruir el saldo. |
| Decisión | Trabajo/paso, política versionada, instante, autorizar/aplazar/cerrar, motivo legible, recurso limitante y siguiente instante de reconsideración. |
| Evidencia | Origen observado, derivado o sintético, unidad y momento en que el planificador conoció el dato. Guardar hipótesis y parámetros junto al resultado. |

**Objetivo de la primera política.** Maximizar trabajos urgentes válidos terminados dentro del plazo, bajo los topes locales declarados. Comparar después el volumen y la espera del trabajo de fondo. Informar ambos objetivos por separado para que una media ponderada no oculte a quién perjudica una reserva.

## 4. Fases de implementación

### F0 · Repositorio, vocabulario y primer caso

1. Crear `pyproject.toml`, paquete `src/rubato`, carpeta `tests` y `examples`. Usar `venv`, `pytest` y una comprobación de estilo. Ejecutar el flujo básico en Windows y Linux en CI. Añadir a `.gitignore` entornos, bases privadas, volcados y archivos de credenciales.
2. Escribir un escenario diminuto con cinco turnos, una petición por turno y tres peticiones de cuota total. Hay tres análisis listos en el turno 1, con plazo al final del turno 3. En el turno 4 llega un trabajo urgente de dos pasos que vence al terminar el turno 5. Con orden de llegada terminan tres análisis y cero urgencias. Con dos peticiones reservadas terminan un análisis a tiempo y una urgencia a tiempo. En el día gemelo sin urgencia, liberar la reserva en el turno 4 permite terminar los otros dos análisis, ya fuera de su plazo. Estos resultados serán la primera prueba.
3. Decidir qué significa «terminado». Un CV y una carta válidos completan el trabajo de candidatura del escenario. Un HTTP 200 con salida inválida consume recursos, pero no completa el paso. La primera versión de este ejemplo puede resolverse a mano y servir como resultado esperado.

**Aceptación.** En un entorno limpio funcionan `py -3.13 -m venv .venv`, `.\.venv\Scripts\python.exe -m pip install -e ".[dev]"` y `.\.venv\Scripts\python.exe -m pytest -q`. Guardar la prueba y su salida en el registro de fase. El proyecto arranca en una máquina sin claves.

### F1 · Trazas, procedencia e importación privada

1. Leer en modo de solo lectura `swiss-job-radar\data\google-ai.db`, tabla `calls`, y `data\radar.db`, tabla `tasks`. Fijar rango de fechas y huella de la exportación. Copiar únicamente campos permitidos, como clase, tiempos, estado normalizado y consumo. Conservar también información de intentos y lotes cuando exista una relación verificable.
2. Resolver una limitación del historial. `calls.created` se escribe al reservar una llamada. `tasks.scheduled_at` puede sobrescribirse al reprogramar. Ninguno demuestra por sí solo la llegada original a la cola. Marcar los campos ausentes, publicar cobertura y permitir escenarios con llegadas o plazos hipotéticos claramente identificados.
3. Definir captura prospectiva de `job_enqueued`, `step_ready`, `attempt_started` y `attempt_finished` con ID de correlación. Preparar un registrador optativo que se pueda conectar pronto al radar en modo de observación. Así se acumulan jornadas útiles mientras se construye el simulador.
4. Construir un generador de trazas sintéticas con semilla y formato versionado. Excluir datos personales, rutas de documentos, ofertas completas, texto libre y credenciales. IDs opacos en los conjuntos privados. El ejemplo distribuible se genera desde cero.

**Control conocido.** Entre el 9 y el 21 de noviembre de 2026, ambas fechas incluidas según `calls.day`, la consulta de referencia da 3.185 llamadas y 2.880 de tipo `match`. Hay 3.085 duraciones positivas y 3.157 valores positivos de tokens de entrada. Son llamadas registradas, no 3.185 trabajos ni 3.185 candidatos.

**Aceptación.** Importar dos veces da el mismo resultado. El informe reconcilia leídas, incluidas y descartadas por motivo. Rechaza un esquema incompatible, distingue un cero de un dato ausente e identifica cuándo es imposible formar el trabajo completo. Ningún ejemplo de Git depende de las bases de Andrés.

### F2 · Simulador determinista

1. Implementar un reloj virtual y una cola de eventos. Ordenar por instante, tipo y secuencia estable. Documentar qué sucede si terminan una llamada y una ventana de cuota en el mismo instante. Usar tiempos UTC y zonas explícitas para reinicios diarios.
2. Modelar ventanas móviles y de calendario, peticiones, tokens y concurrencia. Incorporar el coste monetario en enteros de una unidad documentada con tarifas versionadas cuando se habilite. Las reglas específicas pertenecen al perfil del proveedor. [Límites de Gemini](https://ai.google.dev/gemini-api/docs/rate-limits)
3. Simular dependencias, resultados inválidos, cancelaciones, pausas, reinicios y coste real inferior o superior a la estimación. Una tarea que nunca cabe debe terminar con motivo accionable. Una tarea que puede caber mañana debe recibir hora de reconsideración.
4. Separar previsión y desenlace. La política recibe estimaciones históricas o límites declarados. El simulador conoce el consumo final de la traza solo al completar el intento. Anclar la aleatoriedad a trabajo/paso/intento o a una hora de incidencia común según el escenario.

**Aceptación.** Casos manuales para dos llegadas simultáneas, medianoche, token insuficiente, cancelación, dependencia fallida y agotamiento por dos límites a la vez. Misma semilla, versiones y entradas producen la misma secuencia y las mismas métricas. El reloj no entra en un bucle cuando no hay trabajo elegible.

### F3 · Políticas y explicación

1. Implementar **orden de llegada** y **referencia Radar**. Esta última reproduce prioridad seguida de elegibilidad temporal en el subconjunto comparable. Contrastar con `Workspace.claim` y documentar las diferencias por agrupación en lotes, pausas o información histórica incompleta.
2. Implementar **reserva para urgencias**. Configurar reserva por recurso, momento de liberación, antigüedad del trabajo de fondo y política para plazos imposibles. Si una tarea no cabe, comprobar si otra elegible puede avanzar. Registrar por qué se escoge la alternativa.
3. Construir una referencia de optimización para instancias pequeñas. Enumeración para un caso diminuto y OR-Tools CP-SAT si lo justifica el tamaño. Sirve de cota con conocimiento perfecto bajo las hipótesis del escenario. La política en línea desconoce las llegadas futuras. [Planificación con OR-Tools](https://developers.google.com/optimization/scheduling)
4. Definir motivos estables como `deadline_reserve`, `provider_pause`, `depends_on_step` o `capacity_exhausted`. Cada explicación incluye el valor numérico que determinó la decisión y el instante de reevaluación. Para explicar el coste de cambiar una regla, recalcular el escenario.

**Aceptación.** Presentar un día en que reservar mejora el resultado, otro en que perjudica al lote de fondo y otro en que da igual. Comprobar que el optimizador coincide con enumeración en ejemplos pequeños. La política de referencia debe poder ganar sin que el informe lo oculte.

### F4 · Evaluación y primera demo utilizable

1. Congelar parámetros antes de evaluar. Reparto inicial por fecha del proveedor: 9–15 noviembre para diseño (2.952 llamadas), 16–18 para ajuste (130) y 19–21 para prueba (103). Solo 103 llamadas en el bloque final limitan las conclusiones. Añadir jornadas prospectivas y separar resultados sintéticos de observados.
2. Comparar políticas con las mismas entradas y sin información futura. Variar picos de demanda, urgencia tardía, día sin urgencias, cuota reducida, estimaciones erróneas y consumo por aplicaciones ajenas. Reordenar llamadas históricas no demuestra qué habría respondido el proveedor en otro instante. Declarar ese supuesto en cada escenario.
3. Generar un HTML autocontenido con dos líneas de tiempo, filtro por cliente y clase, ficha de decisión y resumen. Mostrar hora de llegada, espera, fin, trabajos pendientes y cuota disponible. Permitir explorar varias reservas precalculadas o volver a simular desde la CLI.
4. Acordar y congelar los límites del piloto: espera tolerable de trabajo de fondo y qué mejora urgente tendría valor. Publicar números absolutos, denominadores y resultado por día. Si la candidata pierde o empata, distribuir la política de referencia por defecto y conservar el simulador como producto.

| Medida | Definición operativa |
|---|---|
| Urgencias a tiempo | Trabajos válidos completados antes del plazo / urgentes recibidos. Mostrar pendientes y plazos hipotéticos aparte. |
| Servicio de fondo | Terminados y pendientes por clase, p50/p95 de espera de los iniciados y edad del pendiente más antiguo. |
| Consumo | Peticiones, tokens y dinero por ventana, reserva sin usar, rechazos y pausas. Separar coste observado de estimado. |
| Coste del planificador | Latencia p95 de decisión y memoria por tamaño de cola, con equipo y configuración. |

**Aceptación.** Desde un clon limpio, los siguientes comandos previstos generan la comparación sin red. Versionar el escenario, ambos resultados y la configuración que los explica. Esta es la primera entrega que mostrar a otra persona.

```powershell
.\.venv\Scripts\rubato.exe simulate --trace examples/demo.jsonl --scenario urgencia-tardia --policies radar,reserva --out runs/demo
.\.venv\Scripts\rubato.exe compare --run runs/demo --report informe.html
```

### F5 · Servicio de admisión con contabilidad duradera

**Resultado esperado.** Dos procesos pueden competir por el último permiso. Cada cliente sabe si debe ejecutar, esperar o conciliar un intento incierto, incluso si se reinicia el servicio.

1. Crear la API FastAPI, migraciones de PostgreSQL y un cliente Python. Tablas iniciales para clientes, trabajos, pasos, intentos, ventanas de recursos, reservas, eventos y configuración versionada. Índices para pasos listos, próxima reconsideración y claves de idempotencia. Crear `compose.yaml` con API, proceso de planificación y base de datos.
2. Separar registro y ejecución. El cliente registra el trabajo y declara que tiene un ejecutor disponible. El planificador elige entre pasos elegibles de clientes disponibles y guarda un permiso para el cliente propietario. En una transacción, bloquear los recursos afectados en orden estable, comprobar los saldos y reservar todas las dimensiones o ninguna. El SDK recoge el permiso y confirma el inicio antes de contactar al proveedor.
3. Implementar operaciones idempotentes. Una repetición con la misma clave y los mismos parámetros devuelve la respuesta original. Una repetición con parámetros incompatibles devuelve conflicto. Un cliente solo consulta o modifica sus trabajos. Los límites compartidos los gestiona el administrador.
4. Mantener el libro de consumo. Reservar un máximo exigible para los topes estrictos y ajustar al consumo confirmado. El adaptador fija el máximo de salida y conoce la semántica de tokens del proveedor. Si solo existe una estimación, etiquetar el tope como orientativo o rechazar la admisión estricta. Preservar el saldo por ventana aunque la llamada termine tras el reinicio del contador.
5. Manejar caídas y reintentos. Un permiso que caduca antes de iniciar se libera. Una caída tras marcar inicio puede haber consumido cuota: pasar a `unknown`, mantener una contabilización conservadora y conciliar usando un identificador o recibo del proveedor si existe. Separar la caducidad del permiso del cierre contable. Ante un 429, registrar ámbito y pausa y respetar `Retry-After` cuando corresponda.

| Operación API propuesta | Efecto observable |
|---|---|
| `POST /v1/jobs` | Registrar trabajo, pasos, máximos y dependencias con una clave estable del cliente. |
| `POST /v1/claims` | Señalar capacidad disponible y recoger un permiso propio o un tiempo de espera. Controlar el acceso al último cupo con varios clientes. |
| `POST /v1/attempts/{id}/start` y `/finish` | Confirmar inicio antes de llamar y liquidar después con uso, estado y validación. Aceptar entregas repetidas. |
| `POST /v1/attempts/{id}/reconcile` | Resolver con evidencia un intento incierto. Mantener la decisión y el ajuste en el historial. |
| `GET /v1/jobs/{id}` y `POST /v1/jobs/{id}/cancel` | Consultar trabajo o cancelar pasos futuros. Los ya iniciados siguen la semántica de su proveedor. |
| `GET /v1/decisions` y `GET /v1/usage` | Mostrar motivos, saldos y pendientes accesibles al cliente autenticado. |

**Aceptación.** `docker compose up --build` levanta un entorno limpio con un proveedor simulado. Con capacidad para una petición, veinte solicitudes concurrentes obtienen como máximo un permiso válido. Repetir alta, inicio y fin no duplica el consumo. Terminar un proceso antes y después del inicio deja estados distintos y recuperables. Un reinicio preserva trabajos y auditoría. Verificarlo en PostgreSQL real durante las pruebas de integración.

### F6 · Integraciones con Swiss Job Radar y CitaLab

1. Implementar tres modos por cliente: `off` conserva el comportamiento habitual, `shadow` observa y calcula qué habría decidido Rubato, y `enforce` exige permiso antes de una llamada. Un fallo de Rubato en `shadow` permite continuar a la aplicación. En `enforce` el trabajo queda pendiente y visible hasta recuperar la coordinación.
2. **Radar.** Conectar el adaptador en la frontera anterior a la reserva y llamada al proveedor, cubriendo análisis por lotes, CV, carta y reintentos. Mantener las comprobaciones locales que reconoce el proveedor. Las dos capas observan una única llamada remota y Rubato contabiliza una sola liquidación. Los envíos de candidaturas y las aprobaciones siguen siendo responsabilidad del radar.
3. **CitaLab.** Empezar por un lote de evaluación con identificador de campaña, episodio y paso. Un fallo de cuota pausa el episodio y preserva su progreso. Los resultados históricos y su reproducción siguen siendo accesibles. Preparar una campaña sintética y un proveedor local para demostrar competencia por la cuota sin pagar ni enviar datos. [Proyecto CitaLab](https://github.com/Andros5/citalab)
4. Ejecutar `shadow` durante varias jornadas con tráfico suficiente. Registrar latencia añadida, coincidencias y divergencias con la decisión local. Obtener una jornada en la que ambos clientes puedan competir por el mismo límite, indicando si la carga es real o simulada. El modo de observación sirve para verificar integración y estimaciones. El efecto de una decisión alternativa sigue siendo una simulación.
5. Activar primero en un lote de prueba, después en una clase reversible del radar y finalmente en ambos clientes, con tope local y configuración por clase. Si hay desviaciones contables o trabajos perdidos, detener nuevas admisiones, liquidar las reservas abiertas y volver a `off`. Distinguir las pruebas con proveedor simulado del tráfico real observado.

**Aceptación.** Un lote de CitaLab y el análisis de ofertas comparten una cuota. Al llegar la candidatura urgente, la política reserva sus pasos y deja visible cuánto se retrasa el lote. Al volver a `off` se procesan trabajos posteriores por la vía habitual. Pasan las pruebas existentes de las rutas afectadas en ambos repositorios. Conservar commits o parches de integración separados y una guía de reversión.

### F7 · Operación, concurrencia y Kubernetes

1. Añadir registros estructurados y métricas de longitud/edad de cola, permisos, espera hasta inicio, decisiones por motivo, conciliaciones pendientes y recursos reservados/consumidos. Evitar etiquetas de métricas por ID de trabajo. Usar un ID de correlación en los registros. Publicar endpoints de disponibilidad y salud.
2. Probar restauración de la base en una instancia desechable y migración desde la versión anterior del esquema. Definir qué hacer con un intento incierto, un proveedor en pausa, un trabajo bloqueado y una reserva sin liquidar. Incluir comandos de diagnóstico y criterios para reanudar.
3. Desplegar sobre `kind` u otro clúster **local de pruebas**. Entregar manifiestos para API, planificador, configuración, límites de recursos y acceso a PostgreSQL persistente. El almacén conserva la autoridad aunque cambie el número de réplicas. Usar imágenes versionadas y clientes separados con permisos mínimos. [Despliegues de Kubernetes](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/)
4. Ejecutar un ensayo repetible con dos réplicas y dos consumidores. Terminar una réplica durante la reserva y otra durante una llamada simulada. Medir tiempo hasta que el trabajo vuelve a ser atendible, permisos duplicados, discrepancia de saldo y latencia bajo carga. Documentar el hardware, la cola y la versión utilizados.

**Aceptación.** La misma suite de comportamiento pasa en Compose y en el clúster local. En los escenarios ensayados, el consumo confirmado coincide con los recibos del proveedor simulado, los permisos no ejercidos se recuperan y los intentos inciertos quedan visibles. Una restauración conserva la trazabilidad y permite reanudar las decisiones. La v1 publica números medidos en vez de extrapolarlos a producción.

### F8 · Distribución, demo y cierre de la v1

1. Construir un paquete instalable, imágenes locales versionadas, configuración de ejemplo, esquema de trazas y notas de versión. Dejar una vía de instalación para el simulador y otra para el servicio. Verificar ambas desde entornos limpios y sin rutas locales de Andrés.
2. Crear README en inglés, guía breve en español, referencia de CLI/API y tres decisiones de arquitectura: límites estrictos frente a estimaciones, política en línea frente a óptimo con información perfecta, y recuperación de intentos inciertos. Explicar en qué condiciones la reserva rindió peor.
3. Preparar una demo narrada de unos dos minutos. Mostrar el lote, introducir una candidatura, reducir la cuota, comparar ambas políticas y abrir una decisión. Terminar con los trabajos completados y pendientes. El ejemplo distribuido usa exclusivamente datos sintéticos.
4. Preparar un piloto con un mantenedor de otra automatización de IA. Pedirle que importe su carga o un ejemplo representativo, elija el compromiso entre espera y cuota y explique una decisión sin ayuda. Medir tiempo de integración y anotar qué concepto le faltó. Esto valida usabilidad. La disposición a pagar requeriría una conversación y una decisión de compra separadas.
5. Dejar preparada la versión local `v1.0.0` con changelog, pruebas, resultados y guía de instalación. Publicar en GitHub, PyPI o un registro de imágenes es una acción separada de distribución. Las credenciales y los datos de las bases del radar quedan fuera del paquete y del historial público.

**Aceptación.** Un tercero puede instalar el simulador, generar el informe y explicar por qué se aplazó una tarea. Desde Compose se reproduce el escenario con dos clientes. El informe de release permite distinguir qué se probó en local, qué se observó en los proyectos de Andrés y qué queda pendiente de un usuario externo. Si el piloto aún no se ha celebrado, registrar ese hecho y entregar el paquete reproducible.

## 5. Protocolo de trabajo para Claude Code

- **Al iniciar sesión.** Leer `CLAUDE.md`, este plan y `ESTADO.md`. Revisar `git status` y la evidencia del último hito. Identificar un criterio pendiente que se pueda demostrar con una ejecución corta. Comprobar si ya existe en el código antes de reimplementarlo.
- **Al ejecutar.** Trabajar en una porción completa del comportamiento: entrada, decisión, salida y prueba. Si cambian el esquema o la política, conservar versiones y actualizar los ejemplos afectados. Las decisiones nuevas sobre semántica, datos o contabilidad deben quedar documentadas con su alternativa y motivo.
- **Al cerrar una fase.** Registrar fecha, cambios, comando exacto, resultado, ruta de evidencia e incógnitas. Marcar `completa` solo con sus pruebas ejecutadas. Hacer commits locales acotados cuando el cambio esté verificado, con datos privados fuera del índice. Mantener sincronizados los apuntes aunque cambie la sesión.
- **Si falta algo externo.** Marcar el criterio como `pendiente de evidencia` y avanzar con lo independiente. En particular, el paso del tiempo, una cuenta con acceso al modelo o una persona para el piloto no pueden sustituirse por datos sintéticos etiquetados como reales.
- **División de repositorios.** Implementar el producto en `rubato`. Leer los archivos de instrucciones que correspondan antes de tocar los clientes. Preparar una rama o cambio aislado para cada adaptador y conservar la compatibilidad con el modo `off`. Los datos del radar se consultan en modo de solo lectura durante el desarrollo y la medición histórica.

**Plantilla de cierre para `ESTADO.md`**

```markdown
### Fase y criterio
- Estado: pendiente / en curso / pendiente de evidencia / completa
- Cambio entregado y commit, si existe:
- Pruebas ejecutadas, comando y resultado:
- Evidencia reproducible:
- Supuesto o limitación:
- Próxima tarea concreta:
```

## 6. Primer bloque de tareas ejecutables

| Orden | Tarea | Evidencia para cerrarla |
|---|---|---|
| 1 | Comprobar `git status`, Python y la raíz del proyecto. Crear entorno virtual y paquete mínimo. | Un clon limpio instala `rubato` y ejecuta la prueba inicial. |
| 2 | Escribir los dos ejemplos de tres peticiones de F0 y sus resultados esperados a mano. | Un lector entiende en qué jornada conviene reservar y en cuál se pierde capacidad. |
| 3 | Definir el esquema JSONL v1, IDs, unidades y procedencia por campo. | Una traza mínima válida se carga y una inválida produce un error comprensible. |
| 4 | Hacer la consulta de inventario del radar. | El recuento privado coincide con F1 para el intervalo congelado. El informe de cobertura se puede regenerar. |
| 5 | Crear exportador local y generador sintético. | Reimportación sin duplicados. Los ejemplos de Git solo contienen campos permitidos. |
| 6 | Ejecutar la primera comparación completa en la CLI. | Dos políticas, el mismo escenario y un resultado comprobable sin proveedor externo. |

## 7. Decisiones de alcance y recorrido posterior

**Puede considerarse completa la v1** cuando el simulador compara políticas, el servicio comparte capacidad entre dos aplicaciones, los casos de concurrencia y caída son reproducibles y otra persona puede instalarlo siguiendo la guía. Las cifras de rendimiento tienen que indicar el entorno y las limitaciones. La política en uso puede ser la sencilla si así lo aconsejan los resultados.

**Después de la v1, en este orden de interés:** integrar una tercera aplicación para medir el coste real de adopción, admitir varios proveedores con sus propios límites, experimentar con un planificador de horizonte móvil y evaluar selección de modelo por calidad. Este último paso exige datos de calidad por clase y modelo. Un precio menor por token no basta para decidir qué trabajo compensa ejecutar. Compatibilidad con pasarelas existentes puede ahorrar trabajo de enrutamiento. [Documentación de LiteLLM](https://docs.litellm.ai/docs/routing)

**El primer compromiso de ingeniería es pequeño y comprobable:** una política reserva dos peticiones, otra las gasta, llega un trabajo urgente y Rubato explica quién termina a tiempo y quién espera. Todo lo demás se construye a partir de ese caso.
