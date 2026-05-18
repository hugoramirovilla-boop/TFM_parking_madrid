# Alcance actual del TFM

El TFM modela y analiza la dificultad de aparcar en Madrid integrando estacionamiento regulado en superficie (SER), aparcamientos públicos/EMT y fuentes contextuales mediante datos abiertos, análisis espacio-temporal y visualización cartográfica.

## Enfoque

El sistema se estructura en dos núcleos paralelos:

1. **SER**: núcleo principal del modelado. La dificultad de aparcar en superficie no se observa directamente, por lo que se aproxima mediante señales parciales: uso pagado observado, capacidad física y presión estructural.
2. **EMT/off-street**: capa observable complementaria. Permite representar inventario, disponibilidad histórica, ocupación mensual y, si procede, tiempo real.

No se fuerza un único modelo SER+EMT. La relación entre ambos bloques es espacial, temporal, contextual y cartográfica.

## Unidad de análisis

- Granularidad temporal base: 60 minutos.
- SER:
  - `SER_barrio_hora`: salida robusta inicial.
  - `SER_calle_hora`: salida fina deseada, basada en hipótesis explícitas de asignación espacial.
- EMT:
  - `inventario_global_emt`
  - `EMT_parking_hora`
  - `EMT_parking_mes`
  - `EMT_live`

## Entrega actual

La fase actual corresponde a la segunda entrega parcial: descripción y limpieza de datos.
