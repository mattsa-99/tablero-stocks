/* Plan de asignación entre clases de activo.
 *
 * Es la única pantalla que escribe una INTENCIÓN del usuario en vez de derivar
 * algo del ledger. Por eso el editor y la medición viven juntos: ver el desvío
 * sin poder corregir el plan invita a cambiar la cartera cuando a veces lo que
 * hay que cambiar es el plan.
 */

/* Las clases sobre las que se puede fijar un objetivo. Debe coincidir con
 * `allocation.PLANNABLE_CLASSES`; `test_the_plan_page_knows_every_plannable_class`
 * compara las dos tablas. `derivado` no está porque no es accionable y
 * `desconocido` porque no es una decisión, es una carencia de datos. */
const PLANNABLE = [
  { key: "fondo_acciones", label: "Fondos de acciones" },
  { key: "accion", label: "Acciones" },
  { key: "renta_fija", label: "Renta fija" },
  { key: "materias_primas", label: "Materias primas" },
  { key: "cripto", label: "Cripto" },
];

const STATUS_LABEL = {
  dentro: "en banda",
  por_debajo: "por debajo",
  por_encima: "por encima",
  sin_plan: "sin objetivo",
};

/* «Por encima» NO es rojo. Tener de más en una clase no es un problema como lo
 * es una bandera roja: es una desviación que se corrige con el siguiente
 * aporte. El rojo se reserva para lo que exige actuar. */
const STATUS_CLASS = {
  dentro: "border-good/30 bg-good/10 text-good",
  por_debajo: "border-s1/30 bg-s1/10 text-s1",
  por_encima: "border-warn/30 bg-warn/10 text-warn",
  sin_plan: "border-line bg-transparent text-ink-mute",
};

function planPage() {
  return {
    data: null,
    error: null,
    saving: false,
    contribution: 0,
    editable: PLANNABLE.map((c) => ({ ...c, target: 0, band: 5 })),

    get rows() {
      return this.data?.positions ?? [];
    },

    get plannedTotal() {
      return this.editable.reduce((sum, c) => sum + (Number(c.target) || 0), 0);
    },

    init() {
      // Igual que el resto de vistas: la selección inicial de cartera llega de
      // forma asíncrona y un evento se perdería.
      this.$watch("$store.app.selectedId", () => this.load());
      if (this.$store.app.selectedId) this.load();
    },

    async load() {
      const id = this.$store.app.selectedId;
      if (!id) return;
      this.error = null;
      try {
        const params = new URLSearchParams({ portfolio_id: id });
        if (this.contribution > 0) params.set("contribution", this.contribution);
        this.data = await window.api.get(`/api/allocation?${params}`);
        this.syncEditor();
      } catch (e) {
        this.error = e.message ?? "No se pudo cargar el plan.";
      }
    },

    /* El editor refleja lo guardado, no al revés: si el usuario recarga, tiene
     * que ver su plan y no unos ceros que borrarían lo que había al guardar. */
    syncEditor() {
      const porClase = Object.fromEntries(
        (this.data?.positions ?? []).map((p) => [p.asset_class, p]),
      );
      this.editable = PLANNABLE.map((c) => {
        const fila = porClase[c.key];
        return {
          ...c,
          target: fila?.target_pct !== null && fila?.target_pct !== undefined
            ? Number(fila.target_pct) : 0,
          band: fila?.band_pct !== null && fila?.band_pct !== undefined
            ? Number(fila.band_pct) : 5,
        };
      });
    },

    async save() {
      const id = this.$store.app.selectedId;
      if (!id) return;
      this.saving = true;
      this.error = null;
      try {
        // Solo se envían las clases con objetivo > 0: mandar ceros crearía
        // filas que dicen «quiero exactamente 0% aquí», que es una afirmación
        // distinta de «no lo he decidido».
        const targets = this.editable
          .filter((c) => Number(c.target) > 0)
          .map((c) => ({
            asset_class: c.key,
            target_pct: Number(c.target),
            band_pct: Number(c.band) || 0,
          }));
        this.data = await window.api.put(
          `/api/allocation?portfolio_id=${id}`, { targets },
        );
        this.syncEditor();
      } catch (e) {
        this.error = e.message ?? "No se pudo guardar el plan.";
      } finally {
        this.saving = false;
      }
    },

    labelOf(key) {
      return PLANNABLE.find((c) => c.key === key)?.label ?? key;
    },

    statusLabel(status) {
      return STATUS_LABEL[status] ?? status;
    },

    statusClass(status) {
      return STATUS_CLASS[status] ?? STATUS_CLASS.sin_plan;
    },

    clamp(value) {
      return Math.max(0, Math.min(100, window.fmt.num(value) ?? 0));
    },

    pct(value) {
      const n = window.fmt.num(value);
      return n === null ? "—" : `${n.toFixed(1)}%`;
    },

    money(value) {
      const n = window.fmt.num(value);
      if (n === null) return "—";
      return window.fmt.money(n, this.data?.base_currency ?? "USD");
    },
  };
}

window.planPage = planPage;
