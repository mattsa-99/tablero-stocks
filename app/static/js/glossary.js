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
      {
        title: "Importar operaciones",
        entries: [
          {
            term: "Importar CSV (plantilla fija, todo o nada)",
            definition:
              "Carga muchas operaciones de una vez desde un CSV con la plantilla " +
              "del Tablero. No adivina formatos de broker: cada uno exporta " +
              "columnas, fechas y decimales distintos y adivinar mal corrompe el " +
              "coste medio. Primero se REVISA todo sin escribir nada; si hay un " +
              "solo error no se importa ninguna fila.",
            example:
              "Una compra: <b>2026-01-20,BUY,AAPL,2,185.50,,1.00,USD,4150.25</b> " +
              "(fecha, tipo, símbolo, cantidad, precio, importe, comisión, divisa, " +
              "tipo de cambio). Volver a importar el mismo archivo no duplica nada.",
            caveat:
              "Punto decimal y sin separador de miles. Una fecha sin hora se " +
              "guarda al mediodía de Bogotá. Si vendes y compras el mismo día, " +
              "escribe la hora para fijar el orden.",
          },
        ],
      },
    ],
  },

  opportunities: {
    label: "Oportunidades",
    intro:
      "Los ejemplos usan fichas reales observadas el 21/09/2026. Las cifras " +
      "concretas cambian a diario; lo que enseñan, no.",
    groups: [
      {
        /* Va PRIMERO a propósito. Todo lo que sigue son métricas concretas, y
         * sin esta sección el usuario aprende a leerlas una por una sin saber
         * nunca cuánta confianza merece el conjunto. La advertencia estaba
         * repartida en `caveat`s sueltos; aquí se explica de una vez. */
        title: "Qué confianza merece esto",
        entries: [
          {
            term: "Heurística (qué significa «sin backtest»)",
            definition:
              "Una heurística es una regla <b>razonada pero no demostrada</b>. " +
              "Los pesos de la fórmula y los umbrales de las banderas son " +
              "juicios de diseño: alguien decidió que la valoración pesara 35% " +
              "y el momentum 30%, y que «ya subió mucho» empezara en +50% a " +
              "doce meses. Son defendibles; no están probados.",
            example:
              "Cuando la ficha de EQNR dice <b>«ya subió 79% en 12 meses»</b>, " +
              "el 79% es un HECHO. Que eso merezca una bandera amarilla es un " +
              "JUICIO. Lo mismo con «volatilidad alta (39%)»: el 39% se mide, " +
              "el umbral de 35% se eligió.",
            caveat:
              "Distinguir el hecho del juicio es la lectura correcta de toda " +
              "esta pantalla. Los hechos se pueden verificar; los umbrales se " +
              "pueden discutir.",
          },
          {
            term: "Backtest (y por qué aquí no hay)",
            definition:
              "Un backtest aplica la regla al pasado para ver si habría " +
              "funcionado: «comprando cada mes los 10 primeros desde 2015, " +
              "¿habría ganado más que comprando el índice?». <b>Eso nunca se " +
              "hizo aquí</b>, y por eso lo advierte cada pantalla.",
            example:
              "Hacerlo bien exigiría fundamentales históricos —el P/E que se " +
              "veía en 2018, no el de hoy—, incluir las empresas que quebraron " +
              "y no usar ningún dato antes de su fecha de publicación. El " +
              "tablero guarda precios desde 2025 y la foto ACTUAL de los " +
              "fundamentales.",
            caveat:
              "Un backtest malo es PEOR que ninguno: ajustando los pesos hasta " +
              "que el pasado se vea bonito salen cifras espectaculares que no " +
              "valen nada (se llama sobreajuste). Y entonces esta advertencia " +
              "desaparecería, le creerías más, y estarías peor informado.",
          },
          {
            term: "Cuánta diferencia de puntos significa algo",
            definition:
              "El score tiene dos decimales, y esa precisión es <b>falsa</b>. " +
              "Separa bien los extremos y no separa nada en el medio: una " +
              "diferencia de uno o dos puntos no distingue dos activos.",
            example:
              "Observado el 20/09/2026: <b>#1 CIB con 76,46 y #4 PBR con " +
              "75,36</b> — tres puestos de distancia y apenas un punto. En " +
              "cambio un 76 frente a un 45 sí dice algo.",
            caveat:
              "Los ejemplos llevan fecha porque el score es un percentil sobre " +
              "datos vivos: las cifras exactas cambian cada día, la lección no. " +
              "Y nunca compres algo solo porque es #1: es el primero de ESTA " +
              "lista, con ESTOS pesos, HOY.",
          },
          {
            term: "Qué es más fiable que el score",
            definition:
              "No todo en esta pantalla merece la misma confianza. Las " +
              "<b>banderas</b> responden a hechos concretos y verificables " +
              "(«la deuda es 4,5 veces el EBITDA»). La <b>calificación A–E</b> " +
              "compara contra anclas externas: el P/E de su sector y umbrales " +
              "contables. El <b>puesto</b> es una suma ponderada de cuatro " +
              "juicios, y es lo más frágil de los tres.",
            example:
              "Observado el 21/09/2026: <b>PBR era #1 de 268 acciones con " +
              "82,29 y ETB.CL #4 con 78,53</b>. Cuatro puntos de diferencia en " +
              "el score, y veredictos opuestos en la calificación: «Muy " +
              "favorables» frente a «Desfavorables». Hazle caso a la " +
              "calificación.",
            caveat:
              "Si el puesto y la calificación se contradicen, el puesto es el " +
              "que suele estar equivocado.",
          },
          {
            term: "Cómo comprobarlo tú mismo",
            definition:
              "Lo único que puede convertir estas heurísticas en algo medido " +
              "es tu propio historial. Cada decisión anotada en el <b>diario</b> " +
              "—con su tesis, su invalidación y su fecha— es una predicción " +
              "escrita ANTES de conocer el resultado.",
            example:
              "En seis meses tendrás tu propio backtest: pequeño, pero <b>sin " +
              "sobreajuste posible</b>, porque las reglas se escribieron antes. " +
              "Es exactamente lo que ningún backtest de folleto puede prometer.",
            caveat:
              "Solo funciona si anotas también las que salen mal, y si no " +
              "reinterpretas la invalidación a tu favor cuando se cumple.",
          },
        ],
      },
      {
        title: "La nota global",
        entries: [
          {
            term: "Opportunity Score (0-100)",
            definition:
              "Combina tres pilares en una sola cifra. Es un <b>ranking " +
              "RELATIVO dentro de su clase de activo</b>: mide cómo se compara " +
              "con los otros de su clase, no si es bueno en términos absolutos. " +
              "Siempre hay un primero, aunque todos sean malos.",
            example:
              "CIB saca <b>81,27</b> = 23,53 de base + 35,65 de valoración + " +
              "33,97 de momentum − 11,88 de riesgo. El desglose de cada " +
              "tarjeta suma exactamente el score: si no cuadra, es un error.",
            caveat:
              "No es una recomendación de inversión. Los pesos son un juicio " +
              "de diseño, sin backtest que los respalde. Y el score de una " +
              "acción NO es comparable con el de una cripto: cada uno es un " +
              "percentil contra los de SU clase.",
          },
          {
            term: "Clase de activo",
            definition:
              "La dimensión de primer nivel: acciones, fondos de acciones, " +
              "renta fija, materias primas y cripto. <b>El score se calcula " +
              "dentro de cada clase</b>, porque ordenar una acción frente a un " +
              "bono no es una decisión que un número pueda tomar. Entre clases " +
              "lo que se decide es el PESO, y eso lo decides tú en el Plan.",
            example:
              "Antes, con un solo ranking, <b>SJNK -un fondo de bonos basura- " +
              "salía con la MEJOR valoración de los 490</b>, porque el " +
              "proveedor le asigna un «P/E» de 0,89 a algo que no tiene " +
              "beneficios. Hoy compite contra otros 30 fondos de renta fija.",
            caveat:
              "Cuando la clase sale de una suposición y no de un dato del " +
              "proveedor, la tarjeta lo dice. Hoy solo pasa con un activo.",
          },
          {
            term: "El puesto: «#7 de 31»",
            definition:
              "El puesto es dentro de <b>su clase</b>, y el «de N» no es " +
              "adorno: un «#1» se lee como «el mejor de todo» cuando puede ser " +
              "el mejor de veinte. Al filtrar la vista el puesto NO se " +
              "renumera, así que ver huecos (#1, #4, #32) es normal y es " +
              "información.",
            example:
              "<b>BTC-USD es #1 de 25 en cripto con 68,12 puntos, y su " +
              "calificación es «Muy desfavorables»</b>. Encabeza su clase sin " +
              "ser una buena idea: alguien tiene que ser el primero.",
            caveat:
              "Es el mejor ejemplo de por qué el puesto no basta. Compararlo " +
              "siempre con la calificación, que sí es absoluta.",
          },
          {
            term: "Calificación (Muy favorables → Muy desfavorables)",
            definition:
              "La respuesta a «¿esto es bueno?», que es distinta de «¿qué " +
              "puesto ocupa?». No mira a los demás candidatos: compara contra " +
              "el P/E de su sector, contra el signo de la tendencia y contra " +
              "umbrales fijos de riesgo y calidad contable. <b>Habla de las " +
              "SEÑALES medidas, no de la empresa</b>: son cuatro indicadores " +
              "de precio y de contabilidad, no un juicio sobre el negocio.",
            example:
              "<b>ETB.CL es #4 de 268 acciones con 78,53 puntos y está " +
              "«Desfavorables»</b>. Por eso la lista se ordena primero por " +
              "calificación: un score alto no puede encabezar la pantalla si " +
              "el propio tablero dice que las señales son malas.",
            caveat:
              "Con menos de 2 de 4 señales aparece «Sin calificar», nunca " +
              "«Mixtas»: un neutro por defecto afirmaría algo sin base.",
          },
          {
            term: "Cobertura: «2/4 señales»",
            definition:
              "Cuántas de las cuatro señales tienen datos. Hacen falta <b>al " +
              "menos 3 para una nota alta</b>; con menos, la calificación se " +
              "limita a «Mixtas» y la tarjeta lo declara.",
            example:
              "Sin ese tope, la nota se calculaba sobre lo disponible y por " +
              "tanto <b>premiaba la falta de datos</b>: medido sobre los 490 " +
              "del ranking, un ETF sacaba la nota máxima el <b>40,9%</b> de " +
              "las veces y una acción el <b>6,3%</b>. Seis veces y media más, " +
              "solo por tener menos que suspender.",
            caveat:
              "Topar no es suspender. «Mixtas» significa que no hay base para " +
              "afirmar más, no que el activo sea malo.",
          },
        ],
      },
      {
        title: "Los tres pilares",
        entries: [
          {
            term: "Valoración (41% del peso)",
            definition:
              "Si el activo está barato o caro, según sus ratios de precio " +
              "contra beneficios, valor contable y flujo operativo. La " +
              "referencia es <b>su sector</b> cuando tiene pares suficientes, " +
              "y su clase de activo cuando no: un banco a P/E 9,6 no es " +
              "barato, es un banco.",
            example:
              "CIB suma <b>+35,7 pts</b>: con un P/E de 9,5 está entre lo más " +
              "barato de los servicios financieros. IVV, en cambio, suma solo " +
              "<b>+8,1</b>: un P/E de 24,4 es caro <i>frente a otros fondos " +
              "de acciones</i>, que es contra quien se le compara.",
          },
          {
            term: "Momentum (35%)",
            definition:
              "Si el precio viene subiendo de forma sostenida. Mide tendencia " +
              "reciente, no valor: una acción cara puede tener buen momentum. " +
              "Las ventanas se miden en TIEMPO, no en barras, para que «doce " +
              "meses» signifiquen lo mismo en una bolsa y en un mercado que " +
              "abre los 365 días.",
            example: "CIB suma <b>+34,0 pts</b>: casi duplicó su precio en un año.",
          },
          {
            term: "Riesgo (−24%, penaliza)",
            definition:
              "Resta, no suma. Combina cuánto oscila el precio y cuál ha sido " +
              "su peor caída, medida sobre <b>cinco años</b> de histórico. Dos " +
              "activos con el mismo retorno no son equivalentes si uno llegó " +
              "ahí con el triple de sobresaltos.",
            example:
              "CIB resta <b>−11,9 pts</b>. La caída máxima mira la serie " +
              "entera y no el último año: un activo tranquilo doce meses puede " +
              "haber caído un 60% dieciocho meses atrás.",
          },
          {
            term: "Encaje con tu cartera (no suma)",
            definition:
              "Cuánto pesa ya en tus posiciones el cubo de exposición de este " +
              "activo. Se muestra aparte y <b>con contribución cero a " +
              "propósito</b>: el score dice si la oportunidad es buena, y eso " +
              "no puede depender de lo que ya tengas comprado.",
            example:
              "Antes sí sumaba, y el efecto estaba medido: <b>483 de 490 " +
              "símbolos cambiaban de puesto</b> al mirar el mismo universo " +
              "desde otra cartera. IVV pasaba del #220 al #113 sin que nada " +
              "hubiera cambiado en IVV.",
            caveat:
              "Sigue pesando donde la pregunta sí es «¿esto me conviene a " +
              "mí?»: en la Sugerencia óptima y en el Plan de asignación.",
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
      {
        title: "Antes de decidir: la ficha de compra",
        entries: [
          {
            term: "Banderas y veredicto",
            definition:
              "La ficha revisa la empresa con una lista de comprobaciones y las " +
              "clasifica: <b>roja</b> = problema serio, <b>amarilla</b> = revisa " +
              "antes de decidir, <b>verde</b> = punto a favor, <b>info</b> = contexto. " +
              "El veredicto es rojo si hay UNA roja; verde solo si no hay ni rojas " +
              "ni amarillas.",
            example:
              "ETB.CL es la <b>#4 de 268 acciones</b> y aun así la ficha marca " +
              "<b>banderas rojas</b> (señales «Desfavorables» y volatilidad " +
              "alta): estar arriba en el score no la hace buena.",
            caveat:
              "El veredicto NUNCA dice «compra». Sin banderas rojas solo significa " +
              "que este filtro no encontró problemas: no mide el negocio, la " +
              "gerencia, la competencia ni el futuro.",
          },
          {
            term: "reference_pe / pe_vs_reference (P/E frente a su sector)",
            definition:
              "La valoración se compara con el P/E MEDIANO de las empresas de su " +
              "propio sector (si hay al menos 8), no con todo el mercado: un banco " +
              "y una tecnológica no se valoran con la misma vara. Sin suficientes " +
              "pares, se usa el mercado.",
            example:
              "CIB cotiza a P/E <b>9,6</b> frente a una mediana de <b>14,5</b> en " +
              "Financial Services: paga el <b>66%</b> de lo que pagan sus pares.",
            caveat:
              "Barato no es lo mismo que bueno: puede descontar riesgos que aún no " +
              "has identificado, o ganancias en su punto más alto (sectores " +
              "cíclicos como energía o materiales).",
          },
          {
            term: "Deuda neta / EBITDA",
            definition:
              "Cuántos años de beneficio operativo (antes de depreciaciones) haría " +
              "falta para pagar la deuda si la caja se usara para ello. Se calcula " +
              "(deuda total − caja) / EBITDA. Amarilla desde 3x y roja desde 4,5x; " +
              "en sectores con deuda estructural (servicios públicos, inmobiliario, " +
              "telecomunicaciones) los umbrales son 5x y 7x.",
            example:
              "AAPL: <b>0,1x</b>, casi sin deuda neta. Una empresa con 6x tarda " +
              "seis años de beneficio operativo solo en saldarla.",
            caveat:
              "En bancos y aseguradoras NO aplica: la deuda y la caja son su " +
              "materia prima. Y en fabricantes con brazo financiero (autos, " +
              "maquinaria) la deuda incluye los préstamos a clientes, así que " +
              "exagera; ahí la bandera baja a amarilla.",
          },
          {
            term: "Caja libre (FCF) y rendimiento de caja libre",
            definition:
              "Lo que le sobra a la empresa después de pagar su operación e " +
              "inversiones. La <b>caja libre / ventas</b> dice cuánto de cada " +
              "peso vendido se queda como caja; el <b>rendimiento</b> es la caja " +
              "libre entre lo que vale la empresa en bolsa.",
            example:
              "AAPL: caja libre del <b>23,1%</b> de sus ventas y rendimiento de " +
              "<b>2,2%</b>. Si la caja libre es negativa, la empresa quema dinero.",
            caveat:
              "El rendimiento solo se calcula si la empresa reporta en la misma " +
              "moneda en que cotiza y su capitalización cuadra con precio × " +
              "acciones; si no, aparece «n/d» con el motivo (pasa con ADR y " +
              "clases duales).",
          },
          {
            term: "Margen operativo",
            definition:
              "Qué parte de las ventas queda como beneficio de la operación, " +
              "antes de intereses e impuestos. Mide la eficiencia del negocio en sí.",
            example:
              "AAPL: <b>32,6%</b>: de cada 100 vendidos, 32,6 son beneficio " +
              "operativo. Un supermercado suele estar por debajo del 5%.",
            caveat:
              "Solo se compara entre empresas del mismo tipo de negocio.",
          },
          {
            term: "Current ratio (liquidez)",
            definition:
              "Activos de corto plazo entre deudas de corto plazo. Por debajo de " +
              "1 hay más deudas próximas que caja y cobros próximos.",
            example:
              "ETB.CL: <b>0,50</b>, sus deudas de corto plazo duplican sus activos " +
              "de corto plazo. AAPL: <b>1,00</b>.",
            caveat:
              "En algunos sectores (supermercados, restauración) un ratio bajo es " +
              "normal porque cobran al contado y pagan a proveedores a plazo.",
          },
          {
            term: "Precio objetivo de analistas",
            definition:
              "El promedio de lo que estiman los analistas que cubren la acción a " +
              "12 meses. Es una <b>opinión</b>, no un dato, y suele ser optimista.",
            example:
              "AAPL: objetivo de <b>328,22</b>, un <b>−2,4%</b> sobre el precio " +
              "actual, con 39 analistas y recomendación «buy».",
            caveat:
              "Si el objetivo se aleja del precio de forma imposible (más de 5 " +
              "veces o −90%) se descarta como dato roto: suele ser otra moneda.",
          },
          {
            term: "Tamaño de posición (techo por riesgo)",
            definition:
              "Cuánto poner en UNA empresa según cuánto aceptas perder si repite " +
              "su peor caída: <b>peso = presupuesto de riesgo ÷ caída de estrés</b>, " +
              "con un tope por posición. La caída de estrés es la mayor entre la " +
              "peor caída medida y un suelo (35% en acciones, 25% en fondos), " +
              "porque el histórico guardado es de solo ~1 año.",
            example:
              "Aceptas perder 2% de la cartera y la acción llegó a caer 40%: " +
              "2% ÷ 40% = <b>5% de la cartera</b>. Si repite esa caída, pierdes " +
              "el 2% del total, ni más ni menos.",
            caveat:
              "Es un techo razonable, no una orden: no mira cuánto efectivo tienes " +
              "ni cómo se mueve junto con el resto de tu cartera. Los valores por " +
              "defecto (2% y 10%) son un juicio, y puedes cambiarlos en la ficha.",
          },
          {
            term: "Diario: tesis, invalidación y revisión",
            definition:
              "Antes de comprar escribes <b>por qué</b> (tesis), <b>qué hecho " +
              "demostraría que te equivocaste</b> (invalidación, con un precio " +
              "opcional) y <b>cuándo vuelves a mirar</b>. El Tablero guarda una " +
              "foto de ese día y te avisa cuando el precio cruza tu nivel o vence " +
              "la fecha.",
            example:
              "«Compro KO por su caja libre estable; me equivoqué si recorta el " +
              "dividendo o baja de 60». Meses después, si cae a 58, la alerta te " +
              "pide revisar con tu propia regla, no vender por pánico.",
            caveat:
              "Una alerta pide REVISAR, nunca vender. Y anotar una decisión no " +
              "crea ninguna operación: el registro de compras sigue siendo aparte.",
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
  // El diario comparte glosario con oportunidades: sus términos (tesis,
  // invalidación, banderas, tamaño de posición) viven allí.
  return pathname.startsWith("/oportunidades") || pathname.startsWith("/diario")
    ? "opportunities"
    : "portfolio";
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
