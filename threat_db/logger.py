import logging
import os

from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme

custom_theme = Theme({"info": "cyan", "warning": "purple4", "danger": "bold red"})
console = Console(
    log_time=False,
    log_path=False,
    theme=custom_theme,
    width=280,
    color_system="auto",
    record=True,
)

logging.basicConfig(
    level=logging.WARNING,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[
        RichHandler(
            console=console, markup=True, show_path=False, enable_link_path=False
        )
    ],
)
LOG = logging.getLogger(__name__)

# Set logging level
if os.getenv("LOGGING_LEVEL") == "debug":
    LOG.setLevel(logging.DEBUG)
else:
    LOG.setLevel(logging.INFO)
DEBUG = logging.DEBUG
WARNING = logging.WARNING
try:
    os.environ["PYTHONIOENCODING"] = "utf-8"
except Exception:
    pass
