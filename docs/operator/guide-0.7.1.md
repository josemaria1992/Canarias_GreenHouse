# AquaCrop 0.7.1: how the pages work together

One address: **https://aquacrop-bench.local/**. The Digital Twin opens from its menu entry in the same tab, at
`/twin/`, with the same login; "← Real system" brings you back. The header shows the program's version and build;
if the twin and the main dashboard ever run different builds, a red banner says so.

## 1. Equipment

Equipment → New equipment. Type the **Name** (`Valve 1`): the **ID** fills in by itself (`Valve_1`) until you change
it. Choose the type and icon, press Create. It is **planned and unassigned** until a channel is chosen; it cannot run,
and the Overview shows it as such. Open *Details* to change its name, **ID** (*Change ID*: everything that uses it
keeps working), properties, scaling and alarm limits, and choose its channels.

## 2. Draw the installation in the Digital Twin

Digital Twin → *Installation design*:

* **Import from Overview** brings the approved drawing in as your design. If the Overview still has the earlier
  drawing, it comes in as a proposal: the parts it could not link for certain are listed; decide each one.
* **New design** starts empty. **Edit the drawing**, then pick equipment from the list beside the drawing (grouped by
  type) and press *Place*.
* To connect: select a symbol and press one of its free outlets (or use *Connect pipe*). The inlets it can feed turn
  green with a ring; the others say why not. Press the inlet: the pipe is drawn, horizontal and vertical only.
  Escape cancels.
* Drag symbols: a pipe between two aligned symbols stays straight (the ports level themselves when close).
  *Straighten pipes* levels a whole imported drawing. Drag a pipe's square handle to move a segment; change a port's
  side or position in the symbol's panel. Undo and Redo work for everything.
* **Save in the twin**, run it (Run, or Run now on a program in MANUAL), then **Export to Overview** with a note.

On the main dashboard, Digital Twin → *Changes from the twin* shows what changes. An administrator or an operator with
commissioning presses *Approve and apply*; the Overview then draws it. *Undo* takes the latest one back. Applying a
drawing never verifies wiring, activates a program or switches anything.

## 3. Blocks

A block brings together the equipment of one purpose. *New block*: name, purpose, where the water comes from and goes
to, *Find the path and an example plan*. Members are chosen by type (Valves → Valve_1, Valve_2); an item already in
another block is shared, not copied. Check the startup and stop steps. In **Safety settings** enter the **maximum
runtime** of one operation through this block: programs use it, and none can be saved until it is set. Save, then
Validate. The drawing at the top is only for finding equipment; it cannot be changed here.

## 4. Programs

*New program*: Name and ID (as for equipment), the block, then **How much**: *Water for* N minutes per cycle, or
*Deliver* N litres per cycle (measured by the block's flow meter). **When**: the days, then one or more **windows**
(start and finish), and the **wait after each completed cycle**. Add start and stop conditions if needed. Preview,
Save draft, Validate, then test it in the Digital Twin.

What happens: the first cycle starts at the window's start; each next one waits the set time after the previous one
ended and was verified stopped. At the window's finish a running cycle is stopped (litres not reached are reported as
not reached). If another window starts right then, its first cycle starts as soon as the stop is verified, without the
wait. If a stop cannot be verified, everything holds with an alarm and nothing new starts. Each program card says what
it is doing now.

## 5. Add Module

Real modules only. Administrators and operators with commissioning use the wizard (USB adapter on the controller).
Other operators fill in *Module requests*: the request is recorded on this bench, shown to the people who prepare
modules, and its state is shown to you (requested, being prepared, waiting for the configuration, added, rejected,
closed). It is not emailed anywhere. *Added* appears only when the module is in the running configuration.
