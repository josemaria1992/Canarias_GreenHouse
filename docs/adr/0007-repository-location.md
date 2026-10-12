# ADR-0007 Repository location
Status: accepted (2026-10-12)

Context: on 2026-10-11 no repository was named, so the code lived as a git repository mirrored to
the project folder (`aquacrop/` working tree plus `aquacrop.bundle`).
Decision: on 2026-10-12 the owner named `github.com/josemaria1992/Canarias_GreenHouse`. The
delivered history is its `main` branch; further work arrives as pull requests. The project-folder
mirror is kept as a convenience copy only.
Impact: the CI workflow (`.github/workflows/ci.yml`: Ubuntu, Windows and macOS on Python
3.11–3.13, ARM64 and the wheel check) runs on pull requests from now on.
