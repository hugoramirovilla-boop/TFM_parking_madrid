# TFM Parking Madrid

Repositorio del Trabajo Fin de Máster sobre el modelado de la dificultad relativa de aparcar en Madrid a partir de datos abiertos del Servicio de Estacionamiento Regulado (SER), aparcamientos públicos/off-street y cartografía.

El proyecto no se presenta como una aplicación final cerrada, sino como un pipeline metodológico trazable para integrar fuentes heterogéneas, evaluar su calidad, construir proxies explícitos y producir salidas cartográficas.

## Arquitectura conceptual

SER es el núcleo principal del TFM. La dificultad de aparcar en superficie no se observa directamente, por lo que se aproxima mediante señales de demanda pagada, capacidad, calendario y presión estructural.

EMT/off-street es una capa complementaria observable. Aporta un inventario integrado de aparcamientos y, cuando existe respuesta usable, disponibilidad viva procedente del servicio municipal de ocupación en tiempo real de aparcamientos rotacionales.

SER y off-street no se mezclan en un único modelo. Miden fenómenos distintos y se integran principalmente en la salida cartográfica, conservando referencias temporales y trazabilidad separadas.

## SER

La unidad analítica principal es barrio × intervalo temporal. La granularidad operativa seleccionada es 30 minutos.

La escala calle o parquímetro se mantiene como análisis complementario, no como unidad principal del modelo, porque una parte importante de los pagos digitales no puede localizarse de forma verificable a esa escala con las fuentes públicas disponibles.

El target histórico principal es `ocupacion_pagada_proxy`. Representa presión pagada relativa sobre capacidad-tiempo del barrio:

```text
minutos pagados solapados / (plazas SER del barrio × duración del intervalo)
```

No representa ocupación física total. Los tiques observan demanda pagada, no toda la demanda ni toda la disponibilidad. La ausencia de tiques no implica automáticamente facilidad para aparcar.

## Modelado

El baseline operativo seleccionado es `M0_historical_profile`. M0 utiliza perfiles históricos jerárquicos por barrio, día de semana e intervalo, con niveles de fallback hasta una media global cuando faltan perfiles más específicos.

La selección de M0 se basa en validación temporal y en su comportamiento como baseline operativo robusto. En el proceso se comparan modelos Ridge y variantes históricas, considerando rendimiento global y comportamiento en valores altos del target. M0 no debe interpretarse como el modelo con menor error en cualquier métrica aislada, sino como la opción operativa seleccionada por equilibrio metodológico, estabilidad y ausencia de leakage.

Las métricas construidas con tiques del intervalo objetivo no se usan como predictores operativos. La predicción debe utilizar información disponible ex ante.

## `prob_aparcar_proxy`

`prob_aparcar_proxy` es una escala relativa de facilidad, no una probabilidad empírica de encontrar plaza.

Se construye a partir de:

- predicción histórica M0;
- autorizaciones residentes activas;
- turismos Cero Emisiones del IVTM.

El índice ajustado de dificultad combina la señal histórica y la presión estructural:

```text
E = 0.75 * R + 0.25 * C
I = 0.60 * M + 0.40 * E
prob_aparcar_proxy = 1 - I
```

Equivalente:

```text
I = 0.60 * M + 0.30 * R + 0.10 * C
```

Las señales estructurales no reconstruyen ocupación real ausente. Solo aportan una lectura relativa adicional bajo supuestos explícitos.

## EMT/off-street

`emt_parkings` es la fuente ancla del inventario. Se integra con la fuente municipal de aparcamientos públicos para construir un inventario de entidades físicas con `parking_uid`.

Los identificadores EMT originales se conservan para trazabilidad y enlaces posteriores. Una entidad física puede tener varios identificadores EMT como alias. Las capacidades procedentes de alias o de fuentes con semántica distinta no se suman automáticamente.

El inventario integrado se guarda como salida procesada en `data/processed/core/emt/inventario_global_emt.parquet` cuando se dispone de los datos locales.

## Tiempo real municipal

La fuente viva es el servicio municipal de ocupación en tiempo real de aparcamientos rotacionales. No se atribuye exclusivamente a EMT, aunque el enlace técnico con el inventario use identificadores EMT cuando están disponibles.

La disponibilidad viva off-street:

- es parcial;
- corresponde al momento de consulta;
- no es predicción;
- no modifica `prob_aparcar_proxy`;
- no implica que la ausencia de respuesta o de cifra utilizable signifique aparcamiento completo.

El valor `freeParking < 0` se conserva como dato bruto no usable y no se interpreta como cero plazas libres.

## Visualización

El mapa final:

- representa `prob_aparcar_proxy` por barrio;
- incorpora cartografía SER limpia;
- incorpora aparcamientos off-street;
- muestra disponibilidad viva off-street cuando existe;
- mantiene separadas la referencia temporal SER y la referencia temporal de la consulta viva.

Las categorías visuales SER son reglas de interfaz:

- baja: `< 0.30`;
- media: `0.30-0.69`;
- alta: `>= 0.70`.

No son umbrales probabilísticos empíricos. Para disponibilidad off-street, cuando existe capacidad comparable, las categorías visuales son baja `0-29 %`, media `30-69 %` y alta `70-100 %`; la categorización no altera el número original de plazas libres.

## Módulos reutilizables

- `src/models/ser_historical_baseline.py`: carga, validación y aplicación de perfiles históricos M0.
- `src/models/ser_parking_proxy.py`: cálculo de `prob_aparcar_proxy` para todos los barrios SER o una selección, con ranking por facilidad o dificultad.
- `src/data/emt_realtime.py`: consulta SOAP, parseo, normalización y enlace técnico de la disponibilidad viva off-street con el inventario.
- `src/visualization/parking_map.py`: construcción de mapas SER, SER + off-street y SER proxy + disponibilidad viva.
- `src/pipelines/operational_map.py`: orquestación del flujo final: proxy SER, consulta viva independiente y mapa integrado.

## Flujo de notebooks

Orden principal del flujo actual:

1. `notebooks/02_01_ser_tiques.ipynb`
2. `notebooks/02_02_cartografia_ser.ipynb`
3. `notebooks/02_03_ser_oferta_espacial.ipynb`
4. `notebooks/02_04_ser_presion_estructural.ipynb`
5. `notebooks/02_05_contexto_calendario.ipynb`
6. `notebooks/02_06_emt_inventario.ipynb`
7. `notebooks/02_07_emt_tiempo_real.ipynb`
8. `notebooks/04_01_ser_joins_base.ipynb`
9. `notebooks/04_02_ser_panel_barrio_intervalo.ipynb`
10. `notebooks/04_03_ser_model_dataset_baseline.ipynb`
11. `notebooks/04_04_ser_prediccion_prob_aparcar_proxy.ipynb`
12. `notebooks/03_01_mapa_ser_cartografia.ipynb`
13. `notebooks/03_02_mapa_ser_emt_integrado.ipynb`
14. `notebooks/03_03_mapa_ser_prediccion_proxy.ipynb`
15. `notebooks/03_04_mapa_ser_emt_tiempo_real_proxy.ipynb`
16. `notebooks/03_05_orquestacion_mapa_operativo.ipynb`

Los notebooks cartográficos `03_01` a `03_05` consumen salidas previas y construyen mapas o validaciones de visualización.

## Estructura del repositorio

- `data/raw/`: datos originales no transformados, no versionados salvo estructura.
- `data/interim/`: datos limpios o normalizados por fuente, no versionados salvo estructura.
- `data/processed/`: salidas analíticas para joins, modelado o visualización, no versionadas salvo estructura.
- `docs/source_docs/`: documentos fuente usados como evidencia.
- `notebooks/`: notebooks activos de limpieza, integración, modelado y mapas.
- `src/`: código reutilizable.
- `reports/`: mapas, figuras y tablas generadas, con salidas pesadas no versionadas salvo las necesarias.
- `data_catalog.csv`: catálogo de fuentes, rutas, estados y notebooks relacionados.

## Fuentes y reutilización

Origen de los datos: Ayuntamiento de Madrid y, cuando corresponde, Empresa Municipal de Transportes de Madrid (EMT). Las fuentes, periodos de referencia y URLs oficiales utilizadas se documentan en `data_catalog.csv`. Los documentos conservados en `docs/source_docs/` sirven como respaldo documental de las fuentes empleadas.

## Reproducibilidad

El entorno se declara en `environment.yml`.

```bash
conda env create -f environment.yml
conda activate tfm-parking
```

Los notebooks utilizan el kernel `tfm-parking`, que puede registrarse con:

```bash
python -m ipykernel install --user --name tfm-parking --display-name "Python (tfm-parking)"
```

El repositorio público no incluye todos los datos pesados. Para reproducir el flujo completo es necesario descargar o disponer localmente de las fuentes originales indicadas en `data_catalog.csv` y respetar la estructura `data/raw`, `data/interim` y `data/processed`.

Clonar el repositorio no basta por sí solo para ejecutar el pipeline completo desde cero si los datos no están disponibles. La reproducibilidad documental se apoya en notebooks, catálogo y código; la reproducibilidad completa depende de las fuentes originales y de las salidas pesadas regenerables.

## Escritura de outputs

La orquestación operativa permite ejecutar el flujo con o sin escritura de salidas. El parámetro `write_outputs=True` puede guardar snapshots de tiempo real y salidas cartográficas. Una ejecución de tiempo real consulta el estado disponible en ese momento, por lo que sus resultados pueden cambiar entre ejecuciones.

Ejemplo sin escribir outputs:

```python
from src.pipelines.operational_map import build_operational_ser_emt_realtime_map

result = build_operational_ser_emt_realtime_map(
    scenario_datetime=None,
    write_outputs=False,
)
```

Ejemplo con escritura de outputs:

```python
result = build_operational_ser_emt_realtime_map(
    scenario_datetime=None,
    write_outputs=True,
)
```

## Limitaciones y extensiones futuras

- SER no observa ocupación total del estacionamiento regulado.
- Los tiques SER miden demanda pagada, no demanda total ni disponibilidad completa.
- `prob_aparcar_proxy` es una escala relativa, no una probabilidad empírica.
- La disponibilidad viva off-street es parcial y depende del momento de consulta.
- SER y off-street se integran cartográficamente, no como un único modelo estadístico.
- Clima, tráfico, incidencias, eventos y otras fuentes contextuales son posibles extensiones futuras o fuentes condicionadas; no forman parte de las features integradas en el núcleo actual.
