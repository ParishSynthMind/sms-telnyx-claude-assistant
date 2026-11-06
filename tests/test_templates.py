import json
import os

import pytest

from smsassist import templates
from tests.conftest import ROOT

T = templates.load(os.path.join(ROOT, "data", "templates.json"))


def test_render_fills_placeholders():
    out = templates.render(T, "appointment_reminder", {"business": "Brightside", "name": "Sam", "when": "Tue 3pm"})
    assert out.startswith("Brightside: hi Sam") and "Tue 3pm" in out and "{" not in out


def test_missing_value_is_an_error():
    with pytest.raises(templates.TemplateError, match="when"):
        templates.render(T, "appointment_reminder", {"business": "B", "name": "Sam"})


def test_unknown_template_lists_the_real_ones():
    with pytest.raises(templates.TemplateError, match="follow_up"):
        templates.render(T, "nope", {})


def test_values_are_flattened_and_capped():
    out = templates.render(T, "follow_up", {"business": "B", "name": "Sam\nSmith " + "x" * 200})
    assert "\n" not in out.split(": ", 1)[1].split(",")[0]
    assert len(out) < 300


def test_marketing_templates_carry_opt_out_wording():
    """Carriers expect opt-out instructions on messages a business starts."""
    for name in ("appointment_reminder", "follow_up", "missed_call", "start_confirmation", "help"):
        assert "STOP" in T[name], name


def test_templates_file_is_valid_json_of_strings():
    with open(os.path.join(ROOT, "data", "templates.json")) as f:
        data = json.load(f)
    assert all(isinstance(v, str) for v in data.values())
