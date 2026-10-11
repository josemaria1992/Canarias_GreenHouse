# CM4 and Waveshare commissioning — hardware NOT yet tested

## Critical preflight

1. Label both Waveshare Modbus devices with their **real** IDs, baud, parity, stop bits. **Do not assume factory addresses differ.** On one shared RS485 bus, addresses must be unique. Use the USB TO RS485 (C) and test **each module separately** to identify/configure addresses before joining the bus. This project intentionally does not send broadcast configuration changes.
2. Do not connect pumps, chemical valves, VFD enables, contactors, or other live actuators to the eight relay outputs during first bench tests. Use correctly rated isolated indicators/dummy loads, following qualified electrical practice.
3. Verify 24 V compatibility and current budget separately for the CM4 enclosure, Waveshare modules and field loads. Do not assume the 240 W PSU makes an arbitrary connected load safe. Appropriate fuse protection and electrical distribution are not part of the software.
4. Confirm AI board internal hardware jumpers match each intended voltage/current range. Changing a Modbus configuration register alone may not be enough: Waveshare warns that internal jumpers must be modified for current/voltage switching.
5. Some Waveshare relay-board modes link digital inputs to relays, or toggle outputs on input edges. The driver checks all eight relay control-mode registers are NORMAL before it allows even bench writes; it does not change those modes automatically.
6. Confirm serial polarity, bus grounding/isolation topology, termination and biasing according to the official module and carrier manuals. Do not enable every 120-ohm termination jumper indiscriminately.

## CM4 boot/install

- CM4004032 is **not** a CM4 Lite. It boots the internal 32 GB eMMC. The carrier's microSD slot is for Lite variants only.
- Flash Raspberry Pi OS (64-bit) onto eMMC using the CM4 Waveshare carrier's BOOT/USB procedure and Raspberry Pi `rpiboot`/Raspberry Pi Imager process. Carefully follow the manuals linked below; do not try to install the OS by just inserting a microSD card.
- For Raspberry Pi OS versions with `/boot/firmware/config.txt`, identify the correct serial overlays and tty mapping before enabling the carrier's built-in RS485. The Waveshare guide describes enabling `uart3`, `uart4`, `uart5` plus COM0 serial configuration, and notes mappings vary by OS generation. **Verify actual `/dev/tty*` after boot rather than hardcode.**
- An easier bench alternative is connecting the USB TO RS485 (C), using `ls -l /dev/serial/by-id/` to identify the persistent USB serial device.
- The shared Modbus port must have both modules at different addresses and matching serial settings. The program connects as Modbus master and does not initialize/change any physical module settings.

## Read-only Modbus software test

After installing the application and identifying port/addresses, set environmental variables:

```bash
export HYDRO_BACKEND=modbus
export HYDRO_SERIAL_PORT=/dev/serial/by-id/YOUR-VERIFIED-ADAPTER
export HYDRO_RELAY_ID=YOUR-REAL-DEVICE-ADDRESS
export HYDRO_ANALOG_ID=YOUR-OTHER-REAL-ADDRESS
export HYDRO_BAUDRATE=9600   # replace if verified differently
export HYDRO_PARITY=N        # replace if verified differently
export HYDRO_STOPBITS=1      # replace if verified differently
export HYDRO_BENCH_WRITES=0
export HYDRO_PASSWORD='YOUR-UNIQUE-LONG-SECRET'
python -m greenhouse
```

Visit http://127.0.0.1:8000 locally, and confirm all eight analog values, configured module channel types, eight DI, and eight DO are read. `quality` must report `OK`. For a remote PC, establish a secure local/VPN route first.

## Bench-only write verification

After checking the module relay control modes, removing every energized field load, and confirming an isolated dummy-load setup, a qualified operator may change `HYDRO_BENCH_WRITES=1` and restart the application. The browser will permit MANUAL relay toggles only, confirms readback, and logs the action. **Never leave this switch enabled for an unattended greenhouse.** No physical AUTO is provided yet.

The software does not implement guaranteed de-energization when the Pi loses power, the app crashes, the network fails or a motor drive faults. Those requirements must be solved in hardwired safety circuits and electrical design, not by software alone.

## Official sources

- [CM4 datasheet/ordering codes](https://pip-assets.raspberrypi.com/categories/634-raspberry-pi-compute-module-4/documents/RP-008168-DS/cm4-datasheet)
- [Raspberry Pi Compute Module eMMC programming](https://www.raspberrypi.com/documentation/computers/compute-module.html)
- [Waveshare carrier documentation](https://www.waveshare.com/wiki/CM4-ETH-RS485-BOX-B)
- [Waveshare Relay (D) register guide](https://www.waveshare.com/wiki/Modbus_RTU_Relay_%28D%29)
- [Waveshare Analog Input 8CH register guide](https://www.waveshare.com/wiki/Modbus_RTU_Analog_Input_8CH)
- [Waveshare USB TO RS485 (C)](https://docs.waveshare.com/USB_TO_RS485_C)
