/* Modal de "Sugerencia óptima".
 *
 * Todo el cálculo -score, correlación, impacto en volatilidad- lo hace el
 * backend. Este componente presenta y encadena con el simulador.
 */

document.addEventListener("alpine:init", () => {
  Alpine.data("suggestionModal", () => ({
    isOpen: false,
    loading: false,
    error: null,
    data: null,

    get pick() {
      return this.data?.suggestion ?? null;
    },

    get runnersUp() {
      return this.data?.runners_up ?? [];
    },

    /* Sin sugerencia NO es un error: es el resultado honesto cuando ningún
     * candidato pasa la guarda de calidad. Merece su propio estado, no un
     * mensaje rojo de fallo. */
    get isEmpty() {
      return !this.loading && !this.error && this.data && !this.pick;
    },

    open() {
      this.isOpen = true;
      if (!this.data) this.load();
    },

    close() {
      this.isOpen = false;
    },

    async load() {
      const id = this.$store.app.selectedId;
      if (!id) return;

      this.loading = true;
      this.error = null;
      try {
        this.data = await window.api.get(
          `/api/portfolios/${id}/suggested-stock?refresh=false`
        );
        for (const warning of this.data.warnings ?? []) {
          this.$store.app.notify(warning, "warning", 8000);
        }
      } catch (error) {
        this.error = error.message;
        this.data = null;
      } finally {
        this.loading = false;
      }
    },

    gradeStyle(grade) {
      const styles = {
        A: "border-good/50 bg-good/10 text-good",
        B: "border-good/30 bg-good/5 text-good",
        C: "border-line bg-surf-2 text-ink-soft",
        D: "border-warn/40 bg-warn/10 text-warn",
        E: "border-bad/50 bg-bad/10 text-bad",
      };
      return styles[grade] ?? "border-line text-ink-mute";
    },

    /* Los tres multiplicadores, para que la fórmula se pueda verificar a ojo. */
    factorRows(pick) {
      const f = pick.factors;
      return [
        { label: "Opportunity Score", value: f.opportunity_score.toFixed(1), note: "puesto #" + pick.rank_in_opportunities },
        {
          label: "Correlación",
          value: "×" + f.correlation_factor.toFixed(2),
          note: f.correlation === null ? "no medible" : `ρ = ${f.correlation.toFixed(2)}`,
        },
        {
          label: "Concentración",
          value: "×" + f.concentration_factor.toFixed(2),
          note: `«${pick.exposure_bucket}» al ${f.bucket_weight_pct.toFixed(1)}%`,
        },
      ];
    },

    /* Encadena con el simulador, que vive en la OTRA vista.
     *
     * El traspaso va por sessionStorage y no por querystring: la compra lleva
     * precio, divisa y sector, y meterlos en la URL la vuelve ilegible y
     * compartible por error. sessionStorage además se limpia al cerrar la
     * pestaña, que es justo la vida útil de una sugerencia. */
    handoffToSimulator() {
      const pick = this.pick;
      if (!pick) return;
      sessionStorage.setItem(
        "tablero:simulate",
        JSON.stringify({
          symbol: pick.symbol,
          price: pick.current_price,
          currency: pick.currency,
          sector: pick.opportunity?.sector ?? null,
        })
      );
    },
  }));
});
