/* Tema chiaro / scuro / automatico, condiviso da tutte le pagine. File generato da genera_sito.py. */
(function () {
  var TEMI = ["auto", "light", "dark"];
  var NOMI = { auto: "Tema automatico", light: "Tema chiaro", dark: "Tema scuro" };
  var ICONE = {
    auto: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor"/></svg>',
    light: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
    dark: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/></svg>'
  };
  function salvato() { try { return localStorage.getItem("tema") || "auto"; } catch (e) { return "auto"; } }
  function applica(t) {
    if (t === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", t);
    try { localStorage.setItem("tema", t); } catch (e) {}
    var b = document.getElementById("bottone-tema");
    if (b) { b.innerHTML = ICONE[t]; b.title = NOMI[t]; b.setAttribute("aria-label", NOMI[t] + " (clic per cambiare)"); }
  }
  function avvia() {
    applica(salvato());
    var b = document.getElementById("bottone-tema");
    if (b) b.addEventListener("click", function () { applica(TEMI[(TEMI.indexOf(salvato()) + 1) % TEMI.length]); });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", avvia); else avvia();
})();
