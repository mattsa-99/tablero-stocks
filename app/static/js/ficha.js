/* Ficha de compra: todo lo que hay que mirar de UNA empresa antes de decidir.
 *
 * Es solo presentación: banderas, veredicto, salud y pares los calcula el
 * backend (`services/ficha.py`) y el script `scripts/ficha.py` imprime
 * exactamente lo mismo en markdown. Aquí no se decide nada.
 *
 * El veredicto NUNCA dice «compra». Dice si el filtro encontró problemas, y la
 * respuesta lo repite en su `disclaimer`, que se muestra siempre.
 */

/* Estilo por nivel de bandera. Las clases van COMPLETAS como literales para que
 * el escáner de Tailwind las vea. El color nunca va solo: cada bandera lleva su
 * etiqueta de texto («Roja», «Amarilla»…). */
const LEVEL_STYLE = {
  red: { label: "Roja", text: "text-bad", ring: "border-bad/40", bg: "bg-bad/10" },
  yellow: { label: "Amarilla", text: "text-warn", ring: "border-warn/40", bg: "bg-warn/10" },
  green: { label: "Verde", text: "text-good", ring: "border-good/40", bg: "bg-good/10" },
  info: { label: "Info", text: "text-ink-mute", ring: "border-line", bg: "bg-surf-2" },
};

const LEVEL_ORDER = ["red", "yellow", "green", "info"];

/* Fracción (0,12) -> «12,0%». Las métricas de salud vienen como fracciones. */
function ratioPct(value, digits = 1) {
  if (value === null || value === undefined) return "n/d";
  return `${(value * 100).toFixed(digits)}%`;
}

function plain(value, digits = 1, suffix = "") {
  if (value === null || value === undefined) return "n/d";
  return `${Number(value).toFixed(digits)}${suffix}`;
}

document.addEventListener("alpine:init", () => {
  Alpine.data("fichaModal", () => ({
    isOpen: false,
    loading: false,
    error: null,
    data: null,
    symbol: null,

    async open(symbol) {
      this.symbol = symbol;
      this.isOpen = true;
      this.data = null;
      this.error = null;
      this.sizingCapital = "";
      this.sizingRisk = "";
      this.sizingCap = "";
      this.sizingError = null;
      await this.load();
    },

    close() {
      this.isOpen = false;
    },

    async load() {
      const id = this.$store.app.selectedId;
      if (!id || !this.symbol) return;
      this.loading = true;
      this.error = null;
      try {
        this.data = await window.api.get(
          `/api/opportunities/${encodeURIComponent(this.symbol)}/ficha?portfolio_id=${id}`,
        );
      } catch (error) {
        this.data = null;
        this.error = error.message;
      } finally {
        this.loading = false;
      }
    },

    /* Supuestos del dimensionado. Vacío = los de la cartera y los valores por
     * defecto del servidor. Recalcular NO rehace la ficha entera. */
    sizingCapital: "",
    sizingRisk: "",
    sizingCap: "",
    sizingBusy: false,
    sizingError: null,

    async recalcSizing() {
      const id = this.$store.app.selectedId;
      if (!id || !this.symbol) return;
      const params = new URLSearchParams({ portfolio_id: id });
      const capital = String(this.sizingCapital).replace(/[^0-9.]/g, "");
      if (capital) params.set("capital", capital);
      if (this.sizingRisk !== "") params.set("risk_budget_pct", this.sizingRisk);
      if (this.sizingCap !== "") params.set("max_position_pct", this.sizingCap);
      this.sizingBusy = true;
      this.sizingError = null;
      try {
        const sizing = await window.api.get(
          `/api/opportunities/${encodeURIComponent(this.symbol)}/sizing?${params}`,
        );
        this.data = { ...this.data, sizing };
      } catch (error) {
        this.sizingError = error.message;
      } finally {
        this.sizingBusy = false;
      }
    },

    get sizing() {
      return this.data?.sizing ?? null;
    },

    /* Cifra en divisa base, sin decimales: importes de una cartera personal. */
    money(value) {
      if (value === null || value === undefined) return "—";
      return window.fmt.money(value, this.data?.portfolio.base_currency ?? "COP");
    },

    levelStyle(level) {
      return LEVEL_STYLE[level] ?? LEVEL_STYLE.info;
    },

    /* Banderas en el orden en que se leen: primero lo que obliga a parar.
     * Lista PLANA a propósito: anidar plantillas dentro de plantillas es
     * justo donde Alpine deja secciones vacías sin avisar. */
    get sortedFlags() {
      const flags = this.data?.flags ?? [];
      return LEVEL_ORDER.flatMap((level) => flags.filter((f) => f.level === level));
    },

    get health() {
      return this.data?.health ?? null;
    },

    /* Filas de la tabla de salud. Una métrica ausente se muestra como «n/d»
     * con el MOTIVO en el tooltip: un hueco sin explicación parece un fallo. */
    get healthRows() {
      const h = this.health;
      if (!h || !h.has_data || !h.applies) return [];
      const why = (key) => h.unavailable?.[key] ?? "";
      return [
        { label: "Deuda neta / EBITDA", value: plain(h.net_debt_to_ebitda, 1, "x"), why: why("net_debt_to_ebitda") },
        { label: "Margen operativo", value: ratioPct(h.operating_margin), why: "" },
        { label: "Caja libre / ventas", value: ratioPct(h.fcf_margin), why: why("fcf_margin") },
        { label: "Rendimiento de caja libre", value: ratioPct(h.fcf_yield), why: why("fcf_yield") },
        { label: "Current ratio", value: plain(h.current_ratio, 2), why: "" },
        { label: "Reparto en dividendo", value: ratioPct(h.payout_ratio, 0), why: "" },
      ];
    },

    ratioPct,
    plain,

    signedRatio(value) {
      if (value === null || value === undefined) return "n/d";
      const sign = value > 0 ? "+" : value < 0 ? "−" : "";
      return `${sign}${Math.abs(value * 100).toFixed(1)}%`;
    },
  }));
});
