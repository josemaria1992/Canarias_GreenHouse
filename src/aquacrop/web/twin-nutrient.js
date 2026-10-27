// 0.7.3 PROG-10: a labelled simulation of a nutrient preparation with the synthetic chemistry fixture. Nothing is
// dosed, stored or proposed as a recipe; a program over a nutrient preparation block never starts on the real system.
(function () {
  "use strict";
  const T = window.AqTwin, { h, api } = T;
  const put = (el, ...kids) => el.replaceChildren(...kids.flat(Infinity).filter(k => k != null && k !== false));
  function card() {
    const out = h("div", { "data-nutrient-result": "" });
    const num = (label, v, max) => h("label", null, label + " ", h("input", { type: "number", min: 0, max, step: "any", class: "w-num", value: v, "aria-label": label }));
    const vol = num("Mains water (L)", "", 100000), a = num("Stock A (L)", "", 1000), b = num("Stock B (L)", "", 1000),
      acid = num("Acid after 10 min (L)", "", 1000);
    const val = el => Number(el.querySelector("input").value || 0);
    const run = async () => {
      const additions = [["A", a, 0], ["B", b, 0], ["ACID", acid, 600]].filter(([, el]) => val(el) > 0)
        .map(([what, el, at]) => ({ what, litres: val(el), at_s: at }));
      if (!(val(vol) > 0) || !additions.length) { put(out, h("p", { class: "bad small" }, "Enter the water volume and at least one addition.")); return; }
      try {
        const r = await api("POST", "api/twin/nutrient/simulate", { volume_L: val(vol), additions, duration_s: 3600 });
        const f = r.final, s = r.samples, q = s[Math.min(s.length - 1, 4)];
        put(out, h("p", { class: "pill warn" }, "SIMULATED"), h("p", { class: "small" }, r.label),
          h("p", { class: "small" }, `After an hour: EC ${f.ec_bulk} mS/cm, pH ${f.ph_bulk} in the tank (the probe reads EC ${f.ec_probe}, pH ${f.ph_probe}); ${f.volume_L} L. After ${q.t_s / 60} min the probe still read EC ${q.ec_probe}.`),
          h("ul", { class: "muted small" }, r.notes.map(n => h("li", null, n))));
      } catch (e) { put(out, h("p", { class: "bad small" }, String(e.message || e))); }
    };
    return h("div", { class: "card", id: "twin-nutrient" }, h("h2", null, "Nutrient preparation (simulated)"),
      h("p", { class: "muted small" }, "What the synthetic chemistry fixture predicts for stock and acid added to mains water. It is not a recipe and gives no dosing amounts; real dosing stays blocked."),
      h("div", { class: "row wrap" }, vol, a, b, acid, h("button", { onclick: run }, "Simulate")), out);
  }
  window.AqTwinNutrient = { card };
})();
