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
    # a case's result is recorded in the result form, never in a printed box
    assert out.count('href="results.html#UAT-') == 16 and "☐" not in out


def test_the_result_form_starts_not_run_and_keeps_the_first_time_operator_apart():
    md = (ROOT / "docs" / "operator" / "walkthrough.md").read_text(encoding="utf-8")
    page = guide.results_page(md, "0.3.3")
    assert page.count('value="NOT RUN" checked') == 16 and page.count('value="PASS"') == 16
    assert [f'id="UAT-{i:02d}"' in page for i in range(1, 17)] == [True] * 16
    assert page.count('value="Not tried" checked') == 4 and "Recover from a simulated problem" in page
    assert 'id="owner-part"' in page and 'id="operator-part" hidden' in page   # one record per person, kept apart
    assert "This page keeps nothing" in page and "Download results" in page
    assert not re.search(r"https?://", page)                                   # offline
    assert "localStorage" not in page
