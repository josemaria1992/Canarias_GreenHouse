# Equipment-type observations at a8cdcde

Application source remains reviewed 0.7.5. No signed-in live-bench types were queried or changed. Python 3.12.14 and Node; fake installation plus execution of the real renderer function with a minimal container.

```text
PYTHONPATH=src;.
pytest -q -s -o addopts='' <absolute-path>/test_equipment_type_availability_20261027.py
TANK_TYPE built-in template present; initially absent;
  seeded as DRAFT (not selectable); ACTIVE after explicit activation;
  existing pump unchanged
1 observation passed in 5.79 seconds

node <absolute-path>/type_editor_refresh_observation_20261027.cjs <baseline-checkout>
TYPE_EDITOR_REFRESH {"before":[true,false],"after":[false,false]}
```

The renderer probe executes the source `mount` function and demonstrates duplicate-text state collision. It intentionally asserts current defective behavior; write corrected-behavior tests and browser acceptance for the fix. The Python run emitted the existing Starlette/httpx deprecation warning. These checks explain the lifecycle and reproduce a specific closing defect; they do not establish which lifecycle state the owner's Tank type currently has.
