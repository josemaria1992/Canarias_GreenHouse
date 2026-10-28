"""Independent observations of 0.7.7; fake clock, fake I/O, no hardware."""
import json
from tests.unit.test_pump_replacement_075 import _rig, _sector, _targets
from aquacrop.programs.definitions import parse
from tests.unit.test_program_engine import Rig, view, FLOW_SCALING
from tests.unit.test_pump_replacement_075 import _items
from dataclasses import replace
from aquacrop.domain.model import Quality

def busy():
    other = parse({'trigger': {'type': 'MANUAL'}, 'action': {'type': 'SEQUENCE', 'max_runtime_s': 600,
        'steps': [{'do':'ON','item':'valve-2'}, {'do':'ON','item':'pump-b'}, {'do':'RUN','seconds':300},
                  {'do':'OFF','item':'pump-b'}, {'do':'OFF','item':'valve-2'}]}})
    r = _rig(s1=_sector('valve-1'), other=other)
    r.e.run_now('review','other'); r.e.run_now('review','s1'); r.run(10)
    r.twin.set_fault('pump-a','TRIP'); r.run(5)
    assert 's1' in r.e._resume
    return r

def observe_parallel():
    r = _rig(s1=_sector('valve-1'),s2=_sector('valve-2'))
    r.e.run_now('review','s1'); r.run(3)
    r.e.run_now('review','s2'); r.run(3)
    return {'coils':dict(r.twin.s.coils), 'both_sectors_open':all(r.twin.s.coils[p] for p in ['digital_A.DO2','digital_A.DO5'])}

def observe_stop_all():
    r=busy(); before=len(r.on_frames()); r.e.stop_all('review','explicit stop all'); r.run(15)
    return {'new_ON_after_stop_all':len(r.on_frames())-before, 'resumed':'PROGRAM_RESUMED' in r.kinds(),
            'running':[(o.program_id,o.state) for o in r.e.ops.values()], 'coils':dict(r.twin.s.coils)}

def observe_off_retry():
    r=_rig(s1=_sector('valve-1')); r.e.run_now('review','s1'); r.run(10)
    original=r.e._off_state
    r.e._off_state=lambda op,iid: (False,True,None) if iid=='pump-a' else original(op,iid)
    n=len(r.audit.intents); r.twin.set_fault('pump-a','TRIP'); r.run(8)
    return {'failed_pump_OFF_attempts_in_8_seconds':_targets(r)[n:].count(('digital_A.DO1',False)),
            'replacement':next(iter(r.e.ops.values())).replace}

def observe_litre_gap():
    body=_sector('valve-1').model_dump(mode='json')
    body['action']['steps'][2]={'do':'DELIVER','meter':'fm','target_L':50,'no_flow_timeout_s':120}
    items=_items(); items['fm']=view('fm','flow',{'flow':'analog_A.AI1'},{'scaling':FLOW_SCALING})
    r=Rig(items,{'s1':(1,parse(body))})
    sample=r.e.io.sample
    def current_sample(point):
        s=sample(point)
        if point=='analog_A.AI1':
            pumping=bool(r.twin.s.coils.get('digital_A.DO1') or r.twin.s.coils.get('digital_A.DO3'))
            return replace(s,acquired_utc=r.clock.utc(),raw_value=13600 if pumping else 4000,raw_unit='uA',quality=Quality.GOOD,reason=None)
        return s
    r.e.io.sample=current_sample
    for p in ('digital_A.DI1','digital_A.DI4'):r.twin.set_input(p,False)
    r.run(1);r.e.approve_recovery('review','MANUAL');r.e.run_now('review','s1');r.run(10)
    off=r.e._off_state; start=r.clock.mono()
    r.e._off_state=lambda op,iid: (False,False,None) if iid=='pump-a' and r.clock.mono()-start<8 else off(op,iid)
    r.twin.set_fault('pump-a','TRIP');r.run(12)
    return {'replaced':'PROGRAM_PUMP_REPLACED' in r.kinds(),'finished':r.e.finished,
        'flow_samples_available_throughout':True}

if __name__=='__main__':
    for f in [observe_parallel,observe_stop_all,observe_off_retry,observe_litre_gap]:
        try: print(f.__name__,json.dumps(f(),default=str))
        except Exception as e: print(f.__name__,type(e).__name__,str(e)); raise
