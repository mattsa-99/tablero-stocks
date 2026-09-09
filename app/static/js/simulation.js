/* Panel "Modo Simulación": operaciones propuestas y su impacto.
 *
 * Todo el cálculo lo hace el backend. Este componente solo recoge las
 * operaciones y presenta la comparación: duplicar aquí la lógica de P&L o de
 * diversificación garantizaría que las dos versiones diverjan.
 */


document.addEventListener("alpine:init", () => {
  Alpine.data("simulationPanel", () => ({
    isOpen: false,
    loading: false,
    error: null,
    result: null,

    draft: null,
    trades: [],

    marketPrice: null,
    marketPriceAsOf: null,
    marketPriceIsStale: false,

    init() {
      // El panel se renderiza con x-show, así que sus bindings se evalúan
      // aunque esté cerrado. Sin un draft inicial, cada `draft.type` lanza
      // "Cannot read properties of null" antes de que nadie lo abra.
      this.draft = this.blankDraft();

      // Traspaso desde «Sugerencia óptima», que vive en la otra vista. Se
      // CONSUME al leerlo: si quedara, volver al dashboard reabriría el
      // simulador con una compra que el usuario ya descartó.
      const handoff = sessionStorage.getItem("tablero:simulate");
      if (handoff) {
        sessionStorage.removeItem("tablero:simulate");
        try {
          this.$nextTick(() => this.open(JSON.parse(handoff)));
        } catch {
          /* payload corrupto: se ignora en silencio, no es crítico */
        }
      }
    },

    clearMarketPrice() {
      this.marketPrice = null;
      this.marketPriceAsOf = null;
      this.marketPriceIsStale = false;
    },

    blankDraft() {
      return {
        type: "BUY",
        symbol: "",
        quantity: "",
        price: "",
        cash_amount: "",
        fees: "0",
        currency: "USD",
        fx_rate_to_base: "",
        sector: "",
      };
    },

    get needsAsset() {
      return ["BUY", "SELL", "DIVIDEND"].includes(this.draft?.type);
    },

    get needsQuantity() {
      return ["BUY", "SELL"].includes(this.draft?.type);
    },

    get needsCash() {
      return ["DIVIDEND", "DEPOSIT", "WITHDRAWAL"].includes(this.draft?.type);
    },

    get currency() {
      return this.result?.base_currency ?? this.$store.app.baseCurrency;
    },

    get hasResult() {
      return !this.loading && !this.error && this.result !== null;
    },

    /* Sectores que cambian, ordenados por magnitud del cambio.
     * Es el "tu exposición a Tecnología pasa del 30% al 45%". */
    get sectorShifts() {
      if (!this.result) return [];
      const before = new Map(
        this.result.current_state.sector_exposures.map((e) => [
          e.sector,
          window.fmt.num(e.weight_pct),
        ])
      );
      const after = new Map(
        this.result.simulated_state.sector_exposures.map((e) => [
          e.sector,
          window.fmt.num(e.weight_pct),
        ])
      );
      const sectors = new Set([...before.keys(), ...after.keys()]);

      return [...sectors]
        .map((sector) => ({
          sector,
          before: before.get(sector) ?? 0,
          after: after.get(sector) ?? 0,
          delta: (after.get(sector) ?? 0) - (before.get(sector) ?? 0),
        }))
        .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta));
    },

    get headlineMetrics() {
      if (!this.result) return [];
      const { current_state: before, simulated_state: after, deltas } = this.result;
      return [
        { label: "Valor de mercado", before: before.market_value, after: after.market_value,
          delta: deltas.market_value, kind: "money" },
        { label: "Caja", before: before.cash_balance, after: after.cash_balance,
          delta: deltas.cash_balance, kind: "money" },
        { label: "P&L total", before: before.total_pnl, after: after.total_pnl,
          delta: deltas.total_pnl, kind: "money" },
        { label: "Diversificación", before: before.diversification_index,
          after: after.diversification_index, delta: deltas.diversification_index,
          kind: "index" },
        { label: "Posiciones efectivas", before: before.effective_positions,
          after: after.effective_positions, delta: deltas.effective_positions,
          kind: "decimal" },
        { label: "Mayor posición", before: before.largest_position_pct,
          after: after.largest_position_pct, delta: deltas.largest_position_pct,
          kind: "pct" },
      ];
    },

    formatMetric(value, kind) {
      if (value === null || value === undefined) return "—";
      if (kind === "money") return window.fmt.compact(value, this.currency);
      if (kind === "pct") return window.fmt.pct(value, 1);
      if (kind === "index") return window.fmt.num(value).toFixed(1);
      return window.fmt.num(value).toFixed(2);
    },

    formatDelta(value, kind) {
      if (value === null || value === undefined) return "—";
      const n = window.fmt.num(value);
      const sign = n > 0 ? "+" : n < 0 ? "−" : "";
      if (kind === "money") return `${sign}${window.fmt.compact(Math.abs(n), this.currency)}`;
      if (kind === "pct") return `${sign}${Math.abs(n).toFixed(1)} pp`;
      return `${sign}${Math.abs(n).toFixed(kind === "index" ? 1 : 2)}`;
    },

    /* Para la diversificación, MÁS es mejor; para la mayor posición, MENOS.
     * Sin esta distinción el color diría lo contrario de lo que significa. */
    deltaClass(value, label) {
      const n = window.fmt.num(value);
      if (n === null || n === 0) return "text-ink-mute";
      const lowerIsBetter = label === "Mayor posición";
      const good = lowerIsBetter ? n < 0 : n > 0;
      return good ? "text-good" : "text-bad";
    },

    /* Rellena símbolo, divisa y sector desde la sugerencia elegida.
     *
     * El sector importa especialmente aquí: es lo que permite calcular el
     * efecto real sobre la diversificación en vez de agrupar el activo en
     * «Desconocido». Antes había que teclearlo a mano. */
    applySymbol(suggestion) {
      if (!suggestion) return;
      this.draft.symbol = suggestion.symbol;
      // Solo si vienen: el combobox emite al elegir (sin divisa ni sector) y
      // otra vez tras resolver. La guarda evita que el primer evento pise al
      // segundo cuando llegan desordenados.
      if (suggestion.currency) this.draft.currency = suggestion.currency;
      if (suggestion.sector) this.draft.sector = suggestion.sector;

      this.marketPrice = suggestion.price ?? null;
      this.marketPriceAsOf = suggestion.price_as_of ?? null;
      this.marketPriceIsStale = suggestion.price_is_stale ?? false;

      // Aquí SÍ se prellena: una simulación es siempre "si lo hiciera ahora",
      // así que el precio de mercado actual es exactamente el pertinente.
      // (En el alta de una transacción real NO se prellena: allí la fecha
      // puede ser pasada y el precio de hoy sería un dato falso.)
      // No se pisa lo que el usuario ya haya escrito.
      if (this.marketPrice !== null && !this.draft.price) {
        this.draft.price = window.fmt.priceForInput(this.marketPrice);
      }
    },

    useMarketPrice() {
      if (this.marketPrice !== null) this.draft.price = window.fmt.priceForInput(this.marketPrice);
    },

    /* Distancia entre el precio simulado y el de mercado, en %.
     * Simular una compra un 15% por encima del mercado suele ser un dedazo,
     * y verlo evita interpretar mal el resultado. */
    get priceGapPct() {
      const entered = window.fmt.num(this.draft?.price);
      if (entered === null || !this.marketPrice) return null;
      return (entered / this.marketPrice - 1) * 100;
    },

    open(preset = null) {
      this.draft = this.blankDraft();
      this.error = null;

      // Precarga desde la sugerencia óptima: llega con símbolo, precio, divisa
      // y sector ya resueltos, así que solo falta la cantidad.
      if (preset?.symbol) {
        this.draft.symbol = preset.symbol;
        if (preset.price) this.draft.price = window.fmt.priceForInput(preset.price);
        if (preset.currency) this.draft.currency = preset.currency;
        if (preset.sector) this.draft.sector = preset.sector;
        this.marketPrice = preset.price ?? null;
        this.marketPriceAsOf = null;
        this.marketPriceIsStale = false;
      }

      this.isOpen = true;
      this.$nextTick(() => {
        const field = preset?.symbol ? "sim-qty" : "sim-symbol";
        document.getElementById(field)?.focus();
      });
    },

    close() {
      this.isOpen = false;
    },

    reset() {
      this.trades = [];
      this.result = null;
      this.error = null;
      this.draft = this.blankDraft();
    },

    addTrade() {
      const draft = this.draft;
      const trade = {
        type: draft.type,
        fees: draft.fees || "0",
        currency: draft.currency,
      };
      if (this.needsAsset && draft.symbol) trade.symbol = draft.symbol.toUpperCase();
      if (this.needsQuantity) {
        trade.quantity = draft.quantity;
        trade.price = draft.price;
      }
      if (this.needsCash) trade.cash_amount = draft.cash_amount;
      if (draft.fx_rate_to_base) trade.fx_rate_to_base = draft.fx_rate_to_base;
      if (draft.sector) trade.sector = draft.sector;

      this.trades.push(trade);
      this.draft = this.blankDraft();
      this.clearMarketPrice();
      this.run();
    },

    removeTrade(index) {
      this.trades.splice(index, 1);
      if (this.trades.length) {
        this.run();
      } else {
        this.result = null;
        this.error = null;
      }
    },

    tradeLabel(trade) {
      const verb = {
        BUY: "Comprar", SELL: "Vender", DIVIDEND: "Dividendo",
        DEPOSIT: "Depositar", WITHDRAWAL: "Retirar",
      }[trade.type];
      if (trade.quantity) {
        return `${verb} ${trade.quantity} ${trade.symbol} @ ${trade.price} ${trade.currency}`;
      }
      return `${verb} ${trade.cash_amount} ${trade.currency}${trade.symbol ? ` (${trade.symbol})` : ""}`;
    },

    async run() {
      const id = this.$store.app.selectedId;
      if (!id || !this.trades.length) return;

      this.loading = true;
      this.error = null;
      try {
        this.result = await window.api.post(`/api/portfolios/${id}/simulate`, {
          trades: this.trades,
        });
        for (const warning of this.result.warnings ?? []) {
          this.$store.app.notify(warning, "warning", 9000);
        }
        this.$nextTick(() => this.drawComparison());
      } catch (error) {
        // El error se muestra DENTRO del panel: el usuario tiene que verlo
        // junto a la operación que lo provocó.
        this.error = error.message;
        this.result = null;
      } finally {
        this.loading = false;
      }
    },

    /* Dos donuts lado a lado: antes y después.
     *
     * Se descartó un solo donut animado entre ambos estados: la animación
     * cuenta la transición pero impide COMPARAR, que es justo lo que el
     * usuario necesita hacer aquí. Dos gráficos estáticos con la misma escala
     * y los mismos colores por sector permiten mirar uno y otro.
     */
    drawComparison() {
      if (!this.result) return;

      const toItems = (state) =>
        state.sector_exposures.map((e) => ({
          label: e.sector,
          value: window.fmt.num(e.value),
        }));

      window.charts.renderDoughnut(this.$refs.beforeChart, toItems(this.result.current_state), {
        currency: this.currency,
      });
      window.charts.renderDoughnut(
        this.$refs.afterChart,
        toItems(this.result.simulated_state),
        { currency: this.currency }
      );
    },

    /* Aplica las operaciones simuladas de verdad.
     *
     * Se envían una a una al endpoint real: la simulación NUNCA escribe, así
     * que confirmarla es crear transacciones normales, con toda su validación.
     */
    async commit() {
      const id = this.$store.app.selectedId;
      if (!id || !this.trades.length) return;

      this.loading = true;
      this.error = null;
      const now = new Date().toISOString();

      try {
        for (const trade of this.trades) {
          const { sector, ...payload } = trade; // `sector` solo existe al simular
          await window.api.post(`/api/transactions?portfolio_id=${id}`, {
            ...payload,
            executed_at: now,
          });
        }
        this.$store.app.notify(
          `${this.trades.length} operación(es) registradas`,
          "success"
        );
        this.reset();
        this.close();
        window.dispatchEvent(new CustomEvent("transaction-created"));
      } catch (error) {
        // Puede haber quedado a medias: se avisa explícitamente en vez de
        // fingir que no pasó nada.
        this.error =
          `${error.message}. Revisa el historial: puede que algunas operaciones ` +
          `sí se hayan registrado.`;
        window.dispatchEvent(new CustomEvent("transaction-created"));
      } finally {
        this.loading = false;
      }
    },
  }));
});
