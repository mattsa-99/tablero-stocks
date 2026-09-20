# Librerías de terceros

Se sirven desde este servidor, no desde un CDN. El motivo no es la latencia:
sin red la página se quedaba **sin estilos y sin Alpine**, o sea inservible.
Un tablero auto-alojado que se abre a diario no puede depender de que `cdnjs`
conteste, y menos cuando todo el backend está construido para degradar con
elegancia cuando el proveedor falla.

Vendorizar también **congela la versión de verdad**. Antes la fijaba la URL, y
eso protegía de una publicación nueva pero no de que el CDN cambiara lo que
sirve bajo esa misma URL.

| Archivo | Versión | Origen |
|---|---|---|
| `alpine.min.js` | 3.14.1 | https://cdnjs.cloudflare.com/ajax/libs/alpinejs/3.14.1/cdn.min.js |
| `alpine-collapse.min.js` | 3.14.1 | https://cdnjs.cloudflare.com/ajax/libs/alpinejs-collapse/3.14.1/cdn.min.js |
| `chart.umd.min.js` | 4.5.0 | https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.5.0/chart.umd.min.js |

El plugin `collapse` va **antes** que el core de Alpine: se registra sobre
`window.Alpine` al arrancar, y cargarlo después lo dejaría sin registrar.

Para actualizar: descargar el archivo, cambiar la versión en esta tabla y
comprobar que `test_frontend_assets.py` sigue en verde.
