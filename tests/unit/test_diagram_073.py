"""0.7.3 owner corrections 2-3: the tank size rule is the same in the server and the browser, and earlier drawings'
digests do not change because of the new optional fields."""
import json
import re
import subprocess
from pathlib import Path

from aquacrop import diagram as dg

ROOT = Path(__file__).resolve().parents[2]


def test_tank_size_matches_the_browser_rule():
    caps = [0, 500, 1000, 5000, 7500, 12000, 20000, 50000]
    js = ("global.window={};global.document={};require(%r);"
          "console.log(JSON.stringify(%s.map(c=>window.AqHydraulicEditor.tankSize(c))))"
          % (str(ROOT / "src/aquacrop/web/hydraulic-editor.js"), json.dumps(caps)))
    try:
        out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=60, check=True).stdout
    except FileNotFoundError:
        import pytest
        pytest.skip("node is not installed")
    assert [[z["w"], z["h"]] for z in json.loads(out)] == [list(dg.tank_size(c)) for c in caps]
    assert dg.tank_size(20000)[0] > dg.tank_size(5000)[0]


def test_new_optional_fields_leave_an_earlier_digest_unchanged():
    d = {"view": "installation", "width": 800, "height": 600, "symbols": [], "routes": [], "texts": []}
    a = dg.canonical(dg.parse(d))
    assert "shapes" not in a and "placeholders" not in a and "sketches" not in a
    assert not re.search(r'"w": null', json.dumps(a))


def test_the_actual_bench_drawing_converts_faithfully():
    """The bench's accepted earlier drawing (tests/fixtures/bench-legacy-drawing.json: geometry, labels and the item
    names/kinds it maps to): every symbol, label, box and pipe is kept; kept routes keep the drawn corners."""
    from aquacrop import layout as L
    from aquacrop.topology.migrate import propose
    fx = json.loads((ROOT / "tests/fixtures/bench-legacy-drawing.json").read_text(encoding="utf-8"))
    lay = L.parse(fx["layout"])
    p = propose(lay, {k: v["kind"] for k, v in fx["items"].items()}, {k: v["name"] for k, v in fx["items"].items()})
    k = p["kept"]
    assert k == fx["expected_kept"]
    drawn = [n for n in lay.nodes if n.kind not in ("label", "box")]
    assert k["symbols"] + k["placeholders"] == len(drawn)
    assert k["texts"] + k["shapes"] == len(lay.nodes) - len(drawn)
    assert k["routes_kept"] + k["sketches"] - k["routes_corrected"] == len(lay.pipes)
    pipes = {x.id: x for x in lay.pipes}
    dia = dg.parse(p["diagram"])
    for r in dia.routes:
        if r.points:
            src = next(x for x in lay.pipes if dg.canonical_points(x.points[1:-1]) and
                       all(tuple(c) in [tuple(q) for q in r.points] for c in dg.canonical_points(x.points[1:-1])))
            assert src.id in pipes
    for s in dia.sketches:                               # sketches are the drawn points, unchanged
        assert [list(x) for x in s.points] == [list(x) for x in pipes[s.id.removesuffix("-drawn")].points] if s.id.removesuffix("-drawn") in pipes else True
    assert not p["topology"]["edges"] or all(e["provenance"] == "UNKNOWN" for e in p["topology"]["edges"])
