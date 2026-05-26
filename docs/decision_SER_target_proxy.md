# Decisión metodológica: target proxy SER

## 1. Objetivo de esta decisión

Este documento fija cómo se construirá el target proxy de dificultad de aparcamiento en el bloque SER. Su función es evitar que la limpieza, agregación, modelado y visualización se hagan de forma aislada o arbitraria.

La decisión central es que el TFM no observará directamente la probabilidad real de encontrar plaza. En su lugar, construirá una medida proxy de dificultad SER a partir de la ocupación pagada observada, señales de flujo de tiques y presión estructural no observada.

---

## 2. Separación entre Régimen A y Régimen B

El sistema SER se divide en dos regímenes porque los tiques solo son una señal comparable cuando existe obligación de pago regulado.

| Régimen | Señal principal | Qué permite estimar | Nivel de confianza |
|---|---|---|---|
| Régimen A: SER observable | tiques SER | dificultad dinámica proxy | mayor |
| Régimen B: fuera de régimen observable | estructura + contexto | dificultad estructural/contextual | menor |

La separación evita interpretar la ausencia de tiques como ausencia de demanda. En domingos, festivos, noches o franjas sin pago SER, no puede asumirse que una calle sea fácil de aparcar solo porque no existan tiques.

---

## 3. Régimen A: dificultad dinámica observable con tiques SER

### 3.1. Decisión principal

En el Régimen A, la dificultad SER se aproximará mediante una lectura conjunta de:

1. ocupación pagada observada;
2. entradas y salidas de tiques;
3. rotación y persistencia;
4. presión estructural no observada;
5. variables temporales y contextuales si aportan valor.

El núcleo del target será la ocupación pagada proxy, no el número bruto de tiques.

---

### 3.2. Unidad de análisis

La unidad fina deseada será:

    calle × intervalo temporal

La unidad robusta de control será:

    barrio × intervalo temporal

La salida por calle solo se interpretará como aproximación espacial. Se asume que el usuario paga normalmente en un parquímetro del entorno donde aparca, pero esto no garantiza la posición exacta del vehículo.

---

### 3.3. Tratamiento temporal de los tiques

El dataset final de modelado no será una tabla de tiques individuales, sino un panel espacio-temporal:

    una fila = unidad espacial × intervalo temporal

El proceso será:

1. limpiar tiques;
2. asignarlos a barrio/calle cuando sea posible;
3. ordenar por unidad espacial y tiempo;
4. dividir el tiempo en intervalos comunes;
5. calcular métricas por unidad espacial e intervalo.

#### 3.3.1. Repetición de tiques entre intervalos

Un mismo tique puede aparecer en varios intervalos. Esto no es duplicación errónea, sino asignación temporal de su ocupación.

Ejemplo:

| Tique | Intervalo | Papel |
|---|---|---|
| 09:50-10:40 | 09:30-10:00 | entra y permanece |
| 09:50-10:40 | 10:00-10:30 | persiste completo |
| 09:50-10:40 | 10:30-11:00 | sale dentro |

El tique no se cuenta como tres tiques distintos. Cada intervalo recibe únicamente los minutos que realmente solapan con él.

#### 3.3.2. Regla de intervalos semiabiertos

Se usarán intervalos semiabiertos:

    [inicio_intervalo, fin_intervalo)

Esto significa que un tique que termina exactamente a las 10:30 cuenta hasta el intervalo 10:00-10:30, pero no entra en 10:30-11:00. Esta regla evita dobles conteos en los bordes.

---

### 3.4. Descomposición de tiques por intervalo

Para cada intervalo [a,b), cada tique que solapa con él se clasificará en una de cuatro categorías excluyentes:

| Tipo | Condición | Interpretación |
|---|---|---|
| A | empieza antes de a y termina después de b | persistente completo |
| B | empieza antes de a y termina dentro de [a,b) | ya estaba y sale |
| C | empieza dentro de [a,b) y termina dentro de [a,b) | entra y sale |
| D | empieza dentro de [a,b) y termina después de b | entra y permanece |

Ejemplo para el intervalo 10:00-10:30:

| Tique | Tipo |
|---|---|
| 09:50-10:40 | A |
| 09:50-10:20 | B |
| 10:05-10:20 | C |
| 10:05-10:40 | D |

---

### 3.5. Métricas derivadas

Sea C_u el número de plazas SER de la unidad espacial u y Δ_t la duración del intervalo en minutos.

| Métrica | Fórmula | Qué mide | Uso |
|---|---|---|---|
| ocupacion_pagada_proxy | minutos solapados / (C_u × Δ_t) | ocupación pagada media | núcleo del target |
| stock_inicio_proxy | (A+B)/C_u | tiques activos al inicio | diagnóstico/modelo |
| stock_fin_proxy | (A+D)/C_u | tiques activos al final | diagnóstico/modelo |
| afluencia_tiques | (C+D)/C_u | entradas nuevas | demanda pagada entrante |
| liberacion_tiques | (B+C)/C_u | tiques que terminan | oportunidad potencial |
| saldo_flujo | (D-B)/C_u | tendencia neta | sube/baja ocupación |
| rotacion_bruta | (B+2C+D)/C_u | movimiento total | dinamismo |
| persistencia_completa | A/C_u | ocupación rígida | dificultad sin renovación |

La ocupación pagada proxy se calcula con minutos exactos de solape, no con duración media.

Ejemplo:

    calle con 4 plazas
    intervalo de 30 minutos
    capacidad-tiempo = 4 × 30 = 120 plazas-minuto
    minutos solapados totales = 70
    ocupacion_pagada_proxy = 70 / 120 = 0,583

Interpretación: durante ese intervalo, los tiques pagados cubrieron aproximadamente el 58,3% de la capacidad-tiempo observable.

---

### 3.6. Lectura conjunta de métricas

Las métricas anteriores se interpretarán de forma conjunta, no aislada.

| Situación | Ocupación | Afluencia | Liberación | Saldo | Rotación | Persistencia | Lectura |
|---|---:|---:|---:|---:|---:|---:|---|
| Calle llena sin movimiento | alta | baja | baja | ≈0 | baja | alta | muy difícil |
| Calle llena con mucha rotación | alta | alta | alta | ≈0 | alta | media/baja | difícil, pero con oportunidades |
| Muchos tiques cortos | media/baja | alta | alta | ≈0 | alta | baja | zona dinámica |
| Entran muchos y no sale nadie | subiendo | alta | baja | positivo | media | creciente | dificultad creciente |
| Salen muchos y entran pocos | bajando | baja | alta | negativo | media | baja | mejora de disponibilidad |
| Pocos tiques y baja ocupación | baja | baja | baja | ≈0 | baja | baja | baja señal pagada |
| Pocos tiques pero mucho movimiento | baja/media | media | media/alta | ≈0 | media/alta | baja | estancias cortas |
| Calle vacía sin movimiento | baja | baja | baja | ≈0 | baja | baja | sin presión pagada observada |
| Alta ocupación y saldo positivo | alta | alta | baja/media | positivo | media | alta | saturación aumentando |
| Alta ocupación y saldo negativo | alta | baja/media | alta | negativo | media | bajando | saturación bajando |

Los flags binarios se crearán solo si el EDA demuestra que ayudan a capturar regímenes claros, por ejemplo:

    flag_saturacion_rigida
    flag_saturacion_con_rotacion
    flag_dificultad_creciente
    flag_liberacion_relevante
    flag_baja_senal_pagada
    flag_proxy_inconsistente

---

### 3.7. De métricas a dificultad SER proxy

La dificultad SER proxy se derivará principalmente de ocupacion_pagada_proxy, matizada por señales de flujo y presión estructural.

La lectura general será:

| Patrón | Interpretación |
|---|---|
| ocupación alta + persistencia alta + liberación baja | dificultad muy alta |
| ocupación alta + rotación alta | dificultad alta, pero con oportunidades |
| ocupación baja + baja señal pagada | no implica necesariamente facilidad |
| saldo positivo | dificultad creciendo |
| saldo negativo | posible mejora |

La variable prob_aparcar_proxy será una lectura inversa y normalizada de la dificultad, no una probabilidad real observada.

    prob_aparcar_proxy = 1 - dificultad_SER_proxy_normalizada

Los umbrales de clases se definirán después del EDA, no en esta decisión inicial.

---

### 3.8. Granularidad temporal y leakage

La granularidad temporal final no se fijará solo por conveniencia. Se compararán intervalos de:

    15, 30, 45 y 60 minutos

La malla temporal final será común para todas las calles, para mantener comparabilidad espacial y temporal.

Se podrán construir features multiescala siempre que usen solo información disponible en el pasado.

| Permitido | No permitido |
|---|---|
| ocupación pasada | ocupación futura |
| últimos 30/60/120 minutos | media centrada |
| misma hora del día anterior | total diario calculado con horas futuras |
| misma hora de la semana anterior | variables calculadas con información posterior al instante de predicción |

---

## 4. Régimen B: dificultad fuera del régimen observable SER

### 4.1. Decisión principal

Fuera del régimen observable SER no se estimará la misma dificultad dinámica basada en tiques. En esos periodos, la ausencia de tiques no significa ausencia de ocupación ni facilidad para aparcar.

Por tanto, el Régimen B se tratará como una dificultad estructural o contextual separada, con menor nivel de confianza.

---

### 4.2. Variables estructurales candidatas

La dificultad estructural del Régimen B podrá apoyarse en:

    autorizaciones_por_plaza
    plazas_SER
    atractores cercanos
    tráfico histórico si aporta señal
    calendario
    zona/barrio

Las autorizaciones no explican fluctuaciones intradía, pero sí capturan presión estructural no observada por los tiques. El resto de variables actúan como contexto espacial o temporal, no como observación directa de ocupación.

---

### 4.3. Salida prevista y nivel de confianza

La salida del Régimen B no será directamente comparable con la dificultad dinámica del Régimen A.

| Régimen | Salida | Confianza |
|---|---|---|
| A | dificultad dinámica proxy | mayor |
| B | dificultad estructural/contextual | menor |

En mapas o análisis, ambas salidas deberán diferenciarse explícitamente.

---

### 4.4. Condición para pasar a un modelo dinámico

Solo se planteará un modelo dinámico para el Régimen B si aparece una fuente fuerte que aproxime ocupación o demanda fuera del horario regulado, por ejemplo:

    ocupación real SER interna
    sensores/cámaras
    datos agregados de movilidad
    fuente de demanda nocturna suficientemente granular

Si no aparece una fuente de ese tipo, el Régimen B se mantendrá como capa estructural/contextual.

---

## 5. Limitaciones principales

| Limitación | Consecuencia |
|---|---|
| Los tiques no observan toda la ocupación real | proxy parcial |
| Residentes, autorizados y otros vehículos no observados por tique | posible infravaloración de la dificultad |
| Parquímetro no equivale a plaza exacta | incertidumbre espacial en calle |
| ocupacion_pagada_proxy > 1 puede aparecer | flag de inconsistencia o saturación proxy |
| Fuera del horario SER no hay señal comparable | Régimen B separado |
| Calle puede ser unidad demasiado fina | posible uso de barrio, zona o buffer |
| Contexto no sustituye señal principal | entra solo si aporta valor |

---

## 6. Criterios de validación y próximos pasos

| Decisión futura | Criterio |
|---|---|
| Usar calle como unidad fina | porcentaje suficiente de tiques asignables a parquímetro/calle |
| Mantener barrio como control | siempre, para robustez y validación |
| Elegir granularidad temporal | comparación 15/30/45/60 min |
| Crear flags binarios | solo si el EDA muestra regímenes claros |
| Incorporar contexto | mejora en validación temporal o interpretación clara |
| Modelar Régimen B dinámico | solo si aparece fuente fuerte |
| Convertir dificultad en clases | después de analizar distribución y estabilidad del proxy |

