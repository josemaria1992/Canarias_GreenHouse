# Targeted review evidence

Source: `f91e34da58f55f4fa7fe725a532969439dbe6525` from [PR #11](https://github.com/josemaria1992/Canarias_GreenHouse/pull/11). Latest fetched branch head `3cc3c34f9943056948ec2098abeddc6f8170294a`, documentation-only delta.

Environment: Windows, Python 3.12.14 in the existing review venv; exact archived source, `PYTHONPATH=src;.`. Fake Modbus/TestClient services only. Node used for JavaScript tests. Ordinary tests emitted a Starlette/httpx deprecation warning.

Executed existing checks:

```text
pytest -q tests/integration/test_ids_071.py tests/integration/test_program_forms_070.py tests/unit/test_windows_071.py tests/unit/test_migration_071.py tests/hardware/test_twin_design_071.py tests/hardware/test_journal_rereview_rr.py tests/unit/test_stop_plans_r07.py
# 36 collected and passed

pytest -q -o addopts='' tests/unit/test_ids_071.py tests/integration/test_modules_071.py tests/hardware/test_commissioning_api.py tests/integration/test_blocks_070.py
# 10 passed

pytest -q -o addopts='' tests/hardware/test_twin_service.py::test_pages_name_their_scripts_by_build_and_say_which_build_answers
# 1 passed

node --test tests/js/router.test.js tests/js/editor_align.test.js
# 11 passed
```

Independent probe file included alongside this evidence: `test_owner_073_probes.py`. Run from the pinned source with `PYTHONPATH=src;.` and pass the probe file's absolute path to pytest. The assertions intentionally capture current behavior; convert the defect observations to expected corrected outcomes when implementing fixes.

Observed Preview API replies:

```json
{"status":422,"detail":{"message":"String should match pattern '^[a-z0-9][a-z0-9_\\-]{0,47}$'","field":"block_id"}}
{"status":422,"detail":{"message":"String should match pattern '^[a-z0-9][a-z0-9_\\-]{0,47}$'","field":"permissives.0.item"}}
{"status":422,"detail":{"message":"String should match pattern '^[a-z0-9][a-z0-9_\\-]{0,47}$'","field":"permissives.0.role"}}
```

Legacy probe: six input symbols; three output graph nodes; missing `Mains` source, `Main tank`, `Sector 1`; retained `p2` route has `points: []`. Explicit graph probe: exact canonical graph and diagram equality after import; zero additional fake-bus writes.

[Failed GitHub UI job](https://github.com/josemaria1992/Canarias_GreenHouse/actions/runs/40475446692/job/122092425512), retrieved via GitHub CLI job-log API:

```text
2026-10-27T08:46:59Z FAIL UI check ran to the end: locator.innerText: Timeout 30000ms exceeded.
  waiting for locator('#overview-sectors')
  scripts/ui_check/ui_check.cjs:668:52
2026-10-27T08:47:30Z FAIL HW drive interface: the overview lists drive:MAIN over Modbus with its PROPOSED map and the status word it reads
2026-10-27T08:47:32Z 81 passed, 2 failed
```

Bench health: HTTPS GET `/api/health` and `/twin/api/health`, each timed out after eight seconds from this session. No authenticated actions or output commands were issued. Deployment and release ZIP checksum remain author-reported.
