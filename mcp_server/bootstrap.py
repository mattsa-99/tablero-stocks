"""Sitúa el proceso en la raíz del proyecto ANTES de importar nada de `app`.

POR QUÉ HACE FALTA
==================
Claude Desktop **no respeta el `cwd`** del `claude_desktop_config.json`: lanza
el servidor desde otro directorio. Y dos cosas del tablero son relativas al
directorio de trabajo:

- `TABLERO_DATABASE_URL` por defecto es `sqlite:///./tablero.db`.
- `Settings` lee el `.env` del directorio actual.

Arrancado desde fuera, el servidor apuntaba a una base que no existe y el
asesor recibía «unable to open database file». Reproducido:

    cwd = raíz del proyecto  ->  OK
    cwd = /                  ->  unable to open database file

Se resuelve con `chdir` y no fijando una ruta absoluta en el config por dos
razones: así sigue valiendo el `.env` del usuario -si apunta a PostgreSQL o a
otra base, se respeta- y no hay una ruta duplicada que se quede obsoleta el
día que muevas la carpeta.

Tiene que ejecutarse antes de importar `app.core.config`, que crea `Settings`
en tiempo de import, y `app.db.session`, que crea el engine.
"""

from __future__ import annotations

import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def situarse_en_la_raiz() -> Path:
    """Cambia al directorio del proyecto. Devuelve dónde quedó.

    Si la raíz no existe -paquete instalado fuera del repositorio- se deja el
    directorio como esté: es mejor fallar con el error de base de datos, que
    dice qué pasa, que cambiar a un sitio arbitrario.
    """
    if (RAIZ / "app").is_dir():
        os.chdir(RAIZ)
    return Path.cwd()
