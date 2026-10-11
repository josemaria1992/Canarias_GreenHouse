# ADR-0007 Code kept as a git repository in the project folder
Status: interim

Context: no GitHub repository is connected to the project and none of the owner's existing
repositories matches.
Decision: develop in a local git repository mirrored to the project folder (`aquacrop/` working
tree plus `aquacrop.bundle`). Clone with `git clone aquacrop.bundle`.
Impact: CI (`.github/workflows/ci.yml`) has not run; Windows/macOS/ARM64 results are NOT RUN until
the code is pushed to a repository the owner names.
