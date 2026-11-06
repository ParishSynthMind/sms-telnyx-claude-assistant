"""Message templates. Every placeholder must be filled, so a text never goes out
saying 'Hi {name}'."""
import json
import string
from pathlib import Path
from typing import Dict


class TemplateError(ValueError):
    pass


def load(path: str) -> Dict[str, str]:
    return json.loads(Path(path).read_text())


def fields(template: str) -> set:
    return {f for _, f, _, _ in string.Formatter().parse(template) if f}


def render(templates: Dict[str, str], name: str, values: Dict[str, str]) -> str:
    if name not in templates:
        raise TemplateError(f"unknown template {name!r}; have: {', '.join(sorted(templates))}")
    template = templates[name]
    missing = fields(template) - set(values)
    if missing:
        raise TemplateError(f"template {name!r} needs: {', '.join(sorted(missing))}")
    clean = {k: " ".join(str(v).split())[:80] for k, v in values.items()}  # one line, short
    return template.format(**clean)
