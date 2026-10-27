# Codex and Claude review handoff

This is the shared repository location for Codex feedback and Claude's implementation summaries. Keep each review cycle in its dated folder so the reviewed source and evidence remain traceable.

## Current cycle

**Owner decisions: [block-safety approval and pump-changeover clarification](2026-10-27-0.7.3/OWNER_DECISIONS_20261027.md)**. The owner approved review/validation/retesting before new starts under changed block safety. The owner also requested alarms and service of both sectors by the healthy pump; the detailed failover questions are awaiting answers. Read this alongside the latest review; do not treat its unanswered questions as approval.

**Latest: [Codex follow-up on Claude's 0.7.5 update](2026-10-27-0.7.3/CODEX_REVIEW_0.7.5_20261027.md)** — reviews `7a70ee6`. Start here for current findings C075-01 through C075-07 and the two pending owner policy questions. Claude's next summary should answer these findings in the existing `CLAUDE_UPDATE.md`.

**[0.7.3 owner corrections — 27 October 2026](2026-10-27-0.7.3/CLAUDE_FIX_INSTRUCTIONS.md)**

- Start with **[implementation instructions](2026-10-27-0.7.3/CLAUDE_FIX_INSTRUCTIONS.md)**. They contain the owner corrections and token-efficient working agreement.
- Consult **[review findings](2026-10-27-0.7.3/REVIEW.md)** and **[evidence](2026-10-27-0.7.3/EVIDENCE.md)** only as needed. They record review of `f91e34d`, with the documentation-only `3cc3c34` delta checked.
- Claude writes the response into **[CLAUDE_UPDATE.md](2026-10-27-0.7.3/CLAUDE_UPDATE.md)** in the same folder and commits/pushes it with the implementation. It now contains the 0.7.5 summary independently reviewed in the latest follow-up above; it is not independent acceptance evidence.

Publication context: this handoff is based on `fdf40b82810e1d6e9f54a6094aa1b6bcf6a04067` on `feat/operator-upgrade-0.7`. That commit updates the old UI checks to follow the new Overview; it landed after the review. Verify its outcome rather than repeating the same CI edit. Its passing status has not been independently established in this handoff.

## Repeatable workflow

1. Claude fetches the implementation branch, reads the current instructions and implements the outstanding corrections. Preserve the historical review and evidence; record responses in `CLAUDE_UPDATE.md` rather than rewriting the findings.
2. Claude fills in the summary with requirement-by-requirement results, exact implementation commit, test evidence, remaining gaps and actual deployment status, then pushes it to the same implementation branch. An implementation commit is recorded before making the summary-only commit; no self-referential commit hash is needed.
3. The owner asks Codex to review the latest repository update. Codex fetches it, reads the summary and reviews the implementation diff and evidence. A summary claiming completion is not independent verification.
4. Further Codex findings belong in a dated follow-up in this folder, or a new cycle folder if the scope changes. Update this index's current-cycle link when a new cycle is started. Do not overwrite old evidence or mark unrun checks as passed.

The historical probe `test_owner_073_probes.py` intentionally asserts observed faulty behavior in several cases. It is outside the normal `tests/` suite. Use it to reproduce the baseline, then write proper corrected-behavior regressions in `tests/`; do not treat unchanged observational assertions as acceptance tests.

Keep discussion concise and in the repository. Read only the applicable instructions, relevant source/tests and unresolved requirements; avoid repeatedly copying historical packages or full test logs. No external message or automatic follow-up is implied by committing a summary.
