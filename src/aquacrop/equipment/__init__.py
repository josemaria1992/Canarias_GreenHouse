"""User-defined equipment: types (definitions), individual items and their channel bindings (0.5.0).

A type says what an item of that kind needs (channel roles, properties, limits, permitted operations,
simulation behaviour); an item is one named piece of equipment made from a type version; a binding says
which module channel carries one of the item's roles. Bindings are append-only records like the I/O
assignments of ADR-0017: PROPOSED when chosen, VERIFIED after a server-side check plus the user's
observation, CLEARED when freed. A binding is not an output permission: physical commands additionally
need the bench-test profile, its allowlist and an armed session.
"""
