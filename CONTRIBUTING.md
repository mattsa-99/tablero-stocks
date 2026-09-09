# Convención de commits

Cada commit lleva un emoji, un tipo y una descripción corta en español, en
imperativo:

```
<emoji> <tipo> - <descripción>
```

Ejemplos:

```
✨ feat - refresco de mercado agendado con launchd
🐛 fix - GBp se trataba como GBP y la posición salía 100x
📝 docs - explica la frontera Decimal/float en el README
```

## Tipos y su emoji

| Emoji | Tipo | Cuándo |
|-------|------|--------|
| 🎉 | `init` | Commit inicial de un proyecto o módulo nuevo |
| ✨ | `feat` | Funcionalidad nueva |
| 🐛 | `fix` | Corrección de un bug |
| 🚑 | `hotfix` | Corrección urgente en producción, fuera del ciclo normal |
| ♻️ | `refactor` | Cambia la estructura del código sin cambiar el comportamiento |
| ⚡️ | `perf` | Mejora de rendimiento |
| 📝 | `docs` | Solo documentación (README, docstrings, comentarios, guías) |
| ✅ | `test` | Añade o corrige tests |
| 🔧 | `chore` | Configuración, tooling, dependencias, scripts de apoyo |
| 🎨 | `style` | Formato, lint, orden de imports; sin cambio de lógica |
| 🗃️ | `db` | Migraciones de Alembic o cambios de esquema |
| ⏪️ | `revert` | Revierte un commit anterior |
| 🚀 | `deploy` | Cambios de despliegue (launchd, CI, empaquetado) |

## Reglas del proyecto

- **Idioma:** el código, los comentarios, los mensajes de la interfaz y los
  commits van en español (ver `CLAUDE.md`).
- **Nada de datos del usuario en el repo:** `tablero.db*` y `.env` están en
  `.gitignore` y deben seguir estándolo. La base contiene transacciones reales.
- **Los tests deben pasar** (`pytest -q`) y `ruff check .` debe estar limpio
  antes de cada commit.
- Un commit = un cambio con sentido propio. Si la descripción necesita una "y",
  probablemente son dos commits.
