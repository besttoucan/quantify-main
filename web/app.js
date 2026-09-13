(() => {
  "use strict";

  const root = document.getElementById("app");
  const layer = document.getElementById("layer");
  const toastNode = document.getElementById("toast");

  // localStorage throws in a private window or when site data is blocked. Every
  // read and write goes through here so a blocked store can never stop boot.
  const store = {
    get: (key) => { try { return localStorage.getItem(key); } catch (_) { return null; } },
    set: (key, value) => { try { localStorage.setItem(key, value); } catch (_) { /* private mode */ } },
    remove: (key) => { try { localStorage.removeItem(key); } catch (_) { /* private mode */ } },
  };

  const S = {
    auth: null,
    boot: null,
    locationId: store.get("quantify.location") || "",
    view: store.get("quantify.view") || "today",
    date: "",
    data: null,
    historyTab: "days",
    settingsTab: store.get("quantify.stab") || "location",
    open: new Set(),
    pulse: { version: null, checkedAt: null, live: false, pending: false },
    // What each screen last showed, keyed by view, location and date, so coming
    // back to a screen paints at once and refreshes underneath.
    cache: {},
    itemCache: {},
    history: { days: [], nextBefore: null, hasMore: true, loading: false, range: "all", costs: null },
    costs: null,
    tour: null,
    order: { days: 3, edits: {}, extras: [] },
    todayPane: "make",
    outlook: null,
    menu: null,
    attention: null,
    orders: { rows: [], nextDate: null, nextSkip: 0, hasMore: true, loading: false, range: "all" },
    challenge: "",
    setup: null,
    onboarding: { step: 0, values: {}, tz: null },
    cancelFlow: null,
    narrativeTried: "",
  };

  /* ---------- helpers ---------- */
  const e = (v) => String(v ?? "")
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#039;");

  const money = (v, precise) => new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD",
    minimumFractionDigits: precise ? 2 : 0, maximumFractionDigits: precise ? 2 : 0,
  }).format(Number(v || 0));
  const num = (v) => new Intl.NumberFormat("en-US").format(Math.round(Number(v || 0)));
  const pct = (v) => `${Number(v) > 0 ? "+" : ""}${Math.round(Number(v || 0))}%`;
  const noun = (n, one, many) => `${num(n)} ${Math.abs(Math.round(Number(n || 0))) === 1 ? one : (many || one + "s")}`;

  // Dates arrive as YYYY-MM-DD, sometimes with a time on the end. Only the day
  // part is read, and a bad value formats as nothing instead of throwing.
  const dObj = (iso) => new Date(`${String(iso || "").slice(0, 10)}T12:00:00`);
  const dFmt = (iso, options) => {
    const d = dObj(iso);
    return Number.isNaN(d.getTime()) ? "" : new Intl.DateTimeFormat("en-US", options).format(d);
  };
  const dShort = (iso) => dFmt(iso, { month: "short", day: "numeric" });
  const dMed = (iso) => dFmt(iso, { weekday: "short", month: "short", day: "numeric" });
  const dLong = (iso) => dFmt(iso, { weekday: "long", month: "long", day: "numeric" });
  const weekday = (iso) => dFmt(iso, { weekday: "long" });
  const clock = (t) => {
    const [h, m] = String(t || "").split(":");
    const hour = Number(h);
    return `${((hour % 12) || 12)}:${m} ${hour < 12 ? "AM" : "PM"}`;
  };
  const localISO = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const addDays = (iso, n) => {
    const d = dObj(iso); d.setDate(d.getDate() + n);
    return localISO(d);
  };
  // "Today" is the location's own date, sent by /api/bootstrap and refreshed by
  // /api/pulse. The browser clock is only a fallback before the first answer.
  const todayISO = () => (S.boot && S.boot.today) || localISO(new Date());

  // Copies text and resolves true when it worked. Plain http has no
  // navigator.clipboard, so the old execCommand path is kept as the fallback.
  async function copyText(text) {
    const value = String(text || "");
    if (navigator.clipboard && navigator.clipboard.writeText) {
      try { await navigator.clipboard.writeText(value); return true; } catch (_) { /* fall through */ }
    }
    const box = document.createElement("textarea");
    box.value = value; box.setAttribute("readonly", ""); box.style.position = "fixed"; box.style.top = "-1000px";
    document.body.appendChild(box); box.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (_) { ok = false; }
    box.remove();
    return ok;
  }

  const ICONS = {
    today: '<path d="M3 8h18M7 3v3M17 3v3M5 5h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2Z"/>',
    history: '<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/><path d="M12 8v4l3 2"/>',
    order: '<path d="M3 7.5 12 3l9 4.5v9L12 21l-9-4.5v-9Z"/><path d="M3 7.5 12 12l9-4.5M12 12v9"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-2.7 1.1V21a2 2 0 1 1-4 0v-.1A1.6 1.6 0 0 0 7.5 19a1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.6 1.6 0 0 0 3 13.5H3a2 2 0 1 1 0-4h.1A1.6 1.6 0 0 0 4.6 7.5a1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1Z"/>',
    chevR: '<path d="M9 5l7 7-7 7"/>',
    chevL: '<path d="M15 5l-7 7 7 7"/>',
    chevD: '<path d="M5 9l7 7 7-7"/>',
    close: '<path d="M18 6 6 18M6 6l12 12"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 16v-5M12 8h.01"/>',
    prev: '<path d="M15 18l-6-6 6-6"/>',
    next: '<path d="M9 6l6 6-6 6"/>',
    copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
    external: '<path d="M14 3h7v7"/><path d="M10 14 21 3"/><path d="M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5"/>',
    empty: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M9 4v16"/>',
  };
  const icon = (name, cls = "ico") =>
    `<svg class="ico ${cls}" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ""}</svg>`;

  // `hold` is for messages somebody has to actually read and act on, like being
  // told a person is going to call them. Those stay up long enough to be read
  // twice and carry their own dismiss.
  function toast(message, kind = "ok", hold = false) {
    toastNode.innerHTML = `${icon(kind === "error" ? "info" : "check")}<span>${e(message)}</span>`
      + (hold ? `<button class="toast-x" data-do="close-toast" aria-label="Dismiss">${icon("close")}</button>` : "");
    toastNode.className = `toast show ${kind === "error" ? "error" : ""} ${hold ? "hold" : ""}`;
    clearTimeout(toastNode._t);
    toastNode._t = setTimeout(() => { toastNode.className = "toast"; }, hold ? 16000 : 3800);
  }

  const wordmark = (cls = "") => `<span class="wordmark ${cls}">Quantify</span>`;

  function booting(message) {
    root.innerHTML = `<main class="boot">${wordmark("lg")}<div class="bar"><i></i></div><p>${e(message)}</p></main>`;
  }

  // Errors reach the screen as a sentence, never as a stack trace or a
  // browser's own wording for a broken response.
  function plainError(error) {
    const text = String((error && error.message) || error || "");
    if (!text || /^(TypeError|RangeError|SyntaxError|ReferenceError)|Failed to fetch|NetworkError|Request failed \(5/.test(text)) {
      return "Quantify could not reach the server. Check the connection and try again.";
    }
    return text;
  }

  function fatal(error) {
    root.innerHTML = `<main class="boot">${wordmark("lg")}
      <div class="card" style="max-width:440px"><div class="card-body" style="text-align:center">
        <h2 style="font-size:16px">Quantify could not load</h2>
        <p class="lede" style="margin-top:8px">${e(plainError(error))}</p>
        <div class="btn-row" style="justify-content:center;margin-top:16px"><button class="btn primary" data-do="retry">Try again</button></div>
      </div></div></main>`;
  }

  /* ---------- routing ---------- */
  const NEWLINE = String.fromCharCode(10);

  const pathNow = () => window.location.pathname.replace(/\/+$/, "") || "/";

  // If the history API is ever blocked, a redirect can bounce forever. This
  // counts rapid consecutive redirects and falls back to the landing page rather
  // than spinning.
  const nav = { count: 0, at: 0 };

  function go(path, replace = false) {
    const target = path.replace(/\/+$/, "") || "/";
    const now = Date.now();
    nav.count = now - nav.at < 400 ? nav.count + 1 : 1;
    nav.at = now;
    if (nav.count > 6) { nav.count = 0; return renderLanding(); }
    if (pathNow() === target) return boot();
    window.history[replace ? "replaceState" : "pushState"]({}, "", target);
    window.scrollTo(0, 0);
    return boot();
  }
  window.addEventListener("popstate", () => { closeLayer(); boot(); });

  /* ---------- boot ---------- */
  async function boot() {
    const path = pathNow();
    const inApp = path === "/app" || path.startsWith("/app/");
    // The boot screen only shows when there is nothing on screen yet. A retry
    // or a back button keeps whatever is already painted.
    if (inApp && !root.querySelector(".app")) booting("Opening Quantify");
    try {
      S.auth = await API.get("/api/auth/state");
      if (S.auth.user?.csrf_token) API.setCsrf(S.auth.user.csrf_token);
      const signedIn = !!S.auth.authenticated;

      // Anyone already signed in goes straight to their dashboard. The marketing
      // pages are for people who do not have an account yet.
      const home = () => (S.auth.email_verification_required ? "/verify" : "/app");
      if (signedIn && (path === "/login" || path === "/signup")) return go(home(), true);
      if (path === "/signup") { leaveApp(); return renderCreateAccount(); }
      if (path === "/login") { leaveApp(); return renderSignIn(); }
      if (path === "/reset") { leaveApp(); return renderReset(); }
      if (path === "/verify") {
        leaveApp();
        if (!signedIn) return go("/login", true);
        if (!S.auth.email_verification_required) return go("/app", true);
        return renderConfirmEmail();
      }

      if (inApp) {
        if (!signedIn) return go("/login", true);
        if (S.auth.email_verification_required) return go("/verify", true);
        if (S.auth.onboarding_required) return await startOnboarding();
        return await loadWorkspace();
      }

      if (signedIn && path === "/") return go("/app", true);
      leaveApp();
      return await renderLanding();
    } catch (error) {
      fatal(error);
    }
  }

  function leaveApp() {
    stopPulse();
  }

  async function loadWorkspace() {
    S.boot = await API.get("/api/bootstrap");
    const known = S.boot.locations.some((row) => row.id === S.locationId);
    if (!known) S.locationId = S.boot.default_location_id || "";
    store.set("quantify.location", S.locationId);
    // The day opens on the location's date, not the browser's.
    if (!S.date) S.date = todayISO();
    // Screens that used to be top level now live inside others. A browser
    // that remembers the old name lands on the new place, not on a blank page.
    if (S.view === "forecast") { S.view = "today"; S.todayPane = "ahead"; }
    if (S.view === "menu") { S.view = "settings"; S.settingsTab = "menu"; }
    if (S.view === "suppliers") S.view = "ordering";
    if (!["today", "ordering", "history", "settings"].includes(S.view)) S.view = "today";
    if (["email", "connections"].includes(S.settingsTab)) S.settingsTab = "location";
    if (S.settingsTab === "suppliers") S.settingsTab = "location";
    if (S.settingsTab === "security") S.settingsTab = "account";
    if (!["location", "menu", "costs", "account"].includes(S.settingsTab)) S.settingsTab = "location";
    store.set("quantify.stab", S.settingsTab);
    await loadView();
    startPulse();
    // First arrival gets the tutorial, once. It waits for the real screen so
    // every stop lands on this location's own numbers.
    if (tourEligible()) setTimeout(() => startTour(true), 550);
  }

  /* ---------- landing ---------- */
  // The sample screen is the product running on the sample location, not a
  // picture of it. /api/showcase computes the day, the order list and the
  // closed days with the same code the signed-in screens use, and the frame
  // below paints them with the app's own drawing functions, so the two cannot
  // drift apart.
  const demoTabs = { tab: "today", day: null };

  // A drawing function from another screen may be mid-change. If one throws,
  // the frame shows the fallback instead of taking the page down.
  const drawn = (fn, fallback = "") => { try { return fn(); } catch (_) { return fallback; } };

  async function renderLanding() {
    let show = { available: false };
    try { show = await API.get("/api/showcase"); } catch (_) { /* the page still works */ }
    S.show = show;
    const where = show.location || {};
    const years = show.history_days >= 700 ? "two years" : noun(show.history_days || 0, "day");
    const caption = where.name
      ? `Running on ${e(where.name)}, a sample ${e(String(where.concept || "restaurant").toLowerCase())}, and its ${years} of sales.`
      : "The sample location is still being set up.";
    const monthly = money(show.pricing?.monthly || 79);
    const yearly = money(show.pricing?.annual_monthly || 69);

    root.innerHTML = `<div class="site">
      <header class="site-nav">
        <div class="site-nav-inner">
          <a class="site-mark" href="/" data-link="/">${wordmark()}</a>
          <div class="site-cta">
            <a class="btn ghost" href="/login" data-link="/login">Sign in</a>
            <a class="btn accent" href="/signup" data-link="/signup">Create account</a>
          </div>
        </div>
      </header>

      <main class="site-main">
        <section class="hero">
          <h1>Know how much of each thing to make tomorrow.</h1>
          <p>Quantify reads what your register has already sold and works out how many of each item tomorrow needs. It gives you a number per item, what that number rests on, and how far off it has been before.</p>
        </section>

        <section class="sample" id="sample">
          ${screenFrame(show)}
          <p class="sample-caption">${caption}</p>
        </section>

        <section class="prose" id="how">
          <h2>How it works</h2>
          <p>Quantify reads the register and learns what each item sells on each kind of day. Weather, holidays and what is on nearby are checked against your own sales, and dropped when they never moved them. Count what is in the walk-in and it says what to buy, from which supplier, and by when. Each closed day is scored, per item, against the number given the day before, and that score sits in History.</p>
        </section>

        <section class="prose" id="pricing">
          <h2>Price</h2>
          <p>${monthly} per location, per month. ${yearly} a month paid yearly. No card to look around. Cancel from the account page.</p>
        </section>
      </main>

      <footer class="site-foot">
        <div class="site-foot-inner">
          ${wordmark()}
          <a href="mailto:support@quantify.app">support@quantify.app</a>
        </div>
      </footer>
    </div>`;
  }

  // The four real destinations down the side, and the real screen for each.
  // The main panel is inert: it is there to be read, not worked.
  function screenFrame(show) {
    const where = show.location || {};
    if (!show.brief) {
      return `<div class="card">${emptyState("The sample is still being set up", "Give it a moment and refresh the page.")}</div>`;
    }
    return `<div class="screen">
      <div class="screen-rail">
        <div class="screen-rail-head">${wordmark()}</div>
        <nav class="screen-nav-list">
          ${NAV.map(([key, label, ico]) => `
            <button class="screen-nav ${demoTabs.tab === key ? "on" : ""}" data-demo-tab="${key}">
              <i>${icon(ico)}</i><span>${e(label)}</span></button>`).join("")}
        </nav>
        <div class="screen-rail-foot"><b>${e(where.name || "")}</b><span>${e([where.city, where.region].filter(Boolean).join(", "))}</span></div>
      </div>
      <div class="screen-main" id="screen-main" inert>${screenPanel(show)}</div>
    </div>`;
  }

  function screenPanel(show) {
    const where = show.location || {};
    const b = show.brief;
    if (!b) return "";
    const top = (title, subtitle, tools = "") => `<div class="screen-top">
      <div><h4>${title}</h4>${subtitle ? `<p>${subtitle}</p>` : ""}</div>${tools}</div>`;

    if (demoTabs.tab === "ordering") {
      const o = show.order;
      const window = o && o.start ? `${e(dMed(o.start))} through ${e(dMed(o.end))}` : "";
      const group = o ? drawn(() => supplyGroup(
        { supplier: o.supplier || null, lines: o.lines || [], extras: [] },
        { counts_taken: o.counts_taken || 0 },
        o.supplier ? [o.supplier] : [], true,
      ), "") : "";
      return `${top("Order", window)}
        <div class="stack">${group || `<section class="card">${emptyState("Nothing to order yet", "Once the register has some history, this becomes the list of what to buy.")}</section>`}</div>`;
    }

    if (demoTabs.tab === "history") {
      const days = show.days || [];
      const rows = days.map((day) => drawn(() => dayRow(day), "")).join("");
      return `${top("History", "", `<div class="seg"><button class="on">Days</button><button>Track record</button></div>`)}
        <div class="stack"><section class="card">
          <div class="card-head"><div><h2>Days</h2></div></div>
          <div class="dayrows">${rows || emptyState("No closed days yet", "Closed days appear here the morning after.")}</div>
        </section></div>`;
    }

    if (demoTabs.tab === "settings") {
      const rows = [
        ["Name", where.name || ""],
        ["What you serve", where.concept || ""],
        ["City", [where.city, where.region].filter(Boolean).join(", ")],
        ["Time zone", where.timezone_label || ""],
        ["Hours", `${hourLabel(Number(where.open_hour ?? 7))} to ${hourLabel(Number(where.close_hour ?? 21))}`],
        ["Morning email", `${clock(where.email_time || "05:30")} to the owner`],
      ];
      return `${top("Settings", "", `<div class="seg"><button class="on">Location</button><button>Menu</button><button>Costs</button><button>Account</button></div>`)}
        <div class="stack"><section class="card">
          <div class="card-head"><div><h2>Location</h2></div></div>
          <table class="dt sample-list"><tbody>
            ${rows.map(([k, v]) => `<tr><td class="name">${e(k)}</td><td>${e(v)}</td></tr>`).join("")}
          </tbody></table>
        </section></div>`;
    }

    return `${top(e(dLong(b.date)), e(where.name || ""))}${sampleToday(b)}`;
  }

  // Today as the app lays it out: the headline with its three figures, the
  // running low card, then the panes card with the real make list.
  function sampleToday(b) {
    const s = b.summary;
    const cmp = b.comparison;
    const dir = Math.abs(s.revenue_change_percent) < 5 ? "plain" : s.revenue_change_percent >= 0 ? "up" : "down";
    const make = drawn(() => makeTotal(b), (b.items || []).reduce((n, row) => n + (row.make ?? row.expected), 0));
    const list = drawn(() => itemTable(b), "");
    return `<div class="stack">
      <section class="headline solo">
        <div class="headline-main">
          <div class="headline-meta"><span class="tag ${dir}">${e(s.demand_level)}</span></div>
          <h2>${e(b.headline)}</h2>
          <div class="sample-figures">
            <div><b>${money(s.expected_revenue)} expected</b><small>${money(cmp.sales)} on ${e(cmp.label)}</small></div>
            <div><b>${num(make)} to make</b><small>${num(s.expected_units)} expected to sell</small></div>
            <div><b>busiest ${e(s.peak_hour || "not set")}</b><small>${s.peak_share_percent}% of the day</small></div>
          </div>
        </div>
      </section>
      <section class="card">
        <div class="card-head"><div><h2>Running low</h2></div>
          <div class="spacer"></div><button class="btn sm">Open the order</button></div>
        <div class="card-body"><p class="lede">Nothing has been counted yet. Count what is in the walk-in on the Order page and this fills in.</p></div>
      </section>
      <section class="card">
        <div class="card-head panehead">
          <div class="seg panes">${PANES.map(([k, l]) => `<button class="${k === "make" ? "on" : ""}">${l}</button>`).join("")}</div>
        </div>
        <div class="tablewrap">${list}</div>
      </section>
    </div>`;
  }

  /* ---------- auth panel ---------- */
  // One column, the form and nothing beside it.
  function authFrame(inner) {
    const slot = document.getElementById("auth-slot");
    if (slot) slot.innerHTML = inner;
    else {
      root.innerHTML = `<main class="auth">
        <section class="auth-left">
          <a class="auth-mark" href="/" data-link="/">${wordmark()}</a>
          <div class="auth-form" id="auth-slot">${inner}</div>
        </section>
      </main>`;
    }
    const first = root.querySelector("#auth-slot [autofocus], #auth-slot input:not([type=hidden])");
    if (first) { try { first.focus({ preventScroll: true }); } catch (_) { /* nothing to focus */ } }
  }

  function renderCreateAccount() {
    authFrame(`
      <h1>Create your account</h1>
      <p>Takes a minute. The sample location is ready the moment you are in, before anything is connected.</p>
      <form id="f-create">
        <label class="field"><span>Your name</span><input name="display_name" autocomplete="name" required minlength="2" placeholder="Jordan Lee"></label>
        <label class="field"><span>Work email</span><input name="email" type="email" autocomplete="email" required placeholder="you@yourrestaurant.com">
          <small>A six-digit code goes here to confirm it.</small></label>
        <label class="field"><span>Password</span><input name="password" type="password" autocomplete="new-password" required minlength="12" placeholder="At least 12 characters">
          <small>Twelve characters or more, with at least three of: capital letters, small letters, numbers, symbols.</small></label>
        <button class="btn accent lg block" type="submit">Create account</button>
      </form>
      <p class="auth-alt">Already have an account? <a href="/login" data-link="/login">Sign in</a></p>`);
  }

  function renderSignIn() {
    authFrame(`
      <h1>Sign in</h1>
      <form id="f-signin">
        <label class="field"><span>Email</span><input name="email" type="email" autocomplete="username" required></label>
        <label class="field"><span>Password</span><input name="password" type="password" autocomplete="current-password" required></label>
        <button class="btn accent lg block" type="submit">Sign in</button>
      </form>
      <p class="auth-alt"><a href="/reset" data-link="/reset">Forgot your password?</a></p>
      <p class="auth-alt">No account yet? <a href="/signup" data-link="/signup">Create one</a></p>`);
  }

  function renderSignInCode() {
    authFrame(`
      <h1>Enter your code</h1>
      <p>Open your authenticator app and type the six digits showing now. A backup code works here too.</p>
      <form id="f-code">
        <label class="field"><span>Six-digit code</span><input class="code-input" name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="9" required autofocus></label>
        <button class="btn accent lg block" type="submit">Sign in</button>
        <button class="btn ghost block" type="button" data-do="back-signin">Back</button>
      </form>`);
  }

  function renderConfirmEmail(info) {
    const v = info || S.verification || {};
    authFrame(`
      <h1>Confirm your email</h1>
      <p>A six-digit code went to <b>${e(v.sent_to || S.auth?.user?.email || "your inbox")}</b>. Type it below.</p>
      ${v.preview_code ? `<p class="form-note" style="margin-top:12px">${e(v.preview_note || "")} It is already filled in below.</p>` : ""}
      <form id="f-confirm-email">
        <label class="field"><span>Six-digit code</span>
          <input class="code-input" name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" required autofocus
                 value="${e(v.preview_code || "")}"></label>
        <button class="btn accent lg block" type="submit">Confirm and continue</button>
      </form>
      <p class="auth-alt">Nothing came through? <button type="button" data-do="resend-code">Send it again</button></p>`);
  }

  // Forgotten password: the email first, then the code with the new password.
  // The server answers the same way whether or not the address has an account.
  function renderReset() {
    S.reset = S.reset || { stage: "start", email: "", preview_code: "", preview_note: "" };
    const r = S.reset;
    if (r.stage === "code") {
      authFrame(`
        <h1>Set a new password</h1>
        <p>A six-digit code went to <b>${e(r.email)}</b>. Type it with the new password.</p>
        ${r.preview_code ? `<p class="form-note" style="margin-top:12px">${e(r.preview_note || "")} It is already filled in below.</p>` : ""}
        <form id="f-reset-complete">
          <label class="field"><span>Six-digit code</span>
            <input class="code-input" name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" required autofocus value="${e(r.preview_code || "")}"></label>
          <label class="field"><span>New password</span><input name="new_password" type="password" autocomplete="new-password" required minlength="12" placeholder="At least 12 characters">
            <small>Twelve characters or more, with at least three of: capital letters, small letters, numbers, symbols.</small></label>
          <button class="btn accent lg block" type="submit">Set the new password</button>
        </form>
        <p class="auth-alt">Nothing came through? <button type="button" data-reset="again">Send it again</button></p>
        <p class="auth-alt"><button type="button" data-reset="start">Use a different email</button></p>`);
      return;
    }
    authFrame(`
      <h1>Reset your password</h1>
      <p>Type the email you sign in with and a six-digit code goes there.</p>
      <form id="f-reset-start">
        <label class="field"><span>Email</span><input name="email" type="email" autocomplete="username" required value="${e(r.email || "")}" autofocus></label>
        <button class="btn accent lg block" type="submit">Send a code</button>
      </form>
      <p class="auth-alt"><a href="/login" data-link="/login">Back to sign in</a></p>`);
  }

  async function startReset(email) {
    const result = await API.send("/api/auth/password/reset/start", "POST", { email });
    S.reset = { stage: "code", email, preview_code: result.preview_code || "", preview_note: result.preview_note || "" };
    renderReset();
    toast(result.preview_code ? "Code below" : "Code sent");
  }

  document.addEventListener("click", async (event) => {
    const target = event.target.closest("[data-reset]");
    if (!target || !S.reset) return;
    if (target.dataset.reset === "start") { S.reset.stage = "start"; return renderReset(); }
    target.disabled = true;
    try { await startReset(S.reset.email); }
    catch (error) { toast(plainError(error), "error"); }
    finally { target.disabled = false; }
  });
  /* ---------- onboarding ---------- */
  const ONB_STEPS = ["Business", "Where", "How you work", "Ready"];

  // Typed answers survive a reload. They live in sessionStorage, which is per
  // tab and gone when the tab closes, so nothing about the business lingers on
  // a shared counter machine.
  const ONB_KEY = "quantify.onb";
  const session = {
    get: (key) => { try { return sessionStorage.getItem(key); } catch (_) { return null; } },
    set: (key, value) => { try { sessionStorage.setItem(key, value); } catch (_) { /* private mode */ } },
    remove: (key) => { try { sessionStorage.removeItem(key); } catch (_) { /* private mode */ } },
  };

  function saveOnboarding() {
    if (!S.onboarding || !S.onboarding.values) return;
    session.set(ONB_KEY, JSON.stringify({ step: S.onboarding.step, values: S.onboarding.values, tz: S.onboarding.tz }));
  }

  function savedOnboarding() {
    try { return JSON.parse(session.get(ONB_KEY) || "null") || null; } catch (_) { return null; }
  }

  async function startOnboarding() {
    const info = await API.get("/api/onboarding");
    const kept = savedOnboarding();
    S.onboarding = {
      step: 0,
      values: {
        company: info.organization?.name && info.organization.name !== "Quantify Demo Group" && info.organization.name !== "Your company"
          ? info.organization.name : "",
        concept: "",
        location_count: "1",
        goals: [],
        pos: "Not sure yet",
        place: "",
        open_hour: 7,
        close_hour: 21,
      },
      tz: null,
      suggestions: [],
      sample: info.sample_locations || [],
      owner: info.owner,
    };
    if (kept && kept.values) {
      Object.assign(S.onboarding.values, kept.values);
      S.onboarding.step = Math.min(ONB_STEPS.length - 1, Math.max(0, Number(kept.step) || 0));
      S.onboarding.tz = kept.tz || null;
    }
    renderOnboarding();
  }

  const CONCEPTS = [
    "Bakery or cafe", "Coffee shop", "Pizza", "Burgers and grill", "Fast casual",
    "Full service restaurant", "Deli or sandwiches", "Bar and kitchen", "Juice or smoothies",
    "Ice cream or dessert", "Food truck", "Ghost kitchen", "Grocery or corner shop",
    "Something else",
  ];

  const GOALS = ["Cut waste", "Stop selling out", "Staff the right hours", "Plan orders", "Understand the swings"];
  const REGISTERS = ["Not sure yet", "Square", "Toast", "Clover", "Lightspeed", "SpotOn", "Revel", "Shopify POS", "Something else"];

  function hourLabel(hour) {
    const h = ((hour % 24) + 24) % 24;
    const suffix = h < 12 ? "AM" : "PM";
    const shown = h % 12 === 0 ? 12 : h % 12;
    return `${shown} ${suffix}${hour >= 24 ? " next day" : ""}`;
  }

  function hourOptions(selected, from = 0, to = 24) {
    let out = "";
    for (let hour = from; hour <= to; hour += 1) {
      out += `<option value="${hour}" ${Number(selected) === hour ? "selected" : ""}>${hourLabel(hour)}</option>`;
    }
    return out;
  }

  function renderOnboarding() {
    const { step, values, tz, suggestions } = S.onboarding;
    const progress = ((step + 1) / ONB_STEPS.length) * 100;
    saveOnboarding();
    let body = "";

    if (step === 0) {
      body = `<h1>What is the business called?</h1>
        <p>This is the name on your morning email.</p>
        <form id="f-onb">
          <label class="field"><span>Business name</span>
            <input name="company" required minlength="2" value="${e(values.company || "")}" placeholder="Juniper Bakehouse" autofocus autocomplete="organization"></label>
          <label class="field"><span>What do you serve?</span>
            <select name="concept">
              ${CONCEPTS.map((v) => `<option ${values.concept === v ? "selected" : ""}>${v}</option>`).join("")}
            </select></label>
          <div class="field"><span>How many locations?</span>
            <div class="chipset">
              ${["1", "2 to 5", "6 to 20", "More than 20"].map((v) => `
                <button type="button" class="chip ${values.location_count === v ? "on" : ""}" data-pick="location_count" data-value="${v}">${v}</button>`).join("")}
            </div></div>
          <div class="onb-actions"><span class="spacer"></span><button class="btn accent lg" type="submit">Continue</button></div>
        </form>`;
    } else if (step === 1) {
      body = `<h1>Where is it?</h1>
        <p>This sets the weather, the local calendar, and the time your morning email goes out. A city, a state, or a ZIP code is enough.</p>
        <form id="f-onb" autocomplete="off">
          <label class="field"><span>City, state, or ZIP</span>
            <div class="typeahead">
              <input type="text" name="place" id="place-input" data-typeahead required
                     value="${e(values.place || "")}" placeholder="Start typing a town"
                     autocomplete="off" autofocus>
              ${suggestions && suggestions.length ? `<div class="ta-list">
                ${suggestions.map((s) => `
                  <button type="button" class="ta-item" data-place="${e(s.place)}" data-tz="${e(s.timezone)}">
                    <b>${e(s.place)}</b><small>${e(s.label)}</small></button>`).join("")}
              </div>` : ""}
            </div>
            <div id="tz-hint" data-tz-hint>${tzHint(tz)}</div></label>
          <div class="onb-actions">
            <button class="btn" type="button" data-do="onb-back">Back</button>
            <span class="spacer"></span><button class="btn accent lg" type="submit">Continue</button></div>
        </form>`;
    } else if (step === 2) {
      const goals = values.goals || [];
      body = `<h1>How do you work?</h1>
        <p>Pick what matters most, then set your opening hours.</p>
        <form id="f-onb">
          <div class="field"><span>What do you want out of it?</span>
            <div class="chipset">
              ${GOALS.map((label) => `
                <button type="button" class="chip ${goals.includes(label) ? "on" : ""}" data-toggle-goal="${e(label)}">${label}</button>`).join("")}
            </div>
          </div>
          <div class="field pair">
            <label><span>What time do you open?</span>
              <select name="open_hour">${hourOptions(values.open_hour ?? 7, 0, 14)}</select></label>
            <label><span>What time do you close?</span>
              <select name="close_hour">${hourOptions(values.close_hour ?? 21, 14, 28)}</select></label>
          </div>
          <p class="field-note">Only open hours are planned, so an hour either side matters. This can change later in Settings.</p>
          <label class="field"><span>What register do you run?</span>
            <select name="pos">
              ${REGISTERS.map((v) => `<option ${values.pos === v ? "selected" : ""}>${v}</option>`).join("")}
            </select>
            <small>Square connects today. Until yours is plugged in, Quantify runs on sample data.</small></label>
          <div class="onb-actions">
            <button class="btn" type="button" data-do="onb-back">Back</button>
            <span class="spacer"></span><button class="btn accent lg" type="submit">Continue</button></div>
        </form>`;
    } else {
      const where = tz?.matched && tz.confident ? tz.matched : (values.place || "not set");
      const rows = [
        ["Business", values.company || "Your company"],
        ["Serves", values.concept || "Not set"],
        ["Where", where],
        ["Open", `${hourLabel(Number(values.open_hour ?? 7))} to ${hourLabel(Number(values.close_hour ?? 21))}`],
      ];
      if (values.pos && values.pos !== "Not sure yet") rows.push(["Register", values.pos]);
      if ((values.goals || []).length) rows.push(["Focus", values.goals.join(", ")]);
      body = `<h1>Ready</h1>
        <p>Check these, then open it.</p>
        <div class="summary">
          ${rows.map(([k, v]) => `<div><span>${k}</span><b>${e(v)}</b></div>`).join("")}
        </div>
        <div class="onb-next">
          ${[
            ["Today", "What to make, why, and the next two weeks."],
            ["Order", "What to buy, and from whom."],
            ["History", "Closed days, and how close each call was."],
            ["Settings", "Your menu, recipes, and costs."],
          ].map(([t, d]) => `<div class="onb-next-item"><b>${t}</b><span>${d}</span></div>`).join("")}
        </div>
        <div class="onb-actions">
          <button class="btn" type="button" data-do="onb-back">Back</button>
          <span class="spacer"></span>
          <button class="btn accent lg" type="button" data-do="onb-finish">Open Quantify</button>
        </div>`;
    }

    root.innerHTML = `<div class="onb">
      <div class="onb-top">${wordmark()}
        <div class="onb-progress"><span>Step ${step + 1} of ${ONB_STEPS.length}</span>
          <div class="track"><i style="width:${progress}%"></i></div></div>
      </div>
      <div class="onb-body"><div class="onb-card">${body}</div></div>
    </div>`;

    const input = document.getElementById("place-input");
    if (input) {
      const cursor = input.value.length;
      input.setSelectionRange?.(cursor, cursor);
    }
  }

  // Anything typed or picked on a step is kept as it changes, so a reload
  // halfway through a step loses nothing.
  document.addEventListener("input", (event) => {
    const field = event.target.closest("#f-onb [name]");
    if (!field || !S.onboarding || !S.onboarding.values) return;
    S.onboarding.values[field.name] = field.value;
    saveOnboarding();
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest("[data-pick], [data-toggle-goal]")) return;
    setTimeout(saveOnboarding, 0);
  });

  // Painted in place rather than through a re-render, so the caret never jumps
  // while somebody is typing. `holder` is whichever .typeahead the input lives
  // in, so the same code serves onboarding and every settings field.
  function paintSuggestions(holder, rows) {
    if (!holder) return;
    const existing = holder.querySelector(".ta-list");
    if (existing) existing.remove();
    if (!rows || !rows.length) return;
    holder.insertAdjacentHTML("beforeend", `<div class="ta-list">
      ${rows.map((s) => `
        <button type="button" class="ta-item" data-place="${e(s.place)}" data-tz="${e(s.timezone)}">
          <b>${e(s.place)}</b><small>${e(s.label)}</small></button>`).join("")}
    </div>`);
  }

  // A field with data-typeahead offers places as you type. Delegated from the
  // document, so it keeps working through every re-render and on every screen
  // that wants it.
  let placeTimer = 0;
  function askPlaces(input) {
    clearTimeout(placeTimer);
    placeTimer = setTimeout(async () => {
      const holder = input.closest(".typeahead");
      const value = input.value.trim();
      const hint = holder && holder.parentElement
        ? holder.parentElement.querySelector("[data-tz-hint]") : null;
      if (value.length < 2) {
        paintSuggestions(holder, []);
        if (hint) hint.innerHTML = "";
        return;
      }
      try {
        const result = await API.get(`/api/timezone?q=${encodeURIComponent(value)}`);
        // Somebody may have typed on since this request went out.
        if (input.value.trim() !== value) return;
        const rows = result.suggestions || [];
        paintSuggestions(holder, rows);
        // A list to pick from is the answer; the hint only speaks when there is none.
        if (hint) hint.innerHTML = rows.length && !result.match?.confident ? `<span class="tz-hint">Pick one below.</span>` : tzHint(result.match);
        if (input.id === "place-input") {
          S.onboarding.tz = result.match;
          S.onboarding.suggestions = rows;
          saveOnboarding();
        }
      } catch (_) { /* still typing */ }
    }, 200);
  }

  document.addEventListener("input", (event) => {
    const input = event.target.closest("[data-typeahead]");
    if (!input) return;
    if (input.id === "place-input") S.onboarding.values.place = input.value.trim();
    askPlaces(input);
  });

  // Clicking away closes any open list without swallowing the click.
  document.addEventListener("pointerdown", (event) => {
    if (event.target.closest(".typeahead")) return;
    document.querySelectorAll(".ta-list").forEach((node) => node.remove());
  }, true);

  function tzHint(tz) {
    if (!tz) return "";
    if (!tz.confident) {
      return `<span class="tz-hint unknown">${icon("info")} That place is not recognised yet. Keep typing, or use a city or ZIP code.</span>`;
    }
    if (tz.matched === "time zone name" || tz.matched === "time zone") {
      return `<span class="tz-hint">${icon("check")} Running on ${e(tz.label)}.</span>`;
    }
    return `<span class="tz-hint">${icon("check")} Read as ${e(tz.matched)}, so ${e(tz.label)}.</span>`;
  }
  /* ---------- shell ---------- */
  // Three places to work and one place to set things up. What to make today,
  // what to buy, and what happened. The next two weeks are a panel inside
  // Today, and the menu is a settings page, because both are read far less
  // often than they are worth a button on every screen.
  const NAV = [
    ["today", "Today", "today"],
    ["ordering", "Order", "order"],
    ["history", "History", "history"],
    ["settings", "Settings", "settings"],
  ];

  const currentLocation = () => S.boot.locations.find((row) => row.id === S.locationId) || S.boot.locations[0] || {};

  function shell(title, subtitle, tools, body) {
    const location = currentLocation();
    const many = S.boot.locations.length > 1;
    // The Today button says which day is open when it is not today.
    const navLabel = (key, label) => (key === "today" && S.date !== todayISO() ? dShort(S.date) : label);
    const where = `<span><b>${e(location.name || "Choose a location")}</b><span>${e([location.city, location.region].filter(Boolean).join(", "))}</span></span>`;
    return `<div class="app">
      <aside class="rail">
        <div class="rail-head">${wordmark()}</div>
        <nav class="rail-nav">
          ${NAV.map(([key, label, ico]) => `
            <button class="nav-item ${S.view === key ? "active" : ""}" data-view="${key}">
              <i>${icon(ico)}</i><span>${e(navLabel(key, label))}</span></button>`).join("")}
        </nav>
        <div class="rail-foot">
          ${many
            ? `<button class="locpick" data-do="switch-location" aria-label="Switch location">${where}${icon("chevD")}</button>`
            : `<div class="locpick static">${where}</div>`}
          <div class="rail-status">
            <span class="pulse-dot ${S.pulse.live ? "" : "stale"}"></span>
            <span id="pulse-text">${pulseLabel()}</span>
          </div>
        </div>
      </aside>
      <div class="main">
        <header class="topbar">
          <div class="progress" aria-hidden="true"><i></i></div>
          <div class="topbar-title"><h1>${e(title)}</h1>${subtitle ? `<p>${subtitle}</p>` : ""}</div>
          <div class="topbar-tools">${tools || ""}</div>
          ${many ? `<button class="btn sm ghost locpick-top" data-do="switch-location" aria-label="Switch location"><span>${e(location.name || "")}</span>${icon("chevD")}</button>` : ""}
        </header>
        <main class="content">${body}</main>
      </div>
    </div>`;
  }

  // Two arrows and the date as a button. The native picker sits invisibly over
  // the button, so a tap opens it on every browser without a second control.
  function dateTools(includeToday = true) {
    return `<div class="datectl">
      <button class="icon-btn" data-day="-1" aria-label="Previous day">${icon("prev")}</button>
      <label class="datebtn"><span>${e(dShort(S.date))}</span>
        <input type="date" id="date-picker" value="${e(S.date)}" aria-label="Pick a date"></label>
      <button class="icon-btn" data-day="1" aria-label="Next day">${icon("next")}</button>
    </div>${includeToday && S.date !== todayISO() ? `<button class="btn sm" data-do="today">Back to today</button>` : ""}`;
  }

  // The thin bar at the top of the page while something is being fetched. The
  // screen underneath stays put.
  function progress(on) {
    const bar = root.querySelector(".progress");
    if (bar) bar.classList.toggle("on", !!on);
  }

  const isTyping = () => {
    const active = document.activeElement;
    return !!active && ["INPUT", "TEXTAREA", "SELECT"].includes(active.tagName) && active.type !== "date";
  };

  /* ---------- data loading ---------- */
  // What a screen last held, so it can be put back at once next time. Settings
  // is left out: its forms are saved from, so it always reads fresh.
  function viewKey() {
    const base = `${S.view}:${S.locationId}:${S.date}`;
    if (S.view === "ordering") return `${base}:${S.order.days}`;
    if (S.view === "history") return `${base}:${S.historyTab}:${S.history.range}:${S.orders.range}`;
    return base;
  }
  function snapshot() {
    if (S.view === "today") return { data: S.data, attention: S.attention, outlook: S.outlook };
    if (S.view === "history") return { data: S.data, history: S.history, orders: S.orders };
    return { data: S.data };
  }
  function remember() {
    if (S.view === "settings") return;
    const keys = Object.keys(S.cache);
    if (keys.length > 40) keys.slice(0, 20).forEach((key) => { delete S.cache[key]; });
    S.cache[viewKey()] = snapshot();
  }

  // Keeps the current screen on while the next one is fetched. A screen seen
  // before paints from memory at once and refreshes underneath; only a first
  // open with nothing on screen shows a placeholder.
  async function loadView(silent = false) {
    clearTimeout(menuPollTimer);
    const first = !root.querySelector(".app");
    const cached = S.cache[viewKey()];
    if (first) root.innerHTML = shell(viewTitle(), "", "", skeleton());
    else if (!silent && cached) { Object.assign(S, cached); render(); silent = true; }
    const token = (loadView._seq = (loadView._seq || 0) + 1);
    const q = `location_id=${encodeURIComponent(S.locationId)}`;
    progress(true);
    try {
      if (S.view === "today") {
        // The stock strip is optional. Nothing on Today waits for it, and a
        // build without the supply routes just shows no strip.
        const [brief, attention] = await Promise.all([
          API.get(`/api/brief?${q}&date=${S.date}`),
          API.get(`/api/supply/attention?${q}`).catch(() => null),
        ]);
        if (token !== loadView._seq) return;
        S.data = brief;
        S.attention = attention;
      }
      if (S.view === "ordering") {
        const plan = await API.get(`/api/ordering?${q}&start=${S.date}&days=${S.order.days}`);
        if (token !== loadView._seq) return;
        S.data = plan;
      }
      if (S.view === "history") {
        await loadHistory(silent);
        if (token !== loadView._seq) return;
      }
      if (S.view === "settings") {
        // Setup and billing are read once per visit and again after a save
        // (the save handlers drop S.data). Tab taps never refetch them.
        const have = S.data && S.data.setup && S.data.billing && S.data.location_id === S.locationId;
        if (!have) {
          const [setup, billing] = await Promise.all([API.get(`/api/setup?${q}`), API.get("/api/billing")]);
          if (token !== loadView._seq) return;
          S.data = { setup, billing, location_id: S.locationId };
        }
        if (S.settingsTab === "menu" && !S.menu) {
          const menu = await API.get(`/api/menu?${q}`);
          if (token !== loadView._seq) return;
          S.menu = menu;
        }
        if (S.settingsTab === "costs" && !S.costs) {
          const costs = await API.get(`/api/costs?${q}`);
          if (token !== loadView._seq) return;
          S.costs = costs;
        }
        if (token !== loadView._seq) return;
      }
      remember();
      S.pulse.pending = false;
      // A background refresh never repaints under someone's fingers.
      if (!(silent && (isTyping() || layer.innerHTML))) render(silent);
      if (S.view === "today") maybeFetchNarrative();
      if (S.view === "today" && S.todayPane === "ahead") loadOutlook();
      if (S.view === "settings" && S.settingsTab === "menu") scheduleMenuPoll();
    } catch (error) {
      if (token !== loadView._seq) return;
      if (error.status === 403) return boot();
      if (first) fatal(error); else toast(plainError(error), "error");
    } finally {
      if (token === loadView._seq) progress(false);
    }
  }

  async function loadHistory(silent) {
    const q = `location_id=${encodeURIComponent(S.locationId)}`;
    if (S.historyTab === "accuracy") {
      S.data = await API.get(`/api/accuracy?${q}&as_of=${todayISO()}&days=45`);
      return;
    }
    if (S.historyTab === "days") {
      if (!silent) S.history = { days: [], nextBefore: null, hasMore: true, loading: false, range: S.history.range, costs: S.history.costs };
      const start = rangeStart(S.history.range);
      const page = await API.get(`/api/history/days?${q}&limit=18${start ? `&start=${start}` : ""}`);
      if (silent) {
        // A background refresh must not throw away pages the reader scrolled to.
        const fresh = new Set(page.days.map((row) => row.date));
        S.history.days = page.days.concat(S.history.days.filter((row) => !fresh.has(row.date)));
      } else {
        S.history.days = page.days;
        S.history.nextBefore = page.next_before;
        S.history.hasMore = page.has_more;
      }
      S.history.costs = page.costs || S.history.costs || null;
      S.data = { source: page.source };
      return;
    }
    if (!silent) S.orders = { rows: [], nextDate: null, nextSkip: 0, hasMore: true, loading: false, range: S.orders.range };
    const start = rangeStart(S.orders.range);
    const page = await API.get(`/api/history/orders?${q}&limit=40${start ? `&start=${start}` : ""}`);
    if (silent) {
      const fresh = new Set(page.orders.map((row) => row.id));
      S.orders.rows = page.orders.concat(S.orders.rows.filter((row) => !fresh.has(row.id)));
    } else {
      S.orders.rows = page.orders;
      S.orders.nextDate = page.next_before_date;
      S.orders.nextSkip = page.next_skip;
      S.orders.hasMore = page.has_more;
    }
    S.data = { source: page.source };
  }

  function rangeStart(range) {
    if (range === "month") return addDays(todayISO(), -30);
    if (range === "quarter") return addDays(todayISO(), -90);
    if (range === "year") return addDays(todayISO(), -365);
    return null;
  }

  async function maybeFetchNarrative() {
    const key = `${S.locationId}:${S.date}`;
    if (S.data?.narrative || S.narrativeTried === key) return;
    S.narrativeTried = key;
    try {
      const result = await API.get(`/api/brief/narrative?location_id=${encodeURIComponent(S.locationId)}&date=${S.date}`);
      // A late answer for another day or another location is dropped.
      const same = S.view === "today" && `${S.locationId}:${S.date}` === key && S.data;
      if (result && !result.pending && same) {
        S.data.narrative = result;
        remember();
        if (!isTyping() && !layer.innerHTML) render(true);
      }
    } catch (_) { /* the built-in writer already filled the page */ }
  }

  function viewTitle() {
    if (S.view === "today") return dLong(S.date);
    if (S.view === "ordering") return "Order";
    if (S.view === "history") return "History";
    return "Settings";
  }

  // Only ever shown when there is nothing on screen yet.
  function skeleton() {
    return `<div class="stack">
      <div class="skel" style="height:150px;border-radius:13px"></div>
      <div class="tiles three">${[1, 2, 3].map(() => `<div class="skel" style="height:96px;border-radius:13px"></div>`).join("")}</div>
      <div class="skel" style="height:280px;border-radius:13px"></div>
    </div>`;
  }

  function render(preserve = false) {
    const y = window.scrollY;
    try {
      if (S.view === "today") renderToday();
      if (S.view === "history") renderHistory();
      if (S.view === "ordering") renderOrdering();
      if (S.view === "settings") renderSettings();
    } catch (error) {
      // One broken card must not take the whole screen down. What was on
      // screen stays, and the person is told in a sentence.
      if (!root.querySelector(".app")) throw error;
      toast("Part of this screen could not be drawn. Try again in a moment.", "error");
      return;
    }
    if (preserve) window.scrollTo(0, y);
  }

  /* ---------- today ---------- */
  // One screen. The call, three figures, what to do, what is running low, then
  // one panel that switches between the make list, the reasons, the hours and
  // the two weeks ahead. Nothing stacks under that, so the page ends where the
  // list ends and a person at the counter never scrolls past what they need.
  const PANES = [["make", "What to make"], ["why", "Why"], ["hours", "Through the day"], ["ahead", "Next two weeks"]];

  function renderToday() {
    const b = S.data;
    const s = b.summary;
    const cmp = b.comparison;
    const n = b.narrative;
    const dir = s.revenue_change_percent >= 0 ? "up" : "down";
    const live = b.intraday && b.intraday.in_service ? b.intraday : null;

    const headline = n?.headline || b.headline;
    const summaryText = n?.summary || defaultSummary(b);
    const actions = (n?.actions?.length ? n.actions : b.actions).slice(0, 3);

    const body = `<div class="stack">
      <section class="headline solo">
        <div class="headline-main">
          <div class="headline-meta">
            <span class="tag ${dir} dot">${e(s.demand_level)}</span>
            ${live && live.revision ? runningTag(live.revision) : `<span class="tag plain">${s.confidence}% sure</span>`}
            ${b.data_health.pos_freshness === "current" ? "" : `<span class="tag warn dot">Register data is ${e(b.data_health.pos_freshness)}</span>`}
          </div>
          <h2>${e(headline)}</h2>
          <p class="sum">${e(summaryText)}</p>
        </div>
      </section>

      <section class="tiles three" id="daytiles">
        ${tile("Expected sales", money(s.expected_revenue),
          `<b>${money(cmp.sales)}</b> on ${e(cmp.label)}. ${diffPhrase(s.difference_sales, "money")}`,
          b.costs ? `About ${money(b.costs.left_after_costs)} kept after food and wages` : `Averaged over ${noun(cmp.based_on_days, weekday(b.date))} here`)}
        ${tile("Units to make", num(makeTotal(b)),
          `<b>${num(s.expected_units)}</b> will sell. The rest is the cushion for running out.`,
          `The Make column, added up`)}
        ${tile("Busiest hour", s.peak_hour || "Not set",
          `<b>${money(s.peak_revenue)}</b> and ${noun(s.peak_units, "item")} in that hour alone.`,
          `${s.peak_share_percent}% of the day lands in one hour`)}
      </section>

      ${actions.length ? `<section class="card" id="whattodo">
        <div class="card-head"><div><h2>What to do</h2></div></div>
        <div class="actions">${actions.map((row, i) => `
          <div class="action ${row.type || ""}">
            <span class="mark">${row.type === "watch" ? "!" : (i + 1)}</span>
            <div><b>${e(row.title)}</b><p>${e(row.detail)}</p></div>
            <span class="metric">${e(row.metric)}</span>
          </div>`).join("")}</div>
      </section>` : ""}

      ${runningLow()}

      <section class="card" id="daypanes">
        <div class="card-head panehead">
          <div class="seg panes">${PANES.map(([k, l]) =>
            `<button class="${S.todayPane === k ? "on" : ""}" data-pane="${k}">${l}</button>`).join("")}</div>
        </div>
        ${todayPane(b)}
      </section>
    </div>`;

    root.innerHTML = shell(dLong(S.date), `${e((S.boot.locations.find((l) => l.id === S.locationId) || {}).name || "")}`,
      `${dateTools()}<button class="btn sm" data-do="preview-email">Preview email</button>`, body);
  }

  // During service the confidence tag gives way to where the day is actually
  // running against the morning call, which is the number the record is
  // scored on. The full split is on the Through the day panel.
  function runningTag(r) {
    const pace = r.sold_units - r.called_by_now_units;
    return `<span class="tag ${pace >= 0 ? "up" : "down"} dot">${pace >= 0 ? "+" : ""}${num(pace)} items against the morning call, read at ${e(r.label)}</span>`;
  }

  function sureNote(b) {
    const cmp = b.comparison;
    return b.narrative?.confidence_note
      || `Built from ${noun(cmp.based_on_days, "comparable " + weekday(b.date))} inside ${noun(b.trust.history_days, "day")} of this location's own sales.`;
  }

  // Counted stock against what the next days will use. Empty until somebody
  // has counted something on the Order screen, and silent when nothing is
  // short, so it only appears when there is something to act on.
  function runningLow() {
    const rows = ((S.attention && S.attention.lines) || []).slice(0, 5);
    if (!rows.length) return "";
    return `<section class="card" id="runninglow">
      <div class="card-head"><div><h2>Running low</h2></div>
        <div class="spacer"></div><button class="btn sm" data-view="ordering">Open the order</button></div>
      <div class="lowlist">${rows.map((r) => {
        const days = Number(r.days_of_cover);
        const left = days < 1 ? "runs out today" : days < 2 ? "about a day left" : `about ${Math.round(days)} days left`;
        return `<div class="lowrow">
          <b>${e(r.name)}</b>
          <span>${left}, ${num(r.on_hand)} ${e(r.unit || "")} on hand</span>
          <span class="when">${r.order_by ? `Order by ${e(dMed(r.order_by))}` : ""}${r.supplier ? `${r.order_by ? " from " : ""}${e(r.supplier)}` : ""}</span>
        </div>`;
      }).join("")}</div>
    </section>`;
  }

  function todayPane(b) {
    if (S.todayPane === "why") {
      return `<div class="reasons">
        <div class="reason"><div class="reason-top"><b>How sure</b>
          <span class="tag plain">${b.summary.confidence}% ${e(confidenceWord(b.summary.confidence))}</span></div>
          <p>${e(sureNote(b))}</p></div>
        ${reasonRows(b, b.narrative)}</div>${weatherFoot(b)}`;
    }
    if (S.todayPane === "hours") return `<div class="card-body">${hourChart(b)}</div>${liveTable(b)}`;
    if (S.todayPane === "ahead") return aheadPane();
    return `<div class="tablewrap">${itemTable(b)}</div>${prepGroups(b)}
      <div class="card-foot">Lean toward the top of the range on anything cheap to make and quick to sell. Open any item for its whole record.</div>`;
  }

  // What the register has rung so far against the morning call, item by item.
  function liveTable(b) {
    const live = b.intraday;
    if (!live || !live.in_service) return "";
    const r = live.revision;
    if (!r) return `<div class="card-foot">Not enough of the day has finished to say where it is running yet.</div>`;
    const ahead = r.difference_units >= 0;
    return `<div class="card-body" style="border-top:1px solid var(--line)">
      <p class="small muted" style="margin-bottom:12px">Read at ${e(r.label)}, with ${r.expected_share_percent}% of a normal ${e(weekday(b.date))} behind us. The register has rung ${num(r.sold_units)} items and ${money(r.sold_sales)}.</p>
      <div class="live-split">
        <div><span>Called this morning</span><b>${num(r.opening_units)} items</b><small>${money(r.opening_sales)}</small></div>
        <div><span>Where it looks like finishing</span><b>${num(r.revised_units)} items</b><small>${money(r.revised_sales)}</small></div>
        <div><span>Change</span><b class="${ahead ? "up" : "down"}">${ahead ? "+" : ""}${num(r.difference_units)} items</b>
          <small>${ahead ? "+" : ""}${money(r.difference_sales)}</small></div>
      </div>
      ${r.items.length ? `<table class="dt" style="margin-top:14px"><thead><tr>
        <th>Item</th><th class="num right">Called</th><th class="num right">Sold so far</th>
        <th class="num right">Now expecting</th><th class="num right">Change</th></tr></thead><tbody>
        ${r.items.map((row) => `<tr class="clickable" data-item-sheet="${e(row.item_id)}">
          <td class="name"><b>${e(row.name)}</b></td>
          <td class="num right">${num(row.opening)}</td>
          <td class="num right">${num(row.sold_so_far)}</td>
          <td class="num right plan">${num(row.revised)}</td>
          <td class="num right ${row.difference >= 0 ? "up" : "down"}">${row.difference >= 0 ? "+" : ""}${num(row.difference)}</td>
        </tr>`).join("")}
      </tbody></table>` : `<p class="small muted" style="margin-top:12px">Nothing has moved by enough to be worth changing.</p>`}
    </div>`;
  }

  // Portions grouped by what the kitchen holds. Folded away, because the make
  // list above it is the decision and this is the same decision regrouped.
  function prepGroups(b) {
    const rows = (b.material_pressure || []).slice(0, 7);
    if (!rows.length) return "";
    return `<details class="context-disclosure" style="margin:0 18px">
      <summary>Grouped by what the kitchen holds</summary>
      <div class="context-detail"><div class="tablewrap"><table class="dt" style="min-width:520px"><thead><tr>
        <th>Group</th><th class="num right">Today</th><th class="num right">Normal ${e(weekday(b.date))}</th>
        <th class="num right">Difference</th><th>Driven by</th></tr></thead><tbody>
        ${rows.map((row) => `<tr>
          <td class="name"><b>${e(row.family)}</b></td>
          <td class="num right plan">${num(row.demand_index)}</td>
          <td class="num right">${num(row.baseline_index)}</td>
          <td class="num right ${row.change_units >= 0 ? "up" : "down"}">${row.change_units >= 0 ? "+" : ""}${num(row.change_units)}</td>
          <td class="muted small">${e(topItemForFamily(b, row.family))}</td>
        </tr>`).join("")}
      </tbody></table></div></div>
    </details>`;
  }

  // The next fourteen days, fetched the first time the panel is opened and
  // kept until the date or the location changes.
  function outlookKey() { return `${S.locationId}:${S.date}`; }

  async function loadOutlook() {
    const key = outlookKey();
    if (S.outlook && S.outlook.key === key) return;
    if (loadOutlook._inflight === key) return;
    loadOutlook._inflight = key;
    try {
      const d = await API.get(`/api/outlook?location_id=${encodeURIComponent(S.locationId)}&start=${S.date}&days=14`);
      S.outlook = { key, ...d };
      if (S.view === "today" && S.todayPane === "ahead") render(true);
    } catch (error) { toast(error.message, "error"); }
    loadOutlook._inflight = "";
  }

  function aheadPane() {
    const d = S.outlook && S.outlook.key === outlookKey() ? S.outlook : null;
    if (!d) return `<div class="card-body"><div class="skel" style="height:320px;border-radius:9px"></div></div>`;
    return `<div class="outlook-head"><span>Day</span><span>Expected</span><span>Biggest line</span><span>Against normal</span><span>Busiest</span></div>
      ${d.days.map((day) => `
        <button class="outlook-row" data-open-date="${day.date}">
          <span><b>${e(dMed(day.date))}</b><small>${e(day.weather.condition)}, ${day.weather.high}°</small></span>
          <span class="money"><b>${money(day.expected_revenue)}</b><small>${num(day.expected_units)} items</small></span>
          <span><b>${e(day.top_surges[0] ? `${day.top_surges[0].name}, ${day.top_surges[0].vs_baseline_units >= 0 ? "+" : ""}${day.top_surges[0].vs_baseline_units} vs normal` : `${num(day.top_item_units)} ${(day.top_item || "items").toLowerCase()}`)}</b><small>${e(day.occasion_name || "")}</small></span>
          <span class="money ${day.revenue_change_percent >= 0 ? "up" : "down"}"><b>${pct(day.revenue_change_percent)}</b><small class="muted">${day.confidence}% sure</small></span>
          <span><b>${e(day.peak_hour || "Not set")}</b><small>${e(day.demand_level)}</small></span>
        </button>`).join("")}
      <div class="card-foot">Open a day to work it. Days further out lean more on this location's own repeating pattern, and the confidence on each row already allows for that.</div>`;
  }
  // Which item contributes most to a prep group, so the row says why it moved.
  function topItemForFamily(brief, family) {
    const key = family.toLowerCase();
    const hit = brief.items
      .filter((row) => (row.family || "").replace(/-/g, " ").toLowerCase().includes(key.split(" ")[0]))
      .sort((a, b) => b.expected - a.expected)[0];
    return hit ? `${num(hit.expected)} ${hit.name.toLowerCase()}` : "the menu mix";
  }

  function confidenceWord(score) {
    if (score >= 80) return "sure";
    if (score >= 65) return "fairly sure";
    return "wide range";
  }

  function defaultSummary(b) {
    const s = b.summary; const cmp = b.comparison;
    if (Math.abs(s.revenue_change_percent) < 4) {
      return `Plan for ${money(s.expected_revenue)} and ${noun(s.expected_units, "item")}. A normal ${weekday(b.date)} here runs ${money(cmp.sales)}, so today sits inside the usual spread.`;
    }
    const more = s.difference_sales >= 0;
    return `Plan for ${money(s.expected_revenue)} against ${money(cmp.sales)} on ${cmp.label}. That is ${money(Math.abs(s.difference_sales))} ${more ? "more" : "less"} and about ${noun(Math.abs(s.difference_units), "item")} ${more ? "more" : "fewer"} across the menu.`;
  }

  function diffPhrase(value, kind) {
    const v = Number(value || 0);
    if (Math.abs(v) < (kind === "money" ? 1 : 1)) return "Level with it.";
    const word = v > 0 ? "more" : "less";
    return kind === "money"
      ? `<span class="${v > 0 ? "up" : "down"}">${money(Math.abs(v))} ${word}</span> today.`
      : `<span class="${v > 0 ? "up" : "down"}">${num(Math.abs(v))} ${v > 0 ? "more" : "fewer"}</span> today.`;
  }

  function tile(label, value, versus, basis) {
    return `<div class="tile">
      <span class="eyebrow">${e(label)}</span>
      <span class="value">${e(value)}</span>
      <span class="versus">${versus}</span>
      <span class="basis">${e(basis)}</span>
    </div>`;
  }

  function reasonRows(b, n) {
    const written = n?.factors || [];
    if (written.length) {
      return written.map((row, i) => {
        const signal = b.context.signals[i];
        return `<div class="reason">
          <div class="reason-top"><b>${e(row.heading)}</b>
            ${signal ? `<span class="effect-chip ${signal.effect >= 0 ? "up" : "down"}">${pct(signal.effect)} · ${signal.units >= 0 ? "+" : ""}${num(signal.units)} items</span>` : ""}
            <span class="tag plain">${e(row.confidence)} confidence</span></div>
          <p>${e(row.explanation)}</p>
          <div class="basis">${icon("info")}<span>${e(row.based_on)}</span></div>
        </div>`;
      }).join("");
    }
    if (!b.context.signals.length) {
      return `<div class="reason"><div class="reason-top"><b>Nothing unusual</b></div>
        <p>No outside condition moved today's number far enough to mention. The forecast is this location's own ${e(weekday(b.date))} pattern.</p>
        <div class="basis">${icon("info")}<span>${e(noun(b.comparison.based_on_days, "comparable " + weekday(b.date)))}</span></div></div>`;
    }
    return b.context.signals.map((row) => `<div class="reason">
      <div class="reason-top"><b>${e(row.label)}</b>
        <span class="effect-chip ${row.effect >= 0 ? "up" : "down"}">${pct(row.effect)} · ${row.units >= 0 ? "+" : ""}${num(row.units)} items · ${row.sales >= 0 ? "+" : "-"}${money(Math.abs(row.sales))}</span></div>
      <p>${e(row.detail)}</p>
      ${row.based_on ? `<div class="basis">${icon("info")}<span>${e(row.based_on)}</span></div>` : ""}
    </div>`).join("");
  }

  function weatherFoot(b) {
    const w = b.context.weather;
    const events = b.context.material_events || [];
    const parts = [
      `Weather: ${w.condition}, high ${w.high}°, low ${w.low}°${w.precipitation_mm ? `, ${w.precipitation_mm} mm rain` : ""}${w.snowfall_cm ? `, ${w.snowfall_cm} cm snow` : ""}.`,
      `Register history: ${num(b.data_health.history_days)} trading days through ${b.data_health.latest_sale_date ? dShort(b.data_health.latest_sale_date) : "not available"}.`,
      `Nearby listings checked: ${num(b.context.event_candidates_reviewed)}${events.length ? `, ${events.length} close enough and big enough to matter` : ", none big enough to matter"}.`,
    ];
    return `<div class="card-foot">${parts.map(e).join(" ")}</div>`;
  }

  function hourChart(b) {
    const rows = b.service_curve || [];
    if (!rows.length) return `<p class="muted small">No hourly pattern yet for this location.</p>`;
    const live = b.intraday && b.intraday.in_service ? b.intraday : null;
    const byslot = {};
    (live ? live.hours : []).forEach((h) => { byslot[h.slot] = h; });
    const max = Math.max(...rows.map((r) => Number(r.revenue)), ...(live ? live.hours.map((h) => h.rung_sales) : []), 1);
    const peak = rows.reduce((best, row) => (Number(row.revenue) > Number(best.revenue) ? row : best), rows[0]);
    const normalScale = b.summary.baseline_revenue / Math.max(1, b.summary.expected_revenue);
    return `<div class="hours">${rows.map((row) => {
      const slot = row.slot ?? row.hour;
      const h = Math.max(3, (Number(row.revenue) / max) * 100);
      const state = byslot[slot];
      const rung = state && state.state === "done" ? Math.max(2, (state.rung_sales / max) * 100) : null;
      const title = state && state.state === "done"
        ? `${row.label}: rang ${money(state.rung_sales)} against ${money(row.revenue)} called`
        : `${row.label}: ${money(row.revenue)} called, ${row.units} items`;
      return `<div class="hourcol ${row.hour === peak.hour ? "peak" : ""} ${state ? state.state : ""}" title="${e(title)}">
        <div class="track"><i class="ghost" style="height:${Math.max(3, h * normalScale)}%"></i><i style="height:${h}%"></i>${
          rung === null ? "" : `<i class="rung" style="height:${rung}%"></i>`}</div>
        <span>${e(row.label.replace(" ", ""))}</span>
      </div>`;
    }).join("")}</div>
    <div class="hour-legend">
      ${live
        ? `<span>The filled bar is what has rung. The outline is what was called this morning.</span>`
        : `<span>Busiest hour <b>${e(peak.label)}</b>, about <b>${money(peak.revenue)}</b> and <b>${peak.units} items</b></span>`}
      <span>Opens <b>${hourLabel(Number(b.location.open_hour))}</b>, closes <b>${hourLabel(Number(b.location.close_hour))}</b></span>
    </div>`;
  }

  // Where the day is actually running, and what that has changed. The morning
  // call is always shown beside the revision, because the record is scored on
  // the morning call and the operator should be able to see both.
  function liveCard(b) {
    const live = b.intraday;
    if (!live || !live.in_service) return "";
    const r = live.revision;
    if (!r) {
      return `<section class="card live">
        <div class="card-head"><div><h2>Where the day is running</h2>
          <p>${live.locked_local
            ? `Called at ${e(live.locked_local)} this morning, before service.`
            : ``}</p></div></div>
        <div class="card-body"><p class="lede">Not enough of the day has finished to say anything yet.</p></div>
      </section>`;
    }
    const ahead = r.difference_units >= 0;
    const pace = r.sold_units - r.called_by_now_units;
    return `<section class="card live">
      <div class="card-head"><div><h2>Where the day is running</h2>
        <p>Read at ${e(r.label)}, with ${r.expected_share_percent}% of a normal ${e(weekday(b.date))} behind us.</p></div>
        ${live.locked_local ? `<span class="tag plain">called at ${e(live.locked_local)}</span>` : ""}</div>
      <div class="card-body">
        <p class="lede">The register has rung <b>${num(r.sold_units)}</b> items and <b>${money(r.sold_sales)}</b>.
          By now a day like the one we called would have rung <b>${num(r.called_by_now_units)}</b>, so we are
          <b class="${pace >= 0 ? "up" : "down"}">${pace >= 0 ? "+" : ""}${num(pace)}</b> against that.</p>
        <div class="live-split">
          <div><span>Called this morning</span><b>${num(r.opening_units)} items</b><small>${money(r.opening_sales)}</small></div>
          <div><span>Where it looks like finishing</span><b>${num(r.revised_units)} items</b><small>${money(r.revised_sales)}</small></div>
          <div><span>Change</span><b class="${ahead ? "up" : "down"}">${ahead ? "+" : ""}${num(r.difference_units)} items</b>
            <small>${ahead ? "+" : ""}${money(r.difference_sales)}</small></div>
        </div>
        ${r.items.length ? `<table class="dt" style="margin-top:14px"><thead><tr>
          <th>Item</th><th class="num right">Called</th><th class="num right">Sold so far</th>
          <th class="num right">Now expecting</th><th class="num right">Change</th></tr></thead><tbody>
          ${r.items.map((row) => `<tr class="clickable" data-item-sheet="${e(row.item_id)}">
            <td class="name"><b>${e(row.name)}</b></td>
            <td class="num right">${num(row.opening)}</td>
            <td class="num right">${num(row.sold_so_far)}</td>
            <td class="num right plan">${num(row.revised)}</td>
            <td class="num right ${row.difference >= 0 ? "up" : "down"}">${row.difference >= 0 ? "+" : ""}${num(row.difference)}</td>
          </tr>`).join("")}
        </tbody></table>` : `<p class="small muted" style="margin-top:12px">Nothing has moved by enough to be worth changing.</p>`}
      </div>
    </section>`;
  }

  function makeTotal(b) {
    return (b.items || []).reduce((total, row) => total + (row.make ?? row.expected), 0);
  }

  // A share-of-day strip that can actually be read: the busiest hour is the only
  // one that carries a value, and the half-sold point is drawn where it falls.
  function hourShape(hourly) {
    const rows = hourly.hours || [];
    if (!rows.length) return "";
    const peak = rows.reduce((best, row) => (row.share_percent > best.share_percent ? row : best), rows[0]);
    return `<div class="shape" style="margin-top:14px">
      ${rows.map((row) => {
        const isPeak = row.label === peak.label;
        const half = row.label === hourly.half_sold_by;
        return `<div class="shape-col ${isPeak ? "peak" : ""} ${half ? "half" : ""}"
             title="${e(row.label)}: ${row.per_day} a day, ${row.share_percent}% of this item">
          <div class="shape-track"><i style="height:${Math.max(3, (row.share_percent / Math.max(1, peak.share_percent)) * 100)}%"></i></div>
          <span class="shape-val">${isPeak ? `${row.share_percent}%` : ""}</span>
          <span class="shape-lab">${e(row.label.replace(" ", ""))}</span>
        </div>`;
      }).join("")}
    </div>
    <p class="small muted" style="margin-top:8px">Tallest bar is ${e(peak.label)} at ${peak.share_percent}% of this item's day.${
      hourly.half_sold_by ? ` The marked hour is where half have gone.` : ""}</p>`;
  }

  const ROLES = new Set(["base", "protein", "dairy", "produce", "bread", "sauce",
    "sweetener", "beverage", "packaging", "other"]);

  function roleClass(role) {
    const key = String(role || "other").toLowerCase().trim();
    return `role-${ROLES.has(key) ? key : "other"}`;
  }

  // The share column, drawn. Widths are relative to the largest line so the
  // shape of the cost is readable, and every bar keeps its number.
  function shareCell(share, largest) {
    const width = Math.max(3, (Number(share) / Math.max(1, largest)) * 100);
    return `<div class="sharecell"><span>${share}%</span>
      <i style="width:${width}%"></i></div>`;
  }

  function itemTable(b) {
    const max = Math.max(...b.items.map((row) => row.upper), 1);
    return `<table class="dt"><thead><tr>
      <th>Item</th><th class="num right">Make</th><th class="num right">Will sell</th><th class="num right">Normal</th>
      <th>Range</th><th></th></tr></thead><tbody>
      ${b.items.map((item) => `
        <tr class="clickable" data-item-sheet="${e(item.item_id)}">
          <td class="name"><b>${e(item.name)}</b><small>${e(item.category)}${item.override ? " · you adjusted this" : ""}</small></td>
          <td class="num right plan">${num(item.make ?? item.expected)}<div class="small muted">${item.sell_out_percent !== null && item.sell_out_percent !== undefined ? `${item.sell_out_percent}% chance of running out` : ""}</div></td>
          <td class="num right">${num(item.expected)}</td>
          <td class="num right">${num(item.baseline)}<div class="small ${item.vs_baseline_units >= 0 ? "up" : "down"}">${item.vs_baseline_units >= 0 ? "+" : ""}${num(item.vs_baseline_units)}</div></td>
          <td><div class="rangebar">
            <div class="line"><i style="left:${(item.lower / max) * 100}%;width:${Math.max(3, ((item.upper - item.lower) / max) * 100)}%"></i><b style="left:${(item.expected / max) * 100}%"></b></div>
            <span>${num(item.lower)} to ${num(item.upper)}</span></div></td>
          <td class="right"><button class="btn sm ghost" data-do="adjust" data-item="${e(item.item_id)}" data-name="${e(item.name)}" data-qty="${item.make ?? item.expected}">Adjust</button></td>
        </tr>`).join("")}
    </tbody></table>`;
  }

  /* ---------- history ---------- */
  const RANGES = [["all", "All time"], ["year", "Past year"], ["quarter", "90 days"], ["month", "30 days"]];

  function renderHistory() {
    const tabs = `<div class="seg">
      ${[["days", "By day"], ["orders", "Orders"], ["accuracy", "Track record"]].map(([k, l]) =>
        `<button class="${S.historyTab === k ? "on" : ""}" data-htab="${k}">${l}</button>`).join("")}
    </div>`;
    let body = "";
    if (S.historyTab === "days") body = historyDays();
    else if (S.historyTab === "orders") body = historyOrders();
    else body = historyAccuracy();
    root.innerHTML = shell("History", "Closed days, orders, and how close each call was", tabs, body);
  }

  function rangeBar(current, attr) {
    return `<div class="seg">${RANGES.map(([k, l]) =>
      `<button class="${current === k ? "on" : ""}" data-${attr}="${k}">${l}</button>`).join("")}</div>`;
  }

  function historyDays() {
    const days = S.history.days;
    return `<div class="stack">
      <section class="card">
        <div class="card-head">
          <div><h2>Closed days</h2></div>
          <div class="spacer"></div>${rangeBar(S.history.range, "drange")}
        </div>
        <div id="dayrows">${days.length ? `<div class="dayrow head">
            <span class="when">Day</span><span class="cell">Rang up</span>
            <span class="cell">${S.history.costs ? "Left after costs" : "Units"}</span>
            <span class="cell">${S.history.costs ? "Cost to run" : "Average order"}</span>
            <span class="accmeter">How close the call was</span><span class="chev"></span>
          </div>` : ""}
        ${days.length ? days.map(dayRow).join("") : emptyState("No trading days in this range", "Pick a wider range, or connect the register to bring history in.")}</div>
        ${S.history.hasMore ? `<div class="loadmore"><button class="btn sm" data-do="more-days">${S.history.loading ? "Loading" : "Load more"}</button></div>` : ""}
        <div class="scroll-sentinel" id="sentinel"></div>
      </section>
      ${costsFoot(S.history.costs)}
      <p class="small muted" style="padding:0 2px">${S.data?.source === "register" ? "Orders come straight from the connected register." : "This location has daily and hourly totals but not line-level receipts, so individual orders are reconstructed from those totals. They add up exactly, and they are replaced by real receipts the moment a register is connected."}</p>
    </div>`;
  }

  function dayRow(day) {
    if (day.closed) {
      return `<div class="dayrow closed">
        <span class="when"><b>${e(dMed(day.date))}</b><small>${e(day.weekday)}</small></span>
        <span class="closed-note" style="grid-column:2 / -1">
          <b>Closed</b><span>${e(day.note)}</span></span>
      </div>`;
    }
    const acc = day.accuracy;
    const cls = acc === null ? "" : acc >= 90 ? "" : acc >= 80 ? "mid" : "low";
    const c = day.costs;
    return `<button class="dayrow" data-day-detail="${day.date}">
      <span class="when"><b>${e(dMed(day.date))}</b><small>${e(day.weekday)}</small></span>
      <span class="cell"><b>${money(day.sales)}</b><small>net of tax, ${num(day.orders)} orders</small></span>
      <span class="cell">${c
        ? `<b>${money(c.left_after_costs)}</b><small>left, about ${c.margin_percent}% of net</small>`
        : `<b>${num(day.units)}</b><small>units sold</small>`}</span>
      <span class="cell">${c
        ? `<b>${money(c.cogs + c.labour + c.other)}</b><small>food, wages and fixed</small>`
        : `<b>${money(day.average_order, true)}</b><small>average ticket, inc. tax</small>`}</span>
      <span class="accmeter">
        ${acc === null
          ? `<span class="small muted">Not scored yet</span>`
          : `<span class="top"><b>${Math.round(acc)}% per item</b><small>${num(day.predicted_units)} called, ${num(day.units)} sold</small></span>
             <span class="line"><i class="${cls}" style="width:${Math.max(4, acc)}%"></i></span>`}
      </span>
      <span class="chev">${icon("chevR")}</span>
    </button>`;
  }

  function costsFoot(summary) {
    if (!summary) return "";
    const w = summary.wage;
    return `<p class="small muted" style="padding:0 2px">${summary.configured
        ? `It uses the wage and the costs you entered.`
        : `Nobody has entered your real costs yet, so it is using ${e(w.detail)} and no fixed costs at all.`}
      <a href="/app" data-stab="costs">Put your own numbers in</a> and every figure here follows them.</p>`;
  }

  function historyOrders() {
    const rows = S.orders.rows;
    return `<div class="stack">
      <section class="card">
        <div class="card-head">
          <div><h2>Orders</h2><p>Every ticket, newest first. ${num(rows.length)} loaded so far.</p></div>
          <div class="spacer"></div>${rangeBar(S.orders.range, "orange")}
        </div>
        ${rows.length ? `<div class="orderrow head">
            <span>Time</span><span>What they ordered</span><span class="ch">Channel</span><span class="pay">Payment</span><span class="amt">Total</span>
          </div>` : ""}
        ${rows.length ? rows.map(orderRow).join("") : emptyState("No orders in this range", "Widen the range or connect the register.")}
        ${S.orders.hasMore ? `<div class="loadmore"><button class="btn sm" data-do="more-orders">${S.orders.loading ? "Loading" : "Load more"}</button></div>` : ""}
        <div class="scroll-sentinel" id="sentinel"></div>
      </section>
    </div>`;
  }

  function orderRow(order) {
    const names = order.lines.map((l) => `${l.quantity > 1 ? l.quantity + "x " : ""}${l.name}`).join(", ");
    return `<div class="orderrow">
      <span class="t">${e(clock(order.time))}<div class="small muted">${e(dShort(order.date))}</div></span>
      <span class="what"><b>${e(names)}</b><small>${order.number} · ${noun(order.item_count, "item")}</small></span>
      <span class="ch"><span class="tag plain">${e(order.channel)}</span></span>
      <span class="pay small muted">${e(order.payment)}</span>
      <span class="amt">${money(order.total, true)}<div class="small muted" style="font-weight:400">${money(order.subtotal, true)} before tax${order.tip ? " and tip" : ""}</div></span>
    </div>`;
  }

  function historyAccuracy() {
    const d = S.data;
    const trend = d.trend || { series: [], average: null, days: 0 };
    return `<div class="stack">
      <section class="tiles">
        ${tile("Forecast accuracy", `${d.summary.forecast_accuracy}%`,
          `Scored item by item against what the registers rang, so a day whose total lands can still score low.`,
          `Over the last ${noun(d.summary.days_evaluated, "closed day")}`)}
        ${tile("Days within 10%", trend.within_ten !== null ? `${trend.within_ten}%` : "Scoring",
          `Best ${trend.best ?? 0}%, worst ${trend.worst ?? 0}%.`,
          `Over the last ${noun(trend.days, "scored day")}`)}
        ${tile("Items tracked", num(d.summary.items_evaluated),
          `Every item that sold in the window has its own score.`,
          `Over the last ${noun(d.summary.days_evaluated, "closed day")}`)}
      </section>

      <section class="card">
        <div class="card-head"><div><h2>Called against sold</h2>
          <p>Solid is what sold. Dashed is what Quantify said the day before.</p></div></div>
        <div class="card-body">${lineChart(d.daily)}</div>
      </section>

      <section class="card">
        <div class="card-head"><div><h2>Where it misses</h2>
          <p>Worst first. An item that keeps missing the same way usually means a recipe, a portion, or a price changed.</p></div></div>
        <div class="tablewrap"><table class="dt"><thead><tr>
          <th>Item</th><th class="num right">Accuracy</th><th class="num right">Average miss</th><th class="num right">Units tested</th><th></th></tr></thead><tbody>
          ${d.item_accuracy.slice(0, 14).map((row) => `<tr>
            <td class="name"><b>${e(row.name)}</b></td>
            <td class="num right">${row.accuracy}%</td>
            <td class="num right ${row.wape > 25 ? "down" : ""}">${row.wape}%</td>
            <td class="num right">${num(row.actual_units)}</td>
            <td><div class="accmeter"><span class="line"><i class="${row.accuracy >= 90 ? "" : row.accuracy >= 80 ? "mid" : "low"}" style="width:${Math.max(4, row.accuracy)}%"></i></span></div></td>
          </tr>`).join("")}
        </tbody></table></div>
        
      </section>
    </div>`;
  }

  function lineChart(rows) {
    if (!rows || !rows.length) return `<p class="muted small">No closed days in this window yet.</p>`;
    const W = 900, H = 240, P = 34;
    const max = Math.max(...rows.flatMap((r) => [Number(r.actual), Number(r.predicted)]), 1) * 1.08;
    const x = (i) => P + (i * (W - P * 2)) / Math.max(1, rows.length - 1);
    const y = (v) => H - P - (Number(v) / max) * (H - P * 2);
    const line = (key) => rows.map((r, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(r[key]).toFixed(1)}`).join(" ");
    const grids = [0, 0.5, 1].map((f) => {
      const yy = H - P - f * (H - P * 2);
      return `<line class="grid" x1="${P}" y1="${yy}" x2="${W - P}" y2="${yy}"/>
        <text class="axis-label" x="${P - 6}" y="${yy + 3}" text-anchor="end">${num(max * f)}</text>`;
    }).join("");
    const ticks = rows.filter((_, i) => i % Math.ceil(rows.length / 7) === 0)
      .map((r) => `<text class="axis-label" x="${x(rows.indexOf(r))}" y="${H - P + 16}" text-anchor="middle">${dShort(r.date)}</text>`).join("");
    return `<div class="linechart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Predicted against actual item units">
      ${grids}${ticks}
      <path class="predicted" d="${line("predicted")}"/>
      <path class="actual" d="${line("actual")}"/>
    </svg>
    <div class="chart-key"><span><i class="k-actual"></i>Sold</span><span><i class="k-pred"></i>Called</span></div></div>`;
  }

  /* ---------- menu and recipes, under Settings ---------- */
  let menuPollTimer;
  function scheduleMenuPoll() {
    clearTimeout(menuPollTimer);
    if (!(S.menu?.pending_compositions > 0) || S.view !== "settings" || S.settingsTab !== "menu") return;
    const locationId = S.locationId;
    menuPollTimer = setTimeout(async () => {
      const current = () => S.locationId === locationId && S.view === "settings" && S.settingsTab === "menu";
      if (!current()) return;
      if (isTyping() || layer.innerHTML) return scheduleMenuPoll();
      try {
        const menu = await API.get(`/api/menu?location_id=${encodeURIComponent(locationId)}`);
        if (!current()) return;
        if (!isTyping() && !layer.innerHTML) { S.menu = menu; render(true); }
      } catch (_) { /* Keep the usable menu and retry while this tab is open. */ }
      if (current()) scheduleMenuPoll();
    }, 2500);
  }
  // Confirmed means somebody at this location saved the recipe; anything else
  // was read from the till label and is an estimate until then.
  const recipeConfirmed = (item) => !!(item && item.composition && item.composition.writer === "owner");

  function menuAddCard() {
    return `<section class="card" id="menu-add">
      <div class="card-head"><div><h2>Add items by hand</h2>
        <p>One item per line: name, category, price. Items that come from the register are left as they are.</p></div></div>
      <div class="card-body">
        <textarea id="menu-text" aria-label="Items to add, one item per line" rows="5" placeholder="Double cheeseburger, Burgers, 15.50&#10;Pep slice, Slices, 4.25&#10;Iced latte, Drinks, 6.00"></textarea>
        <div class="btn-row" style="margin-top:12px">
          <button class="btn" type="button" data-do="menu-preview">Preview</button>
          <button class="btn accent" type="button" data-do="menu-import">Add items</button>
        </div>
        <div id="menu-preview-out"></div>
      </div>
    </section>`;
  }

  function settingsMenu() {
    const d = S.menu;
    if (!d) return `<section class="card"><div class="card-body">${skeleton()}</div></section>`;
    if (!d.items.length) {
      return `<div class="stack-tight">
        <section class="card">${emptyState("Nothing on the menu yet", "Connect the register and the menu fills in on its own, or add items below.",
          `<div class="btn-row" style="justify-content:center">
            <button class="btn" type="button" data-stab="location">Connect the register</button>
            <a class="btn" href="#menu-add" data-scroll="menu-add">Add items</a>
          </div>`)}</section>
        ${menuAddCard()}
      </div>`;
    }
    const confirmed = d.items.filter(recipeConfirmed).length;
    return `<div class="stack-tight">
      <section class="card">
        <div class="card-head">
          <div><h2>Recipes</h2>
            <p>${noun(d.items.length, "item")} on the menu, ${confirmed ? `${num(confirmed)} confirmed` : "none confirmed yet"}. Open one to see what goes into it. Confirmed recipes drive the order list.</p></div>
        </div>
        <div id="menulist">${d.items.map(menuRow).join("")}</div>
      </section>
      ${menuAddCard()}
    </div>`;
  }

  function menuRow(item) {
    const open = S.open.has(item.id);
    const comp = item.composition;
    const confirmed = recipeConfirmed(item);
    const hasCost = item.food_cost !== undefined && item.food_cost !== null;
    const food = hasCost ? Math.round(Number(item.food_cost) * 10) / 10 : 0;
    const foodText = hasCost ? `${money(food, true)} (${item.cost_share_percent}%)` : "";
    return `<div class="exp ${open ? "open" : ""}">
      <button class="exp-head menu-head" type="button" data-expand="${e(item.id)}" aria-expanded="${open ? "true" : "false"}">
        <span class="chev">${icon("chevR")}</span>
        <span class="menu-name"><b>${e(item.normalized_name)}</b><small>${e(item.category)}</small>
          <small class="menu-line">Sells for ${money(item.price, true)}${hasCost ? ` · Food about ${foodText}` : ""}</small></span>
        <span class="tag plain">${confirmed ? "Confirmed" : "Estimated"}</span>
        <span class="menu-money"><small>Sells for</small><b>${money(item.price, true)}</b></span>
        <span class="menu-money">${hasCost
          ? `<small>Food about</small><b>${foodText}</b>`
          : `<small>Food cost</small><b class="muted">Not set</b>`}</span>
      </button>
      ${open ? `<div class="exp-body">
        ${comp ? `
          <p class="lede" style="margin-top:12px">${e(comp.summary)}</p>
          <table class="dt parts recipe"><thead><tr>
            <th>Part</th><th class="num right">Each</th><th class="num right">Share of food cost</th></tr></thead><tbody>
            ${(() => { const top = Math.max(...comp.components.map((c) => Number(c.share) || 0), 1);
              return comp.components.map((c) => `<tr>
              <td class="name"><b>${e(c.name)}</b>${c.confidence === "low" ? ` <span class="check-this">check this</span>` : ""}</td>
              <td class="num right">${e(c.quantity || "not stated")}</td>
              <td class="num right">${shareCell(c.share, top)}</td>
            </tr>`).join(""); })()}
          </tbody></table>
          <div class="btn-row" style="margin-top:12px">
            <button class="btn sm" type="button" data-do="edit-composition" data-item="${e(item.id)}">Edit recipe</button>
            <button class="btn sm ghost" type="button" data-item-sheet="${e(item.id)}">See how it sells</button>
            <details class="recipe-more"><summary class="btn sm ghost">More</summary>
              <button class="btn sm ghost" type="button" data-do="recompose" data-item="${e(item.id)}">Re-read from the till label</button>
            </details>
          </div>`
        : `<p class="lede" style="padding:14px 0">Working this one out. Check back in a moment.</p>`}
      </div>` : ""}
    </div>`;
  }

  // One editable line of a recipe: what the part is, what kind of thing it
  // is, how much goes into one item, and its share of the food cost.
  const RECIPE_ROLES = ["protein", "bread", "dairy", "produce", "sauce", "base", "sweetener", "beverage", "packaging", "other"];
  function recipeRow(c = {}) {
    const role = String(c.role || "other").toLowerCase();
    return `<div class="recipe-row">
      <label class="recipe-field recipe-name"><span>Part</span><input class="rr-name" placeholder="Beef patty" value="${e(c.name || "")}"></label>
      <label class="recipe-field"><span>Role</span><select class="rr-role">${RECIPE_ROLES.map((r) =>
        `<option value="${r}" ${role === r ? "selected" : ""}>${r}</option>`).join("")}</select></label>
      <label class="recipe-field"><span>Each</span><input class="rr-qty" placeholder="1 patty" value="${e(c.quantity || "")}"></label>
      <label class="recipe-field"><span>Share of food cost</span><span class="unitwrap"><input class="rr-share" type="number" min="0" max="100" step="1" inputmode="numeric" value="${c.share === undefined || c.share === "" ? "" : e(c.share)}"><i class="unit">%</i></span></label>
      <button class="icon-btn" type="button" data-settings="recipe-drop" aria-label="Remove this part">${icon("close")}</button>
    </div>`;
  }

  // What the recipe editor currently holds, as the payload the API takes.
  function recipeRowsRead() {
    return Array.from(document.querySelectorAll("#recipe-rows .recipe-row")).map((node) => ({
      name: node.querySelector(".rr-name").value.trim(),
      role: node.querySelector(".rr-role").value,
      quantity: node.querySelector(".rr-qty").value.trim(),
      share: Number(node.querySelector(".rr-share").value) || 0,
      confidence: "high",
    })).filter((row) => row.name || row.share);
  }

  function recipeTotalPaint() {
    const total = recipeRowsRead().reduce((n, row) => n + row.share, 0);
    const node = document.getElementById("recipe-total");
    if (node) node.textContent = `Shares add up to ${Math.round(total)}%`;
  }

  /* ---------- ordering ---------- */
  // The buying list, one card per supplier. Three things a register cannot
  // know are asked for here once and then kept: who sells the thing, how it
  // is bought, and what is on the shelf. With those, what you will use becomes
  // what to order and by when. Lines that need a hand come first; the rest sit
  // behind one line that says how long they are covered for.
  const ORDER_WINDOWS = [["2", "2 days"], ["3", "3 days"], ["5", "5 days"], ["7", "7 days"]];
  const LEAD_CHOICES = [[0, "same day"], [1, "next day"], [2, "two days"], [3, "three days"], [5, "five days"], [7, "a week"]];
  const WEEKDAY_CHOICES = [["mon", "Mon"], ["tue", "Tue"], ["wed", "Wed"], ["thu", "Thu"], ["fri", "Fri"], ["sat", "Sat"], ["sun", "Sun"]];
  const PACK_LABELS = ["case", "box", "bag", "tray", "flat", "tub", "bucket", "sleeve", "carton", "sack", "pack"];
  const UNCHANGED_UNITS = new Set(["g", "kg", "lb", "oz", "ml", "l", "gal", "floz", "qt"]);
  const CHANNEL_LABELS = {
    sent: "Sent by email", outbox: "Saved, not sent", failed: "Email did not go through",
    drafted: "Sent from the mail app", confirmed: "Sent from the mail app", opened: "Placed on their site", copied: "Copied",
  };

  const lineKey = (line) => String(line.name || "").toLowerCase();
  const cap = (text) => (text ? text.charAt(0).toUpperCase() + text.slice(1) : "");
  const locationName = () => (S.boot.locations.find((row) => row.id === S.locationId) || {}).name || "";
  const locQ = () => `location_id=${encodeURIComponent(S.locationId)}`;
  const counted = (line) => line.on_hand !== null && line.on_hand !== undefined;
  // Whole numbers stay whole; anything else keeps one decimal, the same rule
  // for what you will use, what is on hand and what to order.
  const fmtQty = (v) => {
    const n = Number(v || 0);
    return Number.isInteger(n) ? num(n) : String(Math.round(n * 10) / 10);
  };
  const glyph = (path) => `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">${path}</svg>`;
  const MINUS = glyph('<path d="M5 12h14"/>');
  const PLUS = glyph('<path d="M12 5v14M5 12h14"/>');

  function plural(n, unit) {
    const u = String(unit || "");
    if (Number(n) === 1 || !u || UNCHANGED_UNITS.has(u) || /s$/.test(u)) return u;
    if (/[^aeiou]y$/.test(u)) return u.slice(0, -1) + "ies";
    if (/(s|x|ch|sh)$/.test(u)) return u + "es";
    return u + "s";
  }

  function hostOf(url) {
    try { return new URL(url).hostname.replace(/^www\./, ""); } catch (_) { return url; }
  }

  // Today, tomorrow, a weekday inside the week, otherwise the date. Timestamps
  // arrive in the location's own clock, so the date part is the local date.
  function whenLabel(iso) {
    if (!iso) return "";
    const day = String(iso).slice(0, 10);
    const today = todayISO();
    if (day === today) return "today";
    if (day === addDays(today, 1)) return "tomorrow";
    if (day === addDays(today, -1)) return "yesterday";
    const gap = Math.round((dObj(day) - dObj(today)) / 86400000);
    if (gap > 1 && gap < 7) return weekday(day);
    return dShort(day);
  }
  // The same, short enough for a cell: "Tue" for Tuesday.
  const shortDay = (text) => String(text || "").replace(/^([A-Z][a-z]{2})[a-z]*day$/, "$1");
  const whenShort = (iso) => shortDay(whenLabel(iso));

  // "Tuesday by 3 PM" -> "by Tue 3 PM"; "today by 3 PM" -> "by 3 PM today".
  function orderByShort(label) {
    const t = String(label || "");
    if (!t || t === "now") return t;
    const m = t.match(/^(.+?) by (.+)$/);
    if (!m) return `by ${shortDay(t)}`;
    if (m[1] === "today") return `by ${m[2]} today`;
    return `by ${shortDay(m[1])} ${m[2]}`;
  }

  function joinAnd(items) {
    if (items.length <= 1) return items.join("");
    if (items.length === 2) return `${items[0]} and ${items[1]}`;
    return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
  }

  function orderQty(line) {
    const edit = S.order.edits[lineKey(line)];
    return edit === undefined ? Number(line.suggested.quantity || 0) : edit;
  }

  // Packs step by one. Loose units step by a grain that suits the size.
  function orderGrain(line) {
    if (line.pack_size) return 1;
    return line.typical >= 100 ? 10 : line.typical >= 20 ? 5 : 1;
  }

  // Short, or not counted yet. The API says so when it can; otherwise it is
  // read off the line.
  function needsAction(line) {
    if (line.needs_action !== undefined && line.needs_action !== null) return !!line.needs_action;
    if (!counted(line)) return true;
    return Number(line.suggested.quantity || 0) > 0 || orderQty(line) > 0;
  }

  // "Add something else" lines live in the browser, per location, until an
  // order carrying them goes out.
  const extrasKey = () => `quantify.extras.${S.locationId}`;
  function loadExtras() {
    if (S.order.extrasFor === S.locationId) return;
    S.order.extrasFor = S.locationId;
    let rows = [];
    try { rows = JSON.parse(store.get(extrasKey()) || "[]"); } catch (_) { rows = []; }
    S.order.extras = Array.isArray(rows) ? rows.filter((row) => row && row.name) : [];
  }
  function saveExtras() { store.set(extrasKey(), JSON.stringify(S.order.extras)); }

  // Every supplier gets a card, even one with nothing assigned yet, so it can
  // always be reached to edit or remove. The unassigned lines come last.
  function groupBySupplier(lines, suppliers) {
    const byId = new Map(suppliers.map((s) => [s.id, { supplier: s, lines: [], extras: [] }]));
    const loose = { supplier: null, lines: [], extras: [] };
    lines.forEach((line) => (byId.get(line.supplier_id) || loose).lines.push(line));
    S.order.extras.forEach((row) => (byId.get(row.supplier_id) || loose).extras.push(row));
    const groups = Array.from(byId.values());
    if (loose.lines.length || loose.extras.length || !groups.length) groups.push(loose);
    return groups;
  }

  function findGroup(id) {
    const d = S.data;
    if (!d || !d.ready) return null;
    return groupBySupplier((d.lines || []).filter((row) => row.orderable), d.suppliers || [])
      .find((g) => (g.supplier ? g.supplier.id : "") === (id || "")) || null;
  }

  function renderOrdering() {
    const d = S.data;
    S.order.folds = S.order.folds || {};
    loadExtras();
    const tools = `<div class="seg">${ORDER_WINDOWS.map(([k, l]) =>
      `<button class="${String(S.order.days) === k ? "on" : ""}" data-owin="${k}">${l}</button>`).join("")}</div>`;
    const lines = d && d.ready ? (d.lines || []).filter((row) => row.orderable) : [];
    if (!lines.length) {
      root.innerHTML = shell("Order", "", tools, `<section class="card">${orderEmpty(d)}</section>`);
      return;
    }
    // Whatever was being typed keeps its cursor through a repaint.
    const active = document.activeElement;
    const keep = active && (active.dataset.supplyCount ? ["data-supply-count", active.dataset.supplyCount]
      : active.dataset.oqty ? ["data-oqty", active.dataset.oqty] : null);

    const c = d.counts || {};
    const shares = (d.lines || []).filter((row) => !row.orderable);
    const suppliers = d.suppliers || [];
    const groups = groupBySupplier(lines, suppliers);
    const changed = Object.keys(S.order.edits).length;
    const looseShown = groups.some((g) => !g.supplier);
    const backlog = d.backlog || [];

    const body = `<div class="stack">
      ${groups.map((g) => supplyGroup(g, d, suppliers)).join("")}
      <div class="btn-row supply-page-actions">
        <button class="btn sm" data-supply="order-add">Add something else</button>
        ${looseShown ? "" : `<button class="btn sm" data-supply="supplier-new">Add supplier</button>`}
        ${changed ? `<button class="btn sm ghost" data-supply="order-reset">Back to the suggested numbers</button>` : ""}
      </div>
      ${c.uncovered ? `<p class="small muted supply-note">${num(c.uncovered)} of your ${num(c.menu_items)} menu items have no recipe on file, so nothing they use is on this list.</p>` : ""}
      ${backlog.length ? `<details class="context-disclosure supply-note">
        <summary>${backlog.length === 1 ? "1 item worth a recipe" : `${backlog.length} items worth a recipe`}</summary>
        <div class="context-detail">
          <p>Each one moves that much of your buying onto this list.</p>
          <div class="tablewrap"><table class="dt"><tbody>
            ${backlog.map((row) => `<tr class="clickable" data-item-sheet="${e(row.item_id)}">
              <td class="name"><b>${e(row.name)}</b><small>about ${num(row.monthly_units)} a month</small></td>
              <td class="num right">${money(row.monthly_value)}<div class="small muted">a month</div></td>
            </tr>`).join("")}
          </tbody></table></div>
        </div></details>` : ""}
      ${shares.length ? `<details class="context-disclosure supply-note">
        <summary>${shares.length === 1 ? "1 thing used by the batch, not bought by the count" : `${shares.length} things used by the batch, not bought by the count`}</summary>
        <div class="context-detail">
          ${shares.map((row) => `<p><b>${e(row.name)}</b>: ${num(row.typical)} ${e(row.unit)}.</p>`).join("")}
        </div></details>` : ""}
      <details class="context-disclosure supply-note" id="orders-sent" ${S.order.logOpen ? "open" : ""}>
        <summary>Orders you sent</summary>
        <div class="context-detail" id="orders-sent-body">${ordersSentBody()}</div>
      </details>
    </div>`;
    root.innerHTML = shell("Order", `${e(dMed(d.start))} through ${e(dMed(d.end))}`, tools, body);
    if (S.order.logOpen) loadOrderLog();

    if (keep) {
      const node = root.querySelector(`[${keep[0]}="${CSS.escape(keep[1])}"]`);
      if (node) { node.focus({ preventScroll: true }); node.select?.(); }
    }
  }

  // Nothing to buy: one heading, one sentence, the one button that fixes it.
  function orderEmpty(d) {
    const cause = orderCause(d);
    if (cause === "no_menu") {
      return emptyState("Nothing to buy yet", "Add your menu and Quantify works out what each item uses.",
        `<button class="btn accent" data-stab="menu">Add the menu</button>`);
    }
    if (cause === "no_recipes") {
      return emptyState("Nothing to buy yet", "Confirm what goes into each item and the list of what to order appears here.",
        `<button class="btn accent" data-stab="menu">Open the menu</button>`);
    }
    return emptyState("Nothing to buy yet", "Once the register has a week of sales, this becomes the list of what to buy.",
      `<button class="btn accent" data-stab="location">Connect the register</button>`);
  }

  // Why the list is empty. The plan says so when it can; otherwise items with
  // no recipe mean recipes, and the day's brief says whether there is a menu.
  function orderCause(d) {
    if (d && d.cause) return d.cause;
    if (d && ((d.unknown || []).length || (d.items_covered || 0) + (d.items_uncovered || 0) > 0)) return "no_recipes";
    const known = S.order.cause;
    if (known && known.for === S.locationId) return known.value;
    fetchOrderCause();
    return "no_sales";
  }

  async function fetchOrderCause() {
    const loc = S.locationId;
    if (S.order.causeFor === loc) return;
    S.order.causeFor = loc;
    try {
      const brief = await API.get(`/api/brief?location_id=${encodeURIComponent(loc)}&date=${todayISO()}`);
      const items = (brief.items || []).length;
      const health = brief.data_health || {};
      const noSales = brief.no_history !== undefined ? !!brief.no_history : !health.latest_sale_date;
      S.order.cause = { for: loc, value: noSales ? "no_sales" : items ? "no_recipes" : "no_menu" };
      if (S.view === "ordering" && S.locationId === loc && !isTyping() && !layer.innerHTML) render(true);
    } catch (_) {
      S.order.causeFor = "";
    }
  }

  function supplierLine(s) {
    const parts = [];
    const days = s.delivery_days || [];
    parts.push(days.length ? `Delivers ${joinAnd(days.map((k) => cap(k)))}.` : "Delivers any day.");
    const sched = s.schedule || {};
    if (sched.next_delivery) {
      const next = whenLabel(sched.next_delivery);
      const label = String(sched.order_by_label || "");
      const m = label.match(/^(.+?) by (.+)$/);
      const byDay = m ? m[1] : label;
      const byClock = m ? m[2] : "";
      if (!byClock && byDay === next) parts.push(`Next delivery ${next}. Order the same day.`);
      else if (byClock && byDay === next) parts.push(`Next delivery ${next} if the order is in by ${byClock} that day.`);
      else if (!byClock) parts.push(`Next delivery ${next} if the order is in ${byDay === "today" ? "today" : `by ${byDay}`}.`);
      else parts.push(`Next delivery ${next} if the order is in by ${byClock} ${byDay}.`);
    }
    if (s.last_order && s.last_order.status !== "copied") {
      const o = s.last_order;
      parts.push(`Last order ${whenLabel(o.sent_at)}${o.expected_on ? `, lands ${whenLabel(o.expected_on)}` : ""}.`);
    }
    return e(parts.join(" "));
  }

  function supplyGroup(g, d, suppliers) {
    const s = g.supplier;
    const id = s ? s.id : "";
    const name = s ? s.name : "";
    const title = s ? name : (suppliers.length ? "Not assigned yet" : "What to buy");
    const sub = s ? supplierLine(s)
      : suppliers.length ? "Tap a name to choose who you buy it from."
      : d.counts_taken ? "" : "Count what is on the shelf and the Order column drops to what is short.";
    const act = g.lines.filter(needsAction);
    const rest = g.lines.filter((line) => !needsAction(line));
    const rows = act.map((line) => supplyRow(line)).join("") + g.extras.map((row) => extraRow(row)).join("");
    const fold = rest.length ? `<details class="sfold" data-fold="${e(id)}" ${S.order.folds[id] ? "open" : ""}>
        <summary>${rest.length === 1 ? "1 more line is" : `${rest.length} more lines are`} covered through ${e(whenLabel(d.end))}</summary>
        ${rest.map((line) => supplyRow(line)).join("")}
      </details>` : "";
    const nothing = !g.lines.length && !g.extras.length;
    const ready = groupOrderLines(g).length > 0;
    const off = `data-needs-order ${ready ? "" : 'disabled title="Put a number on at least one line first"'}`;
    const open = s && s.website
      ? `<a class="btn sm accent" href="${e(s.website)}" target="_blank" rel="noopener" data-supply="order-site" data-id="${e(id)}" data-needs-order aria-disabled="${!ready}" tabindex="${ready ? 0 : -1}">Open ${e(name)} ${icon("external")}</a>`
      : "";
    const actions = s ? `
        ${open ? `<button class="btn sm" data-supply="order-email" data-id="${e(id)}" ${off}>Email order</button>${open}`
          : `<button class="btn sm accent" data-supply="order-email" data-id="${e(id)}" ${off}>Email order</button>`}
        <details class="overflow">
          <summary class="btn sm ghost" aria-label="More for ${e(name)}">More</summary>
          <div class="overflow-menu">
            <button type="button" data-supply="order-copy" data-id="${e(id)}" ${off}>Copy the list</button>
            <button type="button" data-supply="supplier-edit" data-id="${e(id)}">Edit supplier</button>
          </div>
        </details>`
      : `<button class="btn sm ghost" data-supply="order-copy" data-id="" ${off}>Copy the list</button>
         <button class="btn sm accent" data-supply="supplier-new">Add supplier</button>`;
    return `<section class="card supply-group" data-supplier-group="${e(id)}">
      <div class="card-head">
        <div><h2>${e(title)}</h2>${sub ? `<p>${sub}</p>` : ""}</div>
        <div class="spacer"></div>
        <div class="btn-row supply-head-actions">${actions}</div>
      </div>
      ${nothing ? `<div class="card-body small muted">Nothing assigned to ${e(name)} yet. Tap a line's name to choose who you buy it from.</div>` : `
      <div class="sline head"><span>Item</span><span>Will use</span><span>On hand</span><span>Runs out</span><span class="right">Order</span></div>
      ${rows}${fold}`}
    </section>`;
  }

  function supplyRow(line) {
    const key = lineKey(line);
    const qty = orderQty(line);
    const unit = line.suggested.unit;
    const has = counted(line);
    const inPacks = !!line.pack_size;
    const meta = [];
    if (inPacks) meta.push(`${fmtQty(line.pack_size)} ${plural(line.pack_size, line.pack_unit)} per ${line.pack_label}`);
    if (line.product_code) meta.push(`code ${line.product_code}`);
    if (line.pack_note) meta.push(line.pack_note);
    return `<div class="sline" data-line-row="${e(key)}">
      <div class="s-name">
        <button class="s-open" data-supply="item" data-key="${e(key)}" aria-label="Settings for ${e(line.name)}">${e(line.name)}</button>
        ${meta.length ? `<small>${e(meta.join(" · "))}</small>` : ""}
      </div>
      <div class="s-use"><span class="lbl">Will use</span><b>${fmtQty(line.typical)}</b> <span class="unit">${e(plural(line.typical, line.unit))}</span></div>
      <div class="s-count"><span class="lbl">On hand</span>
        <label class="count">
          <input type="number" inputmode="decimal" min="0" step="any"
                 data-supply-count="${e(key)}" data-unit="${inPacks ? "pack" : e(line.unit)}" data-kind="${e(line.kind)}"
                 value="${has ? e(fmtQty(inPacks ? line.on_hand_packs : line.on_hand)) : ""}" aria-label="On hand, ${e(line.name)}">
          <span>${e(plural(2, inPacks ? line.pack_label : line.unit))}</span>
        </label>
        <small class="count-sub">${countSub(line)}</small>
        <small class="count-err" hidden></small>
      </div>
      <div class="s-runs">${runsOutCell(line)}</div>
      <div class="s-order right"><span class="lbl">Order</span>
        <div class="stepper">
          <button type="button" class="qstep" data-oadj="${e(key)}" data-step="-1" tabindex="-1" aria-label="Less">${MINUS}</button>
          <input class="qin" data-oqty="${e(key)}" type="number" inputmode="decimal" min="0" step="any" value="${fmtQty(qty).replace(/,/g, "")}" aria-label="Order, ${e(line.name)}">
          <button type="button" class="qstep" data-oadj="${e(key)}" data-step="1" tabindex="-1" aria-label="More">${PLUS}</button>
        </div>
        <small class="qunit">${orderUnit(line, qty)}</small>
      </div>
    </div>`;
  }

  function countSub(line) {
    return counted(line) && line.pack_size ? `${fmtQty(line.on_hand)} ${e(plural(line.on_hand, line.unit))}` : "";
  }

  function orderUnit(line, qty) {
    const unit = line.suggested.unit;
    return `${e(plural(qty, unit))}${line.pack_size && qty ? `, ${fmtQty(qty * line.pack_size)} ${e(plural(qty * line.pack_size, line.pack_unit))}` : ""}`;
  }

  // One line: the day it runs out, then what to do about it. Colour only
  // inside two days. Nothing at all until it has been counted.
  function runsOutCell(line) {
    if (!counted(line)) return "";
    const days = line.days_of_cover;
    if (days === null || days === undefined) return `<span class="lbl">Runs out</span><small>Not used in this window</small>`;
    const o = line.order || {};
    const tone = days < 1 ? "down" : days < 2 ? "warn" : "";
    const when = `<b class="${tone}">${e(cap(line.runs_out_label || whenLabel(line.runs_out_on)))}</b>`;
    const onOrder = Number(line.on_order || 0);
    let note = "";
    if (onOrder > 0) {
      const unit = line.on_order_unit || line.suggested.unit;
      note = `<small>${fmtQty(onOrder)} ${e(plural(onOrder, unit))} on order${line.arrives ? `, lands ${e(whenShort(line.arrives))}` : ""}</small>`;
    } else if (line.no_supplier || !line.supplier_id) {
      note = `<small>Choose a supplier</small>`;
    } else if (o.late) {
      note = `<small class="down">Order now${o.arrives ? `, lands ${e(whenShort(o.arrives))}` : ""}</small>`;
    } else if (o.order_by_label && (Number(line.suggested.quantity || 0) > 0 || orderQty(line) > 0)) {
      note = `<small>Order ${e(orderByShort(o.order_by_label))}</small>`;
    }
    return `<span class="lbl">Runs out</span>${when}${note}`;
  }

  function extraRow(row) {
    const index = S.order.extras.indexOf(row);
    return `<div class="sline extra">
      <div class="s-name"><b>${e(row.name)}</b><small>added by you</small></div>
      <div class="s-use"></div><div class="s-count"></div><div class="s-runs"></div>
      <div class="s-order right"><span class="lbl">Order</span><b>${e(row.qty)}</b>
        <button class="btn sm ghost" data-supply="order-drop" data-index="${index}">Remove</button></div>
    </div>`;
  }

  // A stepper tap changes one number on one row. The page is not repainted.
  function orderAdjust(key, step) {
    const line = ((S.data && S.data.lines) || []).find((row) => lineKey(row) === key);
    if (!line) return;
    const now = orderQty(line);
    setOrderQty(line, Math.max(0, Math.round((now + step * orderGrain(line)) * 10) / 10));
  }

  function setOrderQty(line, qty) {
    const key = lineKey(line);
    S.order.edits[key] = qty;
    const row = root.querySelector(`[data-line-row="${CSS.escape(key)}"]`);
    if (!row) return;
    const box = row.querySelector("[data-oqty]");
    if (box && document.activeElement !== box) box.value = String(qty);
    const unit = row.querySelector(".qunit");
    if (unit) unit.innerHTML = orderUnit(line, qty);
    const runs = row.querySelector(".s-runs");
    if (runs) runs.innerHTML = runsOutCell(line);
    const reset = root.querySelector("[data-supply='order-reset']");
    updateSupplierActions(line);
    if (!reset) {
      const actions = root.querySelector(".supply-page-actions");
      if (actions) actions.insertAdjacentHTML("beforeend", `<button class="btn sm ghost" data-supply="order-reset">Back to the suggested numbers</button>`);
    }
  }

  function updateSupplierActions(line) {
    const id = line.supplier_id || "";
    const group = findGroup(id);
    if (!group) return;
    const ready = groupOrderLines(group).length > 0;
    const card = root.querySelector(`[data-supplier-group="${CSS.escape(id)}"]`);
    card?.querySelectorAll("[data-needs-order]").forEach((node) => {
      if (node.tagName === "A") {
        node.setAttribute("aria-disabled", String(!ready));
        node.tabIndex = ready ? 0 : -1;
      } else if (!node.dataset.busy) node.disabled = !ready;
      if (ready) node.removeAttribute("title");
    });
  }

  function groupOrderLines(g) {
    const lines = g.lines.map((line) => ({
      name: line.name, quantity: orderQty(line), unit: line.suggested.unit,
      pack_size: line.pack_size || 0, pack_unit: line.pack_unit || "", product_code: line.product_code || "",
    })).filter((row) => row.quantity > 0);
    g.extras.forEach((row) => {
      const match = String(row.qty).match(/^\s*([\d.]+)\s*(.*)$/);
      lines.push({ name: row.name, quantity: match ? Number(match[1]) : 1, unit: match ? match[2].trim() : String(row.qty),
        pack_size: 0, pack_unit: "", product_code: "" });
    });
    return lines;
  }

  function linesText(rows) {
    return rows.map((row) => `${row.name}: ${fmtQty(row.quantity)} ${plural(row.quantity, row.unit)}`
      + (row.pack_size ? ` (${fmtQty(row.pack_size)} ${plural(row.pack_size, row.pack_unit)} each)` : "")
      + (row.product_code ? `, code ${row.product_code}` : "")).join(NEWLINE);
  }

  function orderTextFor(g, d) {
    const s = g.supplier;
    const head = `Order for ${locationName()}${s ? `, ${s.name}` : ""}, ${dMed(d.start)} to ${dMed(d.end)}`;
    return `${head}${NEWLINE}${NEWLINE}${linesText(groupOrderLines(g))}`;
  }

  // Copying never records an order. Only "I placed this order" and "I sent
  // this order" and a sent email do.
  async function supplyCopy(id) {
    const g = findGroup(id);
    if (!g) return;
    if (!groupOrderLines(g).length) return toast("Nothing on this list yet", "error");
    const ok = await copyText(orderTextFor(g, S.data));
    toast(ok ? "Copied" : "Could not copy on this browser", ok ? "ok" : "error");
  }

  function logOrder(id, channel, g, extra = {}) {
    const d = S.data;
    return API.send(`/api/supply/order?${locQ()}`, "POST", {
      supplier_id: id, channel, lines: groupOrderLines(g), window_start: d.start, window_end: d.end, ...extra,
    });
  }

  // After an order goes out: its typed numbers and added lines are done with,
  // and the list is read again so the lines show what is on order.
  function afterOrder(g) {
    const id = g.supplier ? g.supplier.id : "";
    g.lines.forEach((line) => { delete S.order.edits[lineKey(line)]; });
    S.order.extras = S.order.extras.filter((row) => (row.supplier_id || "") !== id);
    saveExtras();
    S.order.log = null;
    return loadView(true);
  }

  async function supplyEmail(id) {
    const loc = S.locationId;
    const g = findGroup(id);
    if (!g || !g.supplier) return;
    const s = g.supplier;
    if (!s.order_email) return openSupplierForm(s, "Needed to email the order");
    if (!groupOrderLines(g).length) return toast("Nothing on this order yet", "error");
    if (!S.data.mail_provider || S.data.mail_provider === "outbox") {
      // No mail service on this account: the order opens in the person's own
      // mail app, and nothing is recorded until they say it went.
      const subject = `Order from ${locationName()}, ${dShort(S.data.start)} to ${dShort(S.data.end)}`;
      const body = orderTextFor(g, S.data);
      window.location.href = `mailto:${encodeURIComponent(s.order_email)}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
      return openOrderSheet(id, "mail");
    }
    try {
      const r = await logOrder(id, "email", g);
      if (S.locationId !== loc || S.view !== "ordering") return;
      const status = r.order && r.order.status;
      if (status !== "sent") {
        S.order.log = null;
        return toast(r.message || "The order was not sent. Try again.", "error", true);
      }
      toast("Sent");
      return afterOrder(g);
    } catch (error) { return toast(error.message, "error"); }
  }

  async function supplyPlaced(id, channel) {
    const loc = S.locationId;
    const g = findGroup(id);
    if (!g || !g.supplier) return;
    try {
      const r = await logOrder(id, channel, g, channel === "mail-app" ? { confirmed: true } : {});
      if (S.locationId !== loc || S.view !== "ordering") return;
      closeLayer();
      const lands = r.order && r.order.expected_on ? whenLabel(r.order.expected_on) : "";
      toast(lands ? `Noted, lands ${lands}` : "Noted");
      return afterOrder(g);
    } catch (error) { return toast(error.message, "error"); }
  }

  // The list to type in while the supplier's site or the mail app is open in
  // another window, and the one button that records the order went.
  function openOrderSheet(id, mode) {
    const g = findGroup(id);
    const s = g && g.supplier;
    if (!s) return;
    const lines = groupOrderLines(g);
    const intro = mode === "mail"
      ? "Your mail app should open with the order written out. Send it there, then come back."
      : `${hostOf(s.website)} is open in the other tab. Type this in, then come back.`;
    openLayer(`<div class="scrim" data-do="close-layer"></div>
      <aside class="sheet" role="dialog" aria-modal="true" aria-label="Order for ${e(s.name)}">
        <div class="sheet-head">
          <div><h2>${e(s.name)}</h2><p>${e(intro)}</p></div>
          <div style="margin-left:auto"><button class="icon-btn" data-do="close-layer" aria-label="Close">${icon("close")}</button></div>
        </div>
        <div class="sheet-body">
          <section class="card">
            ${s.notes ? `<div class="card-head"><div><p>${e(s.notes)}</p></div></div>` : ""}
            <div class="site-list">
              ${lines.map((row) => `<div class="site-line"><b>${e(row.name)}</b>
                <span>${fmtQty(row.quantity)} ${e(plural(row.quantity, row.unit))}${row.pack_size ? `, ${fmtQty(row.pack_size)} ${e(plural(row.pack_size, row.pack_unit))} each` : ""}${row.product_code ? ` · code ${e(row.product_code)}` : ""}</span></div>`).join("")}
            </div>
            <div class="card-body supply-actions"><div class="btn-row">
              <button class="btn accent" data-supply="${mode === "mail" ? "order-sent" : "order-placed"}" data-id="${e(id)}">${mode === "mail" ? "I sent this order" : "I placed this order"}</button>
              <button class="btn ghost" data-supply="order-copy" data-id="${e(id)}">Copy</button>
            </div></div>
          </section>
        </div>
      </aside>`);
  }

  // Tap a name: who sells it, how it is bought, the code on their order guide.
  function openItemSettings(key) {
    const line = ((S.data && S.data.lines) || []).find((row) => lineKey(row) === key);
    if (!line) return;
    const suppliers = (S.data && S.data.suppliers) || [];
    const drivers = (line.driven_by || []).map((x) => `${x.item} ${x.share_percent}%`).join(", ");
    openLayer(`<div class="scrim" data-do="close-layer"></div>
      <div class="modal-wrap"><div class="modal" role="dialog" aria-modal="true" aria-label="${e(line.name)}">
        <button class="modal-close" data-do="close-layer" aria-label="Close">${icon("close")}</button>
        <div class="modal-head"><h2>${e(line.name)}</h2>
          <p>About ${fmtQty(line.per_day)} ${e(plural(line.per_day, line.unit))} a day${drivers ? `, from ${e(drivers)}` : ""}.</p></div>
        <form id="f-supply-item" class="modal-body">
          <input type="hidden" name="ingredient" value="${e(key)}">
          <label class="field"><span>Who you buy it from</span>
            <select name="supplier_id" ${suppliers.length ? "autofocus" : ""}>
              <option value="">Not set</option>
              ${suppliers.map((s) => `<option value="${e(s.id)}" ${s.id === line.supplier_id ? "selected" : ""}>${e(s.name)}</option>`).join("")}
            </select>
            ${suppliers.length ? "" : `<small>No suppliers yet. Add one from the Order page and it appears here.</small>`}</label>
          <div class="field"><span class="field-label">How it is bought</span>
            <div class="packrow">
              <label class="field"><span>Units in a pack</span>
                <input name="pack_size" type="number" inputmode="decimal" min="0" step="any" value="${line.pack_size || ""}" placeholder="how many"></label>
              <label class="field"><span>Unit</span>
                <input name="pack_unit" value="${e(line.pack_size ? line.pack_unit : line.unit)}" placeholder="${e(line.unit)}"></label>
              <label class="field"><span>Pack</span>
                <select name="pack_label">${PACK_LABELS.map((p) => `<option ${p === (line.pack_label || "case") ? "selected" : ""}>${p}</option>`).join("")}</select></label>
            </div>
            <small>Leave the amount empty to keep ordering in ${e(plural(2, line.unit))}.</small></div>
          <label class="field"><span>Code on their order guide</span>
            <input name="product_code" value="${e(line.product_code || "")}" placeholder="Optional"></label>
          <div class="modal-foot" style="margin:6px -22px -20px">
            <button class="btn ghost" type="button" data-do="close-layer">Cancel</button>
            <button class="btn accent" type="submit">Save</button>
          </div>
        </form>
      </div></div>`);
  }

  // These are website shortcuts, not direct order connections. A supplier's
  // account, prices and delivery schedule still belong to that supplier.
  const SUPPLIER_SITES = [
    { id: "sysco", name: "Sysco", site: "Sysco Shop", website: "https://shop.sysco.com/" },
    { id: "usfoods", name: "US Foods", site: "MOXe", website: "https://www.usfoods.com/how-we-help-you/easy-ordering" },
    { id: "performance", name: "Performance Foodservice", site: "CustomerFirst and regional sites", website: "https://www.performancefoodservice.com/Company/Sign-In" },
  ];

  function openSupplierPicker() {
    openLayer(`<div class="scrim" data-do="close-layer"></div>
      <div class="modal-wrap"><div class="modal" role="dialog" aria-modal="true" aria-label="Add supplier">
        <button class="modal-close" data-do="close-layer" aria-label="Close">${icon("close")}</button>
        <div class="modal-head"><h2>Add supplier</h2><p>Choose where you buy. Orders are placed on the supplier's website or by email.</p></div>
        <div class="modal-body">
          <div class="supplier-choices">${SUPPLIER_SITES.map((s) => `<button class="supplier-choice" type="button" data-supply="supplier-preset" data-id="${s.id}">
            <span><b>${e(s.name)}</b><small>${e(s.site)}</small></span>${icon("chevR")}</button>`).join("")}</div>
          <button class="btn" type="button" data-supply="supplier-custom">My supplier isn't listed</button>
          <p class="supplier-help">Use any supplier by adding their details. You can also ask for help arranging a connection.</p>
        </div>
      </div></div>`);
  }

  function openSupplierForm(existing, emailNote = "", preset = null) {
    const s = existing || { delivery_days: [], lead_days: 1, ...(preset || {}) };
    const days = new Set(s.delivery_days || []);
    openLayer(`<div class="scrim" data-do="close-layer"></div>
      <div class="modal-wrap"><div class="modal" role="dialog" aria-modal="true" aria-label="${existing ? e(s.name) : "New supplier"}">
        <button class="modal-close" data-do="close-layer" aria-label="Close">${icon("close")}</button>
        <div class="modal-head"><h2>${existing ? e(s.name) : "Add supplier"}</h2>
          <p>Save the details you use to order. Confirm delivery days and cutoff with your supplier.</p></div>
        <form id="f-supply-supplier" class="modal-body" novalidate>
          <input type="hidden" name="sid" value="${e(s.id || "")}">
          <input type="hidden" name="delivery_days" value="${e((s.delivery_days || []).join(","))}">
          <div class="form-grid two">
            <label class="field"><span>Name</span><input name="name" required minlength="2" value="${e(s.name || "")}" placeholder="Sysco" ${existing ? "" : "autofocus"}></label>
            <label class="field"><span>Your rep</span><input name="rep_name" value="${e(s.rep_name || "")}" placeholder="Optional"></label>
          </div>
          <div class="form-grid two">
            <label class="field"><span>Order email</span><input name="order_email" type="email" inputmode="email" autocomplete="off" value="${e(s.order_email || "")}" placeholder="orders@example.com" ${emailNote ? "autofocus" : ""}>
              <small class="field-err" ${emailNote ? "" : "hidden"}>${e(emailNote)}</small></label>
            <label class="field"><span>Phone</span><input name="phone" type="tel" inputmode="tel" value="${e(s.phone || "")}" placeholder="Optional"></label>
          </div>
          <div class="form-grid two">
            <label class="field"><span>Ordering site</span><input name="website" type="url" inputmode="url" autocomplete="off" value="${e(s.website || "")}" placeholder="https://shop.example.com">
              <small class="field-err" hidden></small></label>
            <label class="field"><span>Account number</span><input name="account_number" value="${e(s.account_number || "")}" placeholder="Optional"></label>
          </div>
          <div class="field"><span class="field-label">Delivery days</span>
            <div class="chipset">${WEEKDAY_CHOICES.map(([k, l]) =>
              `<button type="button" class="chip ${days.has(k) ? "on" : ""}" data-supply-day="${k}">${l}</button>`).join("")}</div>
            <small>Leave them all off if they deliver any day.</small></div>
          <div class="form-grid two">
            <label class="field"><span>Order in by</span><input name="cutoff_time" type="time" value="${e(s.cutoff_time || "")}">
              <small>Their cutoff. Empty if there is none.</small></label>
            <label class="field"><span>Arrives</span>
              <select name="lead_days">${LEAD_CHOICES.map(([v, l]) => `<option value="${v}" ${Number(s.lead_days) === v ? "selected" : ""}>${l}</option>`).join("")}</select>
              <small>After the order goes in.</small></label>
          </div>
          <label class="field"><span>Notes</span><textarea name="notes" rows="2" placeholder="Minimum order, who to call when the truck is late">${e(s.notes || "")}</textarea></label>
          <details class="supplier-help"><summary>Need help connecting this supplier?</summary>
            <p>Send the supplier's name and website to ask about a connection. You can keep ordering by website, phone or email.</p>
            <button class="btn" type="button" data-supply="supplier-help">Email a connection request</button>
            <small>Your email app opens with a draft. Send it there to contact support.</small>
          </details>
          <div class="modal-foot" style="margin:6px -22px -20px">
            ${existing ? `<button class="btn ghost" type="button" data-supply="supplier-remove" data-id="${e(s.id)}">Remove</button>` : ""}
            <span style="flex:1"></span>
            <button class="btn ghost" type="button" data-do="close-layer">Cancel</button>
            <button class="btn accent" type="submit">Save</button>
          </div>
        </form>
      </div></div>`);
  }

  function emailSupplierRequest(target) {
    const form = target.closest("form");
    const data = form ? Object.fromEntries(new FormData(form).entries()) : {};
    const name = String(data.name || "").trim();
    if (!name) {
      form?.querySelector('[name="name"]')?.focus();
      return toast("Enter the supplier's name", "error");
    }
    const support = S.boot?.support_email || "support@quantify.app";
    const subject = `Supplier connection request: ${name}`;
    const body = [
      `Please help me arrange a supplier connection for ${locationName()}.`, "",
      `Supplier: ${name}`, `Ordering website: ${data.website || "Not known"}`,
      `Supplier representative: ${data.rep_name || "Not known"}`,
      `Supplier email: ${data.order_email || "Not known"}`, `Phone: ${data.phone || "Not known"}`,
      "", `Contact: ${S.boot?.user?.name || ""}`, `Reply to: ${S.boot?.user?.email || ""}`,
    ].join(NEWLINE);
    window.location.href = `mailto:${encodeURIComponent(support)}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
  }

  // Removing a supplier is its own question, with what it does said plainly.
  function openRemoveSupplier(id) {
    const s = ((S.data && S.data.suppliers) || []).find((row) => row.id === id);
    if (!s) return;
    const n = ((S.data && S.data.lines) || []).filter((row) => row.supplier_id === id).length;
    openLayer(`<div class="scrim" data-do="close-layer"></div>
      <div class="modal-wrap"><div class="modal" role="dialog" aria-modal="true" aria-label="Remove ${e(s.name)}">
        <button class="modal-close" data-do="close-layer" aria-label="Close">${icon("close")}</button>
        <div class="modal-head"><h2>Remove ${e(s.name)}?</h2>
          <p>${n ? `This takes ${e(s.name)} off ${noun(n, "line")}. ` : ""}Orders you sent stay in the log.</p></div>
        <div class="modal-body">
          <div class="modal-foot" style="margin:6px -22px -20px">
            <button class="btn ghost" type="button" data-supply="supplier-edit" data-id="${e(id)}">Keep it</button>
            <button class="btn danger" type="button" data-supply="supplier-remove-yes" data-id="${e(id)}">Remove ${e(s.name)}</button>
          </div>
        </div>
      </div></div>`);
  }

  function openOrderAdd() {
    const suppliers = (S.data && S.data.suppliers) || [];
    openLayer(`<div class="scrim" data-do="close-layer"></div>
      <div class="modal-wrap"><div class="modal" role="dialog" aria-modal="true" aria-label="Add something else">
        <button class="modal-close" data-do="close-layer" aria-label="Close">${icon("close")}</button>
        <div class="modal-head"><h2>Add something else</h2>
          <p>Foil, gloves, fryer oil, anything the recipes do not cover.</p></div>
        <form id="f-supply-extra" class="modal-body">
          <label class="field"><span>What</span><input name="name" required minlength="2" placeholder="Fryer oil" autofocus></label>
          <label class="field"><span>How much</span><input name="qty" required value="1" placeholder="2 cases"></label>
          ${suppliers.length ? `<label class="field"><span>From</span>
            <select name="supplier_id"><option value="">Not set</option>
              ${suppliers.map((s) => `<option value="${e(s.id)}">${e(s.name)}</option>`).join("")}</select></label>` : ""}
          <div class="modal-foot" style="margin:6px -22px -20px">
            <button class="btn ghost" type="button" data-do="close-layer">Cancel</button>
            <button class="btn accent" type="submit">Add it</button>
          </div>
        </form>
      </div></div>`);
  }

  // Suppliers live on the Order page. This stays only until the Settings tab
  // that called it is gone.
  function settingsSuppliers() {
    return `<section class="card">${emptyState("Suppliers are on the Order page", "Add, edit and order from each supplier there.",
      `<button class="btn accent" data-view="ordering">Open the order</button>`)}</section>`;
  }

  // The log of orders that went out, read when the fold is opened.
  function ordersSentBody() {
    const log = S.order.log;
    if (!log || log.for !== S.locationId) return `<p class="small muted">Loading</p>`;
    if (!log.rows.length) return `<p class="small muted">Nothing sent yet. Orders you email or place show here.</p>`;
    return `<div class="srows">${log.rows.map(orderLogRow).join("")}</div>`;
  }

  async function loadOrderLog(force = false) {
    const loc = S.locationId;
    if (!force && S.order.log && S.order.log.for === loc) return;
    if (S.order.logFor === loc && !force) return;
    S.order.logFor = loc;
    try {
      const r = await API.get(`/api/supply/orders?${locQ()}`);
      S.order.log = { for: loc, rows: r.orders || [] };
    } catch (_) {
      S.order.log = { for: loc, rows: [] };
    } finally {
      S.order.logFor = "";
    }
    const host = document.getElementById("orders-sent-body");
    if (host && S.locationId === loc) host.innerHTML = ordersSentBody();
  }

  function orderLogRow(o) {
    const span = o.window_start && o.window_end ? `for ${dShort(o.window_start)} to ${dShort(o.window_end)}` : "";
    return `<div class="srow clickable" data-supply="order-view" data-id="${e(o.id)}" role="button" tabindex="0">
      <div><b>${e(o.supplier_name || "No supplier")}</b><small>${noun(o.line_count, "line")} · ${e(CHANNEL_LABELS[o.status] || cap(String(o.status || "").replace(/-/g, " ")))}${o.sent_by ? ` · ${e(o.sent_by)}` : ""}</small></div>
      <div><span>${e(cap(whenLabel(o.sent_at)))}</span>${o.expected_on ? `<small>lands ${e(whenLabel(o.expected_on))}</small>` : ""}</div>
      <div class="small muted">${e(span)}</div>
    </div>`;
  }

  // One past order, written out, with a Copy so it can go again.
  function openOrderView(id) {
    const o = (((S.order.log || {}).rows) || []).find((row) => row.id === id);
    if (!o) return;
    const head = `Order for ${locationName()}${o.supplier_name ? `, ${o.supplier_name}` : ""}${o.window_start && o.window_end ? `, ${dMed(o.window_start)} to ${dMed(o.window_end)}` : ""}`;
    const text = `${head}${NEWLINE}${NEWLINE}${linesText(o.lines || [])}`;
    openLayer(`<div class="scrim" data-do="close-layer"></div>
      <aside class="sheet" role="dialog" aria-modal="true" aria-label="${e(head)}">
        <div class="sheet-head">
          <div><h2>${e(o.supplier_name || "No supplier")}</h2>
            <p>${e(cap(whenLabel(o.sent_at)))}${o.sent_by ? `, ${e(o.sent_by)}` : ""} · ${e(CHANNEL_LABELS[o.status] || cap(String(o.status || "")))}${o.expected_on ? ` · lands ${e(whenLabel(o.expected_on))}` : ""}</p></div>
          <div style="margin-left:auto"><button class="icon-btn" data-do="close-layer" aria-label="Close">${icon("close")}</button></div>
        </div>
        <div class="sheet-body">
          <section class="card">
            <div class="site-list">
              ${(o.lines || []).map((row) => `<div class="site-line"><b>${e(row.name)}</b>
                <span>${fmtQty(row.quantity)} ${e(plural(row.quantity, row.unit))}${row.product_code ? ` · code ${e(row.product_code)}` : ""}</span></div>`).join("")}
            </div>
            <div class="card-body supply-actions"><div class="btn-row">
              <button class="btn" data-do="copy" data-copy="${e(text)}">Copy</button>
            </div></div>
          </section>
        </div>
      </aside>`);
  }

  // A count is saved the moment it changes, and only that row is redrawn from
  // the answer. Nothing else on the page moves under the person's hands.
  let countChain = Promise.resolve();
  function saveCount(input) {
    const raw = input.value.trim();
    const cell = input.closest(".s-count");
    const err = cell && cell.querySelector(".count-err");
    const say = (message) => {
      if (err) { err.textContent = message; err.hidden = !message; }
      input.classList.toggle("bad", !!message);
    };
    if (input.validity && input.validity.badInput) return say("Enter a number, like 12 or 2.5");
    if (raw !== "") {
      const n = Number(raw.replace(/,/g, ""));
      if (!Number.isFinite(n)) return say("Enter a number, like 12 or 2.5");
      if (n < 0) return say("A count cannot be below zero");
    }
    say("");
    const key = input.dataset.supplyCount;
    const loc = S.locationId;
    const query = `location_id=${encodeURIComponent(loc)}`;
    const start = S.date;
    const days = S.order.days;
    const body = { ingredient: key, on_hand: raw, unit: input.dataset.unit, kind: input.dataset.kind, start, days };
    countChain = countChain.then(async () => {
      try {
        const r = await API.send(`/api/supply/count?${query}`, "POST", body);
        if (S.view !== "ordering" || S.locationId !== loc) return;
        const saved = (r && r.saved && r.saved[0]) || r || {};
        let line = saved.line && saved.line.suggested ? saved.line : (saved.suggested ? saved : null);
        if (!line) {
          // Until the count route answers with the line, the list is read
          // again and just this row is taken from it.
          const plan = await API.get(`/api/ordering?${query}&start=${start}&days=${days}`);
          if (S.view !== "ordering" || S.locationId !== loc) return;
          line = (plan.lines || []).find((row) => lineKey(row) === key) || null;
          if (S.data && plan.ready) { S.data.counts_taken = plan.counts_taken; S.data.last_counted_at = plan.last_counted_at; }
        }
        if (line) patchLine(line);
      } catch (error) {
        say(plainError(error));
      }
    });
    return undefined;
  }

  function patchLine(line) {
    const key = lineKey(line);
    const lines = (S.data && S.data.lines) || [];
    const i = lines.findIndex((row) => lineKey(row) === key);
    if (i < 0) return;
    lines[i] = line;
    updateSupplierActions(line);
    remember();
    const row = root.querySelector(`[data-line-row="${CSS.escape(key)}"]`);
    if (!row) return;
    const runs = row.querySelector(".s-runs");
    if (runs) runs.innerHTML = runsOutCell(line);
    const sub = row.querySelector(".count-sub");
    if (sub) sub.innerHTML = countSub(line);
    const box = row.querySelector("[data-supply-count]");
    if (box && document.activeElement !== box) {
      box.value = counted(line) ? fmtQty(line.pack_size ? line.on_hand_packs : line.on_hand).replace(/,/g, "") : "";
    }
    if (S.order.edits[key] === undefined) {
      const qty = orderQty(line);
      const qin = row.querySelector("[data-oqty]");
      if (qin && document.activeElement !== qin) qin.value = String(qty);
      const unit = row.querySelector(".qunit");
      if (unit) unit.innerHTML = orderUnit(line, qty);
    }
  }

  const validEmail = (text) => /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(String(text || "").trim());
  // "shop.example.com" or a full address; anything without a real host is
  // refused before it can become a button that opens nothing.
  function cleanSite(text) {
    const t = String(text || "").replace(/\s+/g, "");
    if (!t) return "";
    const url = /^https?:\/\//i.test(t) ? t : `https://${t}`;
    try {
      const u = new URL(url);
      return /^[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$/i.test(u.hostname) ? u.href : null;
    } catch (_) { return null; }
  }
  function fieldError(form, name, message) {
    const input = form.querySelector(`[name=${name}]`);
    const note = input && input.parentElement.querySelector(".field-err");
    if (note) { note.textContent = message; note.hidden = !message; }
    if (input && message) input.focus();
  }

  async function supplySubmit(form) {
    const data = Object.fromEntries(new FormData(form).entries());
    const button = form.querySelector("button[type=submit]");
    const formId = form.getAttribute("id");
    if (formId === "f-supply-supplier") {
      if (String(data.name || "").trim().length < 2) return toast("Give the supplier a name", "error");
      fieldError(form, "order_email", "");
      fieldError(form, "website", "");
      if (String(data.order_email || "").trim() && !validEmail(data.order_email)) {
        return fieldError(form, "order_email", "That does not look like an email address");
      }
      const site = cleanSite(data.website);
      if (site === null) return fieldError(form, "website", "The ordering site should look like shop.example.com");
      data.website = site;
    }
    if (form.dataset.saving === "true") return;
    form.dataset.saving = "true";
    if (button) button.disabled = true;
    try {
      if (formId === "f-supply-supplier") {
        data.id = data.sid || "";
        delete data.sid;
        data.delivery_days = String(data.delivery_days || "").split(",").filter(Boolean);
        data.lead_days = Number(data.lead_days);
        const saved = await API.send(`/api/supply/supplier?${locQ()}`, "POST", data);
        closeLayer();
        toast("Saved");
        S.supply = null;
        return loadView(true);
      }
      if (formId === "f-supply-item") {
        data.pack_size = Number(data.pack_size) || 0;
        await API.send(`/api/supply/item?${locQ()}`, "POST", data);
        closeLayer();
        toast("Saved");
        return loadView(true);
      }
      if (formId === "f-supply-extra") {
        S.order.extras.push({ name: String(data.name).trim(), qty: String(data.qty || "1").trim(), supplier_id: data.supplier_id || "" });
        saveExtras();
        closeLayer();
        return render(true);
      }
    } catch (error) {
      toast(error.message, "error");
    } finally {
      delete form.dataset.saving;
      if (button) button.disabled = false;
    }
    return undefined;
  }

  const pendingSupplierOrders = new Set();
  async function supplyAction(target) {
    const kind = target.dataset.supply;
    const id = target.dataset.id || "";
    if (target.disabled) return undefined;
    const menu = target.closest("details.overflow");
    if (menu) menu.removeAttribute("open");
    if (kind === "supplier-new") return openSupplierPicker();
    if (kind === "supplier-custom") return openSupplierForm(null);
    if (kind === "supplier-preset") {
      const preset = SUPPLIER_SITES.find((s) => s.id === id);
      return preset ? openSupplierForm(null, "", { name: preset.name, website: preset.website }) : undefined;
    }
    if (kind === "supplier-help") return emailSupplierRequest(target);
    if (kind === "supplier-edit") {
      const s = ((S.data && S.data.suppliers) || []).find((row) => row.id === id);
      return s ? openSupplierForm(s) : toast("That supplier is not on this location. Refresh and try again.", "error");
    }
    if (kind === "supplier-remove") return openRemoveSupplier(id);
    if (kind === "supplier-remove-yes") {
      target.disabled = true;
      try {
        await API.send(`/api/supply/supplier?${locQ()}`, "DELETE", { id });
        closeLayer();
        toast("Removed");
        S.supply = null;
        return loadView(true);
      } catch (error) { target.disabled = false; return toast(error.message, "error"); }
    }
    if (kind === "item") return openItemSettings(target.dataset.key);
    if (kind === "order-copy") return supplyCopy(id);
    if (["order-email", "order-placed", "order-sent"].includes(kind)) {
      const key = `${S.locationId}:${id}`;
      if (pendingSupplierOrders.has(key)) return;
      pendingSupplierOrders.add(key);
      target.disabled = true;
      target.dataset.busy = "true";
      try {
        if (kind === "order-email") return await supplyEmail(id);
        return await supplyPlaced(id, kind === "order-sent" ? "mail-app" : "site");
      } finally {
        pendingSupplierOrders.delete(key);
        delete target.dataset.busy;
        target.disabled = false;
      }
    }
    if (kind === "order-site") return openOrderSheet(id, "site");
    if (kind === "order-view") return openOrderView(id);
    if (kind === "order-add") return openOrderAdd();
    if (kind === "order-reset") { S.order.edits = {}; return render(true); }
    if (kind === "order-drop") {
      S.order.extras.splice(Number(target.dataset.index), 1);
      saveExtras();
      return render(true);
    }
    return undefined;
  }

  document.addEventListener("click", (event) => {
    const day = event.target.closest("[data-supply-day]");
    if (day) {
      day.classList.toggle("on");
      const hidden = day.closest("form")?.querySelector("[name=delivery_days]");
      if (hidden) hidden.value = Array.from(day.parentElement.querySelectorAll(".chip.on")).map((n) => n.dataset.supplyDay).join(",");
      return;
    }
    const step = event.target.closest("[data-oadj]");
    if (step) return orderAdjust(step.dataset.oadj, Number(step.dataset.step));
    const target = event.target.closest("[data-supply]");
    if (target) {
      if (target.getAttribute("aria-disabled") === "true") return event.preventDefault();
      supplyAction(target);
    }
  });

  // The overflow menu closes when the tap lands anywhere else.
  document.addEventListener("pointerdown", (event) => {
    document.querySelectorAll("details.overflow[open]").forEach((node) => {
      if (!node.contains(event.target)) node.removeAttribute("open");
    });
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const row = event.target.closest && event.target.closest("[data-supply='order-view']");
    if (row && event.target === row) { event.preventDefault(); openOrderView(row.dataset.id); }
  });

  document.addEventListener("change", (event) => {
    const count = event.target.closest("[data-supply-count]");
    if (count) return saveCount(count);
    const box = event.target.closest("[data-oqty]");
    if (box) {
      const line = ((S.data && S.data.lines) || []).find((row) => lineKey(row) === box.dataset.oqty);
      const value = Number(box.value);
      if (line) setOrderQty(line, Number.isFinite(value) && value >= 0 ? Math.round(value * 10) / 10 : 0);
      return undefined;
    }
    return undefined;
  });

  // Which folds are open survives a repaint. toggle does not bubble, so it is
  // caught on the way down.
  document.addEventListener("toggle", (event) => {
    const node = event.target;
    if (!node || node.tagName !== "DETAILS") return;
    S.order.folds = S.order.folds || {};
    if (node.dataset.fold !== undefined) S.order.folds[node.dataset.fold] = node.open;
    if (node.id === "orders-sent") { S.order.logOpen = node.open; if (node.open) loadOrderLog(); }
  }, true);

  document.addEventListener("submit", (event) => {
    const form = event.target;
    const formId = form.getAttribute ? (form.getAttribute("id") || "") : "";
    if (!formId.startsWith("f-supply-")) return;
    event.preventDefault();
    supplySubmit(form);
  });

  /* ---------- settings ---------- */
  // Four tabs. The morning email and the connections live inside Location,
  // suppliers live on Order. Which tab is open is kept in the store.
  const SETTINGS_TABS = [
    ["location", "Location"],
    ["menu", "Menu"],
    ["costs", "Costs"],
    ["account", "Account"],
  ];

  function renderSettings() {
    const body = `<div class="settings">
      <nav class="settings-nav" aria-label="Settings">${SETTINGS_TABS.map(([k, l]) =>
        `<button class="${S.settingsTab === k ? "on" : ""}" type="button" data-stab="${k}">${l}</button>`).join("")}</nav>
      <div class="settings-body">${settingsPanel()}</div>
    </div>`;
    root.innerHTML = shell("Settings", "", "", body);
  }

  function settingsPanel() {
    const { setup, billing } = S.data;
    if (S.settingsTab === "menu") return settingsMenu();
    if (S.settingsTab === "costs") return settingsCosts();
    if (S.settingsTab === "account") return settingsAccount(setup, billing);
    return settingsLocation(setup);
  }

  // A plain word for how a connection stands. Never green: connected is not
  // "more than normal", it is just connected.
  function connTag(row, connected) {
    if (connected) return `<span class="connection-state">Connected</span>`;
    if (row && row.mode === "demo") return `<span class="connection-state">Sample data</span>`;
    return `<span class="connection-state">Not connected</span>`;
  }

  function settingsLocation(setup) {
    const l = setup.location;
    const tz = setup.timezone || {};
    const p = setup.email || {};
    const mail = !!p.provider_connected;
    const byProvider = Object.fromEntries((setup.integrations || []).map((row) => [row.provider, row]));
    const reg = setup.register || {};
    const registerOn = reg.connected !== undefined
      ? !!reg.connected
      : !!(byProvider.pos && byProvider.pos.status === "connected" && byProvider.pos.mode !== "demo");
    const weather = byProvider.weather || {};
    const events = byProvider.events || {};
    const synced = (row) => (row && row.last_sync ? `<div class="why">Last updated ${e(dMed(row.last_sync))}</div>` : "");
    return `<form id="f-location" class="stack-tight" autocomplete="off">
      <section class="card">
        <div class="card-head"><div><h2>Location</h2><p>Name, hours and where it is. The morning email runs on this time zone.</p></div></div>
        <div class="card-body form-grid">
          <div class="form-grid two">
            <label class="field"><span>Name</span><input name="name" value="${e(l.name)}" required></label>
            <label class="field"><span>What you serve</span><input name="concept" value="${e(l.concept)}"></label>
          </div>
          <div class="form-grid two">
            <label class="field"><span>City</span>
              <div class="typeahead">
                <input type="text" name="city" data-typeahead value="${e(l.city)}"
                       placeholder="Start typing a town" autocomplete="off">
              </div></label>
            <label class="field"><span>State</span>
              <input type="text" name="region" value="${e(l.region)}" placeholder="NY"></label>
          </div>
          <label class="field"><span>Time zone</span>
            <div class="typeahead">
              <input type="text" name="timezone_text" id="tz-input" data-typeahead value="${e(tz.label || l.timezone)}"
                     placeholder="Eastern, Chicago, or a ZIP code" autocomplete="off">
            </div>
            <input type="hidden" name="timezone" value="${e(l.timezone)}">
            <div id="tz-hint" data-tz-hint></div>
            <small>A town, a state, a ZIP code, or a time zone.</small></label>
          <div class="form-grid two">
            <label class="field"><span>Opens</span><select name="open_hour">${hourOptions(l.open_hour, 0, 14)}</select></label>
            <label class="field"><span>Closes</span><select name="close_hour">${hourOptions(l.close_hour, 14, 28)}</select></label>
          </div>
          <p class="form-note">Past midnight is fine. A bar open 11 AM to 2 AM is a fifteen hour day.</p>
        </div>
      </section>

      <section class="card">
        <div class="card-head"><div><h2>Morning email</h2><p>What to make, why, and the six days ahead. Sent before you open.</p></div></div>
        <div class="card-body form-grid">
          <div class="form-grid two">
            <label class="field"><span>Send to</span>
              <input name="owner_email" type="email" value="${e(p.owner_email || "")}" placeholder="${e(S.boot.user.email || "")}"></label>
            <label class="field"><span>Send at</span>
              <input name="send_time" type="time" value="${e(p.send_time || "05:30")}">
              <small>${e(tz.label || l.timezone)}</small></label>
          </div>
          <label class="check"><input name="enabled" type="checkbox" ${p.enabled ? "checked" : ""}>
            <span><b>Send it every day</b><small>Goes out at the time above, on this location's clock.</small></span></label>
          <div class="btn-row">
            <button class="btn" type="button" data-do="preview-email">Preview</button>
            <button class="btn" type="button" data-do="send-test" ${mail ? "" : "disabled"}>Send a test</button>
          </div>
          ${mail ? "" : `<p class="form-note">Email is not connected on this account yet.</p>`}
        </div>
      </section>

      <section class="card">
        <div class="card-head"><div><h2>Where the numbers come from</h2></div></div>
        <div class="conn">
          <div><b>Register ${connTag(byProvider.pos, registerOn)}</b>
            ${synced(byProvider.pos)}</div>
          ${registerOn
            ? `<button class="btn sm" type="button" data-sync="pos">Sync now</button>`
            : `<button class="btn sm" type="button" data-settings="pos-connect">Connect Square</button>`}
        </div>
        <div class="conn">
          <div><b>Weather ${connTag(weather, weather.status === "connected" && weather.mode !== "demo")}</b>
            ${synced(weather)}</div>
          <button class="btn sm" type="button" data-sync="weather">Refresh</button>
        </div>
        <div class="conn">
          <div><b>Nearby ${connTag(events, events.status === "connected" && events.mode !== "demo")}</b>
            ${synced(events)}</div>
          <button class="btn sm" type="button" data-sync="events">Refresh</button>
        </div>
      </section>

      <div class="savebar"><button class="btn accent" type="submit">Save</button></div>
    </form>`;
  }

  function openSquareConnect() {
    openLayer(`<div class="scrim" data-do="close-layer"></div>
      <div class="modal-wrap"><div class="modal" role="dialog" aria-modal="true" aria-label="Connect Square">
        <button class="modal-close" data-do="close-layer" aria-label="Close">${icon("close")}</button>
        <div class="modal-head"><h2>Connect Square</h2>
          <p>Quantify reads your items and completed tickets from Square, up to three years back.</p></div>
        <form id="f-square" class="modal-body" autocomplete="off">
          <label class="field"><span>Access token</span>
            <input name="access_token" type="password" autocomplete="off" required></label>
          <label class="field"><span>Location ID</span>
            <input name="location_id" required></label>
          <p class="form-note">Both are on your Square application page. <a href="https://developer.squareup.com/apps" target="_blank" rel="noopener">Where to find these</a></p>
          <p class="form-error" id="square-error"></p>
          <div class="modal-foot" style="margin:6px -22px -20px">
            <button class="btn ghost" type="button" data-do="close-layer">Not now</button>
            <button class="btn accent" type="submit">Connect</button>
          </div>
        </form>
      </div></div>`);
  }

  const PERIODS = [["day", "every day"], ["week", "every week"], ["month", "every month"]];

  // A number field with its unit printed inside the box, so nobody has to
  // guess whether 18 is dollars, percent or people.
  function unitField(label, name, value, unit, attrs = "", note = "") {
    return `<label class="field"><span>${label}</span>
      <span class="unitwrap"><input name="${name}" type="number" inputmode="decimal" value="${e(value)}" ${attrs}>${unit ? `<i class="unit">${unit}</i>` : ""}</span>
      ${note ? `<small>${note}</small>` : ""}</label>`;
  }

  function settingsCosts() {
    const c = S.costs;
    if (!c) return `<section class="card"><div class="card-body">${skeleton()}</div></section>`;
    const s = c.settings;
    const w = c.wage;
    const ex = c.example;
    const wage = s.hourly_wage || "";
    const reference = w.reference || {};
    const payroll = w.payroll_reference || { components: [], sources: [], notes: [] };
    const known = (value) => value !== null && value !== undefined && Number.isFinite(Number(value));
    const referenceNote = known(reference.rate)
      ? `${money(reference.rate, true)} is the ${e(reference.place || "state")} reference for ${e(reference.reference_year)}${reference.effective_from ? `, effective ${e(reference.effective_from)}` : ""}. ${e(reference.scope)}`
      : e(reference.scope || "No verified wage reference for this location. Enter what you pay.");
    return `<form id="f-costs" class="stack-tight" autocomplete="off">
      <section class="card">
        <div class="card-head"><div><h2>What an hour of work costs</h2>
          <p>${e(w.detail)}</p></div></div>
        <div class="card-body form-grid">
          <div class="form-grid two">
            ${unitField("Average pay per hour", "hourly_wage", wage, "$ / hour", 'step="0.01" min="0"', "From your payroll, before employer taxes and insurance. Blank means unknown.")}
            ${unitField("Employer payroll costs on top", "payroll_load_percent", w.payroll_load_source === "owner" ? s.payroll_load_percent : "", "%", 'step="0.01" min="0" max="60"', "Employer taxes, insurance and benefits divided by gross wages, from the same payroll period. Blank means unknown.")}
          </div>
          <p class="field-note">${known(w.loaded) ? `With your figures, an hour costs about ${money(w.loaded, true)} including employer payroll costs.` : "Wages and money kept stay unknown until both pay fields are entered."}</p>
          <details class="cost-reference"><summary>Wage and payroll references</summary><div class="cost-reference-body">
            <p>${referenceNote} <a href="${e(reference.source_url || "https://www.dol.gov/agencies/whd/minimum-wage/state")}" target="_blank" rel="noopener">Wage source</a>.</p>
            ${(payroll.components || []).map((row) => `<p><b>${e(row.name)}: ${e(row.percent)}%.</b> ${e(row.detail)}</p>`).join("")}
            ${(payroll.notes || []).map((note) => `<p>${e(note)}</p>`).join("")}
            <p>${(payroll.sources || []).map((source) => `<a href="${e(source.url)}" target="_blank" rel="noopener">${e(source.label)}</a>`).join(" · ")}</p>
            <p>References checked ${e(reference.checked_on || "")}. They do not replace your payroll report or establish which rules apply to each worker.</p>
          </div></details>
          <div class="form-grid two">
            ${unitField("Tickets one person handles an hour", "orders_per_person_per_hour", s.orders_per_person_per_hour, "tickets", 'step="0.5" min="1" max="40"', "Planning assumption. Set this from your own shifts.")}
            ${unitField("Fewest people on at once", "min_staff", s.min_staff, "people", 'step="1" min="1" max="30"', "Counted for every open hour.")}
          </div>
          <div class="form-grid two">
            ${unitField("Prep before you open", "prep_hours", s.prep_hours, "hours", 'step="0.5" min="0" max="12"')}
            ${unitField("Clean down after you close", "close_hours", s.close_hours, "hours", 'step="0.5" min="0" max="12"')}
          </div>
        </div>
      </section>

      <section class="card">
        <div class="card-head"><div><h2>Food cost by category</h2>
          <p>Change any of these and the profit on every day follows.</p></div></div>
        <div class="card-body">
          <div class="form-grid two">
            ${unitField("Anything without its own figure", "default_cost_share", Math.round(s.default_cost_share * 100), "% of price", 'step="1" min="1" max="95"')}
          </div>
        </div>
        <table class="dt costs-table"><thead><tr>
          <th>Category</th><th class="num right">Share of price</th><th class="num right">On a typical item</th></tr></thead><tbody>
          ${c.categories.map((row) => `<tr>
            <td class="name"><b>${e(row.category)}</b><small>${row.set_by_owner ? "your figure" : "estimate"}, ${noun(row.items, "item")}</small></td>
            <td class="num right"><span class="unitwrap mini"><input class="mini" data-cost-category="${e(row.category)}" type="number" inputmode="numeric" step="1" min="1" max="95" value="${row.percent}" aria-label="Share of price for ${e(row.category)}"><i class="unit">%</i></span></td>
            <td class="num right">${money(row.average_price * row.percent / 100, true)}</td>
          </tr>`).join("")}
        </tbody></table>
      </section>

      <section class="card">
        <div class="card-head"><div><h2>Everything else you pay for</h2>
          <p>Rent, insurance, the card machine, the linen service.</p></div></div>
        <div class="card-body">
          <div id="cost-lines">${(c.recurring.length ? c.recurring : [{ name: "", amount: "", period: "month" }])
            .map(costLine).join("")}</div>
          <div class="btn-row" style="margin-top:12px">
            <button class="btn sm" type="button" data-do="add-cost-line">Add another</button>
          </div>
          ${c.recurring_daily ? `<p class="small muted" style="margin-top:12px">That comes to <b>${money(c.recurring_daily)}</b> a day, charged whether you trade or not.</p>` : ""}
        </div>
      </section>

      ${ex && ex.complete !== false ? `<section class="card">
        <div class="card-head"><div><h2>What this does to a real day</h2>
          <p>${e(dMed(ex.date))}, worked through with the numbers above.</p></div></div>
        <div class="card-body"><div class="ledger">
          <div><b>Sold</b></div><div class="num"><b>${money(ex.revenue)}</b></div>
          <div><b>Food and packaging</b><small>${ex.cogs_percent}% of sales</small></div><div class="num">${money(-ex.cogs)}</div>
          <div><b>Wages</b><small>${ex.staff_hours} staff hours at ${money(ex.loaded_wage, true)}, peak of ${noun(ex.busiest_staff, "person", "people")}</small></div><div class="num">${money(-ex.labour)}</div>
          ${ex.other ? `<div><b>Everything else</b><small>one day's share of what you listed</small></div><div class="num">${money(-ex.other)}</div>` : ""}
          <div class="total"><b>Kept</b><small>about ${ex.margin_percent}% of sales</small></div><div class="num total"><b>${money(ex.left_after_costs)}</b></div>
        </div></div>
      </section>` : ex ? `<section class="card"><div class="card-body"><p>Sales and food estimates are available for ${e(dMed(ex.date))}. Enter pay and employer payroll costs above before estimating wages or money kept.</p></div></section>` : ""}

      <div class="savebar"><span class="form-error" id="costs-error"></span><button class="btn accent" type="submit">Save costs</button></div>
    </form>`;
  }

  function costLine(row) {
    return `<div class="costline">
      <input class="cl-name" placeholder="What it is" value="${e(row.name || "")}" aria-label="What it is">
      <span class="unitwrap"><input class="cl-amount" type="number" inputmode="decimal" step="1" min="0" placeholder="Amount" value="${row.amount || ""}" aria-label="Amount"><i class="unit">$</i></span>
      <select class="cl-period" aria-label="How often">${PERIODS.map(([k, l]) =>
        `<option value="${k}" ${row.period === k ? "selected" : ""}>${l}</option>`).join("")}</select>
      <button class="icon-btn" type="button" data-do="drop-cost-line" aria-label="Remove this line">${icon("close")}</button>
    </div>`;
  }

  async function saveCosts() {
    const form = document.getElementById("f-costs");
    const body = form ? Object.fromEntries(new FormData(form).entries()) : {};
    body.categories = Array.from(document.querySelectorAll("[data-cost-category]")).map((node) => ({
      category: node.dataset.costCategory, percent: node.value === "" ? null : Number(node.value),
    }));
    body.recurring = Array.from(document.querySelectorAll(".costline")).map((node) => ({
      name: node.querySelector(".cl-name").value.trim(),
      amount: Number(node.querySelector(".cl-amount").value),
      period: node.querySelector(".cl-period").value,
    })).filter((row) => row.name);
    const errorNode = document.getElementById("costs-error");
    if (errorNode) errorNode.textContent = "";
    try {
      const saved = await API.send(`/api/costs?location_id=${encodeURIComponent(S.locationId)}`, "PUT", body);
      S.costs = saved;
      const skipped = Array.isArray(saved.skipped) ? saved.skipped : [];
      toast(skipped.length ? `Saved, skipped ${skipped.join(", ")}` : "Saved");
      render(true);
    } catch (error) {
      const text = plainError(error);
      if (errorNode) errorNode.textContent = text;
      else toast(text, "error");
    }
  }

  // What the plan card says about where the account stands, with the date.
  function planState(billing) {
    const status = billing.status;
    const trial = billing.trial_end ? dShort(billing.trial_end) : "";
    const period = billing.current_period_end ? dShort(billing.current_period_end) : "";
    if (status === "trial_ended") return `Your free two weeks ended ${trial || "already"}. Add a card to keep it running.`;
    if (status === "trialing") return `Free until ${trial}.${billing.payment_method ? "" : " Add a card before then to keep it running."}`;
    if (status === "past_due") return "The last payment did not go through. Update the card to keep it running.";
    if (status === "canceled") return "This plan has ended.";
    if (billing.cancel_at_period_end && period) return `Ends ${period}.`;
    if (period) return `Next charge ${period}.`;
    return "";
  }

  function settingsAccount(setup, billing) {
    const plan = billing.plan;
    const connected = !!(billing.provider && billing.provider.connected);
    const cancelling = !!billing.cancel_at_period_end;
    const mfa = !!setup.security.mfa_enabled;
    const codes = Number(setup.security.recovery_codes_remaining || 0);
    const state = planState(billing);
    return `<div class="stack-tight">
      <section class="card">
        <div class="card-head"><div><h2>Your account</h2></div></div>
        <div class="card-body" style="display:grid;gap:14px">
          <div id="account-name">
            <div class="eyebrow">Name</div>
            <div class="account-line"><b>${e(S.boot.user.name)}</b>
              <button class="btn sm ghost" type="button" data-settings="name-edit">Edit name</button></div>
          </div>
          <div><div class="eyebrow">Email</div><div class="account-line"><span>${e(S.boot.user.email)}</span></div></div>
          <div class="btn-row">
            <button class="btn" type="button" data-settings="password">Change password</button>
            <button class="btn ghost" type="button" data-do="signout">Sign out</button>
          </div>
        </div>
      </section>

      <section class="card">
        <div class="card-head"><div><h2>Two-step sign in</h2>
          <p>${mfa
            ? "On. Signing in asks for a code from your authenticator app as well as your password."
            : "Off. Signing in asks only for your password."}</p></div></div>
        <div class="card-body">
          <div class="btn-row">${mfa
            ? `<button class="btn" type="button" data-do="mfa-off">Turn it off</button>`
            : `<button class="btn accent" type="button" data-do="mfa-on">Turn on two-step sign in</button>`}</div>
          <p class="form-error" id="mfa-error"></p>
        </div>
      </section>

      ${mfa ? `<section class="card">
        <div class="card-head"><div><h2>Backup codes</h2>
          <p>Ten single-use codes that get you in if your phone is lost. ${codes ? `${num(codes)} unused.` : "None left."}</p></div></div>
        <div class="card-body"><div class="btn-row">
          <button class="btn" type="button" data-do="new-codes">${codes ? "Replace my codes" : "Create backup codes"}</button>
        </div></div>
      </section>` : ""}

      <section class="card">
        <div class="card-head"><div><h2>Plan</h2><p>${e(plan.blurb)}</p></div></div>
        <div class="plancard">
          <div>
            <div class="price">${money(plan.monthly)}<span> per location, per month</span></div>
            <div class="small muted" style="margin-top:5px">${money(plan.annual_monthly)} a month if you pay for the year.</div>
            ${connected && state ? `<p class="plan-state">${e(state)}</p>` : ""}
          </div>
          ${connected ? `<div class="plan-side">
            ${billing.payment_method
              ? `<div class="plan-cardbox">
                   <div class="eyebrow">Card on file</div>
                   <div style="margin-top:4px;font-weight:600">${e(billing.payment_method.brand || "Card")} ending ${e(billing.payment_method.last4)}</div>
                   <div class="small muted">Expires ${e(billing.payment_method.expires || "")}</div></div>`
              : `<div class="small muted">No card on file yet.</div>`}
            <button class="btn ${billing.payment_method ? "" : "accent"}" type="button" data-do="billing-portal">${billing.payment_method ? "Update card" : "Add a card"}</button>
            ${!billing.payment_method && !cancelling ? `<button class="btn" type="button" data-do="billing-checkout">Start the plan</button>` : ""}
          </div>` : ""}
        </div>
        ${billing.invoices && billing.invoices.length ? `<div style="border-top:1px solid var(--line)">
          ${billing.invoices.map((inv) => `<div class="invoice">
            <span class="muted">${e(dShort(inv.date) || inv.date)}</span><span>${e(inv.number || "Invoice")}</span>
            <span class="amt">${money(inv.amount, true)}</span>
            <span>${inv.url ? `<a class="btn sm" href="${e(inv.url)}" target="_blank" rel="noopener">Receipt ${icon("external")}</a>` : `<span class="tag plain">${e(inv.status)}</span>`}</span>
          </div>`).join("")}</div>` : ""}
        ${connected ? "" : `<div class="card-foot">Billing is not switched on for this account yet.</div>`}
      </section>

      ${!connected ? "" : cancelling ? `<section class="card">
        <div class="card-head"><div><h2>Your plan ends ${e(dMed(billing.current_period_end))}</h2>
          <p>Everything keeps working until then. Turn it back on before that and nothing is interrupted.</p></div></div>
        <div class="card-body"><div class="btn-row"><button class="btn accent" type="button" data-do="billing-resume">Keep plan</button></div></div>
      </section>` : `<section class="card">
        <div class="card-head"><div><h2>Cancel plan</h2>
          <p>Cancelling stops the next charge. You keep access until the end of the period you paid for.</p></div></div>
        <div class="card-body"><div class="btn-row"><button class="btn" type="button" data-do="cancel-start">Cancel plan</button></div></div>
      </section>`}

      <section class="card">
        <div class="card-head"><div><h2>Show me around</h2><p>A short walk through Today, Order, History and an item, using this location's own numbers.</p></div></div>
        <div class="card-body"><div class="btn-row"><button class="btn" type="button" data-do="tour-start">Start the tour</button></div></div>
      </section>
    </div>`;
  }

  function openPasswordChange() {
    openLayer(`<div class="scrim" data-do="close-layer"></div>
      <div class="modal-wrap"><div class="modal" role="dialog" aria-modal="true" aria-label="Change password">
        <button class="modal-close" data-do="close-layer" aria-label="Close">${icon("close")}</button>
        <div class="modal-head"><h2>Change password</h2>
          <p>Every other device is signed out once it changes.</p></div>
        <form id="f-password" class="modal-body">
          <label class="field"><span>Current password</span>
            <input name="current_password" type="password" autocomplete="current-password" required></label>
          <label class="field"><span>New password</span>
            <input name="new_password" type="password" autocomplete="new-password" minlength="12" required>
            <small>Twelve characters or more, with at least three of: capital letters, small letters, numbers, symbols.</small></label>
          <label class="field"><span>Repeat the new password</span>
            <input name="repeat_password" type="password" autocomplete="new-password" minlength="12" required></label>
          <p class="form-error" id="password-error"></p>
          <div class="modal-foot" style="margin:6px -22px -20px">
            <button class="btn ghost" type="button" data-do="close-layer">Cancel</button>
            <button class="btn accent" type="submit">Change password</button>
          </div>
        </form>
      </div></div>`);
  }

  /* ---------- cancellation flow ---------- */
  // Two clicks: the button on the plan card, then this one modal. The reason
  // is optional and nothing is booked or promised on the way out.
  async function cancelStart() {
    const billing = S.data?.billing || await API.get("/api/billing");
    S.cancelFlow = { stage: "ask", reason: "", detail: "", reasons: billing.cancellation_reasons || [], message: "" };
    renderCancelModal();
  }

  function renderCancelModal() {
    const f = S.cancelFlow;
    if (!f) return closeLayer();
    let body = "";
    let foot = "";
    if (f.stage === "done") {
      body = `<div class="modal-head"><h2>Your plan is cancelled</h2><p>${e(f.message)}</p></div>
        <div class="modal-body"><p class="small muted">Change your mind before then and the Account tab has a button that turns it straight back on.</p></div>`;
      foot = `<button class="btn primary" type="button" data-do="close-layer">Done</button>`;
    } else {
      body = `<div class="modal-head"><h2>Cancel your plan?</h2>
          <p>The next charge stops. Everything keeps working until the end of the period you paid for.</p></div>
        <div class="modal-body">
          ${f.reasons.length ? `<p class="small muted">If something went wrong, say which. Optional.</p>
          <div style="display:grid;gap:8px">
            ${f.reasons.map((r) => `
              <button type="button" class="choice ${f.reason === r.code ? "on" : ""}" data-cancel-reason="${e(r.code)}">
                <span class="radio"></span><div><b>${e(r.label)}</b></div></button>`).join("")}
          </div>` : ""}
          <label class="field"><span>Anything else</span>
            <textarea id="cancel-detail" rows="2" placeholder="Optional">${e(f.detail)}</textarea></label>
        </div>`;
      foot = `<button class="btn ghost" type="button" data-do="close-layer">Keep plan</button>
        <button class="btn danger" type="button" data-do="cancel-confirm">Cancel plan</button>`;
    }
    openLayer(`<div class="scrim" data-do="close-layer"></div>
      <div class="modal-wrap"><div class="modal" role="dialog" aria-modal="true" aria-label="Cancel your plan">
        <button class="modal-close" data-do="close-layer" aria-label="Close">${icon("close")}</button>
        ${body}<div class="modal-foot">${foot}</div>
      </div></div>`);
  }

  // Region-local actions for Settings: the small things that do not need a
  // place in the shared click switch.
  document.addEventListener("click", async (event) => {
    const target = event.target.closest("[data-settings]");
    if (!target) return;
    const action = target.dataset.settings;
    if (action === "pos-connect") return openSquareConnect();
    if (action === "password") return openPasswordChange();
    if (action === "name-edit") {
      const host = document.getElementById("account-name");
      if (!host) return;
      host.innerHTML = `<div class="eyebrow">Name</div>
        <form id="f-name" class="account-edit" autocomplete="off">
          <input name="display_name" value="${e(S.boot.user.name || "")}" minlength="2" maxlength="80" required aria-label="Name">
          <button class="btn sm" type="submit">Save name</button>
          <button class="btn sm ghost" type="button" data-settings="name-cancel">Cancel</button>
        </form>`;
      const field = host.querySelector("input");
      if (field) { field.focus(); field.select(); }
      return;
    }
    if (action === "name-cancel") return render(true);
    if (action === "recipe-add") {
      const host = document.getElementById("recipe-rows");
      if (!host) return;
      host.insertAdjacentHTML("beforeend", recipeRow({}));
      const last = host.lastElementChild && host.lastElementChild.querySelector(".rr-name");
      if (last) last.focus();
      return;
    }
    if (action === "recipe-drop") {
      const row = target.closest(".recipe-row");
      const host = document.getElementById("recipe-rows");
      if (row && host && host.children.length > 1) row.remove();
      else if (row) row.querySelectorAll("input").forEach((node) => { node.value = ""; });
      recipeTotalPaint();
      return;
    }
    if (action === "recompose-force") {
      closeLayer();
      return composeItem(target.dataset.item, true, true);
    }
  });

  document.addEventListener("input", (event) => {
    if (event.target.closest("#recipe-rows")) recipeTotalPaint();
  });

  // When a city is picked on the Location form, the time zone follows it.
  document.addEventListener("click", (event) => {
    const pick = event.target.closest("#f-location [data-place]");
    if (!pick) return;
    const holder = pick.closest(".typeahead");
    const field = holder && holder.querySelector("[data-typeahead]");
    if (!field || field.name !== "city") return;
    API.get(`/api/timezone?q=${encodeURIComponent(pick.dataset.place)}`).then((r) => {
      if (!r.match || !r.match.confident) return;
      const text = document.getElementById("tz-input");
      const hidden = document.querySelector("#f-location input[name=timezone]");
      if (text) text.value = r.match.label || r.match.timezone;
      if (hidden) hidden.value = r.match.timezone;
      const hint = document.getElementById("tz-hint");
      if (hint) hint.innerHTML = "";
    }).catch(() => {});
  });

  // The forms that belong to Settings modals and inline edits. The shared
  // submit handler already stops the page reload and re-enables the button.
  document.addEventListener("submit", async (event) => {
    const form = event.target;
    if (!["f-name", "f-password", "f-square"].includes(form.id)) return;
    event.preventDefault();
    if (form.dataset.saving) return;
    form.dataset.saving = "true";
    const button = form.querySelector("button[type=submit]");
    if (button) button.disabled = true;
    const data = Object.fromEntries(new FormData(form).entries());
    const say = (id, text) => { const node = document.getElementById(id); if (node) node.textContent = text; };
    try {
      if (form.id === "f-name") {
        const result = await API.send("/api/auth/profile", "POST", { display_name: data.display_name });
        if (S.boot && S.boot.user) S.boot.user.name = result.display_name || data.display_name;
        toast("Name saved");
        return render(true);
      }
      if (form.id === "f-password") {
        say("password-error", "");
        if (data.new_password !== data.repeat_password) return say("password-error", "The two new passwords do not match.");
        await API.send("/api/auth/password/change", "POST",
          { current_password: data.current_password, new_password: data.new_password });
        closeLayer();
        return toast("Password changed");
      }
      if (form.id === "f-square") {
        say("square-error", "");
        await API.send(`/api/integrations/pos/credentials?location_id=${encodeURIComponent(S.locationId)}`, "POST",
          { access_token: data.access_token, location_id: data.location_id, environment: "production" });
        closeLayer();
        toast("Square connected");
        S.data = null;
        return loadView(true);
      }
    } catch (error) {
      const text = plainError(error);
      if (form.id === "f-password") return say("password-error", text);
      if (form.id === "f-square") return say("square-error", text);
      toast(text, "error");
    } finally {
      delete form.dataset.saving;
      if (button) button.disabled = false;
    }
  });

  /* ---------- one item, in full ---------- */
  // Any item, however quiet. The point of the product is that the crème brûlée
  // nobody orders gets the same treatment as the best seller.
  async function openItemSheet(itemId) {
    layer.innerHTML = `<div class="scrim" data-do="close-layer"></div>
      <aside class="sheet wide"><div class="sheet-head"><div><h2>Reading the record</h2>
</div>
        <div style="margin-left:auto"><button class="icon-btn" data-do="close-layer">${icon("close")}</button></div></div>
      <div class="sheet-body">${skeleton()}</div></aside>`;
    try {
      const p = await API.get(`/api/item?location_id=${encodeURIComponent(S.locationId)}&item_id=${encodeURIComponent(itemId)}&date=${S.date}`);
      layer.innerHTML = `<div class="scrim" data-do="close-layer"></div>
        <aside class="sheet wide">
          <div class="sheet-head">
            <div><h2>${e(p.item.name)}</h2>
              <p>${e(p.item.category)} · ${money(p.item.price, true)} · ${e(p.weekday)} ${e(dShort(p.date))}</p></div>
            <div style="margin-left:auto;display:flex;gap:6px">
              <button class="btn sm" data-do="adjust" data-item="${e(p.item.id)}" data-name="${e(p.item.name)}" data-qty="${p.today.expected}">Set my own number</button>
              <button class="icon-btn" data-do="close-layer">${icon("close")}</button></div>
          </div>
          <div class="sheet-body">${itemSheetBody(p)}</div>
        </aside>`;
    } catch (error) {
      toast(error.message, "error");
      closeLayer();
    }
  }

  function itemSheetBody(p) {
    const t = p.today, prep = p.prep, dist = p.distribution, wp = p.weekday_profile;
    const established = (p.drivers || []).filter((d) => d.verdict === "established");
    const watching = (p.drivers || []).filter((d) => d.verdict === "watching");
    const rejected = (p.drivers || []).filter((d) => d.verdict === "rejected");

    return `<div class="stack">
      <section class="tiles" style="grid-template-columns:repeat(4,minmax(0,1fr))">
        ${tile("Make today", num(prep.quantity ?? t.expected),
          (prep.quantity ?? t.expected) > t.expected
            ? `The model expects <b>${num(t.expected)}</b> to sell. Make more than that, because running out costs more than throwing away.`
            : `The model expects <b>${num(t.expected)}</b> to sell.`,
          prep.fractile_percent ? `Covers ${prep.fractile_percent}% of the days that could happen` : "From the model")}
        ${tile("A normal " + p.weekday, num(t.normal),
          `<b>${t.difference >= 0 ? "+" : ""}${num(t.difference)}</b> against that today.`,
          `Read off ${num(dist.days || 0)} comparable days`)}
        ${tile("Honest range", `${num(dist.today_low ?? t.low)} to ${num(dist.today_high ?? t.high)}`,
          `On a day like this one, the middle half lands between <b>${num(dist.today_p25 ?? t.low)}</b> and <b>${num(dist.today_p75 ?? t.high)}</b>.`,
          `${e(dist.basis || "past days")} ran ${num(dist.lowest ?? 0)} to ${num(dist.highest ?? 0)}`)}
        ${tile("Worth today", money(t.revenue),
          `<b>${p.standing.revenue_share_percent}%</b> of takings over 90 days, ranked ${num(p.standing.revenue_rank)} of ${num(p.standing.of_items)}.`,
          `${num(p.standing.units_90_days)} sold in the last 90 days`)}
      </section>

      ${prep.levels ? `<section class="card">
        <div class="card-head"><div><h2>How many to make</h2>
          <p>${e(prep.reason)}</p></div></div>
        <div class="tablewrap"><table class="dt" style="min-width:600px"><thead><tr>
          <th>If you make</th><th class="num right">Chance you run out</th>
          <th class="num right">Typical left over</th><th class="num right">Typical missed sales</th>
          <th class="num right">Cost of being wrong</th></tr></thead><tbody>
          ${prep.levels.map((l) => `<tr class="${l.label === "This number" ? "highlight" : ""}">
            <td class="name"><b>${num(l.quantity)}</b>${l.label === "This number" ? `<small>what we suggest</small>` : `<small>${e(l.label.toLowerCase())}</small>`}</td>
            <td class="num right ${l.sell_out_percent > 40 ? "down" : ""}">${l.sell_out_percent}%</td>
            <td class="num right">${l.typical_leftover}</td>
            <td class="num right">${l.typical_missed}</td>
            <td class="num right">${money(l.cost_of_being_wrong, true)}</td></tr>`).join("")}
        </tbody></table></div>
        <div class="card-foot">Worked from ${num(dist.days || 0)} comparable days. Leftovers are costed at food cost, missed sales at lost margin.</div>
      </section>` : ""}

      <section class="card">
        <div class="card-head"><div><h2>Which days it belongs to</h2>
          <p>${wp.matters
            ? `The day of the week explains about ${wp.explains_percent}% of the swing in this item. ${wp.busiest_day}s run ${wp.spread_percent}% ahead of ${wp.quietest_day}s.`
            : "The day of the week does not explain this item's swing, which is unusual."}</p></div>
          <div class="spacer"></div>
          <span class="tag ${wp.matters ? "up" : "plain"}">${e(wp.strength)} evidence</span></div>
        <div class="tablewrap"><table class="dt" style="min-width:600px"><thead><tr>
          <th>Day</th><th class="num right">Typical</th><th class="num right">Middle half</th>
          <th class="num right">Quietest</th><th class="num right">Busiest</th>
          <th class="num right">Against all days</th><th class="num right">Days seen</th></tr></thead><tbody>
          ${wp.days.filter((d) => d.days).map((d) => `<tr class="${d.weekday === p.weekday ? "highlight" : ""}">
            <td class="name"><b>${e(d.weekday)}</b>${d.weekday === p.weekday ? `<small>today</small>` : ""}</td>
            <td class="num right plan">${d.typical}</td>
            <td class="num right muted">${d.low} to ${d.high}</td>
            <td class="num right muted">${d.quietest}</td>
            <td class="num right muted">${d.busiest}</td>
            <td class="num right ${d.vs_all_days_percent >= 0 ? "up" : "down"}">${pct(d.vs_all_days_percent)}</td>
            <td class="num right muted">${d.days}</td></tr>`).join("")}
        </tbody></table></div>
      </section>

      <section class="card">
        <div class="card-head"><div><h2>What actually moves it</h2>
</div></div>
        ${(p.drivers || []).length ? `<div class="card-body" style="padding-bottom:0"><p class="lede">${
          established.filter((d) => d.matters).length
            ? `Of ${p.drivers.length} conditions tested, ${established.length} hold up, and ${established.filter((d) => d.matters).length === 1 ? "one is" : `${established.filter((d) => d.matters).length} are`} big enough to change what you make.`
            : established.length
              ? `Of ${p.drivers.length} conditions tested, ${established.length === 1 ? "one holds" : `${established.length} hold`} up, but ${established.length === 1 ? "it moves" : "they move"} this item by less than one on a typical day. Nothing here should change your number today.`
              : `${p.drivers.length} conditions tested. None of them hold up. What this item does is mostly about the day of the week, not the weather or the calendar.`
        }</p></div>` : ""}
        ${established.length ? `<div class="tablewrap"><table class="dt" style="min-width:660px"><thead><tr>
          <th>Condition</th><th class="num right">Effect</th><th class="num right">Today</th>
          <th class="num right">Chance it is noise</th><th>Evidence</th></tr></thead><tbody>
          ${established.map((d) => `<tr>
            <td class="name"><b>${e(d.label)}</b><small>${e(d.phrase)}</small></td>
            <td class="num right ${d.effect_percent >= 0 ? "up" : "down"}">${d.effect_percent >= 0 ? "+" : ""}${d.effect_percent}%</td>
            <td class="num right ${d.today_effect_units >= 0 ? "up" : "down"}">${d.today_effect_units >= 0 ? "+" : ""}${d.today_effect_units}</td>
            <td class="num right">${formatP(d.q)}</td>
            <td class="small muted">${e(d.evidence)}${d.provisional ? " · thin sample" : ""}${d.note ? `<br><b>${e(d.note)}</b>` : ""}</td></tr>`).join("")}
        </tbody></table></div>`
        : `<div class="empty">${icon("empty")}<b>Nothing outside the restaurant moves this item</b>
             <span>Once the day of the week is accounted for, no condition tested here changes it enough to be sure of.</span></div>`}
        ${watching.length ? `<div class="card-body" style="padding-top:0">
          <p class="small muted" style="margin-bottom:8px">Leaning one way, not proven. Not enough to act on.</p>
          ${watching.map((d) => `<p class="small muted" style="margin-bottom:6px"><b>${e(d.label)}</b> looks like ${d.effect_percent >= 0 ? "+" : ""}${d.effect_percent}% ${e(d.phrase)}, but the chance of seeing that from noise alone is ${formatP(d.q)}. ${e(d.note)}</p>`).join("")}
        </div>` : ""}
        ${rejected.length ? `<details class="context-disclosure" style="margin:0 18px 16px">
          <summary>${rejected.length} conditions tested that did not hold up</summary>
          <div class="context-detail">
            ${rejected.map((d) => `<p><b>${e(d.label)}</b>: ${d.effect_percent >= 0 ? "+" : ""}${d.effect_percent}% per unit, but the chance of seeing that from noise alone is ${formatP(d.q)}. ${e(d.evidence)}.</p>`).join("")}
          </div></details>` : ""}
      </section>

      <div class="brief-grid lower-grid" style="border:0">
        <section class="card">
          <div class="card-head"><div><h2>Has it changed?</h2></div></div>
          <div class="card-body">
            ${p.trend.recent_average !== null && p.trend.recent_average !== undefined ? `
              <p class="lede">The last four weeks averaged <b>${p.trend.recent_average}</b> a day against <b>${p.trend.prior_average}</b> in the four weeks before.
              ${p.trend.moved
                ? `That is a real move: a difference this size would come up by chance about ${formatP(p.trend.p)} of the time.`
                : `That is not a real move. A gap this size is ordinary week-to-week variation.`}</p>
              ${p.trend.same_weeks_last_year ? `<p class="lede" style="margin-top:10px">The same weeks last year averaged <b>${p.trend.same_weeks_last_year}</b>.</p>` : ""}
              ${p.trend.long_run ? `<p class="small muted" style="margin-top:10px">Over the whole record it is moving ${p.trend.long_run.per_year >= 0 ? "up" : "down"} about ${Math.abs(p.trend.long_run.per_year)} a day per year, ${e(p.trend.long_run.strength)} evidence.</p>` : ""}
            ` : `<p class="muted small">Not enough history yet.</p>`}
          </div>
        </section>

        <section class="card">
          <div class="card-head"><div><h2>When it sells</h2></div></div>
          <div class="card-body">
            <p class="lede">Busiest at <b>${e(p.hourly.busiest || "no clear hour")}</b>, which takes ${p.hourly.busiest_share}% of the day.
            Half of them are gone by <b>${e(p.hourly.half_sold_by || "the end of service")}</b>.</p>
            ${hourShape(p.hourly)}
            <p class="small muted" style="margin-top:10px">Averaged over the last ${p.hourly.window_days} days.</p>
          </div>
        </section>
      </div>

      ${p.related.length ? `<section class="card">
        <div class="card-head"><div><h2>What it moves with</h2>
          <p>A negative pairing means people are choosing between them.</p></div></div>
        <div class="tablewrap"><table class="dt" style="min-width:560px"><thead><tr>
          <th>Item</th><th>Relationship</th><th class="num right">Strength</th><th class="num right">Days compared</th></tr></thead><tbody>
          ${p.related.map((r) => `<tr class="clickable" data-item-sheet="${e(r.item_id)}">
            <td class="name"><b>${e(r.name)}</b></td>
            <td class="${r.r > 0 ? "up" : "down"}">${e(r.kind)}</td>
            <td class="num right">${Math.abs(r.r)}</td>
            <td class="num right muted">${num(r.days)}</td></tr>`).join("")}
        </tbody></table></div>
        <div class="card-foot">Strength runs from 0 to 1. Anything above about 0.4 is a strong pairing for daily food sales.</div>
      </section>` : ""}

      <div class="brief-grid lower-grid" style="border:0">
        ${p.accuracy.days ? `<section class="card">
          <div class="card-head"><div><h2>How well we call this one</h2></div></div>
          <div class="card-body">
            <p class="lede">Over the last ${noun(p.accuracy.days, "scored day")} this item has been called <b>${p.accuracy.accuracy}%</b> right,
            missing by <b>${p.accuracy.average_miss}</b> a day on average. Lean: ${e(p.accuracy.bias_direction)}.</p>
            ${p.accuracy.sold_out_days ? `<p class="lede down" style="margin-top:8px">It ran out during service on ${noun(p.accuracy.sold_out_days, "of those days")}.</p>` : ""}
          </div>
        </section>` : ""}

        ${p.unusual.length ? `<section class="card">
          <div class="card-head"><div><h2>Days it behaved oddly</h2>
            <p>Days this item did something its own record cannot account for.</p></div></div>
          <div class="card-body" style="display:grid;gap:9px">
            ${p.unusual.map((u) => `<div style="display:grid;grid-template-columns:96px 1fr auto;gap:12px;align-items:baseline;font-size:12.5px">
              <b>${e(dShort(u.date))}</b>
              <span class="muted">${e(u.weekday)}, sold ${num(u.sold)}. ${e(u.note)}</span>
              <span class="${u.sigma >= 0 ? "up" : "down"} tnum">${u.above_normal >= 0 ? "+" : ""}${u.above_normal}</span>
            </div>`).join("")}
          </div>
        </section>` : ""}
      </div>

      ${p.composition ? `<section class="card">
        <div class="card-head"><div><h2>What goes into it</h2><p>${e(p.composition.summary)}</p></div>
          <div class="spacer"></div><span class="tag ${p.composition.confidence === "high" ? "up" : "plain"}">${e(p.composition.confidence)} confidence</span></div>
        <div class="tablewrap"><table class="dt" style="min-width:520px"><thead><tr>
          <th>Part</th><th>Role</th><th class="num right">Per ${e(p.item.unit)}</th>
          <th class="num right">For ${num(prep.quantity ?? p.today.expected)} today</th></tr></thead><tbody>
          ${p.composition.components.map((c) => `<tr>
            <td class="name"><b>${e(c.name)}</b></td>
            <td><span class="rolechip"><i class="${roleClass(c.role)}"></i>${e(c.role)}</span></td>
            <td class="num right">${e(c.quantity || "not stated")}</td>
            <td class="num right">${e(scaleQuantity(c.quantity, prep.quantity ?? p.today.expected))}</td></tr>`).join("")}
        </tbody></table></div>
      </section>` : ""}

      
    </div>`;
  }

  // Multiply a per-unit amount up to today's batch when the amount is numeric.
  function scaleQuantity(quantity, batch) {
    if (!quantity) return "";
    const match = String(quantity).match(/([\d.]+)\s*(g|kg|oz|lb|ml|l|cup|cups|slice|slices|piece|pieces|patty|patties|bun|buns|egg|eggs|set|sets|portion|portions|box|boxes|bag|bags|carton|cartons|scoop|scoops|coat|coats|pinch|plate|plates|wrap|wraps|sleeve|sleeves|ball|balls|lid|lids|straw|straws)\b/i);
    if (!match) return "";
    const total = Number(match[1]) * Number(batch || 0);
    if (!Number.isFinite(total) || total <= 0) return "";
    let unit = match[2].toLowerCase();
    let value = total;
    if (unit === "g" && total >= 1000) { value = total / 1000; unit = "kg"; }
    if (unit === "ml" && total >= 1000) { value = total / 1000; unit = "l"; }
    if (unit === "oz" && total >= 16) { value = total / 16; unit = "lb"; }
    const rounded = value >= 100 ? Math.round(value) : Math.round(value * 10) / 10;
    // Weights and volumes stay as they are; countable things take a plural.
    const countable = { slice: "slices", piece: "pieces", patty: "patties", bun: "buns", egg: "eggs",
      set: "sets", portion: "portions", box: "boxes", bag: "bags", carton: "cartons", scoop: "scoops",
      coat: "coats", plate: "plates", wrap: "wraps", sleeve: "sleeves", ball: "balls", lid: "lids",
      straw: "straws", cup: "cups" };
    if (rounded !== 1 && countable[unit]) unit = countable[unit];
    return `${rounded.toLocaleString()} ${unit}`;
  }

  function formatP(p) {
    const value = Number(p);
    if (!Number.isFinite(value)) return "unknown";
    if (value < 0.0001) return "under 1 in 10,000";
    if (value < 0.001) return "about 1 in 1,000";
    if (value < 0.01) return `about 1 in ${Math.round(1 / value)}`;
    if (value < 0.2) return `about 1 in ${Math.round(1 / value)}`;
    return `${Math.round(value * 100)}%`;
  }

  /* ---------- first run tutorial ---------- */
  // Five stops. Each one lands on a real part of the real product with the
  // operator's own numbers already in it, because a tour of empty boxes teaches
  // nothing. Advancing switches the screen, scrolls the thing into view, and
  // rings it; the card sits beside it, never on top of it.
  // `target` is a list of selectors, first match wins, so a stop survives a
  // screen being reworked around it.
  const TOUR = [
    {
      view: "today",
      target: [".headline", "#daytiles"],
      title: "Today's three numbers",
      body: "What is expected to sell, how many to make, and the busiest hour. Each one sits next to a normal day of the same name.",
      place: "bottom",
    },
    {
      view: "today",
      pane: "make",
      target: ["#daypanes .card-head", "#daypanes"],
      title: "How many to make",
      body: "A number for every item. Make sits above what is expected to sell, because running out costs more than throwing away. Adjust any line and say why.",
      place: "bottom",
    },
    {
      view: "today",
      target: ["[data-view='ordering']"],
      title: "What to buy",
      body: "The day turned into a shopping list, by supplier. Count what is in the walk-in and it says what runs out when, and who to order it from.",
      place: "right",
    },
    {
      view: "history",
      target: ["#dayrows .dayrow:not(.head)", "#dayrows", ".dayrows"],
      title: "How close past calls were",
      body: "Each closed day: what it sold, what it kept, and how close the morning number was. The score is always against what was said before service.",
      place: "bottom",
    },
    {
      view: "today",
      pane: "make",
      target: ["#daypanes .dt tbody tr", "#daypanes"],
      title: "Any item, in full",
      body: "Tap any item name for its whole record: which days it belongs to, what changes it, and how close past calls were.",
      place: "bottom",
    },
  ];

  function tourEligible() {
    if (store.get("quantify.tour") === "done") return false;
    return S.view === "today" && !!S.data && !!document.getElementById("daypanes");
  }

  // Works from any screen: the first stop is on Today, so Today is opened
  // first. Replayed from Settings > Account through data-do="tour-start".
  async function startTour(fromStart = true) {
    if (fromStart) S.tour = { step: 0 };
    if (!S.tour) return;
    if (S.view !== "today") {
      S.view = "today"; S.todayPane = "make"; store.set("quantify.view", S.view);
      window.scrollTo(0, 0);
      await loadView();
    }
    document.body.classList.add("tour-on");
    return paintTour();
  }

  // Every way out lands back on Today, at the top, with the tour marked done.
  // closeLayer hands the tour here, so Escape and the scrim end it too.
  function endTour() {
    const wasElsewhere = S.view !== "today";
    document.body.classList.remove("tour-on");
    S.tour = null;
    store.set("quantify.tour", "done");
    closeLayer();
    if (wasElsewhere) {
      S.view = "today"; S.todayPane = "make"; store.set("quantify.view", S.view);
      loadView();
    }
    window.scrollTo(0, 0);
  }

  function tourFrame(box, pad) {
    if (!box) return `<div class="tour-veil" data-do="tour-end" style="inset:0"></div>`;
    const top = Math.max(0, box.top - pad);
    const bottom = Math.min(window.innerHeight, box.bottom + pad);
    const left = Math.max(0, box.left - pad);
    const right = Math.min(window.innerWidth, box.right + pad);
    const band = `top:${top}px;height:${Math.max(0, bottom - top)}px`;
    return `
      <div class="tour-veil" data-do="tour-end" style="top:0;left:0;right:0;height:${top}px"></div>
      <div class="tour-veil" data-do="tour-end" style="top:${bottom}px;left:0;right:0;bottom:0"></div>
      <div class="tour-veil" data-do="tour-end" style="${band};left:0;width:${left}px"></div>
      <div class="tour-veil" data-do="tour-end" style="${band};left:${right}px;right:0"></div>
      <div class="tour-ring" style="top:${top}px;left:${left}px;width:${Math.max(0, right - left)}px;height:${Math.max(0, bottom - top)}px"></div>`;
  }

  const tourTarget = (stop) => (stop.target || []).map((sel) => document.querySelector(sel)).find(Boolean) || null;
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  async function paintTour() {
    if (!S.tour) return;
    const tour = S.tour;
    const sequence = paintTour._sequence = (paintTour._sequence || 0) + 1;
    const current = () => S.tour === tour && sequence === paintTour._sequence;
    const stop = TOUR[S.tour.step];
    if (!stop) return endTour();

    const needsView = stop.view && S.view !== stop.view;
    if (needsView) {
      S.view = stop.view;
      store.set("quantify.view", S.view);
      await loadView();
      if (!current()) return;
      // loadView repaints the whole screen, so the tour card has to go back on.
      document.body.classList.add("tour-on");
    }
    if (stop.pane && S.todayPane !== stop.pane) {
      S.todayPane = stop.pane;
      render();
    }

    const node = tourTarget(stop);
    if (node) {
      // A card taller than half the screen is scrolled to its top, so its
      // heading and first rows stay in sight with the tour card below them.
      const tall = node.getBoundingClientRect().height > window.innerHeight * 0.55;
      node.scrollIntoView({ behavior: "smooth", block: tall ? "start" : "center" });
      await wait(420);
      if (tall) { window.scrollBy({ top: -80, behavior: "smooth" }); await wait(260); }
    } else {
      window.scrollTo({ top: 0, behavior: "smooth" });
      await wait(260);
    }
    if (!current()) return;
    const box = node ? node.getBoundingClientRect() : null;
    const last = S.tour.step === TOUR.length - 1;
    openLayer(`${tourFrame(box, 8)}
      <div class="tour-card" role="dialog" aria-modal="true" aria-label="${e(stop.title)}">
        <div class="tour-top">
          <span class="tour-count">${S.tour.step + 1} of ${TOUR.length}</span>
          <button class="tour-skip" data-do="tour-end">Skip</button>
        </div>
        <h2>${e(stop.title)}</h2>
        <p>${e(stop.body)}</p>
        <div class="tour-foot">
          <button class="tour-back" data-do="tour-back" ${S.tour.step === 0 ? "disabled" : ""} aria-label="Back">
            ${icon("chevL")}
          </button>
          <div class="tour-dots">${TOUR.map((_, i) =>
            `<i class="${i === S.tour.step ? "on" : ""}${i < S.tour.step ? " seen" : ""}"></i>`).join("")}</div>
          <button class="btn accent" data-do="tour-next" autofocus>${last ? "Finish" : "Next"}</button>
        </div>
      </div>`);
    placeTourCard(node, stop.place, box);
  }

  // The card goes beside the ring, never over it. On a phone it docks to the
  // bottom edge (or the top edge when the ring is in the lower half), and the
  // ring is cut short so it ends above the card.
  function placeTourCard(node, place, box) {
    const card = layer.querySelector(".tour-card");
    if (!card) return;
    const pad = 18;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    if (!node || !box) {
      card.style.top = "50%"; card.style.left = "50%"; card.style.transform = "translate(-50%, -50%)";
      return;
    }
    const size = card.getBoundingClientRect();

    if (vw < 620) {
      const lowerHalf = box.top + box.height / 2 > vh / 2;
      card.style.left = "8px"; card.style.right = "8px"; card.style.transform = "none";
      if (lowerHalf) { card.style.top = "8px"; card.style.bottom = "auto"; }
      else { card.style.top = "auto"; card.style.bottom = "calc(8px + env(safe-area-inset-bottom))"; }
      clampRing(box, card.getBoundingClientRect());
      return;
    }

    const fitsAbove = box.top - size.height - pad >= pad;
    const fitsBelow = box.bottom + size.height + pad <= vh - pad;
    const fitsRight = vw - box.right >= size.width + pad * 2;
    let top;
    let left = box.left + box.width / 2 - size.width / 2;

    if (place === "right" && fitsRight) { left = box.right + pad; top = Math.max(pad, Math.min(box.top, vh - size.height - pad)); }
    else if (place === "top" && fitsAbove) top = box.top - size.height - pad;
    else if (place === "bottom" && fitsBelow) top = box.bottom + pad;
    else if (fitsBelow) top = box.bottom + pad;
    else if (fitsAbove) top = box.top - size.height - pad;
    else if (fitsRight) { left = box.right + pad; top = Math.max(pad, Math.min(box.top, vh - size.height - pad)); }
    else if (box.left >= size.width + pad * 2) { left = box.left - size.width - pad; top = Math.max(pad, Math.min(box.top, vh - size.height - pad)); }
    else {
      // A target that fills the screen. The card takes the bottom corner and
      // the ring is cut short above it, so what is ringed stays in view.
      left = vw - size.width - pad;
      top = vh - size.height - pad;
    }

    card.style.top = Math.max(pad, Math.min(top, vh - size.height - pad)) + "px";
    card.style.left = Math.max(pad, Math.min(left, vw - size.width - pad)) + "px";
    card.style.transform = "none";
    clampRing(box, card.getBoundingClientRect());
  }

  // Redraws the veil so the ring stops short of the card when the two overlap.
  function clampRing(box, card) {
    const overlaps = box.bottom > card.top && box.top < card.bottom && box.right > card.left && box.left < card.right;
    if (!overlaps) return;
    const cut = { top: box.top, bottom: box.bottom, left: box.left, right: box.right };
    if (card.top > box.top + 40) cut.bottom = Math.min(box.bottom, card.top - 12);
    else if (card.bottom < box.bottom - 40) cut.top = Math.max(box.top, card.bottom + 12);
    else return;
    layer.querySelectorAll(".tour-veil, .tour-ring").forEach((n) => n.remove());
    layer.insertAdjacentHTML("afterbegin", tourFrame(cut, 8));
  }
  /* ---------- sheets ---------- */
  async function openDaySheet(dateISO) {
    layer.innerHTML = `<div class="scrim" data-do="close-layer"></div>
      <aside class="sheet"><div class="sheet-head"><div><h2>${e(dLong(dateISO))}</h2><p>Loading the day</p></div>
      <div style="margin-left:auto"><button class="icon-btn" data-do="close-layer">${icon("close")}</button></div></div>
      <div class="sheet-body">${skeleton()}</div></aside>`;
    try {
      const d = await API.get(`/api/history/day?location_id=${encodeURIComponent(S.locationId)}&date=${dateISO}`);
      const r = d.review || {};
      const acc = d.accuracy;
      const maxHour = Math.max(...(d.hourly || []).flatMap((h) => [h.actual, h.predicted]), 1);
      layer.innerHTML = `<div class="scrim" data-do="close-layer"></div>
        <aside class="sheet">
          <div class="sheet-head">
            <div><h2>${e(dLong(dateISO))}</h2><p>${e(d.weekday)} · ${num(d.orders)} orders · ${money(d.sales)}</p></div>
            <div style="margin-left:auto;display:flex;gap:6px">
              <button class="btn sm" data-open-date="${dateISO}" data-do="close-layer">Open the plan</button>
              <button class="icon-btn" data-do="close-layer">${icon("close")}</button></div>
          </div>
          <div class="sheet-body">
            <section class="card"><div class="card-body">
              <div class="eyebrow">How the call went</div>
              <h3 style="margin-top:8px;font-size:16px;line-height:1.4">${e(r.headline || "")}</h3>
              <p class="lede" style="margin-top:10px">${e(r.where_error_sat || "")}</p>
              <p class="lede" style="margin-top:8px">${e(r.likely_reason || "")}</p>
              <p class="lede" style="margin-top:8px"><b>${e(r.matters || "")}</b></p>
              ${acc !== null ? `<div class="accmeter" style="margin-top:14px">
                <div class="top"><b>${Math.round(acc)}% per item</b><small>${num(d.predicted_units)} called, ${num(d.units)} sold</small></div>
                <div class="line"><i class="${acc >= 90 ? "" : acc >= 80 ? "mid" : "low"}" style="width:${Math.max(4, acc)}%"></i></div></div>` : ""}
            </div></section>

            <section class="tiles" style="grid-template-columns:repeat(${d.costs ? 3 : 2},minmax(0,1fr))">
              ${tile("Rang up", money(d.sales), `<b>${num(d.units)}</b> items across <b>${num(d.orders)}</b> orders.`, `Average order ${money(d.average_order, true)}`)}
              ${d.costs ? tile("Left after costs", money(d.costs.left_after_costs),
                `<b>${money(d.costs.cogs)}</b> in food and <b>${money(d.costs.labour)}</b> in wages came out of that${d.costs.other ? `, plus <b>${money(d.costs.other)}</b> fixed` : ""}.`,
                `About ${d.costs.margin_percent}% of net sales`) : ""}
              ${tile("Called", d.predicted_sales !== null ? money(d.predicted_sales) : "Not scored", d.predicted_units !== null ? `<b>${num(d.predicted_units)}</b> items expected.` : "This day has not been scored yet.", `Called the day before`)}
            </section>

            ${(d.hourly || []).length ? `<section class="card">
              <div class="card-head"><div><h2>Hour by hour</h2><p>Solid is what sold. The outline is what was called.</p></div></div>
              <div class="card-body"><div class="hours">${d.hourly.map((h) => `
                <div class="hourcol" title="${clock(String(h.hour).padStart(2, "0") + ":00")}: ${num(h.actual)} sold, ${num(h.predicted)} called">
                  <div class="track"><i class="ghost" style="height:${Math.max(3, (h.predicted / maxHour) * 100)}%"></i><i style="height:${Math.max(3, (h.actual / maxHour) * 100)}%"></i></div>
                  <span>${((h.hour % 12) || 12)}</span></div>`).join("")}</div></div>
            </section>` : ""}

            ${(d.item_scores || []).length ? `<section class="card">
              <div class="card-head"><div><h2>Item by item</h2><p>Sorted by how far off each one was.</p></div></div>
              <div class="tablewrap"><table class="dt" style="min-width:0"><thead><tr>
                <th>Item</th><th class="num right">Called</th><th class="num right">Sold</th><th class="num right">Gap</th></tr></thead><tbody>
                ${d.item_scores.map((row) => `<tr>
                  <td class="name"><b>${e(row.name)}</b>${row.sold_out ? `<small class="down">ran out during service</small>` : ""}</td>
                  <td class="num right">${num(row.predicted)}</td><td class="num right">${num(row.actual)}</td>
                  <td class="num right ${row.gap >= 0 ? "up" : "down"}">${row.gap >= 0 ? "+" : ""}${num(row.gap)}</td></tr>`).join("")}
              </tbody></table></div>
            </section>` : ""}

            <section class="card">
              <div class="card-head"><div><h2>Where the orders came from</h2></div></div>
              <div class="card-body" style="display:grid;gap:10px">
                ${(() => { const gross = d.channels.reduce((n, r) => n + r.sales, 0) || 1; return d.channels.map((c) => `<div class="mixrow">
                  <div class="who"><b>${e(c.channel)}</b><small>${num(c.orders)} orders</small></div>
                  <div class="mixbar"><i style="width:${Math.max(2, Math.min(100, (c.sales / gross) * 100))}%"></i></div>
                  <span class="qty">${money(c.sales)}<div class="small muted">${Math.round((c.sales / gross) * 100)}% of tickets</div></span>
                  </div>`).join(""); })()}
              </div>
            </section>
          </div>
        </aside>`;
    } catch (error) {
      toast(error.message, "error");
      closeLayer();
    }
  }

  /* ---------- layers ---------- */
  // Every sheet and modal opens through here so it gets focus on open and
  // gives it back on close. Screens still setting layer.innerHTML directly
  // are converted by their own region; new code uses openLayer.
  function openLayer(html) {
    if (!openLayer._from) openLayer._from = document.activeElement;
    layer.innerHTML = html;
    const first = layer.querySelector("[autofocus], input:not([type=hidden]), textarea, select, button:not(.modal-close):not(.scrim)");
    if (first) { try { first.focus({ preventScroll: true }); } catch (_) { /* nothing focusable */ } }
  }

  function closeLayer() {
    if (S.tour) return endTour(false);
    layer.innerHTML = "";
    S.cancelFlow = null;
    const from = openLayer._from;
    openLayer._from = null;
    if (from && from.focus && document.contains(from)) { try { from.focus({ preventScroll: true }); } catch (_) { /* gone */ } }
  }

  // One heading, one sentence saying what will appear, and at most one button.
  function emptyState(title, detail, button = "") {
    return `<div class="empty">${icon("empty")}<b>${e(title)}</b><span>${e(detail)}</span>${button ? `<div>${button}</div>` : ""}</div>`;
  }

  /* ---------- live pulse ---------- */
  // Asks the server every few seconds whether anything changed. It never
  // repaints while a field has focus, while a sheet or modal is open, or on
  // Settings, and it rests while the tab is hidden.
  function pulseLabel() {
    return S.pulse.live ? "Up to date" : "Reconnecting";
  }

  function stopPulse() {
    clearInterval(startPulse._t);
    startPulse._t = null;
    if (startPulse._vis) document.removeEventListener("visibilitychange", startPulse._vis);
    startPulse._vis = null;
  }

  function startPulse() {
    stopPulse();
    const check = async () => {
      if (document.hidden || !S.boot) return;
      try {
        const result = await API.get(`/api/pulse?location_id=${encodeURIComponent(S.locationId)}`);
        S.pulse.checkedAt = Date.now();
        if (result.today) S.boot.today = result.today;
        const changed = !!(S.pulse.version && S.pulse.version !== result.version);
        S.pulse.version = result.version;
        S.pulse.live = true;
        const busy = !!layer.innerHTML || isTyping() || S.view === "settings";
        if ((changed || S.pulse.pending) && !busy) { S.pulse.pending = false; await loadView(true); }
        else if (changed) S.pulse.pending = true;
      } catch (_) {
        S.pulse.live = false;
      }
      paintPulse();
    };
    const tick = () => { clearInterval(startPulse._t); startPulse._t = setInterval(check, 11000); };
    startPulse._vis = () => {
      if (document.hidden) { clearInterval(startPulse._t); startPulse._t = null; }
      else { check(); tick(); }
    };
    document.addEventListener("visibilitychange", startPulse._vis);
    check();
    tick();
  }

  function paintPulse() {
    const dot = document.querySelector(".pulse-dot");
    const text = document.getElementById("pulse-text");
    if (!dot || !text) return;
    const cls = `pulse-dot ${S.pulse.live ? "" : "stale"}`.trim();
    if (dot.className !== cls) dot.className = cls;
    const label = pulseLabel();
    if (text.textContent !== label) text.textContent = label;
  }

  // Everything a location holds in memory. Called when the location changes so
  // nothing from the last one is shown or saved against the new one.
  function resetLocationState() {
    S.costs = null; S.menu = null; S.supply = null; S.attention = null; S.outlook = null; S.data = null;
    S.order.edits = {}; S.order.extras = [];
    S.narrativeTried = ""; S.itemCache = {}; S.open = new Set();
    S.history.costs = null; S.pulse.version = null; S.pulse.pending = false;
  }

  async function moreDays() {
    if (S.history.loading || !S.history.hasMore || !S.history.nextBefore) return;
    S.history.loading = true;
    try {
      const start = rangeStart(S.history.range);
      const page = await API.get(`/api/history/days?location_id=${encodeURIComponent(S.locationId)}&before=${S.history.nextBefore}&limit=18${start ? `&start=${start}` : ""}`);
      S.history.days = S.history.days.concat(page.days);
      S.history.nextBefore = page.next_before;
      S.history.hasMore = page.has_more;
    } catch (error) { toast(error.message, "error"); }
    S.history.loading = false;
    render(true);
  }

  async function moreOrders() {
    if (S.orders.loading || !S.orders.hasMore || !S.orders.nextDate) return;
    S.orders.loading = true;
    try {
      const start = rangeStart(S.orders.range);
      const page = await API.get(`/api/history/orders?location_id=${encodeURIComponent(S.locationId)}&before=${S.orders.nextDate}&skip=${S.orders.nextSkip}&limit=40${start ? `&start=${start}` : ""}`);
      S.orders.rows = S.orders.rows.concat(page.orders);
      S.orders.nextDate = page.next_before_date;
      S.orders.nextSkip = page.next_skip;
      S.orders.hasMore = page.has_more;
    } catch (error) { toast(error.message, "error"); }
    S.orders.loading = false;
    render(true);
  }

  /* ---------- interactions ---------- */
  // Moves to another day. The page starts at the top and any order quantities
  // typed for the old window are let go.
  function setDate(iso) {
    if (!iso || iso === S.date) return;
    S.date = iso;
    if (S.view === "ordering") S.order.edits = {};
    window.scrollTo(0, 0);
    return loadView();
  }

  document.addEventListener("click", async (event) => {
    // Tapping the dimmed page outside a sheet or modal closes it.
    if (event.target.classList.contains("scrim") || event.target.classList.contains("modal-wrap")) return closeLayer();
    if (event.target.id === "date-picker") {
      // Chrome only opens the calendar from its own icon; ask for it outright.
      try { if (event.target.showPicker) event.target.showPicker(); } catch (_) { /* the native tap already opened it */ }
      return;
    }
    const link = event.target.closest("a[data-link]");
    if (link) { event.preventDefault(); return go(link.dataset.link); }
    const anchor = event.target.closest("a[data-scroll]");
    if (anchor) {
      event.preventDefault();
      document.getElementById(anchor.dataset.scroll)?.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    // Links that behave like buttons are handled here too, so a link to a
    // settings page switches the page instead of reloading the whole app.
    const target = event.target.closest("button, a[data-do], a[data-stab], a[data-view]");
    if (!target) {
      const row = event.target.closest("[data-item-sheet]");
      if (row) return openItemSheet(row.dataset.itemSheet);
      return;
    }
    if (target.tagName === "A") event.preventDefault();

    if (target.dataset.view) {
      // A second tap on the same button while it is still loading does nothing.
      if (S.view === target.dataset.view && root.querySelector(".progress.on")) return;
      S.view = target.dataset.view;
      store.set("quantify.view", S.view);
      window.scrollTo(0, 0);
      return loadView();
    }
    if (target.dataset.htab) { S.historyTab = target.dataset.htab; return loadView(); }
    if (target.dataset.owin) { S.order.days = Number(target.dataset.owin); S.order.edits = {}; return loadView(); }
    if (target.dataset.stab) {
      // The tab paints from what is already in memory; only what it lacks is
      // fetched afterwards.
      const have = S.view === "settings" && S.data && S.data.setup && S.data.billing;
      S.settingsTab = target.dataset.stab;
      store.set("quantify.stab", S.settingsTab);
      S.view = "settings"; store.set("quantify.view", S.view);
      window.scrollTo(0, 0);
      if (have) { render(); return loadView(true); }
      return loadView();
    }
    if (target.dataset.pane) {
      S.todayPane = target.dataset.pane;
      // Only the panel changes, not the page around it.
      const panel = document.getElementById("daypanes");
      const head = panel && panel.querySelector(".card-head");
      if (panel && head && S.data && typeof todayPane === "function") {
        head.querySelectorAll("[data-pane]").forEach((node) => node.classList.toggle("on", node.dataset.pane === S.todayPane));
        while (head.nextSibling) head.nextSibling.remove();
        head.insertAdjacentHTML("afterend", todayPane(S.data));
      } else render(true);
      if (S.todayPane === "ahead") loadOutlook();
      return;
    }
    if (target.dataset.drange) { S.history.range = target.dataset.drange; return loadView(); }
    if (target.dataset.orange) { S.orders.range = target.dataset.orange; return loadView(); }
    if (target.dataset.day) return setDate(addDays(S.date, Number(target.dataset.day)));
    if (target.dataset.openDate) {
      S.view = "today"; S.todayPane = "make"; closeLayer();
      store.set("quantify.view", S.view);
      S.date = ""; return setDate(target.dataset.openDate);
    }
    if (target.dataset.dayDetail) return openDaySheet(target.dataset.dayDetail);
    if (target.dataset.itemSheet) return openItemSheet(target.dataset.itemSheet);
    if (target.dataset.expand) {
      const id = target.dataset.expand;
      if (S.open.has(id)) S.open.delete(id); else S.open.add(id);
      return render(true);
    }
    if (target.dataset.pick) {
      S.onboarding.values[target.dataset.pick] = target.dataset.value;
      target.parentElement.querySelectorAll("[data-pick]").forEach((node) => {
        node.classList.toggle("on", node.dataset.value === target.dataset.value);
      });
      return;
    }
    if (target.dataset.toggleGoal) {
      const goal = target.dataset.toggleGoal;
      const goals = S.onboarding.values.goals || [];
      const next = goals.includes(goal) ? goals.filter((g) => g !== goal) : goals.concat(goal);
      S.onboarding.values.goals = next;
      target.classList.toggle("on", next.includes(goal));
      return;
    }
    if (target.dataset.place) {
      const holder = target.closest(".typeahead");
      const field = holder ? holder.querySelector("[data-typeahead]") : null;
      if (field) field.value = target.dataset.place;
      if (field && field.id === "place-input") {
        S.onboarding.values.place = target.dataset.place;
        S.onboarding.suggestions = [];
      }
      paintSuggestions(holder, []);
      API.get(`/api/timezone?q=${encodeURIComponent(target.dataset.place)}`).then((r) => {
        if (field && field.id === "place-input") S.onboarding.tz = r.match;
        const hint = holder && holder.parentElement
          ? holder.parentElement.querySelector("[data-tz-hint]") : document.querySelector("[data-tz-hint]");
        if (hint) hint.innerHTML = tzHint(r.match);
      }).catch(() => {});
      return;
    }
    if (target.dataset.cancelReason) {
      S.cancelFlow.detail = document.getElementById("cancel-detail")?.value || "";
      S.cancelFlow.reason = target.dataset.cancelReason;
      return renderCancelModal();
    }
    if (target.dataset.demoTab) {
      demoTabs.tab = target.dataset.demoTab;
      const main = document.getElementById("screen-main");
      if (main) main.innerHTML = screenPanel(S.show || {});
      document.querySelectorAll("[data-demo-tab]").forEach((node) =>
        node.classList.toggle("on", node.dataset.demoTab === demoTabs.tab));
      return;
    }
    if (target.dataset.sync) return runSync(target);

    const action = target.dataset.do;
    if (!action) return;

    switch (action) {
      case "retry": return boot();
      case "today": return setDate(todayISO());
      case "back-signin": return go("/login");
      case "close-layer": return closeLayer();
      case "copy": {
        const ok = await copyText(target.dataset.copy || "");
        return toast(ok ? "Copied" : "Could not copy on this browser", ok ? "ok" : "error");
      }
      case "onb-back":
        S.onboarding.step = Math.max(0, S.onboarding.step - 1);
        return renderOnboarding();
      case "onb-finish": return finishOnboarding();
      case "add-cost-line": {
        const host = document.getElementById("cost-lines");
        if (host) host.insertAdjacentHTML("beforeend", costLine({ name: "", amount: "", period: "month" }));
        return;
      }
      case "drop-cost-line": {
        const line = target.closest(".costline");
        const host = document.getElementById("cost-lines");
        if (line && host && host.children.length > 1) line.remove();
        else if (line) line.querySelectorAll("input").forEach((node) => { node.value = ""; });
        return;
      }
      case "preview-email": return previewEmail();
      case "send-test": return sendTest();
      case "adjust":
        try { return openAdjust(target); }
        catch (_) { return toast("Open Today to adjust this item", "error"); }
      case "clear-adjust": return clearAdjust(target);
      case "recompose": return composeItem(target.dataset.item, true);
      case "edit-composition": return editComposition(target.dataset.item);
      case "menu-preview": case "menu-import": return menuImport(action === "menu-import");
      case "switch-location": return openLocationPicker();
      case "new-codes": return newRecoveryCodes();
      case "resend-code": return resendCode();
      case "mfa-on": return openTwoStep();
      case "mfa-off": return openTwoStepOff();
      case "signout":
        try { await API.send("/api/auth/logout", "POST", {}); } catch (_) { /* expire locally */ }
        leaveApp();
        API.setCsrf(""); S.auth = null; S.boot = null; S.cache = {}; S.date = "";
        store.set("quantify.view", "today");
        return go("/", true);
      case "more-days": return moreDays();
      case "more-orders": return moreOrders();
      case "cancel-start": return cancelStart();
      case "cancel-confirm": return cancelConfirm();
      case "close-toast": toastNode.className = "toast"; return;
      case "tour-next": if (!S.tour) return; target.disabled = true; S.tour.step += 1; return paintTour();
      case "tour-back": if (!S.tour) return; target.disabled = true; S.tour.step = Math.max(0, S.tour.step - 1); return paintTour();
      case "tour-end": return endTour(false);
      case "tour-start":
        // Replayed from Settings > Account: the tour begins on Today.
        closeLayer();
        S.view = "today"; S.todayPane = "make"; store.set("quantify.view", S.view);
        window.scrollTo(0, 0);
        await loadView();
        return startTour(true);
      case "billing-portal": return billingRedirect("/api/billing/portal");
      case "billing-checkout": return billingRedirect("/api/billing/checkout");
      case "billing-resume":
        try { const r = await API.send("/api/billing/resume", "POST", {}); toast(r.message); await loadView(); }
        catch (error) { toast(error.message, "error"); }
        return;
      default: return;
    }
  });

  document.addEventListener("change", async (event) => {
    if (event.target.id === "date-picker") return setDate(event.target.value);
  });

  // One handler per form id. The supply forms (ids starting f-supply-) have
  // their own listener in the ordering block and are skipped here. The submit
  // button stays disabled until the request settles, so a double tap sends once.
  document.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target;
    if (String(form.id || "").startsWith("f-supply-")) return;
    if (["f-name", "f-password", "f-square"].includes(form.id)) return;
    if (form.dataset.saving) return;
    form.dataset.saving = "1";
    const data = Object.fromEntries(new FormData(form).entries());
    const button = form.querySelector("button[type=submit]");
    if (button) button.disabled = true;
    try {
      if (form.id === "f-costs") { await saveCosts(); return; }
      if (form.id === "f-create") {
        const result = await API.send("/api/auth/setup", "POST", data);
        API.setCsrf(result.csrf_token);
        S.auth = { authenticated: true, user: { ...result.user, csrf_token: result.csrf_token } };
        S.verification = result.verification;
        S.auth.email_verification_required = true;
        return go("/verify", true);
      }
      if (form.id === "f-confirm-email") {
        await API.send("/api/auth/email/confirm", "POST", { code: data.code });
        toast("Email confirmed");
        return go("/app", true);
      }
      if (form.id === "f-signin") {
        const result = await API.send("/api/auth/login", "POST", data);
        if (result.mfa_required) { S.challenge = result.challenge; return renderSignInCode(); }
        API.setCsrf(result.csrf_token);
        // Signing in always lands on Today, whatever screen was open last time.
        S.view = "today"; store.set("quantify.view", "today");
        return go("/app", true);
      }
      if (form.id === "f-code") {
        const result = await API.send("/api/auth/verify", "POST", { challenge: S.challenge, code: data.code });
        API.setCsrf(result.csrf_token);
        S.challenge = "";
        S.view = "today"; store.set("quantify.view", "today");
        return go("/app", true);
      }
      if (form.id === "f-reset-start") {
        await startReset(String(data.email || "").trim());
        return;
      }
      if (form.id === "f-reset-complete") {
        await API.send("/api/auth/password/reset/complete", "POST",
          { email: (S.reset || {}).email || "", code: data.code, new_password: data.new_password });
        S.reset = null;
        toast("Password changed");
        return go("/login", true);
      }
      if (form.id === "f-enable") {
        await API.send("/api/auth/totp/enable", "POST", { code: data.code });
        closeLayer();
        toast("Two-step sign in is on");
        return loadView();
      }
      if (form.id === "f-disable-mfa") {
        await API.send("/api/auth/mfa/disable", "POST", { password: data.password });
        closeLayer();
        toast("Two-step sign in is off");
        return loadView();
      }
      if (form.id === "f-onb") {
        const goals = S.onboarding.values.goals;
        Object.assign(S.onboarding.values, data);
        S.onboarding.values.goals = goals;
        S.onboarding.step += 1;
        return renderOnboarding();
      }
      if (form.id === "f-location") {
        // One Save for the whole tab: the location, then the morning email.
        // The time zone box shows a readable label; the id behind it is only
        // replaced when somebody typed something else.
        const setup = (S.data && S.data.setup) || {};
        const shownLabel = (setup.timezone && setup.timezone.label) || (setup.location && setup.location.timezone) || "";
        const typed = String(data.timezone_text || "").trim();
        const place = { name: data.name, concept: data.concept, city: data.city, region: data.region,
          open_hour: data.open_hour, close_hour: data.close_hour,
          timezone: typed && typed !== shownLabel ? typed : data.timezone };
        const saved = await API.send(`/api/location?location_id=${encodeURIComponent(S.locationId)}`, "POST", place);
        const enabled = !!(form.elements.enabled && form.elements.enabled.checked);
        const address = String(data.owner_email || "").trim();
        await API.send(`/api/email/preferences?location_id=${encodeURIComponent(S.locationId)}`, "POST",
          { owner_email: address, send_time: data.send_time || "05:30", enabled, include_week_ahead: true });
        if (saved && saved.timezone && saved.timezone.confident === false && typed && typed !== shownLabel) {
          toast("Saved, but that time zone was not recognised. Try a city or ZIP code.", "error");
        } else toast("Saved");
        S.boot = await API.get("/api/bootstrap");
        S.data = null;
        return loadView(true);
      }
      if (form.id === "f-composition") {
        const say = (text) => { const node = document.getElementById("recipe-error"); if (node) node.textContent = text; };
        say("");
        const components = recipeRowsRead();
        if (!components.length || components.some((row) => !row.name || !(row.share > 0))) {
          return say("Each part needs a name and a share of the food cost above zero.");
        }
        const total = components.reduce((n, row) => n + row.share, 0);
        if (total < 95 || total > 105) return say(`The shares add up to ${Math.round(total)}%. Use between 95% and 105%.`);
        await API.send(`/api/menu/composition?location_id=${encodeURIComponent(S.locationId)}`, "PUT",
          { item_id: data.item_id, summary: data.summary, components });
        closeLayer();
        toast("Recipe saved");
        S.menu = null;
        return loadView(true);
      }
      if (form.id === "f-adjust") {
        await API.send(`/api/forecast/override?location_id=${encodeURIComponent(S.locationId)}`, "POST",
          { item_id: data.item_id, date: S.date, quantity: Number(data.quantity), reason: data.reason });
        // The item sheet notes which item it has open in S.sheetItem, so an
        // adjustment made from the sheet lands back on the sheet.
        const back = S.sheetItem || "";
        closeLayer();
        toast("Adjusted");
        if (S.view !== "today") { S.view = "today"; S.todayPane = "make"; store.set("quantify.view", S.view); }
        await loadView(true);
        if (back) openItemSheet(back);
        return;
      }
    } catch (error) {
      toast(plainError(error), "error");
    } finally {
      delete form.dataset.saving;
      if (button && document.contains(button)) button.disabled = false;
    }
  });

  /* ---------- actions ---------- */
  // The sample location takes the owner's name, concept, city and time zone,
  // so the first screen is theirs and not a stranger's restaurant.
  async function finishOnboarding() {
    const button = document.querySelector("[data-do='onb-finish']");
    if (button) button.disabled = true;
    try {
      const v = S.onboarding.values;
      const tz = S.onboarding.tz && S.onboarding.tz.confident ? S.onboarding.tz : null;
      const matched = tz && /,/.test(tz.matched || "") ? tz.matched.split(",").map((part) => part.trim()) : [];
      await API.send("/api/onboarding", "POST", {
        company: v.company, concept: v.concept, location_count: v.location_count,
        goal: (v.goals || []).join(", "), pos: v.pos, place: v.place,
        city: matched[0] || v.place || "", region: matched[1] || "",
        timezone: tz ? tz.timezone : "",
        open_hour: Number(v.open_hour ?? 7), close_hour: Number(v.close_hour ?? 21),
      });
      session.remove(ONB_KEY);
      S.view = "today"; store.set("quantify.view", "today");
      return go("/app", true);
    } catch (error) {
      toast(plainError(error), "error");
      if (button && document.contains(button)) button.disabled = false;
    }
  }

  async function runSync(target) {
    const provider = target.dataset.sync;
    const label = target.textContent;
    target.disabled = true;
    target.textContent = "Working";
    try {
      await API.send(`/api/integrations/${provider}/sync?location_id=${encodeURIComponent(S.locationId)}`, "POST",
        { days: provider === "pos" ? 1095 : provider === "events" ? 90 : 16, backfill_days: 1095 });
      toast("Synced");
      // The connection rows carry the sync time, so setup is read again.
      S.data = null;
      await loadView(true);
    } catch (error) {
      toast(plainError(error), "error");
      target.disabled = false;
      target.textContent = label;
    }
  }

  async function previewEmail() {
    openLayer(`<div class="scrim" data-do="close-layer"></div>
      <div class="modal-wrap"><div class="modal wide" role="dialog" aria-modal="true" aria-label="Morning email">
        <button class="modal-close" data-do="close-layer" aria-label="Close">${icon("close")}</button>
        <div class="modal-head"><h2>Morning email</h2></div>
        <div class="modal-body"><div class="skel" style="height:60vh;border-radius:10px"></div></div></div></div>`);
    try {
      const result = await API.get(`/api/email/preview?location_id=${encodeURIComponent(S.locationId)}&date=${S.date}`);
      if (!layer.innerHTML) return;
      openLayer(`<div class="scrim" data-do="close-layer"></div>
        <div class="modal-wrap"><div class="modal wide" role="dialog" aria-modal="true" aria-label="Morning email">
          <button class="modal-close" data-do="close-layer" aria-label="Close">${icon("close")}</button>
          <div class="modal-head"><h2>${e(result.subject)}</h2></div>
          <div class="modal-body"><iframe class="emailframe" title="Email preview"></iframe></div>
        </div></div>`);
      layer.querySelector("iframe").srcdoc = result.html;
    } catch (error) { closeLayer(); toast(plainError(error), "error"); }
  }

  async function sendTest() {
    try {
      const result = await API.send(`/api/email/send-test?location_id=${encodeURIComponent(S.locationId)}`, "POST", { date: S.date });
      toast(result.status === "outbox" ? "Saved, not sent" : "Test sent");
    } catch (error) { toast(plainError(error), "error"); }
  }

  function openAdjust(target) {
    const item = S.data.items.find((row) => row.item_id === target.dataset.item);
    layer.innerHTML = `<div class="scrim" data-do="close-layer"></div>
      <div class="modal-wrap"><div class="modal">
        <button class="modal-close" data-do="close-layer">${icon("close")}</button>
        <div class="modal-head"><h2>Set your own number for ${e(target.dataset.name)}</h2>
          <p>The model's number was ${num(item?.model_expected ?? target.dataset.qty)}.</p></div>
        <form id="f-adjust" class="modal-body">
          <input type="hidden" name="item_id" value="${e(target.dataset.item)}">
          <label class="field"><span>Make this many</span><input name="quantity" type="number" min="0" max="100000" value="${e(target.dataset.qty)}" required></label>
          <label class="field"><span>Why</span><textarea name="reason" rows="3" required minlength="4" placeholder="Catering order for 30 confirmed this morning"></textarea>
            <small>Whoever opens this tomorrow will see the reason next to the number.</small></label>
          <div class="modal-foot" style="margin:6px -22px -20px">
            ${item?.override ? `<button class="btn ghost" type="button" data-do="clear-adjust" data-item="${e(target.dataset.item)}">Go back to the model</button>` : ""}
            <button class="btn accent" type="submit">Save</button>
          </div>
        </form>
      </div></div>`;
  }

  async function clearAdjust(target) {
    try {
      await API.send(`/api/forecast/override?location_id=${encodeURIComponent(S.locationId)}`, "DELETE",
        { item_id: target.dataset.item, date: S.date });
      closeLayer();
      toast("Back to the model result");
      await loadView();
    } catch (error) { toast(error.message, "error"); }
  }

  // Re-reads what an item is made of from its till label. A recipe somebody
  // confirmed is only replaced after they say so.
  async function composeItem(itemId, force, confirmed) {
    const item = (S.menu?.items || []).find((row) => row.id === itemId);
    if (force && !confirmed && recipeConfirmed(item)) {
      openLayer(`<div class="scrim" data-do="close-layer"></div>
        <div class="modal-wrap"><div class="modal" role="dialog" aria-modal="true" aria-label="Replace this recipe">
          <button class="modal-close" data-do="close-layer" aria-label="Close">${icon("close")}</button>
          <div class="modal-head"><h2>Replace your recipe?</h2>
            <p>This recipe was confirmed here. Re-reading the till label replaces it with an estimate.</p></div>
          <div class="modal-foot">
            <button class="btn ghost" type="button" data-do="close-layer">Keep mine</button>
            <button class="btn danger" type="button" data-settings="recompose-force" data-item="${e(itemId)}">Replace it</button>
          </div>
        </div></div>`);
      return;
    }
    try {
      const result = force
        ? await API.send(`/api/menu/composition?location_id=${encodeURIComponent(S.locationId)}`, "POST", { item_id: itemId })
        : await API.get(`/api/menu/composition?location_id=${encodeURIComponent(S.locationId)}&item_id=${encodeURIComponent(itemId)}`);
      if (item) item.composition = result;
      S.open.add(itemId);
      render(true);
    } catch (error) { toast(plainError(error), "error"); }
  }

  function editComposition(itemId) {
    const item = (S.menu?.items || []).find((row) => row.id === itemId);
    const comp = item?.composition;
    if (!comp) return toast("Nothing to edit yet", "error");
    const rows = comp.components && comp.components.length ? comp.components : [{}];
    openLayer(`<div class="scrim" data-do="close-layer"></div>
      <div class="modal-wrap"><div class="modal wide" role="dialog" aria-modal="true" aria-label="Edit recipe">
        <button class="modal-close" data-do="close-layer" aria-label="Close">${icon("close")}</button>
        <div class="modal-head"><h2>${e(item.normalized_name)}</h2>
          <p>What goes into one ${e(item.production_unit || "item")}. Shares are of the food cost and should add up to about 100.</p></div>
        <form id="f-composition" class="modal-body" autocomplete="off">
          <input type="hidden" name="item_id" value="${e(itemId)}">
          <label class="field"><span>In a sentence</span>
            <input name="summary" value="${e(comp.summary || "")}" maxlength="400" required></label>
          <div class="recipe-head"><span>Part</span><span>Role</span><span>Each</span><span>Share</span><span></span></div>
          <div id="recipe-rows">${rows.map(recipeRow).join("")}</div>
          <div class="btn-row" style="align-items:center">
            <button class="btn sm" type="button" data-settings="recipe-add">Add a part</button>
            <span class="small muted" id="recipe-total">Shares add up to ${Math.round(rows.reduce((n, c) => n + (Number(c.share) || 0), 0))}%</span>
          </div>
          <p class="form-error" id="recipe-error"></p>
          <div class="modal-foot" style="margin:6px -22px -20px">
            <button class="btn ghost" type="button" data-do="close-layer">Cancel</button>
            <button class="btn accent" type="submit">Save recipe</button>
          </div>
        </form>
      </div></div>`);
  }

  // Lines with no price come back flagged and are skipped when added. The
  // flag is read from whichever field the server sends, or worked out here.
  const importLineSkipped = (row) => row.ok === false || row.skipped === true || !(Number(row.price) > 0);

  async function menuImport(commit) {
    if (menuImport._busy) return;
    const box = document.getElementById("menu-text");
    const text = box?.value || "";
    if (!text.trim()) return toast("Type an item first", "error");
    const out = document.getElementById("menu-preview-out");
    menuImport._busy = true;
    const buttons = [...root.querySelectorAll('[data-do="menu-preview"], [data-do="menu-import"]')];
    buttons.forEach((button) => { button.disabled = true; });
    try {
      const result = await API.send(`/api/menu/import?location_id=${encodeURIComponent(S.locationId)}`, "POST", { text, commit });
      if (commit) {
        const skipped = Number(result.skipped || 0);
        toast(`${num(result.created || 0)} added${result.updated ? `, ${num(result.updated)} updated` : ""}${skipped ? `, ${num(skipped)} skipped` : ""}`);
        if (box) box.value = "";
        if (out) out.innerHTML = "";
        S.menu = null;
        return loadView(true);
      }
      const rows = result.preview || [];
      if (!out) return;
      out.innerHTML = `<div class="menu-preview">
        ${rows.length ? rows.map((row) => importLineSkipped(row)
          ? `<div class="menu-preview-row warn"><b>${e(row.name)}</b><span>${e(row.reason || "No price found, so this line will be skipped")}</span></div>`
          : `<div class="menu-preview-row"><b>${e(row.name)}</b><span>${e(row.category)} · ${money(row.price, true)}</span></div>`).join("")
        : `<div class="menu-preview-row warn"><span>Nothing here reads as an item. Try one per line, like Pep slice, Slices, 4.25</span></div>`}
      </div>`;
    } catch (error) { toast(plainError(error), "error"); }
    finally { menuImport._busy = false; buttons.forEach((button) => { button.disabled = false; }); }
  }

  function openLocationPicker() {
    openLayer(`<div class="scrim" data-do="close-layer"></div>
      <div class="modal-wrap"><div class="modal" role="dialog" aria-modal="true" aria-label="Switch location">
        <button class="modal-close" data-do="close-layer" aria-label="Close">${icon("close")}</button>
        <div class="modal-head"><h2>Switch location</h2></div>
        <div class="modal-body"><div style="display:grid;gap:8px">
          ${S.boot.locations.map((row) => `
            <button type="button" class="choice ${row.id === S.locationId ? "on" : ""}" data-pick-location="${e(row.id)}">
              <span class="radio"></span>
              <div><b>${e(row.name)}</b><small>${e(row.concept)} · ${e(row.city)}, ${e(row.region)}</small></div></button>`).join("")}
        </div></div>
      </div></div>`);
    layer.querySelectorAll("[data-pick-location]").forEach((node) => {
      node.addEventListener("click", async () => {
        S.locationId = node.dataset.pickLocation;
        store.set("quantify.location", S.locationId);
        resetLocationState();
        closeLayer();
        await loadView();
      });
    });
  }

  async function resendCode() {
    try {
      const result = await API.send("/api/auth/email/resend", "POST", {});
      if (result.already_verified) {
        toast("That address is already confirmed");
        return go("/app", true);
      }
      S.verification = result;
      renderConfirmEmail(result);
      toast(result.preview_code ? "New code ready below" : `Sent again to ${result.sent_to}`);
    } catch (error) { toast(error.message, "error"); }
  }

  async function openTwoStep() {
    const errorNode = document.getElementById("mfa-error");
    if (errorNode) errorNode.textContent = "";
    try {
      const setup = await API.get("/api/auth/mfa/setup");
      openLayer(`<div class="scrim" data-do="close-layer"></div>
        <div class="modal-wrap"><div class="modal wide" role="dialog" aria-modal="true" aria-label="Turn on two-step sign in">
          <button class="modal-close" data-do="close-layer" aria-label="Close">${icon("close")}</button>
          <div class="modal-head"><h2>Turn on two-step sign in</h2>
            <p>Scan the square with any authenticator app. After this, signing in asks for a six-digit code as well as your password.</p></div>
          <div class="modal-body">
            <div class="mfa-grid">
              <div class="qr">${setup.qr_svg || ""}</div>
              <div>
                <ol class="steps">
                  <li>Open Google Authenticator, 1Password, Authy, or whichever app you use.</li>
                  <li>Scan the square. If you cannot scan, type the key below instead.</li>
                  <li>Enter the six digits it shows.</li>
                </ol>
                <div class="keybox" style="margin-top:14px">
                  <code>${e(setup.secret_grouped || "")}</code>
                  <button class="icon-btn" type="button" data-do="copy" data-copy="${e(setup.secret || "")}" aria-label="Copy key">${icon("copy")}</button>
                </div>
              </div>
            </div>
            <form id="f-enable" style="margin-top:6px">
              <label class="field"><span>Code from the app</span>
                <input class="code-input" name="code" inputmode="numeric" maxlength="6" required autofocus></label>
              <div class="modal-foot" style="margin:14px -22px -20px">
                <button class="btn ghost" type="button" data-do="close-layer">Not now</button>
                <button class="btn accent" type="submit">Turn it on</button>
              </div>
            </form>
          </div>
        </div></div>`);
    } catch (error) {
      if (errorNode) errorNode.textContent = plainError(error);
      else toast(plainError(error), "error");
    }
  }

  function openTwoStepOff() {
    openLayer(`<div class="scrim" data-do="close-layer"></div>
      <div class="modal-wrap"><div class="modal" role="dialog" aria-modal="true" aria-label="Turn off two-step sign in">
        <button class="modal-close" data-do="close-layer" aria-label="Close">${icon("close")}</button>
        <div class="modal-head"><h2>Turn off two-step sign in</h2>
          <p>Your password alone will get into this account again. Enter it to confirm this is you.</p></div>
        <form id="f-disable-mfa" class="modal-body">
          <label class="field"><span>Your password</span><input name="password" type="password" autocomplete="current-password" required autofocus></label>
          <p class="form-note">Any unused backup codes stop working, and so does the entry in your authenticator app.</p>
          <div class="modal-foot" style="margin:6px -22px -20px">
            <button class="btn ghost" type="button" data-do="close-layer">Keep it on</button>
            <button class="btn danger" type="submit">Turn it off</button>
          </div>
        </form>
      </div></div>`);
  }

  async function newRecoveryCodes() {
    try {
      const result = await API.send("/api/auth/recovery-codes", "POST", {});
      openLayer(`<div class="scrim" data-do="close-layer"></div>
        <div class="modal-wrap"><div class="modal" role="dialog" aria-modal="true" aria-label="Your backup codes">
          <button class="modal-close" data-do="close-layer" aria-label="Close">${icon("close")}</button>
          <div class="modal-head"><h2>Your backup codes</h2>
            <p>Each one works once. Keep them somewhere that is not your phone. They are shown this once.</p></div>
          <div class="modal-body"><div class="codegrid">${result.codes.map((c) => `<code>${e(c)}</code>`).join("")}</div></div>
          <div class="modal-foot">
            <button class="btn" type="button" data-do="copy" data-copy="${e(result.codes.join("\n"))}">Copy all</button>
            <button class="btn primary" type="button" data-do="close-layer">Saved them</button></div>
        </div></div>`);
      S.data = null;
      await loadView(true);
    } catch (error) { toast(plainError(error), "error"); }
  }

  async function cancelConfirm() {
    const f = S.cancelFlow;
    if (!f) return;
    f.detail = document.getElementById("cancel-detail")?.value || "";
    try {
      if (f.reason) {
        // The reason is kept for whoever reads it; a failure to record it
        // must not stop the cancellation itself.
        try { await API.send("/api/billing/cancel/reason", "POST", { reason: f.reason, detail: f.detail, wants_contact: false }); }
        catch (_) { /* the plan still ends */ }
      }
      const result = await API.send("/api/billing/cancel", "POST", { immediate: false });
      f.stage = "done";
      f.message = result.message || "";
      renderCancelModal();
      S.data = null;
      await loadView(true);
    } catch (error) { toast(plainError(error), "error"); }
  }

  async function billingRedirect(path) {
    try {
      const result = await API.send(path, "POST", { plan: "standard" });
      if (result.url) window.location.href = result.url;
      else toast(result.message || "Billing is not switched on for this account yet", "error");
    } catch (error) { toast(plainError(error), "error"); }
  }

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && layer.innerHTML) closeLayer();
  });

  boot();
})();
