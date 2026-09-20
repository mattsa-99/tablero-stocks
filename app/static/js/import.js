/* Importar operaciones desde un CSV con la plantilla fija.
 *
 * Siempre se REVISA antes de importar: al elegir el archivo se hace una
 * revisión en seco (no escribe nada) y el botón de importar solo se habilita
 * si esa revisión salió limpia. Con un solo error no se importa ninguna fila.
 */

document.addEventListener("alpine:init", () => {
  Alpine.data("importModal", () => ({
    isOpen: false,
    busy: false,
    fileName: null,
    csvText: null,
    report: null,
    error: null,

    open() {
      this.isOpen = true;
      this.fileName = null;
      this.csvText = null;
      this.report = null;
      this.error = null;
    },

    close() {
      this.isOpen = false;
    },

    /* La revisión limpia es la única que habilita importar. */
    get canImport() {
      const r = this.report;
      return Boolean(r && r.dry_run && !r.applied && r.errors.length === 0 && r.new_rows > 0);
    },

    get previewRows() {
      return (this.report?.preview ?? []).slice(0, 40);
    },

    async onFile(event) {
      const file = event.target.files?.[0];
      if (!file) return;
      this.fileName = file.name;
      this.error = null;
      this.report = null;
      try {
        // utf-8: el CSV de la plantilla se guarda así. Un Excel en español
        // puede guardar en otra codificación y las tildes saldrían rotas.
        this.csvText = await file.text();
      } catch {
        this.error = "No se pudo leer el archivo";
        return;
      }
      await this.run(true);
    },

    async run(dryRun) {
      const id = this.$store.app.selectedId;
      if (!id || !this.csvText) return;
      this.busy = true;
      this.error = null;
      try {
        this.report = await window.api.post(
          `/api/transactions/import?portfolio_id=${id}&dry_run=${dryRun}`,
          { csv: this.csvText },
        );
        if (this.report.applied) {
          this.$store.app.notify(this.report.message, "success", 5000);
          window.dispatchEvent(new CustomEvent("transaction-created"));
          this.close();
        }
      } catch (error) {
        this.report = null;
        this.error = error.message;
      } finally {
        this.busy = false;
      }
    },

    typeLabel(type) {
      return { BUY: "Compra", SELL: "Venta", DIVIDEND: "Dividendo", DEPOSIT: "Depósito", WITHDRAWAL: "Retiro" }[type] ?? type;
    },
  }));
});
