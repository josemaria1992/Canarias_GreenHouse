"""0.7.3 PROG-09 (drainage lead alternation and standby response) and PROG-10 (labelled simulated nutrient
preparation; real dosing stays blocked), through the engine and the authoring compiler."""
import pytest

from aquacrop.blocks.definitions import BlockBody, digest as block_digest
from aquacrop.programs.definitions import ProgramBody, parse
from aquacrop.twin.nutrient_sim import Preparation, simulate


def _body(**kw):
    d = {"trigger": {"type": "MANUAL"},
         "action": {"type": "SEQUENCE", "max_runtime_s": 600,
                    "steps": [{"do": "ON", "item": "pump-a"}, {"do": "RUN", "seconds": 60}, {"do": "OFF", "item": "pump-a"}]}}
    return parse(d | kw)


def test_new_fields_leave_earlier_digests_unchanged():
    assert "duty" not in _body().model_dump(mode="json") and "simulation_only" not in _body().model_dump(mode="json")
    assert "duty" not in BlockBody().model_dump(mode="json") or block_digest(BlockBody()) == block_digest(
        BlockBody.model_validate({}))


def test_nutrient_simulation_is_labelled_and_responds():
    r = simulate(Preparation(volume_L=1000, additions=[{"at_s": 0, "what": "A", "litres": 5},
                                                       {"at_s": 0, "what": "B", "litres": 5},
                                                       {"at_s": 600, "what": "ACID", "litres": 0.5}], duration_s=1800))
    assert "SIMULATED" in r["label"] and "dosing" in r["label"] and r["fixture"] == "synthetic-carbonate-ec-v1"
    s = r["samples"]
    assert s[0]["ec_bulk"] > s[0]["ec_probe"] + 0.5                      # stock raises EC; the probe has not seen it yet
    assert r["final"]["ph_bulk"] < s[0]["ph_bulk"]                         # acid lowers pH
    assert s[0]["ec_probe"] < s[3]["ec_probe"] < s[3]["ec_bulk"]          # the probe lags, then follows


# ---------------------------------------------------------------- the engine
from aquacrop.programs.engine import Refused  # noqa: E402
from tests.unit.test_program_engine import Rig, view  # noqa: E402


def _pumps():
    return {"pump-a": view("pump-a", "pump", {"start": "digital_A.DO1"}, limits={"max_runtime_s": 3600}),
            "pump-b": view("pump-b", "pump", {"start": "digital_A.DO3", "fault": "digital_A.DI4"},
                           limits={"max_runtime_s": 3600})}


def _on(r, op_id):
    return next(s.item for s in r.e.ops[op_id].body.action.steps if s.do == "ON") if op_id in r.e.ops else None


def test_lead_alternates_and_the_standby_takes_a_start_the_lead_cannot():
    r = Rig(_pumps(), {"drain": (1, _body(duty=("pump-a", "pump-b")))})
    r.twin.set_input("digital_A.DI4", False)                              # pump-b healthy
    r.run(1)
    r.e.approve_recovery("t", "MANUAL")
    leads = []
    for _ in range(3):
        op = r.e.run_now("t", "drain")
        r.run(2)
        leads.append(next(e["lead"] for k, e in reversed(r.events) if k == "PROGRAM_OP_STARTED"))
        r.e.stop_op("t", op["op_id"])
        r.run(5)
    assert leads == ["pump-a", "pump-b", "pump-a"]
    assert r.twin.s.coils["digital_A.DO3"] is False and r.twin.s.coils["digital_A.DO1"] is False
    r.twin.set_fault("pump-b", "TRIP")                                    # pump-b trips: it was due to lead
    r.run(1)
    r.e.run_now("t", "drain")
    r.run(2)
    lead = next(e for k, e in reversed(r.events) if k == "PROGRAM_STANDBY_LEADS")
    assert lead["lead"] == "pump-a" and lead["instead_of"] == "pump-b" and "fault" in lead["why"]
    assert r.twin.s.coils["digital_A.DO1"] and not r.twin.s.coils["digital_A.DO3"]


def test_simulation_only_never_starts_on_real_equipment():
    r = Rig(_pumps(), {"prep": (1, _body(simulation_only="NUTRIENT_PREPARATION"))})
    r.e.physical = True
    r.run(1)
    r.e.approve_recovery("t", "MANUAL")
    with pytest.raises(Refused, match="real dosing stays blocked"):
        r.e.run_now("t", "prep")
    r.e.physical = False                                                  # the twin runs it
    r.e.run_now("t", "prep")
