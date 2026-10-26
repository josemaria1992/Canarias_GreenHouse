"""0.5.0 wizard and discovery through the HTTP API of a hardware profile, on the in-memory buses (not hardware).
The destination check and discovery go through the installation bus's own worker."""
import itertools
import time

from aquacrop.commissioning.adapters import Adapter
from aquacrop.commissioning.wizard import CONFIRM_PHRASE
from tests.fakes import protocol_aquafake as pf
from tests.hardware.test_api_hardware import hw, login  # noqa: F401  (hw is a fixture)

_n = itertools.count()


def wait(pred, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def usb_adapter(app):
    name = f"usbapi{next(_n)}"
    usb = pf.bus(name)
    port = f"aquafake://{name}"
    a = Adapter(path=port, by_id=None, manufacturer="FTDI", product="FT232R", serial="X", vendor_id="0403",
                product_id="6001", driver="ftdi_sio", readable=True, writable=True, used_by_bus=None,
                owned_by_other=False)
    app.state.wizard.adapter_list = lambda: [a]
    app.state.wizard.scan_last = 4
    return usb, port


def test_the_wizard_needs_the_commissioning_flag_and_writes_only_on_the_adapter(hw):
    app, c, bus, relay, ai = hw
    usb, port = usb_adapter(app)
    new = usb.add("digital_io", 1)
    h = login(c, "oper1")                                  # operator without commissioning
    ov = c.get("/api/commissioning", headers=h).json()
    assert ov["available"] and ov["adapters"][0]["selectable"] and "not into this PC" in ov["where"]
    assert c.post("/api/commissioning/open", json={"adapter": port, "bus_id": "rs485_a"}, headers=h).status_code == 403
    c.post("/api/auth/logout", headers=h)
    h = login(c, "bench1")                                 # 0.7.0: the commissioning flag no longer writes modules
    assert c.post("/api/commissioning/open", json={"adapter": port, "bus_id": "rs485_a"}, headers=h).status_code == 403
    c.post("/api/auth/logout", headers=h)
    h = login(c, "admin1")
    v = c.post("/api/commissioning/open", json={"adapter": port, "bus_id": "rs485_a"}, headers=h).json()
    tx = v["tx_id"]
    assert v["state"] == "READ"                              # Start reads the module at once (owner, 2026-10-24)
    assert c.get(f"/api/commissioning/{tx}/suggest", headers=h).json()["address"] == 7   # before naming it
    assert c.post(f"/api/commissioning/{tx}/read", json={"confirmation": CONFIRM_PHRASE}, headers=h).json()["state"] == "READ"
    assert c.post(f"/api/commissioning/{tx}/identity", json={"name": "digital_C", "sku": "26244", "label": "digital_C"},
                  headers=h).json()["state"] == "IDENTIFIED"
    s = c.get(f"/api/commissioning/{tx}/suggest", headers=h).json()
    assert s["address"] == 7
    r = c.post(f"/api/commissioning/{tx}/reserve", json={"address": 1}, headers=h)
    assert r.status_code == 409 and "relay_A" in r.text
    assert c.post(f"/api/commissioning/{tx}/reserve", json={"address": 7}, headers=h).json()["state"] == "RESERVED"
    v = c.post(f"/api/commissioning/{tx}/commit", json={"confirm": True}, headers=h).json()
    assert v["state"] == "PREPARED" and new.address == 7
    assert bus.write_frames() == []                         # the installation bus saw reads only
    del usb.devices[7]
    bus.devices[7] = new
    v = c.post(f"/api/commissioning/{tx}/detect", headers=h).json()
    assert v["state"] == "DETECTED"
    assert bus.write_frames() == [] and not any(f[1] == 0x05 for f in usb.frames_in)
    assert app.state.svc.workers["rs485_a"].health()["ok"]
    pf.BUSES.pop(port.split("//")[1], None)


def test_discovery_reads_only_reports_new_and_known_and_keeps_polling(hw):
    app, c, bus, relay, ai = hw
    svc = app.state.svc
    svc.discovery.unit_range = (1, 4)
    for st in svc.discovery._state.values():
        st["cursor"] = 1
    stranger = bus.add("digital_io", 4)                     # answers but is not configured
    h = login(c, "bench1")
    assert c.post("/api/discovery/sweep", json={"bus_id": "rs485_a"}, headers=h).status_code == 200
    assert wait(lambda: c.get("/api/discovery", headers=h).json()["buses"]["rs485_a"]["last_full_utc"] is not None)
    rows = {r["address"]: r for r in c.get("/api/discovery", headers=h).json()["buses"]["rs485_a"]["rows"]}
    assert rows[1]["status"] == "KNOWN" and rows[2]["status"] == "KNOWN"
    assert rows[4]["status"] == "NEW" and rows[4]["seen_kind"] == "digital" and "no output permission" in rows[4]["note"]
    assert bus.write_frames() == [] and stranger.writes_applied == 0
    polls = svc.workers["rs485_a"].polls
    assert wait(lambda: svc.workers["rs485_a"].polls > polls + 2)   # acquisition keeps its period
    ev = svc.hist.query("SELECT count(*) AS n FROM events WHERE kind='POLL_OVERRUN'")
    assert ev[0]["n"] == 0
    # a configured module that stops answering is MISSING, and kept
    relay.fault = "timeout"
    assert wait(lambda: {r["address"]: r for r in c.get("/api/discovery", headers=h).json()["buses"]["rs485_a"]["rows"]}
                [1]["status"] == "MISSING")


def test_discovery_waits_when_the_poll_has_no_time_to_spare(hw):
    app, c, bus, relay, ai = hw
    d = app.state.svc.discovery
    d.request_full("rs485_a", "test")
    before = d._state["rs485_a"]["steps"]
    d.after_poll("rs485_a", poll_took_s=0.9, interval_s=1.0)
    st = d.view()["rs485_a"]
    assert st["held"] and "waits" in st["held"] and d._state["rs485_a"]["steps"] == before
