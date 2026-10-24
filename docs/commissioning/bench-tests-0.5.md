# Owner test sheet: programs on bench test loads (0.5.0)

These steps are yours to perform and observe. AquaCrop records what it commanded and what the modules
read back; only you can say what the LED, the button and the wiring did. Every line below starts as
**NOT RUN**. A pass here shows bench behaviour, not pump performance, water delivery or field safety.

Scope: digital_A and digital_B (IO 8CH, SKU 26244, 500 mA open-drain transistor outputs) only. Relay
outputs, the drive, pumps, valves and dosing stay out of scope.

## 1. Before you wire anything

* Switch off the 24 V supply of the test board.
* Each IO 8CH output is a transistor **sink**: the load goes between your test supply's + and the DO
  terminal, and the test supply's − goes to the module's GND. An LED needs a series resistor (for 24 V and a
  10 mA LED, about 2.2 kΩ). Keep every output well below 500 mA.
* Buttons go to digital inputs as dry contacts, wired as the Waveshare IO 8CH manual shows for your board
  revision. Check against the label and the manual before you power it.
* Write down which LED is on which DO and which button is on which DI.

## 2. Approve the test loads in the configuration

Add this to `/etc/aquacrop/bench.yaml`, with your own outputs and description, then validate and restart.
The service comes back in RECOVERY and nothing runs until you approve it:

```yaml
bench_tests:
  enabled: true
  allowlist: [digital_A.DO1, digital_A.DO2]
  approved_by: vicente
  loads: "LED board: 2 LEDs with 2.2 kOhm from 24 V to DO1/DO2; buttons on DI1/DI2"
```

```bash
sudo /opt/aquacrop/current/venv/bin/python -m aquacrop config validate --config /etc/aquacrop/bench.yaml
```

```bash
sudo systemctl restart aquacrop-greenhouse
```

Relay (D) outputs are refused in this list by the configuration check.

## 3. Equipment and channels

1. Equipment types → Add built-in templates, then activate **Test LED** and **Button**.
2. Equipment → create one Test LED per wired LED and one Button per wired button.
3. Choose each channel and verify it:
   * LED: arm a test session on the I/O board, switch the output ON and OFF there, then press **Verify** and
     describe what the LED did.
   * Button: press and release the button, then press **Verify** and describe it.

## 4. Test in the twin first

1. On the real dashboard, go to Programs and create, for example:
   * **Blink**: MANUAL, run LED 1 for 10 s.
   * **Button lights LED**: when Button 1 is ON for 1 s, run LED 2 for 10 s.
   * **Schedule**: weekly, every day, a 2-minute window a few minutes from now, run LED 1.
2. Open the Digital twin, press *Copy from the real system*, activate the programs there, approve the
   restart into MANUAL or AUTO, and run them. When a result is right, press **Accept twin result**.

## 5. On the bench

| # | Step | What you should see | Result |
|---|------|---------------------|--------|
| 1 | Operation page after the restart | Mode RECOVERY; every output reads OFF | NOT RUN |
| 2 | Approve into MANUAL; Run now *Blink* with no test session | Refused: no armed bench test session; LED stays off | NOT RUN |
| 3 | Arm a test session with DO1 and DO2 (I/O board); Run now *Blink* | LED 1 on for 10 s, then off; Results shows DONE | NOT RUN |
| 4 | Run *Blink* again; revoke the session after 3 s | LED 1 off within about a second; Results shows the session ended | NOT RUN |
| 5 | Switch to AUTO; press Button 1 for 2 s | LED 2 on for 10 s | NOT RUN |
| 6 | Wait for the *Schedule* window | LED 1 on for the window, then off; it does not run twice | NOT RUN |
| 7 | During the window, switch to MANUAL | LED 1 off; mode MANUAL only after the readback shows OFF | NOT RUN |
| 8 | Let the session expire (30 min) during a window | LED off at expiry; nothing restarts without a new session | NOT RUN |
| 9 | Restart the service during a window | Comes back in RECOVERY with every output OFF; the window is not resumed | NOT RUN |

Record each result in the build thread as PASS or FAIL, with what you saw.

## 6. What remains for the field

Relay outputs, the drive's Modbus run command (CP-06), real pumps and valves, the hydraulic topology,
calibrated sensors and any production setpoint each remain separate commissioning steps.
