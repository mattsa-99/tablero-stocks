"""Servidor MCP del tablero: expone sus datos y cálculos a un asesor externo.

POR QUÉ VIVE AQUÍ Y NO DENTRO DE LA APP
=======================================
El asesor corre en Claude Desktop, no en el tablero. Este paquete es solo la
ventana: traduce los servicios existentes a herramientas MCP y no calcula
NADA por su cuenta. Si una cifra no la sabe producir `app/services`, tampoco
la produce el asesor.

Consecuencia deliberada: el tablero sigue funcionando entero sin esto. La
capa de IA es desmontable.

IMPORTA LOS SERVICIOS EN PROCESO, no habla HTTP con la app: los servicios ya
son importables sin FastAPI, así que funciona con `uvicorn` apagado y no hay
una segunda copia de la lógica que se pueda desincronizar.
"""

# ANTES de cualquier import de `app`: Claude Desktop no respeta el `cwd` y
# tanto la ruta por defecto de la base como el `.env` son relativos a él.
from mcp_server.bootstrap import situarse_en_la_raiz

situarse_en_la_raiz()

from mcp_server.tools import TOOLS  # noqa: E402

__all__ = ["TOOLS"]
