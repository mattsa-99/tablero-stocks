# Cambios propuestos: score, calificación y puestos del tablero (versión multi-activo)

Origen: sesión de trabajo del 2026-09-20/21 armando la cartera real «Portafolio Mateo».
Esta versión **reemplaza** la anterior: la primera solo cubría acciones y ETFs de renta variable. Ahora el objetivo es que
el tablero sirva para **acciones, ETFs, renta fija (fondos y directa: TES, CDT, FIC), materias primas y cripto**.

Estado: **propuesta**. Nada está implementado ni validado. Los umbrales son de diseño, no salen de un backtest. Las
referencias a archivos y líneas salen de leer el repo el 2026-09-21 y son aproximadas.

## Qué se observó

Sobre el ranking:

- ETB.CL sale en el puesto 2 con score 75.94 y calificación **D**.
- IVV pasó del puesto 220 al 113 solo porque cambió la cartera contra la que se mide.
- VT saca **A** con 2 de 4 señales (sin valoración ni calidad), con confianza «media».
- 263 de 490 activos (54%) son A o B.
- La ficha de VT propone un tamaño de 8% (125 USD sobre 1.566), que no aplica a un núcleo.

Sobre otros tipos de activo (del código y del catálogo):

- El universo tiene 269 acciones, 182 ETFs, 22 criptos y 21 «otros» (futuros, ETNs, materias primas), todos con la misma fórmula.
- **No existe el tipo renta fija directa.** `AssetType` es STOCK, ETF, ADR, REIT, FUND, CRYPTO y OTHER: no hay TES, CDT, bonos ni FIC,
  y el proveedor (Yahoo) no los cubre.
- **La clasificación en cubos es una lista fija de símbolos** (`exposure.py`: `BOND_ETFS`, `COMMODITY_ETFS`, `BROAD_ETFS`).
  Un ETF fuera de las listas y sin sector se asume «Diversificado». Según el código, SJNK y USHY (bonos de alto rendimiento) y
  GSG, DBA, DJP (materias primas) caerían en «Diversificado», o sea contados como renta variable amplia.
- **Cripto:** `metrics.py` usa `TRADING_DAYS_YEAR = 252`, SMA de 50/200 barras y momentum de 252/21 barras. El cripto cotiza 365
  días, así que sus ventanas cubren ~8 meses y no 12, y la volatilidad anualizada con √252 queda subestimada ~20% (√(365/252)).
  Además `exposure_bucket` lo manda a «Desconocido» y `sizing.py` le aplica el piso de estrés de una acción (35%).
- **Sizing:** el piso de estrés solo distingue «fondo» (25%) de «acción» (35%). No hay piso propio para cripto, renta fija ni materias primas.
- SJNK sale con valoración 99.87 y no sé por qué: hay que revisar de dónde sale ese número para un fondo de bonos.

## Principio de diseño

**Comparar dentro de una clase de activo, asignar entre clases.** Un score nunca debe ordenar una acción frente a un bono o una
cripto. Entre clases lo que se decide es el **peso** (asignación), y eso es una decisión del usuario, no de un ranking.

| Clase | Señales disponibles | Fuente de datos | ¿Se califica A–E? | Piso de estrés (propuesta inicial) |
|---|---|---|---|---|
| Acciones (STOCK, ADR, REIT) | valoración, tendencia, riesgo, calidad | Yahoo | Sí, con ≥3 señales | 35% (actual) |
| Fondos de renta variable | tendencia, riesgo (+ costo, cobertura si se consigue) | Yahoo | Solo «parcial» hasta tener más señales | 25% (actual) |
| Fondos de renta fija | tendencia, riesgo (+ duración, calidad crediticia, rendimiento si se consigue) | Yahoo (por comprobar) | «Parcial» | por definir con datos; ligado a la duración |
| Materias primas (ETFs, ETNs) | tendencia, riesgo | Yahoo | «Parcial» | por definir con datos |
| Cripto | tendencia, riesgo (+ tamaño y liquidez) | Yahoo | «Parcial» (nunca A/B) | **≥70%** (supuesto: las caídas históricas del BTC superaron ese nivel; validar con BTC-USD del catálogo) |
| Renta fija directa (TES, CDT, FIC) | tasa, plazo, emisor, liquidez, tasa real | **Manual** + Banrep/BVC | **No**: ficha propia (ver cambio 10) | no aplica; riesgo de tasa y emisor |
| Futuros (`=F`) | — | — | — | **Fuera del ranking accionable** (ver cambio 12) |

## Cambios, por orden de prioridad

### 1. Introducir la clase de activo como dimensión de primer nivel

- **Problema:** una sola fórmula y un solo ranking percentil para cosas que no son comparables. Los fondos, materias primas y cripto
  reciben un neutro de 50 en valoración y calidad (VT: `value.available = false`) y compiten con acciones.
- **Cambio:** añadir `asset_class` (ver tabla) derivada de `asset_type` y de datos, no de listas. Rangos percentiles, calificación,
  ranking, sizing y cubos **por clase**. Se puede reutilizar `grouped_percentile_ranks` (hoy usado por sector). La vista muestra
  una tabla por clase, y ningún score cruza clases.
- **Ojo:** el universo mínimo es 5 por grupo; los grupos pequeños (cripto: 22, «otros»: 21) deben declararlo.
- **Prueba:** el mismo activo mantiene su score al filtrar por clase; un fondo nunca aparece en la tabla de acciones.

### 2. Clasificar por datos y no por listas de símbolos

- **Problema:** `exposure.py` depende de `BOND_ETFS`, `COMMODITY_ETFS` y `BROAD_ETFS`. Todo lo que no esté en la lista se asume
  «Diversificado». Es un error silencioso: un fondo de bonos cuenta como renta variable amplia y falsea toda la diversificación.
- **Cambio:** clasificar por los campos del proveedor (`quoteType`, categoría del fondo, nombre) con la lista solo como **override**.
  Cuando la clase sea un supuesto, la ficha debe decirlo (ya existe `is_assumed_bucket`, mostrarlo siempre). Añadir cubos «Cripto»
  y «Renta fija directa»; hoy la cripto cae en «Desconocido».
- **Prueba:** SJNK, USHY → «Renta fija»; GSG, DBA, DJP, PHYS → «Materias primas»; BTC-USD → «Cripto». Un test que falle si un
  ETF queda como supuesto sin declararlo.

### 3. El cubo «Diversificado» no debe disparar alarmas contra el núcleo

- **Problema:** quien arma un núcleo con un fondo amplio (VT, IVV) tendrá siempre >30% en «Diversificado». Cada ficha traerá la bandera
  `sector_concentration` y una penalización, aunque el cubo sea diversificado por definición.
- **Cambio:** tratar «Diversificado» aparte en `exposure.py` / `flags.py` / `opportunities.py`: sin umbral por cubo (o uno propio) y una
  advertencia distinta cuando el fondo es de un solo país (un S&P 500 concentra en EE. UU.).
- **Prueba:** 80% en un fondo amplio no genera `sector_concentration` ni baja el score de otro fondo amplio.

### 4. Score independiente de la cartera; encaje aparte

- **Problema:** el score (`opportunities.py` ≈ l.448–483) suma 0.15 × `diversification_score`, que depende de la cartera, y por eso el
  puesto cambia con ella. Además `suggestion.py` multiplica por `concentration_factor` (`CONCENTRATION_THRESHOLD = 0.25`, l.56): la misma
  concentración se penaliza dos veces y con umbrales distintos (30% y 25%).
- **Cambio:** quitar la diversificación del score (valor, impulso, riesgo) y dejarla solo como «encaje» en la sugerencia, con un umbral único.
- **Ojo:** el rango natural pasa de [−20, 80] a [−20, 65]; el `+20` y la escala a [0, 100] hay que rehacerlos. Anotar la foto antes/después.
- **Prueba:** el mismo activo tiene el mismo score con cualquier cartera. `test_filtering_does_not_change_any_score` debe seguir pasando.

### 5. Ordenar por calificación; no mostrar puestos altos a D/E

- **Problema:** `rows.sort(key=lambda row: row.score, reverse=True)` (`opportunities.py` ≈ l.595) ignora la calificación.
- **Cambio:** conservar `rank` como posición en el ranking completo (regla existente, con huecos) y añadir `rank_in_grade` y un orden por
  defecto (calificación, luego score), ahora **dentro de cada clase**. Nota en la fila si hay score alto con D/E.
- **Prueba:** ETB.CL no aparece por encima de un B en el orden por defecto.

### 6. Cobertura mínima para las notas altas

- **Problema:** `MIN_SIGNALS_FOR_GRADE = 2` (`grading.py` l.96) y la nota es `points / max_points` sobre lo disponible: 2 señales perfectas dan A.
- **Cambio:** exigir ≥3 señales para A y B; con menos, tope en «Normal» y etiqueta «parcial». Añadir `coverage` (disponibles / 4) a
  `Assessment` y mostrarlo junto a la letra («A parcial, 2/4»). Consecuencia buscada: fondos, materias primas y cripto no pueden sacar A/B.
- **Prueba:** un ETF sin valoración ni calidad no puede sacar A.

### 7. Recalibrar y renombrar las etiquetas

- **Problema:** con A = 98 y B = 165 sobre 490, «Muy buena» y «Buena» discriminan poco; el docstring de `_grade_from_points` admite que el corte
  de A (0.75) es una calibración sobre una foto. Y «buena/mala» suena a juicio sobre la empresa, cuando mide señales de precio y ratios.
- **Cambio:** «Señales muy favorables / favorables / mixtas / desfavorables / muy desfavorables» (letras A–E y slugs de la API sin cambios).
  Medir la distribución **después** de los cambios 1 y 6 y fijar antes qué distribución se busca.
- **Dónde:** `GRADE_LABEL` en `grading.py`, `glossary.js`, chips de `opportunities.js` (`GRADE_SLUG` está duplicado).

### 8. Sizing por clase

- **Problema:** `stress_floor(is_fund)` solo distingue fondo (25%) y acción (35%). Una cripto usa el piso de una acción. Y el 8% de un fondo
  amplio (2% de presupuesto ÷ 25%) aplicado a un núcleo dejaría el 92% en efectivo.
- **Cambio:** `stress_floor(asset_class)` con un piso por clase (tabla de arriba) y la fuente documentada. Marcar `sizing.applies_to =
  "single_position"` y, en fondos amplios, mostrar una nota: «techo para una posición suelta; el núcleo se dimensiona por asignación».
- **Prueba:** una cripto nunca recibe un tamaño calculado con el piso de acciones.

### 9. Bases de tiempo por clase

- **Problema:** las ventanas y la anualización asumen 252 sesiones (`metrics.py`: `TRADING_DAYS_YEAR`, `SMA_FAST/SLOW`, momentum 252/21).
  En cripto (365 barras/año) eso son ~8 meses y la volatilidad sale ~20% menor.
- **Cambio:** un parámetro `periods_per_year` por clase (252 / 365) que gobierne ventanas y anualización, o definir las ventanas en días
  calendario. Comprobar también las barras de mercados con festivos distintos (BVC vs NYSE, ya tratado en el rendimiento).
- **Prueba:** la volatilidad anualizada de una serie sintética de 365 barras/año coincide con la esperada.

### 10. Renta fija directa (TES, CDT, FIC): módulo propio, sin score

- **Problema:** no hay tipo de activo, ni datos del proveedor, ni forma de valorarlos. El ledger valora con precios de Yahoo.
- **Cambio:** un tipo `FIXED_INCOME_DIRECT` cargado **a mano**: principal, moneda (COP; UVR como unidad indexada), tasa efectiva anual,
  fecha de compra y vencimiento, emisor, si tiene mercado secundario, retención. Valoración a costo más intereses devengados (declarada como
  aproximación). **Sin score ni calificación A–E**: una ficha con estas preguntas: tasa neta de impuestos frente a inflación y frente a la
  tasa del Banrep, plazo, liquidez, riesgo de emisor y riesgo de tasa (un TES pierde valor si suben las tasas).
- **Ojo:** las tasas de referencia (política del Banrep, inflación, curva TES) no vienen de Yahoo: leerlas de una fuente oficial y mostrar la fecha.
  Hoy la tasa de política es 12% (desde el 2026-07-01) y la inflación de agosto 6,24% (Banrep); la curva TES vigente no se pudo consultar.
- **Prueba:** una posición de CDT carga, aparece en `by_bucket_pct` como «Renta fija directa» y suma en el valor total sin pasar por Yahoo.

### 11. Plan de asignación entre clases

- **Problema:** el tablero mide concentración pero no guarda la **intención** del usuario (cuánto quiere en cada clase), y no se puede derivar.
- **Cambio:** un objeto `allocation_target` por cartera (renta variable mundial, renta variable Colombia, renta fija, cripto, y un reparto COP/USD),
  con **bandas de tolerancia** y regla de rebalanceo (primero con aportes nuevos). `brief` y `whatif` muestran el peso actual frente al objetivo.
  Es una excepción razonable a «todo se calcula»: es un dato del usuario, no del mercado.
- **Prueba:** `whatif` de una compra muestra el cambio de cada clase frente a su objetivo.

### 12. Qué no es accionable queda fuera del ranking

- **Problema:** los futuros (`ES=F`, `RTY=F`, `HG=F`) están en el ranking. Un contrato del E-mini S&P equivale a 50 veces el índice, es inoperable con
  un capital pequeño, vence y duplica al ETF. Es el mismo principio con el que se excluyeron los valores en otras divisas.
- **Cambio:** marcarlos `not_actionable` y sacarlos del ranking accionable (dejar el dato para consulta).

### 13. Notas fiscales por clase (informativas)

- **Cambio:** una bandera `info` por clase que apunte a verificar el tratamiento fiscal, sin calcular impuestos (el coste medio no es para declaraciones).
  Lo encontrado (por confirmar con un contador): dividendos de EE. UU. sin convenio con Colombia y con descuento tributario limitado (Ámbito Jurídico);
  TES con retención de 19% (guía de terceros, abril de 2026); cripto como ganancia ocasional (10%) o renta ordinaria según la frecuencia, con obligación
  de declarar el exterior por encima de cierto monto (fuente única, sin verificar).

### 14. Validación hacia adelante

- **Problema:** los pesos son un juicio sin backtest. Reordenar las etiquetas aclara, no acierta más.
- **Cambio:** guardar una foto semanal del ranking (símbolo, clase, score, calificación, precio): no se puede derivar después porque los fundamentales
  cambian. Con eso y las fotos del diario, un script `scripts/scorecard_grades.py` compara a 6 y 12 meses rendimiento y caída máxima por
  calificación **dentro de cada clase** frente a un índice de su clase.
- **Ojo:** con ~1–2 años de histórico no hay backtest serio hacia atrás; es una prueba futura.

## Orden sugerido de implementación

1. **Correcciones que evitan errores silenciosos:** 2 (clasificación), 3 («Diversificado»), 6 (cobertura), 9 (bases de tiempo), 12 (futuros).
2. **Estructura por clase:** 1, 4, 5, 7, 8.
3. **Lo nuevo:** 10 (renta fija directa), 11 (asignación), 13 (fiscal), 14 (validación).

## Lo que no se debe cambiar

- Las advertencias de producto («no es una recomendación», heurísticas sin backtest, datos de Yahoo con retraso).
- El filtrado **después** de puntuar y `rank` como posición en el ranking completo.
- Que el veredicto de la ficha nunca diga «compra».
- Que un factor no medible se declare como tal y no se inventen neutros sin avisar.
- Que un dato ausente sea `None` y nunca 0.

## Preguntas abiertas

- ¿Cómo se valora un CDT o un FIC en el ledger: a costo más devengo, o con el valor de unidad que informa el fondo?
- ¿Qué entrega Yahoo para fondos de renta fija (duración, rendimiento, calidad crediticia)? Hay que comprobarlo antes de diseñar sus señales.
- ¿Qué distribución de calificaciones se quiere por clase?
- ¿Cripto dentro del mismo tablero, o como seguimiento aparte con reglas propias?
