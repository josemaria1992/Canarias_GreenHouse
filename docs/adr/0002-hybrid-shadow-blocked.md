# ADR-0002 HYBRID_SHADOW is not served in this release
Status: accepted; revisit when a physical mapping is approved

Context: shadow mode compares real telemetry with a predicted model state. That needs a mapping
from real points to model signals (`physical_mapping`), which the brief says must stay empty until
the wiring and topology are approved.
Decision: `serve` refuses `HYBRID_SHADOW`. Without a mapping any "prediction" would be decorative.
The read-only backend, origin labels (`REAL`, `PREDICTED`) and the twin are ready for it.
Impact: acceptance items for shadow are BLOCKED on the owner's topology/wiring decisions.
