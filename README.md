# TFM Parking Madrid

Repositorio técnico del Trabajo Fin de Máster **“Modelado de la dificultad de aparcar en Madrid integrando estacionamiento regulado en superficie y aparcamientos públicos/off-street mediante datos abiertos”**.

## Objetivo

Construir un sistema reproducible para integrar datos abiertos de aparcamiento en Madrid y generar salidas analíticas y cartográficas sobre:

- dificultad estimada de aparcar en superficie regulada SER;
- ocupación y disponibilidad de aparcamientos públicos/EMT;
- integración espacial de ambas capas en mapas.

## Enfoque

El proyecto separa dos bloques principales:

- **SER**: núcleo del modelado. La dificultad de aparcar en calle no se observa directamente, por lo que se aproxima mediante señales de uso pagado, capacidad física y presión estructural.
- **EMT/off-street**: capa complementaria observable. Aporta inventario, histórico horario, ocupación mensual y, si procede, disponibilidad en tiempo real.

No se fuerza un único modelo SER+EMT. La integración entre ambos bloques se realiza de forma espacial, temporal, contextual y cartográfica.

## Estado actual

Primera entrega parcial completada:

- motivación;
- descripción del problema;
- estado del arte;
- objetivos.

Fase actual:

- descripción de datos;
- descarga controlada de fuentes;
- auditoría de calidad;
- limpieza inicial;
- generación de tablas intermedias.

## Organización de datos

La estructura de datos sigue tres niveles:

- `data/raw/`: datos originales, sin modificar.
- `data/interim/`: datos limpios o normalizados por fuente.
- `data/processed/`: tablas analíticas preparadas para análisis, modelado y visualización.

En `data/processed/` se separan las salidas por nivel de madurez:

- `core/`;
- `complementarias_v1/`;
- `complementarias_v2/`;
- `final/`.

Los datos no se versionan en GitHub. El repositorio conserva código, notebooks, documentación técnica y estructura de carpetas.

## Catálogo de datos

`data_catalog.csv` registra las fuentes utilizadas y mantiene la trazabilidad mínima entre:

- identificador del dataset;
- bloque temático;
- prioridad analítica;
- fuente oficial;
- URL;
- periodo del dato;
- archivo raw;
- archivo limpio/intermedio;
- estado de procesamiento.

Las decisiones metodológicas, riesgos y criterios de inclusión se documentan en `docs/`.

## Salidas esperadas

Principales salidas SER:

- `SER_barrio_hora`;
- `SER_calle_hora`;
- oferta SER agregada;
- uso SER agregado a 60 minutos;
- presión estructural por autorizaciones.

Principales salidas EMT:

- `inventario_global_emt`;
- `EMT_parking_hora`;
- `EMT_parking_mes`;
- `EMT_live`.

Salida final:

- mapa integrado SER + EMT;
- análisis separado de dificultad SER y ocupación EMT;
- discusión de cobertura, sesgos, limitaciones y viabilidad.

## Reproducibilidad

El flujo general del proyecto es:

    ingesta → raw → limpieza → interim → processed → EDA → mapas/modelos

Toda decisión relevante debe quedar documentada en `docs/` o en los notebooks correspondientes.
