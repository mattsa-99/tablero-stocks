"""Servidor MCP por stdio. Registra las herramientas y nada más.

Las funciones viven en `tools.py` y son puras respecto del transporte: se
pueden probar llamándolas directamente, sin levantar stdio ni hablar el
protocolo. Aquí solo se declaran, con la descripción que el modelo lee para
decidir cuál usar — que es, en la práctica, la parte más importante del
servidor: una herramienta mal descrita no se llama o se llama mal.

`annotations` marca cuáles son de solo lectura. `journal_write` es la única
que declara escritura, y el cliente puede pedir confirmación por eso además
de la que exige la propia función.
"""

from __future__ import annotations

from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from mcp_server.tools import TOOLS

SOLO_LECTURA = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
ESCRITURA = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)

INSTRUCCIONES = """\
Herramientas del Tablero de Inversiones: cartera, ranking de oportunidades,
ficha de compra, diario de tesis y simulador.

Empieza SIEMPRE por `brief`: da cartera, riesgo, alertas del diario y mejores
candidatos en una llamada, y dice de cuándo son los datos.

Tres reglas que no se negocian:
1. Cita las cifras que devuelven las herramientas. No calcules ni estimes
   números por tu cuenta: si una cifra no la da una herramienta, no la des.
2. El Opportunity Score y las banderas son heurísticas SIN backtest, y los
   datos de Yahoo llegan con retraso y errores. Si no alcanzan para opinar,
   dilo en vez de rellenar el hueco.
3. Nada de esto ejecuta órdenes. El usuario opera con su comisionista.
"""

servidor = MCPServer(
    name="tablero-stocks",
    title="Tablero de Inversiones",
    instructions=INSTRUCCIONES,
)


@servidor.tool(
    name="brief",
    title="Resumen para asesorar",
    description=(
        "EMPIEZA POR AQUÍ. Cartera (posiciones, P&L, atribución activo/divisa), "
        "riesgo (concentración por cubo y por DIVISA, volatilidad, caída), "
        "alertas del diario, mejores candidatos y frescura de los datos, todo "
        "en una llamada. Si la cartera está vacía lo dice y explica que la "
        "tarea es construirla, no optimizarla."
    ),
    annotations=SOLO_LECTURA,
)
def brief(
    portfolio_id: Annotated[int | None, Field(description="Por defecto, el único que haya")] = None,
    top: Annotated[int, Field(description="Cuántos candidatos traer", ge=1, le=20)] = 5,
) -> dict:
    return TOOLS["brief"](portfolio_id=portfolio_id, top=top)


@servidor.tool(
    name="ficha",
    title="Ficha de compra",
    description=(
        "Todo sobre UNA empresa contra la cartera real: score y su desglose, "
        "veredicto de banderas, salud financiera (deuda, márgenes, caja libre), "
        "pares de su sector, lo que ya tienes de ella y el tamaño de posición "
        "sugerido. El veredicto NO dice «compra»: dice si se encontraron "
        "problemas."
    ),
    annotations=SOLO_LECTURA,
)
def ficha(
    symbol: Annotated[str, Field(description="Ticker, por ejemplo EQNR o ECOPETROL.CL")],
    portfolio_id: int | None = None,
    capital: Annotated[
        float | None,
        Field(
            description=(
                "Capital total en divisa base. Sin él, con la cartera vacía el "
                "sizing solo puede dar porcentajes y no montos"
            )
        ),
    ] = None,
) -> dict:
    return TOOLS["ficha"](symbol=symbol, portfolio_id=portfolio_id, capital=capital)


@servidor.tool(
    name="opportunities",
    title="Ranking de oportunidades",
    description=(
        "Ranking completo con filtros. `rank` es la posición en el universo "
        "ENTERO aunque filtres: el score es un percentil y filtrar no lo "
        "recalcula. Regiones: US, COL, LATAM, EU, ASIA, GLOBAL. Calificaciones: "
        "muy_buena, buena, normal, mala, muy_mala, sin_calificar."
    ),
    annotations=SOLO_LECTURA,
)
def opportunities(
    portfolio_id: int | None = None,
    limit: Annotated[int, Field(ge=1, le=50)] = 15,
    regions: Annotated[str | None, Field(description="Separadas por coma, ej. US,COL")] = None,
    quality_tiers: Annotated[str | None, Field(description="Separadas por coma")] = None,
) -> dict:
    return TOOLS["opportunities"](
        portfolio_id=portfolio_id, limit=limit, regions=regions, quality_tiers=quality_tiers
    )


@servidor.tool(
    name="journal",
    title="Diario de tesis",
    description=(
        "Las tesis del usuario con sus alertas (precio de invalidación tocado, "
        "revisión vencida, calificación caída) y el MARCADOR del asesor: cómo "
        "han ido las decisiones anotadas. Revísalo antes de recomendar nada."
    ),
    annotations=SOLO_LECTURA,
)
def journal(portfolio_id: int | None = None, include_archived: bool = False) -> dict:
    return TOOLS["journal"](portfolio_id=portfolio_id, include_archived=include_archived)


@servidor.tool(
    name="journal_write",
    title="Anotar en el diario",
    description=(
        "ESCRIBE. Crea, actualiza o revisa una tesis. Exige `user_confirmed=true`, "
        "y no lo des por supuesto: enseña al usuario el texto exacto de la tesis, "
        "la invalidación y la fecha de revisión, y llama solo cuando diga que sí. "
        "Anotar lo que recomiendas es lo que te hace responsable de ello."
    ),
    annotations=ESCRITURA,
)
def journal_write(
    action: Annotated[str, Field(description="create, update o review")],
    user_confirmed: Annotated[bool, Field(description="El usuario aprobó el texto")] = False,
    symbol: str | None = None,
    kind: Annotated[str | None, Field(description="WATCH, BUY, HOLD, SELL o PASS")] = None,
    thesis: Annotated[str | None, Field(description="Por qué, en palabras del usuario")] = None,
    invalidation: Annotated[str | None, Field(description="Qué la rompería")] = None,
    invalidation_price: float | None = None,
    review_date: Annotated[str | None, Field(description="AAAA-MM-DD, futura")] = None,
    entry_id: int | None = None,
    review_note: str | None = None,
    portfolio_id: int | None = None,
) -> dict:
    return TOOLS["journal_write"](
        action=action,
        symbol=symbol,
        kind=kind,
        thesis=thesis,
        invalidation=invalidation,
        invalidation_price=invalidation_price,
        review_date=review_date,
        entry_id=entry_id,
        review_note=review_note,
        user_confirmed=user_confirmed,
        portfolio_id=portfolio_id,
    )


@servidor.tool(
    name="whatif",
    title="Simular una compra",
    description=(
        "Antes y después de comprar X unidades de un símbolo: valor, "
        "concentración por sector, diversificación. NO escribe nada. Sin "
        "`price` usa el de ahora."
    ),
    annotations=SOLO_LECTURA,
)
def whatif(
    symbol: str,
    quantity: float,
    price: float | None = None,
    portfolio_id: int | None = None,
) -> dict:
    return TOOLS["whatif"](
        symbol=symbol, quantity=quantity, price=price, portfolio_id=portfolio_id
    )


@servidor.tool(
    name="performance",
    title="Evolución de la cartera",
    description=(
        "Curva de valor frente a lo aportado y frente al índice, más el retorno "
        "anualizado ponderado por dinero (XIRR). No se anualiza por debajo de "
        "un trimestre: sobre pocos días multiplica el ruido."
    ),
    annotations=SOLO_LECTURA,
)
def performance(
    portfolio_id: int | None = None,
    days: Annotated[int, Field(ge=7, le=3650)] = 365,
) -> dict:
    return TOOLS["performance"](portfolio_id=portfolio_id, days=days)


@servidor.tool(
    name="allocation",
    title="Plan de asignación entre clases",
    description=(
        "Cuánto hay en cada clase de activo frente a cuánto quiere el usuario, "
        "con la desviación en puntos y a qué clase dirigir el próximo aporte. "
        "SOLO LECTURA: el plan es la única cosa del tablero que expresa una "
        "intención del usuario y no una medición, así que se cambia en el "
        "tablero. Nunca propone vender: rebalancear con aportes nuevos evita "
        "realizar ganancias y pagar comisiones."
    ),
    annotations=SOLO_LECTURA,
)
def allocation(
    portfolio_id: int | None = None,
    contribution: Annotated[float, Field(ge=0)] = 0.0,
) -> dict:
    return TOOLS["allocation"](portfolio_id=portfolio_id, contribution=contribution)


@servidor.tool(
    name="fixed_income",
    title="Renta fija directa (TES, CDT, FIC)",
    description=(
        "Los instrumentos que el usuario cargó a mano, con las cinco preguntas "
        "contestadas: tasa real tras inflación, comparación con el mercado y "
        "con lo que pagaron otros bancos, plazo restante, liquidez y emisor. "
        "NO llevan score ni calificación A-E y hay que decirlo: un score es un "
        "rango percentil contra pares y aquí no hay pares. Se valoran a costo "
        "más devengo, que es lo que valen a vencimiento y no lo que alguien "
        "pagaría hoy."
    ),
    annotations=SOLO_LECTURA,
)
def fixed_income(symbol: str | None = None) -> dict:
    return TOOLS["fixed_income"](symbol=symbol)


@servidor.tool(
    name="rates",
    title="Tasas de referencia colombianas",
    description=(
        "Curva cero cupón TES, IBR, DTF, tasa de política e inflación del Banco "
        "de la República, con la fecha de cada una, más la tasa REAL por Fisher "
        "exacto. Con `term_days` compara además contra lo que pagaron los bancos "
        "en CDT a ese plazo. Es lo que permite leer un «12,3% anual»: sin la "
        "inflación al lado, esa cifra no dice si se gana o se pierde poder "
        "adquisitivo."
    ),
    annotations=SOLO_LECTURA,
)
def rates(
    term_days: Annotated[int | None, Field(ge=1, le=10_000)] = None,
    issuer: str | None = None,
) -> dict:
    return TOOLS["rates"](term_days=term_days, issuer=issuer)


def main() -> None:
    servidor.run(transport="stdio")
