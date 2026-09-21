from __future__ import annotations

from functools import cached_property

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TABLERO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "sqlite:///./tablero.db"

    # Divisa base por defecto al crear un portafolio.
    #
    # USD y no COP, aunque el usuario esté en Colombia, por tres razones
    # medidas:
    #
    # 1. CUADRAR CON EL BROKER. eToro, XTB e Interactive Brokers llevan la
    #    cuenta en dólares; solo el comisionista local va en pesos. Con base
    #    COP ninguna cifra del tablero coincidiría con la pantalla del broker,
    #    y esa reconciliación es lo que hace que uno se fíe del tablero.
    # 2. MENOS SUPERFICIE DE CONVERSIÓN. Del universo de ingesta, 476 activos
    #    cotizan en USD y 18 en COP. Con base USD la conversión afecta a 18
    #    posiciones posibles en vez de a 476.
    # 3. DESBLOQUEA MÉTRICAS. La comparación con el índice y la curva de valor
    #    no necesitan ningún tipo de cambio para lo que cotiza en dólares.
    #
    # Esto NO esconde el peso: `PortfolioSummary.fx_pnl` separa explícitamente
    # cuánto del resultado vino de la divisa, que es justo lo que antes estaba
    # untado e invisible dentro de cada posición.
    #
    # `base_currency` no es modificable después de crear el portafolio
    # (ver `schemas/portfolio.py`): cambiarla invalidaría todos los
    # `fx_rate_to_base` ya congelados en el ledger.
    default_base_currency: str = "USD"

    # Solo afecta a la presentación. El almacenamiento es siempre UTC.
    display_timezone: str = "America/Bogota"

    sql_echo: bool = False
    log_level: str = "INFO"

    # ------------------------------------------------------------------
    # Frescura de datos de mercado
    # ------------------------------------------------------------------
    # Dos cadencias distintas y deliberadas:
    #  - Cotizaciones: TTL corto, refresco perezoso en cada lectura. Es lo que
    #    da frescura intradía al dashboard.
    #  - Barras diarias, fundamentales y FX: job completo cada ~84 h (dos veces
    #    por semana). Pedirlos con más frecuencia no aporta (los cierres son
    #    inmutables) y solo consume presupuesto de rate limit.
    quote_ttl_seconds: int = 900  # 15 min
    quote_ttl_market_closed_seconds: int = 14_400  # 4 h
    price_history_ttl_seconds: int = 43_200  # 12 h
    fundamentals_ttl_seconds: int = 86_400  # 24 h
    # FX con la MISMA frescura que una cotización, y no 24 veces menos.
    #
    # En una cartera en COP con activos en USD el tipo de cambio multiplica el
    # valor de TODO, mientras que un precio afecta a una sola posición: tener
    # la cotización de Apple a 15 minutos y el USD/COP a 6 horas deja fresco lo
    # que menos pesa. Cuesta muy poco corregirlo, porque los pares son unos
    # pocos por cartera aunque el universo tenga cientos de activos.
    fx_ttl_seconds: int = 900  # 15 min
    fx_ttl_market_closed_seconds: int = 14_400  # 4 h
    metadata_ttl_seconds: int = 2_592_000  # 30 días

    # Histórico de tipos de cambio. TTL de un día, no de 15 minutos: son
    # CIERRES ya cerrados, y el tipo del día en curso lo mantiene fresco
    # `refresh_fx` con la cotización viva.
    fx_history_ttl_seconds: int = 86_400  # 24 h
    # Ventana por defecto del relleno. 1.100 días es la misma retención que las
    # barras de precio: la curva de valor cruza las dos series y una ventana
    # más corta en divisas recortaría la curva justo donde hay precios.
    fx_history_days: int = 1_100

    # ------------------------------------------------------------------
    # Pipeline de ingesta
    # ------------------------------------------------------------------
    # Cron diario tras el cierre de NYSE. Las 18:00 ET y no las 16:00: el cierre
    # ajustado y el volumen definitivo tardan en asentarse, y pedirlos a las
    # 16:01 devuelve datos que luego cambian.
    enable_background_refresh: bool = True
    sync_hour: int = 18
    sync_minute: int = 0
    sync_timezone: str = "America/New_York"
    sync_days: str = "mon-fri"

    # Si al arrancar la última sincronización correcta es más antigua que esto,
    # se ejecuta una de recuperación. Imprescindible en una app auto-alojada:
    # un portátil apagado a las 18:00 ET no ejecutaría NUNCA el cron.
    sync_catchup_after_hours: int = 30

    # Semilla del universo. Vacío = la lista por defecto de services/universe.py
    universe_symbols: str = ""
    seed_universe_on_startup: bool = True
    seed_catalog_on_startup: bool = True

    # Procesamiento por lotes. yfinance no publica sus límites, así que estos
    # números son conservadores a propósito: 25 símbolos y 2 s de pausa
    # sostienen ~750 símbolos/minuto, de sobra para un universo de decenas y
    # sin acercarse al umbral donde Yahoo empieza a devolver 429.
    ingestion_batch_size: int = 25
    ingestion_batch_delay_seconds: float = 2.0

    # Cada cuánto se vuelve a pedir el perfil de un fondo. Treinta días: la
    # composición y el ratio de gastos cambian cuando el gestor cambia el
    # mandato, y cuesta una llamada por fondo (182 hoy).
    fund_profile_ttl_seconds: int = 30 * 86_400

    # Antigüedad máxima de un fundamental para fiarse de él.
    #
    # La sincronización completa corre dos veces por semana, así que en
    # condiciones normales nada pasa de cuatro días. Siete deja holgura para
    # una corrida que falle y aún cazan un dato realmente viejo. Por encima,
    # la fila baja su confianza: es el mismo principio que
    # `price_series_max_age_days`, que existe porque una serie parada calcula
    # sus indicadores igual de bien que una viva.
    fundamentals_max_age_days: int = 7

    # Presupuesto de reloj para una sincronización completa. Al agotarse se
    # corta entre lotes y la corrida queda PARCIAL, con el motivo a la vista.
    #
    # MEDIDO: con la red sana, `.info` tarda 0,71 s por símbolo y los 494
    # proyectan 5,8 minutos. Las corridas reales de esta instalación tardaron
    # 15 minutos el buen día y 79 y 93 los malos. No es que falte un timeout
    # por petición -`YfData.get` ya impone 30 s-: es que 494 símbolos por
    # varias peticiones cada uno, todas lentas, suman horas sin que ninguna
    # incumpla su límite.
    #
    # Treinta minutos son cinco veces el coste medido: no corta un día normal
    # ni uno algo lento, y convierte un día roto en un fallo acotado en vez de
    # una hora y media de molienda. Es la misma idea que el enfriamiento de
    # red: degradar, no averiarse.
    sync_budget_minutes: int = 30

    # Fracción de símbolos que pueden fallar sin invalidar la foto semanal del
    # ranking. Por encima de esto NO se congela nada.
    #
    # No es una precaución abstracta: en las últimas ocho sincronizaciones de
    # esta instalación hubo una con 420 símbolos caídos de 494 y otra con 469.
    # Una foto tomada ese día registraría los scores de un ranking calculado
    # con precios de hace días para el 85% del universo, y meses después el
    # scorecard compararía contra eso creyendo que era el ranking de ese día.
    # Una foto mala es peor que ninguna: no se puede distinguir a posteriori.
    scorecard_max_failed_share: float = 0.10

    # Tasas de referencia colombianas (Banrep). Es OTRA fuente de red, con su
    # propio dominio y su propia forma de fallar, así que tiene su propio
    # interruptor: la suite lo apaga igual que apaga el planificador, porque
    # `run_sync` las pide y ningún test puede salir a internet.
    enable_reference_rates: bool = True

    # Carga perezosa: al consultar un activo del catálogo sin datos, se traen
    # bajo demanda en lugar de esperar a la sincronización diaria.
    enable_lazy_ingestion: bool = True

    # Antigüedad máxima de la última barra para fiarse de la serie.
    #
    # Por encima de esto NO se calculan momentum, volatilidad ni drawdown: una
    # serie parada los calcula igual de bien y el activo aparecería con
    # "confianza alta" sobre precios de hace semanas. Diez días naturales dejan
    # holgura para un puente largo y para un valor poco líquido de la BVC, y
    # aun así cazan un histórico realmente abandonado (GXG llevaba 46).
    price_series_max_age_days: int = 10

    # Retención de barras diarias. Tiene que ser MAYOR que
    # `price_history_days` o la poda borraría justo lo que el relleno acaba de
    # traer; el margen absorbe un cambio de ventana sin perder datos.
    price_history_retention_days: int = 2_000
    enable_history_pruning: bool = False

    # Profundidad de histórico: cinco años.
    #
    # No es un capricho. Con los 400 días anteriores NINGÚN activo de la base
    # llegaba a dos años (máximo medido: 419 días), y sobre esa ventana la
    # «caída máxima» de `sizing.py` solo veía catorce meses de mercado alcista.
    # Por eso existe el suelo de estrés del 35%: no era conservadurismo, era
    # tapar la falta de datos. Cinco años incluyen al menos una corrección
    # seria para la mayoría de los activos.
    #
    # El coste en LLAMADAS es cero: `fetch_history` es un único `yf.download`
    # por lote, así que pedir cinco años cuesta lo mismo que pedir uno y solo
    # cambia el tamaño de la respuesta.
    price_history_days: int = 1_825

    # Cada cuánto se vuelve a preguntar por el PASADO de un símbolo cuya serie
    # sigue sin llegar a `price_history_days`. Treinta días, porque la respuesta
    # solo cambia cuando Yahoo amplía su propio archivo: para un símbolo joven
    # -una salida a bolsa reciente- la respuesta será «no hay más» durante
    # mucho tiempo, y preguntarlo a diario sería redescargar cinco años en cada
    # sincronización.
    price_history_backfill_ttl_seconds: int = 30 * 86_400

    # ------------------------------------------------------------------
    # Motor de oportunidades
    # ------------------------------------------------------------------
    opportunity_min_universe: int = 5
    opportunity_sector_threshold: float = 0.30

    # Tamaño de posición (ver services/sizing.py). Son PUNTOS DE PARTIDA de
    # juicio, no resultado de una optimización: el usuario puede cambiarlos en
    # la ficha para ver qué pasa.
    #
    # Presupuesto de riesgo: cuánto de la cartera aceptas perder por UNA
    # posición si repite su peor caída. Un 2% con 10-20 posiciones deja que un
    # desastre aislado duela sin arruinar el plan.
    position_risk_budget_pct: float = 2.0
    # Tope por posición, aunque el riesgo permitiera más.
    position_max_weight_pct: float = 10.0
    opportunity_weight_value: float = 0.35
    opportunity_weight_momentum: float = 0.30
    opportunity_weight_diversification: float = 0.15
    opportunity_weight_risk: float = 0.20

    # Valoración RELATIVA AL SECTOR. Un banco cotiza a P/E y P/B estructuralmente
    # más bajos que una tecnológica, así que rankear los múltiplos contra los
    # 494 activos mezclados premia al sector barato y no a la empresa barata.
    # Con esto activo, cada múltiplo se ordena DENTRO de su sector cuando hay
    # pares suficientes; con menos, se usa el universo entero (y se dice).
    opportunity_sector_neutral_value: bool = True
    # Mínimo de pares con dato para fiarse de un percentil o de una mediana de
    # sector. Con 3 empresas, «el más barato de su sector» sale con 83 puntos
    # por no tener rivales: es el mismo ruido que el universo mínimo evita.
    opportunity_sector_min_peers: int = 8

    provider_timeout_seconds: int = 20

    @cached_property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


settings = Settings()
