"""Independent boundary observations at e40285f. All equipment and clocks are fake."""
import json
from dataclasses import replace
from tests.unit.test_recovery_c077 import _weekly
from tests.unit.test_recovery_c077 import _litre_rig
from tests.unit.test_pump_replacement_075 import _rig,_sector,_items
from tests.unit.test_program_engine import Rig,view
from aquacrop.programs.definitions import parse
from aquacrop.equipment.definitions import TEMPLATES
from aquacrop.domain.model import Quality

def stopped_turn():
    r=_rig()
    r.programs.update(s1=(1,_weekly('valve-1',run_s=60)),s2=(1,_weekly('valve-2',run_s=60)))
    r.e.request_mode('review','AUTO');r.run(62)
    waiting=[v['pid'] for v in r.e._turn_wait.values()]
    before=len(r.on_frames());r.e.stop_all('review','explicit stop all');r.run(15)
    return {'waiting_before_stop':waiting,'new_ON_after_stop':len(r.on_frames())-before,
      'running':[(o.program_id,o.state) for o in r.e.ops.values()]}

def flow_units():
    body=_sector('valve-1').model_dump(mode='json')
    body['action']['steps'][2]={'do':'DELIVER','meter':'fm','target_L':50,'no_flow_timeout_s':120}
    items=_items()
    roles=[dict(x,unit='L/h') for x in TEMPLATES['flow']['body']['roles']]
    scaling={'flow':{'raw_min':4,'raw_max':20,'raw_unit':'mA','eng_min':0,'eng_max':1000,'unit':'L/h','provenance':'DEMO'}}
    items['fm']=view('fm','flow',{'flow':'analog_A.AI1'},{'scaling':scaling},roles=roles)
    r=Rig(items,{'p':(1,parse(body))});base=r.e.io.sample
    def sample(point):
        s=base(point)
        if point=='analog_A.AI1':
            on=r.twin.s.coils.get('digital_A.DO1') or r.twin.s.coils.get('digital_A.DO3')
            return replace(s,acquired_utc=r.clock.utc(),raw_value=13600 if on else 4000,raw_unit='uA',quality=Quality.GOOD,reason=None)
        return s
    r.e.io.sample=sample
    for p in ['digital_A.DI1','digital_A.DI4']:r.twin.set_input(p,False)
    r.run(1);r.e.approve_recovery('review','MANUAL');r.e.run_now('review','p')
    actual=0.0
    for _ in range(100):
        if r.twin.s.coils.get('digital_A.DO1') or r.twin.s.coils.get('digital_A.DO3'):actual+=600/3600*0.5
        r.run(0.5)
        if r.e.finished:break
    return {'sensor_unit':'L/h','rate':600,'actual_litres_at_done':actual,'result':r.e.finished[-1]}

def completed_during_changeover():
    r,op=_litre_rig(target=11,delay_s=20)
    base=r.e.io.command;t0=r.clock.mono()
    def slow_off(point,desired,**kw):
        if point=='digital_A.DO1' and desired is False and r.clock.mono()-t0<8:
            return type('Dropped',(),{'outcome':None,'detail':'dropped'})()
        return base(point,desired,**kw)
    r.e.io.command=slow_off
    r.run(16)
    before={'measured_L':op.measured_L,'requested_L':op.requested_L,'state':op.state,'replacement':dict(op.replace or {})}
    n=len(r.on_frames());r.run(10)
    return {'during':before,'additional_ON_after_target_already_met':len(r.on_frames())-n,'ended':r.e.finished}

if __name__=='__main__':
    for f in [stopped_turn,flow_units,completed_during_changeover]:print(f.__name__,json.dumps(f(),default=str))
