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

    # Divisa base por defecto al crear un portafolio. El contexto de uso es
    # Colombia: portafolio en COP con activos mayoritariamente en USD.
    default_base_currency: str = "COP"

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

    # Retención de barras diarias. 1.100 días > 252 (momentum 12-1) + 200
    # (SMA200) con holgura de calendario; guardar 10 años multiplica la tabla
    # por nueve sin que ninguna métrica lo use.
    price_history_retention_days: int = 1_100
    enable_history_pruning: bool = False

    # Días de histórico a mantener. 400 > 252 + 200: cubre el momentum 12-1 y
    # la SMA200 con margen para festivos y días sin cotización.
    price_history_days: int = 400

    # ------------------------------------------------------------------
    # Motor de oportunidades
    # ------------------------------------------------------------------
    opportunity_min_universe: int = 5
    opportunity_sector_threshold: float = 0.30
    opportunity_weight_value: float = 0.35
    opportunity_weight_momentum: float = 0.30
    opportunity_weight_diversification: float = 0.15
    opportunity_weight_risk: float = 0.20

    provider_timeout_seconds: int = 20

    @cached_property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


settings = Settings()
