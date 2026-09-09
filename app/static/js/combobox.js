/* Combobox de búsqueda de símbolos.
 *
 * Un solo componente para los dos formularios que piden un ticker (alta de
 * transacción y modo simulación): duplicarlo garantizaría que las dos copias
 * diverjan en el manejo de teclado, que es la parte fácil de hacer mal.
 *
 * Al seleccionar emite `symbol-selected` con la sugerencia YA resuelta
 * (divisa y sector incluidos), para que el formulario padre rellene esos
 * campos y el usuario no tenga que saberlos.
 */

const DEBOUNCE_MS = 250;
const MIN_QUERY = 2;

const TYPE_LABEL = {
  STOCK: "Acción",
  ETF: "ETF",
  ADR: "ADR",
  REIT: "REIT",
  FUND: "Fondo",
  CRYPTO: "Cripto",
  OTHER: "Otro",
};

document.addEventListener("alpine:init", () => {
  Alpine.data("symbolCombobox", (config = {}) => ({
    // `inputId` permite que haya varias instancias en la misma página sin que
    // los ids de las opciones (aria-activedescendant) colisionen.
    inputId: config.inputId ?? "symbol-combobox",
    placeholder: config.placeholder ?? "Busca por nombre o ticker…",

    query: "",
    results: [],
    open: false,
    loading: false,
    resolving: false,
    error: null,
    highlighted: -1,
    selected: null,

    _timer: null,
    _requestId: 0,

    get showDropdown() {
      return this.open && (this.loading || this.error || this.results.length || this.searched);
    },

    searched: false,

    get isTooShort() {
      return this.query.trim().length > 0 && this.query.trim().length < MIN_QUERY;
    },

    get isEmpty() {
      return this.searched && !this.loading && !this.error && this.results.length === 0;
    },

    optionId(index) {
      return `${this.inputId}-option-${index}`;
    },

    get activeDescendant() {
      return this.highlighted >= 0 ? this.optionId(this.highlighted) : null;
    },

    typeLabel(type) {
      return TYPE_LABEL[type] ?? type;
    },

    onInput() {
      this.selected = null;
      this.error = null;
      this.highlighted = -1;
      clearTimeout(this._timer);

      const term = this.query.trim();
      if (term.length < MIN_QUERY) {
        this.results = [];
        this.searched = false;
        this.loading = false;
        this.open = term.length > 0;
        return;
      }

      this.open = true;
      this.loading = true;
      // Debounce: sin él, escribir "nvidia" dispara seis peticiones a un
      // proveedor con rate limit para una sola búsqueda.
      this._timer = setTimeout(() => this.search(term), DEBOUNCE_MS);
    },

    async search(term) {
      // Contador de petición: las respuestas pueden llegar desordenadas, y sin
      // esto una búsqueda antigua y lenta pisaría los resultados de la actual.
      const requestId = ++this._requestId;
      try {
        const body = await window.api.get(
          `/api/market-data/search?q=${encodeURIComponent(term)}&limit=8`
        );
        if (requestId !== this._requestId) return;

        this.results = body.results;
        this.searched = true;
        this.highlighted = body.results.length ? 0 : -1;

        for (const warning of body.warnings ?? []) {
          this.$store.app.notify(warning, "warning", 6000);
        }
      } catch (error) {
        if (requestId !== this._requestId) return;
        this.error = error.message;
        this.results = [];
      } finally {
        if (requestId === this._requestId) this.loading = false;
      }
    },

    move(delta) {
      if (!this.results.length) return;
      const next = this.highlighted + delta;
      // Envolvente: bajar desde el último vuelve al primero. En una lista de
      // ocho es más rápido que obligar a recorrerla al revés.
      this.highlighted = (next + this.results.length) % this.results.length;
      this.$nextTick(() => {
        document
          .getElementById(this.optionId(this.highlighted))
          ?.scrollIntoView({ block: "nearest" });
      });
    },

    onEnter() {
      if (this.highlighted >= 0 && this.results[this.highlighted]) {
        this.choose(this.results[this.highlighted]);
      }
    },

    onEscape() {
      this.open = false;
      this.highlighted = -1;
    },

    close() {
      this.open = false;
      this.highlighted = -1;
    },

    async choose(suggestion) {
      this.selected = suggestion;
      this.query = suggestion.symbol;
      this.close();

      // Se emite ya con lo que se sabe, para que el símbolo quede fijado al
      // instante aunque resolver divisa y sector tarde o falle.
      this.emit(suggestion);

      // Yahoo no devuelve divisa ni sector en la búsqueda: se resuelven aquí,
      // una sola vez, al seleccionar.
      if (suggestion.currency && suggestion.sector) return;

      this.resolving = true;
      try {
        const resolved = await window.api.get(
          `/api/market-data/resolve/${encodeURIComponent(suggestion.symbol)}`
        );
        this.selected = resolved;
        this.emit(resolved);
      } catch (error) {
        // No es un fallo del flujo: el símbolo ya está elegido y el usuario
        // puede escribir divisa y sector a mano.
        this.$store.app.notify(
          `No se pudieron completar los datos de ${suggestion.symbol}: ${error.message}`,
          "warning",
          6000
        );
      } finally {
        this.resolving = false;
      }
    },

    emit(suggestion) {
      this.$dispatch("symbol-selected", suggestion);
    },

    clear() {
      this.query = "";
      this.results = [];
      this.selected = null;
      this.searched = false;
      this.error = null;
      this.close();
      this.$dispatch("symbol-cleared");
      this.$nextTick(() => document.getElementById(this.inputId)?.focus());
    },

    /* Permite al formulario padre reiniciar el campo tras enviar. */
    reset() {
      this.query = "";
      this.results = [];
      this.selected = null;
      this.searched = false;
      this.error = null;
      this.close();
    },
  }));
});
