# Checklist operativo: limpieza del bloque SER

## 1. Objetivo

Este documento traduce `docs/decision_SER_target_proxy.md` en una guía operativa para limpiar y preparar las fuentes SER.

El objetivo no es hacer un EDA completo ni calcular todavía el target proxy final. El flujo correcto es:

1. limpiar cada fuente SER individualmente;
2. validar que las fuentes limpias conectan entre sí;
3. construir las tablas integradas `SER_barrio_intervalo` y `SER_calle_intervalo`;
4. calcular las métricas del proxy solo cuando las fuentes estén limpias y conectadas.

La limpieza debe dejar preparadas las fuentes para calcular posteriormente:

- ocupacion_pagada_proxy;
- stock_inicio_proxy;
- stock_fin_proxy;
- afluencia_tiques;
- liberacion_tiques;
- saldo_flujo;
- rotacion_bruta;
- persistencia_completa.

---

## 2. Flujo temporal obligatorio

| Fase | Qué se hace | Qué NO se hace todavía |
|---|---|---|
| 1. Limpieza individual | limpiar cada fuente por separado | no calcular target proxy |
| 2. Validación de joins | comprobar claves espaciales/temporales entre fuentes limpias | no entrenar modelos |
| 3. Construcción de panel SER | crear `SER_barrio_intervalo` y, si procede, `SER_calle_intervalo` | no asumir que calle es exacta |
| 4. Cálculo de métricas | calcular ocupación, flujos, rotación y persistencia | no convertir aún en clases finales |
| 5. EDA y sensibilidad | comparar 15/30/45/60 min y revisar flags | no fijar granularidad sin evidencia |

Regla principal: primero fuentes limpias, después joins, después panel, después proxy.

---

## 3. Fuentes SER implicadas

| Fuente | Papel en el bloque SER |
|---|---|
| SER tiques | señal temporal principal |
| SER calles y número de plazas | capacidad física / denominador |
| SER parquímetros | refinamiento espacial hacia calle |
| SER autorizaciones | presión estructural no observada |
| Zonas SER/cartografía | delimitación y validación espacial |
| IVTM padrón vehículos por barrio | presión estructural potencial de vehículos no observados por tiques, anual y por barrio |

---

## 4. Limpieza individual por fuente

### 4.1. SER tiques de aparcamiento

#### Objetivo de limpieza

Construir una tabla limpia de eventos de tique, con fechas válidas, duración coherente, barrio/distrito usable y matrícula de parquímetro preparada para joins.

#### Validaciones mínimas

Comprobar:

- existencia de `fecha_inicio`, `fecha_fin`, `minutos_tique`;
- parseo correcto de fechas y horas;
- `fecha_fin >= fecha_inicio`;
- tiques con duración 0;
- tiques con duración excesiva;
- duplicados exactos;
- nulos en barrio, distrito o código de barrio;
- nulos en `matricula_parquimetro`;
- coherencia entre `minutos_tique` y diferencia `fecha_fin - fecha_inicio`;
- cobertura temporal por año, trimestre, mes y día;
- presencia de tiques en días/horas sin régimen SER comparable.

#### Columnas mínimas preparadas

- `fecha`
- `anio`
- `mes`
- `dia`
- `hora_inicio`
- `hora_fin`
- `duracion_min_calculada`
- `cod_distrito`
- `distrito`
- `cod_barrio`
- `barrio`
- `matricula_parquimetro`
- `tipo_zona`
- `importe_tique`
- `flag_duracion_invalida`
- `flag_fecha_invalida`
- `flag_fuera_regimen_SER`
- `flag_matricula_parquimetro_nula`
- `archivo_origen`
- `dataset_id`

#### Salida esperada

Tabla limpia de eventos:

- `data/interim/ser/ser_tiques/ser_tiques_clean.parquet`

Esta salida todavía no debe contener las métricas del proxy. Solo debe dejar los tiques listos para agregación posterior por intervalo.

---

### 4.2. SER calles y número de plazas

#### Objetivo de limpieza

Construir la oferta física SER limpia y armonizada, tanto a nivel barrio como a nivel calle/tramo si la fuente lo permite.

#### Validaciones mínimas

Comprobar:

- columnas de barrio, distrito, calle y número de plazas;
- cambios de esquema entre años;
- plazas nulas;
- plazas igual a 0;
- valores negativos;
- duplicados por calle/tramo;
- coherencia de colores/tipos de plaza;
- coordenadas válidas si existen;
- cobertura temporal disponible;
- si los códigos de barrio son comparables con tiques, autorizaciones e IVTM.

#### Columnas mínimas preparadas

- `anio`
- `cod_distrito`
- `distrito`
- `cod_barrio`
- `barrio`
- `calle`
- `numero_finca`
- `color`
- `bateria_linea`
- `numero_plazas`
- `flag_plazas_nulas`
- `flag_plazas_cero`
- `flag_esquema_anio`
- `archivo_origen`
- `dataset_id`

#### Agregados esperados

- `oferta_SER_barrio`
- `oferta_SER_calle`

Variables:

- `plazas_SER_barrio`
- `plazas_SER_calle`
- `plazas_verdes_barrio`
- `plazas_azules_barrio`
- `plazas_alta_rotacion_barrio`
- `plazas_verdes_calle`
- `plazas_azules_calle`

#### Salidas esperadas

- `data/interim/ser/ser_calles_plazas/ser_calles_plazas_clean.parquet`
- `data/interim/ser/ser_calles_plazas/oferta_SER_barrio.parquet`
- `data/interim/ser/ser_calles_plazas/oferta_SER_calle.parquet`

Sin esta fuente, los tiques solo miden actividad pagada, no dificultad relativa.

---

### 4.3. SER parquímetros

#### Objetivo de limpieza

Construir una tabla limpia de parquímetros para aproximar la posición espacial del tique y evaluar si es viable bajar de barrio a calle.

#### Validaciones mínimas

Comprobar:

- existencia de `matricula`;
- duplicados de matrícula;
- coordenadas válidas;
- barrio/distrito/calle;
- `fecha_de_alta`;
- `fecha_de_baja`;
- parquímetros dados de baja;
- registros con calle `SIN ASIGNAR`;
- porcentaje de tiques enlazables por `matricula_parquimetro`.

#### Columnas mínimas preparadas

- `matricula`
- `fecha_de_alta`
- `fecha_de_baja`
- `cod_distrito`
- `distrito`
- `cod_barrio`
- `barrio`
- `calle`
- `numero_finca`
- `latitud`
- `longitud`
- `flag_calle_sin_asignar`
- `flag_parquimetro_sin_coordenadas`
- `archivo_origen`
- `dataset_id`

#### Salida esperada

- `data/interim/ser/ser_parquimetros/ser_parquimetros_clean.parquet`

#### Uso posterior

Esta fuente permitirá validar:

- tique → parquímetro;
- parquímetro → calle;
- calle → capacidad SER.

La salida `SER_calle_intervalo` solo debe construirse si el porcentaje de asignación es suficiente y las inconsistencias espaciales son aceptables.

---

### 4.4. SER autorizaciones

#### Objetivo de limpieza

Construir una tabla de presión estructural no observada por tiques, principalmente residentes y comerciales, agregada por barrio y periodo.

#### Validaciones mínimas

Comprobar:

- periodo disponible;
- ruptura de esquema antes/después de 2023;
- tipo de autorización;
- subtipo;
- estado;
- fechas de activación, inicio de periodo y vigencia;
- barrio/distrito;
- registros con barrio `CUALQUIERA` o nulo;
- duplicados administrativos;
- autorizaciones activas por periodo.

#### Columnas mínimas preparadas

- `periodo`
- `anio`
- `mes`
- `cod_distrito`
- `distrito`
- `cod_barrio`
- `barrio`
- `tipo_autorizacion`
- `subtipo_autorizacion`
- `estado`
- `fecha_inicio_periodo`
- `fecha_activacion`
- `fecha_vigencia`
- `flag_barrio_cualquiera`
- `flag_autorizacion_sin_barrio`
- `archivo_origen`
- `dataset_id`

#### Agregados esperados

- `autorizaciones_residentes_barrio_periodo`
- `autorizaciones_comerciales_barrio_periodo`
- `autorizaciones_activas_barrio_periodo`
- `autorizaciones_por_plaza`
- `ratio_residente_comercial`

#### Salida esperada

- `data/interim/ser/ser_autorizaciones/ser_autorizaciones_clean.parquet`
- `data/interim/ser/ser_autorizaciones/autorizaciones_SER_barrio_periodo.parquet`

Las autorizaciones no explican variación intradía, pero sí capturan presión estructural no observada por los tiques.

---

### 4.5. Zonas SER y cartografía

#### Objetivo de limpieza

Construir una capa limpia para delimitar el ámbito SER y validar espacialmente calles, tramos y mapas.

#### Validaciones mínimas

Comprobar:

- cobertura espacial;
- campos de vía/tramo/zona SER;
- duplicados por tramo;
- coherencia con calles/plazas;
- posibilidad de join con calle o tramo;
- utilidad para mapas;
- registros sin zona SER o con tramo ambiguo.

#### Columnas mínimas preparadas

- `codigo_via`
- `nombre_via`
- `tipo_tramo`
- `numero_inicial`
- `numero_final`
- `zona_SER_tramo`
- `flag_tramo_ambiguo`
- `flag_sin_zona_SER`
- `archivo_origen`
- `dataset_id`

#### Salida esperada

- `data/interim/ser/ser_zonas/ser_zonas_clean.parquet`

Esta fuente no aporta señal temporal ni capacidad. Sirve para delimitar, validar y mapear.

---

### 4.6. IVTM padrón de vehículos por barrio

#### Identificación

- `dataset_id`: `ser_padron_vehiculos_ivtm_barrio`
- nombre: IVTM padrón de vehículos por barrio - distintivo ambiental
- fuente: Ayuntamiento de Madrid, dataset 204278
- prioridad: complementaria_v1
- unidad espacial: barrio
- granularidad temporal: anual
- cobertura objetivo: 2023-2025

#### Objetivo de limpieza

Construir una tabla anual por barrio que aproxime presión estructural potencial de vehículos no observados por los tiques, usando como señal complementaria los vehículos con distintivo ambiental `0`.

Esta fuente no mide ocupación real ni presencia horaria en calle. No debe usarse como target ni sustituir a los tiques SER.

#### Archivos esperados

Comprobar existencia y lectura de:

- `ser_padron_vehiculos_ivtm_barrio__2023.csv`
- `ser_padron_vehiculos_ivtm_barrio__2024.csv`
- `ser_padron_vehiculos_ivtm_barrio__2025.csv`

#### Validaciones mínimas

Comprobar:

- año del nombre de archivo coincide con `EJERCICIO`;
- años presentes exactamente 2023, 2024 y 2025;
- columnas esperadas;
- `CONTADOR` numérico, entero y no negativo;
- valores únicos de `ETIQUETA_MEDIOAMBIENTAL`;
- presencia de etiqueta `0` en todos los años;
- registros con `COD_BARRIO = 0` o barrio no asignado;
- consistencia entre etiqueta y clasificación ambiental;
- duplicados exactos;
- códigos de barrio comparables con tiques, plazas y autorizaciones.

#### Columnas mínimas normalizadas

- `anio`
- `cod_distrito`
- `distrito`
- `cod_barrio`
- `barrio`
- `cod_tipo_vehiculo`
- `tipo_vehiculo`
- `etiqueta_medioambiental`
- `clasificacion_ambiental`
- `tipo_carburante`
- `contador`
- `flag_cod_barrio_0`
- `archivo_origen`
- `dataset_id`

#### Agregados esperados

Tabla final a nivel:

- `anio × cod_distrito × distrito × cod_barrio × barrio`

Variables:

- `n_vehiculos_total_barrio`
- `n_vehiculos_distintivo_0`
- `n_turismos_distintivo_0`
- `peso_distintivo_0_sobre_total_barrio`
- `peso_turismos_0_sobre_total_barrio`
- `n_etiqueta_b`
- `n_etiqueta_c`
- `n_etiqueta_eco`
- `n_sin_distintivo`
- `n_sin_clasificacion_ambiental`

#### Salida esperada

- `data/interim/ser/ser_padron_vehiculos_ivtm_barrio/ser_padron_vehiculos_ivtm_barrio_clean.parquet`

Diagnóstico recomendado:

- `reports/review/ser_padron_vehiculos_ivtm_barrio_quality_checks.csv`

#### Uso posterior

Join natural con `SER_barrio_intervalo`:

- `anio`
- `cod_barrio`

Variables candidatas:

- `n_vehiculos_distintivo_0`
- `n_turismos_distintivo_0`
- `peso_distintivo_0_sobre_total_barrio`
- `n_vehiculos_distintivo_0_por_plaza_ser`
- `n_turismos_distintivo_0_por_plaza_ser`

Estas variables son presión estructural potencial anual por barrio. No observan ocupación real ni fluctuaciones intradía.

#### Criterio go/no-go

Go si:

- los tres años 2023-2025 se leen correctamente;
- existe etiqueta `0` en todos los años;
- `CONTADOR` es válido;
- la mayoría de registros tienen barrio real;
- los códigos de barrio se pueden armonizar con SER;
- la variable muestra variabilidad espacial razonable.

No-go parcial si:

- hay demasiados registros sin barrio;
- la categoría `0` cambia de codificación;
- los códigos de barrio no conectan con SER;
- la fuente queda demasiado agregada o poco informativa.

---

## 5. Validación de joins entre fuentes limpias

Esta fase solo empieza cuando las fuentes anteriores ya tienen tablas limpias en `data/interim`.

### 5.1. Join barrio

Validar claves comunes entre:

- SER tiques;
- SER calles/plazas;
- SER autorizaciones;
- IVTM padrón vehículos;
- calendario, si se incorpora para régimen observable.

Comprobar:

- formato de `cod_barrio`;
- nombres de barrio;
- barrios presentes en una fuente y ausentes en otra;
- registros con barrio nulo, `CUALQUIERA`, `--` o código 0;
- necesidad de crear `cod_barrio_norm`.

### 5.2. Join calle/parquímetro

Validar:

- `tiques.matricula_parquimetro` contra `parquimetros.matricula`;
- vigencia del parquímetro en la fecha del tique;
- coherencia barrio tique vs barrio parquímetro;
- porcentaje de tiques asignados a parquímetro;
- porcentaje de tiques asignados a calle;
- calles `SIN ASIGNAR`;
- posibilidad de unir calle_proxy con oferta_SER_calle.

### 5.3. Join capacidad

Validar:

- `oferta_SER_barrio` contra tiques por barrio;
- `oferta_SER_calle` contra tiques asignados a calle;
- barrios/calles con tiques pero sin plazas;
- barrios/calles con plazas pero sin tiques;
- plazas nulas o cero antes de calcular ratios.

### 5.4. Join presión estructural

Validar:

- autorizaciones por barrio-periodo contra oferta_SER_barrio;
- IVTM por barrio-año contra oferta_SER_barrio;
- cálculo de ratios por plaza;
- no mezclar autorizaciones e IVTM como si midieran lo mismo.

### 5.5. Join cartográfico

Validar:

- calles/plazas dentro de zona SER;
- calles/parquímetros fuera de delimitación esperada;
- coherencia para mapas;
- posible necesidad de buffer o unidad intermedia.

---

## 6. Construcción posterior del panel SER

Solo después de validar joins, construir:

- `SER_barrio_intervalo`
- `SER_calle_intervalo`, si el join fino es viable.

### 6.1. Output robusto: SER_barrio_intervalo

Debe incluir:

- fecha;
- anio;
- mes;
- intervalo;
- distrito;
- barrio;
- plazas_SER_barrio;
- ocupacion_pagada_proxy;
- stock_inicio_proxy;
- stock_fin_proxy;
- afluencia_tiques;
- liberacion_tiques;
- saldo_flujo;
- rotacion_bruta;
- persistencia_completa;
- autorizaciones_por_plaza;
- variables IVTM seleccionadas;
- flags de calidad;
- flag_regimen_observable.

### 6.2. Output fino: SER_calle_intervalo

Debe incluir, si el join lo permite:

- fecha;
- anio;
- mes;
- intervalo;
- distrito;
- barrio;
- calle_proxy;
- plazas_SER_calle;
- parquimetros_asociados;
- métricas equivalentes a barrio;
- flags de asignación espacial;
- flag_proxy_inconsistente.

La salida fina no debe interpretarse como ocupación exacta por calle.

---

## 7. Flags mínimos de calidad

Crear o evaluar:

- `flag_regimen_observable`
- `flag_fuera_regimen_SER`
- `flag_fecha_invalida`
- `flag_duracion_invalida`
- `flag_parquimetro_no_encontrado`
- `flag_parquimetro_inactivo`
- `flag_calle_sin_asignar`
- `flag_inconsistencia_barrio_tique_parquimetro`
- `flag_ocupacion_proxy_mayor_1`
- `flag_plazas_nulas`
- `flag_plazas_cero`
- `flag_baja_senal_pagada`
- `flag_cod_barrio_0`
- `flag_autorizacion_sin_barrio`
- `flag_ivtm_sin_barrio`

---

## 8. Criterios go/no-go

| Decisión | Go | No-go |
|---|---|---|
| Usar calle como unidad fina | alto porcentaje de tiques asignables a parquímetro/calle | muchas inconsistencias espaciales |
| Usar barrio como unidad robusta | siempre | no aplica |
| Calcular ocupación pagada proxy | fechas limpias y plazas disponibles | fechas o capacidad no fiables |
| Incorporar autorizaciones | periodo 2023+ limpio y barrio usable | exceso de `CUALQUIERA` o nulos |
| Incorporar IVTM | etiqueta 0 estable y barrio armonizable | demasiados registros sin barrio o sin join |
| Crear flags interpretativos | EDA muestra regímenes claros | flags redundantes o inestables |
| Elegir granularidad final | sensibilidad 15/30/45/60 comparada | decisión sin evidencia |

---

## 9. Reglas metodológicas

- No interpretar ausencia de tiques como facilidad para aparcar.
- No interpretar tiques como ocupación real total.
- No interpretar calle como posición exacta del vehículo.
- No mezclar Régimen A y Régimen B como si fueran equivalentes.
- No usar variables futuras para construir features predictivas.
- No bajar a calle si la asignación parquímetro-calle no es suficientemente fiable.
- No calcular métricas del proxy antes de limpiar fuentes y validar joins.
- No mezclar autorizaciones e IVTM como si midieran lo mismo.
- Documentar cualquier pérdida de registros en la limpieza.

