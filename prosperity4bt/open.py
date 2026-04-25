"""Small helper for opening generated visualizer output."""

from pathlib import Path
import webbrowser


def open_visualizer(path: Path) -> None:
    """Open a generated log/HTML file with the system default application."""
    webbrowser.open(Path(path).resolve().as_uri())

