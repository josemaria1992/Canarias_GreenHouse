# Owner decisions after the 0.7.5 review

This records the owner's replies after `CODEX_REVIEW_0.7.5_20261027.md`. It supersedes that review's recommendations only where a decision is explicitly approved below. Findings C075-01 through C075-07 remain open until fixed and independently checked. No operating policy should be inferred from an unanswered question.

## Approved: changes to block safety require program review

The owner accepted Codex's recommendation: programs using superseded block safety settings become unavailable for new starts until reviewed, validated and retested. Retain **Review and use vN**; never silently rewrite a validated program.

Implementation instructions:

- When a newer validated block version changes safety settings (maximum runtime, start conditions or stop conditions), identify programs compiled against the older settings and refuse new starts using those superseded settings. An ordinary unvalidated draft is not the newly accepted block version.
- Explain the reason in Programs and the runtime refusal: the block's safety settings changed, the program is using the older version, and review/testing is required. Show the versions and changes without losing the operator's form entries.
- The operator explicitly adopts the new block version in a program draft, previews it, validates it and obtains current Twin test evidence before it becomes eligible again through the existing activation/readiness gates. Preserve old immutable versions and history.
- Enforce this on the server/engine start paths, including scheduled and manual starts; a frontend warning alone is insufficient. Recheck after restart. Do not allow stale accepted Twin evidence to restore eligibility.
- This approval concerns **new starts**. It does not authorize silently editing a running operation or specify a new response for an already-running operation when block settings change.

Acceptance: an older program cannot start after changed block safety is validated; editing a block draft alone does not apply its settings; updating the program without validation/current Twin evidence does not unblock it; the complete review/validate/retest journey does. Confirm the same behavior after restart. Use virtual/fake services.

## Approved goal: one failed pump must not strand either sector

The owner requested:

> If one pump fails it should notify immediately and raise an alarm. Both sectors need always to be watered. It can then jump to the working pump watering sector 2, and when it is time to water sector 1 again, it should stop pump 2, close valve 2, open valve 1 and turn on pump 2 to water sector 1.

Record the requested behavior accurately:

- On detection of a pump failure, promptly notify the operator and raise an alarm through the dashboard's alarm workflow.
- Use the healthy pump to serve both sectors rather than abandoning one sector because its usual pump failed.
- The owner's example changes from Pump 2 watering Sector 2 to Pump 2 watering Sector 1 in this order: **stop Pump 2 → close Valve 2 → open Valve 1 → start Pump 2**.
- The earlier recommendation to restrict all fallback to a later independent program start has **not** been accepted as the final product behavior. The owner has instead requested coordinated service of both sectors using the working pump.
- This is a software requirement, not permission to operate physical outputs or commission an unconfirmed installation. Existing stop verification, alarms, interlocks and the approved hard window finish remain requirements. Record a blocked changeover honestly if its prerequisites cannot be established; do not infer successful stop/valve motion from a drawing or elapsed time alone.

## Details still awaiting the owner's answer

Questions were sent to the owner; no answer is recorded yet:

1. If Pump 1 fails while watering Sector 1, does Pump 2 serve Sector 2 first and return to Sector 1 at its next scheduled turn, or finish the interrupted Sector 1 watering first?
2. When Sector 1 is served again, is the target only its remaining time/litres or a full new cycle? How unmeasurable delivery is treated must remain explicit; never guess delivered litres.
3. Can either pump hydraulically supply either sector through the selected sector valve, or is that arrangement not confirmed?

Do not invent answers, fixed pump/valve IDs, simultaneous watering, early schedule starts or a physical changeover procedure. The owner's named pumps/sectors describe the example; implementation must use the installation's explicit equipment/block relationships. Implement the approved block-safety decision and review fixes independently while the failover details are clarified. Update this section with the owner's actual answers before completing the coordinated failover instructions.
