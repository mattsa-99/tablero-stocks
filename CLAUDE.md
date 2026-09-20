# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Dashboard de inversiones: FastAPI + SQLite + yfinance, con frontend Jinja2 +
Tailwind + Alpine.js + Chart.js servido por la misma app. Contexto de uso:
inversor en Colombia que opera con brokers en **USD** (eToro, XTB, IBKR) y un
comisionista local en **COP** para la BVC.

La divisa base por defecto es **USD**, y no es una simplificación: es la
divisa de tres de los cuatro brokers, reduce la superficie de conversión de
476 activos a 18 y desbloquea la curva de valor y la comparación con el índice
sin necesidad de tipos de cambio. El peso NO se esconde: la atribución de
divisa lo separa explícitamente (ver más abajo).

El código, los comentarios y los mensajes de la interfaz están **en español**.
Mantén ese idioma al añadir código.

## Comandos

```bash
source .venv/bin/activate          # el venv ya existe con todo instalado

pytest -q                          # 650 tests, ~10 s, sin red
pytest tests/test_pnl.py -q        # un archivo
pytest tests/test_pnl.py::test_single_buy -q
pytest -k "simulation and not api" # por expresión

ruff check .                       # lint
ruff check --fix .

python scripts/ficha.py AAPL          # ficha de compra en Markdown (solo lectura;
                                   #   --portfolio N, --db ruta)

alembic upgrade head               # tras actualizar: crea journal_entries
alembic downgrade -1
alembic revision --autogenerate -m "descripción"

uvicorn app.main:app --reload      # http://127.0.0.1:8000

npm install                        # solo para compilar el CSS
npm run build:css                  # OBLIGATORIO tras tocar clases de Tailwind
npm run watch:css                  # lo mismo, en continuo
```

`app/static/css/tailwind.css` **se genera y se versiona**: ejecutar la app no
necesita node, pero añadir una clase a una plantilla o a un .js sí exige
recompilar. Una clase que no esté en el CSS no lanza nada -el elemento sale
sin estilo-, así que `test_frontend_assets.py` compara las clases usadas
contra las generadas y convierte ese silencio en un fallo.

Los tests **no tocan la red**: `tests/fakes.py::FakeProvider` implementa el
Protocol `MarketProvider` con datos sintéticos y se inyecta con
`app.dependency_overrides[get_provider]`. `conftest.py` fuerza
`TABLERO_ENABLE_BACKGROUND_REFRESH=false` antes de importar nada de `app`,
porque el lifespan arrancaría el planificador y saldría a Yahoo de verdad.

## Arquitectura

### El ledger es la única fuente de verdad

No existe tabla `positions`, ni `cash_balances`, ni `pnl`. Todo se deriva
reproduciendo `transactions` en orden cronológico (`app/services/pnl.py`,
lógica pura sin BD ni red). Antes de añadir una tabla de estado agregado,
asume que la respuesta es "se calcula".

El replay ordena por `(executed_at, id)` — el desempate por id es lo que lo
hace determinista. Se puede insertar una transacción con fecha **pasada**, así
que toda escritura revalida el ledger completo con `strict=True`, no solo
compara contra la posición actual.

Método contable: **coste medio ponderado**. Vender no cambia el coste medio de
lo que queda.

### Frontera Decimal / float

Es la regla que sostiene la exactitud del P&L:

- Dinero del usuario (`quantity`, `price`, `fees`, `cash_amount`,
  `fx_rate_to_base`) → `Decimal` exacto vía `ExactNumeric` (`app/db/types.py`),
  que en SQLite guarda TEXT porque el dialecto pasaría por float.
- Precios de mercado y ratios fundamentales → `float`.
- El cruce **siempre** por `to_decimal()` (`app/core/money.py`), que pasa por
  `str`. `Decimal(0.1)` arrastra la representación binaria; `Decimal(str(0.1))` no.

Consecuencia: las columnas `ExactNumeric` no admiten `SUM()`/`ORDER BY` en SQL
bajo SQLite (comparación lexicográfica). Toda agregación financiera va en Python.

### Multi-divisa: `fx_rate_to_base` está desnormalizado a propósito

Cada transacción congela el tipo de cambio aplicado. Corregir `fx_rates` a
posteriori **no debe** mover el coste histórico. Si faltan datos de cambio, la
operación se **rechaza** en vez de asumir 1: en COP/USD eso erraría por ~4000×.

**Divisas en subunidad** (`app/providers/currencies.py`). Yahoo cotiza algunas
bolsas en la centésima parte de la divisa y lo señala SOLO con la caja del
código: `GBp` son peniques (LSE), `ILA` agorot, `ZAc` centavos. Un `.upper()`
los convierte en `GBP`/`ILS`/`ZAR`, divisas reales y 100× mayores. Se
normalizan en la frontera del proveedor —cotizaciones, metadatos **y barras
históricas**— para que aguas abajo nadie vuelva a ver `GBp`. Como
`yf.download` no devuelve divisa, el histórico se resuelve sondeándola, pero
solo en los sufijos donde el problema existe (`.L`, `.TA`, `.JO`): el sufijo
decide **a quién se pregunta, nunca la respuesta** (en la LSE conviven valores
en GBp, GBP y USD).

**Triangulación vía USD.** La mayoría de divisas no tiene par contra el peso:
`HKDCOP=X`, `KRWCOP=X` y `MXNCOP=X` devuelven 404, mientras que todas cotizan
contra el dólar. `_triangulate_fx` compone `X/USD × USD/COP` y marca el
resultado en `source` (`yfinance:USD`) para que la procedencia sea legible.
No contradice la regla de rechazar lo ausente: son dos tipos **medidos**, no
un supuesto, y pasan la misma comprobación de sanidad que uno directo. Con el
pivote en un extremo no se triangula: sería circular.

**Sanidad de tipos.** `FX_SANITY_RANGES` mantiene overrides explícitos, pero la
banda general se **deriva** de `CURRENCY_USD_MAGNITUDE`, un orden de magnitud
por divisa (25 números en vez de las 600 filas que harían falta por par). El
umbral de "no comprobable" no es arbitrario: si el tipo esperado es `e`, el
invertido es `1/e` y distan `e²`, así que la inversión solo se detecta cuando
`e > √tolerancia`. Los pares cercanos a la paridad (EUR/USD) son
indetectables por magnitud y necesitan override.

### Capas y su frontera

```
routers/     HTTP. Sin lógica de negocio.
services/    Negocio. NO importan FastAPI ni yfinance -> testeables sin red.
repositories/ Consultas compartidas por varios servicios.
providers/   Frontera con Yahoo. Devuelve dataclasses propias, nunca objetos yfinance.
```

Los errores de dominio (`app/core/exceptions.py`) se traducen a HTTP en **un
solo punto**, `app/main.py`. Los servicios lanzan excepciones tipadas.

Los servicios **nunca** llaman al proveedor: leen de las tablas. Solo
`market_data.py` y `ingestion.py` cruzan a la red.

### Datos de mercado: degradación, nunca pantalla en blanco

Si yfinance falla se conserva el último valor, se marca `is_stale` y se acumula
un aviso en la respuesta. Un precio ausente es `None`, **nunca 0** (un 0 sería
una pérdida del 100% inexistente).

Dos cadencias: cotizaciones con TTL corto refrescadas al leer un portafolio;
barras diarias, fundamentales y FX en el job completo (APScheduler, 18:00
`America/New_York`, L-V). `data_sync_state` controla TTL y backoff por recurso;
`sync_runs` registra cada ejecución del pipeline y alimenta el indicador de
"última sincronización". El planificador tiene recuperación al arrancar porque
un portátil apagado a las 18:00 ET nunca vería el cron.

**Ninguna petición HTTP espera a la red.** `app/services/refresh_jobs.py`
encola el refresco para DESPUÉS de enviar la respuesta, con su propia sesión
y un candado no bloqueante. La regla nació de una medición: la vista de
oportunidades llamaba a `full_refresh` -el job de dos veces por semana- sobre
los 494 símbolos dentro de la petición, y tardaba **269 s** frente a los
**0,23 s** de puntuar lo ya guardado.

Del job completo, lo único que cambia intradía y le importa al ranking es el
PRECIO (de ahí sale `fresh_trailing_pe`): las barras diarias son cierres
inmutables, los fundamentales trimestrales y los metadatos tienen TTL de 30
días. Por eso el refresco de fondo es `refresh_quotes_and_fx` y no
`full_refresh`. La respuesta trae `refreshing` y el frontend sondea con
`refresh=false` hasta que baja; filtrar NO refresca, porque es una operación
de vista.

**El universo puntuado se memoiza, y la huella no es la fecha de la barra.**
Filtrar por región o calificación es una operación de VISTA -el motor puntúa
siempre el universo completo-, pero cada clic repetía el trabajo entero: 186 ms,
de los cuales 85 eran releer 135.851 barras para recalcular indicadores
idénticos. Con la caché, un clic cuesta **0,5 ms** (294x).

La tentación es cachear por «fecha de la última barra». Sería un error: las
cotizaciones cambian intradía y SÍ mueven el score, porque `fresh_trailing_pe`
rehace el P/E con el precio de ahora. Esa caché congelaría el ranking justo
durante el refresco de fondo, que es cuando el usuario está mirando. La huella
pregunta por agregados baratos (~1,2 ms), con `data_sync_state` como red
principal porque toda escritura del proveedor pasa por `mark_success`.
`count(*)` sobre `price_history` o `fundamental_snapshots` queda fuera: son 83
y 41 ms de barrido completo, más de lo que ahorran.

`_ScoredUniverse` es inmutable porque se comparte entre peticiones: si una
petición filtrada renumerara `rank`, la siguiente vería el destrozo. Y la caché
se resetea en `conftest.py` porque cada test estrena una base con los mismos
ids, así que dos tests distintos producen huellas idénticas con facilidad.

La excepción son los símbolos que el usuario **escribe** en `?symbols=`: esos
se cargan en línea, porque sin histórico no se pueden puntuar y desaparecerían
del ranking que se pidió a propósito. Son un puñado, no 494.

**Un fallo de 429 o de RED se apunta contra el proveedor, nunca contra los
símbolos.** Es la diferencia entre degradar y averiarse, y es el arreglo de
mayor impacto del pipeline. Ninguno de los dos dice nada sobre el ticker que
tocaba pedir -en el caso de la red ni siquiera se llegó a preguntar-, pero
cargárselo a cada símbolo lo mete en un backoff exponencial individual como si
estuviera roto.

Medido sobre el log real del agente de launchd: **1.282 fallos de DNS frente a
23 de rate limit**, o sea que el caso dominante es el portátil suspendido, no
Yahoo cortando. Reproducida la sincronización de 494 símbolos con la red caída:

    antes:  81 llamadas, 494 símbolos en backoff
    ahora:   1 llamada,    0 símbolos en backoff

En producción esas 81 llamadas eran intentos que expiraban -el log tiene
timeouts de 946 s- y por eso la sincronización del 18-09-2026 estuvo 67
minutos para terminar con 0 cotizaciones, 0 barras y 0 fundamentales.

`_translate_error` clasifica en tres, y el orden importa: el rate limit se
comprueba PRIMERO porque un 429 llega por una conexión que funcionó.
`ProviderUnreachable` es subclase de `ProviderUnavailable` para no romper a
quien ya la captura.

Los dos enfriamientos NO duran lo mismo, y confundirlos sale caro en ambos
sentidos. Un 429 es el proveedor pidiendo que pares: 15 min de base, techo de
6 h. Una caída de red es un problema NUESTRO y puede resolverse en cualquier
segundo: 2 min de base y **techo propio de 15 min**. Ese techo bajo evita
además un bloqueo real: mientras el enfriamiento corre no se llama a nadie, y
si no se llama a nadie no hay respuesta correcta que pueda cerrarlo.

Cualquier respuesta correcta cierra el enfriamiento (`clear_rate_limit`, desde
`mark_success`); sin eso el contador solo sube y se clava en el techo.

**`fetch_quotes` va en lote de verdad, y no lo parecía.** `yf.Tickers(...)` es
perezoso: se construye en 3 ms y cada `fast_info` posterior es una petición
HTTP propia de 0,42 s. Eran 494 viajes -208 s- y la ráfaga es lo que
disparaba el 429. Con `yf.download` el universo entero tarda **40 s** y trae
488 de 494. Dos detalles que no se pueden relajar:

- **Ventana de 5 días, no de un mes.** Con ventana larga, un símbolo cuya
  serie lleva semanas parada devuelve igualmente un número y lo presentaría
  como precio de hoy: AVB da 68,14 con `period="1mo"` -última barra del 24 de
  agosto- cuando cotiza a 184. Con cinco días no devuelve nada, que es el
  fallo correcto.
- **Reintento acotado de rezagados.** Una descarga grande deja caer símbolos
  bajo carga: faltaron 18 y al volver a pedir solo esos aparecieron 12 en
  3,5 s. Se reintenta UNA vez y solo si la primera pasada trajo algo: que no
  venga nada no son rezagados, es una caída o un rate limit.

`yf.download` no devuelve divisa, así que `QuoteData.currency` llega en `None`
y el consumidor usa la del activo, fijada por los metadatos, que es donde
`normalize_currency` ya corrió. Un `"USD"` por defecto marcaría como dólares
los 22 tickers de la BVC. El divisor de subunidad (`GBp`) sí se aplica en la
frontera, con el mismo sondeo acotado que usa el histórico.

### Motor de oportunidades

`Score = 0.35·V + 0.30·M + 0.15·D − 0.20·R + 20`, todo en [0,100]. El `+20` es
`0.20×100`: la transformación afín que lleva el rango natural `[−20, 80]` a
`[0, 100]` sin clipping.

Normalización por **rango percentil cross-seccional** (Hazen,
`100·(r−0.5)/n`), no z-score ni min-max: robusto a outliers y acotado. El score
es por tanto **relativo al universo evaluado**, no una nota absoluta — la
respuesta lo declara en `disclaimer` y el frontend debe mostrarlo.

Reglas que no son obvias: un P/E ≤ 0 no es "barato" (se descarta, no se trata
como valor bajo); un factor ausente se imputa 50 (el valor neutro por
construcción del rango percentil), pero un candidato al que le falten 2 de 3
factores informativos se **excluye**; el universo mínimo es 5.

Las series se leen con `adj_close`; sin ajustar por splits, un 2:1 aparece como
−50% e invierte el momentum.

### Valoración relativa al sector

El factor de valor NO compara el P/E de un banco con el de una tecnológica.
`opportunities.py` ancla la valoración a la **mediana de su sector**
(`sector_group()`, público) cuando hay al menos 8 pares y el activo es
STOCK/ADR/REIT; sin pares suficientes queda la señal absoluta contra el índice
de referencia, y sin ella, «sin dato» y nunca un neutro inventado. Los avisos
del ranking cuentan cuántos activos se quedaron sin señal de valoración.

### Ficha de compra (informativa, no recomienda)

`GET /api/opportunities/{symbol}/ficha` (`app/services/ficha.py`,
`schemas/ficha.py`) y `scripts/ficha.py` (mismo servicio, salida Markdown de
`ficha_markdown.py`). **Solo lee lo guardado: no llama al proveedor.** Reúne el
score y puesto, salud financiera, banderas, calendario, analistas, pares del
sector, contexto de cartera, tamaño de posición y frescura de los datos.

- **`health.py`**: solo ratios del MISMO reporte (deuda neta/EBITDA, margen
  operativo, caja libre/ventas, current ratio…), porque el reporte y la
  cotización pueden venir en divisas distintas y mezclarlos daría el mismo
  error que el P/B de CIB. Bancos: «no aplica» (el EBITDA no significa nada en
  un banco); financieras cautivas y sectores apalancados por estructura
  suavizan el umbral en vez de llenar de rojo.
- **`flags.py`**: banderas `red/yellow/green/info` con veredicto ASIMÉTRICO:
  una roja basta para advertir, pero la ausencia de rojas nunca dice «compra».
  Un activo excluido del ranking devuelve una bandera roja `not_ranked` con el
  motivo, no un 404.
- **`sizing.py`**: peso = presupuesto de riesgo ÷ pérdida en estrés, con tope.
  Estrés = máx(caída máxima observada, suelo del 35 % acciones / 25 % fondos):
  con ~1 año de histórico una calma reciente no prueba nada. Ajustes
  `position_risk_budget_pct` (2,0) y `position_max_weight_pct` (10,0) en
  `config.py`. `GET /api/opportunities/{symbol}/sizing` recalcula con otros
  supuestos. Es un techo por posición, no una orden.
- `OpportunityResponse.freshness` y `OpportunityRead.price_as_of /
  fundamentals_as_of` declaran la edad del dato; la interfaz avisa si es vieja.

Los tests de ficha marcan los activos como `is_universe=True` porque la ficha
puntúa contra el universo de ingesta, igual que el catálogo real.

### Diario de decisiones y vigilancia

`app/models/journal.py` (tabla `journal_entries`, migración `ae5068edce82`),
`services/journal.py`, `routers/journal.py`, vista `/diario`. Cada entrada
guarda tesis, qué la invalidaría, precio de invalidación y fecha de revisión
(por defecto +90 días, debe ser futura), más una **foto** de score, puesto,
calificación, veredicto, banderas y precio del día de la anotación.

Alertas: `invalidation_breached` (roja), `review_overdue`, `grade_dropped`
(≥2 escalones) y `not_ranked_now` (amarillas), `review_soon` (info). Una alerta
pide revisar, nunca vender. **Una entrada NO toca el ledger**: no crea
transacciones. Las entradas activas se unen al universo de ingesta
(`universe.watched_asset_ids`), así que un símbolo vigilado sigue recibiendo
precios aunque no esté en el catálogo del universo. Crear una entrada llama a
`ensure_data_for`.

### Importación de operaciones por CSV

`app/services/importer.py`, `POST /api/transactions/import?portfolio_id=&dry_run=`
y `GET /api/transactions/import/template`. Una plantilla fija
(`date,type,symbol,quantity,price,amount,fees,currency,fx_rate_to_base,notes,
external_id`), coma, punto decimal, sin adivinar formatos de broker: adivinar
mal corrompería el coste medio.

- **`dry_run=true` por defecto** y **todo o nada**: una fila mala aborta todo.
- Solo fecha = mediodía de Bogotá (-05:00). Con hora y desfase se respeta.
- Se reproduce el ledger con `strict=True`; si falla, una búsqueda binaria sobre
  el prefijo localiza la PRIMERA fila culpable, y el mensaje nombra el símbolo.
- `external_id` determinista (sha1 del contenido + ocurrencia) si no viene:
  subir el mismo archivo dos veces no duplica.
- La columna `currency` es obligatoria para símbolos desconocidos: sin ella no
  se puede asumir la divisa, y asumir USD marcaría en dólares una acción de la BVC.
- El frontend es `import.js` + el modal de `dashboard.html`.

### Vista de oportunidades: qué se ve por defecto

`opportunities.js` arranca con `DEFAULT_TIERS=["A","B"]` para que quien empieza
no vea primero lo malo; un aviso cuenta cuántas quedan fuera y **Ver todos**
guarda `tablero:tiers="[]"`. `hiddenByDefault` distingue esa situación de un
filtro elegido a propósito. Cada tarjeta abre la ficha (`ficha.js`).

### Autocompletado de símbolos

`GET /api/market-data/search?q=nvi` busca por **nombre o ticker parcial**:
catálogo local primero (instantáneo, sin red, y es lo que al usuario le
interesa), luego el buscador de Yahoo. Un fallo del proveedor **no** es un
error del endpoint: devuelve lo local con un aviso.

La caché de búsqueda es **en memoria con TTL** (`app/services/search.py`), no
`data_sync_state`: son miles de claves efímeras — un autocompletado dispara una
consulta por pulsación — y persistirlas convertiría esa tabla en basura.

Yahoo no devuelve divisa ni sector al buscar. Se resuelven con
`GET /api/market-data/resolve/{symbol}`, **una sola vez al seleccionar**, no una
por sugerencia. Ese endpoint no persiste nada porque también lo usa el
simulador.

El combobox (`app/static/js/combobox.js` + `templates/partials/
symbol_combobox.html`) es único y compartido por los dos formularios. Emite
`symbol-selected` **dos veces**: al elegir y tras resolver. Por eso
`applySymbol` solo sobrescribe divisa y sector **si vienen** — sin esa guarda,
el primer evento pisaría al segundo.

**El precio se prellena SOLO en el simulador.** Una simulación es siempre "si
lo hiciera ahora", así que el precio actual es el pertinente. En el alta de una
transacción real la fecha la elige el usuario y puede ser pasada: prellenar el
precio de hoy en una compra de hace tres semanas es un dato falso, fácil de
guardar sin mirar, que queda congelado en el coste medio para siempre. Allí se
ofrece con un botón «usar» y su fecha visible. `test_only_the_simulator_
prefills_the_price` fija la distinción.

### Catálogo vs universo de ingesta

La distinción que hace escalable el sistema frente al rate limit de yfinance:

- **Catálogo** (`assets` con `is_universe=False`, sembrado desde
  `app/data/catalog.json`): miles de filas posibles. Solo alimenta el buscador.
  Cuesta bytes, no llamadas. **Nunca se ingesta en bloque.**
- **Universo de ingesta** (`is_universe=True` o con posiciones): hoy 494
  símbolos, **solo en USD y COP**. Precios, fundamentales e histórico
  refrescados en lotes con pausa (`ingestion_batch_size`,
  `ingestion_batch_delay_seconds`). Medido: el cuello de botella es `.info` a
  ~0,76 s/símbolo, así que una sincronización completa ronda los 7 minutos.

**El universo se limita a las divisas en que se financia la cartera** (COP y
USD, `candidates.FUNDING_CURRENCIES`). No es simplificación cosmética:

1. El comisionista compra en COP o USD, así que rankear un valor en yenes
   produce recomendaciones sobre las que no se puede actuar.
2. Y es de CORRECCIÓN: `momentum_12_1`, la volatilidad y el drawdown se
   calculan sobre la serie en divisa **local**, sin convertir, porque no existe
   histórico de tipos de cambio (`fx_rates` solo guarda el día en curso).
   Medido sobre 2 años: Toyota da +4,0% de momentum en Tokio y −4,7% vía su
   ADR — **cambia de signo**; ASML, 130,9% frente a 114,8%. Restringir el
   universo elimina el error por construcción en lugar de parchearlo: el precio
   de un ADR ya incorpora el movimiento de la divisa, y la BVC cotiza en COP.

**Una empresa, un listado.** `candidates.UNIVERSE_EXCLUSIONS` retira del
universo los símbolos que duplican a otro ya presente: el ADR cuando la empresa
cotiza en la BVC (va ECOPETROL.CL, no EC), la ordinaria cuando existe la
preferencial (va PFAVAL.CL), y GOOG frente a GOOGL. No es estética: la guarda
de "ya lo tienes" de `suggestion.py` compara SÍMBOLOS, así que con los dos
listados dentro, tener uno y recibir el otro como sugerencia sería doblar la
apuesta en la misma empresa presentada como diversificación.
`test_no_company_appears_twice_in_the_universe` lo vigila por nombre
normalizado, no por una lista de pares, para que un duplicado nuevo salte solo.

La exposición internacional entra por **ETFs de país** (EWJ, EWZ, MCHI) y
**ADRs** (TM, TSM, NSRGY, BABA, PBR), todos en USD. Las primarias extranjeras
(7203.T, SHEL.L, SAP.DE) siguen en el catálogo: buscables y con carga perezosa
si algún día se compran, pero fuera del ranking.

Si algún día se financia en otra divisa, ampliar `FUNDING_CURRENCIES` NO basta:
antes hay que convertir las series a la divisa base, y eso exige un histórico
de tipos de cambio que hoy no existe.

La pertenencia al universo se declara **dentro del catálogo** (`"universe":
true`), no en una lista aparte. Dos listas se desincronizan, y un símbolo del
universo ausente del catálogo nacería con los valores por defecto -USD y
STOCK-, que para un valor japonés o un ETF de bonos es falso.
`universe.DEFAULT_UNIVERSE` queda solo como respaldo si no hay catálogo.

`app/data/catalog.json` **se genera, no se edita a mano**:
`python scripts/build_catalog.py` verifica cada candidato de
`scripts/candidates.py` contra Yahoo y descarta el que no devuelva precio y
divisa. Un descarte solo cuenta tras reintentar: un 429 pasajero expulsaría a
un valor real (le pasó a MMC y a ROG.SW).

**No se generó con el screener de Yahoo, y se intentó.** Ningún filtro resultó
fiable para "¿es de este país o una cotización cruzada?": las 25 mayores
"alemanas" del screener son TODAS ADRs estadounidenses (NVD.DE es NVIDIA), el
campo `market` solo repite la región consultada, y filtrar por
`currency != financialCurrency` sí las caza pero excluye a HSBC, Shell,
AstraZeneca y BHP -británicas que reportan en USD-. Importar eso sería peor que
no ampliar: el usuario creería diversificar geográficamente comprando NVIDIA
otra vez, con ruido de divisa encima.

`ingestion.ensure_data_for()` es la carga perezosa: trae bajo demanda lo que
falte. Mira **barras almacenadas**, no el TTL — responde "¿existe?" y no
"¿está fresco?". El endpoint de oportunidades usa el universo de ingesta, no
`get_active_assets()`: solo se puede rankear lo que se ha medido.

El catálogo NO inventa sectores: los copia verificados de Yahoo o los deja
nulos, y nunca sobrescribe datos del proveedor al resembrar.

### Motor de sugerencia

`Sugerencia = OpportunityScore × FactorCorrelación × FactorConcentración`.
Multiplicadores, no sumandos: un activo excelente en un sector al 60% debe caer
proporcionalmente, no perder puntos fijos que su score compense.

- `correlation_factor(None) == 1.0` — un rho no medible es **neutro, nunca una
  prima**. Premiar por una descorrelación que nadie midió es el fallo más
  traicionero de este motor.
- La correlación empareja **por fecha**, no por posición
  (`correlation.paired`). Emparejar por índice da un número plausible y falso.
- La comprobación de "ya lo tienes" usa el ledger, **no las posiciones
  valoradas**: sin tipo de cambio nada se valora y recomendaría lo que ya
  posees.
- Guarda de calidad: no se sugiere nada calificado D/E. Sin ella, este motor
  reintroduce el problema que la calificación absoluta resolvió, ahora bajo un
  botón que promete lo óptimo.

### Calificación absoluta (complementa al score)

El score es **ordinal**: dice quién es el mejor del conjunto, nunca si ese
mejor es bueno. `app/services/grading.py` añade una calificación **A–E** que no
mira a los otros candidatos, sino a anclas independientes:

1. **Valoración** contra el P/E del índice de referencia (SPY), no contra los
   demás candidatos.
2. **Tendencia**: el signo de una tendencia es absoluto por definición.
3. **Riesgo** contra bandas fijas de volatilidad y drawdown.
4. **Calidad**: ROE, margen, deuda y crecimiento contra umbrales contables.

Reglas que no se pueden relajar sin devolver el problema:

- Se califica por la **proporción** `points/max_points`, no por puntos
  absolutos: el máximo alcanzable varía (8 para una acción, 4 para un futuro
  sin fundamentales) y con umbrales fijos un futuro nunca podría sacar A.
- Con menos de 2 de 4 señales → **SIN_CALIFICAR**, jamás "Normal": un neutro
  por defecto afirma algo sin base.
- `universe_quality_warning` avisa cuando NINGÚN candidato llega a "Buena".
  Es lo que impide presentar como oportunidad al menos malo de un mal grupo.
  `test_a_bad_universe_produces_a_bad_first_place` lo fija.

**Escalas de yfinance, fáciles de confundir**: ROE, margen y crecimiento son
fracciones (0.1779 = 17,8%); `debtToEquity` viene en **porcentaje**
(380.26 = 3,8x); `dividendYield` también en porcentaje.

### Filtro de datos rotos del proveedor

`app/services/data_quality.py`. Yahoo calcula los ratios que mezclan PRECIO
con CONTABILIDAD POR ACCIÓN **sin convertir la divisa**, y el error no lanza
nada:

    CIB          cotiza USD, reporta COP  ->  price_to_book = 0,0022
    MINEROS.CL   cotiza COP, reporta USD  ->  price_to_book = 9.267

Medido: con el 0,0022 la valoración de CIB sale 94,6 y encabeza el ranking;
con un valor plausible baja a 84,7 y cae del puesto 1 al 4. Un dato roto
decidía la primera recomendación.

**El P/E NO está afectado y eso se comprobó**: Yahoo sí convierte el beneficio
antes de dividir (`precio / eps` coincide exactamente con su `trailingPE` en
CIB, MINEROS.CL, TM, ASML y AAPL). Por eso solo se descartan `price_to_book`,
`price_to_sales` y `ev_to_ebitda`.

Dos redes, porque una sola no basta: la comparación de divisas es exacta
porque ataca la causa, pero no ve a `BRK-B` -cotiza y reporta en USD y aun así
da 0,001, comparando el precio de la clase B con el valor contable de la clase
A-; para eso está la banda de magnitud. La banda es ANCHA a propósito: el P/B
de 303 de Colgate es real y debe pasar.

Se **descarta, no se corrige**: reescalar por el tipo de cambio da 6,97 para
CIB, todavía implausible, porque un ADR representa varias acciones locales y
esa proporción no viene en los datos.

**`trailing_pe` se recalcula con el precio de ahora** (`fresh_trailing_pe`).
Es precio entre beneficio, así que se mueve a diario aunque el beneficio sea
trimestral. Medido: 1,6% de desviación media y 7,6% máxima, que mueve a un
candidato 1,7 puestos de media. Poco, pero no cuesta ni una llamada de red.

### Series de precios detenidas

`price_series_max_age_days` (10 días). Por encima de eso NO se calculan
momentum, volatilidad ni drawdown.

Es el fallo más traicionero de los tres porque no se veía: **una serie parada
calcula sus indicadores igual de bien que una viva**. GXG llegó al puesto 8 con
`data_completeness` 1.00 y confianza «alta», con la última barra de hacía seis
semanas. `data_completeness` mide si el factor se pudo calcular, no si el dato
está fresco.

### Filtros de la vista de oportunidades

`GET /api/opportunities` acepta `?regions=US,COL` y `?quality_tiers=muy_buena,buena`.
Dos reglas que no se pueden relajar:

- **Se filtra DESPUÉS de puntuar, nunca antes.** El score es un rango
  percentil: su valor depende de contra quién se compara. Filtrar el universo
  antes de puntuar haría que "solo Colombia" recalculara los percentiles entre
  19 activos y un valor mediocre saliera con 90 puntos por no tener rivales.
  `test_filtering_does_not_change_any_score` lo fija.
- **`rank` es la posición en el ranking COMPLETO**, con huecos (#1, #4, #32).
  Ver que el mejor colombiano es el #32 de 494 es información; renumerarlo a #3
  la destruiría e insinuaría que encabeza el ranking.

**El selector «Mostrar» llega a 200, y ese tope está medido.** Puntuar cuesta
lo mismo con cualquier límite (~165 ms): el universo entero SIEMPRE se evalúa
y `limit` solo recorta lo que se devuelve. El coste es el DOM: 100 tarjetas son
14.700 nodos y 447 ms; 200 son 28.900 y 563 ms; las 494 son 70.300 nodos, 878
ms y **65.547 px de página**, o sea 65 pantallas de scroll, que ya no es una
vista sino una maratón. Se corta en 200 porque con cualquier filtro de mercado
puesto se ve la región completa -la mayor tiene 170- y porque más allá la
cuadrícula deja de poder recorrerse.

La cabecera dice siempre **«Mostrando N de M»** distinguiendo tres cifras: las
tarjetas en pantalla, las que pasan el filtro y las evaluadas. Antes, con el
límite en 10, se veían diez tarjetas sin ninguna señal de que había 480 más.

`grade_counts` y `region_counts` se calculan sobre el universo entero y NO
sobre el resultado filtrado: si siguieran al filtro, al pulsar «Muy buena» los
demás chips marcarían cero y no habría forma de saber qué queda por explorar.
`matched_size` es lo que el filtro deja ver.

El estado de la vista (filtros, «Mostrar» y candidatos extra) va en la URL con
**los mismos nombres que la API**, no traducidos al español como las rutas: la
cadena de consulta de la página se pega tal cual detrás de `/api/opportunities`
y no hay tabla de equivalencias que mantener. La URL MANDA sobre el
`localStorage` al restaurar -un enlace compartido es una petición explícita-, y
se escribe con `replaceState` porque cada clic ajusta la misma vista y no es un
destino nuevo.

Un valor desconocido en cualquiera de los dos parámetros se **ignora**, no
devuelve 422: son parámetros de interfaz y fallar dejaría la vista en blanco.

**La región es el DOMICILIO de la empresa, no su bolsa** (`app/services/
regions.py`). `CIB` cotiza en Nueva York y es Bancolombia. Como el universo se
financia solo en USD y COP, casi todo cotiza en bolsas estadounidenses:
clasificar por bolsa devolvería ~95% en «EE.UU.» y dejaría Europa, LatAm y Asia
vacías. Se usa `Asset.country` del proveedor; los ETFs no traen país, así que
su geografía se declara en `REGION_BY_SYMBOL` y el resto se resuelve con el
cubo de exposición para que filtro y diversificación no clasifiquen distinto.

Seis cubos: US, COL, LATAM, EU, ASIA y GLOBAL. Colombia va aparte de LatAm
porque es el mercado local. `ASIA` se mantiene **estricto**: Australia, Canadá
y Sudáfrica quedan en GLOBAL aunque los índices hablen de «Asia-Pacífico»,
porque el chip dice «Asia» y meter ahí a BHP haría que el filtro afirmara algo
falso. GLOBAL es el cajón honesto, no un descarte.

El frontend **duplica** `REGION_ORDER` y `GRADE_SLUG` en `opportunities.js`
-no hay build que los genere-, así que
`test_the_frontend_knows_every_region` compara las dos tablas: añadir una
región solo en Python la dejaría sin chip y sin ningún error visible.

### Centro de ayuda (glosario)

`app/static/js/glossary.js` + el drawer de `base.html`. Cada ficha lleva
**definición y ejemplo obligatorios**: la definición sola deja al usuario
entendiendo la frase y sin saber qué hacer con el número que tiene delante.

Los ejemplos usan **cifras reales verificadas** contra la propia aplicación
(la cartera de prueba y la ficha de CIB). Un ejemplo inventado que no cuadre
con lo que se ve en pantalla enseña a desconfiar de la pantalla.

`caveat` es para las métricas cuyo dato puede llegar MAL del proveedor. El
caso vivo es `price_to_book`: Yahoo divide el precio del ADR en dólares entre
un valor contable en pesos y devuelve **0,0022 para CIB** (y algo parecido en
BRK-B, precio de la clase B contra valor contable de la clase A). Sin el aviso,
el glosario enseñaría a leer ese dato roto como una ganga histórica.

`test_every_factor_input_shown_in_the_ui_is_explained` lee las claves de
`inputs={...}` del servicio y exige que todas aparezcan en el glosario: un
sub-factor nuevo sin documentar saldría en la interfaz como un nombre técnico
crudo.

### Cubos de exposición

`app/services/exposure.py`. La diversificación NO se mide por sector GICS a
secas: Yahoo no da sector a los ETFs, y el motor lo leía como carencia de datos
imputando el neutro (50) — penalizando justo a SPY, lo más diversificador que
existe. Ahora cada activo cae en un cubo: sector GICS, «Diversificado»,
«Renta fija» o «Materias primas». Se usa igual en `portfolio.sector_weights`,
en el simulador y en el motor de oportunidades; si divergen, el "antes" del
simulador y el factor de diversificación medirían cosas distintas.

### Simulador What-If

`POST /api/portfolios/{id}/simulate` no escribe nada, **por construcción**:
las transacciones simuladas nunca pasan por `db.add()`, no se les asigna la
relación `portfolio` (la cascada las persistiría), todo corre en
`db.no_autoflush`, y los símbolos nuevos usan stubs con **id negativo**.
`test_simulation_writes_nothing` cuenta filas antes y después.

Se eligió memoria sobre SAVEPOINT+rollback: SQLite serializa escritores y una
simulación mantendría un write lock durante todo el cálculo.

Diversificación por HHI (`100·(1−Σwᵢ²)`), no por conteo de sectores. Es escala
**relativa**: con 11 sectores GICS el techo real es ~90,9.

### Histórico de precios

`price_history` es `WITHOUT ROWID` y sin columnas de procedencia por fila
(esa información vive en `sync_runs`). Medido: 159 → 95 B/fila. La poda de
columnas aportó más que el `WITHOUT ROWID`.

**No leas series activo por activo.** Usa
`market_repo.get_price_series_bulk()`: una consulta con tuplas en vez de N con
objetos ORM (107 ms → 23 ms con 120 activos). Para escribir, `app/db/bulk.py::
insert_ignore_duplicates` (ON CONFLICT DO NOTHING, consciente del dialecto).

### Rendimiento en el tiempo

`app/services/performance.py` reproduce el ledger a lo largo del calendario de
cotización en vez de una sola vez. Todo se deriva, igual que el resto: no hay
tabla de valores históricos que pudiera desincronizarse.

**`close` y NO `adj_close`.** `adj_close` reescribe el pasado también por
dividendos, y esos ya están en el ledger como entradas de caja: contarlos dos
veces inflaría la curva. Y el último punto tiene que coincidir con el KPI que
se dibuja justo encima, que usa el precio real.
`test_the_last_point_agrees_with_the_dashboard` lo fija. Para medir
RENDIMIENTO (momentum, volatilidad, correlación) sigue mandando `adj_close`:
sin ajustar por splits, un 2:1 invierte el momentum. De ahí el parámetro
`adjusted` de `get_price_series_dated`.

**Relleno hacia adelante.** La BVC y la NYSE no cierran los mismos días. Sin
arrastrar el último valor conocido, cada festivo de un mercado abriría un
hueco en la curva del otro. Un día SIN precio no se dibuja: misma regla que en
todo el sistema, ausente es None y nunca 0.

**El índice recibe la misma aportación el mismo día.** La pregunta no es
«cuánto subió SPY» sino «cuánto tendría yo si ese dinero, puesto ese día,
hubiera ido a SPY». Cuando la cartera no registra depósitos -deliberado, es lo
que permite cargar un histórico ya existente- se toman las compras como
aportación; sin eso la caja queda negativa por el importe entero y
`total_value` muestra solo la plusvalía (88.870 COP en vez de 2,2 millones en
la cartera de prueba).

**XIRR por bisección**, no por Newton, que diverge con flujos irregulares -lo
que produce una cartera real- y devolvería un número plausible y falso. No se
anualiza por debajo de 90 días: sobre 18 días multiplica por 20 y da un «129%
anual» que solo significa «subió un 4% en tres semanas».

### Atribución de divisa

`unrealized_pnl` mezclaba lo que hizo la empresa con lo que hizo el cambio.
Medido: SPY subió 16,6% en dólares a un año mientras quien mide en pesos
perdía 5,5%, porque el peso se revaluó 19%. La descomposición es EXACTA:

    efecto activo = cantidad · (precio_hoy − coste_medio_local) · fx_hoy
    efecto divisa = cantidad · coste_medio_local · (fx_hoy − fx_medio)

y suma el P&L no realizado. El efecto del activo se mide al tipo de HOY porque
es la convención que deja el residuo en cero; repartir el término cruzado de
otro modo haría que las dos cifras no sumaran el total.

Para eso el replay lleva el coste **también en divisa local**
(`AssetPosition.total_cost_local`). Al vender se retira en la misma proporción
que el de divisa base, no con un coste medio local calculado aparte: con dos
cálculos independientes el redondeo los separaría y la atribución dejaría de
cuadrar.

### Histórico de tipos de cambio

`refresh_fx_history` rellena los CIERRES diarios. Dos reglas:

- **El día en curso no lo toca**: el «cierre» que Yahoo da para hoy es el
  último precio, y guardarlo lo congelaría como si la jornada hubiera
  terminado. Ese día lo mantiene `refresh_fx` con la cotización viva.
- **Un día ya cerrado SÍ se pisa** (`bulk.upsert`, no `insert_ignore_duplicates`).
  Una fila escrita por el refresco de jornada guarda la cotización viva del
  momento en que se pidió, no el cierre: medido sobre USD/COP, esas filas se
  desviaban del cierre entre 0,1% y 0,9%.

El relleno mira el rango COMPLETO almacenado, no solo la última fecha: con
solo el máximo únicamente sabe avanzar, y sobre la base real traía 2 filas
dejando intactos tres años de hueco anterior.

## Trampas conocidas

- **`yf.download` NO aplana el MultiIndex con un solo símbolo.** Las columnas
  siguen siendo `('AAPL', 'Close')`, así que leer el frame entero devuelve
  None en todas las filas y CERO barras sin lanzar nada. Estuvo roto en
  `fetch_history` y en `_download_quotes` a la vez, y solo mordía con
  exactamente un símbolo: la carga perezosa y los `?symbols=` tecleados. Se
  lee con `_sub_frame`, que acepta las dos formas porque yfinance ha cambiado
  este comportamiento entre versiones.
- **Tailwind no aplica `/10` a un color que es `var(--good)`.** No puede
  componer el canal alfa sobre una custom property opaca, así que la utilidad
  **no se emite** y la clase queda muerta en el HTML, sin advertencia. Regía
  igual bajo el Play CDN: `bg-good/10`, `bg-s1/10`, `bg-bg/85` y otras 24 no
  pintaron nunca. Se resuelve con `color-mix` en `tailwind.config.js`, que deja
  la paleta donde está (`app.css`).
- **No hay CDN.** Tailwind, Alpine y Chart.js se sirven desde
  `app/static/vendor/`. Sin red, la página se quedaba sin estilos y sin Alpine,
  o sea inservible: lo contrario de lo que hace el backend, que degrada y avisa.
  El plugin `collapse` va ANTES que el core de Alpine.

- **Un fallo de JavaScript en Alpine no se ve.** Expresiones que llaman a
  métodos inexistentes dejan la sección vacía sin error visible: tras tocar
  `ficha.js`, `journal.js` o `import.js` conviene abrir la vista en un
  navegador y mirar la consola, no fiarse solo de los tests.
- **`GET /api/opportunities` lanza un refresco de fondo** que puede mantener la
  base SQLite bloqueada para escritura mientras espera a la red
  (`busy_timeout` = 5 s): una transacción registrada en ese momento puede
  fallar con «database is locked».
- **`<template x-if>` no puede envolver a `<template x-for>`.** Alpine clona
  `firstElementChild`; si es otro template, el bucle no se inicializa y la
  sección se renderiza **vacía sin error en consola**. Calcula la colección en
  un getter del componente. `test_no_nested_alpine_templates` lo vigila.
- **Los estáticos van versionados (`static_url()` en `app/routers/views.py`).**
  `StaticFiles` no manda `Cache-Control`, así que el navegador aplica frescura
  heurística y puede servir su copia sin preguntar. Como la plantilla SÍ se
  renderiza en cada petición, el resultado es **HTML nuevo con JavaScript
  viejo**, y las expresiones de Alpine que apuntan a métodos inexistentes no
  lanzan nada: la sección se queda vacía. Pasó con los chips de mercado.
- **`GBp` no es `GBP`.** Difieren solo en la caja y valen 100× distinto. Es la
  trampa más cara del sistema porque no lanza nada: produce una posición
  valorada 100 veces de más que, si se registra una compra, queda congelada en
  el coste medio para siempre. Ver `app/providers/currencies.py`.
- **Las cotizaciones traen ruido de coma flotante.** IVV no cotiza a 764,92
  sino a 764.919982910156. Quien registra una compra al precio de hoy -que el
  formulario redondea a dos decimales- se queda con un P&L de −0,0000170898, y
  la pantalla mostraba «−US$ 0,00» EN ROJO: el signo y el color afirmaban una
  pérdida que la propia cifra desmiente. Se resuelve en PRESENTACIÓN
  (`roundsToZero` en `store.js`), no redondeando el dato: el backend mantiene
  Decimal exacto a propósito y recortar el precio rompería los activos que
  cotizan por debajo del centavo. Por eso `pnlClass` recibe los decimales con
  que se imprime la cifra: el color tiene que coincidir con lo que se lee al
  lado.
- **Pydantic serializa `Decimal` como string.** Todo valor monetario del JSON
  pasa por `window.fmt.num()` en el frontend; sin eso, `a > b` compara
  lexicográficamente.
- **Las vistas reaccionan con `$watch` sobre `$store.app.selectedId`**, no con
  eventos: la selección inicial llega de forma asíncrona y un evento se perdería.
- **SQLite ignora las FK sin `PRAGMA foreign_keys=ON`.** Está en un listener de
  `connect` en `app/db/session.py`; sin él CASCADE y RESTRICT son decorativos.
- **Los CHECK numéricos necesitan `CAST(x AS NUMERIC)`.** Los importes son TEXT
  en SQLite y `'-5' > 0` es verdadero por clase de tipo.
- **Alembic autogenerate no detecta `WITHOUT ROWID`** ni añade el import de
  `app.db.types`. La plantilla `alembic/script.py.mako` lo inyecta;
  `app/db/alembic_support.py` filtra los CHECK generados por los Enum, que si
  no producirían una migración espuria que los borraría.
- Al añadir un modelo, impórtalo en `app/db/registry.py` o autogenerate lo
  ignorará en silencio.

## Advertencias de producto que deben sobrevivir

Están en la interfaz y en los docstrings; no las quites al refactorizar:

- El Opportunity Score **no es una recomendación de inversión**. Los pesos son
  un juicio de diseño, sin backtest detrás.
- El coste medio ponderado mide rendimiento, **no genera declaraciones
  fiscales** (coincide con el costo promedio del Estatuto Tributario colombiano
  para acciones, pero eso lo verifica un contador).
- Los tickers de la BVC **sí están verificados** ya (22 símbolos con precio y
  divisa reales, `.CL`, en COP y por tanto sin conversión). Lo que
  sigue sin verificar es la correspondencia entre un ticker de Yahoo y el
  instrumento exacto que uno compra por su comisionista.
- La ficha, las banderas y el tamaño de posición son heurísticas SIN backtest y
  no son asesoría financiera: el veredicto nunca debe decir «compra».
- Los datos de Yahoo llegan con retraso y errores posibles; la interfaz declara
  su fecha y manda a comprobar las cifras clave en fuentes oficiales.
- La caja puede quedar negativa: es deliberado, para poder cargar un histórico
  ya existente sin registrar depósitos previos.
