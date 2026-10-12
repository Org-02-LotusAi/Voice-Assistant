// Login page: POST /auth/login sets the session cookie; the next page learns its CSRF token from GET /auth/me.
(function () {
  const { api, ApiError, showError, setApiKeyMode } = window.S015;
  const $ = (id) => document.getElementById(id);

  function message(e) {
    if (e instanceof ApiError && e.status === 401) return new Error("Invalid username or password.");
    if (e instanceof ApiError && e.status === 429) return new Error("Too many failed attempts. Wait a few minutes before trying again.");
    return e;
  }

  $("form").onsubmit = async (ev) => {
    ev.preventDefault();
    showError("globalError", null);
    $("submit").disabled = true;
    try {
      const r = await api("POST", "/auth/login", { username: $("username").value.trim(), password: $("password").value }, { noAuth: true });
      $("password").value = "";
      setApiKeyMode(false);  // a cookie sign-in must not be shadowed by a stale Bearer-key mode
      if (r.user.must_change_password) location.assign("/change-password");
      else if (r.user.role === "viewer") location.assign("/monitor.html");
      else location.assign("/");
    } catch (e) {
      $("password").value = "";
      showError("globalError", message(e));
      $("submit").disabled = false;
    }
  };
})();
