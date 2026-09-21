"""El servidor habla el protocolo de verdad.

Arranca `python -m mcp_server` como subproceso y hace el saludo completo, que
es lo que hará Claude Desktop. No comprueba datos -de eso va
`test_mcp_tools.py`-: comprueba que el transporte no esté roto, que es un
fallo distinto y se diagnostica distinto.

Se lanza con una base VACÍA y temporal a propósito: así el saludo y el
listado se prueban sin depender de que haya cartera, y no se toca la del
desarrollador.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
# El catálogo completo, y se comprueba por IGUALDAD y no por inclusión: una
# herramienta que se registra en `tools.py` y se olvida en `server.py` no falla
# en ningún sitio, simplemente no existe para el asesor.
ESPERADAS = {
    "brief", "ficha", "opportunities", "journal",
    "journal_write", "whatif", "performance",
    "allocation", "fixed_income", "rates",
}


async def _dialogo(peticiones: list[dict], tmp: str) -> dict[int, dict]:
    """Habla por turnos: manda una petición y espera SU respuesta.

    No vale mandarlo todo de golpe y cerrar stdin. El servidor atiende de forma
    asíncrona y el cierre del canal lo termina antes de contestar lo que quede
    en la cola: probándolo así solo se veía el saludo y ninguna herramienta.
    Un cliente real tampoco cuelga stdin entre mensajes.
    """
    entorno = {
        **os.environ,
        "TABLERO_DATABASE_URL": f"sqlite:///{Path(tmp) / 'mcp.db'}",
        "TABLERO_ENABLE_BACKGROUND_REFRESH": "false",
    }
    proceso = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "mcp_server",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=RAIZ,
        env=entorno,
    )
    respuestas: dict[int, dict] = {}
    try:
        for peticion in peticiones:
            proceso.stdin.write((json.dumps(peticion) + "\n").encode())
            await proceso.stdin.drain()
            if "id" not in peticion:  # una notificación no se contesta
                continue
            linea = await asyncio.wait_for(proceso.stdout.readline(), timeout=60)
            mensaje = json.loads(linea)
            respuestas[mensaje["id"]] = mensaje
    finally:
        proceso.terminate()
        await proceso.wait()
    return respuestas


def hablar(peticiones: list[dict]) -> dict[int, dict]:
    with tempfile.TemporaryDirectory() as tmp:
        return asyncio.run(_dialogo(peticiones, tmp))


INICIO = [
    {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "1"},
        },
    },
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
]


@pytest.mark.slow
def test_the_server_completes_the_handshake_and_lists_its_tools():
    respuestas = hablar([*INICIO, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])

    assert respuestas[1]["result"]["serverInfo"]["name"] == "tablero-stocks"
    nombres = {t["name"] for t in respuestas[2]["result"]["tools"]}
    assert nombres == ESPERADAS


@pytest.mark.slow
def test_only_the_journal_tool_declares_that_it_writes():
    """El cliente decide si pedir confirmación mirando esta anotación.

    Si una de lectura se marcara como escritura, Claude Desktop preguntaría de
    más; al revés -la de escritura marcada como lectura- guardaría sin avisar.
    """
    respuestas = hablar([*INICIO, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
    herramientas = {t["name"]: t for t in respuestas[2]["result"]["tools"]}

    escriben = {
        nombre
        for nombre, t in herramientas.items()
        if (t.get("annotations") or {}).get("readOnlyHint") is False
    }
    assert escriben == {"journal_write"}


@pytest.mark.slow
def test_a_missing_table_tells_the_user_the_exact_command():
    """El SDK convierte una excepción en «Error executing tool» y se traga el
    mensaje. Para un asesor eso es inservible.

    Este caso pasó de verdad: una migración sin aplicar dejaba cuatro endpoints
    en 500 con un error opaco. Aquí el asesor recibe el comando que lo arregla
    y puede decírselo al usuario en vez de quedarse mudo.
    """
    respuestas = hablar([
        *INICIO,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "brief", "arguments": {}}},
    ])

    contenido = respuestas[2]["result"]["content"][0]["text"]
    assert "alembic upgrade head" in contenido
    assert "traceback" not in contenido.lower()


@pytest.mark.slow
def test_the_server_finds_its_database_from_any_directory():
    """Claude Desktop NO respeta el `cwd` del config: lanza desde otra carpeta.

    Y dos cosas del tablero son relativas al directorio de trabajo: la ruta por
    defecto de la base (`sqlite:///./tablero.db`) y el `.env`. Arrancado desde
    fuera, el asesor recibía «unable to open database file» y se quedaba sin
    cartera, sin alertas y sin candidatos.

    Se lanza desde la raíz del sistema, el caso más hostil posible.
    """
    entorno = {**os.environ}
    entorno.pop("TABLERO_DATABASE_URL", None)  # que use la ruta relativa real

    resultado = subprocess.run(
        [sys.executable, "-c",
         "import mcp_server, pathlib; print(pathlib.Path.cwd())"],
        capture_output=True, text=True, timeout=60, cwd="/", env=entorno, check=False,
    )

    assert resultado.returncode == 0, resultado.stderr
    assert resultado.stdout.strip() == str(RAIZ), (
        "el servidor tiene que situarse en la raíz del proyecto al importarse"
    )
