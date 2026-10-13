# The shared drive's run command over Modbus RTU

Owner decision, 2026-10-13: keep the 48 points (all 16 DI and 16 DO are allocated) and investigate
the drive's own Modbus interface for the extra run command. Keep the physical interface unconfigured
until it is verified; finish the simulation and bench software meanwhile. ADR-0015 records the design.

**Status.** Software done and tested against a simulated drive and a register-level test double. No
real drive, manual or register map has been seen. The installation configuration keeps the adapter
`NOT_CONFIGURED`. Nothing here is physical verification.

## 1. What the software does

| Operation | When | Function | Proof it gives |
|---|---|---|---|
| Read the status word | every poll of the drive's bus, on every profile | FC03 or FC04, one register | the drive answers; running, fault and ready bits as the manual defines them |
| Read diagnostics (optional) | with the status word | FC03 or FC04, one register each | frequency, current, fault code; informational only |
| Write the run sequence | a run command | FC06 to the control register, one frame per value (1 to 3 values), in order | each echo proves the drive received that value |
| Write the stop value | a stop command | FC06, one frame | the echo |

What it never does: write a speed reference, a parameter, a fault reset, a broadcast, or any
register other than the configured control register (`RtuBus` refuses every other unit and register
before anything is sent, and a bus built without a drive cannot write a register at all). It never
retries a write, never rewrites the control value periodically, and sends nothing on start, lease
end, shutdown or reconnect.

An echo is not motor proof. The processes keep using the hardwired `drive.MAIN.running` and
`drive.MAIN.fault` inputs (relay_A DI1 and DI2 in the proposed allocation) as the evidence that the
motor runs or has stopped. The status word is a second opinion: in the twin, if it disagrees with the
hardwired input for 10 s, `DRIVE_STATUS_MISMATCH` is raised.

## 2. Where it runs

| Profile | Adapter | Map | Writes |
|---|---|---|---|
| Installation twin (`installation-48`) | `MODBUS_RTU` | `DEMO` (the simulated drive's own map) | by the irrigation controller, simulated |
| Malmö bench (`bench-malmo.example`, Europe/Stockholm) | `NOT_CONFIGURED`; `MODBUS_RTU` with a `PROPOSED` map for CP-06 | from the drive's manual | only under a bench lease (`drive:MAIN` in the allowlist), motor disconnected |
| Installation (`hardware-read-only.example`, Atlantic/Canary) | `NOT_CONFIGURED` | `VERIFIED` after CP-06, with its record | none in this release (read-only; no physical AUTO) |

A real profile refuses a `DEMO` map. A `VERIFIED` map needs its CP-06 evidence. A `PROPOSED` map is
accepted on a real profile so it can be read by `diagnostics read` and the hardware service and
written under a lease; it is how the map gets verified.

## 3. Wiring

* Give the drive **its own RS485 port** on the CM4 carrier (a second bus in the configuration), not
  the relay and AI modules' bus. Drive cables carry switching noise, and a drive that stops answering
  must not cost the modules their polls (each bus has its own worker and backoff).
* Twisted pair with shield, shield bonded at one end, termination at both ends of the segment,
  biasing where the drive's manual requires it, signal common connected. Route it away from the motor
  cable and the drive's output.
* Prefer an isolated RS485 interface on the drive side if the manual offers one.
* The emergency stop and any safe torque off (STO) input stay hardwired, as they would be without
  Modbus. **Modbus is not a safety function.**

## 4. Drive settings (made at the drive by the commissioning engineer, recorded in CP-06)

| Setting | Why |
|---|---|
| Command source = fieldbus / serial (Modbus) | otherwise the drive ignores the control register |
| Speed reference = keypad, preset or analog (not fieldbus) | the software writes no speed; the speed stays a drive setting |
| Serial settings and address | must match the bus and the map's `address`; the software never writes them |
| Communication-loss timeout and action (`comm_loss_timeout_s`, `comm_loss_action`) | the drive must stop by itself when frames stop arriving; record exactly what it does (coast, ramp, trip) |
| What resets the comm-loss timer | see section 6; must be "any valid frame addressed to the drive" or "reads" for this design |
| Behaviour at power-up | the drive must come up stopped and must not restart from a remembered run command |
| Auto-restart after a fault | off; a fault is reset at the drive by a person |

The timeout must be at least twice the poll interval of the drive's bus (the configuration refuses
less), so one slow poll does not stop a healthy drive.

## 5. Reading the register map from the manual

| Field | Notes |
|---|---|
| `control_register` | the register run and stop are written to. Check the addressing convention: manuals write "40001" or "register 1" for protocol address 0 in some cases and protocol address 1 in others. The configuration holds the **protocol address** (what goes in the frame, 0..65535). CP-06 confirms it by reading the register back. |
| `run_sequence` | the values written, in order, to start. A drive with a simple command register needs one value. A drive with a profile state machine (shut down, switch on, enable operation) needs two or three. Never more than three. |
| `stop_value` | one value; must differ from the last run value |
| `status_register`, `status_function` | the status word and whether the manual reads it with FC03 (holding) or FC04 (input) |
| `running_mask`, `fault_mask`, `ready_mask` | the bits of the status word; running and fault must not share a bit |
| `frequency`, `current`, `fault_code` | optional; register, function, signed or not, scale and unit as the manual gives them |
| `drive_model`, `manual_reference` | make, model and firmware; document, edition and page, so anyone can check the numbers |

Check that the drive supports FC06 on the control register and FC03/FC04 on the status register,
and that **the control register is not stored in non-volatile memory** (some drives save certain
registers to EEPROM, which wears it out and survives power-up). If it is, stop and record it.

## 6. Open points that only the real drive can answer

1. **What resets the drive's comm-loss timer.** This design writes the control register only when a
   command is given, then reads the status word every poll. If the drive's timer is reset only by
   writes to the control register, a running drive would stop after the timeout. Then the choice is
   either a drive setting that counts reads, or periodic re-writes of the current control value,
   which this release does not do and which would need an ADR addendum.
2. **Power-up state** of the control register and of the drive (section 4).
3. **Whether the run sequence must be repeated after a fault reset** at the drive.
4. Whether the status word reports ready separately (`ready_mask` is optional).

## 7. The simulated drive

`twin/vfd.py` answers the DEMO map in `configs/installation-48.yaml` (address 10 on `bus_sim_1`,
control register 100, run sequence 1 then 3, stop 0, status word 101 with ready 0x1, running 0x4 and
fault 0x8, frequency 102 and current 103 in 0.1 units, fault code 104, comm-loss stop after 5 s).
These numbers belong to the simulated drive only. Like a drive set up as in section 4, it stops and
trips when no frame reaches it for the timeout while it runs. Injectable faults (twin page or
`ACTUATOR_FAULT` kind `drive_comm`): `NO_RESPONSE`, `EXCEPTION`, `IGNORES_WRITES`. Scenarios:
`drive_modbus_comm_loss` (PC-12), `drive_modbus_refused` and `drive_modbus_ignores_writes` (PC-02).

## 8. Bench verification (CP-06)

Procedure in `docs/commissioning/procedures.md` and template
`docs/commissioning/templates/CP-06_drive_modbus.md`: the drive on the Malmö bench with the motor
disconnected, the map entered as `PROPOSED`, status bits checked against the keypad, run and stop
under a lease, comm-loss test by unplugging the bus. Only after a signed CP-06 does the map become
`VERIFIED` and move to the installation's configuration.

## 9. What the owner supplies

* Drive make, model and firmware, and its Modbus manual (or the page set for Modbus).
* Which RS485 port the drive uses, and its serial settings and address.
* The person who sets the drive parameters in section 4 and signs CP-06.
