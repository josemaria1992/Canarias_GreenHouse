# ADR-0003 Bench writes are a runtime lease on top of HARDWARE_READ_ONLY
Status: accepted

Context: the brief lists `BENCH_MANUAL_WRITE` as an access policy and also requires that every real
profile starts read-only and that persistent configuration cannot renew bench permission.
Decision: `BENCH_MANUAL_WRITE` is rejected as a configured boot profile. A real installation is
always `HARDWARE_READ_ONLY`; the bench state exists only as an in-memory, persisted-for-audit lease
bound to session, process and config version. The banner switches to `REAL — BENCH WRITES ARMED`
while it is active.
Impact: no file, environment variable or restart can produce a write-capable state.
