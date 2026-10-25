/* Built-in equipment icons (0.5.0), shared by the dashboard and the twin page. Outline glyphs drawn from fixed
 * path data; nothing is uploaded and an icon never decides what a channel does. */
"use strict";
(function () {
  const NS = "http://www.w3.org/2000/svg";
  // 24x24 outline glyphs, one path list each (stroke only).
  const GLYPHS = {
    pump: ["M12 4a8 8 0 1 0 0 16a8 8 0 1 0 0-16", "M12 4h8", "M9 9l6 3l-6 3z"],
    valve: ["M3 8v8l9-4z", "M21 8v8l-9-4z", "M12 12V5", "M8 5h8"],
    tank: ["M5 4h14v16H5z", "M5 13h14"],
    level: ["M7 3h10v18H7z", "M7 14h10", "M4 14h2", "M18 14h2"],
    flow: ["M3 12h14", "M13 8l4 4l-4 4", "M3 7h6", "M3 17h6"],
    pressure: ["M12 4a8 8 0 1 0 0 16a8 8 0 1 0 0-16", "M12 12l4-4"],
    temperature: ["M10 4h4v10a4 4 0 1 1-4 0z", "M12 9v7"],
    button: ["M6 10h12v6H6z", "M9 10V7h6v3"],
    lamp: ["M9 18h6", "M10 21h4", "M12 3a6 6 0 0 0-3 11v2h6v-2a6 6 0 0 0-3-11"],
    fan: ["M12 12m-1 0a1 1 0 1 0 2 0a1 1 0 1 0-2 0", "M12 11c0-4 2-7 4-7s1 5-4 7", "M13 12c4 0 7 2 7 4s-5 1-7-4",
      "M12 13c0 4-2 7-4 7s-1-5 4-7", "M11 12c-4 0-7-2-7-4s5-1 7 4"],
    heater: ["M5 6h14v12H5z", "M8 9v6", "M12 9v6", "M16 9v6"],
    motor: ["M4 7h12v10H4z", "M16 12h5", "M7 7V5h6v2"],
    sensor: ["M12 12m-2 0a2 2 0 1 0 4 0a2 2 0 1 0-4 0", "M7 7a7 7 0 0 0 0 10", "M17 7a7 7 0 0 1 0 10"],
    switch: ["M4 12h5", "M15 12h5", "M9 12l6-4"],
    drop: ["M12 3c3 5 6 8 6 11a6 6 0 0 1-12 0c0-3 3-6 6-11"],
    gauge: ["M4 16a8 8 0 1 1 16 0", "M12 16l4-5"],
    plug: ["M9 3v5", "M15 3v5", "M6 8h12v4a6 6 0 0 1-12 0z", "M12 18v3"],
    generic: ["M5 5h14v14H5z"],
  };
  function icon(name, size) {
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("width", size || 28); svg.setAttribute("height", size || 28);
    svg.setAttribute("class", "eq-icon"); svg.setAttribute("aria-hidden", "true");
    for (const d of GLYPHS[name] || GLYPHS.generic) {
      const p = document.createElementNS(NS, "path"); p.setAttribute("d", d); svg.appendChild(p);
    }
    return svg;
  }
  window.AqIcons = { icon, names: Object.keys(GLYPHS) };
})();
