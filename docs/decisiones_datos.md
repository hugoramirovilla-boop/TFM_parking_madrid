# Decisiones de datos

## 2026-05-18 — Catálogo inicial de fuentes

Se crea `data_catalog.csv` como catálogo operativo vivo para registrar las fuentes utilizadas en el TFM, su prioridad analítica, modo de acceso, periodo objetivo, archivos raw/interim y estado de procesamiento.

El catálogo inicial contiene las fuentes necesarias para arrancar el core SER + EMT + calendario. Las fuentes complementarias se añadirán progresivamente cuando se aborde su descarga, validación y limpieza.

## 2026-05-28 — Arquitectura de notebooks para descripción y limpieza de datos

Se decide dividir la fase de descripción y limpieza en un notebook central y varios notebooks específicos por bloque funcional.

El notebook central `02_descripcion_limpieza_datos.ipynb` no genera datos `interim`. Su función es controlar el estado global del pipeline: cargar el catálogo, validar existencia de `raw`, inspeccionar formatos/esquemas, detectar incidencias comunes y asignar cada fuente al notebook de limpieza correspondiente.

La limpieza específica se organizará así:

- `02_01_ser_tiques.ipynb`: limpieza individual de tiques SER.
- `02_02_ser_oferta_espacial.ipynb`: limpieza de calles/plazas, zonas SER y parquímetros.
- `02_03_ser_presion_estructural.ipynb`: limpieza de autorizaciones SER e IVTM por barrio.
- `02_04_emt_inventario.ipynb`: limpieza de inventario EMT y aparcamientos públicos municipales.
- `02_05_emt_historico.ipynb`: limpieza de históricos de ocupación EMT.
- `02_06_contexto_calendario.ipynb`: limpieza del calendario laboral.

En esta fase no se construirán joins finales, `SER_barrio_intervalo`, `SER_calle_intervalo`, `inventario_global_emt`, métricas proxy ni modelos. Esos pasos quedan para fases posteriores, una vez las fuentes individuales estén limpias y validadas.

