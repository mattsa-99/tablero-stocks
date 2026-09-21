/* Formateo del cero. Se ejecuta con node desde `test_frontend_formatters.py`.
 *
 * Vive en JavaScript y no portado a Python porque lo que se comprueba es el
 * comportamiento REAL de `store.js`, incluido lo que hace `Intl`: una copia de
 * la lógica en Python probaría la copia, no lo que ve el usuario.
 */
import { readFileSync } from "node:fs";
import assert from "node:assert";

// store.js se escribió para el navegador: se ejecuta con un `document` y un
// `window` mínimos para probar los formateadores de forma aislada.
const code = readFileSync("app/static/js/store.js", "utf8");
const scope = { document: { addEventListener() {} }, window: {} };
new Function("document", "window", code)(scope.document, scope.window);
const fmt = scope.window.fmt;

const NEUTRO = "text-[var(--text-secondary)]";
const MALO = "text-[var(--critical)]";
const BUENO = "text-[var(--good)]";

/* EL caso que se vio en pantalla.
 *
 * IVV cotiza a 764.919982910156, no a 764,92. Una compra registrada hoy al
 * precio de hoy -que el formulario redondea a dos decimales- deja un P&L de
 * −0,0000170898, y se mostraba como «−US$ 0,00» EN ROJO: el signo y el color
 * afirmaban una pérdida que la propia cifra desmiente. */
const RUIDO = -0.0000170898438;
assert.strictEqual(fmt.signed(RUIDO, "USD"), fmt.money(0, "USD"),
  "un cero impreso no puede llevar signo");
assert.strictEqual(fmt.pnlClass(RUIDO), NEUTRO,
  "un cero impreso no puede pintarse de rojo");
assert.strictEqual(fmt.signedPct(-0.0000022, 2), "0.00%",
  "tampoco en porcentaje");

// Una pérdida DE VERDAD sigue siendo roja y con signo.
assert.match(fmt.signed(-12.34, "USD"), /^−/);
assert.strictEqual(fmt.pnlClass(-12.34), MALO);
assert.strictEqual(fmt.pnlClass(12.34), BUENO);

// El umbral respeta la precisión que se imprime: un centavo SÍ se ve.
assert.match(fmt.signed(-0.01, "USD"), /^−/, "un centavo es visible, no ruido");
assert.strictEqual(fmt.pnlClass(-0.01), MALO);

// En pesos se imprimen 0 decimales, así que el umbral es medio peso: por eso
// `pnlClass` recibe los decimales y no los da por supuestos.
assert.strictEqual(fmt.pnlClass(-0.4, fmt.decimalsFor("COP")), NEUTRO);
assert.strictEqual(fmt.pnlClass(-0.4, fmt.decimalsFor("USD")), MALO);

// Ausente sigue siendo ausente, que no es lo mismo que cero.
assert.strictEqual(fmt.signed(null, "USD"), "—");
assert.strictEqual(fmt.pnlClass(null), NEUTRO);

console.log("formateo del cero: 12 comprobaciones OK");
