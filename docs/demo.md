# Demo narrada (≈ 2 min)

Datos sintéticos, proveedor simulado. Guion para `scripts/demo.ps1` (o los mismos
comandos a mano). Duración aproximada por bloque entre corchetes.

1. **El lote** [20 s]. «Un día normal: 108 análisis de ofertas en ráfagas y 12
   evaluaciones de CitaLab comparten 150 peticiones diarias.» `simulate … sin-urgencia`:
   ambas políticas terminan 111 de 120 trabajos; la reserva solo sube la espera p95
   del fondo de 109 a 165 s.
2. **Llega una candidatura** [25 s]. «A las 17:15 aparece una oferta urgente: CV y carta
   antes de las 18:00.» `simulate … urgencia-tardia`: la referencia Radar la deja
   pendiente (0/1) porque la cuota ya se gastó; la reserva de 4 peticiones la termina
   (1/1) a costa de 2 análisis.
3. **Baja la cuota** [15 s]. `simulate … cuota-reducida` (70 %): la reserva sigue
   salvando la candidatura; el fondo pasa de 72 a 70 terminados.
4. **Comparar** [30 s]. `compare` abre el informe: resumen por objetivo con «▲ mejor»
   en cada fila (la referencia gana el fondo, la reserva gana la urgencia), dos líneas de
   tiempo con la candidatura arriba y la cuota disponible cayendo a cero.
5. **Abrir una decisión** [20 s]. Pulsar la candidatura en la línea de la referencia:
   «APLAZA [capacity_exhausted] rpd: needs 1, 0 available; reconsiderar 08:00 UTC».
   En la reserva: «AUTORIZA [urgent_first]».
6. **Cierre** [10 s]. «Terminados: 109 de fondo y la candidatura; pendientes: 10 análisis
   que se reconsiderarán al reinicio de la cuota. La política por defecto sigue siendo
   la referencia, porque en el historial real la reserva empató.»

Variante con servicio: `docker compose --profile demo up --build -d` y `rubato demo-service` (dos
clientes, cuota 24, la candidatura llega al final; con reserva termina y un episodio de
CitaLab queda pendiente).
