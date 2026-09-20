"""Ejecuta las comprobaciones de `store.js` con node.

Los formateadores son el último filtro antes del ojo del usuario, y su fallo
más caro no es calcular mal sino AFIRMAR algo que el dato no dice. Se prueban
en JavaScript porque lo que importa es lo que hace `Intl` de verdad; una copia
de la lógica en Python probaría la copia.

Se omite si no hay node: la suite tiene que seguir corriendo sin él. Node ya
hace falta para compilar el CSS, así que en la máquina de desarrollo está.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SUITE = ROOT / "tests" / "js" / "format.test.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no está instalado")
def test_the_javascript_formatters_behave():
    result = subprocess.run(
        ["node", str(SUITE)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
