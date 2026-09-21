"""Punto de entrada: `python -m mcp_server`.

Es lo que arranca Claude Desktop. No imprime nada por stdout: ese canal ES el
protocolo, y una sola línea suelta lo rompe.
"""

from mcp_server.server import main

if __name__ == "__main__":
    main()
