# TFM Parking Madrid

Repositorio del Trabajo Fin de Máster sobre el **modelado de la dificultad de aparcar en Madrid** a partir de datos abiertos de estacionamiento regulado en superficie, aparcamientos EMT/off-street, contexto urbano y cartografía.

El objetivo del proyecto es construir un **pipeline reproducible de datos, análisis y salidas cartográficas**. No se plantea como una aplicación final, sino como una base metodológica para integrar fuentes heterogéneas, evaluar su calidad y producir evidencias trazables sobre dificultad de aparcamiento.

## Arquitectura conceptual

El proyecto separa las fuentes según su papel analítico:

- **SER** es el núcleo principal. La dificultad de aparcar en superficie no se observa directamente, por lo que se aproxima mediante una variable proxy construida desde demanda pagada, capacidad física, régimen temporal y presión estructural.
- **EMT/off-street** es una capa complementaria observable. Aporta inventario y, si se desarrolla posteriormente, series de ocupación de aparcamientos fuera de superficie.
- **Contexto** entra solo si aporta señal, cobertura y calidad suficiente. Clima, tráfico, incidencias y afecciones no son core automático.
- **Cartografía** es la salida común: las capas SER, EMT y contextuales deben poder representarse espacialmente y conservar trazabilidad.

No se fuerza un único modelo SER + EMT. SER y EMT miden fenómenos distintos y deben combinarse con cautela, principalmente mediante lectura espacial, temporal y cartográfica.

## Estado actual del enfoque SER

La salida robusta inicial prevista para el bloque SER es `SER_barrio_intervalo` / `SER_barrio_hora`, no una salida fina por calle como producto principal. El notebook `04_01_ser_joins_base.ipynb` prepara la base SER a escala barrio y genera la capacidad anual por barrio. En el árbol actual existen:

- `data/processed/core/ser/ser_tiques_barrio_base/`
- `data/processed/core/ser/ser_barrio_capacidad_anio.parquet`

El panel final `SER_barrio_intervalo` todavía no está cerrado. La escala calle/parquímetro queda como análisis complementario parcial, aplicable solo a tiques con identificador físico suficiente. Los pagos digitales o de aplicación móvil no deben imputarse a parquímetro, calle o tramo si la fuente pública no permite esa localización.

## Cartografía SER

La cartografía SER limpia procede de `02_02_cartografia_ser.ipynb` y se apoya en:

- `ser_geoportal_limite_ser`
- `ser_geoportal_barrios_ser`
- `ser_geoportal_bandas_aparcamiento`
- `callejero_viales_vigentes`

El notebook `02_03_ser_oferta_espacial.ipynb` queda centrado en las fuentes tabulares y puntuales de oferta SER:

- `ser_calles_plazas`
- `ser_parquimetros`

El notebook `03_01_mapa_ser_cartografia.ipynb` usa estas capas para generar mapas y controles visuales del ámbito SER.

## Estado actual EMT

El notebook `02_06_emt_inventario.ipynb` construye el inventario integrado `inventario_global_emt`, disponible en:

- `data/processed/core/emt/inventario_global_emt.parquet`
- `data/processed/core/emt/parking_id_map.parquet`

EMT se trata como capa off-street complementaria. El repositorio contiene fuentes raw de ocupación horaria y mensual, pero el README no asume que exista ya una previsión EMT ni una integración operativa de API en tiempo real.

## Contexto

El notebook `02_05_contexto_calendario.ipynb` limpia el calendario laboral y genera una tabla diaria auxiliar. Su informe interpretativo se muestra en pantalla durante la ejecución del notebook; no se mantiene como documento de limpieza independiente. El calendario sirve para validar regímenes temporales y días comparables, pero no mide aparcamiento ni ocupación.

Otras fuentes de contexto solo deben incorporarse si superan criterios de calidad, cobertura temporal y utilidad analítica para el target SER o para la lectura cartográfica.

## Estructura del repositorio

- `data/raw/`: datos originales descargados o incorporados sin transformar.
- `data/interim/`: datos limpios o normalizados por fuente.
- `data/processed/`: salidas analíticas preparadas para joins, modelado o visualización.
- `docs/`: decisiones metodológicas y documentación técnica viva.
- `docs/source_docs/`: documentos fuente usados como evidencia interna.
- `notebooks/`: notebooks activos de limpieza, integración, diagnóstico y mapas.
- `src/`: código auxiliar del proyecto, incluyendo scripts reutilizables.
- `reports/`: tablas, figuras, mapas y revisiones generadas durante el trabajo.

## Notebooks activos

- `02_01_ser_tiques.ipynb`
- `02_02_cartografia_ser.ipynb`
- `02_03_ser_oferta_espacial.ipynb`
- `02_04_ser_presion_estructural.ipynb`
- `02_05_contexto_calendario.ipynb`
- `02_06_emt_inventario.ipynb`
- `03_01_mapa_ser_cartografia.ipynb`
- `04_01_ser_joins_base.ipynb`

## Outputs principales actuales

Outputs SER y cartografía confirmados en el árbol actual:

- base SER depurada a escala barrio, particionada, en `data/processed/core/ser/ser_tiques_barrio_base/`;
- capacidad anual SER por barrio en `data/processed/core/ser/ser_barrio_capacidad_anio.parquet`;
- capas cartográficas limpias en `data/interim/cartografia/`;
- oferta espacial SER limpia en `data/interim/ser/ser_calles_plazas/` y `data/interim/ser/ser_parquimetros/`;
- autorizaciones e IVTM limpios como presión estructural en `data/interim/ser/`;
- calendario laboral limpio en `data/interim/contexto/`;
- inventario global EMT y mapa de identificadores en `data/processed/core/emt/`;
- mapas SER en `reports/maps/` y figuras asociadas en `reports/figures/`.

No se declara como existente ningún panel final de modelado SER ni modelo predictivo entrenado.

## Reproducibilidad

Las rutas se gestionan desde la raíz del repositorio y se contrastan con `data_catalog.csv`, que mantiene la trazabilidad entre fuente, archivo raw, salida intermedia, salida procesada, notebook generador y riesgos conocidos.

El flujo distingue entre:

- datos originales en `data/raw/`;
- tablas limpias o normalizadas en `data/interim/`;
- salidas analíticas en `data/processed/`.

Algunos datos pesados pueden no estar versionados. Este README no incluye instrucciones de instalación porque no hay un fichero de entorno validado en el repositorio.

## Limitaciones

- Los tiques SER miden demanda pagada, no ocupación real total.
- La ausencia de tiques no equivale a facilidad para aparcar.
- Los pagos digitales no son localizables públicamente a parquímetro, calle o tramo.
- EMT/off-street no debe mezclarse forzadamente con SER: representa otra capa de oferta y ocupación.
- Las fuentes de contexto solo entran si superan criterios de calidad, cobertura y señal.
- La granularidad fina por calle o parquímetro tiene incertidumbre espacial y no debe presentarse como verdad observada.

## Pendiente de cierre

- panel `SER_barrio_intervalo`;
- comparación final de granularidades 15/30/45/60 minutos;
- target proxy definitivo;
- modelado predictivo SER, si procede;
- previsión EMT, si procede;
- mapa integrado SER + EMT final.
