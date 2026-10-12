// Change-password page: forced when must_change_password, otherwise reachable from the header.
(function () {
  const { api, showError, init } = window.S015;
  const $ = (id) => document.getElementById(id);
  let me = null;

  $("form").onsubmit = async (ev) => {
    ev.preventDefault();
    showError("globalError", null); $("msg").textContent = "";
    if ($("next").value !== $("again").value) { showError("globalError", new Error("The new passwords do not match.")); return; }
    if (!me) { showError("globalError", new Error("Your session could not be loaded; reload the page.")); return; }
    $("submit").disabled = true;
    try {
      await api("POST", "/auth/change-password", { current_password: $("current").value, new_password: $("next").value });
      for (const id of ["current", "next", "again"]) $(id).value = "";
      $("msg").textContent = "Password changed. Redirecting...";
      location.assign(me.effective_role === "viewer" ? "/monitor.html" : "/");
    } catch (e) {
      showError("globalError", e);
      $("submit").disabled = false;
    }
  };

  init({ allowPasswordChange: true, anyRole: true }).then((m) => {
    me = m;
    if (!m) return;
    if (m.kind !== "user") {
      showError("globalError", new Error("Only a signed-in account can change its password (not API key mode)."));
      $("submit").disabled = true;
    }
    if (m.must_change_password) $("forced").classList.remove("hidden");
  });
})();
