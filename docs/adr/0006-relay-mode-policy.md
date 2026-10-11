# ADR-0006 All eight relay modes must be NORMAL for bench writes
Status: accepted (follows the brief)

Context: the vendor V2 page says only Linkage mode blocks command control. The brief requires
exactly eight valid mode registers, all normal, for the initial bench policy.
Decision: follow the brief. Modes are read when arming and again before every write; a short,
missing, unknown or changed mode set refuses the lease or the command without sending anything.
Impact: a module with any Toggle/Edge/Linkage channel cannot be bench tested until it is changed
by a separate, manual, one-device procedure outside this application.
