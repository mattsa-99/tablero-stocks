# Auditoría de `cambios-tablero-scores.md`

Fecha: 2026-09-20. Rama: `feat/clases-de-activo`. Base real `tablero.db` (41 MB).
Línea base antes de tocar nada: **675 tests en verde, `ruff` limpio**.
Foto del ranking guardada en `Claude outputs/snapshots/` (490 filas × 2 carteras),
generada con `scripts/snapshot_ranking.py` (solo lectura).

Todo lo que sigue está medido contra la base y contra Yahoo en vivo. Lo que no
pude comprobar está marcado como tal.

---

## 1. Veredicto en una línea

**El documento acierta en 12 de sus 14 diagnósticos y se queda corto en el más
importante.** El cambio 4 (quitar la diversificación del score) no es "el puesto
cambia con la cartera": **483 de 490 símbolos cambian de puesto** al mirar el
mismo universo desde otra cartera. Eso no es un sesgo, es que el ranking no
significa lo que dice significar.

Hay además **un fallo que el documento no menciona y que bloquea a los cambios 14
y a la ampliación del histórico**: el relleno de barras solo avanza hacia
adelante, así que subir la retención no traería ni un día más de pasado.

---

## 2. Dónde te equivocas (cosas que comprobé y no se sostienen)

### 2.1. Los fundamentales **sí** se guardan en su fecha

Dices: «Los fundamentales no se guardan "en su fecha", así que no hay backtest posible».

La tabla `fundamental_snapshots` tiene `as_of DATE` con
`UNIQUE (asset_id, as_of)`: ya es una serie temporal. Lo que pasa es otra cosa:

```
MIN(as_of)=2026-08-30   MAX(as_of)=2026-09-19   fechas distintas=11   filas=3.732
```

**21 días de archivo, con huecos** (el 16, 17 y 19 de septiembre solo entraron
25, 75 y 202 de 494 activos: la sincronización no terminó). Y `as_of` es la
fecha en que se *descargó*, no la del reporte contable.

La conclusión práctica es la tuya —hoy no hay backtest— pero la causa cambia el
trabajo: no hay que crear el almacén, hay que **darle profundidad y cerrar las
sincronizaciones a medias**.

### 2.2. PHYS no cae en «Diversificado», cae en «Desconocido»

Yahoo lo clasifica `quoteType: EQUITY` (es un trust, no un ETF), así que en la
base es `STOCK`, `sector` nulo, y `exposure_bucket()` lo manda a `UNKNOWN`.
Importa porque la corrección que propones (clasificar fondos por datos) **no lo
alcanzaría**: no es un fondo para Yahoo.

### 2.3. El rango natural tras quitar la diversificación es `[0, 85]`, no `[−20, 65]`

Dices que hay que rehacer el `+20` y la escala. No hace falta:

```
Score = 0.35·V + 0.30·M − 0.20·R + 20
mín = 0 + 0 − 20 + 20 =  0
máx = 35 + 30 − 0 + 20 = 85
```

El `+20` sigue cancelando exactamente el término de riesgo. Solo se pierden los
15 puntos de arriba. Si quieres recuperar `[0, 100]`, basta dividir por 0,85:

```
Score = 0.4118·V + 0.3529·M − 0.2353·R + 23.53
```

Y esto es importante: **dividir por una constante positiva no cambia ni un
puesto del ranking**. Es cosmético. Todo el movimiento de puestos viene de
quitar `D`, no de reescalar. Conviene medir las dos cosas por separado para no
atribuirle al reescalado un efecto que no tiene.

---

## 3. Paso 1 — auditoría de datos de renta variable

### 3.1. Composición real del universo

| Tipo | Total catálogo | En universo |
|---|---:|---:|
| STOCK | 510 | 271 |
| ETF | 184 | 182 |
| CRYPTO | 22 | 22 |
| OTHER | 19 | 19 |
| **Total** | **735** | **494** |

Tu documento decía 269/182/22/**21**, y en lo de los 21 tenías razón tú y no
`asset_type`. Hay **21 futuros** en el universo, no 19:

```
=F en el símbolo:                       21
   quoteType = FUTURE:                  20   -> de ellos, asset_type STOCK: 2 (GC=F, CL=F)
   quoteType = ALTSYMBOL:                1   (CT=F, algodón)
   asset_type = OTHER:                  19
```

**Ni `asset_type` ni `quoteType` identifican bien a un futuro.** `GC=F` (oro) y
`CL=F` (crudo) están guardados como `STOCK`, así que `sizing.py` les aplica el
suelo de estrés del 35 % de una acción individual, y `CT=F` escaparía a
cualquier filtro por `quoteType`. **El único discriminador fiable es el sufijo
`=F`**, que es justo lo que ya usa `exposure_bucket`. Tenlo en cuenta al
implementar el cambio 12: filtrar por `quoteType == FUTURE` dejaría dentro a
`CT=F` y filtrar por `asset_type == OTHER` dejaría dentro al oro y al crudo.

Conviene saber además que **`ADR`, `REIT` y `FUND` existen en el enum y no los
usa ni una fila**. Eso tiene una consecuencia silenciosa:
`_SECTOR_COMPARABLE_TYPES` incluye `{STOCK, ADR, REIT}`, así que hoy **la
valoración relativa al sector solo se aplica a `STOCK`**, y los ADRs (TM, TSM,
PBR…) entran como acciones normales, que es lo correcto por accidente.

De los 494, se puntúan **490** (4 excluidos por faltarles 2 de 3 factores).

### 3.2. Los ETFs sacan mejores notas que las acciones, y es un artefacto

```
           n    A    B    C    D    E     %A      %A+B
ETF      181   74   63   31    6    7   40,9%   75,7%
STOCK    271   17  100  119   27    8    6,3%   43,2%
CRYPTO    19    0    0    1    6   12    0,0%    0,0%
OTHER     19    7    2    4    5    1   36,8%   47,4%
```

**Un ETF tiene 6,5 veces más probabilidad de sacar una A que una acción.** No
porque sea mejor: porque tiene menos señales que suspender.

```
señales disponibles (media)   STOCK 3,96   ETF 2,71   CRYPTO 2,00   OTHER 2,00
```

`_grade_from_points` califica por `points / max_points`, y `max_points = 2 × señales
disponibles`. Con 2 señales, dos aciertos dan 4/4 = 1,0 → **A**. Con 4 señales
hay que acertar en casi todo. La regla se diseñó para no penalizar a un futuro
por no tener contabilidad, y el efecto colateral es que **premia la ausencia de datos**.

**43 de los 263 A/B del ranking (16 %) descansan sobre 2 señales**, entre ellos
7 contratos de futuros con A: `RTY=F` (#71), `HG=F` (#72), `ES=F` (#82),
`NQ=F` (#87), `YM=F` (#96), `ZS=F` (#98), `ZC=F` (#136).

Tu cambio 6 es correcto y es el de mejor relación valor/riesgo del documento.

### 3.3. De dónde salen las valoraciones absurdas (esto lo pediste explícitamente)

Fui al `raw` de Yahoo. Los tres casos:

**SJNK, valoración 99,9 (el máximo del universo).**
Yahoo devuelve `info["trailingPE"] = 0.89050037` para un fondo de bonos de alto
rendimiento. Es el P/E positivo más bajo de los 494, así que
`invert(percentile_rank())` lo deja en 99,9. Un fondo de bonos no tiene
beneficios: el número no significa nada.

Y hay una refutación limpia dentro del propio Yahoo: `Ticker("SJNK").funds_data.equity_holdings`
devuelve **`Price/Earnings = 0.0`**. O sea que Yahoo *sabe* que no hay P/E, y
aun así publica 0,89 en `info`.

**TLT, valoración 97,5.**
`priceToBook = 0.542`, calculado como `previousClose (80.93) / bookValue (148.865)`.
Pero el propio `raw` trae `navPrice = 80.92253`. **El "valor contable" que usa
Yahoo está un 84 % por encima del NAV que publica en la misma respuesta.** Para
un fondo, el NAV *es* su valor contable por participación: son dos cifras
incompatibles en el mismo JSON.

**PHYS, valoración 98,8.**
`trailingEps = 5.51`, precio 32,36 → P/E 5,89. Sprott Physical Gold Trust solo
tiene oro; ese "beneficio por acción" es la revalorización del oro por unidad.
El efecto es perverso: **cuanto más sube el oro, más "barato" parece PHYS.** Y
como `fresh_trailing_pe` lo recalcula con el precio de hoy, el sistema refresca
el disparate a diario.

Otros con múltiplo vivo y sin sentido: GLD (P/B 2,36 sobre lingotes), IAU (2,38),
GSG (P/E 8,05), DBA (11,89), COMT (88,6), PALL (0,03), USHY (10,63).
**Son 10 fondos con al menos un múltiplo falso alimentando el 35 % del score.**

**Mi corrección propuesta**, en `data_quality.py` (es donde ya vive esta clase de
red, y el precedente de CIB es exactamente el mismo):

1. **Un fondo que no tiene acciones dentro no tiene valoración.**
   `funds_data.asset_classes` da `stockPosition` para el 100 % de los fondos que
   probé. Si `stockPosition < 0,5`, se descartan `trailing_pe`, `forward_pe`,
   `price_to_book`, `price_to_sales` y `ev_to_ebitda`. El fondo queda **sin señal
   de valoración**, que es la verdad, no un neutro inventado.
2. **A ningún fondo se le mide el P/B.** Por arbitraje, precio ≈ NAV, así que el
   P/B "correcto" de cualquier fondo es ≈ 1: cuando sale distinto está roto, y
   cuando sale bien no informa de nada. Red secundaria por si falla `funds_data`:
   `|bookValue / navPrice − 1| > 0,10` ⇒ descartar.
3. **El P/E de un fondo de acciones SÍ sirve**, y hay que decirlo: es la media
   ponderada del P/E de su cartera. Comprobado: Yahoo da 24,40 para IVV y el
   S&P 500 cotiza ahí. No hay que tirarlo.

> **Trampa que casi me cuela y que debe quedar escrita:**
> `funds_data.equity_holdings` devuelve los ratios **invertidos** — son
> rendimientos, no múltiplos. IVV da `Price/Earnings = 0.04035`, que es
> `1/24,78`; `Price/Book = 0.18934`, que es `1/5,28`. Quien lea ese campo como un
> P/E dejará a IVV con un P/E de 0,04 y encabezando el ranking para siempre.

> **Segunda trampa, de mi propia prueba:** clasifiqué por subcadena de la
> categoría y `XLC` («Communications») quedó como no accionario porque
> **"com·muni·cations" contiene "muni"**. La clasificación por categoría tiene
> que ser por **nombre exacto contra una tabla**, nunca por subcadena.

### 3.4. Los cubos de exposición: 136 supuestos, no 6

```
Diversificado (SUPUESTO)      136      Materias primas        27
Technology                     46      Consumer Defensive     20
Financial Services             44      Desconocido            20
Consumer Cyclical              36      Communication Serv.    18
Healthcare                     31      Diversificado (real)   15
Industrials                    29      Real Estate            14
Energy                         20      Renta fija             10
                                       Utilities/Basic Mat.   24
```

**136 de 490 activos (27,8 %) están clasificados como «Diversificado» por
suposición**, no por dato. Tu documento cita 6 símbolos; son 136. La
diversificación de la cartera y el factor `D` del score se calculan sobre eso.

La buena noticia: **los 182 ETFs del universo traen `category` de Morningstar,
los 182.** Cobertura del 100 %. El cambio 2 no es "mejorable", es directamente
resoluble con un dato que ya está descargado y guardado en `raw`.

### 3.5. El score depende de la cartera (cambio 4)

Puntué el mismo universo con la cartera 1 (tiene IVV, PBR, EQNR) y con la
cartera 2 (vacía):

```
símbolos que cambian de puesto:  483 de 490  (98,6 %)
scores distintos:                470 de 490
IVV:   #220 (score 57,79) -> #113 (score 61,37)
mayor salto: GDXJ #331 -> #205 (+126 puestos)
```

Tu observación de IVV 220→113 está reproducida exactamente. El mecanismo: la
cartera 1 tiene IVV, que cae en «Diversificado»; ese cubo pesa mucho, y el
factor `D` castiga **a los 151 fondos amplios del universo a la vez**, con
−3,59 puntos cada uno.

Esto es a la vez el cambio 3 y el cambio 4, y por eso los juntaría.

### 3.6. Cripto: el error está medido

```
símbolo   barras  días  bar/año  vol_√252  vol_real   error   ventana momentum
AAPL         289   417    253,1    0,252     0,252    +0,2%     336 días
IVV          288   417    252,3    0,129     0,129    +0,1%     336 días
BTC-USD      419   418    366,1    0,391     0,471   +20,5%     231 días
ETH-USD      419   418    366,1    0,530     0,639   +20,5%     231 días
SOL-USD      419   418    366,1    0,538     0,648   +20,5%     231 días
```

**El tablero dice que BTC tiene 39,1 % de volatilidad; son 47,1 %.**
Y su "momentum 12-1" cubre **231 días (7,6 meses)** frente a los 336 (11 meses)
de una acción. Ambas cifras entran en rangos percentiles compartidos con las
acciones, así que el cripto compite con una volatilidad rebajada un 20 %.

Tu cambio 9 es correcto y la cifra que estimaste (~20 %) es exacta.

### 3.7. El histórico: 419 días, y ampliarlo no es subir una constante

```
price_history:  203.610 filas   2025-07-27 -> 2026-09-18
barras por activo:  mín 14   máx 419   media 286
activos con ≥1 año: 708 de 711
activos con ≥2 años:  0
activos con ≥5 años:  0
```

**Ningún activo tiene dos años.** El suelo de estrés de `sizing.py` (35 % / 25 %)
no es conservadurismo opcional: es lo único que impide dimensionar posiciones
sobre catorce meses de un mercado alcista.

Ahora el fallo que no está en tu documento. En `market_data.py:398-406`:

```python
default_start = today - dt.timedelta(days=settings.price_history_days)
for asset in candidates:
    last = last_dates.get(asset.id)
    start = last + dt.timedelta(days=1) if last else default_start
```

**El relleno solo avanza hacia adelante.** Para los 711 activos que ya tienen
barras, `start` es siempre el día siguiente a la última: subir
`price_history_days` a 1.825 **no traería ni un día más de pasado**. Es
exactamente el mismo fallo que ya se corrigió en `refresh_fx_history` (mirar el
rango completo, no solo el máximo) y que sigue vivo aquí.

Así que "ampliar a 5 años" son cuatro cambios, no uno:

| | Qué | Sin esto |
|---|---|---|
| a | Relleno que mire `MIN(date)` y pida hacia atrás | No entra ni un día |
| b | `price_history_retention_days` 1.100 → ~2.000 | La poda lo borraría (hoy está desactivada) |
| c | `price_history_days` 400 → ventana larga **al leer** | El motor no lo vería |
| d | Decidir qué métrica usa la ventana larga | `max_drawdown(window=252)` seguiría mirando 1 año |

Sobre (d): el drawdown y la correlación **sí** deben usar 5 años; el momentum
12-1 **no**, por definición.

**El coste es mucho menor de lo que temes.** `fetch_history` es un único
`yf.download` por lote: pedir 5 años cuesta **las mismas llamadas** que pedir 1,
solo más bytes. El riesgo de 429 no cambia. En disco: hoy 203.610 filas dentro de
una base de 41 MB; cinco años del universo de 494 rondarían las 660.000 filas,
unos 63 MB en esa tabla. Irrelevante.

Lo que sí cuesta es la **primera** pasada, porque hay que pedir el rango antiguo
completo de 494 símbolos. Con el lote de 25 y 2 s de pausa, es del orden de
minutos, una sola vez.

### 3.8. `data_quality.py` sigue cubriendo lo que prometía

Comprobado en la base de hoy:

```
CIB          P/B, P/S, EV/EBITDA -> NULL   (descartados) ✓
MINEROS.CL   P/B, P/S, EV/EBITDA -> NULL   (descartados) ✓
BRK-B        P/B, EV/EBITDA -> NULL;  P/S = 2,84 conservado ✓
CL (Colgate) P/B = 295,5 CONSERVADO  ✓  (es real, debe pasar)
```

Las dos redes funcionan y el falso positivo que más preocupaba (Colgate) no se
produce. **Lo que no cubre es lo de §3.3**, porque ahí no hay discrepancia de
divisa ni magnitud implausible: 0,89 y 0,54 son números perfectamente plausibles
para una acción. Hace falta una tercera red, y es la de "esto no es una empresa".

---

## 4. Los 14 cambios, uno a uno

Esfuerzo en una escala tosca: **S** = un rato, **M** = una sesión, **L** = varias.

### Fase A — correcciones de errores silenciosos

| # | Viabilidad | Qué rompe | Esf. |
|---|---|---|---|
| **2** Clasificar por datos | **Alta.** 182/182 ETFs traen `category`. | `exposure_bucket` cambia para ~136 activos ⇒ cambia `D` ⇒ cambia el ranking entero. `sector_weights`, simulador y `flags` se mueven juntos (bien: comparten función). Cuidado con el match por subcadena (§3.3). | M |
| **6** Cobertura mínima | **Alta.** Un `if` en `assess()` más `coverage` en `Assessment`. | 43 filas A/B bajan a «Normal». Los tests de `grading` que fijan notas con 2 señales fallarán **y deben fallar**. | S |
| **9** Bases de tiempo | **Alta.** `periods_per_year` por clase. | Cambia vol/momentum/drawdown de los 22 criptos ⇒ cambian sus percentiles ⇒ se mueve todo el ranking un poco. | M |
| **12** Futuros fuera | **Alta**, pero **filtra por el sufijo `=F`**, no por `quoteType` ni por `asset_type` (§3.1). | **21** filas salen del ranking ⇒ **los percentiles de las 469 restantes se recalculan**. No es "quitar 21 filas", es repuntuar. | S |
| **3** «Diversificado» aparte | **Alta**, pero ver abajo. | — | S |

**Discrepo parcialmente del cambio 3.** Propones tratar «Diversificado» con
umbral propio. Yo lo haría al revés: si haces el **cambio 4**, el problema
desaparece solo, porque la diversificación deja de estar en el score. Lo que
quedaría del 3 es solo la bandera `sector_concentration` de la ficha, y ahí sí:
un 80 % en VT no es concentración sectorial. **Haría el 4 primero y luego
reevaluaría si el 3 sigue haciendo falta.** Sospecho que se reduce a tres líneas
en `flags.py`.

**Y añado a la fase A la corrección de §3.3** (valoración de fondos), que no está
en tu lista y que es, con diferencia, el dato roto de mayor impacto: afecta al
35 % del score de 10 fondos, y dos de ellos están en el top-65.

### Fase B — estructura por clase

| # | Viabilidad | Qué rompe | Esf. |
|---|---|---|---|
| **4** Score sin diversificación | **Alta**, y es el de más impacto. | 483/490 puestos. `FORMULA`, los pesos, `OpportunityRead.diversification`, el glosario, la ficha y el frontend. `test_filtering_does_not_change_any_score` sigue pasando. | M |
| **1** Clase de activo | **Media.** Ver la objeción abajo. | Todo. `_ScoredUniverse` guarda `rows` planas: o se añade `asset_class` a la fila y se agrupa al presentar, o se rompe la caché. `_universe_fingerprint` debe incluir la definición de clases. | L |
| **5** Ordenar por calificación | **Alta.** | `rank` se conserva (tu regla), se añade `rank_in_grade`. El frontend ordena por score hoy. | S |
| **7** Renombrar etiquetas | **Alta.** `GRADE_LABEL` + `glossary.js` + chips. Los slugs no cambian, así que las URLs y el `localStorage` guardados siguen valiendo. | Nada funcional. Hay que recompilar Tailwind si cambian clases. | S |
| **8** Sizing por clase | **Alta.** `stress_floor(asset_class)`. | Cambia el tamaño sugerido de 41 activos (22 cripto + 19 futuros) y de los fondos de renta fija. | S |

**Mi objeción al cambio 1, que es la más seria del documento.**

Propones percentiles dentro de cada clase. Con eso, **el mejor cripto de 22 sacaría
un score alto** — hoy los 22 son D o E. Es exactamente el fallo que `grading.py`
existe para tapar: *«un universo de 27 acciones malas produce igualmente un primer
puesto con score alto»*. Comparar dentro de la clase **garantiza** que cada clase
produzca su propio ganador con buena nota, tenga o no sentido comprarlo.

Y hay un problema de tamaño: con n=22, los percentiles de Hazen solo pueden valer
2,3 / 6,8 / 11,4 … 97,7. Con `opportunity_min_universe = 5` pasa el filtro, pero el
score deja de discriminar: la diferencia entre el 3.º y el 4.º cripto es de 4,5
puntos fijos, venga de donde venga.

No digo que no lo hagas. Digo que **el cambio 1 obliga a que la calificación A–E
absoluta sea obligatoria y visible en todas partes**, nunca el score solo, y que
las clases pequeñas declaren su tamaño en la propia tarjeta. Si el cambio 1 entra
sin el 6, el tablero acabará recomendando el menos malo de 22 criptos con un 90.

### Fase C

| # | Viabilidad | Comentario | Esf. |
|---|---|---|---|
| **10** Renta fija directa | **Media.** Ver el Paso 2: las tasas de referencia se automatizan; las posiciones no. Tabla nueva + Alembic + `registry.py`. Y hay que decidir cómo valorarla sin romper "el ledger es la única fuente de verdad". | L |
| **11** Plan de asignación | **Alta**, y es el que más te va a servir para armar la cartera. Es un dato del usuario, como bien dices. Tabla + Alembic. | M |
| **13** Notas fiscales | **Alta** como banderas `info`. **No verifiqué ninguna de las cifras fiscales que citas** y no las pondría en la interfaz sin una fuente oficial con fecha. | S |
| **14** Validación hacia adelante | **Alta**, y **ya tienes la mitad**: `fundamental_snapshots` es una serie temporal (§2.1) y `scripts/snapshot_ranking.py` es la otra mitad. Falta programarlo semanalmente y cerrar las sincronizaciones a medias. | M |

Sobre el 14: no sirve de nada empezar a guardar fotos si la sincronización deja
fuera a 400 de 494 activos tres días de cada once, como pasó este mes.

---

## 5. Paso 2 — de dónde salen los datos de renta fija

Probado en vivo el 2026-09-20. `robots.txt` comprobado en cada dominio.

| Fuente | Sirve para | Cobertura | Retraso | Auth | Formato | Límites / términos |
|---|---|---|---|---|---|---|
| **Banrep API** (`suameca.banrep.gov.co/estadisticas-economicas-back/rest/…`) | **(a) tasas de referencia** | Cero cupón TES COP y UVR a 1/5/10 años (5.763 puntos, **desde 2003**); IBR ON/1m/3m/6m/12m (desde 2008); DTF y CDT 180/360 (desde **1984**); tasa de política (desde 1998); inflación y meta; UVR diaria | TES **9 días**; IBR 2 días; política **mismo día** | **No** | JSON `[epoch_ms, valor]` | Sin cuota publicada. Requiere cabecera `Referer`. **Ver la nota TLS.** |
| **datos.gov.co / Socrata** (`axk9-g2nh`) | **(c) oferta real de CDT** | Tasa y monto de emisión de CDT **por banco y por plazo** (30/60/90/120/180/360/>360). 2.069.978 filas desde 2018 | **4 días** | **No** (API key opcional, sube la cuota) | JSON/CSV, SoQL (`$where`, `$group`, `$order`) | `robots.txt`: `Crawl-delay: 1`, `/resource/` permitido. Sin clave hay cuota por IP |
| **yfinance `funds_data`** | **(b) instrumento** | Duración, vencimiento, calidad crediticia, ratio de gastos, composición por clase de activo | Diario | No | Objeto Python | Ver **fiabilidad** abajo |
| **yfinance `^TNX/^TYX/^FVX/^IRX`** | (a) tasas de EE. UU. | 16.166 barras **desde 1962** | 1-2 días | No | Igual que cualquier ticker | Ya lo tienes integrado: es un símbolo más |
| **yfinance `GXTESCOL.CL`** | **(b) instrumento** | ETF de TES en la BVC, **en COP**, 333 barras desde 2025-05 | 1-2 días | No | Igual que cualquier ticker | Sin `funds_data` (Yahoo no cubre fondos de la BVC) |
| **FRED** | (a) tasas de EE. UU. | `fredgraph.csv?id=DGS10` funciona **sin API key** (HTTP 200, 269 KB) | 1-2 días | No para CSV | CSV | `robots.txt` prohíbe `fredgraph.png`, **no** el `.csv`. `Crawl-delay: 1` |

### Confirmo tu hipótesis, con dos correcciones

> «Las tasas de referencia se pueden automatizar, pero las posiciones individuales
> de TES, CDT y FIC serán de carga manual.»

**Confirmada en lo esencial.** Las dos correcciones:

**1. La oferta de CDT SÍ se automatiza, y es oficial.** Del corte del 2026-09-16:

```
13,56 %  Coltefinanciera        > 360 días
13,48 %  Banco Pichincha          360 días
13,02 %  Bancolombia            > 360 días   (monto emitido: 371.087.758)
12,85 %  Lulo Bank              > 360 días
```

Matiz que hay que declarar en la interfaz: son tasas **efectivamente pactadas**
ponderadas por monto emitido, no la tasa de la vitrina. Es un dato mejor, pero no
es lo mismo.

**2. Hay exposición a TES sin carga manual: `GXTESCOL.CL`**, ETF de la BVC en COP
con serie de precios diaria. Entra en el tablero como un activo más, con su
histórico, su volatilidad y su drawdown. Lo que no da es duración ni rendimiento
al vencimiento.

Lo que sigue siendo manual: **un TES concreto, un CDT concreto, una FIC**. No hay
fuente pública que los cotice por instrumento.

### Lo que NO es fiable (refuto parte de tu premisa)

**La duración que da Yahoo está mal.** Medido contra lo que esos fondos son:

```
fondo   Yahoo   real (aprox)
SJNK     6,48    ~1,9   <- es un fondo de CORTO plazo
TLT      3,60    ~16    <- es el de 20+ años
AGG      3,87    ~6
TIP      1,34    ~6,6
USHY     6,72    ~3,4
```

No hay un patrón: no es un factor de escala ni una unidad distinta. **No usaría
`bond_holdings.Duration` para nada.** `Credit Quality` viene vacío en los cinco
fondos; en cambio el diccionario `bond_ratings` sí trae el reparto por
calificación (SJNK: bb 54,8 %, b 33,4 %, below_b 9,6 %).

Lo que **sí** es fiable de `funds_data`, en los 12 fondos que probé: `categoryName`,
`legalType`, `asset_classes` y el ratio de gastos (IVV 0,03 %, GSG 0,75 %, DBA 0,85 %).

**`sector_weightings` también miente**: para SJNK devuelve
`communication_services: 1.0` en un fondo que es 98,7 % bonos.

### Nota TLS — esto te va a morder

El servidor de Banrep **no envía el certificado intermedio**:

```
0  CN=suameca.banrep.gov.co        emitido por: GeoTrust EV RSA CA G2
1  DigiCert Global Root G2         <- salta directamente a la raíz
```

Falta el eslabón `GeoTrust EV RSA CA G2`. Consecuencia medida:

```
curl (usa el llavero de macOS, resuelve el intermedio por AIA)   -> HTTP 200
httpx / requests (usan certifi)  -> SSL: CERTIFICATE_VERIFY_FAILED
```

**No se arregla con `verify=False`.** Se arregla añadiendo el intermedio al
bundle, y lo dejé comprobado:

```
descargado de  http://cacerts.digicert.com/GeoTrustEVRSACAG2.crt
httpx con el intermedio añadido: 200, 78.136 bytes, verificación ACTIVA
```

### Recomendación

1. **Banrep para todo lo colombiano.** Cubre TES cero cupón, IBR, DTF, CDT,
   política e inflación, con más historia y menos retraso que cualquier
   alternativa. Un solo cliente, con el intermedio empaquetado.
2. **FRED no hace falta.** Probé cinco series de Colombia
   (`IRLTLT01COMM156N`, `COLCPIALLMINMEI`, `IR3TIB01COM156N`, `INTDSRCOM193N`,
   `CCUSMA02COM618N`): **las cinco dan 404**. Y para EE. UU. ya tienes `^TNX` por
   yfinance, que es el mismo camino que el resto del tablero.
3. **datos.gov.co solo cuando vayas a comprar un CDT.** No es un dato de
   seguimiento diario.
4. **`funds_data` sí, pero solo para clasificar y para el ratio de gastos.**
   Duración y calidad crediticia, no.

---

## 6. El orden que yo haría

Distinto del tuyo en dos puntos: subo el **4** a lo primero, y meto la corrección
de valoración de fondos en la fase A.

**Fase 0 — antes de cambiar una fórmula** (no cambia ningún puesto)
- Arreglar el relleno de histórico hacia atrás (§3.7a) y traer 5 años.
- Cerrar las sincronizaciones a medias (§2.1).
- Programar `scripts/snapshot_ranking.py` semanalmente (mitad del cambio 14).

> Por qué primero: los cambios 8, 9 y 14 se apoyan en el histórico, y hoy se
> apoyan en 419 días. Y si vas a mover el ranking, conviene tener fotos antes.

**Fase A — datos rotos** (cambian puestos, pero corrigiendo mentiras)
- Valoración de fondos (§3.3) ← **no está en tu documento**
- Cambio 2, cambio 9, cambio 12, cambio 6

**Fase B — la fórmula**
- Cambio 4 (y con él, reevaluar si el 3 sigue haciendo falta)
- Cambio 5, cambio 7, cambio 8
- Cambio 1 **solo si el 6 ya está dentro**

**Fase C**
- Cambio 11 (el que más te sirve para empezar), 14, 10, 13

---

## 7. Lo que no pude verificar

- **Las cifras fiscales del cambio 13.** No consulté ninguna fuente oficial. No
  las pondría en la interfaz tal como están.
- **Los suelos de estrés que propones por clase** (≥70 % para cripto). Con 419
  días de histórico no puedo medirlo: BTC no ha tenido en esta ventana una caída
  que lo confirme ni lo refute. Se puede comprobar después de la fase 0.
- **La duración "real" de los fondos de §5.** La saqué de lo que esos fondos son
  por mandato, no de una fuente de datos. Lo que sí está medido es que las cifras
  de Yahoo son incoherentes entre sí (SJNK, de corto plazo, con más duración que TLT).
- **Si Yahoo cubre FICs colombianas.** Encontré `HCOLSEL.CL` y `TEVAICOL.CL` en la
  BVC y no los examiné.
- **Qué distribución de calificaciones quieres.** Es tu pregunta abierta y sigue
  abierta: es una decisión, no un dato.
