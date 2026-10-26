// Runs in <head> before the first paint so the right theme shows at once.
// The choice ("auto" | "light" | "dark") is saved in Settings, and the server writes it into
// <html data-choice>; "auto" follows the system. Listeners hear every change, including a
// system switch while the choice is "auto", so every theme control can stay in sync.
(function () {
  var root = document.documentElement;
  var choice = root.getAttribute("data-choice") || "auto";
  var system = window.matchMedia("(prefers-color-scheme: dark)");
  var listeners = [];
  function apply() {
    var dark = choice === "dark" || (choice === "auto" && system.matches);
    root.dataset.theme = dark ? "dark" : "light";
    root.setAttribute("data-choice", choice);
    for (var i = 0; i < listeners.length; i++) {
      try { listeners[i](root.dataset.theme, choice); } catch (err) { console.error(err); }
    }
  }
  window.studioTheme = {
    get: function () { return choice; },
    resolved: function () { return root.dataset.theme; },
    set: function (c) { choice = c === "light" || c === "dark" ? c : "auto"; apply(); },
    onChange: function (fn) { listeners.push(fn); },
  };
  system.addEventListener("change", apply);
  apply();
})();
