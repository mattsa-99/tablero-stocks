/* Diario de decisiones y lista de vigilancia.
 *
 * Guarda TUS razones -tesis, qué te haría cambiar de opinión, cuándo revisar- y
 * las compara con lo que dice el Tablero hoy. Una alerta pide REVISAR, nunca
 * vender. Anotar una decisión no crea ninguna transacción.
 */

const KIND_LABEL = {
  WATCH: "Vigilando",
  BUY: "Compré / voy a comprar",
  HOLD: "Mantengo",
  SELL: "Vendí / voy a vender",
  PASS: "Descartada",
};

/* Clases completas como literales para el escáner de Tailwind. El color nunca
 * va solo: cada alerta lleva su etiqueta escrita. */
const ALERT_STYLE = {
  red: { label: "Urgente", text: "text-bad", ring: "border-bad/40", bg: "bg-bad/10" },
  yellow: { label: "Revisar", text: "text-warn", ring: "border-warn/40", bg: "bg-warn/10" },
  info: { label: "Aviso", text: "text-ink-mute", ring: "border-line", bg: "bg-surf-2" },
};

const GRADE_LABEL_ES = {
  A: "Muy buena", B: "Buena", C: "Normal", D: "Mala", E: "Muy mala",
};

function emptyForm() {
  return {
    symbol: "",
    kind: "WATCH",
    thesis: "",
    invalidation: "",
    invalidation_price: "",
    review_date: "",
  };
}

document.addEventListener("alpine:init", () => {
  Alpine.data("journalView", () => ({
    loading: true,
    error: null,
    data: null,
    includeArchived: false,

    formOpen: false,
    editingId: null,
    form: emptyForm(),
    saving: false,
    formError: null,

    reviewingId: null,
    reviewNote: "",
    reviewNext: "",
    reviewArchive: false,

    kinds: Object.entries(KIND_LABEL).map(([value, label]) => ({ value, label })),

    get entries() {
      return this.data?.entries ?? [];
    },

    get counts() {
      return this.data?.counts ?? { active: 0, overdue: 0, breached: 0, with_alerts: 0 };
    },

    init() {
      // `?nuevo=KO` viene de la ficha: abre el formulario con el símbolo listo.
      const wanted = new URLSearchParams(window.location.search).get("nuevo");
      if (wanted) {
        this.form.symbol = wanted.trim().toUpperCase();
        this.formOpen = true;
      }
      this.$watch("$store.app.selectedId", (id) => {
        if (id) this.load();
      });
      if (this.$store.app.selectedId) this.load();
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
        const params = new URLSearchParams({ portfolio_id: id });
        if (this.includeArchived) params.set("include_archived", "true");
        this.data = await window.api.get(`/api/journal?${params}`);
        this.$store.app.refreshJournalCounts?.();
      } catch (error) {
        this.data = null;
        this.error = error.message;
      } finally {
        this.loading = false;
      }
    },

    toggleArchived() {
      this.includeArchived = !this.includeArchived;
      this.load();
    },

    // ---------------- Crear y editar ----------------

    openNew() {
      this.editingId = null;
      this.form = emptyForm();
      this.formError = null;
      this.formOpen = true;
    },

    openEdit(entry) {
      this.editingId = entry.id;
      this.form = {
        symbol: entry.symbol,
        kind: entry.kind,
        thesis: entry.thesis,
        invalidation: entry.invalidation ?? "",
        invalidation_price: entry.invalidation_price ?? "",
        review_date: entry.review_date,
      };
      this.formError = null;
      this.formOpen = true;
      this.$nextTick(() => document.getElementById("journal-form")?.scrollIntoView({ block: "center" }));
    },

    closeForm() {
      this.formOpen = false;
      this.editingId = null;
    },

    /* Cuerpo para la API. Un campo vacío es `null` -borra o no se envía- y
     * nunca una cadena vacía. */
    payload() {
      const price = String(this.form.invalidation_price).replace(/[^0-9.]/g, "");
      return {
        kind: this.form.kind,
        thesis: this.form.thesis.trim(),
        invalidation: this.form.invalidation.trim() || null,
        invalidation_price: price ? price : null,
        review_date: this.form.review_date || null,
      };
    },

    async save() {
      const id = this.$store.app.selectedId;
      if (!id) return;
      this.saving = true;
      this.formError = null;
      try {
        const body = this.payload();
        if (this.editingId) {
          // `review_date` vacío al editar = no tocarla.
          if (!body.review_date) delete body.review_date;
          await window.api.patch(`/api/journal/${this.editingId}`, body);
          this.$store.app.notify("Entrada actualizada", "success");
        } else {
          await window.api.post(`/api/journal?portfolio_id=${id}`, {
            ...body,
            symbol: this.form.symbol.trim().toUpperCase(),
          });
          this.$store.app.notify("Decisión anotada. No se creó ninguna operación.", "success");
        }
        this.closeForm();
        await this.load();
      } catch (error) {
        this.formError = error.message;
      } finally {
        this.saving = false;
      }
    },

    // ---------------- Revisar, cerrar, borrar ----------------

    openReview(entry) {
      this.reviewingId = entry.id;
      this.reviewNote = "";
      this.reviewNext = "";
      this.reviewArchive = false;
    },

    async submitReview() {
      if (!this.reviewingId) return;
      try {
        const body = { note: this.reviewNote.trim(), archive: this.reviewArchive };
        if (this.reviewNext) body.next_review_date = this.reviewNext;
        await window.api.post(`/api/journal/${this.reviewingId}/review`, body);
        this.reviewingId = null;
        this.$store.app.notify("Revisión guardada", "success");
        await this.load();
      } catch (error) {
        this.$store.app.notify(error.message, "error");
      }
    },

    async reopen(entry) {
      try {
        await window.api.patch(`/api/journal/${entry.id}`, { is_active: true });
        await this.load();
      } catch (error) {
        this.$store.app.notify(error.message, "error");
      }
    },

    async remove(entry) {
      // Borrar es irreversible y el diario existe para conservar razones: se
      // confirma, y se sugiere cerrar en lugar de borrar.
      if (!window.confirm(`¿Borrar la entrada de ${entry.symbol}? Cerrarla conserva tu razonamiento; borrarla lo pierde.`)) return;
      try {
        await window.api.delete(`/api/journal/${entry.id}`);
        await this.load();
      } catch (error) {
        this.$store.app.notify(error.message, "error");
      }
    },

    // ---------------- Presentación ----------------

    kindLabel(kind) {
      return KIND_LABEL[kind] ?? kind;
    },

    alertStyle(level) {
      return ALERT_STYLE[level] ?? ALERT_STYLE.info;
    },

    gradeLabel(grade) {
      return GRADE_LABEL_ES[grade] ?? "sin calificar";
    },

    dateLabel(iso) {
      if (!iso) return "—";
      // Mediodía: una fecha sin hora se interpreta en UTC y en Bogotá caería
      // el día anterior.
      return new Intl.DateTimeFormat("es-CO", { dateStyle: "medium" }).format(
        new Date(`${iso}T12:00:00`),
      );
    },

    reviewText(entry) {
      const d = entry.days_to_review;
      if (d < 0) return `hace ${-d} días`;
      if (d === 0) return "hoy";
      return `en ${d} días`;
    },

    signedPct(value) {
      if (value === null || value === undefined) return "—";
      const sign = value > 0 ? "+" : value < 0 ? "−" : "";
      return `${sign}${Math.abs(value).toFixed(1)}%`;
    },

    price(value, currency) {
      if (value === null || value === undefined) return "—";
      return window.fmt.money(value, currency);
    },
  }));
});
