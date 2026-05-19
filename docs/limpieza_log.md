
## 2026-05-19 — Descarga inicial de fuentes core

Se descargan las fuentes core iniciales SER, EMT y calendario laboral mediante `src/data/download_sources.py`.

Incidencia detectada en `emt_ocupacion_hora`:
- Los CSV oficiales de diciembre 2025, enero 2026 y febrero 2026 devolvían error 404 en el portal.
- Se descargaron manualmente los formatos TSV oficiales alternativos para esos tres meses.
- En limpieza se normalizarán junto con los CSV restantes.

Nota temporal:
- El fichero de mayo 2026 de `emt_ocupacion_hora` puede estar incompleto, ya que la descarga se realizó el 19/05/2026.
- Cuando cierre mayo, conviene volver a descargar ese mes y sustituir el raw correspondiente.
