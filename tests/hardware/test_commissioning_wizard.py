"""0.5.0 Hardware → Add module wizard over the in-memory RS485 buses (not hardware): one fake USB adapter bus
for the new module and the fake installation bus of `fake_bus`."""
import itertools

import pytest

from aquacrop.commissioning.adapters import Adapter
from aquacrop.commissioning.wizard import CONFIRM_PHRASE, Wizard, WizardRefused
from aquacrop.drivers import setup as hw
from aquacrop.historian.writer import Historian
from tests.fakes import protocol_aquafake as pf

_n = itertools.count()


def adapter(path, **kw):
    return Adapter(path=path, by_id=kw.get("by_id"), manufacturer="FTDI", product="FT232R USB UART", serial="BG03Z87K",
                   vendor_id="0403", product_id="6001", driver="ftdi_sio", readable=kw.get("readable", True),
                   writable=kw.get("writable", True), used_by_bus=kw.get("used_by_bus"), owned_by_other=False)


@pytest.fixture
def rig(tmp_path, fake_bus, monkeypatch):
    cfg, inst, relay, ai = fake_bus
    name = f"usb{next(_n)}"
    usb = pf.bus(name)
    port = f"aquafake://{name}"
    adapters = [adapter(port, by_id="/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_BG03Z87K-if00-port0")]
    h = Historian(tmp_path / "db.sqlite3", cfg, "s1")
    probes = []

    def probe(bus_id, unit):
        probes.append((bus_id, unit))
        b = hw._bus(cfg.buses[0].port, 9600, 0.05)
        try:
            return hw.identify(b, unit) if hw.read_one(b, unit, hw.ADDRESS_REGISTER) is not None else None
        finally:
            b.close()
    wz = Wizard(h, cfg, state_dir=tmp_path, lock_root=tmp_path / "locks", probe_destination=probe,
                adapter_list=lambda: adapters, scan_last=8)
    yield wz, usb, inst, port, adapters, probes, h
    h.close()
    pf.BUSES.pop(name, None)


def prepare(wz, port, *, sku="26244", name="digital_C", ai_variant=None, ai_range=None, address=None):
    tx = wz.open("bench1", port, "rs485_a")["tx_id"]
    assert wz.read("bench1", tx, only_new_module=CONFIRM_PHRASE)["state"] == "READ"
    wz.identify("bench1", tx, name=name, sku=sku, label=name, ai_variant=ai_variant, ai_range=ai_range)
    a = address or wz.suggest(tx)["address"]
    wz.reserve("bench1", tx, a)
    return tx


def test_adapter_list_never_offers_an_installation_port(rig):
    wz, usb, inst, port, adapters, _, _ = rig
    adapters.append(adapter("/dev/ttyUSB1", used_by_bus="rs485_a"))
    adapters.append(adapter("/dev/ttyUSB2", readable=False, writable=False))
    got = {a["path"]: a for a in wz.adapters()}
    assert got[port]["selectable"] and got[port]["stable_id"].startswith("/dev/serial/by-id/")
    assert not got["/dev/ttyUSB1"]["selectable"] and "rs485_a" in got["/dev/ttyUSB1"]["why_not"]
    assert not got["/dev/ttyUSB2"]["selectable"] and "permission" in got["/dev/ttyUSB2"]["why_not"]
    assert "not the module" in got[port]["note"]
    with pytest.raises(WizardRefused, match="not plugged"):
        wz.open("bench1", "/dev/ttyUSB9", "rs485_a")          # removed meanwhile


def test_full_journey_prepares_one_module_and_never_touches_existing_ones(rig):
    wz, usb, inst, port, _, probes, _ = rig
    new = usb.add("digital_io", 1)
    new.modes[4] = 2                                       # one channel shipped in toggle mode
    before = {a: (d.address, list(d.modes), list(d.coils)) for a, d in inst.devices.items()}
    tx = wz.open("bench1", port, "rs485_a")["tx_id"]
    with pytest.raises(WizardRefused, match="type exactly"):
        wz.read("bench1", tx, only_new_module="yes")
    v = wz.read("bench1", tx, only_new_module=CONFIRM_PHRASE)
    assert v["state"] == "READ" and v["found"][0]["kind"] == "digital"
    with pytest.raises(WizardRefused, match="not digital|looks digital"):
        wz.identify("bench1", tx, name="analog_C", sku="25821", label="analog_C", ai_variant="A", ai_range="4-20mA")
    with pytest.raises(WizardRefused, match="already a module"):
        wz.identify("bench1", tx, name="relay_A", sku="26517", label="relay_A")
    wz.identify("bench1", tx, name="digital_C", sku="26244", label="digital_C")
    s = wz.suggest(tx)
    assert s["address"] == 7 and "not proof" in s["note"]
    for bad, why in ((0, "1..247"), (248, "1..247"), (1, "configured: relay_A"), (2, "configured: analog_A")):
        with pytest.raises(WizardRefused, match=why):
            wz.reserve("bench1", tx, bad)
    v = wz.reserve("bench1", tx, 9)                         # a custom address
    assert v["state"] == "RESERVED" and v["preview"]["writes"] == ["mode register 0x1004 = 0",
                                                                  "address register 0x4000: 1 → 9"]
    assert usb.write_frames() == [] and inst.write_frames() == []
    v = wz.commit("bench1", tx, confirm=True)
    assert v["state"] == "PREPARED" and new.address == 9 and new.modes == [0] * 8
    assert [w[:6].hex() for w in usb.write_frames()] == ["010610040000", "010640000009"]   # unit 1 only, no broadcast
    assert all(f[0] != 0 for f in usb.frames_in) and not any(f[1] == 0x05 for f in usb.frames_in)   # no output ON/OFF
    assert inst.write_frames() == []
    assert {a: (d.address, list(d.modes), list(d.coils)) for a, d in inst.devices.items()} == before
    assert v["move_steps"] and "maintenance stop" in " ".join(v["move_steps"])
    # moved to the installation bus
    del usb.devices[9]
    inst.devices[9] = new
    v = wz.detect("bench1", tx)
    assert v["state"] == "DETECTED"
    assert inst.write_frames() == []
    doc = wz._load(tx)["doc"]["proposal"]
    assert doc["device"]["address"] == 9 and doc["device"]["model"] == "WAVESHARE_MODBUS_RTU_IO_8CH"
    from aquacrop.config import load_config
    assert any(d.device_uid == "digital_C" for d in load_config(doc["path"]).devices)


def test_two_responders_or_none_are_refused(rig):
    wz, usb, inst, port, _, _, _ = rig
    tx = wz.open("bench1", port, "rs485_a")["tx_id"]
    v = wz.read("bench1", tx, only_new_module=CONFIRM_PHRASE)
    assert v["state"] == "OPEN" and "no module answers" in v["log"][-1]["reason"]
    usb.add("digital_io", 1)
    usb.add("ai", 3)
    v = wz.read("bench1", tx, only_new_module=CONFIRM_PHRASE)
    assert v["state"] == "OPEN" and "2 modules answer" in v["log"][-1]["reason"]
    assert usb.write_frames() == []


def test_an_address_answering_on_the_destination_is_never_reserved(rig):
    wz, usb, inst, port, _, _, _ = rig
    usb.add("digital_io", 1)
    inst.add("digital_io", 7)                                # an unconfigured device already sits at 7
    tx = prepare(wz, port, address=8)
    assert wz.view(tx)["address"] == 8
    w = wz.suggest(tx)                                       # 7 answers; 8 is this module's own reservation
    assert w["address"] == 8 and {"address": 7, "answers": True} in w["probed"]
    with pytest.raises(WizardRefused, match="answers at address 7"):
        wz.reserve("bench1", tx, 7)


def test_concurrent_wizards_cannot_reserve_the_same_address(rig, tmp_path):
    wz, usb, inst, port, adapters, _, h = rig
    usb.add("digital_io", 1)
    tx = prepare(wz, port, address=7)
    name2 = f"usb{next(_n)}"
    usb2 = pf.bus(name2)
    usb2.add("digital_io", 1)
    port2 = f"aquafake://{name2}"
    adapters.append(adapter(port2))
    tx2 = wz.open("bench1", port2, "rs485_a")["tx_id"]
    wz.read("bench1", tx2, only_new_module=CONFIRM_PHRASE)
    wz.identify("bench1", tx2, name="digital_D", sku="26244", label="digital_D")
    with pytest.raises(WizardRefused, match="reserved by another module"):
        wz.reserve("bench1", tx2, 7)
    assert wz.suggest(tx2)["address"] == 8
    # the same adapter cannot be opened twice
    with pytest.raises(WizardRefused):
        wz.open("bench1", port, "rs485_a")
    pf.BUSES.pop(name2, None)


def test_lost_answer_after_the_address_write_is_uncertain_and_keeps_the_address(rig):
    wz, usb, inst, port, _, _, _ = rig
    new = usb.add("digital_io", 1)
    tx = prepare(wz, port, address=7)
    orig = usb.respond

    def lossy(frame):          # the module applies the address change, then stops answering
        out = orig(frame)
        if frame[1] == 0x06 and frame[2:4] == b"\x40\x00":
            new.fault = "timeout"
        return out
    usb.respond = lossy
    v = wz.commit("bench1", tx, confirm=True)
    assert v["state"] == "UNCERTAIN" and v["recovery"] and v["address"] == 7
    with pytest.raises(WizardRefused, match="cannot be cancelled"):
        wz.cancel("bench1", tx, "give up")
    writes = len(usb.write_frames())
    assert wz.recheck("bench1", tx)["state"] == "UNCERTAIN"        # still silent: still uncertain
    new.fault = None                                                 # power cycled
    assert wz.recheck("bench1", tx)["state"] == "PREPARED"
    assert len(usb.write_frames()) == writes                         # re-check never writes


def test_cancel_and_restart_leave_records(rig, tmp_path):
    wz, usb, inst, port, _, probes, h = rig
    usb.add("digital_io", 1)
    tx = prepare(wz, port, address=7)
    assert wz.cancel("bench1", tx, "wrong module")["state"] == "CANCELLED"
    # the address is free again and the adapter is released
    tx2 = prepare(wz, port, address=7)
    h.submit(lambda c: c.execute("UPDATE commissioning_tx SET state='WRITING' WHERE tx_id=?", (tx2,)), wait=True)
    wz2 = Wizard(h, wz.cfg, state_dir=tmp_path, lock_root=tmp_path / "locks2", probe_destination=wz.probe_destination,
                 adapter_list=wz.adapter_list)
    assert wz2.startup() == [tx2] and wz2.view(tx2)["state"] == "UNCERTAIN"
    assert [x["state"] for x in wz2.list()][:2] == ["UNCERTAIN", "CANCELLED"]


def test_analog_module_needs_its_variant_and_range(rig):
    wz, usb, inst, port, _, _, _ = rig
    m = usb.add("ai", 1)
    tx = wz.open("bench1", port, "rs485_a")["tx_id"]
    wz.read("bench1", tx, only_new_module=CONFIRM_PHRASE)
    with pytest.raises(WizardRefused, match="range"):
        wz.identify("bench1", tx, name="analog_C", sku="25821", label="analog_C", ai_variant="A")
    with pytest.raises(WizardRefused, match="version printed"):
        wz.identify("bench1", tx, name="analog_C", sku="25821", label="analog_C", ai_range="4-20mA")
    wz.identify("bench1", tx, name="analog_C", sku="25821", label="analog_C", ai_variant="A", ai_range="4-20mA")
    wz.reserve("bench1", tx, 7)
    assert wz.commit("bench1", tx, confirm=True)["state"] == "PREPARED" and m.modes == [3] * 8 and m.address == 7
