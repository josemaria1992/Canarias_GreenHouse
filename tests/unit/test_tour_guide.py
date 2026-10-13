"""The illustrated owner tour is built from the walkthrough itself: every case, its result line NOT RUN, and
list items that wrap onto indented lines stay one item (DEL-16)."""
import importlib.util
import re

from tests.conftest import ROOT

_spec = importlib.util.spec_from_file_location("make_tour_guide", ROOT / "scripts" / "ui_check" / "make_tour_guide.py")
guide = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guide)


def test_wrapped_list_items_stay_one_item():
    md = "- **Part B.** Sixteen cases, each with the exact preset,\n  buttons and expected result.\n\nNext paragraph.\n"
    out = guide.markdown(md, lambda case: "")
    assert "<li><strong>Part B.</strong> Sixteen cases, each with the exact preset, buttons and expected result.</li>" in out
    assert out.count("<p>") == 1


def test_walkthrough_has_no_broken_list_paragraphs_and_every_case():
    md = (ROOT / "docs" / "operator" / "walkthrough.md").read_text(encoding="utf-8")
    out = guide.markdown(md, lambda case: f'<div class="fig">{case}</div>')
    assert not re.findall(r"</(?:ul|ol)>\n<p>[a-z]", out)
    assert [f"UAT-{i:02d}" for i in range(1, 17)] == re.findall(r'<div class="fig">(UAT-\d\d)</div>', out)
    assert out.count("NOT RUN</strong>") >= 16
