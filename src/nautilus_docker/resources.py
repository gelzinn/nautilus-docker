"""Shared resource descriptions and application styles."""

import json
from importlib.resources import files

RESOURCE_DIR = files("nautilus_docker") / "resources"
SECTIONS = json.loads((RESOURCE_DIR / "sections.json").read_text())


def stylesheet():
    return (RESOURCE_DIR / "style.css").read_text()
