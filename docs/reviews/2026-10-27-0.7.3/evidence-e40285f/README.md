# Independent evidence for e40285f

Run from the repository root at `e40285f`, with its Python dependencies, Node, and `PYTHONPATH=src;.` (Windows) / `src:.` (POSIX):

```text
python docs/reviews/2026-10-27-0.7.3/evidence-e40285f/review_e40285f_probes.py
node docs/reviews/2026-10-27-0.7.3/evidence-e40285f/review_e40285f_form_probes.cjs docs/reviews/2026-10-27-0.7.3/evidence-e40285f/templates.json
```

These probes intentionally report the faulty behaviour observed at the reviewed head. They are not tests that should keep passing after a fix. `templates.json` is the reviewed built-in catalog, not bench configuration. Engine observations use existing fake rigs, synthetic measurements and a simulated clock; no physical bus.

`targeted.xml` records 38 passing scoped Python tests. The additional 37 JavaScript tests across eight files passed in the review tool log. `ci-status.json` is a point-in-time PR check snapshot for exact e40285f, not a promise that all jobs completed.

`type-form-browser.png` is independent browser evidence from the real reviewed UI on a fresh simulated integration fixture, not the owner's installation or an author screenshot. Manual browser observations are listed precisely in the review. The isolated fixture used its existing throwaway accounts and adapted its single authorized loopback origin to the temporary server port; it changed no production security settings. The new Button/Switch draft was saved without a polarity answer; a Pump draft demonstrated relevant questions, icon/diagram preview distinction and a local-only unsupported request. No type activation, channel assignment or hardware operation. The temporary server and browser tab were closed after the review.

The current-design readiness probe executes the actual `migrationBox` source function with explicit state; it is not claimed as a manual Twin-browser walkthrough. Likewise the changed-quantity round trip is pure model execution, not a completed browser journey. Read the limitations before using this evidence as acceptance.
