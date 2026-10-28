# Independent evidence at 7a70ee6

Python 3.12.14, archived exact source, `PYTHONPATH=src;.`. Temporary databases, fake item views and fake Modbus only. No application source edits.

## Existing checks

```text
pytest -q -o addopts='' tests/integration/test_program_problems_073.py tests/integration/test_block_conditions_073.py tests/unit/test_diagram_073.py tests/unit/test_prog0910_073.py tests/integration/test_import01_073.py tests/integration/test_hist03_data01_073.py tests/hardware/test_twin_design_071.py
18 passed in 23.00 s

pytest -q -o addopts='' tests/hardware/test_proof_context_r07.py tests/hardware/test_journal_rereview_rr.py tests/unit/test_stop_plans_r07.py tests/unit/test_windows_071.py
31 passed in 37.63 s

node --test <all tests/js/*.test.js files>
17 passed; 0 failed
```

Each Python run emitted the existing Starlette/httpx deprecation warning. These are targeted checks, not a full-suite or final-CI result.

## Independent observations

The included Python probe imports the exact baseline's test fixtures. Run from the baseline checkout with `PYTHONPATH=src;.` and pass the probe's absolute path to pytest:

```text
pytest -q -s -o addopts='' <absolute-path>/test_review075_observations.py
PROOF ['pump-a'] unchanged_after_standby_rebind True
EXPORT legacy_nodes 47 explicit False exported_symbols 0
IMPORT_STALE_PREVIEW 200 [{'kind': 'block', 'id': 's1', 'status': 'DRAFT'}]
IMPORT_MALFORMED 500
HISTORY relay_B.DO6 []
5 observations reproduced in 7.76 s

node <absolute-path>/review075-editor-observations.cjs <absolute-baseline-checkout>
DUTY_PICKER after selecting both pumps: null
REJECTED_TANK_MOVE {"moved":false,"tankX":100,"sensorBefore":120,"sensorAfter":1220}
2 observations reproduced
```

The probes intentionally assert defective baseline behavior. Do not wire them into acceptance unchanged; write regressions that require corrected behavior. The history test forces identical timestamps to exercise the ordering ambiguity deterministically. It does not write production history.

## Compatibility, deployment and CI limits

- All 116 `src/**/*.py` files parsed with `ast.parse(..., feature_version=(3,11))`: zero syntax failures. This checks grammar, not native Python 3.11 execution or release dependencies.
- Main and Twin bench health requests both failed from this session (`An invalid argument was supplied. (aquacrop-bench.local:443)`). No authenticated/physical actions were attempted; this result does not diagnose a bench outage.
- Last checked PR head: `7a70ee6026eef2a53214b127087f4d0f8e6bb7b2`; 2 completed successes, 4 running, 30 queued. CI is a snapshot, not a final outcome.
- Running build, release artifact checksum and backup/rollback claims in `CLAUDE_UPDATE.md` were not independently verified.
