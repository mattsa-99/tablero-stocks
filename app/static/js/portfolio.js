/* Vista de portafolio: KPIs, tabla de posiciones y donut de distribución. */


document.addEventListener("alpine:init", () => {
  Alpine.data("portfolioView", () => ({
    // Cuatro estados explícitos y mutuamente excluyentes. `loaded` no es un
    // flag aparte: es `!loading && !error && summary !== null`.
    loading: true,
    error: null,
    summary: null,
    refreshing: false,

    chartMode: "symbol", // symbol | sector
    legend: [],
    sortKey: "market_value",
    sortDesc: true,

    get positions() {
      return this.summary?.positions ?? [];
    },

    get currency() {
      return this.summary?.base_currency ?? this.$store.app.baseCurrency;
    },

    get isEmpty() {
      return !this.loading && !this.error && this.positions.length === 0;
    },

    /* Sin transacciones NI caja: el portafolio está recién creado.
     * Se distingue de "tengo caja pero ninguna posición abierta", que es un
     * estado legítimo tras vender todo y merece un mensaje distinto. */
    get isBrandNew() {
      return this.isEmpty && window.fmt.num(this.summary?.cash_balance) === 0;
    },

    /* Las colecciones que consumen las plantillas se calculan AQUÍ, no con
     * `x-if` envolviendo un `x-for`.
     *
     * Alpine clona `template.content.firstElementChild` para `x-if`; si ese
     * hijo es otro `<template x-for>`, el bucle interior nunca se inicializa y
     * la sección se renderiza vacía sin ningún error en consola. Devolver una
     * lista vacía mientras carga logra lo mismo sin anidar templates. */
    get kpis() {
      if (this.loading || !this.summary) return [];
      return [
        { label: "Valor de mercado", value: this.summary.market_value, kind: "money" },
        { label: "Coste total", value: this.summary.total_cost, kind: "money" },
        { label: "P&L total", value: this.summary.total_pnl, kind: "signed" },
        { label: "Retorno", value: this.summary.total_return_pct, kind: "pct" },
      ];
    },

    get breakdown() {
      if (this.loading || !this.summary) return [];
      return [
        { label: "P&L realizado", value: this.summary.realized_pnl, signed: true },
        { label: "P&L no realizado", value: this.summary.unrealized_pnl, signed: true },
        { label: "Dividendos", value: this.summary.dividend_income, signed: true },
        { label: "Caja", value: this.summary.cash_balance, signed: false },
      ];
    },

    get rows() {
      return this.loading ? [] : this.sortedPositions;
    },

    get skeletonCount() {
      return this.loading ? 4 : 0;
    },

    get sortedPositions() {
      const key = this.sortKey;
      const direction = this.sortDesc ? -1 : 1;
      return [...this.positions].sort((a, b) => {
        const left = key === "symbol" ? a.symbol : window.fmt.num(a[key]);
        const right = key === "symbol" ? b.symbol : window.fmt.num(b[key]);
        // Las posiciones sin precio van al final en ambos sentidos: no tienen
        // valor con el que ordenar y colarlas entre las demás sería engañoso.
        if (left === null) return 1;
        if (right === null) return -1;
        if (left < right) return -1 * direction;
        if (left > right) return 1 * direction;
        return 0;
      });
    },

    sortBy(key) {
      if (this.sortKey === key) {
        this.sortDesc = !this.sortDesc;
      } else {
        this.sortKey = key;
        this.sortDesc = true;
      }
    },

    init() {
      // El watch cubre tanto la selección inicial -que llega de forma
      // asíncrona tras cargar los portafolios- como los cambios del selector.
      this.$watch("$store.app.selectedId", (id) => {
        if (id) this.load();
      });
      if (this.$store.app.selectedId) this.load();

      // Chart.js no lee CSS: al cambiar de tema hay que repintar.
      window.addEventListener("theme-changed", () => this.$nextTick(() => this.drawChart()));
    },

    async load() {
      const id = this.$store.app.selectedId;
      if (!id) {
        this.loading = false;
        return;
      }

      this.loading = true;
      this.error = null;
      try {
        // Se pide el RESUMEN, no /positions: trae las posiciones y además los
        // totales y el P&L en una sola ida y vuelta. Pedir ambos endpoints
        // duplicaría el replay del ledger en el servidor para el mismo dato.
        this.summary = await window.api.get(`/api/portfolios/${id}`);
        this.$nextTick(() => this.drawChart());
        this.warnIfStale();
      } catch (error) {
        this.error = error.message;
        this.summary = null;
        this.$store.app.notify(`No se pudo cargar el portafolio: ${error.message}`, "error");
      } finally {
        this.loading = false;
      }
    },

    /* Refresco solo de la tabla, sin tocar los KPIs.
     * Aquí sí se usa /positions: es exactamente el dato que se repinta, y
     * pedir el resumen entero sería traer de más. */
    async refreshPositions() {
      const id = this.$store.app.selectedId;
      if (!id || this.refreshing) return;

      this.refreshing = true;
      try {
        const positions = await window.api.get(`/api/portfolios/${id}/positions?refresh=true`);
        this.summary = { ...this.summary, positions };
        this.$nextTick(() => this.drawChart());
        this.warnIfStale();
        this.$store.app.notify("Cotizaciones actualizadas", "success", 3000);
      } catch (error) {
        // Fallo al refrescar ≠ fallo al cargar: se conserva lo que ya está en
        // pantalla y solo se avisa. Vaciar la tabla sería peor que datos viejos.
        this.$store.app.notify(`No se pudo actualizar: ${error.message}`, "error");
      } finally {
        this.refreshing = false;
      }
    },

    warnIfStale() {
      const missing = this.summary?.positions_without_price ?? [];
      if (missing.length) {
        this.$store.app.notify(
          `Sin precio de mercado para ${missing.join(", ")}: esas posiciones no se valoran.`,
          "warning",
          9000
        );
      } else if (this.summary?.has_stale_prices) {
        this.$store.app.notify(
          "Algunos precios están obsoletos; se muestra el último valor conocido.",
          "warning"
        );
      }
    },

    /* Agrega por símbolo o por sector. Solo entran posiciones CON precio: una
     * sin valorar no tiene tamaño de porción, y meterla con cero distorsionaría
     * los porcentajes de todas las demás. */
    chartItems() {
      const grouped = new Map();
      for (const position of this.positions) {
        const value = window.fmt.num(position.market_value);
        if (value === null || value <= 0) continue;
        const label =
          this.chartMode === "sector" ? (position.sector ?? "Sin sector") : position.symbol;
        grouped.set(label, (grouped.get(label) ?? 0) + value);
      }
      return [...grouped].map(([label, value]) => ({ label, value }));
    },

    drawChart() {
      const canvas = this.$refs.donut;
      if (!canvas) return;

      const items = this.chartItems();
      if (!items.length) {
        window.charts.destroyChart(canvas);
        this.legend = [];
        return;
      }

      const result = window.charts.renderDoughnut(canvas, items, { currency: this.currency });
      if (!result) {
        this.legend = [];
        return;
      }
      const { slices, colors, total } = result;

      // La leyenda se construye en HTML con valor y porcentaje por fila. Es lo
      // que permite comparar magnitudes parecidas, que es justo lo que un donut
      // no resuelve por geometría.
      this.legend = slices.map((slice, index) => ({
        label: slice.label,
        value: slice.value,
        share: total > 0 ? (slice.value / total) * 100 : 0,
        color: colors[index],
      }));
    },

    setChartMode(mode) {
      if (this.chartMode === mode) return;
      this.chartMode = mode;
      this.$nextTick(() => this.drawChart());
    },
  }));

  // -------------------------------------------------------------------------
  // Alta de transacciones
  // -------------------------------------------------------------------------

  Alpine.data("transactionModal", () => ({
    isOpen: false,
    saving: false,
    error: null,
    form: {},

    marketPrice: null,
    marketPriceAsOf: null,
    marketPriceIsStale: false,

    // Qué campos pide cada tipo. Refleja las restricciones CHECK del ledger:
    // una compra exige símbolo, cantidad y precio; un depósito no admite
    // símbolo. Validarlo aquí evita un viaje al servidor para saberlo.
    get needsAsset() {
      return ["BUY", "SELL", "DIVIDEND"].includes(this.form.type);
    },

    get needsQuantity() {
      return ["BUY", "SELL"].includes(this.form.type);
    },

    get needsCash() {
      return ["DIVIDEND", "DEPOSIT", "WITHDRAWAL"].includes(this.form.type);
    },

    blank() {
      // datetime-local necesita hora LOCAL sin zona; el offset se añade al
      // enviar. Mandar un naive haría que el backend lo rechace.
      const now = new Date();
      const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
      return {
        type: "BUY",
        symbol: "",
        quantity: "",
        price: "",
        cash_amount: "",
        fees: "0",
        currency: "USD",
        fx_rate_to_base: "",
        executed_at: local.toISOString().slice(0, 16),
        notes: "",
      };
    },

    /* Recibe la sugerencia YA resuelta del combobox y rellena los campos que
     * el usuario no tendría por qué conocer.
     *
     * La divisa solo se sobrescribe si la sugerencia la trae: el combobox
     * emite dos veces -al elegir y tras resolver-, y la primera aún no la
     * tiene. Sin la guarda, el segundo evento borraría lo que puso el primero. */
    applySymbol(suggestion) {
      if (!suggestion) return;
      this.form.symbol = suggestion.symbol;
      if (suggestion.currency) this.form.currency = suggestion.currency;

      this.marketPrice = suggestion.price ?? null;
      this.marketPriceAsOf = suggestion.price_as_of ?? null;
      this.marketPriceIsStale = suggestion.price_is_stale ?? false;
      // A propósito NO se rellena `form.price`. Aquí la fecha la elige el
      // usuario y puede ser pasada: prellenar el precio de HOY en una compra
      // de hace tres semanas es un dato falso fácil de guardar sin mirar, y el
      // coste base queda mal para siempre. Se ofrece a un clic.
    },

    useMarketPrice() {
      if (this.marketPrice !== null) this.form.price = window.fmt.priceForInput(this.marketPrice);
    },

    open() {
      this.form = this.blank();
      this.error = null;
      this.marketPrice = null;
      this.marketPriceAsOf = null;
      this.marketPriceIsStale = false;
      this.isOpen = true;
      this.$nextTick(() => document.getElementById("tx-symbol")?.focus());
    },

    close() {
      this.isOpen = false;
    },

    async submit() {
      const id = this.$store.app.selectedId;
      if (!id) return;

      this.saving = true;
      this.error = null;

      const payload = {
        type: this.form.type,
        executed_at: new Date(this.form.executed_at).toISOString(),
        fees: this.form.fees || "0",
        currency: this.form.currency,
      };
      if (this.needsAsset && this.form.symbol) payload.symbol = this.form.symbol.toUpperCase();
      if (this.needsQuantity) {
        payload.quantity = this.form.quantity;
        payload.price = this.form.price;
      }
      if (this.needsCash) payload.cash_amount = this.form.cash_amount;
      if (this.form.fx_rate_to_base) payload.fx_rate_to_base = this.form.fx_rate_to_base;
      if (this.form.notes) payload.notes = this.form.notes;

      try {
        await window.api.post(`/api/transactions?portfolio_id=${id}`, payload);
        this.$store.app.notify("Transacción registrada", "success", 3500);
        this.close();
        // La vista de portafolio escucha esto y recarga.
        window.dispatchEvent(new CustomEvent("transaction-created"));
      } catch (error) {
        this.error = error.message;
      } finally {
        this.saving = false;
      }
    },
  }));
});
