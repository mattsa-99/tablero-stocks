# Guía operativa: cómo uso este tablero para decidir una compra

> Escrita en primera persona, como se lo explicaría a alguien que empieza.
> Las cifras de los ejemplos son **inventadas y redondas** para que la
> aritmética se siga a ojo; los tuyos serán otros.
>
> Esta guía enseña el **proceso**. Para saber qué significa una métrica
> concreta, usa el botón **?** de la barra superior: el glosario define cada
> una con su ejemplo.

---

## 1. Apostar y gestionar no son lo mismo

Cuando empecé, mis decisiones se parecían a esto: leía que una empresa iba a
revolucionar algo, me entusiasmaba, compraba. Si subía, era listo; si bajaba,
era mala suerte. Eso es **apostar**: la decisión nace de una narrativa y de
cómo amanecí ese día, y no queda registro de por qué la tomé.

**Gestionar de forma sistemática** es lo contrario, y es más aburrido:

| Apostar | Gestionar |
|---|---|
| Miro las empresas de las que se habla | Aplico **el mismo criterio a todos** los candidatos |
| Decido cuando me emociono | Decido en un momento fijo, con el mismo procedimiento |
| Justifico después | Dejo constancia de la cifra que me convenció |
| Un mal resultado es mala suerte | Un mal resultado es una hipótesis que puedo revisar |

No se trata de acertar más. Se trata de **poder equivocarte de forma
revisable**: si dentro de un año una compra salió mal, quiero saber qué vi
entonces, no reconstruir mi estado de ánimo.

### Para qué sirve el Opportunity Score

Tengo unos 490 candidatos. Ningún humano compara 490 empresas por cuatro
dimensiones sin agotarse ni hacer trampa. El score hace dos cosas concretas:

- **Mata la parálisis.** Convierte 490 en una lista corta por la que empezar
  a mirar.
- **Mata el sesgo del titular.** Puntúa igual a la empresa de la que todo el
  mundo habla y a la aburrida de la que nadie habla.

> **Lo que el score NO es.** No es un consejo de compra. Los pesos de la
> fórmula (`0,35·Valoración + 0,30·Momentum + 0,15·Diversificación −
> 0,20·Riesgo`) son **un juicio de diseño, sin ningún backtest detrás**. Y es
> un **ranking relativo**: dice quién es el mejor del grupo, nunca si ese
> mejor es bueno. Para eso está la calificación A–E, que sí es absoluta.

---

## 2. Mi rutina: 25 minutos, cuatro pasos

### Paso 1 — Diagnóstico (Portafolio · 5 min)

Abro la vista **Portafolio** y leo las ocho tarjetas de arriba. No las miro
todas con la misma atención:

**Las que miro siempre:**

- **P&L no realizado** — cuánto he ganado o perdido *en papel*. Es el número
  que más me tienta a hacer tonterías, y por eso lo miro con desconfianza
  deliberada (ver consejo 3).
- **Caja** — cuánto puedo comprar hoy sin inventarme dinero. Si está en
  negativo, mi primer trabajo del día no es comprar: es registrar el depósito
  que falta.
- **Retorno** — el porcentaje sobre lo que invertí. Es el único que compara
  peras con peras entre carteras de tamaños distintos.

**Las que miro cuando algo me chirría:** Valor de mercado, Coste total, P&L
total, P&L realizado y Dividendos.

Después bajo al panel **Distribución**. Tiene dos pestañas, y aquí está el
truco que más me ha servido:

> **Míralo siempre en «Sector», no en «Símbolo».**
>
> En «Símbolo» ves que tienes cinco posiciones y te quedas tranquilo. Cambias
> a «Sector» y descubres que cuatro son tecnología. Cinco posiciones no son
> diversificación si todas caen el mismo día por la misma noticia.
>
> *Ejemplo:* si una posición pesa el 52% de la cartera, no tengo una cartera:
> tengo una apuesta con cuatro acompañantes. Ese número es mi señal para que
> la compra de hoy vaya a **otro** sector.

### Paso 2 — Escaneo (Oportunidades · 10 min)

Voy a la vista **Oportunidades**. Sin filtros son ~490 candidatos: demasiados.
Filtro en dos golpes.

**Primero por calidad.** Al abrir la vista ya vienen activados `Muy buena` y
`Buena`, y un aviso te dice cuántas quedan fuera; **Ver todos** las muestra.
En la fila **Calidad** puedo cambiarlo: son chips, se activan y desactivan.
Se combinan. Con eso descarto de golpe
todo lo que en términos absolutos no merece la pena, sin importar qué puesto
ocupe.

**Después por mercado.** En la fila **Mercado** elijo la región. Uso esto para
corregir lo que vi en el paso 1: si voy cargado de EE.UU., filtro por
`Europa`, `LatAm` o `Colombia` y miro solo lo que me diversifica de verdad.

> **Dos cosas que conviene entender de los filtros:**
>
> 1. **Filtrar no recalcula nada.** El score se calcula siempre contra los
>    490 candidatos. Si filtro por Colombia, no estoy comparando 18 activos
>    entre sí: sigo viendo su puesto en la lista completa.
> 2. **Por eso los números saltan.** Verás `#1`, `#4`, `#32`. No es un fallo:
>    el #32 es el tercer mejor colombiano *de todo el ranking*, y esa
>    información se perdería si lo renumerara a #3.
>
> Los contadores de los chips (`126 Muy buena`, `18 Colombia`…) son siempre
> del universo entero, para que sepas qué te queda por explorar aunque el
> filtro actual no muestre nada.

Si quiero ver una región completa, subo el selector **Mostrar** a 50 o 100. La
cabecera me dice siempre cuántas estoy viendo de cuántas coinciden, así que no
me quedo con la duda de si hay más debajo. Cuando quiero volver al universo
entero, **Limpiar filtros** los desactiva todos de golpe.

**Cómo leo una tarjeta.** Cada una descompone su score en cuatro barras que
suman exactamente el total:

```
Valoración      +34.8 pts    ¿está barata?
Momentum        +28.1 pts    ¿viene subiendo?
Diversificación +15.0 pts    ¿me aporta algo a MÍ?
Riesgo          −11.5 pts    resta: ¿cuánto se mueve y cuánto llegó a caer?
base            +20.0 pts
```

Lo primero que miro no es el score: es **la etiqueta de calidad** al lado del
nombre (`Muy buena 6/8`, `Mala −2/6`). Un score alto con calificación mala
significa «es el mejor de un grupo flojo», y eso no es una compra.

Luego despliego **Métricas observadas** y miro tres cosas:

- **`trailing_pe`** — cuántas veces el beneficio anual estoy pagando. Bajo es
  barato; negativo o cero significa que la empresa pierde dinero, no que sea
  una ganga.
- **`sma50_over_sma200_minus_1`** — si la media de 50 días va por encima de la
  de 200. Positivo es tendencia alcista.
- **`annualized_volatility`** — cuánto oscila. Un ETF amplio ronda el 15%; por
  encima del 30% prepárate para sustos.

### Paso 2b — Antes de decidir: la ficha de compra (5 min)

Con un candidato que me interesa, pulso **Ver ficha de compra** en su tarjeta.
Es una sola pantalla con lo que un score no puede decir:

- **Banderas** rojas, amarillas y verdes, ordenadas por gravedad. El veredicto
  solo dice si el filtro encontró problemas; **sin banderas rojas no significa
  «compra»**, significa que este filtro no vio nada grave.
- **Salud financiera**: deuda neta frente al EBITDA, margen operativo, caja
  libre, liquidez. En bancos aparece «no aplica»: esas métricas no miden un
  banco.
- **Calendario y analistas**: próximos resultados y objetivo medio, como
  opinión, no como dato.
- **Frente a su sector**: su puesto entre las empresas del mismo sector. La
  valoración se compara con la mediana del sector, no con todo el universo.
- **Tu cartera**: si ya la tienes y cuánto pesa.
- **Cuánto poner**: un techo, no una orden. Se calcula como presupuesto de
  riesgo dividido entre la caída que la empresa podría repetir. Puedo cambiar
  el capital, el riesgo aceptado y el tope por posición y pulsar **Recalcular**.
- **De cuándo son los datos**: precio, histórico y fundamentales con su fecha.
  Si algo está viejo, lo dice.

Para el mismo informe en texto: `python scripts/ficha.py SYMBOL`.

Si la ficha me convence, pulso **Anotar en el diario** (ver el paso 4).

### Paso 3 — What-if (Simulador · 10 min)

Ya tengo un candidato. **No lo compro todavía.** Paso al simulador, que
responde la única pregunta que importa: *¿cómo queda mi cartera si hago esto?*

**Cómo llego ahí** — dos caminos, y conviene saber cuál es cuál:

- Desde el modal **Sugerencia óptima** (botón morado en Oportunidades): al
  pulsar **Simular compra** se lleva el símbolo y el precio al simulador
  automáticamente.
- Desde una tarjeta cualquiera del ranking: **no hay botón de simular**. Anoto
  el símbolo, voy a Portafolio, pulso **Simular** y lo tecleo a mano.

Dentro añado la compra fantasma y comparo el **antes / después**:

| Métrica | Qué me dice |
|---|---|
| **Diversificación** | Si sube, la compra reparte riesgo. Si baja, lo concentra |
| **Posiciones efectivas** | Cuántas posiciones *de verdad* tengo. Cinco posiciones donde una pesa el 80% son ~1,5 efectivas |
| **Mayor posición** | El porcentaje de la más grande. Es mi termómetro de concentración |
| **Caja** | Si queda negativa, esta compra no la puedo pagar |
| Valor de mercado y P&L total | Cambian poco al comprar; los uso de comprobación |

También hay dos donuts de sector, antes y después. Si el «después» se parece
demasiado al «antes», la compra no me está diversificando por mucho que el
pilar de Diversificación puntuara alto.

> **El simulador no escribe nada.** Ni una fila. Puedes probar diez compras
> absurdas y cerrar sin consecuencias. Está garantizado por diseño y por un
> test que cuenta las filas antes y después.

### Paso 4 — Ejecución (2 min)

Si el «después» me convence, pulso **Aplicar de verdad**. Eso sí registra las
transacciones en la base de datos y deja la trazabilidad: cantidad, precio,
comisiones y tipo de cambio quedan congelados, y de ahí sale mi coste medio.

> **Dos cosas que debes saber de este botón:**
>
> 1. **Sella la fecha y hora de ahora.** No sirve para cargar una compra que
>    hiciste la semana pasada. Para eso usa **Nueva transacción**, donde eliges
>    la fecha.
> 2. **Si son varias operaciones y falla a mitad, puede quedar aplicada solo
>    una parte.** La app te avisa cuando pasa. Revisa la lista de posiciones
>    antes de reintentar, o registrarás la misma compra dos veces.

**Anoto por qué.** En la vista **Diario** pulso **Anotar decisión** y escribo,
con mis palabras: por qué lo compro, **qué hecho demostraría que me equivoqué**,
a qué precio dejo de creerme la tesis y cuándo vuelvo a mirar (por defecto, en
90 días). El Tablero guarda una foto del score de ese día y me avisa cuando el
precio cae bajo mi nivel, cuando toca revisar o cuando la calificación empeora.
Anotar una decisión **no registra ninguna operación**, y una alerta pide
revisar, no vender.

**Si ya tengo un historial.** En Portafolio, **Importar CSV** carga operaciones
con la plantilla del Tablero (se descarga desde el mismo diálogo). Primero
revisa todo sin escribir nada; si una sola fila falla, no se importa ninguna.
Volver a subir el mismo archivo no duplica operaciones.

Y ya está. Cierro el tablero. **No vuelvo a mirarlo hasta mañana.**

---

## 3. Cinco consejos que me habría gustado recibir

### 1. No te enamores de una narrativa sin mirar el P/E y la calidad

Una empresa puede ser magnífica y una pésima inversión **al mismo precio**.
Son dos preguntas distintas: *¿es buen negocio?* y *¿está bien de precio?*

Un `trailing_pe` de 300 significa que pagas 300 años de beneficios actuales.
Puede estar justificado si va a crecer muchísimo — o puede ser que todo el
mundo esté igual de entusiasmado que tú, y el entusiasmo ya esté en el precio.

**Qué hacer:** antes de leer el nombre de la empresa, mira la etiqueta de
calidad y el P/E. Si el orden es al revés, tu cerebro ya habrá decidido.

### 2. La diversificación es el único almuerzo gratis

Es lo único en finanzas que **mejora tu relación riesgo/rentabilidad sin
costarte rentabilidad esperada**. Todo lo demás son intercambios.

Por eso el pilar de Diversificación premia lo que **no** tienes. Cuando ves
`sector_weight = 0.000`, el sistema te está diciendo: *no tienes nada de este
sector, así que esto te aporta algo que tu cartera no tiene*. Ese activo se
lleva los 15 puntos completos.

Y al revés: si ya tienes el 47% en tecnología, la siguiente tecnológica —por
excelente que sea— apenas te aporta. No porque sea mala, sino porque **ya
tienes ese riesgo**.

**Qué hacer:** que la vista «Sector» del donut, y no tu entusiasmo, decida en
qué sector compras hoy.

### 3. El P&L no realizado es una ilusión hasta que vendes

Ese `+15%` en verde no es dinero. Es una opinión del mercado sobre lo que
alguien pagaría hoy. Mañana puede ser `+3%`, y no habrás hecho nada.

Ahí es donde se pierde dinero de verdad, en las dos direcciones:

- **Avaricia:** «sube un 40%, va a seguir» → compras más arriba, subes tu
  coste medio y quedas más expuesto justo cuando más caro está.
- **Pánico:** «baja un 20%, vendo antes de que empeore» → conviertes una
  pérdida en papel en una pérdida real, y encima queda registrada en tu P&L
  realizado para siempre.

**Qué hacer:** decide *antes* de comprar qué te haría vender. Si tu única
razón para vender es «bajó», no tenías una tesis, tenías una esperanza.

### 4. Respeta el indicador de Caja

La caja **puede quedar negativa** en este tablero, y es a propósito: permite
cargar un histórico ya existente sin inventarse depósitos que nunca ocurrieron.

Pero una caja negativa en el día a día significa una de dos cosas, y ninguna
es buena: o te falta registrar un depósito —y tus números están mal—, o estás
comprando con dinero que no tienes.

**Qué hacer:** que la caja cuadre antes de comprar. Y si el simulador te deja
la caja en negativo después de la compra, esa compra no la puedes pagar. No es
una advertencia sutil: es aritmética.

### 5. Confía en el proceso, no en tu criterio del martes

Pulsar **Sincronizar** y hacer el recorrido de 25 minutos no te hace acertar
más. Hace otra cosa, que a largo plazo vale más:

- **Te da una serie histórica.** Dentro de un año podrás ver qué decidiste y
  con qué datos. Sin registro no hay aprendizaje, solo anécdotas.
- **Te quita la decisión del momento emocional.** Si tu rutina es a las 8 de
  la mañana, no comprarás por un titular de las 3 de la tarde.
- **Convierte «invertir» en un hábito aburrido.** Y en esto, aburrido es un
  cumplido.

La sincronización completa tarda unos minutos porque son ~490 símbolos. Déjala
correr mientras te preparas un café.

---

## 4. Lo que este tablero NO hace

Esto no está para cubrirme las espaldas. Son límites reales que **cambian cómo
debes leer lo que ves**:

- **El score siempre produce un primero, aunque todos sean malos.** Puede
  encabezar el ranking un activo calificado «Mala». Cuando pasa, la app te lo
  dice; hazle caso a la calificación, no al puesto.
- **El universo son solo activos en USD y COP, y es deliberado.** Es lo que
  compras por tu comisionista. La exposición internacional entra por ADRs y
  ETFs de país, no por bolsas extranjeras — entre otras cosas porque el
  momentum de una acción japonesa medido en yenes puede tener el signo
  contrario al que tú experimentarías en pesos.
- **Los datos del proveedor a veces llegan rotos.** Hay filtros que descartan
  ratios imposibles antes de que entren al cálculo, pero **no son
  infalibles**. Si un número te parece demasiado bueno, probablemente lo sea.
- **Un ticker de Yahoo puede no ser el instrumento que vende tu
  comisionista.** Es el único punto de esta lista que puede costarte dinero
  directamente. Verifícalo con tu comisionista antes de comprar.
- **Lo que no se puede medir, no se rankea.** Los candidatos sin histórico
  suficiente quedan excluidos con su motivo visible al final de la lista. No
  aparecerán nunca entre tus oportunidades, aunque sean buenas empresas.
- **La ficha y el tamaño de posición son heurísticas, no un backtest.** Las
  banderas usan umbrales de sentido común y el techo de peso usa una caída de
  estrés con un suelo (35 % en acciones, 25 % en fondos), porque con un año de
  histórico una calma reciente no garantiza que no pueda caer más.
- **Las fechas de los datos importan.** Los fundamentales son trimestrales y
  Yahoo llega con retraso: antes de comprar, comprueba las cifras clave en la
  web de la empresa o en la bolsa.
- **El coste medio mide tu rendimiento, no es una declaración fiscal.**
  Coincide con el costo promedio del Estatuto Tributario colombiano para
  acciones, pero eso lo confirma un contador, no este tablero.

---

## Resumen para pegar en la pared

```
1. Portafolio  →  ¿caja sana? ¿algún sector desbocado?   (5 min)
2. Oportunidades → filtro Calidad + filtro Mercado        (10 min)
                   miro la CALIFICACIÓN antes que el score
   Ficha       →  banderas, salud, sector, cuánto poner    (5 min)
3. Simulador   →  ¿sube Diversificación? ¿caja positiva?  (10 min)
4. Aplicar     →  solo si el "después" es mejor que el "antes"
   Diario      →  anota por qué y qué te haría cambiar de opinión
```

**La regla que resume todo:** si no puedes explicar en una frase por qué esta
compra mejora tu cartera —y no solo por qué la empresa te gusta—, todavía no
es una decisión. Es un impulso con datos alrededor.
