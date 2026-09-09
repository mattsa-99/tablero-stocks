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

function destroyChart(canvas) {
  const chart = registry.get(canvas);
  if (chart) {
    chart.destroy();
    registry.delete(canvas);
  }
}

window.charts = { renderDoughnut, destroyChart, readTokens, foldTail, MAX_SLICES };
