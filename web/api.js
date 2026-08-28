const API = (() => {
  let csrfToken = "";

  async function request(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    if (options.method && options.method !== "GET" && csrfToken) headers.set("X-CSRF-Token", csrfToken);
    const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
    const contentType = response.headers.get("content-type") || "";
    const body = contentType.includes("application/json") ? await response.json() : await response.text();
    if (!response.ok) {
      const message = typeof body === "object" && body?.error ? body.error : `Request failed (${response.status})`;
      const error = new Error(message);
      error.status = response.status;
      error.payload = body;
      throw error;
    }
    return body;
  }

  function setCsrf(value) { csrfToken = value || ""; }
  function get(path) { return request(path); }
  function send(path, method, data) {
    return request(path, { method, body: JSON.stringify(data || {}) });
  }

  return { get, send, setCsrf };
})();
