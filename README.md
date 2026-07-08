# TFM Parking Madrid

Repositorio del Trabajo Fin de Máster sobre el **modelado de la dificultad de aparcar en Madrid** a partir de datos abiertos de estacionamiento regulado en superficie, aparcamientos EMT/off-street, contexto urbano y cartografía.

El objetivo del proyecto es construir un **pipeline reproducible de datos, análisis, proxies operativos y salidas cartográficas**. El repositorio no se presenta como producto software final, sino como una base metodológica trazable para integrar fuentes heterogéneas, evaluar su calidad y producir evidencias sobre dificultad de aparcamiento.

## Arquitectura conceptual

El proyecto mantiene separadas las fuentes según su papel analítico:

- **SER** es el núcleo principal. La dificultad de aparcar en superficie no se observa directamente, por lo que se aproxima mediante proxies construidos desde demanda pagada, capacidad física, régimen temporal y presión estructural.
- **EMT/off-street** es una capa complementaria observable. Aporta inventario integrado de aparcamientos y una fuente viva parcial de plazas libres informadas cuando la API devuelve dato usable.
- **Cartografía** es la salida común. Las capas SER y EMT se integran principalmente en mapas, conservando la trazabilidad de cada fuente.
- **Contexto** se incorpora solo cuando aporta señal, cobertura y calidad suficiente. En el estado actual, el calendario laboral apoya la lectura temporal SER.

No se fuerza un único modelo que mezcle SER y EMT. SER y EMT miden fenómenos distintos y se combinan con cautela mediante lectura espacial, temporal y cartográfica. El proyecto no observa ocupación SER plaza por plaza; construye proxies reproducibles y explícitos.

## Estado SER

El bloque SER ya contiene las piezas principales del flujo histórico y operativo:

- `02_01_ser_tiques.ipynb`: limpieza de tiques SER.
- `02_02_cartografia_ser.ipynb`: cartografía limpia SER.
- `02_03_ser_oferta_espacial.ipynb`: oferta espacial SER, centrada en `ser_calles_plazas` y `ser_parquimetros`.
- `02_04_ser_presion_estructural.ipynb`: presión estructural a partir de autorizaciones SER e IVTM.
- `02_05_contexto_calendario.ipynb`: calendario laboral.
- `04_01_ser_joins_base.ipynb`: base SER depurada a escala barrio.
- `04_02_ser_panel_barrio_intervalo.ipynb`: panel SER barrio-intervalo y selección de granularidad final de 30 minutos.
- `04_03_ser_model_dataset_baseline.ipynb`: dataset de modelado y baseline histórico `M0_historical_profile`.
- `04_04_ser_prediccion_prob_aparcar_proxy.ipynb`: predicción operativa de `prob_aparcar_proxy`.

La unidad principal es **barrio x intervalo temporal**. El target histórico principal es `ocupacion_pagada_proxy`, calculado como señal de presión pagada sobre capacidad-tiempo del barrio. Esta variable no mide ocupación total del estacionamiento regulado: mide intensidad de uso pagado observada en los tiques disponibles.

El modelo `M0_historical_profile` actúa como baseline operativo reutilizable. Se conserva como perfiles históricos y metadatos en `data/processed/core/ser/modeling/`, y se usa como base conservadora para escenarios cartográficos.

La salida `prob_aparcar_proxy` es una escala proxy relativa de facilidad, derivada de la lectura inversa de un índice ajustado de dificultad SER. No debe leerse como una probabilidad observada de encontrar plaza.

La escala calle/parquímetro queda como análisis complementario parcial, no como producto principal. Los pagos app/digitales no se imputan a parquímetro, calle o tramo cuando la fuente pública no permite esa localización.

## Estado EMT/off-street

El bloque EMT/off-street ya contiene:

- `02_06_emt_inventario.ipynb`: inventario integrado EMT/municipal.
- `02_07_emt_tiempo_real.ipynb`: fuente viva parcial EMT tiempo real.
- `src/data/emt_realtime.py`: módulo reusable para consultar, normalizar y unir la respuesta viva con el inventario.

El inventario integrado se guarda en `data/processed/core/emt/inventario_global_emt.parquet`. Actúa como capa cartográfica off-street y tabla de referencia para joins con identificadores EMT.

EMT tiempo real se consulta mediante API SOAP manual. Es una capa viva parcial, no un modelo ni una previsión. No tiene cobertura completa de todos los aparcamientos, y la ausencia de dato vivo no implica aparcamiento lleno ni vacío.

En la normalización:

- `free_valid` representa plazas libres informadas por la API cuando el dato es usable.
- `free_raw < 0` no se interpreta como plazas libres efectivas.

## Mapas y visualización

El bloque cartográfico ya incluye:

- `03_01_mapa_ser_cartografia.ipynb`: mapa cartográfico SER.
- `03_02_mapa_ser_emt_integrado.ipynb`: mapa integrado SER + EMT/off-street.
- `03_03_mapa_ser_prediccion_proxy.ipynb`: mapa SER + EMT con capa `prob_aparcar_proxy`.
- `03_04_mapa_ser_emt_tiempo_real_proxy.ipynb`: mapa integrado SER proxy + EMT tiempo real.
- `03_05_orquestacion_mapa_operativo.ipynb`: ejecución orquestada del mapa operativo integrado.

Salidas principales conocidas:

- `reports/maps/mapa_ser_cartografia.html`
- `reports/maps/mapa_ser_emt_integrado.html`
- `reports/maps/mapa_ser_emt_prediccion_proxy.html`
- `reports/maps/mapa_integrado_ser_proxy_emt_tiempo_real.html`
- `reports/figures/ser_cartografia/`
- `reports/figures/ser_emt_integrado/`
- `reports/figures/ser_prediccion_proxy/`
- `reports/figures/emt_tiempo_real/`

Estas salidas integran capas con naturalezas distintas. La integración SER + EMT es cartográfica: SER aporta proxy por barrio e intervalo; EMT aporta inventario off-street y, cuando está disponible, plazas libres informadas en vivo.

## Código reusable

Módulos principales reutilizables:

- `src/models/ser_historical_baseline.py`: carga, validación y uso de perfiles históricos `M0_historical_profile`.
- `src/models/ser_parking_proxy.py`: construcción de `prob_aparcar_proxy` SER para escenarios operativos.
- `src/data/emt_realtime.py`: consulta SOAP, parseo, normalización y join de EMT tiempo real.
- `src/visualization/parking_map.py`: construcción de mapas SER, SER + EMT y SER proxy + EMT tiempo real.
- `src/pipelines/operational_map.py`: orquestación del flujo final SER proxy + EMT tiempo real + mapa integrado.

`src/pipelines/operational_map.py` coordina el flujo final:

- calcula el proxy SER para la hora solicitada;
- consulta EMT tiempo real en vivo;
- genera el mapa integrado;
- permite activar o desactivar la escritura de outputs.

Ejemplo mínimo con escritura de outputs:

```python
from src.pipelines.operational_map import build_operational_ser_emt_realtime_map

result = build_operational_ser_emt_realtime_map(
    scenario_datetime=None,
    write_outputs=True,
)
```

Ejemplo sin escribir outputs:

```python
result = build_operational_ser_emt_realtime_map(
    scenario_datetime=None,
    write_outputs=False,
)
```

## Estructura del repositorio

- `data/raw/`: datos originales descargados o incorporados sin transformar.
- `data/interim/`: datos limpios o normalizados por fuente.
- `data/processed/`: salidas analíticas preparadas para joins, modelado o visualización.
- `docs/`: decisiones metodológicas y documentación técnica viva.
- `docs/source_docs/`: documentos fuente usados como evidencia interna.
- `notebooks/`: notebooks activos de limpieza, integración, diagnóstico, modelado y mapas.
- `src/`: código reusable del proyecto.
- `reports/`: tablas, figuras, mapas y revisiones generadas durante el trabajo.

## Notebooks activos

- `02_01_ser_tiques.ipynb`
- `02_02_cartografia_ser.ipynb`
- `02_03_ser_oferta_espacial.ipynb`
- `02_04_ser_presion_estructural.ipynb`
- `02_05_contexto_calendario.ipynb`
- `02_06_emt_inventario.ipynb`
- `02_07_emt_tiempo_real.ipynb`
- `03_01_mapa_ser_cartografia.ipynb`
- `03_02_mapa_ser_emt_integrado.ipynb`
- `03_03_mapa_ser_prediccion_proxy.ipynb`
- `03_04_mapa_ser_emt_tiempo_real_proxy.ipynb`
- `03_05_orquestacion_mapa_operativo.ipynb`
- `04_01_ser_joins_base.ipynb`
- `04_02_ser_panel_barrio_intervalo.ipynb`
- `04_03_ser_model_dataset_baseline.ipynb`
- `04_04_ser_prediccion_prob_aparcar_proxy.ipynb`

## Outputs principales actuales

Outputs SER, EMT y cartografía confirmados:

- base SER depurada a escala barrio en `data/processed/core/ser/ser_tiques_barrio_base/`;
- capacidad anual SER por barrio en `data/processed/core/ser/ser_barrio_capacidad_anio.parquet`;
- panel SER barrio-intervalo final en `data/processed/core/ser/ser_barrio_intervalo_global_final.parquet`;
- artefactos M0 en `data/processed/core/ser/modeling/ser_m0_selected_profiles.parquet` y `data/processed/core/ser/modeling/ser_m0_selected_model_metadata.json`;
- cartografía limpia en `data/interim/cartografia/`;
- oferta espacial SER limpia en `data/interim/ser/ser_calles_plazas/` y `data/interim/ser/ser_parquimetros/`;
- autorizaciones e IVTM limpios como presión estructural en `data/interim/ser/`;
- calendario laboral limpio en `data/interim/contexto/`;
- inventario global EMT en `data/processed/core/emt/inventario_global_emt.parquet`;
- mapa de identificadores EMT en `data/processed/core/emt/parking_id_map.parquet`;
- snapshot y join vivo EMT, cuando se escribe, en `data/interim/emt/emt_aparcamientos_rotacionales_tiempo_real/`;
- mapas HTML en `reports/maps/`;
- figuras asociadas en `reports/figures/ser_cartografia/`, `reports/figures/ser_emt_integrado/`, `reports/figures/ser_prediccion_proxy/` y `reports/figures/emt_tiempo_real/`.

## Reproducibilidad

Las rutas se gestionan desde la raíz del repositorio y se contrastan con `data_catalog.csv`, que mantiene la trazabilidad entre fuente, archivo raw, salida intermedia, salida procesada, notebook generador y riesgos conocidos.

El flujo distingue entre:

- datos originales en `data/raw/`;
- tablas limpias o normalizadas en `data/interim/`;
- salidas analíticas en `data/processed/`;
- salidas cartográficas y visuales en `reports/`.

Algunos datos pesados pueden no estar versionados. Este README no incluye instrucciones de instalación porque no hay un fichero de entorno validado en el repositorio.

## Limitaciones

- SER no observa ocupación total del estacionamiento regulado.
- Los tiques SER miden demanda pagada, no demanda total ni disponibilidad completa.
- La ausencia de tiques no implica facilidad efectiva para aparcar.
- `ocupacion_pagada_proxy` depende de tiques observados, capacidad anual por barrio y régimen temporal.
- `prob_aparcar_proxy` depende de supuestos metodológicos explícitos y debe leerse como escala relativa.
- Los pagos app/digitales no son localizables públicamente a parquímetro, calle o tramo.
- EMT tiempo real tiene cobertura parcial y puede cambiar entre ejecuciones porque procede de una API viva.
- La ausencia de dato vivo EMT no indica por sí sola disponibilidad ni saturación.
- `free_raw < 0` no se interpreta como plazas libres efectivas.
- SER proxy y EMT tiempo real tienen naturalezas temporales distintas: SER es un escenario calculado por intervalo; EMT es una observación viva parcial.
- La integración SER + EMT es cartográfica, no un único modelo estadístico.
