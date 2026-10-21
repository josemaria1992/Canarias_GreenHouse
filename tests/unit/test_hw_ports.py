"""ADR-0017: `aquacrop hw ports` reads the boot configuration and the device nodes and changes nothing."""
import json

from aquacrop.cli import main


def run(tmp_path, capsys, boot_text, nodes):
    boot = tmp_path / "config.txt"
    boot.write_text(boot_text)
    dev = tmp_path / "dev"
    dev.mkdir(exist_ok=True)
    for n in nodes:
        (dev / n).write_text("")
    before = boot.read_text()
    code = main(["hw", "ports", "--boot-config", str(boot), "--dev", str(dev), "--state-dir", str(tmp_path / "var")])
    assert boot.read_text() == before                      # read only
    out = capsys.readouterr()
    return code, json.loads(out.out), out.err


def test_missing_overlays_are_reported_with_the_lines_to_add(tmp_path, capsys):
    code, rep, err = run(tmp_path, capsys, "[all]\n#dtoverlay=uart3\ndtparam=i2c_arm=on\n", [])
    assert code == 1 and not rep["ready"]
    assert "dtoverlay=uart3, dtoverlay=uart4, dtoverlay=uart5" in rep["fixes"][0] and "reboot" in err


def test_ports_ready_after_the_overlays_and_a_reboot(tmp_path, capsys):
    code, rep, err = run(tmp_path, capsys, "[all]\ndtoverlay=uart3\ndtoverlay=uart4 # COM2\ndtoverlay=uart5\n",
                         ["ttyAMA1", "ttyAMA2", "ttyAMA3"])
    assert code == 0 and rep["ready"] and rep["fixes"] == []
    assert rep["ports"]["COM1"]["plan"].startswith("2 Relay") and rep["com0"]["used"] is False


def test_overlay_present_but_no_device_says_reboot(tmp_path, capsys):
    code, rep, err = run(tmp_path, capsys, "dtoverlay=uart3\ndtoverlay=uart4\ndtoverlay=uart5\n", ["ttyAMA1"])
    assert code == 1 and any("COM2" in f and "does not exist" in f for f in rep["fixes"])
