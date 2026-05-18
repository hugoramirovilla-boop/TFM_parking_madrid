# TFM Parking Madrid

Repositorio técnico del TFM orientado al modelado de la dificultad de aparcar en Madrid mediante integración de datos SER, aparcamientos públicos municipales/EMT y fuentes contextuales.

## Estructura

- `data/raw/`: datos originales descargados desde las fuentes oficiales, sin modificar.
- `data/interim/`: datos limpios o normalizados por fuente.
- `data/processed/`: tablas analíticas preparadas para análisis, modelado y visualización.
- `notebooks/`: notebooks reproducibles del proyecto.
- `src/`: funciones reutilizables de carga, limpieza, joins, features y visualización.
- `docs/`: decisiones metodológicas, catálogo de fuentes y registro de limpieza.
- `reports/`: figuras, tablas y materiales derivados para entregas.
- `archive/`: material legacy no usado directamente en el flujo principal.

## Alcance inicial

Prioridad core:

1. SER: tiques, calles/plazas, autorizaciones, parquímetros y zonas.
2. EMT: inventario de parkings, histórico horario de plazas y aparcamientos públicos municipales.
3. Contexto mínimo: calendario laboral.

Las fuentes complementarias se incorporarán solo si superan una revisión de cobertura, calidad y utilidad analítica.
