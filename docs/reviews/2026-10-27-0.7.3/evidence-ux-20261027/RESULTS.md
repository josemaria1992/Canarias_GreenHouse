# Block prerequisites and imported-state observations

Baseline: `0417bc2` on `feat/operator-upgrade-0.7`; application source is unchanged from reviewed 0.7.5. No signed-in real-bench inventory was inspected for this check. Python 3.12.14; fake installation and supplied legacy fixture only.

```text
PYTHONPATH=src;.
pytest -q -s -o addopts='' <absolute-path>/test_ux_block_prerequisites_20261027.py

BLOCK_OPTIONS equipment_items=6
  before_drawing: supply=0, destination=0, flow_meter=0
  after_drawing:  supply=2, destination=2, flow_meter=1

LEGACY_STATES
  graph_kinds: sensor=10, meter=7, pump=7, valve=9
  unmapped_kinds: tank=5, sector=2, source=1
  kept: symbols=33, placeholders=8, texts=5, shapes=1,
        routes_kept=4, routes_corrected=0, sketches=15

2 observations passed in 16.86 seconds
```

The assertions document why the current fields can be empty; they are not acceptance of the UX. One existing Starlette/httpx deprecation warning was emitted. The fake test saves a drawing directly to isolate the selector dependency; owner-facing authoring must still use Twin and its export/review flow. No live graph, account, channel or hardware state was changed.
