"""La ficha de compra como markdown, para leerla en texto plano.

Existe para que Claude (o quien sea) pueda analizar una empresa con EXACTAMENTE
los mismos datos y banderas que ve la interfaz: `scripts/ficha.py` llama a
`build_ficha` y a esta función, y nada más. Aquí no se calcula nada nuevo.
"""

from __future__ import annotations

from app.schemas.ficha import FichaResponse, Flag

_ICON = {"red": "ROJA", "yellow": "AMARILLA", "green": "VERDE", "info": "INFO"}
_ORDER = ("red", "yellow", "green", "info")


def _pct(value: float | None, digits: int = 1) -> str:
    return "n/d" if value is None else f"{value * 100:.{digits}f}%"


def _num(value: float | None, digits: int = 1, suffix: str = "") -> str:
    return "n/d" if value is None else f"{value:.{digits}f}{suffix}"


def _flag_lines(flags: list[Flag]) -> list[str]:
    lines: list[str] = []
    for level in _ORDER:
        for flag in (f for f in flags if f.level == level):
            lines.append(f"- **[{_ICON[level]}] {flag.title}**: {flag.detail}")
    return lines


def render_markdown(f: FichaResponse) -> str:
    out: list[str] = []
    title = f"{f.symbol} - {f.name}" if f.name else f.symbol
    out.append(f"# Ficha de compra: {title}")
    meta = [f.asset_type, f.sector or "sin sector", f.industry or "", f.currency]
    out.append(" · ".join(x for x in meta if x))
    out.append(f"Generada: {f.as_of.strftime('%Y-%m-%d %H:%M')} UTC")
    out.append("")
    out.append(f"> **{f.verdict.label}** - {f.verdict.red} rojas, {f.verdict.yellow} amarillas, "
               f"{f.verdict.green} verdes")
    out.append(f"> {f.verdict.disclaimer}")

    out += ["", "## Banderas", *_flag_lines(f.flags)]

    op = f.opportunity
    out += ["", "## Score y calificación"]
    if op is None:
        out.append(f"No está en el ranking: {f.excluded_reason or 'sin datos suficientes'}.")
    else:
        out.append(
            f"- Ranking: #{op.rank} de {f.universe_size} · score {op.score:.1f}/100 · "
            f"calificación absoluta **{op.assessment.grade} ({op.assessment.label})** · "
            f"confianza del dato: {op.confidence}"
        )
        out.append(
            "- Aportes al score: "
            f"valor {op.value.contribution:+.1f} · momentum {op.momentum.contribution:+.1f} · "
            f"diversificación {op.diversification.contribution:+.1f} · "
            f"riesgo {op.risk.contribution:+.1f} · base {op.baseline:+.0f}"
        )
        out.append("- Señales de la calificación:")
        for signal in op.assessment.signals:
            pts = "sin dato" if signal.points is None else f"{signal.points:+d}"
            out.append(f"  - {signal.label} ({pts}): {signal.detail}")
        for note in [*op.assessment.notes, *op.notes]:
            out.append(f"- Nota: {note}")

        v = op.value.inputs
        out += ["", "## Valoración"]
        out.append(
            f"- P/E actual {_num(v.get('trailing_pe'))} · P/E a futuro "
            f"{_num(v.get('forward_pe'))} · P/B {_num(v.get('price_to_book'))} · "
            f"EV/EBITDA {_num(v.get('ev_to_ebitda'))}"
        )
        if op.value_basis == "sector":
            out.append(
                f"- Comparada con su sector ({op.value_reference}, {op.sector_peer_count} "
                f"empresas, P/E mediano {_num(op.sector_pe)})."
            )
        else:
            out.append("- Sector con pocos pares (o sin sector): comparada con todo el universo.")

        m, r = op.momentum.inputs, op.risk.inputs
        out += ["", "## Precio y riesgo"]
        out.append(
            f"- Momentum 12-1: {_pct(m.get('momentum_12_1'))} · tendencia SMA50/SMA200: "
            f"{_pct(m.get('sma50_over_sma200_minus_1'))}"
        )
        out.append(
            f"- Volatilidad anualizada {_pct(r.get('annualized_volatility'))} · caída máxima "
            f"histórica {_pct(r.get('max_drawdown'))} · beta {_num(r.get('beta'), 2)}"
        )

    h = f.health
    out += ["", "## Salud financiera"]
    if not h.has_data:
        out.append("Sin datos crudos del proveedor.")
    elif not h.applies:
        out.append(f"No aplica: {h.not_applicable_reason}")
    else:
        out.append(
            f"- Deuda neta/EBITDA {_num(h.net_debt_to_ebitda, 1, 'x')} · margen operativo "
            f"{_pct(h.operating_margin)} · caja libre/ventas {_pct(h.fcf_margin)} · "
            f"rendimiento de caja libre {_pct(h.fcf_yield)} · current ratio "
            f"{_num(h.current_ratio, 2)}"
        )
    if h.has_data and h.payout_ratio is not None:
        out.append(f"- Reparto de beneficio en dividendo: {_pct(h.payout_ratio, 0)}")
    for reason in h.unavailable.values():
        out.append(f"- No disponible: {reason}")
    for caveat in h.caveats:
        out.append(f"- Ojo: {caveat}")

    c, a = h.calendar, h.analyst
    out += ["", "## Calendario y analistas"]
    out.append(
        "- Próximos resultados: "
        + (f"{c.next_earnings} ({c.days_to_earnings} días"
           f"{', estimada' if c.earnings_is_estimate else ''})"
           if c.next_earnings else "no consta una fecha futura")
    )
    out.append(
        "- Próximo ex-dividendo: "
        + (f"{c.ex_dividend} ({c.days_to_ex_dividend} días)" if c.ex_dividend else "no consta")
    )
    if a.target_mean is not None:
        out.append(
            f"- Consenso de analistas (opinión, no dato): objetivo {a.target_mean:.2f} "
            f"({_pct(a.upside_pct)} sobre el precio), {a.analysts or '?'} analistas, "
            f"recomendación «{a.recommendation or 'n/d'}»"
        )
    else:
        out.append("- Consenso de analistas: sin objetivo fiable.")

    out += ["", "## Frente a su sector"]
    if f.sector_rank is not None:
        out.append(
            f"Es la #{f.sector_rank} de {f.sector_size} empresas de su sector en el universo."
        )
        for p in f.peers:
            out.append(
                f"- {p.symbol} ({p.name or ''}): #{p.rank}, score {p.score:.1f}, "
                f"{p.grade_label}, P/E {_num(p.trailing_pe)}"
            )
    else:
        out.append("Sin comparación sectorial (ETF o sin sector).")

    p = f.portfolio
    out += ["", "## Tu cartera"]
    out.append(f"- Posiciones abiertas: {p.position_count} (divisa base {p.base_currency})")
    if p.holding:
        out.append(
            f"- Ya tienes {p.holding.quantity} títulos ({_num(p.holding.weight_pct)}% de la "
            f"cartera), rendimiento no realizado {_num(p.holding.unrealized_return_pct)}%"
        )
    else:
        out.append("- No tienes este activo.")
    if p.bucket and p.bucket_weight_pct is not None:
        out.append(f"- Peso actual de «{p.bucket}»: {p.bucket_weight_pct:.1f}%")

    z = f.sizing
    out += ["", "## Cuánto poner (techo, no orden)"]
    if z is None:
        out.append("Sin cálculo: la empresa no está en el ranking.")
    else:
        source = (
            f"su peor caída medida ({_num(z.observed_drawdown_pct)}%)"
            if z.stress_source == "observed"
            else "un suelo, porque el histórico es corto"
        )
        out.append(
            f"- Presupuesto de riesgo {z.risk_budget_pct:g}% de la cartera, caída de estrés "
            f"{_num(z.stress_loss_pct, 0)}% (según {source}), tope {z.max_position_pct:g}%."
        )
        out.append(
            f"- Peso objetivo: **{z.target_pct:.1f}%** de la cartera "
            f"(manda el {'riesgo' if z.binding == 'risk' else 'tope'}); hoy tienes "
            f"{z.current_pct:.1f}%, podrías añadir {z.add_pct:.1f}%."
        )
        if z.capital is not None and z.target_amount is not None:
            out.append(
                f"- Con {z.capital:,.0f} {z.base_currency}: objetivo {z.target_amount:,.0f}, "
                f"añadir {z.add_amount:,.0f}. Si repite su peor caída perderías "
                f"{z.loss_if_repeats_amount:,.0f} ({z.loss_if_repeats_pct_of_capital:.1f}% "
                f"de la cartera)."
            )
        else:
            out.append(
                "- Sin capital registrado: indica cuánto piensas invertir para ver importes."
            )
        for note in z.notes:
            out.append(f"- {note}")

    fr = f.freshness
    out += ["", "## De cuándo son los datos"]
    price_time = fr.price_time.strftime("%Y-%m-%d %H:%M UTC") if fr.price_time else "n/d"
    out.append(f"- Precio: {price_time} · última barra del histórico: "
               f"{fr.last_bar_date or 'n/d'} · fundamentales del {fr.fundamentals_as_of or 'n/d'}")
    for note in [*fr.notes, *f.warnings]:
        out.append(f"- {note}")

    out += ["", "---", f.disclaimer]
    return "\n".join(out) + "\n"
