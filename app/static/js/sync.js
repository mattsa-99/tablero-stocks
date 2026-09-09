/* Botón de sincronización y estado del pipeline de ingesta. */

const RELATIVE = new Intl.RelativeTimeFormat("es", { numeric: "auto" });

/* "hace 3 horas" en vez de una marca de tiempo absoluta.
 *
 * Para "¿están frescos mis datos?" la distancia importa más que la hora exacta:
 * "30/08/2026 18:00" obliga a calcular mentalmente; "hace 2 horas" no. */
function relativeTime(iso) {
  if (!iso) return "nunca";
  const seconds = (new Date(iso) - Date.now()) / 1000;
  const units = [
    ["day", 86400],
    ["hour", 3600],
    ["minute", 60],
  ];
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size) {
      return RELATIVE.format(Math.round(seconds / size), unit);
    }
  }
  return "hace un momento";
}

document.addEventListener("alpine:init", () => {
  Alpine.data("syncWidget", () => ({
    loading: true,
    syncing: false,
    status: null,

    get lastRun() {
      return this.status?.last_successful_run ?? null;
    },

    get lastSyncLabel() {
      return relativeTime(this.lastRun?.finished_at);
    },

    get isStale() {
      return this.status?.is_due ?? false;
    },

    get lastRunFailed() {
      return this.status?.last_run?.status === "FAILED";
    },

    get bars() {
      return this.status?.storage?.bars ?? 0;
    },

    get storageLabel() {
      const bytes = this.status?.storage?.estimated_bytes ?? 0;
      if (!bytes) return "sin histórico";
      const mb = bytes / 1e6;
      return mb < 1 ? `${Math.round(bytes / 1e3)} KB` : `${mb.toFixed(1)} MB`;
    },

    init() {
      this.load();
      // El panel de simulación y la vista de portafolio disparan esto tras
      // cambiar datos que afectan al estado del pipeline.
      window.addEventListener("sync-completed", () => this.load());
    },

    async load() {
      this.loading = true;
      try {
        this.status = await window.api.get("/api/market-data/status");
      } catch (error) {
        // El indicador de frescura no es crítico: si falla se oculta en vez de
        // llenar la pantalla de errores.
        console.warn("No se pudo leer el estado de sincronización:", error.message);
        this.status = null;
      } finally {
        this.loading = false;
      }
    },

    async sync() {
      if (this.syncing) return;
      this.syncing = true;

      const notice = this.$store.app.notify(
        "Sincronizando datos de mercado…",
        "info",
        0 // sin autocierre: se reemplaza al terminar
      );

      try {
        const run = await window.api.post("/api/market-data/sync", {});
        this.$store.app.dismiss(notice);

        const written =
          run.quotes_updated + run.bars_written + run.fundamentals_updated + run.fx_updated;
        const detail =
          `${run.quotes_updated} cotizaciones · ${run.bars_written} barras · ` +
          `${run.fundamentals_updated} fundamentales`;

        if (run.status === "FAILED") {
          this.$store.app.notify(`La sincronización falló. ${run.warnings ?? ""}`, "error");
        } else if (run.status === "PARTIAL") {
          this.$store.app.notify(
            `Sincronización parcial: ${run.symbols_failed} símbolo(s) fallaron. ${detail}`,
            "warning",
            9000
          );
        } else if (written === 0) {
          // Un sync correcto que no escribió nada porque todo seguía fresco NO
          // es lo mismo que uno que falló, pero decir "0 cotizaciones · 0
          // barras" se lee exactamente igual de mal.
          this.$store.app.notify(
            `Los datos ya estaban al día (${run.symbols_requested} activos revisados)`,
            "success",
            4000
          );
        } else {
          this.$store.app.notify(`Datos actualizados: ${detail}`, "success", 5000);
        }

        await this.load();
        // Las vistas recargan: los precios que acaban de llegar cambian la
        // valoración, y dejar la pantalla con los anteriores sería incoherente.
        window.dispatchEvent(new CustomEvent("market-data-updated"));
      } catch (error) {
        this.$store.app.dismiss(notice);
        this.$store.app.notify(`No se pudo sincronizar: ${error.message}`, "error");
      } finally {
        this.syncing = false;
      }
    },
  }));
});

window.relativeTime = relativeTime;
