"""User programs (0.5.0): declarative operations on equipment items, their weekly schedules, the engine that runs
them through the audited command path, the installation-level AUTO/MANUAL mode, conflicts and restart recovery.

Nothing here executes code supplied by a user: a program is validated data (`definitions.py`). The engine
(`engine.py`) is the only thing that acts on it, one tick at a time, on the controller.
"""
