/* Vista de oportunidades: tarjetas con el desglose del score.
 *
 * El desglose no es decorativo: es el requisito de interpretabilidad. Las
 * contribuciones más el baseline reconstruyen el score exactamente, y la
 * tarjeta lo muestra para que se pueda verificar a ojo.
 */

/* Colores de los cuatro factores.
 *
 * Slots categóricos 1, 2, 3 y 7 (azul, naranja, aqua, violeta). Esa
 * combinación se validó con el script: peor par adyacente ΔE 9.4 bajo
 * deuteranopia, por encima del objetivo de 8. La combinación "obvia"
 * -rojo para riesgo- caía a ΔE 6.5 contra el aqua bajo protanopia, dentro de
 * la banda que solo es legal con codificación secundaria. Cada barra lleva
 * además su etiqueta de texto, así que el color nunca es el único canal.
 */
const FACTOR_META = [
  { key: "value", label: "Valoración", color: "var(--series-1)" },
  { key: "momentum", label: "Momentum", color: "var(--series-2)" },
  { key: "diversification", label: "Diversificación", color: "var(--series-3)" },
  { key: "risk", label: "Riesgo", color: "var(--series-7)" },
];

const CONFIDENCE_LABEL = { high: "alta", medium: "media", low: "baja" };

/* Escala de calificación ABSOLUTA.
 *
 * Deliberadamente NO es un semáforo rojo-amarillo-verde sobre el score: el
 * score es ordinal y pintarlo así afirmaría un juicio absoluto que no hace.
 * El color va aquí, en la calificación, que sí es absoluta.
 *
 * Se usan los colores de estado (verde/ámbar/rojo), que van SIEMPRE con
 * etiqueta de texto al lado: el tono nunca porta el significado solo.
 */
const GRADE_STYLE = {
  A: { text: "text-good",     ring: "border-good/50",    bg: "bg-good/10" },
  B: { text: "text-good",     ring: "border-good/30",    bg: "bg-good/5" },
  C: { text: "text-ink-soft", ring: "border-line",       bg: "bg-surf-2" },
  D: { text: "text-warn",     ring: "border-warn/40",    bg: "bg-warn/10" },
  E: { text: "text-bad",      ring: "border-bad/50",     bg: "bg-bad/10" },
  SIN_CALIFICAR: { text: "text-ink-mute", ring: "border-line border-dashed", bg: "" },
};

const GRADE_ORDER = ["A", "B", "C", "D", "E", "SIN_CALIFICAR"];
const GRADE_LABEL = {
  A: "Muy buena", B: "Buena", C: "Normal", D: "Mala", E: "Muy mala",
  SIN_CALIFICAR: "Sin calificar",
};

/* Nombre público de cada calificación, el que viaja en la URL.
 * Debe coincidir con `grading.GRADE_SLUG` del backend. */
const GRADE_SLUG = {
  A: "muy_buena", B: "buena", C: "normal", D: "mala", E: "muy_mala",
  SIN_CALIFICAR: "sin_calificar",
};

/* Regiones de mercado. El orden es el de `regions.REGION_ORDER`: primero los
 * mercados en los que se opera de forma directa. */
const REGION_ORDER = ["US", "COL", "LATAM", "EU", "ASIA", "GLOBAL"];
const REGION_LABEL = {
  US: "EE.UU.", COL: "Colombia", LATAM: "LatAm", EU: "Europa",
  ASIA: "Asia", GLOBAL: "Global/ETFs",
};

/* Estado visual de un chip filtro.
 *
 * Se usan los tokens semánticos del tema (good/warn/bad/line/ink) y no colores
 * crudos de la paleta de Tailwind: los mismos tonos ya visten la calificación
 * DENTRO de cada tarjeta, y dos fuentes de color para el mismo concepto se
 * separarían en cuanto alguien tocara una. Además los tokens cambian solos
 * entre tema claro y oscuro, cosa que un `neutral-700` fijo no hace.
 *
 * Inactivo lleva borde tenue y fondo transparente; activo enciende el color de
 * la categoría y añade un anillo, para que el estado no dependa SOLO del tono. */
const CHIP_INACTIVE = "border-line bg-transparent text-ink-mute hover:border-line-strong hover:text-ink-soft";

/* Cada cuánto se vuelve a preguntar mientras el refresco corre por detrás, y
 * cuántas veces como mucho. 3 s x 40 = dos minutos, que cubre de sobra un
 * refresco de cotizaciones del universo completo: medido el 19-09-2026, los
 * 494 símbolos tardan 40 s en una descarga agrupada. */
const POLL_INTERVAL_MS = 3000;
const MAX_POLLS = 40;

/* Resume los avisos repetitivos en una sola línea.
 *
 * El backend manda un aviso POR SÍMBOLO sin cotización, y la vista los
 * convertía en un toast cada uno. En una sincronización con Yahoo limitando
 * el ritmo eso fueron 84 notificaciones apiladas que tapaban la pantalla
 * entera: el usuario no puede hacer nada con 84 nombres de ticker, y el
 * único aviso accionable -si lo hubiera- quedaba sepultado entre ellos.
 *
 * Se agrupan por su texto sin el símbolo, conservando los primeros nombres
 * para que el aviso siga siendo concreto. */
function summarizeWarnings(warnings) {
  const groups = new Map();
  const singles = [];

  for (const warning of warnings) {
    // `\S+` y no `\w+`: \w es [A-Za-z0-9_] y dejaría fuera «cotización».
    const match = /^(Sin \S+) para (.+)$/.exec(warning);
    if (!match) {
      singles.push(warning);
      continue;
    }
    const [, prefix, symbol] = match;
    if (!groups.has(prefix)) groups.set(prefix, []);
    groups.get(prefix).push(symbol);
  }

  const summarized = [];
  for (const [prefix, symbols] of groups) {
    if (symbols.length === 1) {
      summarized.push(`${prefix} para ${symbols[0]}`);
      continue;
    }
    const shown = symbols.slice(0, 3).join(", ");
    const rest = symbols.length - 3;
    summarized.push(
      rest > 0
        ? `${prefix} para ${symbols.length} activos (${shown} y ${rest} más)`
        : `${prefix} para ${symbols.length} activos (${shown})`,
    );
  }
  return [...singles, ...summarized];
}

document.addEventListener("alpine:init", () => {
  Alpine.data("opportunitiesView", () => ({
    loading: true,
    error: null,
    // `insufficient` es un estado propio, no un error genérico: es la
    // respuesta ESPERADA en una instalación nueva, y la acción correcta no es
    // reintentar sino añadir candidatos.
    insufficient: null,
    data: null,

    symbolsInput: "",
    limit: 10,
    expanded: {},

    /* Filtros activos. Vacío = sin filtrar, que NO es lo mismo que "todas
     * seleccionadas": si el usuario deselecciona el último chip queremos ver
     * el universo completo, no una lista vacía. */
    activeTiers: [],
    activeRegions: [],

    /* Firma de los avisos ya mostrados.
     *
     * Con filtros, `load()` se ejecuta en cada clic y los avisos del backend
     * suelen ser los mismos (por ejemplo, "la cartera no tiene posiciones
     * valoradas"). Sin esta guarda se apilaban seis toasts idénticos que
     * llegaban a tapar el botón de limpiar filtros. Solo se avisa de lo que
     * NO se había avisado ya. */
    notifiedWarnings: "",

    /* Hay un refresco de precios corriendo por detrás en el servidor.
     *
     * La vista NO espera a la red: pinta el ranking con lo que hay guardado
     * -0,23 s- y el servidor refresca los precios después de responder. Este
     * campo mantiene visible que eso está pasando y gobierna el sondeo que
     * traerá los precios nuevos cuando terminen. */
    refreshing: false,
    pollTimer: null,
    pollsLeft: 0,

    factors: FACTOR_META,
    regionOrder: REGION_ORDER,

    get results() {
      return this.data?.opportunities ?? [];
    },

    get hasResults() {
      return !this.loading && !this.error && !this.insufficient && this.results.length > 0;
    },

    init() {
      this.symbolsInput = localStorage.getItem("tablero:symbols") ?? "";
      this.activeTiers = this.readStoredList("tablero:tiers");
      this.activeRegions = this.readStoredList("tablero:regions");
      // Ver la nota en store.select(): el watch cubre la selección inicial
      // asíncrona además de los cambios del selector.
      this.$watch("$store.app.selectedId", (id) => {
        if (id) this.load();
      });
      if (this.$store.app.selectedId) this.load();
    },

    /* `silent` repinta sin vaciar la vista; `triggerRefresh` decide si esta
     * llamada además pide al servidor que refresque precios por detrás.
     *
     * Separarlos importa por dos motivos. Un sondeo que pusiera `loading`
     * haría parpadear la cuadrícula entera cada pocos segundos, y uno que
     * volviera a pedir refresco se encadenaría consigo mismo para siempre.
     * Y filtrar es una operación de VISTA -el backend puntúa el universo
     * completo igual-, así que un clic en un chip no debe salir a la red. */
    async load({ silent = false, triggerRefresh = true } = {}) {
      const id = this.$store.app.selectedId;
      if (!id) {
        this.loading = false;
        return;
      }

      if (!silent) this.loading = true;
      this.error = null;
      this.insufficient = null;

      const params = new URLSearchParams({
        portfolio_id: id,
        limit: this.limit,
        refresh: triggerRefresh ? "true" : "false",
      });
      const symbols = this.symbolsInput
        .split(",")
        .map((s) => s.trim().toUpperCase())
        .filter(Boolean);
      if (symbols.length) params.set("symbols", symbols.join(","));

      // Los filtros son de VISTA: el backend sigue puntuando el universo
      // entero y solo recorta qué filas devuelve. Por eso se pueden cambiar
      // sin que el score de nadie se mueva.
      if (this.activeTiers.length) {
        params.set("quality_tiers", this.activeTiers.map((g) => GRADE_SLUG[g]).join(","));
      }
      if (this.activeRegions.length) params.set("regions", this.activeRegions.join(","));

      try {
        this.data = await window.api.get(`/api/opportunities?${params}`);
        localStorage.setItem("tablero:symbols", this.symbolsInput);

        this.refreshing = this.data.refreshing === true;
        if (triggerRefresh) this.pollsLeft = MAX_POLLS;
        this.schedulePoll();

        const warnings = summarizeWarnings(this.data.warnings ?? []);
        const signature = warnings.join("|");
        if (signature !== this.notifiedWarnings) {
          for (const warning of warnings) {
            this.$store.app.notify(warning, "warning", 8000);
          }
          this.notifiedWarnings = signature;
        }
      } catch (error) {
        this.data = null;
        if (error.status === 422) {
          this.insufficient = error.message;
        } else {
          this.error = error.message;
          this.$store.app.notify(`Oportunidades: ${error.message}`, "error");
        }
      } finally {
        this.loading = false;
      }
    },

    /* Vuelve a preguntar mientras el servidor siga refrescando.
     *
     * Con tope: si el refresco se atasca -Yahoo limitando, la red caída-,
     * sondear indefinidamente convierte una vista abierta en un goteo de
     * peticiones que nadie está mirando. Al agotarse se deja de sondear y
     * lo mostrado sigue siendo válido: son los últimos datos guardados,
     * que es exactamente lo que se pintó desde el principio. */
    schedulePoll() {
      clearTimeout(this.pollTimer);
      if (!this.refreshing || this.pollsLeft <= 0) {
        this.refreshing = false;
        return;
      }
      this.pollsLeft -= 1;
      this.pollTimer = setTimeout(
        () => this.load({ silent: true, triggerRefresh: false }),
        POLL_INTERVAL_MS,
      );
    },

    destroy() {
      clearTimeout(this.pollTimer);
    },

    readStoredList(key) {
      try {
        const raw = JSON.parse(localStorage.getItem(key) ?? "[]");
        return Array.isArray(raw) ? raw : [];
      } catch {
        // Un localStorage corrupto no puede impedir que la vista cargue.
        return [];
      }
    },

    /* Alterna un filtro y recarga.
     *
     * Recarga contra el servidor en lugar de filtrar en el cliente porque el
     * cliente solo tiene las `limit` filas mostradas: filtrar sobre 10 de 491
     * daría casi siempre una lista vacía y parecería que no hay nada. */
    toggleTier(grade) {
      this.activeTiers = this.activeTiers.includes(grade)
        ? this.activeTiers.filter((g) => g !== grade)
        : [...this.activeTiers, grade];
      localStorage.setItem("tablero:tiers", JSON.stringify(this.activeTiers));
      this.load({ triggerRefresh: false });
    },

    toggleRegion(region) {
      this.activeRegions = this.activeRegions.includes(region)
        ? this.activeRegions.filter((r) => r !== region)
        : [...this.activeRegions, region];
      localStorage.setItem("tablero:regions", JSON.stringify(this.activeRegions));
      this.load({ triggerRefresh: false });
    },

    get hasFilters() {
      return this.activeTiers.length > 0 || this.activeRegions.length > 0;
    },

    clearFilters() {
      this.activeTiers = [];
      this.activeRegions = [];
      localStorage.removeItem("tablero:tiers");
      localStorage.removeItem("tablero:regions");
      this.load({ triggerRefresh: false });
    },

    /* Clases de un chip de calificación según esté activo o no. */
    tierChipClass(grade) {
      if (!this.activeTiers.includes(grade)) return CHIP_INACTIVE;
      const style = this.gradeStyle(grade);
      return `${style.ring} ${style.bg} ${style.text} ring-1 ring-inset ring-current/30`;
    },

    regionChipClass(region) {
      return this.activeRegions.includes(region)
        ? "border-s1 bg-s1/10 text-s1 ring-1 ring-inset ring-s1/30"
        : CHIP_INACTIVE;
    },

    /* Regiones con su recuento, para los chips.
     *
     * Se recorren en orden fijo y NO se ocultan las de recuento cero: un chip
     * que desaparece al filtrar deja al usuario sin forma de volver atrás. */
    get regionSummary() {
      const counts = this.data?.region_counts ?? {};
      return REGION_ORDER.map((r) => ({
        region: r,
        label: REGION_LABEL[r],
        count: counts[r] ?? 0,
      }));
    },

    /* "Universo: 128 de 494 candidatos" -> lo que el filtro deja ver frente a
     * lo que de verdad se ha evaluado. Sin el segundo número, filtrar parecería
     * haber encogido el universo, y el score seguiría siendo el de los 494. */
    /* Cuántas tarjetas hay realmente en pantalla. Distinto de `matchedCount`:
     * el límite recorta la lista DESPUÉS de filtrar. */
    get visibleCount() {
      return this.results.length;
    },

    /* True cuando el límite esconde filas que sí pasan el filtro. Es lo que
     * justifica sugerir subir «Mostrar» en vez de dejar creer que no hay más. */
    get isTruncated() {
      return this.matchedCount > this.visibleCount;
    },

    get matchedCount() {
      return this.data?.matched_size ?? this.data?.universe_size ?? 0;
    },

    regionLabel(region) {
      return REGION_LABEL[region] ?? region;
    },

    /* Métricas observadas no nulas de un factor.
     *
     * Se filtra AQUÍ y no con un `x-if` dentro del `x-for` de la plantilla:
     * Alpine no inicializa un `<template x-for>` anidado dentro de otro
     * template, y la sección se renderizaría vacía sin ningún error visible. */
    presentInputs(row, key) {
      return Object.entries(row[key].inputs).filter(([, value]) => value !== null);
    },

    hasInputs(row, key) {
      return this.presentInputs(row, key).length > 0;
    },

    get skeletonCount() {
      return this.loading ? 6 : 0;
    },

    gradeStyle(grade) {
      return GRADE_STYLE[grade] ?? GRADE_STYLE.SIN_CALIFICAR;
    },

    /* Resumen del universo para la cabecera: cuántos hay de cada calificación.
     * Responde "¿hay algo bueno aquí?" antes de mirar el ranking. */
    get gradeSummary() {
      const counts = this.data?.grade_counts ?? {};
      return GRADE_ORDER.filter((g) => counts[g]).map((g) => ({
        grade: g,
        label: GRADE_LABEL[g],
        count: counts[g],
        style: GRADE_STYLE[g],
      }));
    },

    get qualityWarning() {
      return this.data?.universe_quality_warning ?? null;
    },

    /* Señales con dato, para el desglose de la calificación. */
    ratedSignals(row) {
      return row.assessment.signals.filter((s) => s.points !== null);
    },

    unratedSignals(row) {
      return row.assessment.signals.filter((s) => s.points === null);
    },

    signalSign(points) {
      if (points > 0) return `+${points}`;
      return String(points);
    },

    signalClass(points) {
      if (points > 0) return "text-good";
      if (points < 0) return "text-bad";
      return "text-ink-mute";
    },

    toggle(symbol) {
      this.expanded = { ...this.expanded, [symbol]: !this.expanded[symbol] };
    },

    /* Ancho de la barra: el SCORE del factor (0-100), no su contribución.
     * La contribución ya depende del peso y compararlas entre factores sería
     * comparar cosas distintas; el score sí es una escala común. */
    barWidth(row, key) {
      return `${Math.max(0, Math.min(100, row[key].score))}%`;
    },

    contribution(row, key) {
      const points = row[key].contribution;
      const sign = points > 0 ? "+" : points < 0 ? "−" : "";
      return `${sign}${Math.abs(points).toFixed(1)}`;
    },

    /* Suma de contribuciones + baseline. Debe coincidir con `score`; se muestra
     * para que la afirmación sea verificable y no un acto de fe. */
    reconstructed(row) {
      const total =
        FACTOR_META.reduce((sum, factor) => sum + row[factor.key].contribution, 0) +
        row.baseline;
      return total.toFixed(2);
    },

    confidenceLabel(value) {
      return CONFIDENCE_LABEL[value] ?? value;
    },

    /* Anillo del score: se usa la rampa secuencial de un solo tono (azul,
     * más oscuro = más alto), NO un semáforo rojo-amarillo-verde. Un score de
     * 45 no es "malo": es un puesto en un ranking relativo, y pintarlo de rojo
     * afirmaría un juicio absoluto que el motor explícitamente no hace. */
    scoreColor(score) {
      if (score >= 75) return "var(--series-1)";
      if (score >= 55) return "color-mix(in srgb, var(--series-1) 75%, var(--surface-3))";
      if (score >= 35) return "color-mix(in srgb, var(--series-1) 50%, var(--surface-3))";
      return "color-mix(in srgb, var(--series-1) 30%, var(--surface-3))";
    },
  }));
});
