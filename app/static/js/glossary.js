/* Centro de ayuda: glosario de métricas con un ejemplo práctico en cada una.
 *
 * POR QUÉ EL EJEMPLO ES OBLIGATORIO Y NO UN ADORNO
 * ================================================
 * Casi todas estas métricas se pueden "entender" leyendo la definición y
 * seguir sin saber qué hacer con el número que se tiene delante. El ejemplo
 * es lo que convierte «desviación estándar anualizada» en «33,5% son
 * oscilaciones moderadas/altas».
 *
 * LOS NÚMEROS SON REALES, NO INVENTADOS
 * =====================================
 * Todos los ejemplos salen de datos verificados contra la propia aplicación:
 * la cartera «Prueba» (TSLA + AAPL en COP) y la ficha de CIB del ranking. Un
 * ejemplo con cifras inventadas que no cuadran con lo que el usuario ve en
 * pantalla enseña a desconfiar de la pantalla.
 */

/* Ejemplo asociado a una métrica.
 *
 * `caveat` es para las métricas cuyo dato puede venir MAL del proveedor. No
 * es pesimismo: sin ese aviso el glosario enseñaría a leer un número roto
 * como si fuera una oportunidad (ver `price_to_book`).
 */
const GLOSSARY = {
  portfolio: {
    label: "Portafolio",
    intro:
      "Los ejemplos usan la cartera de prueba: 1 acción de TSLA y 1 de AAPL, " +
      "valoradas en pesos.",
    groups: [
      {
        title: "Valor y coste",
        entries: [
          {
            term: "Valor de mercado",
            definition:
              "Lo que valen hoy tus posiciones, al último precio de cotización " +
              "y convertido a la divisa de la cartera.",
            example:
              "1 TSLA a $1.184.497 + 1 AAPL a $1.019.997 = <b>$2.204.494 COP</b>.",
          },
          {
            term: "Coste total",
            definition:
              "El dinero que realmente saliste a poner para tener las posiciones " +
              "abiertas, comisiones incluidas. No cambia con el precio de mercado.",
            example:
              "TSLA te costó $1.116.589 y AAPL $1.012.490: <b>$2.129.079 COP</b>.",
          },
          {
            term: "Coste medio",
            definition:
              "Precio promedio ponderado que pagaste por cada acción de una " +
              "empresa. Vender NO lo cambia: si compras dos y vendes una, el " +
              "coste medio de la que queda sigue siendo el mismo.",
            example:
              "Compras 1 AAPL a $100 y otra a $120: tu coste medio es <b>$110</b> " +
              "por acción. Si vendes una, la restante sigue con coste medio $110.",
            caveat:
              "Sirve para medir tu rendimiento. NO es una declaración fiscal: " +
              "coincide con el costo promedio del Estatuto Tributario colombiano " +
              "para acciones, pero eso lo confirma un contador.",
          },
          {
            term: "Peso (%)",
            definition:
              "Cuánto pesa cada activo dentro del valor total de la cartera. " +
              "Es la medida de concentración: un peso alto es mucho depender de " +
              "una sola empresa.",
            example:
              "TSLA vale $1.184.497 de un total de $2.204.494 → <b>53,7%</b>. " +
              "Más de la mitad de la cartera depende de una sola acción.",
          },
        ],
      },
      {
        title: "Resultado",
        entries: [
          {
            term: "P&L total y retorno (%)",
            definition:
              "Ganancia o pérdida acumulada frente a lo que invertiste. El " +
              "retorno es esa misma cifra en porcentaje sobre el coste.",
            example:
              "$2.204.494 de valor − $2.129.079 de coste = <b>+$75.415 COP</b>, " +
              "que sobre el coste es un <b>+3,54%</b>.",
          },
          {
            term: "P&L no realizado",
            definition:
              "La ganancia o pérdida «en papel» de lo que todavía tienes. Sube " +
              "y baja cada día con el precio, y no se convierte en dinero hasta " +
              "que vendes.",
            example:
              "Tu TSLA vale hoy <b>+$67.908 COP</b> más de lo que te costó " +
              "(<b>+6,08%</b>). Sigue siendo tuya, así que no lo has cobrado.",
          },
          {
            term: "P&L realizado",
            definition:
              "Lo que ya consolidaste al vender. A diferencia del anterior, este " +
              "número no vuelve a moverse: la operación está cerrada.",
            example:
              "Compraste a $100 y vendiste a $120 → <b>+$20</b> realizados. Si " +
              "nunca has vendido nada, es <b>$0</b>.",
          },
          {
            term: "Dividendos",
            definition:
              "Efectivo que las empresas reparten y entra en tu cuenta. Es " +
              "rendimiento que cobras sin vender nada.",
            example:
              "AAPL paga $0,25 por acción al trimestre y tienes 10 acciones → " +
              "<b>$2,50 USD</b> ese trimestre.",
          },
        ],
      },
      {
        title: "Efectivo",
        entries: [
          {
            term: "Caja",
            definition:
              "Saldo en efectivo disponible para comprar. Sube con depósitos y " +
              "ventas, baja con compras y retiros.",
            example:
              "Aparece en <b>−$2.129.079 COP</b> si registraste las compras sin " +
              "haber registrado antes el depósito del que salió el dinero.",
            caveat:
              "Que pueda quedar negativa es deliberado: permite cargar un " +
              "histórico ya existente sin inventar depósitos que nunca ocurrieron.",
          },
        ],
      },
    ],
  },

  opportunities: {
    label: "Oportunidades",
    intro:
      "Los ejemplos usan la ficha real de CIB (Bancolombia), primera del " +
      "ranking con 86,3 puntos.",
    groups: [
      {
        title: "La nota global",
        entries: [
          {
            term: "Opportunity Score (0-100)",
            definition:
              "Combina cuatro pilares en una sola cifra. Es un <b>ranking " +
              "RELATIVO</b>: mide cómo se compara un activo con los otros del " +
              "universo evaluado, no si es bueno en términos absolutos. Siempre " +
              "hay un primero, aunque todos sean malos.",
            example:
              "CIB saca <b>86,3</b> = 20 de base + 33,0 de valoración + 28,9 de " +
              "momentum + 15,0 de diversificación − 10,6 de riesgo.",
            caveat:
              "No es una recomendación de inversión. Los pesos de la fórmula son " +
              "un juicio de diseño, sin backtest que los respalde.",
          },
          {
            term: "Calificación A-E (Muy buena → Muy mala)",
            definition:
              "La respuesta a «¿esto es bueno?», que es distinta de «¿qué puesto " +
              "ocupa?». No mira a los demás candidatos: compara contra el P/E del " +
              "mercado, contra el signo de la tendencia y contra umbrales fijos " +
              "de riesgo y calidad contable.",
            example:
              "ETB.CL sale <b>#2 del ranking con 84,6 puntos</b> y sin embargo " +
              "está calificada <b>«Mala»</b>: encabeza la lista sin ser buena.",
            caveat:
              "Con menos de 2 de 4 señales con datos aparece «Sin calificar», " +
              "nunca «Normal»: un neutro por defecto afirmaría algo sin base.",
          },
        ],
      },
      {
        title: "Los cuatro pilares",
        entries: [
          {
            term: "Valoración (35% del peso)",
            definition:
              "Si el activo está barato o caro frente al resto del universo, " +
              "según sus ratios de precio contra beneficios, valor contable y " +
              "flujo operativo.",
            example:
              "CIB suma <b>+33,0 pts</b>: con un P/E de 9,5 está entre lo más " +
              "barato de los 491 candidatos.",
          },
          {
            term: "Momentum (30%)",
            definition:
              "Si el precio viene subiendo de forma sostenida. Mide tendencia " +
              "reciente, no valor: una acción cara puede tener buen momentum.",
            example: "CIB suma <b>+28,9 pts</b>: casi duplicó su precio en un año.",
          },
          {
            term: "Diversificación (15%)",
            definition:
              "Cuánto te aportaría a TI en concreto. Premia lo que no tienes: si " +
              "el activo pertenece a un sector ausente de tu cartera, puntúa al " +
              "máximo. Es el único pilar que depende de tus posiciones.",
            example:
              "CIB suma <b>+15,0 pts</b>, el máximo, porque no tienes nada del " +
              "sector financiero.",
          },
          {
            term: "Riesgo (−20%, penaliza)",
            definition:
              "Resta, no suma. Combina cuánto oscila el precio y cuál ha sido su " +
              "peor caída. Dos activos con el mismo retorno no son equivalentes " +
              "si uno llegó ahí con el triple de sobresaltos.",
            example:
              "CIB resta <b>−10,6 pts</b> por una volatilidad del 33,5% y una " +
              "caída máxima del 23,9%.",
          },
        ],
      },
      {
        title: "Métricas observadas",
        entries: [
          {
            term: "trailing_pe / forward_pe",
            definition:
              "Cuántas veces el beneficio anual estás pagando por la acción. El " +
              "<i>trailing</i> usa el beneficio de los últimos 12 meses; el " +
              "<i>forward</i>, el que los analistas esperan. Más bajo = más barato.",
            example:
              "CIB: <b>9,54</b> y <b>8,28</b>. Pagas $9,54 por cada $1 de " +
              "beneficio pasado, y que el forward sea menor implica que se espera " +
              "que gane más el año que viene.",
            caveat:
              "Un P/E negativo o cero NO es «barato»: significa que la empresa " +
              "pierde dinero. El motor lo descarta en vez de tratarlo como ganga.",
          },
          {
            term: "price_to_book",
            definition:
              "Precio frente al valor contable de la empresa. Por debajo de 1 " +
              "sugiere que cotiza por menos de lo que valen sus activos netos, " +
              "algo típico en bancos y en negocios en dificultades.",
            example:
              "Un banco sano suele estar entre <b>0,8 y 2,0</b>. BAC cotiza a " +
              "1,57 y JPM a 2,68.",
            caveat:
              "CIB muestra <b>0,0022</b>, y eso NO significa que esté 450 veces " +
              "barato: es un fallo del proveedor, que divide el precio del ADR en " +
              "dólares entre un valor contable en pesos. Le pasa también a BRK-B " +
              "(precio de la clase B contra valor contable de la clase A). Si ves " +
              "un P/B por debajo de 0,05, sospecha del dato antes que del mercado.",
          },
          {
            term: "ev_to_ebitda",
            definition:
              "Valor de la empresa (incluida su deuda) frente a su beneficio " +
              "operativo. Complementa al P/E porque no se deja engañar por la " +
              "estructura de deuda ni por la contabilidad de impuestos.",
            example:
              "En CIB aparece <b>vacío</b>: en un banco no es una medida " +
              "significativa, así que el motor lo trata como dato ausente en vez " +
              "de inventarlo.",
          },
          {
            term: "sma50_over_sma200_minus_1",
            definition:
              "Distancia entre la media móvil de 50 días y la de 200. Positivo " +
              "significa que el precio reciente va por encima del de largo plazo: " +
              "tendencia alcista.",
            example:
              "CIB: <b>0,201 (+20,1%)</b>. La media corta está un 20% por encima " +
              "de la larga, señal de tendencia alcista firme.",
          },
          {
            term: "momentum_12_1",
            definition:
              "Rendimiento de los últimos 12 meses <b>excluyendo el último mes</b>. " +
              "Ese mes se quita a propósito: a corto plazo los precios tienden a " +
              "rebotar en contra, y meterlo ensuciaría la señal.",
            example:
              "CIB: <b>0,922 (+92,2%)</b>. Casi duplicó su precio entre hace un " +
              "año y hace un mes.",
          },
          {
            term: "sector_weight",
            definition:
              "Cuánto pesa ya en TU cartera el sector al que pertenece el " +
              "candidato. Cuanto más alto, menos te aporta comprar más de lo mismo.",
            example:
              "CIB: <b>0,000 (0%)</b>. No tienes nada del sector financiero, así " +
              "que su puntuación de diversificación es la máxima.",
          },
          {
            term: "annualized_volatility",
            definition:
              "Cuánto oscila el precio a lo largo de un año. No mide si sube o " +
              "baja, sino con cuánta brusquedad se mueve.",
            example:
              "CIB: <b>0,335 (33,5%)</b>, oscilaciones moderadas-altas. Un ETF " +
              "amplio suele rondar el 15%.",
          },
          {
            term: "max_drawdown",
            definition:
              "La peor caída desde un máximo hasta el mínimo posterior. Responde " +
              "a «¿cuánto habría llegado a perder en el peor momento?».",
            example:
              "CIB: <b>0,239 (23,9%)</b>. Quien compró en el pico llegó a ver su " +
              "posición un 23,9% abajo antes de recuperarse.",
          },
          {
            term: "beta",
            definition:
              "Cuánto se mueve el activo cuando se mueve el mercado. 1,0 es ir a " +
              "la par con el S&P 500; por debajo, más tranquilo; por encima, " +
              "amplifica tanto las subidas como las bajadas.",
            example:
              "CIB: <b>0,437</b>. Si el S&P 500 cae un 10%, esta acción tiende a " +
              "caer un 4,4%: es defensiva.",
          },
        ],
      },
    ],
  },
};

/* Vista actual a partir de la URL.
 *
 * Se deduce de la ruta y no de una variable de plantilla porque el modal vive
 * en `base.html`, compartido por las dos vistas: leer la ruta lo mantiene con
 * una sola fuente de verdad.
 */
function viewFromPath(pathname) {
  return pathname.startsWith("/oportunidades") ? "opportunities" : "portfolio";
}

document.addEventListener("alpine:init", () => {
  Alpine.data("helpDrawer", () => ({
    isOpen: false,
    tab: "portfolio",
    /* Acordeón: se abre la primera entrada de cada grupo y el resto colapsa.
     * Con 23 métricas, mostrarlas todas desplegadas obliga a desplazarse
     * mucho para encontrar la que se busca. */
    expanded: {},
    query: "",

    open() {
      // La pestaña arranca en la vista donde está el usuario, pero puede
      // cambiarla: quien mira Oportunidades quizá quiera repasar el P&L.
      this.tab = viewFromPath(window.location.pathname);
      this.query = "";
      this.isOpen = true;
      this.$nextTick(() => this.$refs.search?.focus());
    },

    close() {
      this.isOpen = false;
    },

    get tabs() {
      return Object.entries(GLOSSARY).map(([key, section]) => ({
        key,
        label: section.label,
      }));
    },

    get section() {
      return GLOSSARY[this.tab];
    },

    /* Grupos ya filtrados por el buscador.
     *
     * Se calcula AQUÍ y no con un `x-if` dentro del `x-for` de la plantilla:
     * Alpine no inicializa un `<template x-for>` anidado dentro de otro
     * template y la sección se renderizaría vacía sin ningún error visible.
     */
    get groups() {
      const needle = this.query.trim().toLowerCase();
      if (!needle) return this.section.groups;
      return this.section.groups
        .map((group) => ({
          ...group,
          entries: group.entries.filter((entry) =>
            `${entry.term} ${entry.definition} ${entry.example}`
              .toLowerCase()
              .includes(needle),
          ),
        }))
        .filter((group) => group.entries.length > 0);
    },

    get isEmpty() {
      return this.groups.length === 0;
    },

    toggle(term) {
      this.expanded[term] = !this.expanded[term];
    },

    isExpanded(term) {
      return !!this.expanded[term];
    },
  }));
});
