/* Configuración de Chart.js.
 *
 * Chart.js no lee CSS: los colores hay que pasárselos como valores resueltos.
 * `readTokens()` los extrae de las custom properties del tema activo, así que
 * la paleta vive en un solo sitio (app.css) y el cambio de tema repinta.
 */

const SERIES_SLOTS = [1, 2, 3, 4, 5, 6, 7, 8];

/* Máximo de segmentos antes de agrupar en «Otros».
 *
 * Un donut solo comunica parte-del-todo de un vistazo hasta ~6 porciones; más
 * allá las clases adyacentes se confunden y la respuesta correcta es una tabla.
 * Aquí el donut conserva la lectura de un vistazo y la leyenda -que sí lista
 * cada valor y porcentaje- hace de tabla. */
const MAX_SLICES = 6;

function readTokens() {
  const styles = getComputedStyle(document.documentElement);
  const token = (name) => styles.getPropertyValue(name).trim();
  return {
    surface: token("--surface-1"),
    surface2: token("--surface-2"),
    border: token("--border"),
    textPrimary: token("--text-primary"),
    textSecondary: token("--text-secondary"),
    textMuted: token("--text-muted"),
    series: SERIES_SLOTS.map((slot) => token(`--series-${slot}`)),
  };
}

/* Agrupa la cola en «Otros» conservando el orden por magnitud.
 *
 * El color sigue a la ENTIDAD por su posición en el ranking de tamaño, que es
 * estable mientras no cambien las posiciones: un filtro que reduzca la lista no
 * debe repintar a los supervivientes. */
function foldTail(items, maxSlices = MAX_SLICES) {
  const sorted = [...items].sort((a, b) => b.value - a.value);
  if (sorted.length <= maxSlices + 1) return sorted;

  const head = sorted.slice(0, maxSlices);
  const tail = sorted.slice(maxSlices);
  const total = tail.reduce((sum, item) => sum + item.value, 0);
  return [...head, { label: `Otros (${tail.length})`, value: total, isOther: true }];
}

/* Formateador del eje Y que se adapta al RANGO, no solo a la magnitud.
 *
 * `fmt.compact` usa un decimal, que basta para una cifra suelta pero no para
 * un eje: una cartera que se mueve entre 2,12 M y 2,23 M rotulaba «$2,2 M» en
 * los siete ticks, y el eje dejaba de informar justo en el caso normal -una
 * curva de valor casi siempre se mueve dentro de un rango estrecho-.
 *
 * Se calculan los dígitos significativos que hacen falta para que dos ticks
 * contiguos se distingan: cuanto más estrecho es el rango frente al valor
 * absoluto, más dígitos. Se acota a 6 para que un rango diminuto no produzca
 * un rótulo interminable.
 */
function compactAxisFormatter(values, currency) {
  const numbers = values.filter((v) => typeof v === "number" && Number.isFinite(v));
  const max = numbers.length ? Math.max(...numbers) : 0;
  const span = numbers.length ? max - Math.min(...numbers) : 0;

  let digits = 3;
  if (span > 0 && max > 0) {
    digits = Math.min(6, Math.max(2, Math.ceil(Math.log10(max / span)) + 2));
  }
  const formatter = new Intl.NumberFormat("es-CO", {
    style: "currency",
    currency,
    notation: "compact",
    maximumSignificantDigits: digits,
  });
  return (value) => formatter.format(value);
}

const registry = new Map();

/* Donut de distribución.
 *
 * Especificaciones aplicadas: separación de 2px en el color de la superficie
 * entre porciones (nunca un borde de otro color, que añadiría tinta que no es
 * dato), sin borde en hover y tooltip con valor y porcentaje.
 */
function renderDoughnut(canvas, items, { currency = "COP" } = {}) {
  if (!canvas) return null;

  // Si el CDN de Chart.js no cargó, el resto del dashboard debe seguir siendo
  // usable: la tabla y los KPIs no dependen del gráfico.
  if (typeof Chart === "undefined") {
    console.warn("Chart.js no está disponible: se omite el gráfico de distribución");
    return null;
  }

  const tokens = readTokens();
  const folded = foldTail(items);
  const total = folded.reduce((sum, item) => sum + item.value, 0);

  const colors = folded.map((item, index) =>
    item.isOther ? tokens.textMuted : tokens.series[index % tokens.series.length]
  );

  const existing = registry.get(canvas);
  if (existing) existing.destroy();

  const chart = new Chart(canvas, {
    type: "doughnut",
    data: {
      labels: folded.map((item) => item.label),
      datasets: [
        {
          data: folded.map((item) => item.value),
          backgroundColor: colors,
          borderColor: tokens.surface,
          borderWidth: 2,
          hoverBorderColor: tokens.surface,
          hoverBorderWidth: 2,
          hoverOffset: 6,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: "62%",
      animation: { duration: 450 },
      plugins: {
        // La leyenda propia de Chart.js se desactiva: se renderiza en HTML con
        // valor y porcentaje por fila, que es lo que permite comparar cifras
        // parecidas -algo que un donut no hace bien por sí solo-.
        legend: { display: false },
        tooltip: {
          backgroundColor: tokens.surface2,
          titleColor: tokens.textPrimary,
          bodyColor: tokens.textSecondary,
          borderColor: tokens.border,
          borderWidth: 1,
          padding: 10,
          cornerRadius: 6,
          displayColors: true,
          boxPadding: 4,
          callbacks: {
            label(context) {
              const value = context.parsed;
              const share = total > 0 ? (value / total) * 100 : 0;
              return ` ${window.fmt.money(value, currency)} · ${share.toFixed(1)}%`;
            },
          },
        },
      },
    },
  });

  registry.set(canvas, chart);
  return { chart, slices: folded, colors, total };
}

/* Curva de valor: la cartera contra lo aportado y contra el índice.
 *
 * Tres líneas y no una, porque una sola no responde a nada. «Vale 2,2 M» no
 * dice si eso es bueno; «vale 2,2 M habiendo puesto 2,1 M, y el índice habría
 * dado 2,09 M» sí.
 *
 * La línea de lo aportado va ESCALONADA (`stepped`) a propósito: el capital no
 * entra de forma continua sino de golpe el día del depósito, e interpolarla
 * dibujaría aportaciones que nunca ocurrieron.
 *
 * Sin puntos (`pointRadius: 0`) salvo al pasar por encima: con cientos de días
 * los marcadores se solapan hasta formar una banda sólida que tapa la línea.
 */
function renderValueSeries(canvas, points, { currency = "COP", benchmarkLabel = null } = {}) {
  if (!canvas) return null;
  if (typeof Chart === "undefined") {
    console.warn("Chart.js no está disponible: se omite la curva de valor");
    return null;
  }

  const tokens = readTokens();
  const existing = registry.get(canvas);
  if (existing) existing.destroy();

  const labels = points.map((p) => p.date);
  const value = points.map((p) => window.fmt.num(p.total_value));
  const invested = points.map((p) => window.fmt.num(p.net_invested));
  const benchmark = points.map((p) => window.fmt.num(p.benchmark_value));

  const datasets = [
    {
      label: "Mi cartera",
      data: value,
      borderColor: tokens.series[0],
      backgroundColor: "transparent",
      borderWidth: 2,
      pointRadius: 0,
      pointHoverRadius: 4,
      tension: 0.15,
      order: 1,
    },
    {
      label: "Aportado",
      data: invested,
      borderColor: tokens.textMuted,
      borderWidth: 1.5,
      borderDash: [4, 4],
      pointRadius: 0,
      pointHoverRadius: 4,
      stepped: true,
      order: 3,
    },
  ];

  if (benchmark.some((v) => v !== null)) {
    datasets.push({
      label: benchmarkLabel ? `Si fuera ${benchmarkLabel}` : "Índice",
      data: benchmark,
      borderColor: tokens.series[1],
      borderWidth: 1.5,
      pointRadius: 0,
      pointHoverRadius: 4,
      tension: 0.15,
      order: 2,
    });
  }

  // Se calcula con TODAS las series dibujadas: si el índice se separa mucho
  // de la cartera, el rango del eje es el de las dos juntas.
  const formatAxis = compactAxisFormatter(
    datasets.flatMap((d) => d.data),
    currency,
  );

  const chart = new Chart(canvas, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 400 },
      // El cruce por el eje X evita tener que acertar el punto exacto de una
      // línea con cientos de días comprimidos en unos cientos de píxeles.
      interaction: { mode: "index", intersect: false },
      scales: {
        x: {
          grid: { display: false },
          ticks: {
            color: tokens.textMuted,
            maxRotation: 0,
            autoSkip: true,
            maxTicksLimit: 8,
            callback(index) {
              return window.fmt.date(this.getLabelForValue(index));
            },
          },
          border: { color: tokens.border },
        },
        y: {
          // NO empieza en cero, y es deliberado. Con el eje forzado a cero, una
          // cartera que se mueve un 5% sobre un valor grande aparece como una
          // recta horizontal y el gráfico deja de informar. La referencia aquí
          // no es el cero sino la línea de lo aportado, que sí está dibujada.
          beginAtZero: false,
          grid: { color: tokens.border },
          border: { display: false },
          ticks: {
            color: tokens.textMuted,
            callback: formatAxis,
          },
        },
      },
      plugins: {
        legend: {
          display: true,
          position: "bottom",
          labels: {
            color: tokens.textSecondary,
            boxWidth: 12,
            boxHeight: 2,
            usePointStyle: false,
            padding: 16,
          },
        },
        tooltip: {
          backgroundColor: tokens.surface2,
          titleColor: tokens.textPrimary,
          bodyColor: tokens.textSecondary,
          borderColor: tokens.border,
          borderWidth: 1,
          padding: 10,
          cornerRadius: 6,
          callbacks: {
            title: (items) => window.fmt.date(items[0].label),
            label: (ctx) =>
              ` ${ctx.dataset.label}: ${window.fmt.money(ctx.parsed.y, currency)}`,
          },
        },
      },
    },
  });

  registry.set(canvas, chart);
  return chart;
}

function destroyChart(canvas) {
  const chart = registry.get(canvas);
  if (chart) {
    chart.destroy();
    registry.delete(canvas);
  }
}

window.charts = {
  renderDoughnut,
  renderValueSeries,
  destroyChart,
  readTokens,
  foldTail,
  MAX_SLICES,
};
