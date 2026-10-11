# ADR-0004 One database per profile
Status: accepted

Context: v0.1 stored simulated limits in a shared settings table that a hardware run then used
(finding F07).
Decision: configuration is versioned YAML per installation; nothing operational is read back from
the database. In addition the server refuses to start on a database that a different profile has
written. The hardware example config uses its own file name.
Impact: simulation history never mixes with real history; switching profile needs a separate state
directory or `historian.db_path`.
