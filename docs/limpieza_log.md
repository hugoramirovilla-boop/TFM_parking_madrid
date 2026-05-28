
## 2026-05-19 — Descarga inicial de fuentes core

Se descargan las fuentes core iniciales SER, EMT y calendario laboral mediante `src/data/download_sources.py`.

Nota temporal:
- El fichero de mayo 2026 de `emt_ocupacion_hora` puede estar incompleto, ya que la descarga se realizó el 19/05/2026.
- Cuando cierre mayo, conviene volver a descargar ese mes y sustituir el raw correspondiente.

## 2026-05-28 — Inicio de limpieza por bloques funcionales

Tras ejecutar el notebook central de descripción y limpieza, se confirma que el catálogo apunta a 11 datasets y que todas las fuentes tienen archivos localizados en `data/raw`.

Observaciones globales pendientes para notebooks específicos:

- `ser_tiques`: fuente pesada y comprimida; requiere procesamiento por ZIP/trimestre.
- `ser_calles_plazas`: presenta cambios de esquema entre años.
- `ser_autorizaciones`: presenta cambios de esquema entre años.
- `ser_parquimetros`: combina CSV tabular y KMZ geográfico.
- `ser_padron_vehiculos_ivtm_barrio`: lectura inicial homogénea, pero requiere validación de etiqueta ambiental 0, códigos de barrio y agregación anual.
- `emt_aparcamientos_publicos`: el CSV es usable; el JSON requiere revisión específica si se decide usarlo.
- `emt_ocupacion_hora`: algunos CSV aparecen con lectura automática de 0 columnas; requiere revisión de separador, cabecera, encoding o contenido.
- `emt_ocupacion_mensual_rotacional`: requiere lector específico por errores de parsing.
- `contexto_calendario_laboral`: lectura inicial homogénea; requiere limpieza temporal y generación de variables de calendario.

