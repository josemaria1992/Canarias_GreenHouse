# ADR-0001 Module quantity and generic inventory
Status: accepted (2026-10-11)

Context: the brief lists Relay (D) and AI 8CH quantities as "awaiting confirmation" and also states
"two of each I/O type (four units, 16 AI + 16 DI + 16 DO = 48)".
Decision: treat that sentence as the owner's answer: 2 × Relay (D) + 2 × AI 8CH = 48 points
(`demo-48`). Keep the inventory generic (`8 × AI + 16 × Relay D`), ship `demo-24` too, and test an
arbitrary extra module. No code assumes four modules or global AI1..AI16 arrays.
Impact: if the installed count differs, only the configuration file changes.
