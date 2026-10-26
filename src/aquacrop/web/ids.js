/* Name -> ID (0.7.1, owner decision): the same rule as aquacrop/ids.py `id_from_name`, so the ID shown while typing is
   the one the server would suggest. `Valve 1` -> `Valve_1`; `Irrigation Pump 2` -> `IrrigationPump_2`. The case is
   kept. Once the user edits the ID it is theirs: typing the name no longer changes it. */
"use strict";
(function () {
  const PUBLIC_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,47}$/;
  const RULE = "1-48 letters (any case), digits, - or _, starting with a letter or digit";
  function fromName(name) {
    const s = String(name || "").normalize("NFKD").replace(/[^\x00-\x7f]/g, "").trim();
    const m = /^(.*?)[\s_-]*(\d+)$/.exec(s);
    let base = m ? m[1] : s;
    const num = m ? m[2] : "";
    base = base.replace(/\s+/g, "").replace(/[^A-Za-z0-9_-]/g, "").replace(/^[_-]+|[_-]+$/g, "");
    const out = base && num ? `${base}_${num}` : base || num;
    return out.replace(/^[_-]+/, "").slice(0, 48);
  }
  /** Wire a name input and an ID input: the ID follows the name until the user types in the ID itself. */
  function follow(nameInput, idInput, onChange) {
    const sync = () => {
      if (idInput.dataset.owned !== "1") { idInput.value = fromName(nameInput.value); idInput.dataset.dirty = "1"; }
      if (onChange) onChange();
    };
    nameInput.addEventListener("input", sync);
    idInput.addEventListener("input", () => { idInput.dataset.owned = idInput.value ? "1" : ""; if (!idInput.value) sync(); else if (onChange) onChange(); });
    return sync;
  }
  function problem(id) { return PUBLIC_RE.test(id || "") ? null : `ID: use ${RULE}.`; }
  const api = { fromName, follow, problem, PUBLIC_RE, RULE };
  if (typeof window !== "undefined") window.AqIds = api;
  if (typeof module !== "undefined") module.exports = api;
})();
