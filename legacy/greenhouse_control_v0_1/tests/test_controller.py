import asyncio
import tempfile
from pathlib import Path

from greenhouse.config import Config
from greenhouse.controller import Controller


def make_controller(tmp):
    c = Config(db_path=str(Path(tmp) / 'test.sqlite'), password='the-test-password')
    c.validate()
    return Controller(c)


def test_simulation_all_channels_and_history():
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            c = make_controller(tmp)
            await c.start()
            assert len(c.state()['io']['analog']) == 8
            assert len(c.state()['io']['digital_inputs']) == 8
            assert len(c.state()['io']['relays']) == 8
            await c.set_input(7, True, 'test')
            await c.set_relay(7, True, 'test')
            assert c.state()['io']['relays'][7]
            assert c.state()['io']['digital_inputs'][7]
            assert len(c.storage.samples()) >= 3
            assert c.storage.samples()[-1]['relays_json'][7] is True
            assert 'DO_8' in c.storage.export_csv()
            await c.stop()
    asyncio.run(run())


def test_auto_virtual_not_mapped_to_relays():
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            c = make_controller(tmp)
            await c.start()
            await c.set_mode('AUTO', 'test')
            assert c.state()['sector'] == 1
            assert c.state()['phase'] == 'IRRIGATING'
            assert not any(c.state()['io']['relays'])
            try:
                await c.set_relay(0, True, 'test')
                assert False, 'Manual relay write should be forbidden in AUTO'
            except PermissionError:
                pass
            c.phase_started -= 50
            await c.poll_once()
            assert c.state()['phase'] == 'PAUSE'
            c.phase_started -= 50
            await c.poll_once()
            assert c.state()['phase'] == 'IRRIGATING'
            assert c.state()['sector'] == 2
            assert not any(c.state()['io']['relays'])
            await c.stop()
    asyncio.run(run())


def test_out_of_range_stops_auto():
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            c = make_controller(tmp)
            await c.start()
            await c.set_mode('AUTO', 'test')
            await c.set_analog(0, 3000, 'test')
            assert c.state()['mode'] == 'MANUAL'
            assert c.state()['phase'] == 'STOPPED_ALARM'
            assert any('AI1' in a for a in c.state()['alarms'])
            await c.set_analog(0, 12000, 'test')
            assert not c.state()['alarms']
            assert any(e['type'] == 'ALARM_CLEARED' for e in c.storage.events())
            await c.stop()
    asyncio.run(run())


def test_overflow_stops_auto():
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            c = make_controller(tmp)
            await c.start()
            await c.set_mode('AUTO', 'test')
            await c.set_flag('overflow', True, 'test')
            assert 'SIMULATED tank overflow' in c.state()['alarms']
            assert c.state()['mode'] == 'MANUAL'
            await c.stop()
    asyncio.run(run())


def test_real_requires_unique_addresses():
    with tempfile.TemporaryDirectory() as tmp:
        try:
            Config(backend='modbus', password='a-long-password', serial_port='/dev/ttyUSB0',
                   relay_id=1, analog_id=1, db_path=str(Path(tmp)/'x.sqlite')).validate()
            assert False, 'duplicate IDs should be rejected'
        except ValueError:
            pass
