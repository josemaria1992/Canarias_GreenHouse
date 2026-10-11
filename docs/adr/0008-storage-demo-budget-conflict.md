# ADR-0008 Demo retention versus demo database budget
Status: open; owner decision needed

Context: measured ≈ 16.2 KB per 48-point cycle (schema v2, indexes included), so 48 points every
3 s is ≈ 445 MiB/day. The brief's demo proposal (7 days full resolution) needs ≈ 3.0 GiB; its
demo budget is 2 GiB (≈ 4.6 days).
Decision so far: keep the schema (every cycle records every point with full provenance, as the
brief requires), keep the DEMO policy that deletes nothing, raise `STORAGE_PRESSURE` when over
budget, document the numbers.
Options for the owner: larger budget; shorter full-resolution retention; slower polling; schema
compaction (move per-cycle text columns to the cycle row, est. ~50 % saving, engineering work).
