"""Build the offline illustrated owner tour (owner-tour/index.html) from docs/operator/walkthrough.md and the
screenshots of Claude's browser rehearsal (run_tour_rehearsal.py --out DIR).

The page is self-contained apart from its img/ folder: no script, no font or style from the internet. The
screenshots and the rehearsal's checks are labelled as Claude's rehearsal; the owner's results stay in
owner-checklist.csv, all NOT RUN until the owner runs the tour.

    python scripts/ui_check/make_tour_guide.py --evidence tour-evidence --out dist/owner-tour
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# what each screenshot shows, in the order the guide places them
CAPTIONS = {
    "UAT-01-overview": "Overview after sign-in: SIMULATION banner and status line",
    "UAT-01-versions": "Settings, Versions: release, source commit, config and the 80-point inventory",
    "UAT-02-nutrients": "Stock A opens Nutrients & acid", "UAT-02-main-tank": "Main tank opens Main tank",
    "UAT-02-main-pumps": "Main pump MP1 opens Main pumps", "UAT-02-sector-S1": "Sector S1 opens Sector 1",
    "UAT-02-sector-S2": "Sector S2 opens Sector 2", "UAT-02-drainage": "Sump opens Drainage",
    "UAT-03-irrigating-S1": "Sector 1 with MP1: requested and observed shown apart",
    "UAT-03-changeover": "Changeover: drive stopped and verified before the pump selection changes",
    "UAT-03-irrigating-S2": "Sector 2 with MP2",
    "UAT-03-transition-history": "Irrigation transition history: select, open, valve lead, start, verify flow, irrigate, stop drive",
    "UAT-04-manual-MP2-S1": "MANUAL step: MP2 on sector 1, bounded duration", "UAT-04-stopped": "After STOP: all outputs off",
    "UAT-05-invalid": "Validate refuses the out-of-range refill trigger and says why",
    "UAT-05-editing-sequence": "Editing the irrigation sequence of the draft",
    "UAT-05-activated": "demo-sim@v2 active; the running step keeps v1", "UAT-05-rolled-back": "Rolled back to demo-sim@v1",
    "UAT-06-ec-target-batch": "EC_TARGET batch: water and staged A/B", "UAT-06-ec-target-done": "Batch completed at the final level",
    "UAT-06-fixed-recipe": "FIXED_VOLUMES recipe tour-fixed@v1 active", "UAT-06-fixed-done": "FIXED_VOLUMES batch completed, EC validated",
    "UAT-07-fill-invalidates": "Stock A INVALIDATED after the water fill",
    "UAT-08-settling": "Acid dose followed by settling", "UAT-08-ph-unusable": "pH probe invalid: dosing inhibited with the reason",
    "UAT-09-return-running": "Return pump running from a high sump", "UAT-09-hold-main-full": "Main tank full: return on HOLD with alarm",
    "UAT-10-drive-trip": "Drive trip: irrigation lockout with the cause", "UAT-10-acknowledged": "Acknowledged: still locked out, nothing restarted",
    "UAT-10-valve-stuck": "Valve S1 stuck closed: lockout, drive never ran",
    "UAT-11-invalid-level": "Main level INVALID: irrigation locks out", "UAT-11-module-timeout": "analog_B UNAVAILABLE, the other modules GOOD",
    "UAT-11-stale-acquisition": "Acquisition stalled: data ages", "UAT-11-ec-boundary": "EC lost at valve opening: step STOPPED, no A/B pump start",
    "UAT-11-audit": "Alarms & events: event and command audit",
    "UAT-12-io-test": "IO_TEST: injected inputs and switched relay and transistor outputs",
    "UAT-12-module-failed": "digital_A failed; the other modules stay online",
    "UAT-12-twin-unassigned": "Plant mode: the added channels read UNASSIGNED",
    "UAT-13-trends": "Trends with units, time zone and run events",
    "UAT-14-users": "Users and roles", "UAT-14-viewer-main-pumps": "Viewer: controls disabled with the permission named",
    "UAT-14-operator-main-pumps": "Operator: simulation controls available",
    "UAT-15-backup": "Local backup created; off-device NOT ACTIVATED", "UAT-15-after-restart": "After restart: history kept",
    "UAT-16-connection-lost": "Server stopped: CONNECTION LOST, values STALE, commands disabled",
}


def inline(t: str) -> str:
    t = html.escape(t, quote=False)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![\w*])\*([^*\s][^*]*?)\*(?![\w*])", r"<em>\1</em>", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', t)
    t = re.sub(r"&lt;(https?://[^&]+)&gt;", r'<a href="\1">\1</a>', t)
    return t


def slug(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")


def markdown(md: str, figures) -> str:
    """The subset of Markdown the walkthrough uses. figures(case_id) returns HTML placed before the case's
    'Your result' line."""
    out, lines, i, case = [], md.splitlines(), 0, None
    para: list[str] = []

    def flush():
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>")
            para.clear()
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            flush()
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            out.append("<pre>" + html.escape("\n".join(lines[i + 1:j])) + "</pre>")
            i = j + 1
            continue
        m = re.match(r"(#{1,4}) (.*)", ln)
        if m:
            flush()
            n, title = len(m.group(1)), m.group(2)
            cm = re.match(r"(UAT-\d\d) ", title)
            case = cm.group(1) if cm else case
            out.append(f'<h{n} id="{slug(title)}">{inline(title)}</h{n}>')
            i += 1
            continue
        if ln.strip() == "---":
            flush(); out.append("<hr>"); i += 1; continue
        if ln.startswith("|"):
            flush()
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", c) for c in cells if c):
                    rows.append(cells)
                i += 1
            head, body = rows[0], rows[1:]
            th = "" if not any(head) else "<thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead>"
            if not any(head):
                body = rows[1:]
            out.append("<table>" + th + "<tbody>" + "".join(
                "<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body) + "</tbody></table>")
            continue
        if re.match(r"\s*(\d+\.|-) ", ln):
            flush()
            # one list block, nested items indented by three spaces
            html_list, stack = [], []
            while i < len(lines) and (re.match(r"\s*(\d+\.|-) ", lines[i]) or (lines[i].startswith("  ") and lines[i].strip())):
                l2 = lines[i]
                m2 = re.match(r"(\s*)(\d+\.|-) (.*)", l2)
                if not m2:                                 # continuation of the previous item
                    html_list[-1] = html_list[-1][:-5] + " " + inline(l2.strip()) + "</li>"
                    i += 1
                    continue
                depth, kind = len(m2.group(1)) // 3, "ol" if m2.group(2)[0].isdigit() else "ul"
                while len(stack) > depth + 1:
                    html_list.append(f"</{stack.pop()}>")
                if len(stack) < depth + 1:
                    stack.append(kind); html_list.append(f"<{kind}>")
                html_list.append(f"<li>{inline(m2.group(3))}</li>")
                i += 1
            while stack:
                html_list.append(f"</{stack.pop()}>")
            out.append("".join(html_list))
            continue
        if ln.startswith("Your result:"):
            flush()
            if case:
                out.append(figures(case))
            out.append('<p class="result">Your result: ☐ PASS ☐ FAIL ☐ <strong>NOT RUN</strong> · Comments: ______ · '
                       'Screenshot: ______ <span class="muted">(record it in owner-checklist.csv)</span></p>')
            i += 1
            continue
        if not ln.strip():
            flush(); i += 1; continue
        para.append(ln.strip())
        i += 1
    flush()
    return "\n".join(out)


CSS = """
:root { --ink:#1d2733; --muted:#5b6675; --line:#d5dbe3; --accent:#1f6f8b; --bg:#ffffff; --card:#f5f7fa; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --ink:#e4e9ef; --muted:#a3adb9; --line:#3a4552; --accent:#6cc3e0; --bg:#14191f; --card:#1d242c; } }
body { margin:0; background:var(--bg); color:var(--ink); font:16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 980px; margin: 0 auto; padding: 16px; }
h1 { font-size: 1.7rem; } h2 { margin-top: 2.2rem; border-bottom: 1px solid var(--line); } h3 { margin-top: 2rem; }
table { border-collapse: collapse; width: 100%; margin: .6rem 0; display: block; overflow-x: auto; }
td, th { border: 1px solid var(--line); padding: 4px 8px; text-align: left; vertical-align: top; }
code { background: var(--card); padding: 0 4px; border-radius: 3px; word-break: break-word; }
pre { background: var(--card); padding: 8px; overflow-x: auto; }
a { color: var(--accent); }
.note { background: var(--card); border-left: 4px solid var(--accent); padding: 8px 12px; }
.result { background: var(--card); padding: 8px 12px; border-radius: 6px; }
.muted { color: var(--muted); font-size: .9em; }
figure { margin: 12px 0; } figure img { max-width: 100%; border: 1px solid var(--line); border-radius: 4px; }
figcaption { color: var(--muted); font-size: .9em; }
details.shots summary { cursor: pointer; color: var(--accent); }
nav.toc { columns: 2; font-size: .95em; } @media (max-width: 640px) { nav.toc { columns: 1; } }
.pass { color: #2e7d32; } .fail { color: #c62828; }
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--evidence", type=Path, required=True, help="output folder of run_tour_rehearsal.py")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--walkthrough", type=Path, default=ROOT / "docs" / "operator" / "walkthrough.md")
    args = ap.parse_args()
    ev, out = args.evidence.resolve(), args.out.resolve()
    reh = json.loads((ev / "rehearsal.json").read_text(encoding="utf-8"))
    # the launcher by its name in the release folder, not the rehearsal machine's path
    launcher = reh.get("launcher", "")
    reh["launcher"] = f"{Path(launcher).name} of this release folder" if "/" in launcher or "\\" in launcher else launcher
    shutil.rmtree(out, ignore_errors=True)
    (out / "img").mkdir(parents=True)
    shots = sorted(p for p in (*ev.glob("UAT-*.png"), *ev.glob("UAT-*.jpg")) if not p.stem.endswith("-error"))
    for p in shots:
        shutil.copy2(p, out / "img" / p.name)
    order = list(CAPTIONS)

    def figures(case: str) -> str:
        mine = [p for p in shots if p.stem.startswith(case + "-")]
        mine.sort(key=lambda p: (order.index(p.stem) if p.stem in order else len(order), p.stem))
        if not mine:
            return ""
        figs = "".join(f'<figure><img loading="lazy" src="img/{p.name}" alt="{html.escape(CAPTIONS.get(p.stem, p.stem))}">'
                       f'<figcaption>{html.escape(CAPTIONS.get(p.stem, p.stem.replace("-", " ")))}</figcaption></figure>'
                       for p in mine)
        return (f'<details class="shots" open><summary>Screenshots from Claude\'s rehearsal ({len(mine)})</summary>'
                f'{figs}</details>')
    md = args.walkthrough.read_text(encoding="utf-8")
    body = markdown(md, figures)
    cases = re.findall(r"^### (UAT-\d\d .*)$", md, re.M)
    toc = "<nav class=\"toc\"><ul>" + "".join(f'<li><a href="#{slug(c)}">{html.escape(c)}</a></li>' for c in cases) + "</ul></nav>"
    checks = "".join(f'<tr><td>{html.escape(c["id"])}</td><td class="{"pass" if c["ok"] else "fail"}">{"pass" if c["ok"] else "FAIL"}</td>'
                     f'<td>{html.escape(c["detail"][:240])}</td></tr>' for c in reh["checks"])
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>AquaCrop Owner Tour</title><style>{CSS}</style></head>
<body><main>
<p class="note">Offline guide. Screenshots come from <strong>Claude's browser rehearsal</strong> of this release
({html.escape(reh.get("launcher", ""))}; headless Chromium at 1366×1000, narrow checks at 390 and 820 px). They show what
to expect; they are <strong>not your results</strong>. Your results go in <code>owner-checklist.csv</code> and all
start as NOT RUN. A narrow browser window is not evidence of a phone over a VPN.</p>
{toc}
{body}
<h2 id="claude-rehearsal">Claude's rehearsal record (not owner acceptance)</h2>
<p>{reh["passed"]} checks passed, {reh["failed"]} failed. Owner results: <strong>{html.escape(reh["owner_results"])}</strong>.
Physical verification: {"yes" if reh.get("physical_verification") else "no"}.</p>
<table><thead><tr><th>Check</th><th>Rehearsal</th><th>Observed</th></tr></thead><tbody>{checks}</tbody></table>
</main></body></html>
"""
    (out / "index.html").write_text(page, encoding="utf-8")
    (out / "claude-rehearsal.json").write_text(json.dumps(reh, indent=1), encoding="utf-8")
    print(json.dumps({"out": str(out), "screenshots": len(shots), "cases": len(cases), "rehearsal_failed": reh["failed"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
