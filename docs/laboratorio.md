# Laboratorio de capacidad

En Windows, abre `Rubato.pyw` con doble clic desde el repositorio. «Entrevista» usa el proyecto
`rubato-entrevista`, puertos locales propios y volúmenes independientes. «Rubato habitual» opera el
proyecto que ya usa Radar. El panel pide el token de su entorno. Los tokens sintéticos de la demo
están en `config/demo-compose.vars`.

«Explorar una jornada» calcula escenarios sintéticos y abre el informe aun con Docker apagado.
Puedes cambiar las políticas, elegir un trabajo y leer sus decisiones en las dos cronologías. El
informe muestra la proporción de urgencias a tiempo, los trabajos de fondo terminados y la espera
p95 de los que empezaron. Con una muestra vacía indica cuántos quedaron pendientes. El p95 de cero
requiere al menos un trabajo que empezó sin esperar.

## Explorar y compartir

```powershell
rubato simulate --trace examples/demo.jsonl --scenario cuota-reducida --policies radar,reserva --out runs/entrevista/base
rubato explore --run runs/entrevista/base --spec examples/entrevista/reservas.json --out runs/entrevista/alternativas
rubato bundle --run runs/entrevista/alternativas --out runs/entrevista/demo.rubato.zip
rubato verify runs/entrevista/demo.rubato.zip --replay
```

`at_offset_s` cuenta desde el origen del escenario. En trazas de segundos es el inicio UTC del día y
en trazas de turnos es la primera llegada. Una variación `reserve` establece la reserva del recurso,
`release_offset` modifica su hora de liberación en segundos y `capacity` añade un incremento a una
capacidad conservando la política de referencia. El motor conserva las decisiones e intentos
anteriores al cambio. Ctrl+C cancela entre variantes. Repite el comando para retomar la misma
exploración.

Los límites en `goals` pueden repetirse por cliente bajo `goals.clients`. Un objetivo de espera
requiere una muestra. La recomendación se elige entre los valores ensayados y el informe conserva
las variantes, las diferencias por trabajo y los parámetros efectivos. En «Cuota reducida», la
reserva de 2 obtiene 1/1 urgencias a tiempo, 70 trabajos de fondo terminados y p95 de 163 s. La
reserva de 8 también obtiene 1/1 y 70 terminados, con p95 de 17.964 s.

Extrae el paquete en otra carpeta y abre `informe.html`. `verify` comprueba las huellas de los
archivos y `--replay` recalcula trabajos, decisiones e importes con la versión correspondiente
del motor. Las exportaciones de trazas privadas contienen únicamente agregados numéricos
sin identificadores. La verificación de esos paquetes comprueba su integridad.

## Importar intentos y calcular gasto

```powershell
rubato doctor --env entrevista --offline
rubato import-csv examples/entrevista/intentos.csv --mapping examples/entrevista/mapa.json --out runs/csv/traza.jsonl
rubato validate runs/csv/traza.jsonl --json
rubato simulate --trace runs/csv/traza.jsonl --tariff examples/entrevista/tarifa.json --scenario sin-urgencia --policies radar --out runs/csv/resultado
rubato compare --run runs/csv/resultado --report runs/csv/informe.html
```

El mapa identifica cliente, trabajo, paso e intento. Admite coma, punto y coma y tabulación.
Las horas locales usan una zona IANA y necesitan un desplazamiento explícito durante un cambio
horario ambiguo. `arrival_from_start` permite derivar la llegada del primer inicio. Las dependencias
usan `|` entre IDs de pasos. Si faltan observaciones necesarias para reconstruir los intentos de un
paso, el informe etiqueta la cobertura y el simulador usa las estimaciones del mapa.

Las tarifas declaran moneda, versión, modelo, precio decimal por millón de tokens y, opcionalmente,
`effective_at` en segundos Unix. Rubato redondea al entero más cercano en millonésimas de la moneda
al liquidar la llamada. Los empates se redondean hacia arriba. La tarifa queda fijada cuando se
concede el permiso. Con la tarifa del ejemplo, 2.000 tokens de entrada y 600 de salida cuestan
5.500 millonésimas de EUR. El presupuesto puede limitar varios recursos en una llamada, y el
informe de gasto la contabiliza una vez.

Para el proveedor simulado, ejecuta `python examples/python_gate.py` o añade `--async`.
`AsyncGate` entrega al callback la clave estable del intento. Una cancelación después del inicio
mantiene esa clave en `pending` hasta consultar el recibo. Si se conoce el resultado y falla la
liquidación, `finish` permite repetirla con el mismo intento.

## Operar el servicio

En «Políticas», guarda un borrador y evalúalo con una traza. La activación comprueba la revisión
base y registra el motivo. Una reversión crea una revisión con los parámetros anteriores. La
política `fair` pondera los turnos de fondo para un recurso designado y mantiene las reglas de
reserva de urgencias. Las ratios observadas dependen de qué clientes tienen trabajo y ejecutores
disponibles.

«Resumen» enlaza las esperas, plazos en riesgo e intentos inciertos con «Trabajos». La ficha muestra
motivo, recurso, dependencias, intentos y próximos instantes de revisión. `rubato digest --since 24h`
usa la misma consulta que «Desde ayer». Como la estimación de plazo parte del camino crítico,
el aviso indica el supuesto de ramas paralelas y muestra ausencias de ejecutores o datos.
Configurar `impossible=drop` acepta de forma explícita el cierre basado en dicha estimación.

«Capturar estado inicial» guarda la cola y saldos vigentes, ejecutores, pausas y configuración. En
una ficha incluida en la captura, «Probar la política del formulario en la captura» compara la
primera ronda bajo ambas políticas. La exportación incluye también los eventos posteriores
registrados y su cobertura. Para reproducir una jornada completa usa el laboratorio con una traza.

Ejecuta `python scripts/ops/ha_drill.py` para comprobar el último permiso y la conciliación en el
proyecto desechable, o `python scripts/ops/benchmark.py` para medir 1, 5 y 20 clientes desde el lado
del cliente. Las latencias del ensayo describen la máquina que lo ejecuta.
