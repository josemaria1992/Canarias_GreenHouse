# ADR-0009 No controller or twin on a real profile in this release
Status: accepted

Context: physical AUTO and live control are out of scope for milestones 1–5.
Decision: a `HARDWARE_READ_ONLY` server runs acquisition, alarms, history and the bench lease only.
Simulation routes (`/api/twin`, `/api/io/*`, `/api/controller*`, `/api/processes*`, `/api/stocks*`,
`/api/recipes*`, `POST /api/commands`) return 409 and the Twin/Control views are hidden.
Impact: there is no code path from a controller to a physical coil.

Addendum (2026-10-12, pre-hardware release stage 3): the hiding rule is narrowed so the installation can
be observed on a real profile. `GET /api/plant` is served there, and the Overview diagram and the process
pages (Nutrients & acid, Main tank, Main pumps, the sectors, Drainage) render read-only: clicking a part
only navigates, every control is replaced by the reason it is not offered, there is no process summary or
recipe page, and every logical signal stays `UNBOUND` ("not measured") until the owner approves a
physical mapping (ADR-0013). Observations are labelled REAL or SIM; synthetic feedback never joins a real
observation stream. A read-only diagram is not a commissioned live twin or a predictive shadow (ADR-0002).
The boundary is unchanged: nothing on these pages can send a command, and the simulation-only routes above
still return 409.
