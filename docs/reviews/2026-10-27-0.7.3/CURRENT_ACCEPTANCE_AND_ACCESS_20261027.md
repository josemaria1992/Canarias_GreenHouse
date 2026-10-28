# Consolidated acceptance and VPN check — 27 October 2026

**Owner answer: 0.7.5 is deployed and accessible over the VPN, but the requested update is not yet complete.**

Fresh Git fetch: branch head `3a8d8440a0e61fd91b9a8371708b898dedf6e77c`. Application code remains the implementation reviewed at `7a70ee6`, released from `caf0f88`. All subsequent commits are review/decision documents. No fixes for C075-01 through C075-07 have been pushed, and the owner's later block-safety/failover decisions have not been implemented. Do not mistake publishing an instruction for implementing it.

## Deployment and access — now independently checked

The owner supplied the controller's LAN IP. Both read-only health requests over the existing OpenVPN route returned:

```json
{"ok":true,"storage_ok":true,"acquisition_ok":true,"runtime":"MAIN","version":"0.7.5","build":"0.7.5+caf0f88e99.w7ed33495"}
{"ok":true,"runtime":"TWIN","version":"0.7.5","build":"0.7.5+caf0f88e99.w7ed33495"}
```

The normal owner URL is **https://aquacrop-bench.local/**. The Twin remains under the same site at `/twin/`; use its navigation. The main HTML independently reports that same build and the title `AquaCrop Greenhouse`. These checks did not sign in or operate equipment.

The VPN route was available, but the hostname initially returned DNS-name-not-found on the owner's PC. With the owner's explicit approval, a single Windows hosts mapping was added for the verified controller address, keeping a private backup. Afterwards Windows resolved the normal name correctly, and both normal HTTPS health URLs and the page loaded **without an IP override**. This is a client-side access correction, not an application deployment. Backups, network logs and the administrator helper remain private outside Git.

The health checks retained certificate-chain/hostname validation and used curl's best-effort revocation option because the private Caddy certificate had no usable revocation information. No global TLS setting, firewall, router, controller configuration or output state was changed.

This mapping applies to this PC. Other VPN clients need the same approved name resolution, or properly configured VPN DNS. Claude should document the actual LAN address/reservation, canonical hostname and certificate-trust setup in the private commissioning record, and complete the signed-in VPN walkthrough. Keep the single canonical URL; do not replace it with new localhost dashboards, raw-IP browser URLs or public exposure.

## Requirement status

| Owner requirement | Current status |
|---|---|
| One bench dashboard and matching Twin release | Both independently report matching 0.7.5. Normal hostname access now works from this PC over VPN. |
| Overview focused on the drawing; Blocks read-only orientation; drawing authored in Twin | Implemented in the reviewed source. Current signed-in real-bench walkthrough remains unperformed. |
| Equipment/types navigation adjacent; unwanted Blocks links removed | Implemented with existing source/browser-copy evidence. |
| Clear Program Preview/Save errors and explicit review of newer block versions | Focused tests passed. Selected-version safety details still need to remain consistent, as noted in the latest review. |
| Existing Overview imported faithfully into Twin | Bench legacy conversion fixture and graph-copy checks passed; unresolved symbols/sketches remain visibly non-hydraulic. End-to-end signed-in import/edit/export/apply acceptance is still required. |
| Capacity-sized tanks and level sensors placed inside tanks without pipes | Basic size/association/carry checks pass. Rejected tank movement leaves its sensor displaced (C075-06). Existing sizing uses placement or an explicit Size from capacity action: verify the owner's requested preview/update when capacity is intentionally edited, while keeping import lossless. |
| Block conditions and maximum runtime | Present. The newer owner decision to block starts under superseded safety settings until review/validation/retesting is **instructions only**, not implemented. |
| Pump replacement finishes Sector 1's remaining cycle with Valve 1 open, then continues to Sector 2 | **Not implemented.** Existing code selects a lead before a start only. Duty selection UI and standby proof defects also remain. Physical routing, an already-busy replacement and accounting details are still unanswered in the owner-decision document. |
| Project exchange | Partial: legacy drawing omitted from export, stale preview can permit unreviewed draft writes, malformed records cause 500, and imported proposals lack a completed user-facing continuation. |
| Run history and charts | Present, but a tied-timestamp rebind can select the wrong historical channel and chart data (C075-05). |
| Labelled nutrient simulation, real dosing blocked | Focused software tests passed; not a commissioned dosing capability. |
| Final acceptance/release evidence | Not complete: open defects, outstanding browser journeys, and no confirmed green final CI. Health/version checks do not establish functional acceptance. |

For the seven independently reproduced defects, see **[CODEX_REVIEW_0.7.5_20261027.md](CODEX_REVIEW_0.7.5_20261027.md)**. The 66 passing checks and seven defect observations remain applicable because application code is unchanged; they were not needlessly rerun for documentation-only commits.

## Newly checked CI evidence

The CI run after the Python 3.11 fix, [40503487329](https://github.com/josemaria1992/Canarias_GreenHouse/actions/runs/40503487329), completed with failure at `2b3b8a8`. Its UI job reports **99 passed, 1 failed**:

```text
FAIL BOARD-01 one card per module, every channel: menu has I/O board; 6 module cards, 80 channels
```

`scripts/ui_check/ui_check.cjs:832–836` requires both the captured navigation text to contain I/O board and the counts to be 6/80. The counts in the failure diagnostic are correct, so the navigation assertion needs investigation. The text "menu has I/O board" is hardcoded in the diagnostic and does not prove the navigation assertion passed. Do not infer a missing 80-channel board or call this a harmless flake without investigating menu readiness/visibility and retaining the coverage.

At this check, the release-source `caf0f88` runs and the latest `3a8d844` runs remain queued. A later version-label change does not itself resolve a failed UI check. Do not report final CI green until the relevant jobs actually finish successfully.

## Comments / next instructions for Claude

1. **Fix C075-01 through C075-07 first**, using the existing proof, graph, import and history layers and the included reproducible observations. Do not claim all features complete while those defects remain.
2. **Implement the approved owner decisions** from `OWNER_DECISIONS_20261027.md`: gate new starts after validated block-safety changes until explicit review/validation/retesting; preserve the confirmed mid-cycle Pump 1 → Pump 2 replacement for Sector 1 with Valve 1 open and remaining time retained, then Sector 2. Ask the remaining concrete questions instead of choosing answers. Test the failure cases on a fake/isolated system.
3. **Complete the owner journeys**, especially creating/configuring a tank, the capacity preview, real pointer drag/undo, faithful import/export and usable project proposals. Screenshots and API 200 responses alone do not replace those journeys. Keep all diagram editing/proposals within the agreed page responsibilities.
4. **Resolve the known CI failure and preflight on Python 3.11** using the built package and real dependencies on an isolated copy, including authenticated Preview/Save. A healthy 0.7.4 process did not detect its broken Programs feature. Full required checks should run after the corrections stabilize, before another deployment.
5. **Finish and document VPN access**, maintaining the same hostname on every authorized client, then check the signed-in real bench without operating outputs. Record actual deployed main/Twin builds and outstanding limitations. The local hostname fix above does not establish cross-client VPN DNS.
6. **Keep the workflow efficient**: one concise `CLAUDE_UPDATE.md` response naming the C075 findings, exact implementation commit and evidence paths. Use affected tests while iterating, then final required checks once after stabilizing; avoid repeated full logs and historical-package rereads. Consider docs-only CI handling that preserves required check semantics rather than queueing the whole application matrix for review-text edits. Do not suppress real failures.

Keep PR #11 draft and preserve owner data. This review did not authorize physical commands, commissioning or a merge.
