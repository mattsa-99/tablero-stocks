/* Estado global, cliente de API y formateadores.
 *
 * Se registra en `alpine:init`, antes de que Alpine arranque: un `Alpine.store`
 * definido después de la inicialización no existe para los componentes que ya
 * se montaron.
 */

// ---------------------------------------------------------------------------
// Cliente de API
// ---------------------------------------------------------------------------

class ApiError extends Error {
  constructor(message, status, type) {
    super(message);
    this.status = status;
    this.type = type;
  }
}

const api = {
  async request(path, options = {}) {
    let response;
    try {
      response = await fetch(path, {
        headers: { "Content-Type": "application/json" },
        ...options,
      });
    } catch (cause) {
      // Fallo de red: el servidor no respondió siquiera.
      throw new ApiError("No se pudo contactar con el servidor", 0, "NetworkError");
    }

    if (response.status === 204) return null;

    const body = await response.json().catch(() => null);

    if (!response.ok) {
      // El backend traduce los errores de dominio a {detail, type} en un único
      // punto, así que aquí siempre hay un mensaje útil que mostrar.
      // 422 de validación de FastAPI llega como una lista en `detail`.
      let detail = body?.detail ?? `Error ${response.status}`;
      if (Array.isArray(detail)) {
        detail = detail
          .map((item) => {
            // Pydantic antepone «Value error, » a los mensajes propios.
            const msg = String(item.msg).replace(/^Value error, /, "");
            const field = item.loc?.slice(1).join(".") ?? "";
            return field ? `${field}: ${msg}` : msg;
          })
          .join("; ");
      }
      throw new ApiError(detail, response.status, body?.type ?? "HTTPError");
    }

    return body;
  },

  get(path) {
    return this.request(path);
  },

  post(path, payload) {
    return this.request(path, { method: "POST", body: JSON.stringify(payload) });
  },

  patch(path, payload) {
    return this.request(path, { method: "PATCH", body: JSON.stringify(payload) });
  },

  put(path, payload) {
    return this.request(path, { method: "PUT", body: JSON.stringify(payload) });
  },

  delete(path) {
    return this.request(path, { method: "DELETE" });
  },
};

// ---------------------------------------------------------------------------
// Formateadores
// ---------------------------------------------------------------------------

/* Pydantic serializa Decimal como STRING en JSON, no como número.
 *
 * Es deliberado -conserva la exactitud que el backend se esfuerza en mantener-
 * pero significa que `position.market_value * 2` produce basura y
 * `a > b` compara lexicográficamente. Todo valor monetario cruza por aquí. */
function num(value) {
  if (value === null || value === undefined || value === "") return null;
  const parsed = typeof value === "number" ? value : Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : null;
}

const MONEY_FORMATTERS = new Map();

/* Decimales con que se IMPRIME una divisa. El COP va sin decimales: los
 * céntimos de peso son ruido visual. */
function decimalsFor(currency) {
  return currency === "COP" ? 0 : 2;
}

/* ¿Esta cifra se imprime como cero?
 *
 * Hace falta porque las cotizaciones de yfinance llegan con RUIDO DE COMA
 * FLOTANTE: IVV no cotiza a 764,92 sino a 764.919982910156. Quien registra
 * una compra al precio de hoy -que el formulario redondea a dos decimales-
 * acaba con un P&L no realizado de −0,0000170898, que es cero para cualquier
 * efecto práctico.
 *
 * Sin esta guarda la pantalla mostraba «−US$ 0,00» en ROJO: el signo y el
 * color afirmaban una pérdida inexistente sobre una cifra que se imprime
 * como cero. Es peor que un error de cálculo, porque parece uno: invita a
 * desconfiar de todas las demás cifras de la pantalla.
 *
 * Se resuelve en PRESENTACIÓN y no redondeando el dato: el backend mantiene
 * Decimal exacto a propósito, y recortar el precio de mercado rompería los
 * activos que cotizan por debajo del centavo.
 */
function roundsToZero(value, decimals) {
  const parsed = num(value);
  if (parsed === null) return false;
  return Math.abs(parsed) < 0.5 * 10 ** -decimals;
}

function moneyFormatter(currency) {
  if (!MONEY_FORMATTERS.has(currency)) {
    // El redondeo es SOLO de presentación; internamente todo sigue exacto.
    const decimals = decimalsFor(currency);
    MONEY_FORMATTERS.set(
      currency,
      new Intl.NumberFormat("es-CO", {
        style: "currency",
        currency,
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      })
    );
  }
  return MONEY_FORMATTERS.get(currency);
}

function fmtMoney(value, currency = "COP") {
  const parsed = num(value);
  if (parsed === null) return "—";
  return moneyFormatter(currency).format(parsed);
}

/* Cifra compacta para las tarjetas KPI: 23,6 M en vez de 23.627.310. */
function fmtCompact(value, currency = "COP") {
  const parsed = num(value);
  if (parsed === null) return "—";
  return new Intl.NumberFormat("es-CO", {
    style: "currency",
    currency,
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(parsed);
}

function fmtPct(value, digits = 2) {
  const parsed = num(value);
  if (parsed === null) return "—";
  return `${parsed.toFixed(digits)}%`;
}

/* El signo explícito es la codificación secundaria del color de estado: verde
 * y rojo nunca portan el significado solos. */
function fmtSigned(value, currency = "COP") {
  const parsed = num(value);
  if (parsed === null) return "—";
  // Cero a la precisión que se imprime: sin signo. Un «−US$ 0,00» afirma una
  // pérdida que la propia cifra desmiente.
  if (roundsToZero(parsed, decimalsFor(currency))) return fmtMoney(0, currency);
  const sign = parsed > 0 ? "+" : "−";
  return `${sign}${fmtMoney(Math.abs(parsed), currency)}`;
}

function fmtSignedPct(value, digits = 2) {
  const parsed = num(value);
  if (parsed === null) return "—";
  if (roundsToZero(parsed, digits)) return `${(0).toFixed(digits)}%`;
  const sign = parsed > 0 ? "+" : "−";
  return `${sign}${Math.abs(parsed).toFixed(digits)}%`;
}

/* Redondeo del precio para PRESENTARLO en un campo de formulario.
 *
 * Una cotización llega como 222.90048394742266 y ningún bróker admite eso: en
 * un input es ruido puro. Dos decimales por encima de 1, seis por debajo, para
 * no aplastar centavos ni cripto de precio bajo.
 *
 * Es redondeo de PRESENTACIÓN, no de almacenamiento: el usuario puede editarlo
 * y lo que se envía es lo que se ve, así que pantalla y dato no divergen.
 */
function priceForInput(value) {
  const parsed = num(value);
  if (parsed === null) return "";
  return String(parsed >= 1 ? Number(parsed.toFixed(2)) : Number(parsed.toPrecision(6)));
}


function fmtQty(value) {
  const parsed = num(value);
  if (parsed === null) return "—";
  return new Intl.NumberFormat("es-CO", { maximumFractionDigits: 4 }).format(parsed);
}

function fmtDateTime(value) {
  if (!value) return "—";
  // Almacenado en UTC, presentado en hora de Bogotá.
  return new Intl.DateTimeFormat("es-CO", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "America/Bogota",
  }).format(new Date(value));
}

function fmtDate(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("es-CO", {
    dateStyle: "medium",
    timeZone: "America/Bogota",
  }).format(new Date(value));
}

/* Clase de color para una cifra con signo. El color es redundante respecto al
 * signo que ya escribe fmtSigned: nunca es el único canal. */
function pnlClass(value, decimals = 2) {
  const parsed = num(value);
  // `roundsToZero` y no `=== 0`: el color tiene que coincidir con lo que se
  // imprime al lado. Con la comparación exacta, un −0,0000170898 salía rojo
  // junto a un «US$ 0,00» y la pantalla se contradecía a sí misma.
  if (parsed === null || roundsToZero(parsed, decimals)) {
    return "text-[var(--text-secondary)]";
  }
  return parsed > 0 ? "text-[var(--good)]" : "text-[var(--critical)]";
}

// ---------------------------------------------------------------------------
// Store global
// ---------------------------------------------------------------------------

document.addEventListener("alpine:init", () => {
  // `$fmt` disponible en cualquier expresión de plantilla. Sin esto habría que
  // pasar los formateadores por el x-data de cada componente.
  Alpine.magic("fmt", () => window.fmt);

  Alpine.store("app", {
    portfolios: [],
    selectedId: null,
    loadingPortfolios: true,
    portfolioError: null,
    toasts: [],
    // Contadores del diario, para el globo de la barra de navegación. Se
    // piden sin puntuar el universo: es barato y se pide en cada página.
    journalCounts: { active: 0, overdue: 0, breached: 0, with_alerts: 0 },
    theme: "dark",
    _nextToastId: 1,

    async init() {
      this.theme = localStorage.getItem("tablero:theme") || "dark";
      document.documentElement.setAttribute("data-theme", this.theme);
      await this.loadPortfolios();
      await this.refreshJournalCounts();
    },

    async refreshJournalCounts() {
      if (!this.selectedId) return;
      try {
        this.journalCounts = await api.get(`/api/journal/summary?portfolio_id=${this.selectedId}`);
      } catch {
        // El globo es un extra: que falle no puede romper la página.
      }
    },

    get selected() {
      return this.portfolios.find((p) => p.id === this.selectedId) ?? null;
    },

    get baseCurrency() {
      return this.selected?.base_currency ?? "COP";
    },

    async loadPortfolios() {
      this.loadingPortfolios = true;
      this.portfolioError = null;
      try {
        this.portfolios = await api.get("/api/portfolios");

        // Se restaura la última selección solo si ese portafolio sigue
        // existiendo: un id guardado que ya no existe dejaría la vista
        // pidiendo un 404 en bucle.
        const stored = Number.parseInt(localStorage.getItem("tablero:portfolio"), 10);
        const isValid = this.portfolios.some((p) => p.id === stored);
        this.selectedId = isValid ? stored : (this.portfolios[0]?.id ?? null);
        if (this.selectedId) {
          localStorage.setItem("tablero:portfolio", String(this.selectedId));
        }
      } catch (error) {
        this.portfolioError = error.message;
        this.notify(error.message, "error");
      } finally {
        this.loadingPortfolios = false;
      }
    },

    /* Solo fija el id. Las vistas reaccionan con `$watch` sobre esta
     * propiedad, NO con un evento.
     *
     * Con un evento habría una carrera: el componente se monta y llama a
     * load() antes de que `loadPortfolios()` -que es asíncrono- haya fijado el
     * id inicial, así que sale sin pedir nada; y como la selección inicial se
     * asigna directamente y no pasa por aquí, ningún evento la despierta
     * después. La vista se queda vacía para siempre y sin error en consola.
     * El watch cubre por igual la selección inicial y los cambios posteriores. */
    select(id) {
      const parsed = Number.parseInt(id, 10);
      if (parsed === this.selectedId) return;
      this.selectedId = parsed;
      localStorage.setItem("tablero:portfolio", String(parsed));
      this.refreshJournalCounts();
    },

    async createPortfolio(payload) {
      const created = await api.post("/api/portfolios", payload);
      await this.loadPortfolios();
      this.select(created.id);
      this.notify(`Portafolio «${created.name}» creado`, "success");
      return created;
    },

    /* Elimina un portafolio y reencamina la selección.
     *
     * El orden importa: primero se borra, luego se recarga la lista y por
     * último se elige otro. Si se cambiara la selección antes del DELETE, las
     * vistas dispararían un fetch contra el portafolio que está a punto de
     * desaparecer y verían un 404. */
    async deletePortfolio(id) {
      const deleted = this.portfolios.find((p) => p.id === id);
      await api.delete(`/api/portfolios/${id}`);

      // Si era el seleccionado, se limpia ANTES de recargar para que ninguna
      // vista intente pintar un id que ya no existe.
      if (this.selectedId === id) {
        this.selectedId = null;
        localStorage.removeItem("tablero:portfolio");
      }

      await this.loadPortfolios();
      this.notify(
        `Portafolio «${deleted?.name ?? id}» eliminado junto con su historial`,
        "success",
        6000
      );
    },

    toggleTheme() {
      this.theme = this.theme === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", this.theme);
      localStorage.setItem("tablero:theme", this.theme);
      // Chart.js no lee CSS: hay que repintar los gráficos con los tokens
      // nuevos o se quedan con los colores del tema anterior.
      window.dispatchEvent(new CustomEvent("theme-changed", { detail: this.theme }));
    },

    notify(message, kind = "info", timeout = 6000) {
      const id = this._nextToastId++;
      this.toasts.push({ id, message, kind });
      if (timeout) setTimeout(() => this.dismiss(id), timeout);
      return id;
    },

    dismiss(id) {
      this.toasts = this.toasts.filter((toast) => toast.id !== id);
    },
  });
});

window.api = api;
window.ApiError = ApiError;
window.fmt = {
  num,
  roundsToZero,
  decimalsFor,
  money: fmtMoney,
  compact: fmtCompact,
  pct: fmtPct,
  signed: fmtSigned,
  signedPct: fmtSignedPct,
  qty: fmtQty,
  priceForInput,
  dateTime: fmtDateTime,
  date: fmtDate,
  pnlClass,
};
