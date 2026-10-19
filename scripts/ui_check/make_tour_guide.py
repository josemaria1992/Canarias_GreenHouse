"""Build the offline illustrated owner tour (owner-tour/index.html) from docs/operator/walkthrough.md and the
screenshots of Claude's browser rehearsal (run_tour_rehearsal.py --out DIR).

The page is self-contained apart from its img/ folder: no script, no font or style from the internet. The
screenshots and the rehearsal's checks are labelled as Claude's rehearsal. It opens with "Starting AquaCrop":
the start steps and the console text of a real first start (first-start.txt from the rehearsal).

results.html next to it is the result form: one record per person, either the owner's tour (UAT-01 to UAT-16)
or a first-time operator's Getting started tasks, kept apart. Results are saved only by its Download button
(a CSV file to send back); nothing in the printed guide records a result. Every owner result starts NOT RUN.

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
    "START-login": "Sign-in page",
    "START-getting-started": "Getting started: four tasks with pictures",
    "UAT-01-overview": "Overview after sign-in: SIMULATION banner, plain status line and its Details",
    "UAT-01-versions": "Settings, Versions: release, source commit, config and the 80-point inventory",
    "UAT-02-nutrients": "Fertiliser A opens Nutrients & acid", "UAT-02-main-tank": "Main tank opens Main tank",
    "UAT-02-main-pumps": "Main pump 1 opens Watering", "UAT-02-sector-S1": "Sector 1 opens Sector 1",
    "UAT-02-sector-S2": "Sector 2 opens Sector 2", "UAT-02-drainage": "Drainage tank opens Drainage return",
    "UAT-03-irrigating-S1": "Sector 1 with main pump 1: what was asked and what the equipment reports, shown apart",
    "UAT-03-changeover": "Changeover: drive stopped and confirmed before the pump selection changes",
    "UAT-03-irrigating-S2": "Sector 2 with main pump 2",
    "UAT-03-transition-history": "Watering card, Details: history of this process (select, open, valve lead, start, verify flow, irrigate, stop drive)",
    "UAT-04-manual-MP2-S1": "Run once: main pump 2 on sector 1, bounded watering time", "UAT-04-stopped": "After Stop watering: all equipment off",
    "UAT-05-invalid": "Check settings refuses the out-of-range refill start level and says why beside the field",
    "UAT-05-editing-sequence": "Changing the watering sequence of the draft",
    "UAT-05-activated": "demo-sim@v2 in use; the running step keeps v1", "UAT-05-rolled-back": "Restore previous settings: demo-sim@v1 in use again",
    "UAT-06-ec-target-batch": "Refill until the EC target: water and staged A/B", "UAT-06-ec-target-done": "Refill completed at the fill level",
    "UAT-06-fixed-recipe": "Set amounts recipe tour-fixed@v1 in use", "UAT-06-fixed-done": "Set amounts refill completed, EC checked",
    "UAT-07-fill-invalidates": "Fertiliser tank A needs preparing again after the water fill",
    "UAT-08-settling": "Acid dose followed by the wait for mixing", "UAT-08-ph-unusable": "No usable pH reading: dosing waits, with the reason",
    "UAT-09-return-running": "Return pump running from a high drainage tank", "UAT-09-hold-main-full": "Main tank full: drainage return on hold with a problem listed",
    "UAT-10-drive-trip": "Drive trip: watering stopped because of a problem, with the steps to recover", "UAT-10-acknowledged": "Marked as seen: still stopped, nothing restarted",
    "UAT-10-valve-stuck": "Sector 1 valve stays closed: watering stopped, the drive never ran",
    "UAT-11-invalid-level": "Main tank level outside the allowed range: watering stops", "UAT-11-module-timeout": "analog_B No reading, the other modules Reading valid",
    "UAT-11-stale-acquisition": "Acquisition stalled: readings grow old", "UAT-11-ec-boundary": "EC lost at valve opening: step STOPPED, no A/B pump start",
    "UAT-11-audit": "Alarms & events: the problem, the commands sent and the event log",
    "UAT-12-io-test": "IO_TEST: injected inputs and switched relay and transistor outputs",
    "UAT-12-module-failed": "digital_A failed; the other modules stay online",
    "UAT-12-twin-unassigned": "Plant mode: the added channels read Unused channel",
    "UAT-13-history": "History with plain names, units, time zone and run events",
    "UAT-14-users": "Users and roles", "UAT-14-viewer-main-pumps": "Viewer: buttons disabled, the account needed named",
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
                out.append(f'<p class="result">Record the result of {case} in the <a href="results.html#{case}">result form</a> '
                           '(it saves only when you press <strong>Download results</strong>).</p>')
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
pre.console { background: #0c0c0c; color: #e6e6e6; border-radius: 6px; padding: 10px 12px; font: 13px/1.45 Consolas, "Cascadia Mono", monospace; white-space: pre-wrap; }
nav.toc { columns: 2; font-size: .95em; } @media (max-width: 640px) { nav.toc { columns: 1; } }
.pass { color: #2e7d32; } .fail { color: #c62828; }
"""


def starting(ev: Path, shots) -> str:
    """The guide's first section: how to start, the console text of a real first start, the first screens."""
    fs = ev / "first-start.txt"
    console = (f'<p>The console window of a first start, exactly as recorded in Claude\'s rehearsal (on Linux, the same '
               f'program; the throwaway folder is shown as &lt;data folder&gt;). On Windows the window first says '
               f'<code>Using the Python bundled with this release</code>, and the data folder is '
               f'<code>%LOCALAPPDATA%\\AquaCrop\\demo</code>. Passwords are never shown, not even as dots.</p>'
               f'<pre class="console">{html.escape(fs.read_text(encoding="utf-8"))}</pre>') if fs.exists() else ""
    pics = "".join(f'<figure><img loading="lazy" src="img/{p.name}" alt="{html.escape(CAPTIONS.get(p.stem, p.stem))}">'
                   f'<figcaption>{html.escape(CAPTIONS.get(p.stem, p.stem))}</figcaption></figure>'
                   for p in shots if p.stem.startswith("START-"))
    return f"""<h2 id="starting-aquacrop">Starting AquaCrop</h2>
<p>Nothing to install: the release folder carries its own Python (folder <code>runtime</code>). The same steps are
in <code>README-FIRST.md</code>, section 3.</p>
<ol>
<li>Right-click the ZIP, <strong>Properties</strong>, tick <strong>Unblock</strong> at the bottom if it is there, <strong>OK</strong>.</li>
<li>Right-click the ZIP, <strong>Extract All…</strong>, and extract to a folder that OneDrive does not sync, for example <code>C:\\AquaCrop</code>.</li>
<li>Open the extracted folder and double-click <strong>AquaCrop-Demo.cmd</strong>. If Windows says "Windows protected your PC",
choose <strong>More info</strong>, then <strong>Run anyway</strong>.</li>
<li>First start only: type the <strong>login name</strong> (no spaces or accents, for example <code>jose</code>) and press Enter;
then the <strong>password</strong> (at least 12 characters, not containing the login name) twice. Nothing appears while you
type the password. If an answer cannot be used, the window says why and asks again.</li>
<li>The browser opens. Sign in, then open <strong>Getting started</strong>, the first item of the menu.</li>
</ol>
{console}
{pics}
<p>To stop AquaCrop, close the black window or press Ctrl+C in it. Your data stays in the data folder.</p>
"""


RESULTS_JS = r"""
"use strict";
const $ = id => document.getElementById(id);
function show() {
  const who = document.querySelector("input[name=who]:checked").value;
  $("owner-part").hidden = who !== "owner"; $("operator-part").hidden = who !== "operator";
}
document.querySelectorAll("input[name=who]").forEach(r => r.addEventListener("change", show));
function q(v) { v = v == null ? "" : String(v); if (/^[=+\-@]/.test(v)) v = "'" + v; return /[",\n\r]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; }
$("download").addEventListener("click", () => {
  const who = document.querySelector("input[name=who]:checked").value, name = $("name").value.trim();
  if (!name) { $("msg").textContent = "Write your name first."; $("name").focus(); return; }
  const rows = [["record", who === "owner" ? "owner tour" : "first-time operator, Getting started"], ["release", $("release").textContent],
    ["name", name], ["date", $("date").value], ["computer", $("pc").value], []];
  if (who === "owner") {
    rows.push(["case", "title", "result", "comments", "screenshot"]);
    document.querySelectorAll("#owner-part fieldset").forEach(f => rows.push([f.dataset.case, f.dataset.title,
      f.querySelector("input[type=radio]:checked").value, f.querySelector("textarea").value, f.querySelector("input[type=text]").value]));
  } else {
    rows.push(["task", "done", "minutes", "what was unclear"]);
    document.querySelectorAll("#operator-part fieldset").forEach(f => rows.push([f.dataset.title,
      f.querySelector("input[type=radio]:checked").value, f.querySelector("input[type=number]").value, f.querySelector("textarea").value]));
  }
  const csv = rows.map(r => r.map(q).join(",")).join("\r\n") + "\r\n";
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob(["\ufeff" + csv], { type: "text/csv" }));
  a.download = `aquacrop-results-${who === "owner" ? "owner" : "operator"}-${name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}-${$("date").value}.csv`;
  document.body.appendChild(a); a.click(); a.remove();
  $("msg").textContent = "Saved as " + a.download + " in your Downloads folder. Send that file back; this page itself keeps nothing.";
});
$("date").value = new Date().toISOString().slice(0, 10);
show();
"""


def results_page(md: str, version: str) -> str:
    """The result form: the owner's tour cases or a first-time operator's tasks, one person per file."""
    cases = re.findall(r"^### (UAT-\d\d) (.*)$", md, re.M)
    radio = lambda name, opts, default: "".join(
        f'<label><input type="radio" name="{name}" value="{v}"{" checked" if v == default else ""}> {v}</label> ' for v in opts)
    owner = "".join(
        f'<fieldset id="{c}" data-case="{c}" data-title="{html.escape(t)}"><legend>{c} {inline(t)}</legend>'
        f'{radio("r-" + c, ["PASS", "FAIL", "NOT RUN"], "NOT RUN")}'
        f'<label>Comments <textarea rows="2" name="c-{c}"></textarea></label>'
        f'<label>Screenshot file name (optional) <input type="text" name="s-{c}"></label></fieldset>' for c, t in cases)
    tasks = ["Read the tank level", "Water one sector", "Stop watering", "Recover from a simulated problem"]
    operator = "".join(
        f'<fieldset data-title="{t}"><legend>{i + 1}. {t}</legend>'
        f'{radio("o-" + str(i), ["Done without help", "Done with help", "Could not do it", "Not tried"], "Not tried")}'
        f'<label>About how many minutes <input type="number" min="0" max="240" name="m-{i}"></label>'
        f'<label>What was unclear (the screen, the words, the next step) <textarea rows="2" name="u-{i}"></textarea></label></fieldset>'
        for i, t in enumerate(tasks))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>AquaCrop Results</title><style>{CSS}
fieldset {{ border: 1px solid var(--line); border-radius: 6px; margin: 10px 0; padding: 8px 12px; }}
legend {{ font-weight: 700; }} label {{ display: block; margin: 4px 0; }} label:has(input[type=radio]) {{ display: inline-block; margin-right: 14px; }}
textarea, input[type=text], input[type=number], input[type=date] {{ font: inherit; width: 100%; max-width: 640px; }}
button {{ font: inherit; font-weight: 700; padding: 8px 16px; background: var(--accent); color: #fff; border: 0; border-radius: 6px; cursor: pointer; }}
</style></head>
<body><main>
<h1>AquaCrop {html.escape(version)}: result form</h1>
<p class="note">One form per person. Fill it in, then press <strong>Download results</strong>: that saves a CSV file in your
Downloads folder, which you send back. <strong>This page keeps nothing</strong>: closing, reloading or printing it saves no
result. Owner results start as NOT RUN and stay so until the owner runs the tour.</p>
<fieldset><legend>Who is testing</legend>
<label><input type="radio" name="who" value="owner" checked> The owner, running the acceptance tour (UAT-01 to UAT-16)</label><br>
<label><input type="radio" name="who" value="operator"> A first-time operator, trying the four Getting started tasks without training</label>
<label>Name <input type="text" id="name" autocomplete="name"></label>
<label>Date <input type="date" id="date"></label>
<label>Computer and browser (optional) <input type="text" id="pc" placeholder="for example Windows 11 laptop, Edge"></label>
<p class="muted">Release: <span id="release">{html.escape(version)}</span></p></fieldset>
<section id="owner-part"><h2>Owner tour</h2><p class="muted">The steps of each case are in the <a href="index.html">illustrated guide</a>.</p>{owner}</section>
<section id="operator-part" hidden><h2>Getting started tasks</h2>
<p class="muted">The tasks are in AquaCrop itself: menu, <strong>Getting started</strong>. Please try them without asking for help first, and
note what was unclear. This record is kept apart from the owner's acceptance results.</p>{operator}</section>
<p><button type="button" id="download">Download results</button></p>
<p id="msg" role="status"></p>
</main><script>{RESULTS_JS}</script></body></html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--evidence", type=Path, required=True, help="output folder of run_tour_rehearsal.py")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--walkthrough", type=Path, default=ROOT / "docs" / "operator" / "walkthrough.md")
    ap.add_argument("--release-version", default="", help="shown on the result form")
    args = ap.parse_args()
    ev, out = args.evidence.resolve(), args.out.resolve()
    reh = json.loads((ev / "rehearsal.json").read_text(encoding="utf-8"))
    # the launcher by its name in the release folder, not the rehearsal machine's path
    launcher = reh.get("launcher", "")
    reh["launcher"] = f"{Path(launcher).name} of this release folder" if "/" in launcher or "\\" in launcher else launcher
    shutil.rmtree(out, ignore_errors=True)
    (out / "img").mkdir(parents=True)
    shots = sorted(p for p in (*ev.glob("UAT-*.png"), *ev.glob("UAT-*.jpg"), *ev.glob("START-*.jpg")) if not p.stem.endswith("-error"))
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
    body = starting(ev, shots) + markdown(md, figures)
    cases = re.findall(r"^### (UAT-\d\d .*)$", md, re.M)
    toc = "<nav class=\"toc\"><ul><li><a href=\"#starting-aquacrop\">Starting AquaCrop</a></li>" + "".join(f'<li><a href="#{slug(c)}">{html.escape(c)}</a></li>' for c in cases) + "</ul></nav>"
    checks = "".join(f'<tr><td>{html.escape(c["id"])}</td><td class="{"pass" if c["ok"] else "fail"}">{"pass" if c["ok"] else "FAIL"}</td>'
                     f'<td>{html.escape(c["detail"][:240])}</td></tr>' for c in reh["checks"])
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>AquaCrop Owner Tour</title><style>{CSS}</style></head>
<body><main>
<p class="note">Offline guide. Screenshots come from <strong>Claude's browser rehearsal</strong> of this release
({html.escape(reh.get("launcher", ""))}; headless Chromium at 1366×1000, narrow checks at 390 and 820 px). They show what
to expect; they are <strong>not your results</strong>. Your results go in <code>owner-checklist.csv</code> and all
start as NOT RUN. Record them with the <a href="results.html">result form</a>. A narrow browser window is not
evidence of a phone over a VPN.</p>
{toc}
{body}
<h2 id="claude-rehearsal">Claude's rehearsal record (not owner acceptance)</h2>
<p>{reh["passed"]} checks passed, {reh["failed"]} failed. Owner results: <strong>{html.escape(reh["owner_results"])}</strong>.
Physical verification: {"yes" if reh.get("physical_verification") else "no"}.</p>
<table><thead><tr><th>Check</th><th>Rehearsal</th><th>Observed</th></tr></thead><tbody>{checks}</tbody></table>
</main></body></html>
"""
    (out / "index.html").write_text(page, encoding="utf-8")
    (out / "results.html").write_text(results_page(md, reh.get("release_version") or args.release_version), encoding="utf-8")
    (out / "claude-rehearsal.json").write_text(json.dumps(reh, indent=1), encoding="utf-8")
    print(json.dumps({"out": str(out), "screenshots": len(shots), "cases": len(cases), "rehearsal_failed": reh["failed"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
