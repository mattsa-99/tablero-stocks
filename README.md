# Tablero de Inversiones

Dashboard de portafolio con cálculo de rendimiento y motor de oportunidades.
FastAPI + SQLite + yfinance en el backend; Jinja2 + Tailwind + Alpine.js +
Chart.js en el frontend, servido por la misma aplicación.

Contexto de uso: cartera en **COP** con activos cotizados en **USD**.

> **¿Primera vez usándolo?** [`docs/GUIA.md`](docs/GUIA.md) explica la rutina
> diaria para pasar del tablero a una decisión de compra, y —tan importante
> como eso— qué límites tiene lo que muestra.

---

## Puesta en marcha

Requiere Python 3.12+.

```bash
cd "Tablero Stocks"

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

cp .env.example .env               # ajusta si hace falta

alembic upgrade head               # crea el esquema
uvicorn app.main:app --reload
```

Abre **http://127.0.0.1:8000**. No hay build de frontend: Tailwind, Alpine y
Chart.js se cargan por CDN y las plantillas las sirve FastAPI.

| Ruta | Qué es |
|---|---|
| `/` | Dashboard de portafolio |
| `/oportunidades` | Ranking de oportunidades |
| `/docs` | Documentación interactiva de la API |

### Primeros pasos en la interfaz

1. **Crear un portafolio** con el botón `+` de la cabecera. La divisa base no
   se puede cambiar después: invalidaría los tipos de cambio ya congelados en
   el historial.
2. **Registrar un depósito** y luego las compras, con *Nueva transacción*.
   Si dejas el tipo de cambio vacío se toma de los datos de mercado en esa
   fecha; si no hay ninguno, la operación se rechaza en vez de asumir paridad.
3. **Poblar datos de mercado**: `curl -X POST localhost:8000/api/market/refresh`
   o espera al refresco automático. Hasta entonces las posiciones aparecen sin
   valorar y marcadas como tales.
4. **Oportunidades** necesita al menos 5 candidatos. Al principio el catálogo
   solo tiene lo que posees, así que añade más en el campo *Candidatos
   adicionales* (`NVDA, KO, JNJ`).

---

## Frescura de los datos

Dos cadencias distintas y deliberadas:

| Recurso | Cuándo se refresca |
|---|---|
| Cotizaciones | Perezosamente al leer un portafolio. TTL 15 min en sesión, 4 h fuera |
| Barras diarias, fundamentales, FX | Job completo tras el cierre de NYSE (18:00 ET, L-V) |

El planificador va dentro de la app (`TABLERO_ENABLE_BACKGROUND_REFRESH=true`,
por defecto): un cron a las 18:00 `America/New_York` L-V, con recuperación al
arrancar si la última corrida quedó atrás. `POST /api/market/refresh?force=true`
lo ejecuta bajo demanda; `force` salta el TTL pero **no** el backoff.

**Refresco sin abrir la app** (`deploy/tablero-refresh.plist`): un agente
`launchd` ejecuta `python -m scripts.scheduled_refresh` cuatro veces por día
hábil —pre-market, apertura, media sesión y cierre— contra la misma base local.
Las tres primeras franjas solo refrescan cotizaciones y FX; el cierre es la
sincronización completa. Copia la plantilla, reemplaza las rutas y cárgala con
`launchctl load`; el detalle está en el encabezado del propio `.plist`.

Si yfinance falla se conserva el último dato conocido, se marca `is_stale` y se
devuelve un aviso. Un fallo de Yahoo nunca deja el dashboard en blanco.

---

## Estructura

```
app/
├── main.py              # app, traducción de errores a HTTP, montaje de estáticos
├── core/                # config, excepciones, dinero, planificador
├── db/                  # Base, sesión, tipos a medida (Decimal exacto, UTC)
├── models/              # SQLAlchemy 2.x
├── schemas/             # Pydantic v2 (contratos de API)
├── repositories/        # consultas compartidas
├── services/            # P&L, portafolio, mercado, scoring
├── providers/           # yfinance y capa de caché
├── routers/             # /api/* y las vistas HTML
├── templates/           # base.html + dashboard.html + opportunities.html
└── static/              # css/app.css, js/{store,charts,portfolio,opportunities}.js
```

Reglas que sostienen el diseño:

- **El ledger de transacciones es la única fuente de verdad.** Posiciones,
  coste medio, caja y P&L se derivan reproduciéndolo. No hay estado agregado
  persistido que pueda desincronizarse.
- **Los services no importan FastAPI ni yfinance.** Por eso los 161 tests
  corren en ~1,4 s sin red.
- **El dinero del usuario es `Decimal` exacto; los precios de mercado son
  `float`.** La frontera es `Decimal(str(x))`, en `app/core/money.py`.

---

## Comandos

```bash
pytest -q                     # 161 tests, sin red
ruff check .                  # lint
alembic upgrade head          # aplicar migraciones
alembic downgrade base        # revertir
alembic revision --autogenerate -m "descripción"
```

`pytest` incluye un test que falla si los modelos y las migraciones divergen.

---

## Notas importantes

- **El Opportunity Score no es una recomendación de inversión.** Es un ranking
  *relativo*: dice quién encabeza la lista, no si encabezarla significa algo.
  Los pesos (0.35/0.30/0.20/0.15) son un juicio de diseño, no un backtest.
- **La calificación A–E sí es absoluta** y es la que hay que mirar para saber
  si algo es bueno. Compara contra el P/E del mercado y contra umbrales fijos
  de riesgo y calidad, así que puede decir «el primero de esta lista es malo».
  Sigue siendo una heurística explicable, no un modelo validado: un grado A
  significa «cumple varios criterios objetivos», no «va a subir».
- **El coste medio ponderado mide rendimiento, no genera declaraciones
  fiscales.** Coincide con el costo promedio que el Estatuto Tributario
  colombiano toma como referencia para acciones, pero verifícalo con un contador
  antes de usar estas cifras para declarar.
- **Los tickers de la BVC no están verificados.** La cobertura de yfinance en
  Latinoamérica es irregular; algunos emisores solo aparecen vía ADR. Un ticker
  que no valida se da de alta como no verificado y aparece en
  `positions_without_price` en lugar de fingir un valor.
- **La caja puede quedar negativa.** Es deliberado: obligar a registrar un
  depósito antes de cada compra haría inusable la carga de un histórico ya
  existente.
