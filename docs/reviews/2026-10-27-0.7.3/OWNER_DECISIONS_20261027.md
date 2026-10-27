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

## Approved: finish the interrupted Sector 1 cycle using Pump 2

The owner has now clarified the earlier example. This **supersedes Sector 2-first / return to Sector 1 next turn**:

1. Sector 1 is being watered by Pump 1 when Pump 1 fails.
2. Promptly notify the operator and raise the pump-failure alarm through the dashboard's alarm workflow.
3. Stop Pump 1. Retain the existing requirement to verify its OFF state before starting its replacement; a fault report or OFF command alone is not proof that it stopped.
4. **Keep Valve 1 open.** Do not close/reopen it as though switching sectors, and do not open Valve 2 merely because the pump changed.
5. Start the healthy Pump 2 for **Sector 1**, subject to the existing readiness, safety, resource and current Twin-proof checks.
6. Pump 2 completes **the remaining watering time of Sector 1's interrupted cycle**, rather than restarting a full cycle or moving to Sector 2 first.
7. Then continue to Sector 2 through its configured normal sector-changeover procedure and watering cycle.

The owner wants both sectors served using the healthy pump rather than abandoning one because its usual pump failed. This is coordinated **mid-cycle replacement**, not merely choosing a different lead on the next independent program start. The previous recommendation to restrict all fallback to a later start was not accepted.

Implementation instructions: preserve the interrupted operation's sector, remaining target, window and identity; record the failure, replacement pump and changeover in its audit/history. Do not mark an interrupted cycle complete before its remaining target is actually satisfied. Retain existing fault-stop behavior when a prerequisite for replacement cannot be verified, and report why takeover was blocked. Add fake-service tests proving OFF verification precedes the replacement ON, Valve 1 stays open, the remaining timed target is retained, and Sector 2 is served afterwards. Test replacement refusal and resource conflicts without assuming that another operation may be preempted.

This is a software requirement, not permission to operate physical outputs or commission an unconfirmed installation. Existing stop verification, alarms, interlocks and the approved hard window finish remain requirements. Do not extend a window or exceed block runtime limits implicitly to make up an interrupted cycle. Do not infer successful stop/valve motion from a drawing or elapsed time alone. Fix C075-01's missing standby proof dependency before relying on replacement-pump evidence.

## Details still awaiting the owner's answer

Priority and timed-cycle remainder are now answered: **finish Sector 1 first, with its remaining time, then Sector 2**. These details remain unanswered:

1. Physical routing: can either pump supply either sector through its sector valve? Do not infer a confirmed installation topology from this software request.
2. Replacement already busy: if Pump 2 is already watering Sector 2, may that operation be interrupted to finish Sector 1, or must Sector 1 wait? Normal parallel/sequential operation has not been confirmed here.
3. Timing/volume accounting: the owner specified the remaining **time**. Explicitly confirm whether downtime pauses that watering countdown and what target to preserve for litre-based cycles. Never guess delivered litres when measurement is unavailable.

Do not invent answers, fixed pump/valve IDs, simultaneous watering, early schedule starts, or a physical changeover procedure. The owner's named pumps/sectors describe the example; implementation must use the installation's explicit equipment/block relationships. Implement the approved block-safety decision, review fixes and the confirmed portions independently while these details are clarified. Update this section with actual owner answers before completing the coordinated failover instructions.

## Owner answers (27 October 2026, given in the Claude session, implemented in 0.7.7)

1. **Routing:** either pump can supply either sector through the sector valve. Each sector block names both pumps as
   its duty pair; whichever is healthy serves it.
2. **Sectors take turns; one healthy pump waters both:** normally Valve 1 opens and Pump 1 waters Sector 1 for its
   time, then stops and Valve 1 closes; Valve 2 opens and Pump 2 waters Sector 2 while Sector 1 waits. If Pump 1
   breaks, Pump 2 waters both sectors and only the valves change: Pump 2 off, Valve 2 closed, Valve 1 open, Pump 2 on.
   No sector is interrupted for another and sectors are never watered at the same time.
   Implemented: the partner idle → it takes over the running cycle at once (0.7.6). The partner busy with the other
   sector → this cycle stops through its normal stop (failed pump OFF, its valve closed, each verified), its remaining
   time or litres wait, and when the partner is free the rest of the cycle starts again (its own startup, valve
   opening, runtime limit and window). Abandoned, and recorded, if its window ends or it has waited an hour.
3. **Changeover and litres:** the remaining watering time counts from when the replacement pump starts (the
   changeover is not watering); a litre cycle delivers exactly the chosen litres in total, then the next cycle
   follows.

## Owner corrections and Codex verification after the 0.7.7 update

This section supersedes conflicting implementation/status claims above; the historical report is retained for traceability.

- The owner confirmed directly in this Codex session that either pump can serve either sector, sectors **must never water simultaneously**, and an interrupted sector waits without preempting an operation already using the healthy pump.
- The owner answered **“No—do not assume that limit”** to the one-hour abandonment rule. It was not approved. Remove it; do not replace it with another invented waiting deadline. Approved window endings, existing safety limits and explicit operator cancellation still apply.
- The owner additionally requires Overview import to bring all pipes and connections into the Twin, ready for simulation; Start an empty design must show a blank project; flowmeters must be aligned in series with the pipe and grid like valves.
- The question whether an empty design also clears the Twin equipment library is pending. No instruction here deletes Equipment, the real Overview, programs or history. Clearing every old drawing element can be fixed without answering that broader scope question.
- Independent review found that 0.7.7 does **not** enforce the claimed sector exclusivity, that Stop all can restart a queued remainder, and that other takeover/Twin defects remain. See [C077 findings and exact instructions](CODEX_REVIEW_0.7.7_20261027.md) and its fake-only evidence. “Implemented” in the earlier author report is not independent acceptance.
