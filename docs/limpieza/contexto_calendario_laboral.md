# Interpretación — contexto_calendario_laboral

## Objetivo de la fuente

`contexto_calendario_laboral` se limpia como tabla temporal diaria auxiliar. No mide aparcamiento ni ocupación, pero permite segmentar días comparables y validar posteriormente cuándo la señal de tiques SER debería ser observable.

## Cobertura raw y cobertura analítica

El fichero raw cargado cubre una ventana más amplia, pero la salida limpia del TFM se restringe a 2023-2026.

- Fecha mínima de la salida limpia: `2023-01-01`
- Fecha máxima de la salida limpia: `2026-12-31`
- Años presentes en la salida limpia: `[2023, 2024, 2025, 2026]`
- Años esperados para el TFM: `[2023, 2024, 2025, 2026]`
- Años faltantes en 2023-2026: `[]`
- Filas finales: `1461`

## Calidad básica

- Fechas nulas: `0`
- Duplicados por fecha: `0`
- Checks críticos superados: `True`
- Checks de aviso superados: `True`

## Avisos detectados en 2023-2026

- Domingos reales no marcados explícitamente como `domingo` en `tipo_dia_raw`: `0`
- Festivos sin `tipo_festivo` ni `festividad`: `0`

Si ambos valores son 0, no hay anomalías relevantes dentro de la ventana analítica usada en el TFM.

## Distribución por tipo de régimen diario base

|   anio | tipo_regimen_ser_dia_base   |   n_dias |
|-------:|:----------------------------|---------:|
|   2023 | domingo                     |       53 |
|   2023 | festivo                     |       14 |
|   2023 | laborable                   |      246 |
|   2023 | sabado                      |       52 |
|   2024 | domingo                     |       52 |
|   2024 | festivo                     |       14 |
|   2024 | laborable                   |      251 |
|   2024 | sabado                      |       49 |
|   2025 | domingo                     |       52 |
|   2025 | festivo                     |       14 |
|   2025 | laborable                   |      249 |
|   2025 | sabado                      |       50 |
|   2026 | domingo                     |       52 |
|   2026 | festivo                     |       14 |
|   2026 | laborable                   |      249 |
|   2026 | sabado                      |       50 |

## Interpretación metodológica

La fuente es apta para construir una dimensión diaria porque supera los checks críticos: no hay fechas nulas, no hay duplicados por fecha, cubre el periodo 2023-2026 y la fecha real es coherente con el día de semana declarado.

La tabla permite validar la ausencia o caída de tiques en domingos y festivos sin interpretar automáticamente esa ausencia como baja dificultad de aparcamiento. Para el régimen SER horario completo no basta esta tabla diaria: la validación fina debe hacerse después en `ser_tiques`, cruzando fecha y hora del tique.

## Salidas generadas

- Tabla limpia: `data/interim/contexto/contexto_calendario_laboral/contexto_calendario_laboral_clean.parquet`
- Checks de calidad: `reports/tables/contexto_calendario_laboral_quality_checks.csv`
- Diagnóstico de anomalías: no generado, porque no hay anomalías en la ventana 2023-2026
