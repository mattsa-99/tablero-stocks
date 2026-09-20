/* Configuración de Tailwind.
 *
 * Es la MISMA que vivía en línea en `base.html` bajo el Play CDN. Al pasar a
 * compilación se mueve aquí sin cambiar ni un token: los colores semánticos
 * siguen apuntando a las custom properties de `app.css`, así la paleta vive en
 * UN solo sitio y el cambio de tema no necesita variantes `dark:` repartidas
 * por las plantillas.
 *
 * `content` tiene que incluir los .js: buena parte de las clases no está en el
 * HTML sino en tablas de JavaScript (GRADE_STYLE, CHIP_INACTIVE...), y el
 * escáner solo genera lo que encuentra escrito. Una clase construida por
 * concatenación NO se encontraría; por eso esas tablas guardan la clase
 * entera como literal, y `test_frontend_assets.py` lo vigila.
 */
/* Un token de color que SÍ admite modificador de opacidad.
 *
 * Tailwind no puede aplicar `/10` a un color que es literalmente `var(--good)`:
 * necesita poder componer el canal alfa, y una custom property opaca no se lo
 * permite. El resultado es que la utilidad NO SE GENERA -ni una advertencia- y
 * la clase queda muerta en el HTML.
 *
 * Eso llevaba pasando desde siempre: bajo el Play CDN regía la misma
 * limitación, así que `bg-good/10`, `bg-s1/10`, `bg-bg/85` y otras 24 clases
 * no pintaban NADA. Los chips de calificación y de región se veían planos, sin
 * el tinte que el diseño daba por hecho, y el encabezado pegajoso no tenía su
 * fondo translúcido.
 *
 * `color-mix` resuelve el alfa en el navegador sin tocar `app.css`, que sigue
 * siendo la única casa de la paleta. Sin modificador, Tailwind sustituye
 * `<alpha-value>` por 1 y el color queda íntegro. El proyecto ya depende de
 * `color-mix` en el anillo del score (`opportunities.js`).
 */
const tint = (variable) =>
  `color-mix(in srgb, var(${variable}) calc(<alpha-value> * 100%), transparent)`;

module.exports = {
  content: [
    "./app/templates/**/*.html",
    "./app/static/js/**/*.js",
  ],
  theme: {
    extend: {
      colors: {
        bg: tint("--bg"),
        surf: {
          DEFAULT: tint("--surface-1"),
          2: tint("--surface-2"),
          3: tint("--surface-3"),
        },
        line: { DEFAULT: tint("--border"), strong: tint("--border-strong") },
        ink: {
          DEFAULT: tint("--text-primary"),
          soft: tint("--text-secondary"),
          mute: tint("--text-muted"),
        },
        good: tint("--good"),
        warn: tint("--warning"),
        bad: tint("--critical"),
        s1: tint("--series-1"),
        s2: tint("--series-2"),
        s3: tint("--series-3"),
        s7: tint("--series-7"),
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', 'Inter', 'Segoe UI', 'system-ui', 'sans-serif'],
      },
    },
  },
};
