"""Small helpers shared by test modules."""


def with_sim_time_recording(cfg, seconds: float = 60.0):
    """A copy of `cfg` with the optional simulation-time recorder on, for tests that need history rows
    from `SimulationRuntime.run_for` without waiting for wall-time labels. Installations record on wall
    time; this series is labelled SIMULATION_TIME and is off by default."""
    return cfg.model_copy(update={"historian": cfg.historian.model_copy(update={"sim_time_recording_s": seconds})})
