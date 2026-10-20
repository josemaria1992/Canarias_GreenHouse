# Bench bring-up: CM4 box, six modules, I/O board

**Status: written from the Waveshare manuals and tested on the in-memory bus and the simulation. Not yet
performed on the hardware.** Record every result in `docs/commissioning/templates/` (CP-00 to CP-04).
Decisions behind it: ADR-0017.

What you will have at the end: AquaCrop running on the CM4, reading all six modules, a browser on your PC
showing the **I/O board** over HTTPS, every channel checked with the guided check, and the channels you
have wired assigned to installation signals and verified. Nothing controls irrigation yet.

You need: the CM4-ETH-RS485-BOX-B with the CM4 flashed (`cm4.md` sections 1 and 2), the six modules and
their supply, twisted-pair cable for RS485, the USB TO RS485 (C) adapter (optional, for debugging), a 4-20
mA source for the analog inputs (a loop calibrator, or a supply and a resistor), and test loads for the
outputs (LEDs or lamps; no pumps, valves or anything that can move water).

## 1. Plan

| Module | Name | Address | Port |
|---|---|---|---|
| Relay (D) #1 | relay_A | 1 | COM1 (/dev/ttyAMA1) |
| Relay (D) #2 | relay_B | 2 | COM1 |
| AI 8CH #1 (version A) | analog_A | 3 | COM2 (/dev/ttyAMA2) |
| AI 8CH #2 (version A) | analog_B | 4 | COM2 |
| IO 8CH #1 | digital_A | 5 | COM1 |
| IO 8CH #2 | digital_B | 6 | COM1 |

COM3 stays free for the pump drive later. COM0 is not used. Write the name on each module with a marker.
The IO 8CH outputs are transistor sinks (max 500 mA, DC), not relay contacts.

## 2. Serial ports on the CM4

1. Add at the end of `/boot/firmware/config.txt` (Bookworm or newer), then reboot:
   ```text
   [all]
   dtoverlay=uart3
   dtoverlay=uart4
   dtoverlay=uart5
   ```
2. After installing AquaCrop (section 4), check:
   `sudo -u aquacrop /opt/aquacrop/current/venv/bin/python -m aquacrop hw ports --state-dir /var/lib/aquacrop`.
   It prints each COM port, whether its overlay is set, whether its device exists and can be opened, and what
   to do if not. It changes nothing. Exit 0 means COM1 and COM2 are ready.

## 3. Wiring

- Daisy-chain each bus (no star): CM4 COM1 → relay_A → relay_B → digital_A → digital_B; CM4 COM2 →
  analog_A → analog_B. A to A, B to B, and the port's GND to each module's GND.
- Termination 120 Ω only at the two ends of each bus: the CM4 port's jumper, and the last module of that
  chain. Check the jumper positions on the board and on the modules (cm4.md section 4).
- Analog inputs: version A ships with every channel's jumper fitted (current mode). Leave them fitted for
  4-20 mA. A two-wire 4-20 mA loop: supply + → transmitter or calibrator → AIN+; AIN- → supply −.

## 4. Install AquaCrop

From the unpacked release ZIP on the CM4 (cm4.md section 6):
```text
sha256sum -c MANIFEST.sha256
sudo python3 deploy/aquacrop-cm4.py check   --release .
sudo python3 deploy/aquacrop-cm4.py install --release .
```
The release carries offline packages for Python 3.11 (Bookworm) and 3.13 (Trixie). Do not start the
service yet.

## 5. Give each module its address, one at a time

Every module ships at address 1, so connect **only one module** to COM1 for this step (the analog ones too;
they move to COM2 afterwards). For each module, power it, connect it alone, then run (example for relay_B):
```text
cd /var/lib/aquacrop
sudo -u aquacrop /opt/aquacrop/current/venv/bin/python -m aquacrop hw scan  --port /dev/ttyAMA1 --state-dir /var/lib/aquacrop
sudo -u aquacrop /opt/aquacrop/current/venv/bin/python -m aquacrop hw setup --port /dev/ttyAMA1 --state-dir /var/lib/aquacrop \
     --model relay --name relay_B --address 2 --record /var/lib/aquacrop/setup-relay_B.json
```
- `--model relay` for Relay (D), `io` for IO 8CH, `ai` for AI 8CH. For the analog modules add
  `--ai-range all=4-20mA --ai-variant A`.
- It shows what it found and what it will write, and asks you to type YES. It refuses if more than one
  module answers, if the model is different, or if AquaCrop is already using the port (stop the service).
- It sets every channel to Normal mode (relay and IO) or 4-20 mA (analog), then the address, reads
  everything back from the new address and checks the old address is silent. The record says DONE.
- relay_A keeps address 1; setup still checks and sets its modes.
- Label the module, disconnect it, next one. When all six are DONE, wire the buses as in section 3.

With the USB TO RS485 adapter instead (on the CM4 or a PC with AquaCrop installed), use its port, e.g.
`--port /dev/serial/by-id/usb-FTDI_…` or `--port COM5` on Windows. The adapter is also useful to listen to
a bus without sending (cm4.md section 3).

## 6. Make the bench configuration and start

```text
cd /var/lib/aquacrop
sudo -u aquacrop /opt/aquacrop/current/venv/bin/python -m aquacrop hw scan --port /dev/ttyAMA1 --state-dir /var/lib/aquacrop   # 4 modules: 1, 2, 5, 6
sudo -u aquacrop /opt/aquacrop/current/venv/bin/python -m aquacrop hw scan --port /dev/ttyAMA2 --state-dir /var/lib/aquacrop   # 2 modules: 3, 4
sudo -u aquacrop /opt/aquacrop/current/venv/bin/python -m aquacrop hw make-config setup-*.json \
     --digital-port /dev/ttyAMA1 --analog-port /dev/ttyAMA2 --port-evidence "hw ports + hw scan <date>" \
     --config-id bench-malmo --timezone Europe/Stockholm --host aquacrop-bench.lan --out /tmp/bench.yaml
sudo install -o root -g aquacrop -m 0640 /tmp/bench.yaml /etc/aquacrop/bench.yaml
```
Set `AQUACROP_CONFIG=/etc/aquacrop/bench.yaml` in `/etc/aquacrop/aquacrop.env`. The configuration is
HARDWARE_READ_ONLY with all four safe-state moments, 1 s polling, the 60 s / 365 day history and no
channel bound to any plant signal. Then:
```text
sudo -u aquacrop /opt/aquacrop/current/venv/bin/python -m aquacrop users bootstrap --config /etc/aquacrop/bench.yaml \
     --state-dir /var/lib/aquacrop --username <you> --generate
sudo systemctl start aquacrop-greenhouse.service
curl -fsS http://127.0.0.1:8000/api/health
```

## 7. HTTPS from your PC

`sudo python3 deploy/aquacrop-cm4.py tls --hostname aquacrop-bench.lan` and follow what it prints
(trust Caddy's root certificate on your PC once; `docs/security/remote-access.md`). The name must resolve
on your LAN (router DNS entry, or the PC's hosts file). Open `https://aquacrop-bench.lan/`, sign in and
change the one-time password. In **Settings, Users** create a second account (role operator) with
**Commissioning** ticked, and use it on the bench: only a commissioning account can switch outputs and
assign channels.

## 8. The I/O board

Menu **I/O board**. Each module has a card: a green light when it answers, the port's polling time,
outputs with two lights (left: asked, right: reported by the module) and a switch, inputs with a light,
analog inputs with the reading in mA, a bar, the raw value and its age.

1. **Start a test session**: describe the bench ("lamps on all outputs, no pumps"), type the confirmation
   phrase, keep all outputs ticked, 1800 s. The banner turns red. **Renew** adds 30 minutes. When the
   session ends (time, End session, or logging out), every output it covers that reads ON is switched OFF.
2. **All outputs OFF** works at any time.
3. **Guided check**: choose every module (or one), Start. For each channel, Next moves on:
   - output: it is switched ON; look at the LED or lamp, press **Works** or **Doesn't work**, Next (it is
     switched OFF before the next channel);
   - input: press the button or close the switch and release; the board shows the change and selects Works;
   - analog: apply 4 mA, 12 mA or 20 mA; the reading is shown; optionally type the expected value.
   Results are recorded with your name and time. "Skip channels that already work" resumes where you stopped.
4. **Assign**: click a channel's name, choose the installation signal it carries (e.g. Sector 1 valve on
   relay_A.DO3), add a note (wire number), Save: PROPOSED. Then **Verify**: switch the output ON and OFF (or
   change the input, or apply a known current), describe what you saw, Mark verified: VERIFIED.
5. **Export** (YAML or JSON) lists every channel with its assignment and its latest check. Attach it to
   CP-03 / CP-04.

The assignments are records. They do not connect anything to the irrigation program; that is the next
stage and needs your written go-ahead.

## 9. If something does not answer

- `hw ports`: overlays, device nodes, permissions, and whether AquaCrop holds the port.
- The module's card says NOT ANSWERING with the last error (timeout, CRC, exception). Check A/B polarity,
  GND, termination at both ends only, the supply, and that no two modules share an address
  (`hw scan` with the service stopped).
- A module that stops answering is asked again after a growing pause, and is switched OFF-safe when it
  answers again (MODULE_RETURN). If the poller of a port itself fails, it starts again after 30 s.
- Listen to the bus with the USB adapter without sending: `python -m serial.tools.miniterm <port> 9600`.
