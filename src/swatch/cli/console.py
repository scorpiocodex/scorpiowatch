"""The single shared ``rich`` Console and Theme for the CLI.

Per ``UI_DESIGN.md`` §4.1 the palette is encoded once, in one ``rich.Theme``, and every
command imports its styles from here rather than hand-picking colors. Human-readable output
goes to :data:`console` (stdout); diagnostics and error framing go to :data:`err_console`
(stderr), keeping stdout a clean stream (``UI_DESIGN.md`` §4.3, and so ``--json`` output —
when it lands — is never interleaved with chrome).
"""

from rich.console import Console
from rich.theme import Theme

# Styles mirror the §2.1 palette: green success, red failure, orange/amber for change and
# warning, blue run IDs, dim for secondary text.
SWATCH_THEME = Theme(
    {
        "success": "bold green",
        "failure": "bold red",
        "warning": "yellow",
        "info": "cyan",
        "muted": "dim",
        "run_id": "blue",
        "event.added": "green",
        "event.modified": "dark_orange",
        "event.deleted": "red",
    }
)

console = Console(theme=SWATCH_THEME)
err_console = Console(theme=SWATCH_THEME, stderr=True)
