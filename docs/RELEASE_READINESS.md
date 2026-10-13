# Release readiness: AquaCrop 0.3.2 (owner demo release of the pre-hardware software)

**0.3.2, 2026-10-13:** the owner demo and acceptance tour. The normal demo is `installation-80`; the
48- and 24-point configurations are legacy test fixtures. No control algorithm, production mapping,
retention, cadence, budget or hardware profile changed from 0.3.1; every item below is as it was.

**Inventory supplement, 2026-10-13:** two IO 8CH modules (SKU 26244) were added: 80 points over six
modules, 16 AI + 32 DI + 32 DO, of which 16 DO are open-drain transistor sinks, not relay contacts
(ADR-0016, `model/digital_io_8ch.md`). The added channels are configured and testable but carry no
field assignment (item 2). Storage is re-measured for 80 points (item 24). The owner review findings
R1-R4 of the same day are fixed and await re-review (`ACCEPTANCE.md`).

**This release is software and simulation only.** Nothing in it has run on the CM4, the I/O
modules, the drive, a probe, the SD card or the owner's OneDrive account. Physical AUTO does not
exist and no setting, profile or flag enables it. A real installation starts and stays
`REAL — READ ONLY`. Item-by-item evidence is in `ACCEPTANCE.md`; this report lists what is still
needed before each physical step, item by item, so nothing hides behind a general "hardware" note.

Status key, as in `ACCEPTANCE.md`: PASS · FAIL · BLOCKED (needs something outside the software) ·
NOT RUN (could run, has not) · NOT CONFIGURED (needs the owner's account or settings).

## 1. Ready for the owner now

| What | Where | Evidence |
|---|---|---|
| Windows demo of the full installation (simulated), offline first run, guided first administrator, no default password | `AquaCrop-Demo.cmd` in the release ZIP; `README-FIRST.md` | DL-01 PASS: `tests/integration/test_launcher.py`; CI job `release-windows` runs the built ZIP offline through `AquaCrop-Demo.cmd`: cold start, sign-in, clean stop, restart keeping the data, upgrade over 0.3.1 (`scripts/release_smoke.py`) |
| Owner acceptance tour UAT-01 to UAT-16 with illustrated guide, checklist and feedback template | `operator/walkthrough.md`; `owner-tour/index.html`, `owner-checklist.csv` in the ZIP | Claude's rehearsal in headless Chromium through the release launcher (CI job `release`); **owner results NOT RUN** |
| Process twin: 20,000 L main, three stocks, sump, shared drive, two sectors, all processes, recipes, faults and recovery | browser, `installation-80` (scenarios also on the legacy `installation-48`) | PH-01..08, PC-01..13 PASS (simulation) |
| Process pages and installation diagram, three roles, phone to PC | browser | UI-01..05 PASS (headless Chromium, simulation and a fake bus) |
| 60 s history for 365 days, clock holds, timestamp quarantine with review before pruning | Settings | OP-01..05 PASS; OP-06 PASS (desktop) |
| SD card and encrypted OneDrive backups, drills, replacement-controller recovery | `operations/offsite-backups.md` | BK-01..07 PASS with fakes and the real rclone binary; activation waits for the on-site setup |
| Guided CM4 install, upgrade and rollback; TLS proxy for OpenVPN access | `installation/cm4.md`, `security/remote-access.md` | DL-02, UI-06 guide and route test PASS in staging; on the CM4 NOT RUN |
| Release ZIP with SHA-256 manifest, commit, wheel and offline wheelhouses for Windows and the CM4 | `scripts/build_release.py` | DL-05 PASS |

## 2. Gates that can only finish with the hardware or the account

| Gate | Status | What is missing (observation, measurement or authorization) | Recorded in |
|---|---|---|---|
| UI-06 home-to-greenhouse route | NOT RUN | controller installed on the greenhouse LAN; the site values of `remote-access.md` (VPN server, LAN and VPN subnets, controller address, name); Caddy's root certificate on the home PC; `aquacrop netcheck` PASS from the LAN and over the VPN; sign-in with each role | CP-00 |
| DL-02 CM4 platform | NOT RUN | CM4 variant and carrier revision; OS image checksum; `deploy/aquacrop-cm4.py check` and `install` on the unit; UART overlay and tty for each COM port, proven with one known module per port; RTC on `i2c-10`; CPU and poll cycle time at 48 points | CP-00 |
| OP-06 storage on the eMMC | NOT RUN (AT-STO-02) | 24 h growth and 365-day projection (`storage report`), WAL during a backup, backup and `verify --full` time, free space after installation, all on the CM4 | CP-00 |
| DL-03 zero writes on the wire | NOT RUN | passive RS485 monitor on a second PC while starting, unplugging a module, reconnecting, restarting, stopping and cutting power: zero FC05/FC06/FC0F/FC10 frames | CP-05 |
| DL-04 bench lease on real relays | NOT RUN | written owner authorization of the bench setup; dummy loads on a fused bench supply; every relay mode NORMAL (CP-01); ON/OFF with coil readback and visual check per output; teardown | CP-04 |
| BK-02 the card | NOT RUN | the 64 GB ext4 card in its USB reader on the CM4: filesystem UUID, `offsite enroll-card`, mount at boot with `nofail`, removal during a backup, write-protect switch, write speed | CP-00 |
| BK-04 the cloud | NOT CONFIGURED | the colleague shares `AQUACROP_DATOS_CANARIAS` by name with edit rights; rclone authorized on the controller with the owner's personal Microsoft account (`offsite-backups.md` 3.1); crypt password and salt generated, printed and sealed; `offsite check` PASS | `offsite-backups.md` section 5 |
| BK-06 monthly cloud drill | NOT RUN | BK-02 and BK-04 done; first upload verified (receipt in the folder); `aquacrop-offsite-drill.service` PASS | Settings, Off-device backups |
| BK-07 real key and account recovery | NOT RUN | the escrow test of `offsite-backups.md` section 4 with the sealed envelope, on a computer that is not the controller; a new Microsoft sign-in; one copy downloaded and `backup verify --full` PASS. Rehearsed only with test keys and a local folder | `offsite-backups.md` 8.1 |
| CP-00..CP-05 | BLOCKED | the hardware on the isolated Malmö bench, a qualified operator, and the owner's written authorization of that setup | `commissioning/templates/` |
| CP-06 drive Modbus interface | BLOCKED | the drive, its make, model and Modbus manual; the drive parameters of `model/vfd_modbus.md` §4 set by a qualified person; the map entered as `PROPOSED`; status bits, run/stop under a lease, comm loss and power cycle with the motor disconnected | `templates/CP-06_drive_modbus.md` |

## 3. Physical configuration the owner still has to supply

Each item is a configuration entry or a recipe field that ships as `TBD`, `NOT_CONFIGURED`,
`PROPOSED` or a labelled DEMO value. The software validates and reports each one; none is guessed.

| # | Item | Today | Needed to close it |
|---|---|---|---|
| 1 | 17th output: drive run (the modules give 16 DO) | decided 2026-10-13: over the drive's Modbus interface (ADR-0015); software and simulation done; adapter `NOT_CONFIGURED` on hardware | drive make, model and Modbus manual; the register map entered as `PROPOSED` on the bench and `VERIFIED` by CP-06 (`model/vfd_modbus.md`) |
| 2 | Point allocation of the 80 channels | relay/analog modules: all 16 DI and 16 DO in use (owner, 2026-10-13), per-channel table still the ADR-0013 proposal; IO 8CH modules: all 16 DI and 16 transistor DO unassigned by owner decision (2026-10-13), DO1 included; the table in `model/signal_budget.md` (last section) is a proposal for later, not fitted devices | owner confirms or changes the relay/analog table; any later IO 8CH binding needs a new owner decision once devices and wiring are verified; wiring drawings that match them |
| 3 | Cause-and-effect table | proposal (`model/cause_and_effect.md`) | owner confirms each row, especially fault propagation through the shared drive |
| 4 | Safe-state matrix: what each output does on fault, power loss and restart | relays keep state; software sends no write at start or reconnect | owner and electrician decide per output; hardwired where it matters |
| 5 | Independent protections: E-stop, motor protection, dry-run, overfill, K1/K2 mutual exclusion | outside the software | electrician's design and test record; the software only reads the safety chain |
| 6 | Module addresses, serial ports, baud and parity | example values in `hardware-read-only.example.yaml` | CP-01 with each module alone on the bus; CP-00 port table |
| 7 | AI channel data types, jumpers and hardware variant | `UNKNOWN`, values unscaled | CP-01 (photos) and CP-02 per channel |
| 8 | Probes: main, sump and stock levels, pH, EC, flows, pressures | proposed tags; DEMO scaling in simulation; a real profile refuses unverified scaling and shows raw values | sensor model, span and calibration certificate per probe; scaling profile set to `VERIFIED` with that evidence |
| 9 | Drive stop and run proof | hardwired DI in the proposal (relay_A DI1/DI2); the Modbus status word is a second opinion | which drive outputs prove running and fault, wired to which DI; status bits checked in CP-06 |
| 10 | Valve feedback (sector closed limits, stock outlet and fill valves) | indirect (flow or level rise), shown as weaker evidence; the IO 8CH inputs stay unassigned (owner, 2026-10-13), so no limit switch is assumed fitted | measured during commissioning that the indirect proof detects a stuck valve; limit switches only by a later owner decision once fitted and wired |
| 11 | Hydraulics: pump flows and curves, channel hold-up (about 16,575 L from the stated sizes), main and sump usable headroom, transit times | DEMO parameters with provenance | measured flows and levels during commissioning |
| 12 | Refill recipe: trigger, endpoint, fixed A/B volumes or EC target with A:B ratio, tolerances, transfer limits | user recipe; DEMO preset in simulation only; the owner's 5,000 + 1,000 + 1,000 + 12,500 + 500 L example is not seeded | production values entered and validated as a recipe |
| 13 | Acid: concentration, dose step, settling window, batch maximum; pH target (owner preference about 6) | synthetic fixture `synthetic-carbonate-ec-v1` | acid stock data sheet and a measured response on the real tank |
| 14 | Mixing method and readiness | `NOT_VERIFIED` on every physical profile, so dosing cannot run there | the method the owner chooses and a test showing the tank is mixed when the reading is taken |
| 15 | Stock preparation procedure and composition record | readiness acknowledgement with actor, time and a free-text note; a level rise invalidates it | the owner's written procedure for A, B and acid |
| 16 | Return pumps: start and stop levels, hysteresis, duty policy | DEMO `ALTERNATE_EACH_START` in simulation | owner chooses levels and policy; no automatic failover is inferred |
| 17 | Sector durations | 300 s each (owner preference), editable | confirm or change in the production recipe |
| 18 | Alarm severities for physical equipment | DEMO rules only | owner list per alarm |
| 19 | Accounts: who is viewer, operator and administrator (1 to 4 people); remote rights | roles enforced; no accounts shipped | names and roles; bench commissioning flag only for the qualified operator |
| 20 | Site time zone | done (owner, 2026-10-13): `Atlantic/Canary` for the installation, twin and demos; `Europe/Stockholm` for the Malmö bench; a real profile must name its zone | — |
| 21 | Network time and OS updates | not set | NTP source on the LAN or the RTC only; `apt` update schedule (never during a bench session) |
| 22 | Remote access site values | examples only in `remote-access.md` | VPN server location, LAN and VPN subnets, controller address and name, one VPN profile per person |
| 23 | Off-device backup activation | disabled; card, folder, key escrow and copy counts chosen 2026-10-12 | `offsite-backups.md` sections 2, 3 and 5 on the controller |
| 24 | Storage for 80 points: database budget, WAL, backups and restore scratch | 60 s complete snapshots, 365 days, SQLite unchanged. Measured on the desktop (400 days, every process in AUTO): 15.0 GiB database, 0.82 GiB backup, restore drill needs 18.05 GiB scratch (19.9 GiB on the card). Owner decision (2026-10-13): 17 GiB planning/warning budget (`db_budget_bytes: 18253611008` in `installation-80` and the bench and read-only examples), leaving about 7.8 GiB of the 32 GB eMMC (about 29.8 GiB) for OS, application, logs and filesystem overhead after the 1 GiB WAL allowance and 4 GiB free-space warning; backups (about 6.6 GiB) and drill scratch (about 19.9 GiB) on the 64 GB card; about 34.4 GiB in OneDrive for 42 copies (`operations/measurements/2026-10-13-measurements.md`) | CP-00 measures OS footprint, usable eMMC and card capacity and real growth on the CM4; the real SD and OneDrive workflow, including a cloud-sourced restore drill, is activated and tested at commissioning; retention, cadence and format are not changed to fit |

## 4. Not part of this release

Live process control with real equipment needs its own reviewed commissioning stage, the owner's
written authorization and physical evidence for items 1 to 16 above. Passing CP-00 to CP-06 does not
release it. Shadow mode (predictions beside real readings) stays blocked until a physical mapping is
approved (ADR-0002).

## 5. What the tests used instead of hardware

| In place of | The tests used | Where |
|---|---|---|
| The greenhouse | the deterministic installation twin with synthetic chemistry | `twin/`, PH and PC gates |
| RS485 modules | the real pymodbus client over an in-memory serial port, and a protocol fake | `tests/fakes`, `tests/hardware` |
| The drive's Modbus interface | a simulated drive answering a DEMO map in the twin; a register-level drive stand-in on the in-memory serial port | `twin/vfd.py`, `tests/hardware/test_vfd_modbus.py` |
| The SD card and USB reader | a replaceable kernel volume probe | `test_offsite.py` |
| OneDrive | a local folder behind the real rclone crypt remote (rclone 1.60) | `test_offsite.py` |
| The sealed key envelope | test-only password and salt | `test_bk07_replacement_controller_recovers_with_only_the_escrowed_keys` |
| The CM4 | a hosted Ubuntu ARM64 runner, installer in a staging root | CI jobs `release-cm4-staging` and `test` on `ubuntu-24.04-arm` |
| A Windows PC | a hosted Windows runner | CI jobs `release-windows`, `launcher` |
| The home network and VPN | an HTTPS endpoint on 127.0.0.1 with a test certificate authority | `test_netcheck.py` |
| A year of operation | a 400-day synthetic history with the measured process event rates | `operations/measurements/` |

None of these is physical verification.
