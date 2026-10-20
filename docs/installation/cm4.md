# CM4 installation guide (Waveshare CM4-ETH-RS485-BOX-B, CM4004032)

**Status: written from vendor documentation; not yet performed on the unit.** Nothing here has
been tested on a CM4. Every step marked **VERIFY** must be confirmed on the actual hardware and
the result recorded in `docs/commissioning/templates/CP-00_platform_record.md`.

Hardware facts from the owner brief: CM4004032 (4 GB RAM, 32 GB eMMC, no wireless) in the
CM4-ETH-RS485-BOX-B carrier (four isolated RS485 ports, dual Ethernet). Source for carrier details:
Waveshare wiki "CM4-ETH-RS485-BASE-B" (`oldid=104318`, read 2026-10-11, the BOX-B page redirects
there). Generic CM4IO-board instructions do not describe this carrier's wiring.

## 1. Choose and record the OS image

Recommended: Raspberry Pi OS Lite (64-bit), the current Bookworm release, ships Python 3.11, which
is in the tested range and has ARM64 wheels for every locked dependency (checked 2026-10-11 with
`scripts/build_wheelhouse.sh 3.11 aarch64`). Record in CP-00: image file name, release date,
SHA-256 of the downloaded image (compare with the published value), and the date it was flashed.
Security updates: `apt` on a schedule the owner approves (TBD); never during a bench session.

## 2. Flash the eMMC

1. Power off. Set the carrier's **BOOT** switch to **ON** (vendor table: "boot as USB Type-C").
2. Connect the carrier's **PWR & USB** Type-C port to the workstation. With BOOT on, that port is
   the programming port. **VERIFY** whether the carrier also needs its DC input during flashing.
3. On the workstation run `rpiboot` (Raspberry Pi `usbboot`; Windows installer "RPiboot_Setup").
   The eMMC appears as a USB mass-storage device.
4. Write the image with Raspberry Pi Imager. In Imager's settings create the admin user, set an SSH
   key (no password login), hostname and time zone (`Atlantic/Canary` for the installation, `Europe/Stockholm`
   for the Malmö bench; the application uses the configuration's `site.timezone` either way). Do not enable Wi-Fi (the module
   has none).
5. Power off, set **BOOT** to **OFF**, power from the DC terminal (vendor: 7–36 V DC). The vendor
   warns the CM4 needs a stable 5 V 2 A internally; an unstable supply causes throttling.

Reference: <https://www.raspberrypi.com/documentation/computers/compute-module.html>

## 3. Serial ports

For the six-module bench, `bench-bringup.md` is the step-by-step guide (port plan, wiring, module setup,
the I/O board); `aquacrop hw ports` checks the overlays and device nodes below without changing anything.

The vendor lists different names per OS release. **Do not paste these blindly; confirm each one.**

For Bookworm the vendor says to add to `/boot/firmware/config.txt` (**VERIFY** the boot partition
path on the chosen image):

```text
dtoverlay=uart3
dtoverlay=uart4
dtoverlay=uart5
```

| Carrier port | GPIO (vendor) | Vendor tty, Bookworm | Vendor tty, older OS | Actual tty on this unit | Verified how / date |
|---|---|---|---|---|---|
| COM0 | 14/15 | `/dev/ttyS0` (after raspi-config: no serial login, hardware serial on) | `/dev/ttyAMA0` | | |
| COM1 | 4/5 | `/dev/ttyAMA1`? (see note) | `/dev/ttyAMA3` | | |
| COM2 | 8/9 | `/dev/ttyAMA2`? | `/dev/ttyAMA4` | | |
| COM3 | 12/13 | `/dev/ttyAMA3`? | `/dev/ttyAMA5` | | |
| USB TO RS485 (C) | USB | `/dev/serial/by-id/usb-…` | — | | |

Note: the vendor's Bookworm list maps COM1–COM3 to `ttyAMA1–3`; on other Bookworm kernels the
`uartN` overlays have produced `ttyAMAN` names. The only acceptable evidence is a loopback or a
read-only diagnostics read on each port with one module connected.

How to verify one port, read-only:

1. `ls -l /dev/ttyAMA* /dev/ttyS0 /dev/serial/by-id/ 2>/dev/null` and `dmesg | grep -i tty`.
2. Make sure no login console owns the port: `systemctl status serial-getty@ttyS0` must be inactive
   for COM0; `cat /proc/cmdline` must not contain `console=serial0` if COM0 is used for Modbus.
3. Connect exactly one module whose address you know to the port, then:
   `python -m aquacrop diagnostics read --config <file with that port> --device <uid> --cycles 3`.
4. Record the port in the table above and in the config's `port_evidence` field.

Prefer `/dev/serial/by-id/...` for the USB adapter. Onboard UARTs have no by-id alias; if a stable
name is needed, add a udev rule matching the verified device path and record it.

Permissions: the service user is in the `dialout` group only; do not chmod device nodes.

## 4. RS485 bus checks (before connecting modules)

- **Termination**: the vendor lists an optional 120 Ω jumper per port. Terminate only at the two
  physical ends of each bus. **VERIFY** jumper location on the board and the modules' own
  termination.
- **Transceiver mode**: the vendor sets auto/semi-auto direction with two 0 Ω resistors per
  channel on the back of the board. The software assumes automatic direction control. **VERIFY**.
- **One owner per bus**: only one process may open a port. The software enforces this with owner
  locks (`AQUACROP_LOCK_DIR` shared by all installations on the host).
- **Unique addresses**: prove each module's address with that module alone on the bus. Software
  cannot reliably detect duplicate addresses.

## 5. Real-time clock

The vendor lists a PCF85063 RTC on `i2c-10`, address `0x51`, CR1220 cell, enabled with
`dtparam=i2c_vc=on`. **VERIFY** with `i2cdetect -y 10`, then configure the matching rtc overlay
and check `timedatectl`. Without a working RTC and no network time, timestamps after boot are
uncertain; the server records wall-clock steps as `WALL_CLOCK_STEP` events and uses the monotonic
clock for all timing.

## 6. Install the software

From the unpacked release folder (`README-FIRST.md`), with the guided installer
(`deploy/aquacrop-cm4.py`, standard library only). Each command says what it does and stops at the
first failure; add `--dry-run` to see every command and file change first.

```text
sha256sum -c MANIFEST.sha256                                    # the release is intact
sudo python3 deploy/aquacrop-cm4.py check   --release .         # read-only: architecture, Python, space, release files
sudo python3 deploy/aquacrop-cm4.py install --release . --config /etc/aquacrop/hardware-read-only.yaml
sudo python3 deploy/aquacrop-cm4.py status
```

`install` verifies every file against `MANIFEST.sha256`, creates the `aquacrop` system user (group
`dialout` only), installs the wheel offline from `wheelhouse-aarch64-py311` (Bookworm) or
`wheelhouse-aarch64-py313` (Trixie), whichever matches the OS Python, with the hashes in
`requirements.lock`, lays out `/opt/aquacrop/releases/<version>` with the `current` link, writes
`/etc/aquacrop/aquacrop.env` (0640) and `/var/lib/aquacrop` (0700), and installs and enables the
systemd units. It does not start the application, create an account, format anything or touch
`/boot/firmware`. It then prints the next steps: the configuration, the first administrator
(one-time password), the start and the health check. Upgrade, rollback and the pre-upgrade backup:
`docs/operations/service.md`. TLS for the LAN and OpenVPN: `docs/security/remote-access.md`.

The configuration: copy `configs/hardware-read-only.example.yaml` to
`/etc/aquacrop/hardware-read-only.yaml`, fill in only verified facts (ports, addresses, evidence),
keep AI modes `UNKNOWN` until CP-02, and validate:

```text
sudo -u aquacrop /opt/aquacrop/current/venv/bin/python -m aquacrop config validate --config /etc/aquacrop/hardware-read-only.yaml
```

By hand, without the installer (the same result):

```text
sudo adduser --system --group --home /var/lib/aquacrop aquacrop
sudo usermod -aG dialout aquacrop
sudo mkdir -p /opt/aquacrop/releases/<version> /etc/aquacrop
sudo python3 -m venv /opt/aquacrop/releases/<version>/venv
sudo /opt/aquacrop/releases/<version>/venv/bin/python -m pip install --no-index \
     --find-links wheelhouse-aarch64-py311 --require-hashes -r requirements.lock
sudo /opt/aquacrop/releases/<version>/venv/bin/python -m pip install --no-deps wheels/aquacrop_greenhouse-<version>-py3-none-any.whl
sudo ln -sfn releases/<version> /opt/aquacrop/current
```

Then follow `docs/operations/service.md` for the systemd units, the first admin user and backups.

**Tested how:** in CI the installer runs from the built release ZIP on a hosted Ubuntu ARM64 runner
in a staging root (check, install, demo verify, installed-wheel check, upgrade, rollback, status),
and `tests/unit/test_cm4_installer.py` covers it with fake releases. A staging root skips the user,
ownership and systemd steps (they are listed). **Not run on a CM4.**

## 7. Performance on the CM4

Not measured. On a desktop x86_64 the simulation runs about 1,700 physics ticks/s (60× needs 600)
and a 48-point history cycle costs about 16 KB on disk. Measure both on the CM4 during CP-00.
