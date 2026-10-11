# ADR-0009 No controller or twin on a real profile in this release
Status: accepted

Context: physical AUTO and live control are out of scope for milestones 1–5.
Decision: a `HARDWARE_READ_ONLY` server runs acquisition, alarms, history and the bench lease only.
Simulation routes (`/api/twin`, `/api/io/*`, `/api/controller*`, `POST /api/commands`) return 409
and the Twin/Control views are hidden.
Impact: there is no code path from a controller to a physical coil.
