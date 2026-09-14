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
    unreadUpdates: 0,
  };
  let updatesUI = null;

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
  const todayISO = () => {
    const timezone = S.boot?.locations?.find(row => row.id === S.locationId)?.timezone;
    if (timezone) {
      try { return new Intl.DateTimeFormat("en-CA", { timeZone: timezone, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date()); }
      catch (_) { /* Fall back to the server date until the timezone is corrected. */ }
    }
    return S.boot?.today || localISO(new Date());
  };

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
    updates: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>',
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
    updatesUI?.stop();
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
    if (!["today", "ordering", "history", "updates", "settings"].includes(S.view)) S.view = "today";
    if (["email", "connections"].includes(S.settingsTab)) S.settingsTab = "location";
    if (S.settingsTab === "suppliers") S.settingsTab = "location";
    if (S.settingsTab === "security") S.settingsTab = "account";
    if (!["location", "menu", "costs", "account"].includes(S.settingsTab)) S.settingsTab = "location";
    store.set("quantify.stab", S.settingsTab);
    await loadView();
    startPulse();
    void getUpdatesUI().start();
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
          <p>Quantify reads the register and learns what each item sells on each kind of day. Weather, holidays and what is on nearby are checked against your own sales, and dropped when they never moved them. Count what is in the walk-in and it says what to buy, from which supplier, and by when. History compares sales with the saved opening call when one exists, and labels reconstructed comparisons.</p>
        </section>

        <section class="pricing" id="pricing">
          <h2>Choose by the number of locations</h2>
          <p>Both plans include Today, Order, History, recipes, Morning email and Updates. Square can connect; other registers use sample data until connected.</p>
          ${planChoices(show.pricing?.plans || [])}
          <p>Try it for 14 days without a card. Prices are in US dollars, billed monthly. Cancel from Account.</p>
          <a class="btn accent" href="/signup" data-link="/signup">Create account</a>
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

    if (demoTabs.tab === "updates") {
      return `${top("Updates", "Observations from the sample location")}
        <div class="stack"><section class="card">
          <div class="card-head"><div><h2>${e(dLong(b.date))}</h2><p>Today's plan</p></div></div>
          <div class="card-body"><p>${money(b.summary.expected_revenue)} expected, compared with ${money(b.comparison.sales)} on ${e(b.comparison.label)}.</p>
            <p>${num(b.summary.expected_units)} items expected to sell. Open Today to see the make list.</p></div>
        </section><section class="card"><div class="card-body"><p>Live observations appear in your workspace when its register and other sources are connected. This preview uses sample data.</p></div></section></div>`;
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

  function closingHourOptions(opens, selected) {
    const first = Number(opens) + 1;
    let closes = Number(selected);
    while (closes < first) closes += 24;
    while (closes > first + 23) closes -= 24;
    return hourOptions(closes, first, first + 23);
  }

  document.addEventListener("change", (event) => {
    const input = event.target.closest('#f-onb select[name="open_hour"], #f-location select[name="open_hour"], #f-new-location select[name="open_hour"]');
    if (!input) return;
    const form = input.closest("form"), closes = form.querySelector('[name="close_hour"]');
    if (!closes) return;
    closes.innerHTML = closingHourOptions(input.value, closes.value);
    if (form.id === "f-onb") {
      S.onboarding.values.open_hour = input.value;
      S.onboarding.values.close_hour = closes.value;
      saveOnboarding();
    }
  });

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
              <select name="open_hour">${hourOptions(values.open_hour ?? 7, 0, 23)}</select></label>
            <label><span>What time do you close?</span>
              <select name="close_hour">${closingHourOptions(values.open_hour ?? 7, values.close_hour ?? 21)}</select></label>
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
  // Daily work, ordering, results, current operating updates, and setup.
  const NAV = [
    ["today", "Today", "today"],
    ["ordering", "Order", "order"],
    ["history", "History", "history"],
    ["updates", "Updates", "updates"],
    ["settings", "Settings", "settings"],
  ];

  const currentLocation = () => S.boot?.locations?.find((row) => row.id === S.locationId) || S.boot?.locations?.[0] || {};

  function paintUpdateCount(count) {
    S.unreadUpdates = count;
    root.querySelectorAll("[data-updates-count]").forEach(node => {
      node.textContent = count > 99 ? "99+" : String(count);
      node.hidden = !count;
    });
    const button = root.querySelector('[data-view="updates"]');
    button?.setAttribute("aria-label", count ? `Updates, ${count} unread` : "Updates");
  }

  function getUpdatesUI() {
    if (!updatesUI) updatesUI = window.QuantifyUpdates.create({
      getContext: () => ({ locationId: S.locationId, view: S.view, timezone: currentLocation().timezone, userKey: S.boot?.user?.email || S.auth?.user?.email || "" }),
      get: url => API.get(url),
      post: (url, body) => API.send(url, "POST", body),
      onUnread: paintUpdateCount,
      onRender: () => { if (S.view === "updates") renderUpdates(); },
      onNavigate: async action => {
        if (!["today", "ordering", "settings", "updates"].includes(action.view)) return;
        const location = S.locationId;
        S.view = action.view;
        if (["today", "ordering"].includes(action.view)) S.date = todayISO();
        if (action.view === "settings" && ["location", "menu", "costs", "account"].includes(action.tab)) {
          S.settingsTab = action.tab; store.set("quantify.stab", action.tab);
        }
        store.set("quantify.view", S.view);
        window.scrollTo(0, 0);
        await loadView();
        if (action.item_id && S.locationId === location && S.view === action.view) await openItemSheet(action.item_id, "", todayISO());
      },
    });
    return updatesUI;
  }

  function renderUpdates() {
    const existing = root.querySelector(".updates-panel");
    const html = getUpdatesUI().panel();
    if (existing) {
      const focused = existing.contains(document.activeElement) ? document.activeElement : null;
      const focusKey = focused && [...focused.attributes].find(attr => attr.name.startsWith("data-update-"));
      const earlierOpen = existing.querySelector(".updates-earlier")?.open;
      existing.outerHTML = html;
      const next = root.querySelector(".updates-panel");
      if (earlierOpen && next.querySelector(".updates-earlier")) next.querySelector(".updates-earlier").open = true;
      if (focusKey) next.querySelector(`[${focusKey.name}="${CSS.escape(focusKey.value)}"]`)?.focus({ preventScroll: true });
      return;
    }
    root.innerHTML = shell("Updates", e(currentLocation().name), "", html);
  }

  function shell(title, subtitle, tools, body) {
    const location = currentLocation();
    const many = S.boot.locations.length > 1;
    // The Today button says which day is open when it is not today.
    const navLabel = (key, label) => (key === "today" && S.date !== todayISO() ? dShort(S.date) : label);
    const where = `<span><b>${e(location.name || "Choose a location")}</b><span>${e([location.city, location.region].filter(Boolean).join(", "))}</span></span>`;
    return `<div class="app" data-location="${e(S.locationId)}" data-context="${e(viewKey())}">
      <aside class="rail">
        <div class="rail-head">${wordmark()}</div>
        <nav class="rail-nav">
          ${NAV.map(([key, label, ico]) => `
            <button class="nav-item ${S.view === key ? "active" : ""}" data-view="${key}" ${S.view === key ? 'aria-current="page"' : ""} ${key === "updates" ? `aria-label="Updates${S.unreadUpdates ? `, ${S.unreadUpdates} unread` : ""}"` : ""}>
              <i>${icon(ico)}</i><span>${e(navLabel(key, label))}</span>${key === "updates" ? `<b class="updates-nav-count" data-updates-count ${S.unreadUpdates ? "" : "hidden"} aria-hidden="true">${S.unreadUpdates > 99 ? "99+" : S.unreadUpdates}</b>` : ""}</button>`).join("")}
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
    if (S.view === "history") return `${base}:${S.historyTab}:${S.history.range}`;
    return base;
  }
  function snapshot() {
    if (S.view === "today") return { data: S.data, attention: S.attention, outlook: S.outlook };
    if (S.view === "history") return { data: S.data, history: S.history };
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
    const app = root.querySelector(".app");
    const first = !app;
    const context = viewKey();
    const sameContext = app && app.dataset.context === context;
    const cached = S.cache[context];
    // A location change must remove the last location's editable forms at once.
    // Other navigation can keep its old content visible, but cannot act on it.
    if (first || app.dataset.location !== S.locationId) root.innerHTML = shell(viewTitle(), e(currentLocation().name), "", skeleton());
    else if (!silent && cached) { Object.assign(S, cached); render(); silent = true; }
    else if (!sameContext) root.querySelector(".content").inert = true;
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
      if (S.view === "updates") {
        await getUpdatesUI().load();
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
      if (first) fatal(error);
      else if (!sameContext) {
        root.innerHTML = shell(viewTitle(), e(currentLocation().name), "", `<section class="card" role="alert">${emptyState(
          "Could not load this page", plainError(error),
          `<button class="btn" data-view="${e(S.view)}">Try again</button>`)}</section>`);
      } else toast(plainError(error), "error");
    } finally {
      if (token === loadView._seq) {
        progress(false);
        const content = root.querySelector(".content");
        if (content) content.inert = false;
      }
    }
  }

  async function loadHistory(silent) {
    const location = S.locationId;
    const q = `location_id=${encodeURIComponent(S.locationId)}`;
    if (S.historyTab !== "accuracy") S.historyTab = "days";
    if (S.historyTab === "accuracy") {
      const key = `${location}:${todayISO()}:${S.pulse.version}`;
      S.accuracyCache = S.accuracyCache || {};
      const accuracy = S.accuracyCache[key] || await API.get(`/api/accuracy?${q}&as_of=${todayISO()}&days=30`);
      S.accuracyCache[key] = accuracy;
      if (S.view === "history" && S.historyTab === "accuracy" && S.locationId === location) S.data = accuracy;
      return;
    }
    // A fresh open starts at the newest page; a background refresh re-reads
    // the page on screen and leaves the rest of the stack alone.
    const h = historyState();
    const page = h.pages[h.at];
    if (page && page.version === S.pulse.version) { h.days = page.days; S.data = { source: page.source }; return; }
    await historyFetch(h, h.at, page ? page.before : null);
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
    if (S.view === "updates") return "Updates";
    return "Settings";
  }

  // Only ever shown when there is nothing on screen yet.
  function skeleton() {
    return `<div class="stack">
      <div class="skel" style="height:190px;border-radius:13px"></div>
      <div class="skel" style="height:72px;border-radius:13px"></div>
      <div class="skel" style="height:320px;border-radius:13px"></div>
    </div>`;
  }

  function render(preserve = false) {
    const y = window.scrollY;
    try {
      if (S.view === "today") renderToday();
      if (S.view === "history") renderHistory();
      if (S.view === "ordering") renderOrdering();
      if (S.view === "updates") renderUpdates();
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
  // One screen. The headline with its three figures, what is running low, then
  // one panel that switches between the make list, the reasons, the hours and
  // the two weeks ahead. Nothing stacks under that, so the page ends where the
  // list ends and a person at the counter never scrolls past what they need.
  const PANES = [["make", "What to make"], ["why", "Why"], ["hours", "Through the day"], ["ahead", "Next two weeks"]];

  const isPast = () => S.date < todayISO();
  // A value that is really a number, so a missing field never reads as zero.
  const has = (v) => v !== undefined && v !== null && v !== "" && Number.isFinite(Number(v));
  // Register data is live only when the last sale is from today.
  const registerCurrent = (b) => !!(b && b.data_health && b.data_health.pos_freshness === "current");
  // A location with no sales rows has nothing to plan from yet.
  const noHistory = (b) => !!(b.no_history || (!(b.items || []).length && !((b.data_health || {}).history_days)));

  function renderToday() {
    const b = S.data;
    const subtitle = e(currentLocation().name || "");
    if (noHistory(b)) {
      root.innerHTML = shell(dLong(S.date), subtitle, dateTools(), `<section class="card">${emptyState(
        "Nothing to plan yet",
        "Connect the register or add your menu and the first plan appears the next morning.",
        `<button class="btn accent" data-stab="location">Connect the register</button><button class="btn" data-stab="menu">Add your menu</button>`,
      )}</section>`);
      return;
    }
    // The stock strip is optional: if it cannot be drawn, the day still can.
    let low = "";
    try { low = runningLow(); } catch (_) { low = ""; }
    const body = `<div class="stack">
      ${isPast() ? pastBanner() : ""}
      ${headlineCard(b)}
      ${low}
      <section class="card" id="daypanes">
        <div class="card-head panehead">
          <div class="seg panes">${PANES.map(([k, l]) =>
            `<button class="${S.todayPane === k ? "on" : ""}" aria-pressed="${S.todayPane === k}" data-pane="${k}">${l}</button>`).join("")}</div>
        </div>
        ${todayPane(b)}
      </section>
    </div>`;
    root.innerHTML = shell(dLong(S.date), subtitle, dateTools(), body);
    if (isPast() && !b.past_day) fillPastHeadline(b);
  }

  // The demand tag is coloured by its level, never by the sign of a small
  // difference: a normal day is plain.
  function demandClass(level) {
    const text = String(level || "").toLowerCase();
    if (/above|strong|busy/.test(text)) return "up";
    if (/below|soft|quiet/.test(text)) return "down";
    return "plain";
  }

  function headlineCard(b) {
    const s = b.summary, cmp = b.comparison;
    const past = isPast();
    const cls = demandClass(s.demand_level);
    const synced = b.data_health && b.data_health.latest_sale_date;
    const sold = b.past_day;
    const figure = (value, label, under) =>
      `<div class="figure"><b>${e(value)}</b> <span class="flabel">${e(label)}</span><span class="under">${e(under)}</span></div>`;
    let title;
    if (!past) title = b.headline || (b.narrative && b.narrative.headline) || `A ${weekday(b.date)}`;
    else if (sold && Number(sold.units) > 0) title = `Expected ${money(s.expected_revenue)}, sold ${money(sold.sales)}`;
    else if (sold) title = `Expected ${money(s.expected_revenue)}. Nothing was recorded.`;
    else title = `Expected ${money(s.expected_revenue)}`;
    const figures = past
      ? [
        figure(money(s.expected_revenue), "expected", `${money(cmp.sales)} on ${cmp.label}`),
        figure(num(s.expected_units), "items expected", sold && Number(sold.units) > 0 ? `${num(sold.units)} sold` : `${num(cmp.units)} on ${cmp.label}`),
      ]
      : [
        figure(money(s.expected_revenue), "expected", `${money(cmp.sales)} on ${cmp.label}`),
        figure(num(makeTotal(b)), "to make", `${num(s.expected_units)} expected to sell`),
      ];
    if (s.peak_hour) figures.push(figure(s.peak_hour, "busiest", `${num(s.peak_share_percent)}% of the day`));
    const costs = b.costs;
    const profit = !past && costs && costs.configured
      ? `<p class="profit">About ${money(costs.left_after_costs)} kept after ${money(costs.cogs)} in food and ${money(costs.labour)} in wages.</p>`
      : "";
    return `<section class="headline solo">
      <div class="headline-main">
        <div class="headline-meta">
          <span class="tag ${cls} ${cls === "plain" ? "" : "dot"}">${e(s.demand_level)}</span>
          ${registerCurrent(b) || !synced ? "" : `<button class="tag warn dot" data-stab="location">Register last synced ${e(dShort(synced))}</button>`}
        </div>
        <h2 id="day-headline">${e(title)}</h2>
        <div class="figures">${figures.join("")}</div>
        ${profit}
      </div>
    </section>`;
  }

  // A day that has closed is a record, not a plan.
  function pastBanner() {
    return `<section class="card"><div class="card-body pastbanner">
      <p><b>${e(dLong(S.date))} is over.</b> What happened is in History.</p>
      <button class="btn" data-view="history">Open History</button>
    </div></section>`;
  }

  // What the day actually did, read once and kept on the brief so a repaint
  // does not lose it.
  async function fillPastHeadline(b) {
    const key = `${S.locationId}:${S.date}`;
    try {
      const d = await API.get(`/api/history/day?location_id=${encodeURIComponent(S.locationId)}&date=${S.date}`);
      if (S.view !== "today" || `${S.locationId}:${S.date}` !== key || S.data !== b) return;
      b.past_day = { sales: Number(d.sales || 0), units: Number(d.units || 0) };
      remember();
      const holder = document.querySelector(".headline.solo");
      if (holder) holder.outerHTML = headlineCard(b);
    } catch (_) { /* the plan alone is fine */ }
  }

  // Counted stock against what the next days will use. Always on the page:
  // nothing counted, nothing short, or the lines that run out first.
  function runningLow() {
    const a = S.attention;
    const head = (button, note = "") => `<div class="card-head"><div><h2>Running low</h2>${note ? `<p>${e(note)}</p>` : ""}</div><div class="spacer"></div>${button}</div>`;
    const open = `<button class="btn sm" data-view="ordering">Open the order</button>`;
    if (!a) return `<section class="card" id="runninglow">${head(open)}<div class="card-body"><p class="lowline">Stock counts are unavailable right now. Open the order to check them.</p></div></section>`;
    const lines = Array.isArray(a.lines) ? a.lines : [];
    const counted = Number(a.counted || 0) > 0 || lines.length > 0;
    if (!counted) {
      return `<section class="card" id="runninglow">${head(open)}
        <div class="card-body"><p class="lowline">Nothing has been counted yet. Count what is in the walk-in on the Order page and this fills in.</p></div>
      </section>`;
    }
    const when = countedWhen(a, lines);
    if (!lines.length) {
      const until = a.horizon || addDays(todayISO(), 7);
      return `<section class="card" id="runninglow">${head(open)}
        <div class="card-body"><p class="lowline">Nothing runs out before ${e(dMed(until))}. ${e(when)}</p></div>
      </section>`;
    }
    const rows = lines.slice(0, 3).map((r) => {
      const runs = r.runs_out_label ? `runs out ${r.runs_out_label}` : coverWords(r.days_of_cover);
      const lands = r.arrives_label ? `, lands ${r.arrives_label}` : "";
      let action;
      if (r.no_supplier || (!r.supplier && !r.order_by_label)) action = "choose a supplier";
      else if (r.late || /^now$/i.test(r.order_by_label || "")) action = `order now${lands}`;
      else if (r.order_by_label) action = `order ${/ by /.test(r.order_by_label) ? "" : "by "}${r.order_by_label}${lands}`;
      else action = `from ${r.supplier}`;
      return `<div class="lowrow ${r.late || r.no_supplier ? "late" : ""}">
        <b>${e(r.name)}</b><span>${e(runs)}</span><span class="when">${e(action)}</span>
      </div>`;
    });
    const more = lines.length > 3 ? ` ${num(lines.length - 3)} more on the Order page.` : "";
    return `<section class="card" id="runninglow">${head(open, when + more)}
      <div class="lowlist">${rows.join("")}</div>
    </section>`;
  }

  function coverWords(days) {
    const n = Number(days);
    if (!Number.isFinite(n)) return "";
    if (n < 1) return "runs out today";
    if (n < 2) return "runs out tomorrow";
    return `about ${num(n)} days left`;
  }

  // "Counted today", "Counted yesterday", "Counted Tuesday".
  function countedWhen(a, lines) {
    let age = has(a.count_age_days) ? Number(a.count_age_days) : null;
    if (age === null) {
      const ages = lines.map((r) => r.count_age_days).filter(has).map(Number);
      if (ages.length) age = Math.min(...ages);
    }
    const stamp = String(a.as_of || (lines[0] && lines[0].counted_at) || "").slice(0, 10);
    if (age === null && stamp) age = Math.round((dObj(todayISO()) - dObj(stamp)) / 86400000);
    if (age === null || !Number.isFinite(age)) return "";
    if (age <= 0) return "Counted today.";
    if (age === 1) return "Counted yesterday.";
    if (age < 7) return `Counted ${weekday(addDays(todayISO(), -age))}.`;
    return `Counted ${dShort(addDays(todayISO(), -age))}.`;
  }

  function todayPane(b) {
    if (S.todayPane === "why") return whyPane(b);
    if (S.todayPane === "hours") return hoursPane(b);
    if (S.todayPane === "ahead") return aheadPane(b);
    return makePane(b);
  }

  // Swaps only the panel body, so the page around it keeps its place.
  function repaintPane() {
    const panel = document.getElementById("daypanes");
    const head = panel && panel.querySelector(".card-head");
    if (!panel || !head || !S.data) return;
    while (head.nextSibling) head.nextSibling.remove();
    head.insertAdjacentHTML("afterend", todayPane(S.data));
  }

  function makePane(b) {
    if (!(b.items || []).length) {
      return emptyState("Nothing to make yet", "Once the register has sold something, the list appears here.");
    }
    return `${doRows(b)}<div class="tablewrap">${itemTable(b)}</div>`;
  }

  // At most two rows, only when the number to make is not the normal number.
  // Built from the make figures the server sends; without them, no rows.
  function doRows(b) {
    if (isPast()) return "";
    const items = b.items || [];
    const rows = (b.actions || [])
      .filter((row) => row.item_id && has(row.make) && has(row.normal_make) && Number(row.make) !== Number(row.normal_make))
      .slice(0, 2);
    if (!rows.length) return "";
    return `<div class="actions">${rows.map((row) => {
      const item = items.find((it) => it.item_id === row.item_id) || {};
      const diff = Number(row.make) - Number(row.normal_make);
      const name = row.item_name || item.name || "";
      const title = row.title || `Make ${num(Math.abs(diff))} ${diff > 0 ? "more" : "fewer"} ${name} than a normal ${weekday(b.date)}`;
      return `<div class="action">
        <button class="action-open" data-item-sheet="${e(row.item_id)}"><b>${e(title)}</b></button>
        ${adjustButton(item, row.item_id, name, row.make, "btn sm")}
      </div>`;
    }).join("")}</div>`;
  }

  // The Adjust button carries everything the modal needs, so it works on any
  // page and for any list.
  function adjustButton(item, itemId, name, make, cls) {
    const o = item && item.override;
    const expected = item ? (item.model_expected ?? item.expected) : "";
    return `<button class="${cls}" data-do="adjust" data-item="${e(itemId)}" data-name="${e(name)}"
      data-qty="${e(make)}" data-expected="${e(expected)}" data-override="${o ? e(o.quantity) : ""}" data-reason="${o ? e(o.reason || "") : ""}">Adjust</button>`;
  }

  function itemTable(b) {
    const past = b.date ? b.date < todayISO() : false;
    const groups = [];
    (b.items || []).forEach((item) => {
      const key = item.category || "Menu";
      let group = groups.find((g) => g.name === key);
      if (!group) { group = { name: key, rows: [] }; groups.push(group); }
      group.rows.push(item);
    });
    const cols = past ? 4 : 5;
    return `<table class="dt make"><thead><tr>
      <th>Item</th><th class="num right">Make</th><th class="num right">Expected</th><th class="num right">Normal</th>${past ? "" : "<th></th>"}</tr></thead><tbody>
      ${groups.map((g) => `${groups.length > 1 ? `<tr class="group"><td colspan="${cols}">${e(g.name)}</td></tr>` : ""}${
        g.rows.map((item) => itemRow(b, item, past, cols)).join("")}`).join("")}
    </tbody></table>`;
  }

  function itemRow(b, item, past, cols) {
    const make = item.make ?? item.expected;
    const expected = item.model_expected ?? item.expected;
    if (item.new_item) {
      return `<tr class="clickable" tabindex="0" data-item-sheet="${e(item.item_id)}" data-date="${e(b.date || todayISO())}">
        <td class="name" colspan="${cols}"><b>${e(item.name)}</b><small>New item. No number yet; after a week of sales it gets one.</small></td>
      </tr>`;
    }
    const diff = has(item.baseline) ? Number(expected) - Number(item.baseline) : 0;
    const o = item.override;
    const note = o ? `${o.updated_by ? `Set by ${o.updated_by}` : "Adjusted"}${o.reason ? `: ${o.reason}` : ""}` : "";
    const risk = Number(item.sell_out_percent) > 50 ? `<span class="tag warn">Likely to run out</span>` : "";
    return `<tr class="clickable" tabindex="0" data-item-sheet="${e(item.item_id)}" data-date="${e(b.date || todayISO())}">
      <td class="name"><b>${e(item.name)}</b>${risk}${note ? `<small>${e(note)}</small>` : ""}</td>
      <td class="num right plan" data-label="Make">${num(make)}</td>
      <td class="num right" data-label="Expected">${num(expected)}</td>
      <td class="num right" data-label="Normal">${num(item.baseline)}${Math.abs(diff) >= 3
        ? `<div class="small ${diff > 0 ? "up" : "down"}">${diff > 0 ? "+" : ""}${num(diff)}</div>` : ""}</td>
      ${past ? "" : `<td class="right">${adjustButton(item, item.item_id, item.name, make, "btn sm ghost")}</td>`}
    </tr>`;
  }

  function whyPane(b) {
    return `<div class="reasons">
      <div class="reason"><div class="reason-top"><b>How sure</b></div><p>${e(sureSentence(b))}</p></div>
      ${reasonRows(b, b.narrative)}
    </div>`;
  }

  // One sentence, in words. A written note is used only when it has no
  // percentage in it.
  function sureSentence(b) {
    const note = b.narrative && b.narrative.confidence_note;
    if (note && !/%|confiden/i.test(note)) return note;
    const word = confidenceWord(b.summary.confidence);
    const rests = noun(b.comparison.based_on_days, `past ${weekday(b.date)}`);
    if (word === "sure") return `Sure about today. It rests on ${rests} here.`;
    if (word === "fairly sure") return `Fairly sure about today. It rests on ${rests} here, so the numbers can move a little.`;
    return `Not sure about today. Only ${rests} here, so the range is wide.`;
  }

  function confidenceWord(score) {
    if (score >= 80) return "sure";
    if (score >= 65) return "fairly sure";
    return "not sure";
  }

  // Each reason: what it is, how many items it moves, one sentence. A pill
  // only when the reason itself is not sure.
  function reasonRows(b, n) {
    const signals = (b.context && b.context.signals) || [];
    const written = (n && n.factors) || [];
    const chip = (units) => (has(units) && Math.round(Number(units)) !== 0
      ? `<span class="effect-chip ${Number(units) > 0 ? "up" : "down"}">${Number(units) > 0 ? "+" : ""}${num(units)} items</span>` : "");
    const sureTag = (level) => {
      const w = String(level || "").toLowerCase();
      if (!w || w === "high") return "";
      return `<span class="tag plain">${w === "medium" ? "fairly sure" : "not sure"}</span>`;
    };
    if (written.length) {
      return written.map((row, i) => {
        const signal = signals.find((sg) => row.key && sg.key === row.key)
          || signals.find((sg) => sg.label === row.heading) || signals[i];
        return `<div class="reason">
          <div class="reason-top"><b>${e(row.heading)}</b>${signal ? chip(signal.units) : ""}${sureTag(row.confidence)}</div>
          <p>${e(row.explanation)}</p>
        </div>`;
      }).join("");
    }
    if (!signals.length) {
      return `<div class="reason"><div class="reason-top"><b>Nothing unusual</b></div>
        <p>Nothing outside moved today's number far enough to mention. Today follows this location's own ${e(weekday(b.date))} pattern.</p></div>`;
    }
    return signals.map((row) => `<div class="reason">
      <div class="reason-top"><b>${e(row.label)}</b>${chip(row.units)}</div>
      <p>${e(row.detail)}</p>
    </div>`).join("");
  }

  // The day is live only when the register is current and service is on.
  const liveState = (b) => (registerCurrent(b) && b.intraday && b.intraday.in_service ? b.intraday : null);

  function hoursPane(b) {
    const live = liveState(b);
    return `<div class="card-body">${hourChart(b, live)}</div>${live ? liveTable(b, live) : ""}`;
  }

  // Expected and sold use different hues and separate bars. Every hour
  // keeps its label and whole-item values, including on a phone.
  function hourChart(b, live) {
    const rows = b.service_curve || [];
    if (!rows.length) return `<p class="muted small">No hourly pattern yet for this location.</p>`;
    const byslot = {};
    ((live && live.hours) || []).forEach((h) => { byslot[h.slot] = h; });
    const max = Math.max(...rows.map((r) => Number(r.units || 0)), ...Object.values(byslot).map((h) => Number(h.rung_units || 0)), 1);
    const peak = rows.reduce((best, row) => (Number(row.units) > Number(best.units) ? row : best), rows[0]);
    return `<div class="chart-key hours-key"><span><i class="k-pred"></i>Expected</span>${live ? '<span><i class="k-actual"></i>Sold</span>' : '<span><i class="k-peak"></i>Busiest hour</span>'}</div><div class="hours">${rows.map((row) => {
      const h = (Number(row.units || 0) / max) * 100;
      const state = byslot[row.slot ?? row.hour];
      const done = !!(state && state.state === "done");
      const rung = done ? (Number(state.rung_units || 0) / max) * 100 : null;
      return `<div class="hourcol ${!live && row.hour === peak.hour ? "peak" : ""} ${state ? e(state.state) : ""}">
        <div class="hour-values"><b>${num(row.units)}</b>${live ? `<span>${done ? num(state.rung_units) : "-"}</span>` : ""}</div>
        <div class="track"><i style="height:${h}%;${h ? "" : "min-height:0"}"></i>${rung === null ? "" : `<i class="rung" style="height:${rung}%;${rung ? "" : "min-height:0"}"></i>`}</div>
        <span>${e(String(row.label || hourLabel(row.hour)).replaceAll(" ", "").toLowerCase())}</span>
      </div>`;
    }).join("")}</div>
    <p class="hour-note">${e(hourSentence(b, live, peak))}</p>`;
  }

  function hourSentence(b, live, peak) {
    const base = `Opens ${hourLabel(Number(b.location.open_hour))}, busiest ${peak.label} with about ${noun(peak.units, "item")}.`;
    if (live) {
      const r = live.revision;
      if (r) return `Read at ${r.label}: ${num(r.sold_units)} items sold so far against ${num(r.called_by_now_units)} expected by now.`;
      return `${base} Sold so far fills in as each hour closes.`;
    }
    if (!isPast() && b.intraday && b.intraday.in_service && b.data_health && b.data_health.latest_sale_date) {
      return `${base} Register data stops at ${dShort(b.data_health.latest_sale_date)}, so today's pace is not shown.`;
    }
    return base;
  }

  // What the register has sold so far against the morning number, item by item.
  function liveTable(b, live) {
    const r = live.revision;
    if (!r) return "";
    const ahead = r.difference_units >= 0;
    return `<div class="card-body" style="border-top:1px solid var(--line)">
      <div class="live-split">
        <div><span>Expected this morning</span><b>${num(r.opening_units)} items</b><small>${money(r.opening_sales)}</small></div>
        <div><span>Expected now</span><b>${num(r.revised_units)} items</b><small>${money(r.revised_sales)}</small></div>
        <div><span>Change</span><b class="${ahead ? "up" : "down"}">${ahead ? "+" : ""}${num(r.difference_units)} items</b>
          <small>${ahead ? "+" : ""}${money(r.difference_sales)}</small></div>
      </div>
      ${(r.items || []).length ? `<table class="dt" style="margin-top:14px"><thead><tr>
        <th>Item</th><th class="num right">Expected</th><th class="num right">Sold so far</th>
        <th class="num right">Expected now</th><th class="num right">Change</th></tr></thead><tbody>
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

  // The fourteen days after the one on screen. The brief's own week paints at
  // once; the full two weeks replace it when they arrive.
  function outlookKey() { return `${S.locationId}:${S.date}`; }

  async function loadOutlook() {
    const key = outlookKey();
    if (S.outlook && S.outlook.key === key) return;
    if (loadOutlook._inflight === key) return;
    loadOutlook._inflight = key;
    try {
      const d = await API.get(`/api/outlook?location_id=${encodeURIComponent(S.locationId)}&start=${addDays(S.date, 1)}&days=14`);
      if (outlookKey() !== key) return;
      S.outlook = { key, ...d };
      remember();
      if (S.view === "today" && S.todayPane === "ahead") repaintPane();
    } catch (error) { toast(plainError(error), "error"); }
    loadOutlook._inflight = "";
  }

  function aheadPane(b) {
    const d = S.outlook && S.outlook.key === outlookKey() ? S.outlook : null;
    const days = (d ? d.days || [] : b.week_ahead || []).filter((day) => day.date && day.date !== S.date).slice(0, 14);
    if (!days.length) return `<div class="card-body"><div class="skel" style="height:320px;border-radius:9px"></div></div>`;
    return `<div class="outlook-head"><span>Day</span><span>Expected</span><span>Biggest line</span><span>Against normal</span></div>
      ${days.map((day) => {
        const change = Number(day.revenue_change_percent ?? day.change_percent ?? 0);
        const cls = change >= 4 ? "up" : change <= -4 ? "down" : "";
        const surge = (day.top_surges || [])[0];
        const under = day.occasion_name || (day.weather?.available !== false && has(day.weather?.high) ? `${day.weather.condition}, ${day.weather.high}°` : "");
        const line = day.top_item ? (has(day.top_item_units) ? `${num(day.top_item_units)} ${day.top_item}` : day.top_item) : "";
        return `<button class="outlook-row" data-open-date="${e(day.date)}">
          <span><b>${e(dMed(day.date))}</b>${under ? `<small>${e(under)}</small>` : ""}</span>
          <span class="money"><b>${money(day.expected_revenue)}</b>${has(day.expected_units) ? `<small>${noun(day.expected_units, "item")}</small>` : ""}</span>
          <span><b>${e(line)}</b>${surge ? `<small>${e(surge.name)} ${surge.vs_baseline_units > 0 ? "+" : ""}${num(surge.vs_baseline_units)} against normal</small>` : ""}</span>
          <span class="money ${cls}"><b>${pct(change)}</b></span>
        </button>`;
      }).join("")}
      <div class="card-foot">${d ? "Tap a day to plan it." : "Tap a day to plan it. The second week is on its way."}</div>`;
  }

  // A tile: label, value, one comparison line, and a basis only when there is
  // something to say.
  function tile(label, value, versus, basis) {
    return `<div class="tile">
      <span class="eyebrow">${e(label)}</span>
      <span class="value">${e(value)}</span>
      <span class="versus">${versus}</span>
      ${basis ? `<span class="basis">${e(basis)}</span>` : ""}
    </div>`;
  }

  function makeTotal(b) {
    return (b.items || []).reduce((total, row) => total + (row.make ?? row.expected), 0);
  }

  // The -5 -1 +1 +5 steppers on the adjust modal.
  document.addEventListener("click", (event) => {
    const step = event.target.closest("[data-adjstep]");
    if (!step) return;
    const box = document.getElementById("adjust-qty");
    if (!box) return;
    box.value = Math.max(0, Math.round(Number(box.value || 0)) + Number(step.dataset.adjstep));
  });

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

  /* ---------- history ---------- */
  const RANGES = [["all", "All time"], ["year", "Past year"], ["quarter", "90 days"], ["month", "30 days"]];
  const DAYS_PER_PAGE = 14;

  // The day list is read a page at a time and kept as a stack of pages, so
  // Later never refetches and Earlier only fetches a page not seen yet. Nothing
  // loads on its own; the two buttons in the card head are the only way through.
  function historyState() {
    const range = (S.history && S.history.range) || "all";
    const key = `${S.locationId}:${range}`;
    S.historyCache = S.historyCache || {};
    if (!S.history || S.history.key !== key) S.history = S.historyCache[key] || { key, range };
    S.historyCache[key] = S.history;
    const h = S.history;
    h.range = range;
    h.days = h.days || [];
    h.pages = h.pages || [];
    h.at = h.at || 0;
    h.range = h.range || "all";
    h.costs = h.costs || null;
    h.newest = h.newest || null;
    return h;
  }

  function renderHistory() {
    if (S.historyTab !== "accuracy") S.historyTab = "days";
    const tabs = `<div class="seg">
      ${[["days", "Days"], ["accuracy", "Track record"]].map(([k, l]) =>
        `<button class="${S.historyTab === k ? "on" : ""}" aria-pressed="${S.historyTab === k}" data-htab="${k}">${l}</button>`).join("")}
    </div>`;
    const body = S.historyTab === "accuracy" ? historyAccuracy() : historyDays();
    root.innerHTML = shell("History", "", tabs, body);
  }

  function rangeBar(current, attr) {
    return `<div class="seg">${RANGES.map(([k, l]) =>
      `<button class="${current === k ? "on" : ""}" aria-pressed="${current === k}" data-${attr}="${k}">${l}</button>`).join("")}</div>`;
  }

  // Reads one page of days into the stack and makes it the page on screen.
  async function historyFetch(h, at, before) {
    const location = S.locationId, version = S.pulse.version;
    const token = h.request = (h.request || 0) + 1;
    const q = `location_id=${encodeURIComponent(S.locationId)}&limit=${DAYS_PER_PAGE}`;
    const start = rangeStart(h.range);
    const page = await API.get(`/api/history/days?${q}${before ? `&before=${before}` : ""}${start ? `&start=${start}` : ""}`);
    if (token !== h.request) return;
    h.pages[at] = { before, days: page.days || [], nextBefore: page.next_before || null, hasMore: !!page.has_more, version, source: page.source };
    h.at = at;
    h.days = h.pages[at].days;
    h.costs = page.costs || h.costs || null;
    h.newest = page.newest_sale_date || null;
    if (S.view === "history" && S.historyTab === "days" && S.history === h && S.locationId === location) S.data = { source: page.source };
  }

  // Earlier and Later. Later always comes from memory; Earlier fetches only
  // when the next page has not been read yet.
  async function historyPage(dir) {
    const h = historyState();
    if (h.loading) return;
    const next = h.at + dir;
    if (next < 0) return;
    if (h.pages[next]) {
      h.at = next;
      h.days = h.pages[next].days;
    } else {
      const current = h.pages[h.at];
      if (!current || !current.hasMore || !current.nextBefore) return;
      h.loading = true; progress(true);
      try { await historyFetch(h, next, current.nextBefore); }
      catch (error) { toast(plainError(error), "error"); }
      h.loading = false; progress(false);
    }
    if (S.view === "history" && S.historyTab === "days" && S.history === h) {
      remember();
      render();
      window.scrollTo(0, 0);
    }
  }

  // The newest day with sales on the first page, for when the API does not
  // say. Pages are newest first, so it is the first row that was open.
  function newestSaleDate(days) {
    const open = (days || []).find((row) => !row.closed);
    return open ? open.date : null;
  }

  // A run of days with nothing recorded becomes one row, so a fortnight with
  // the register unplugged does not push the real days off the page.
  function collapseClosed(days) {
    const out = [];
    (days || []).forEach((day) => {
      const last = out[out.length - 1];
      if (day.closed && last && last.closed && addDays(day.date, 1) === last.from) {
        last.from = day.date;
        last.count += 1;
        return;
      }
      out.push(Object.assign({}, day, { from: day.date, to: day.date, count: 1 }));
    });
    return out;
  }

  function historyDays() {
    const h = historyState();
    const rows = collapseClosed(h.days);
    const page = h.pages[h.at] || { hasMore: false };
    const withCosts = !!h.costs && h.costs.configured !== false;
    const newest = h.newest || newestSaleDate((h.pages[0] || {}).days);
    const quiet = newest && newest < addDays(todayISO(), -1);
    const canEarlier = page.hasMore || h.at + 1 < h.pages.length;
    let list;
    if (rows.length) {
      list = `<div class="dayrow head">
          <span class="when">Day</span><span class="cell">Sold</span>
          <span class="cell">${withCosts ? "Left after costs" : "Items"}</span>
          <span class="accmeter">Accuracy</span><span class="chev"></span>
        </div>${rows.map((row) => dayRow(row, withCosts)).join("")}`;
    } else if (h.at > 0) {
      list = emptyState("No more days", "The earliest day on record is on the page before this one.", `<button class="btn sm" data-do="days-later">Later</button>`);
    } else if (h.range === "all") {
      list = emptyState("No closed days yet", "The first day appears the morning after the first full day of sales.", `<a class="btn sm" href="/app" data-stab="location">Connect the register</a>`);
    } else {
      list = emptyState("No closed days in this range", "Pick a wider range to see earlier days.", `<button class="btn sm" data-drange="all">Show all time</button>`);
    }
    return `<div class="stack">
      <section class="card">
        <div class="card-head days">
          <div><h2>Days</h2></div>
          <div class="spacer"></div>
          ${rangeBar(h.range, "drange")}
          <div class="pager">
            <button class="btn sm" data-do="days-earlier" ${canEarlier ? "" : "disabled"}>Earlier</button>
            <button class="btn sm" data-do="days-later" ${h.at > 0 ? "" : "disabled"}>Later</button>
          </div>
        </div>
        ${quiet ? `<p class="quiet-note">Nothing has come in from the register since ${e(dShort(newest))}. <a href="/app" data-stab="location">Check the connection</a>.</p>` : ""}
        <div id="dayrows">${list}</div>
        ${withCosts && rows.length ? `<div class="card-foot">${costsFoot(h.costs)}</div>` : ""}
      </section>
    </div>`;
  }

  function dayRow(day, withCosts) {
    if (day.closed) {
      const run = day.count > 1;
      return `<div class="dayrow closed">
        <span class="when"><b>${e(run ? `${dShort(day.from)} to ${dShort(day.to)}` : dShort(day.date))}</b><small>${e(run ? noun(day.count, "day") : weekday(day.date))}</small></span>
        <span class="closed-note"><b>Not open</b><span>${run ? "Nothing recorded on these days" : "Nothing recorded"}</span></span>
      </div>`;
    }
    const acc = day.accuracy === null || day.accuracy === undefined ? null : Number(day.accuracy);
    const cls = acc === null ? "" : acc < 70 ? "low" : acc < 80 ? "mid" : "";
    const c = day.costs;
    return `<button class="dayrow" data-day-detail="${e(day.date)}">
      <span class="when"><b>${e(dShort(day.date))}</b><small>${e(weekday(day.date))}</small></span>
      <span class="cell" data-label="Sold"><b>${money(day.sales)}</b><small>${noun(day.orders, "ticket")}</small></span>
      ${withCosts
        ? `<span class="cell" data-label="Left after costs">${c && c.complete !== false && c.left_after_costs != null
          ? `<b>${money(c.left_after_costs)}</b><small>${Math.round(Number(c.margin_percent || 0))}% of sales</small>`
          : `<b class="muted">Not worked out</b><small>costs missing for this day</small>`}</span>`
        : `<span class="cell" data-label="Items"><b>${num(day.units)}</b><small>${noun(day.distinct_items, "different item")}</small></span>`}
      <span class="accmeter" data-label="Accuracy">
        ${acc === null
          ? `<span class="top"><b class="muted">Not scored yet</b><small>scored overnight</small></span>`
          : `<span class="top"><b>${Math.round(acc)}%</b><small>${num(day.predicted_units)} expected, ${num(day.units)} sold</small></span>
             <span class="line"><i class="${cls}" style="width:${Math.max(4, Math.min(100, acc))}%"></i></span>`}
      </span>
      <span class="chev">${icon("chevR")}</span>
    </button>`;
  }

  // One sentence under the day list saying what Left after costs rests on.
  function costsFoot(summary) {
    if (!summary) return "";
    const w = summary.wage || {};
    return summary.configured && w.loaded != null
      ? `Left after costs uses the <a href="/app" data-stab="costs">wages and costs you entered</a>.`
      : `Left after costs needs your actual pay and employer costs. <a href="/app" data-stab="costs">Add them in Costs</a>.`;
  }

  // "Low by about 7 items a day": the lean the API names when it names one,
  // otherwise worked out from the counts it sends; the size from the item's
  // own miss across the window.
  function usually(row, days) {
    const perDay = Math.round((Number(row.wape || 0) / 100) * Number(row.actual_units || 0) / Math.max(1, days));
    let lean = row.usually || row.lean || row.bias_direction || "";
    if (!lean && typeof row.bias === "number") lean = row.bias > 0 ? "high" : row.bias < 0 ? "low" : "";
    if (!lean && row.predicted_units != null && row.actual_units != null) {
      const diff = Number(row.predicted_units) - Number(row.actual_units);
      const steady = Math.abs(diff) >= Math.max(1, Number(row.actual_units) * 0.02);
      lean = steady ? (diff > 0 ? "high" : "low") : "no steady lean";
    }
    lean = String(lean).replace(/^usually\s+/i, "").trim().toLowerCase();
    const size = perDay > 0 ? `about ${noun(perDay, "item")} a day` : "less than one a day";
    if (!lean || lean === "no steady lean") return `Off by ${size}`;
    return `${lean[0].toUpperCase()}${lean.slice(1)} by ${size}`;
  }

  function historyAccuracy() {
    const d = S.data || {};
    const summary = d.summary || {};
    const n = Number(summary.days_evaluated || 0);
    if (!n) {
      return `<div class="stack"><section class="card">${emptyState("No closed days yet", "The first day appears the morning after the first full day of sales.")}</section></div>`;
    }
    const trend = d.trend || {};
    const series = (trend.series || []).slice(-n);
    const scored = series.map((r) => Number(r.accuracy)).filter((v) => !Number.isNaN(v));
    const within = scored.filter((v) => v >= 90).length;
    const best = scored.length ? Math.round(Math.max(...scored)) : null;
    const worst = scored.length ? Math.round(Math.min(...scored)) : null;
    const basis = `Over the last ${noun(n, "closed day")}`;
    const acc = Math.round(Number(summary.forecast_accuracy || 0));
    const previous = summary.previous_accuracy;
    const daily = d.daily || [];
    const gaps = daily.map((r) => Math.abs(Number(r.actual) - Number(r.predicted)));
    const typicalGap = gaps.length ? Math.round(gaps.reduce((a, b) => a + b, 0) / gaps.length) : 0;
    const items = d.item_accuracy || [];
    const shown = items.slice(0, 8);
    const rest = items.length - shown.length;
    const restFloor = rest > 0 ? Math.round(Math.min(...items.slice(8).map((r) => Number(r.accuracy)))) : null;
    return `<div class="stack">
      <section class="tiles two">
        ${tile("Right, item by item", `${acc}%`,
          previous !== undefined && previous !== null
            ? `${Math.round(Number(previous))}% the ${noun(n, "day")} before.`
            : scored.length ? `Best day ${best}%, worst ${worst}%.` : `A day within 10% counts as right.`,
          basis)}
        ${tile("Days within 10%", scored.length ? `${Math.round((within / scored.length) * 100)}%` : "Not scored yet",
          scored.length ? `${within} of ${noun(scored.length, "day")}.` : `Scores fill in as days close.`,
          scored.length && scored.length !== n ? `Over the last ${noun(scored.length, "scored day")}` : basis)}
      </section>

      <section class="card">
        <div class="card-head"><div><h2>Expected against sold</h2>
          <p>${!daily.length ? "Nothing to draw yet."
            : typicalGap > 0 ? `Sold usually lands within about ${noun(typicalGap, "item")} of expected.` : "Sold usually lands within an item of expected."}</p></div></div>
        <div class="card-body">${lineChart(daily)}</div>
      </section>

      <section class="card">
        <div class="card-head"><div><h2>Where it misses</h2>
          <p>Worst first. An item that keeps missing the same way usually means a recipe, a portion or a price changed.</p></div></div>
        ${shown.length ? `<div class="tablewrap"><table class="dt stack misses"><thead><tr>
          <th>Item</th><th class="num right">Right</th><th>Usually</th></tr></thead><tbody>
          ${shown.map((row) => `<tr class="clickable" data-item-sheet="${e(row.item_id)}" data-name="${e(row.name)}">
            <td class="name"><b>${e(row.name)}</b></td>
            <td class="num right" data-label="Right">${Math.round(Number(row.accuracy))}%</td>
            <td data-label="Usually">${e(usually(row, n))}</td>
          </tr>`).join("")}
        </tbody></table></div>
        ${rest > 0 ? `<div class="card-foot">The other ${noun(rest, "item")} ${rest === 1 ? "is" : "are"} right at least ${restFloor}% of the time.</div>` : ""}`
        : `<div class="card-body muted small">No item has enough sales to score yet.</div>`}
      </section>
    </div>`;
  }

  // SVG only draws the lines. HTML labels keep their fixed type size when
  // the plot narrows, and non-scaling strokes stay two screen pixels wide.
  function lineChart(rows) {
    if (!rows || !rows.length) return `<p class="muted small">No closed days in this window yet.</p>`;
    const W = 900, H = 200;
    const max = Math.max(...rows.flatMap((r) => [Number(r.actual), Number(r.predicted)]), 1) * 1.08;
    const x = (i) => 4 + (i * (W - 8)) / Math.max(1, rows.length - 1);
    const y = (v) => H - 4 - (Number(v) / max) * (H - 8);
    const line = (key) => rows.map((r, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(r[key]).toFixed(1)}`).join(" ");
    const grids = [0, 0.5, 1].map((f) => `<line class="grid" x1="0" y1="${y(max * f)}" x2="${W}" y2="${y(max * f)}"/>`).join("");
    const last = rows[rows.length - 1];
    return `<div class="linechart"><p class="plot-unit">Items a day</p>
      <div class="plot-grid"><div class="plot-axis"><span>${num(max)}</span><span>${num(max / 2)}</span><span>0</span></div>
        <div class="plot-area"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Expected in blue dashes against sold in black, items a day">
          ${grids}<path class="predicted" d="${line("predicted")}"/><path class="actual" d="${line("actual")}"/>
        </svg><i class="plot-point" style="left:${x(rows.length - 1) / W * 100}%;top:${y(last.actual) / H * 100}%"></i></div>
        <div class="plot-dates"><span>${e(dShort(rows[0].date))}</span><span>${e(dShort(rows[Math.floor((rows.length - 1) / 2)].date))}</span><span>${e(dShort(last.date))}</span></div>
      </div><div class="chart-key"><span><i class="k-actual"></i>Sold</span><span><i class="k-pred"></i>Expected</span></div>
      <p class="plot-last">${e(dShort(last.date))}: ${num(last.actual)} sold against ${num(last.predicted)} expected.</p></div>`;
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
  function orderSnapshot(g) {
    return { location: S.locationId, date: S.date, days: S.order.days,
      edits: S.order.edits, quantities: Object.fromEntries(g.lines.map(line => [lineKey(line), S.order.edits[lineKey(line)]])),
      extras: new Set(g.extras) };
  }

  function afterOrder(sent) {
    if (S.locationId !== sent.location) return;
    if (S.order.edits === sent.edits && S.date === sent.date && S.order.days === sent.days) {
      Object.entries(sent.quantities).forEach(([key, value]) => {
        if (S.order.edits[key] === value) delete S.order.edits[key];
      });
    }
    S.order.extras = S.order.extras.filter(row => !sent.extras.has(row));
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
    const sent = orderSnapshot(g);
    try {
      const r = await logOrder(id, "email", g);
      if (S.locationId !== loc || S.view !== "ordering") return;
      const status = r.order && r.order.status;
      if (status !== "sent") {
        S.order.log = null;
        return toast(r.message || "The order was not sent. Try again.", "error", true);
      }
      toast("Sent");
      return afterOrder(sent);
    } catch (error) { return toast(error.message, "error"); }
  }

  async function supplyPlaced(id, channel) {
    const loc = S.locationId;
    const g = findGroup(id);
    if (!g || !g.supplier) return;
    const sent = orderSnapshot(g), sheetToken = openLayer._seq;
    try {
      const r = await logOrder(id, channel, g, channel === "mail-app" ? { confirmed: true } : {});
      if (S.locationId !== loc || S.view !== "ordering") return;
      if (sheetToken === openLayer._seq) closeLayer();
      const lands = r.order && r.order.expected_on ? whenLabel(r.order.expected_on) : "";
      toast(lands ? `Noted, lands ${lands}` : "Noted");
      return afterOrder(sent);
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
    if (log.error) return `<p class="small">Orders could not be loaded.</p><button type="button" class="btn sm" data-supply="orders-retry">Try again</button>`;
    if (!log.rows.length) return `<p class="small muted">Nothing sent yet. Orders you email or place show here.</p>`;
    return `<div class="srows">${log.rows.map(orderLogRow).join("")}</div>`;
  }

  async function loadOrderLog(force = false) {
    const loc = S.locationId;
    if (!force && S.order.log && S.order.log.for === loc && !S.order.log.error) return;
    if (S.order.logFor === loc && !force) return;
    S.order.logFor = loc;
    try {
      const r = await API.get(`/api/supply/orders?${locQ()}`);
      if (S.locationId !== loc) return;
      S.order.log = { for: loc, rows: r.orders || [] };
    } catch (_) {
      if (S.locationId !== loc) return;
      S.order.log = { for: loc, rows: [], error: true };
    } finally {
      if (S.order.logFor === loc) S.order.logFor = "";
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
        if (S.view !== "ordering" || S.locationId !== loc || S.date !== start || S.order.days !== days) return;
        const saved = (r && r.saved && r.saved[0]) || r || {};
        let line = saved.line && saved.line.suggested ? saved.line : (saved.suggested ? saved : null);
        if (!line) {
          // Until the count route answers with the line, the list is read
          // again and just this row is taken from it.
          const plan = await API.get(`/api/ordering?${query}&start=${start}&days=${days}`);
          if (S.view !== "ordering" || S.locationId !== loc || S.date !== start || S.order.days !== days) return;
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
    if (kind === "orders-retry") return loadOrderLog(true);
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
            <label class="field"><span>Opens</span><select name="open_hour">${hourOptions(l.open_hour, 0, 23)}</select></label>
            <label class="field"><span>Closes</span><select name="close_hour">${closingHourOptions(l.open_hour, l.close_hour)}</select></label>
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
          <details class="cost-reference"><summary>Wage and payroll references${reference.place ? ` for ${e(reference.place)}` : ""}</summary><div class="cost-reference-body">
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
    const location = S.locationId;
    const form = document.getElementById("f-costs");
    if (!form) return;
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
      const saved = await API.send(`/api/costs?location_id=${encodeURIComponent(location)}`, "PUT", body);
      if (location !== S.locationId || !form.isConnected) return;
      S.costs = saved;
      const skipped = Array.isArray(saved.skipped) ? saved.skipped : [];
      toast(skipped.length ? `Saved, skipped ${skipped.join(", ")}` : "Saved");
      render(true);
    } catch (error) {
      if (location !== S.locationId || !form.isConnected) return;
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

  function planChoices(plans, billing = null) {
    if (!plans.length) return `<p class="muted">Plan details could not be loaded. Refresh to try again.</p>`;
    return `<div class="plan-options">${plans.map((plan) => {
      const current = billing?.plan?.code === plan.code;
      const fits = !billing || (billing.location_allowance?.used || 0) <= plan.max_locations;
      let action = "";
      if (billing) {
        if (current) action = `<span class="plan-choice-note">Current ${billing.status === "trialing" ? "trial " : ""}plan</span>`;
        else if (!fits) action = `<span class="plan-choice-note">Your active locations exceed this plan.</span>`;
        else if (billing.can_select_trial_plan) action = `<button class="btn" type="button" data-do="billing-trial-plan" data-plan="${e(plan.code)}">Use during trial</button>`;
        else if (billing.has_subscription && billing.provider?.connected) action = `<button class="btn" type="button" data-do="billing-portal">Review in Manage billing</button>`;
        else if (plan.checkout_ready) action = `<button class="btn" type="button" data-do="billing-checkout" data-plan="${e(plan.code)}">Continue to payment</button>`;
        else action = `<span class="plan-choice-note">Payments are not set up for this plan yet.</span>`;
      }
      return `<article class="plan-option"><h3>${e(plan.name)}</h3>
        <p class="plan-amount">${money(plan.monthly)}<span> per month</span></p>
        <p>${plan.max_locations === 1 ? "One active location." : `Up to ${num(plan.max_locations)} active locations, ${money(plan.monthly / plan.max_locations)} each when all are used.`}</p>
        <p>All operating tools included.</p>${action}</article>`;
    }).join("")}</div>`;
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
        <div class="card-head"><div><h2>${e(plan.name)}</h2><p>${e(plan.blurb)}</p></div></div>
        <div class="plancard">
          <div>
            <div class="price">${money(plan.monthly_total)}<span>${plan.legacy ? " listed monthly price" : " per month for this account"}</span></div>
            <p class="small muted">${num(billing.location_allowance?.used || 0)} active ${billing.location_allowance?.used === 1 ? "location" : "locations"}${billing.location_allowance?.limit ? ` of ${num(billing.location_allowance.limit)} included` : " on your existing allowance"}.</p>
            ${plan.legacy ? `<p class="small muted">Your existing price and access stay as they are until you choose a change.</p>` : ""}
            ${connected && state ? `<p class="plan-state">${e(state)}</p>` : ""}
          </div>
          ${connected ? `<div class="plan-side">
            ${billing.payment_method
              ? `<div class="plan-cardbox">
                   <div class="eyebrow">Card on file</div>
                   <div style="margin-top:4px;font-weight:600">${e(billing.payment_method.brand || "Card")} ending ${e(billing.payment_method.last4)}</div>
                   <div class="small muted">Expires ${e(billing.payment_method.expires || "")}</div></div>`
              : `<div class="small muted">No card on file yet.</div>`}
            ${billing.has_subscription ? `<button class="btn" type="button" data-do="billing-portal">Manage billing</button>` : ""}
            ${!billing.has_subscription && !cancelling && billing.checkout_ready ? `<button class="btn" type="button" data-do="billing-checkout" data-plan="${e(plan.code)}">Continue to payment</button>` : ""}
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

      <section class="card">
        <div class="card-head"><div><h2>Plans</h2><p>All operating tools are included. Choose by the number of active locations.</p></div></div>
        <div class="card-body">${planChoices(billing.plans || [], billing)}</div>
        <div class="card-foot">Monthly prices in US dollars. A trial choice takes no payment. Paid changes are confirmed through Manage billing.</div>
      </section>

      <section class="card">
        <div class="card-head"><div><h2>Locations</h2><p>Each location has its own register, menu, hours and history.</p></div></div>
        <div class="card-body"><div class="btn-row">
          <button class="btn" type="button" data-do="location-add" ${billing.location_allowance?.remaining === 0 ? "disabled" : ""}>Add location</button>
        </div>${billing.location_allowance?.remaining === 0 ? `<p class="small muted">All ${num(billing.location_allowance.limit)} included locations are in use. Choose a larger plan above to add another.</p>` : ""}</div>
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

  function openAddLocation() {
    if (!toastNode.classList.contains("error") && !toastNode.classList.contains("hold")) toastNode.className = "toast";
    openLayer(`<div class="scrim" data-do="close-layer"></div>
      <div class="modal-wrap"><div class="modal" role="dialog" aria-modal="true" aria-label="Add location">
        <button class="modal-close" data-do="close-layer" aria-label="Close">${icon("close")}</button>
        <div class="modal-head"><h2>Add location</h2><p>Starts with an empty menu and history. Connect its register or add items in Settings.</p></div>
        <form id="f-new-location" class="modal-body">
          <label class="field"><span>Location name</span><input name="name" required minlength="2" maxlength="120"></label>
          <label class="field"><span>What you serve</span><input name="concept" required maxlength="120" placeholder="Bakery, cafe, pizza shop"></label>
          <div class="form-grid two">
            <label class="field"><span>City</span><div class="typeahead"><input name="place" required data-typeahead autocomplete="off" placeholder="Denver"></div></label>
            <label class="field"><span>State or region</span><input name="region" maxlength="40" placeholder="CO"></label>
          </div>
          <details><summary class="btn ghost">Set the time zone yourself</summary><label class="field"><span>Time zone</span><input name="timezone" placeholder="Mountain time"></label></details>
          <div class="form-grid two">
            <label class="field"><span>Opens</span><select name="open_hour">${hourOptions(7, 0, 23)}</select></label>
            <label class="field"><span>Closes</span><select name="close_hour">${closingHourOptions(7, 21)}</select></label>
          </div>
          <p class="small muted">Weather and nearby events wait until this place is matched.</p>
          <p class="form-error" id="new-location-error"></p>
          <div class="btn-row"><button class="btn ghost" type="button" data-do="close-layer">Cancel</button><button class="btn accent" type="submit">Add location</button></div>
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
    const form = field.closest("form"), location = S.locationId, place = pick.dataset.place;
    const text = form.querySelector("#tz-input"), hidden = form.querySelector("input[name=timezone]");
    const region = form.querySelector('[name="region"]');
    const previousText = text?.value, previousZone = hidden?.value, previousRegion = region?.value;
    const sequence = form._cityLookup = (form._cityLookup || 0) + 1;
    API.get(`/api/timezone?q=${encodeURIComponent(place)}`).then((r) => {
      if (!r.match?.confident || !form.isConnected || location !== S.locationId || sequence !== form._cityLookup) return;
      if (field.value.trim() !== place.trim() || text?.value !== previousText || hidden?.value !== previousZone) return;
      if (region?.value !== previousRegion) return;
      if (r.match.city) field.value = r.match.city;
      if (region && r.match.region) region.value = r.match.region;
      if (text) text.value = r.match.label || r.match.timezone;
      if (hidden) hidden.value = r.match.timezone;
      const hint = form.querySelector("#tz-hint");
      if (hint) hint.innerHTML = "";
    }).catch(() => {});
  });

  // The forms that belong to Settings modals and inline edits. The shared
  // submit handler already stops the page reload and re-enables the button.
  document.addEventListener("submit", async (event) => {
    const form = event.target;
    if (!["f-name", "f-password", "f-square", "f-new-location"].includes(form.id)) return;
    event.preventDefault();
    if (form.dataset.saving) return;
    form.dataset.saving = "true";
    const button = form.querySelector("button[type=submit]");
    if (button) button.disabled = true;
    const data = Object.fromEntries(new FormData(form).entries());
    const say = (id, text) => { const node = document.getElementById(id); if (node) node.textContent = text; };
    try {
      if (form.id === "f-new-location") {
        say("new-location-error", "");
        const result = await API.send("/api/locations", "POST", data);
        S.locationId = result.location.id; store.set("quantify.location", S.locationId);
        S.boot = await API.get(`/api/bootstrap?location_id=${encodeURIComponent(S.locationId)}`);
        S.date = S.boot.today; resetLocationState(); closeLayer();
        S.settingsTab = "location"; store.set("quantify.stab", "location");
        toast("Location added. Connect its register to bring in its menu and sales.");
        return loadView();
      }
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
      if (form.id === "f-new-location") return say("new-location-error", text);
      toast(text, "error");
    } finally {
      delete form.dataset.saving;
      if (button) button.disabled = false;
    }
  });

  /* ---------- one item, in full ---------- */
  // One sheet for any item, however quiet: how many to make and why, in the
  // words a cook would use. The record behind the number sits behind one fold.
  // The same item, date and data version paint from memory the second time.
  function itemCacheKey(itemId, date) {
    return `${S.locationId}:${itemId}:${date}:${S.pulse.version}`;
  }

  function forgetItem(itemId) {
    S.itemCache = S.itemCache || {};
    Object.keys(S.itemCache).forEach((key) => { if (key.includes(`:${itemId}:`)) delete S.itemCache[key]; });
  }

  // The name is known before the request: the row that was tapped carries
  // it, or the make list does. "Loading" is the last resort.
  function itemNameHint(itemId) {
    const hint = S.sheetHint;
    if (hint && hint.id === itemId && hint.name) return hint.name;
    const row = ((S.data && S.data.items) || []).find((item) => item.item_id === itemId);
    return row ? row.name : "";
  }

  function itemSheetShell(itemId, head, body, date) {
    return `<div class="scrim" data-do="close-layer"></div>
      <aside class="sheet item" role="dialog" aria-modal="true" aria-label="Item" data-item="${e(itemId)}" data-date="${e(date)}">
        <div class="sheet-head"><div>${head}</div>
          <button class="icon-btn" data-do="close-layer" aria-label="Close">${icon("close")}</button></div>
        <div class="sheet-body">${body}</div>
      </aside>`;
  }

  function itemSheetHead(p) {
    return `<h2>${e(p.item.name)}</h2>
      <p>${e(p.item.category)} · ${money(p.item.price, true)} · ${e(p.weekday)} ${e(dShort(p.date))}</p>`;
  }

  async function openItemSheet(itemId, name, date) {
    S.itemCache = S.itemCache || {};
    date = date || (S.sheetHint && S.sheetHint.id === itemId && S.sheetHint.date) || (S.view === "today" ? S.date : todayISO());
    const location = S.locationId;
    const key = itemCacheKey(itemId, date);
    const cached = S.itemCache[key];
    if (cached) { openLayer(itemSheetShell(itemId, itemSheetHead(cached), itemSheetBody(cached), date)); return; }
    const label = name || itemNameHint(itemId);
    openLayer(itemSheetShell(itemId, `<h2>${e(label || "Loading")}</h2><p>${label ? "Loading" : ""}</p>`, "", date));
    const token = openLayer._seq;
    try {
      const p = await API.get(`/api/item?location_id=${encodeURIComponent(location)}&item_id=${encodeURIComponent(itemId)}&date=${date}`);
      S.itemCache[key] = p;
      if (token !== openLayer._seq || location !== S.locationId) return;
      openLayer(itemSheetShell(itemId, itemSheetHead(p), itemSheetBody(p), date));
    } catch (error) {
      if (token !== openLayer._seq || location !== S.locationId) return;
      toast(plainError(error), "error");
      closeLayer();
    }
  }

  function itemSheetBody(p) {
    const t = p.today || {}, prep = p.prep || {}, dist = p.distribution || {}, standing = p.standing || {};
    const fresh = p.new_item === true || (Number(standing.history_days) || 0) < 7 || !Number(dist.days)
      || prep.quantity === undefined || prep.quantity === null;
    if (fresh) return `<section class="isec"><p class="lede">New item. No number yet; after a week of sales it gets one.</p></section>`;

    const day = p.weekday || weekday(p.date);
    const brief = ((S.data && S.data.items) || []).find((row) => row.item_id === p.item.id);
    const override = t.override && t.override.quantity !== undefined && t.override.quantity !== null ? t.override : null;
    const expected = Math.round(Number(t.model_expected ?? (brief && brief.model_expected) ?? t.expected ?? 0));
    const suggested = Math.round(Number(prep.quantity ?? expected));
    const make = override ? Math.round(Number(override.quantity)) : suggested;
    const normal = Math.round(Number(t.normal ?? 0));
    const low = Math.round(Number(dist.today_low ?? t.low ?? 0));
    const high = Math.round(Number(dist.today_high ?? t.high ?? 0));
    const canAdjust = p.date >= todayISO();
    const when = p.date === todayISO() ? "today" : `on ${dShort(p.date)}`;
    const callSentence = `${canAdjust ? "Expected" : e(t.call_label || "Reconstructed expectation")}: ${num(expected)} ${e(when)}.`;
    const share = Number(prep.cost_share_percent);
    const price = Number(p.item.price || 0);
    const costs = Number.isFinite(share) && price > 0
      ? { waste: price * share / 100, miss: price * (100 - share) / 100 } : null;
    const lastSale = p.last_sale_date ? String(p.last_sale_date).slice(0, 10) : "";
    const stale = lastSale && addDays(lastSale, 2) < p.date;

    const setBy = override
      ? `${override.updated_by ? `${e(override.updated_by)} set` : "You set"} ${num(make)}${setWhen(override.updated_at)}${override.reason ? `: ${e(override.reason)}` : ""}. Quantify would have said ${num(suggested)}.`
      : "";

    const top = `<section class="isec">
      <div class="isec-head"><h2>${canAdjust ? "Make" : override ? "Saved plan" : "Reconstructed plan"} ${num(make)} ${e(when)}</h2>
        ${canAdjust ? `<button class="btn sm" data-do="adjust" data-date="${e(p.date)}" data-item="${e(p.item.id)}" data-name="${e(p.item.name)}" data-qty="${make}" data-expected="${expected}" data-suggested="${suggested}" data-override="${override ? e(override.quantity) : ""}" data-reason="${override ? e(override.reason || "") : ""}">Adjust</button>` : ""}
      </div>
      ${override
        ? `<p class="lede">${setBy} ${callSentence}</p>
           ${canAdjust ? `<div class="btn-row isec-row"><button class="btn sm ghost" data-do="clear-adjust" data-item="${e(p.item.id)}">Use ${num(suggested)}</button></div>` : ""}`
        : `<p class="lede">A normal ${e(day)} sells ${num(normal)}. ${callSentence} With ${num(make)}, you run out ${runOutWords(prep.sell_out_percent)}; anywhere from ${num(low)} to ${num(high)} would still be a normal ${e(day)}.</p>`}
      ${costs ? `<p class="small muted isec-note">Wasting one costs about ${money(costs.waste, true)}. Missing a sale costs about ${money(costs.miss, true)}.</p>` : ""}
      ${stale ? `<p class="small muted isec-note">Register data stops at ${e(dShort(lastSale))}.</p>` : ""}
    </section>`;

    const levels = Array.isArray(prep.levels) ? prep.levels : [];
    const picked = ["Ten fewer", "This number", "Ten more"].map((label) => levels.find((l) => l.label === label)).filter(Boolean);
    const fewerOrMore = picked.length === 3 ? `<section class="isec"><h2>If you make fewer or more</h2>
      <table class="dt tight"><thead><tr><th>If you make</th><th>You run out</th><th class="num right">Costs you about</th></tr></thead><tbody>
        ${picked.map((l) => `<tr class="${l.label === "This number" ? "highlight" : ""}">
          <td class="num">${num(l.quantity)}</td><td>${e(runOutWords(l.sell_out_percent))}</td>
          <td class="num right">${money(l.cost_of_being_wrong)}</td></tr>`).join("")}
      </tbody></table></section>` : "";

    const wp = p.weekday_profile || {};
    const days = (wp.days || []).filter((d) => d.days);
    const week = days.length ? `<section class="isec"><h2>Through the week</h2>
      <div class="wk">${days.map((d) => `<div class="wk-day ${d.weekday === day ? "on" : ""}"><span>${e(String(d.weekday).slice(0, 3))}</span><b>${num(d.typical)}</b></div>`).join("")}</div>
      <p class="small muted">${e(weekSentence(wp, days))}</p></section>` : "";

    const hourly = p.hourly || {};
    const hourPart = (hourly.hours || []).length ? `<section class="isec"><h2>When it sells</h2>
      <p class="lede">${e(itemHourSentence(hourly))}</p>${hourShape(hourly)}</section>` : "";

    const comp = p.composition;
    const parts = comp && Array.isArray(comp.components) ? comp.components : [];
    const confirmed = !!(comp && (comp.confirmed === true || comp.status === "confirmed" || comp.source === "confirmed"));
    const menuLink = `<a href="#" data-stab="menu">Settings > Menu</a>`;
    const prepFor = `<section class="isec"><h2>Prep for ${num(make)}</h2>
      ${parts.length ? `<table class="dt tight"><thead><tr><th>Part</th><th>Each</th><th class="num right">For ${num(make)}</th></tr></thead><tbody>
        ${parts.map((c) => { const total = scaleQuantity(c.quantity, make); return `<tr>
          <td class="name">${e(c.name)}</td><td class="muted">${e(c.quantity || "not stated")}</td>
          <td class="num right">${total ? e(total) : `<span class="muted">not counted</span>`}</td></tr>`; }).join("")}
        </tbody></table>
        <p class="small muted">${confirmed ? `Confirmed in ${menuLink}.` : `Read from the till label. Confirm it in ${menuLink}.`}</p>`
      : `<p class="lede">No recipe yet. Add one in ${menuLink}.</p>`}
    </section>`;

    const tr = p.trend || {};
    const moved = tr.moved === true && tr.recent_average !== null && tr.recent_average !== undefined && Number(tr.prior_average) > 0;
    const trendPart = moved ? `<section class="isec"><h2>${Number(tr.difference) < 0 ? "It has slowed down" : "It has picked up"}</h2>
      <p class="lede">The last ${noun(tr.recent_days, "day")} averaged ${num(tr.recent_average)} a day against ${num(tr.prior_average)} in ${Number(tr.prior_days) >= 26 ? "the four weeks before" : `the ${noun(tr.prior_days, "day")} before that`}.${tr.same_weeks_last_year ? ` The same weeks last year averaged ${num(tr.same_weeks_last_year)}.` : ""}</p></section>` : "";

    const paragraphs = [
      driversPara(p.drivers || []), callsPara(p.accuracy || {}), alongsidePara(p.related || []),
      oddDaysPara(p.unusual || [], p.date), standingPara(standing),
    ].filter(Boolean);
    const more = paragraphs.length ? `<details class="fold"><summary>More about this item</summary>
      <div class="fold-body">${paragraphs.map((text) => `<p>${text}</p>`).join("")}</div></details>` : "";

    return top + fewerOrMore + week + hourPart + prepFor + trendPart + more;
  }

  // "1 day in 3" reads better than 33%, and it is what a cook will repeat.
  function runOutWords(percent) {
    const value = Number(percent);
    if (!Number.isFinite(value)) return "";
    if (value < 3) return "almost never";
    if (value > 97) return "almost every day";
    const options = [[1, 2], [1, 3], [2, 3], [1, 4], [3, 4], [1, 5], [2, 5], [3, 5], [4, 5], [1, 6], [5, 6],
      [1, 8], [7, 8], [1, 10], [9, 10], [1, 20], [19, 20]];
    let best = options[0];
    options.forEach((option) => {
      if (Math.abs(option[0] / option[1] * 100 - value) < Math.abs(best[0] / best[1] * 100 - value)) best = option;
    });
    return `${best[0]} ${best[0] === 1 ? "day" : "days"} in ${best[1]}`;
  }

  function setWhen(updatedAt) {
    const when = String(updatedAt || "").slice(0, 10);
    if (!when) return "";
    return when === todayISO() ? " today" : ` on ${dShort(when)}`;
  }

  function weekSentence(wp, days) {
    const best = days.find((d) => d.weekday === wp.busiest_day);
    const worst = days.find((d) => d.weekday === wp.quietest_day);
    if (!best || !worst || !Number(worst.typical)) return "";
    const ratio = Number(best.typical) / Number(worst.typical);
    if (ratio >= 2.5) return `${best.weekday}s sell more than twice what ${worst.weekday}s do.`;
    if (ratio >= 1.75) return `${best.weekday}s sell almost twice what ${worst.weekday}s do.`;
    if (ratio >= 1.12) return `${best.weekday}s sell about ${num(best.typical - worst.typical)} more than ${worst.weekday}s.`;
    return "It sells about the same every day of the week.";
  }

  // The rush: the hours around the peak that carry at least six tenths of it.
  // hourLabel is the shared helper from the onboarding block.
  function rushWords(hours) {
    let peakAt = 0;
    hours.forEach((row, index) => { if (row.share_percent > hours[peakAt].share_percent) peakAt = index; });
    const floor = hours[peakAt].share_percent * 0.6;
    let a = peakAt, b = peakAt;
    while (a > 0 && hours[a - 1].share_percent >= floor && hours[a - 1].hour === hours[a].hour - 1) a -= 1;
    while (b < hours.length - 1 && hours[b + 1].share_percent >= floor && hours[b + 1].hour === hours[b].hour + 1) b += 1;
    const start = Number(hours[a].hour) % 24, end = (Number(hours[b].hour) + 1) % 24;
    if (a === b) return hourLabel(start);
    const from = hourLabel(start), to = hourLabel(end);
    return from.slice(-2) === to.slice(-2) ? `${from.slice(0, -3)} to ${to}` : `${from} to ${to}`;
  }

  function itemHourSentence(hourly) {
    const hours = hourly.hours || [];
    const half = hourly.half_sold_by ? `Half are gone by ${hourly.half_sold_by}. ` : "";
    return `${half}The rush is ${rushWords(hours)}.`;
  }

  // Whole items per hour, with the busiest hour picked out in green.
  function hourShape(hourly) {
    const rows = (hourly && hourly.hours) || [];
    if (!rows.length) return "";
    const peak = rows.reduce((best, row) => (row.share_percent > best.share_percent ? row : best), rows[0]);
    return `<p class="shape-unit">Items per hour · Green marks the busiest hour</p><div class="shape">${rows.map((row) => `<div class="shape-col ${row === peak ? "peak" : ""}">
        <b class="shape-value">${num(row.per_day ?? row.units ?? 0)}</b>
        <div class="shape-track"><i style="--h:${Math.round((row.share_percent / Math.max(1, peak.share_percent)) * 100)}%;${Number(row.share_percent) ? "" : "min-height:0"}"></i></div>
        <span class="shape-lab">${e(String(row.label || "").replaceAll(" ", "").toLowerCase())}</span></div>`).join("")}</div>`;
  }

  const listWords = (items) => (items.length <= 1 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`);

  function driversPara(drivers) {
    if (!drivers.length) return "";
    const live = drivers.filter((d) => d.matters && Math.abs(Math.round(Number(d.today_effect_units) || 0)) >= 1);
    if (!live.length) return "Weather, daylight, holidays and what is on nearby were all checked. None of them changes this item by more than one a day.";
    return live.slice(0, 3).map((d) => {
      const units = Math.round(Number(d.today_effect_units));
      return `${e(d.label)} ${units > 0 ? "adds" : "takes off"} about ${num(Math.abs(units))} today.`;
    }).join(" ");
  }

  function callsPara(acc) {
    if (!acc || !acc.days) return "";
    const lean = /low/i.test(acc.bias_direction || "") ? "usually low"
      : /high/i.test(acc.bias_direction || "") ? "usually high" : "with no steady lean";
    const miss = Math.round(Number(acc.average_miss) || 0);
    return `Over the last ${noun(acc.days, "day")} the number for this item has been within about ${num(miss)} a day, ${lean}.`
      + (acc.sold_out_days ? ` It ran out on ${noun(acc.sold_out_days, "of those days")}.` : " It has not run out.");
  }

  function alongsidePara(related) {
    const link = (r) => `<button class="textlink" data-item-sheet="${e(r.item_id)}" data-name="${e(r.name)}">${e(r.name)}</button>`;
    const rises = related.filter((r) => Number(r.r) > 0).slice(0, 3);
    const against = related.filter((r) => Number(r.r) < 0).slice(0, 2);
    const parts = [];
    if (rises.length) parts.push(`${listWords(rises.map(link))} ${rises.length === 1 ? "rises and falls" : "rise and fall"} with it.`);
    against.forEach((r) => parts.push(`People pick between it and ${link(r)}.`));
    return parts.join(" ");
  }

  function oddCause(note) {
    const text = String(note || "").trim().replace(/\.$/, "");
    if (!text || /nothing in the (data|record)/i.test(text)) return "";
    const lower = text.charAt(0).toLowerCase() + text.slice(1);
    if (/rain/i.test(text)) return `, in ${lower}`;
    if (/nearby/i.test(text)) return ", with something big on nearby";
    if (/ran out/i.test(text)) return ", and it ran out during service";
    return `, ${lower}`;
  }

  // At most three days that did something unusual, each with a cause.
  function oddDaysPara(unusual, dateISO) {
    const rows = unusual.filter((u) => oddCause(u.note))
      .sort((a, b) => Math.abs(Number(b.sigma) || 0) - Math.abs(Number(a.sigma) || 0)).slice(0, 3);
    if (!rows.length) return "";
    const year = String(dateISO || "").slice(0, 4);
    return rows.map((u) => {
      const units = Math.round(Number(u.above_normal) || 0);
      const sameYear = String(u.date).slice(0, 4) === year;
      const when = dFmt(u.date, sameYear
        ? { weekday: "short", month: "short", day: "numeric" }
        : { weekday: "short", month: "short", day: "numeric", year: "numeric" });
      return `${e(when)} sold ${num(u.sold)}, about ${num(Math.abs(units))} ${units >= 0 ? "more" : "fewer"} than a normal ${e(u.weekday)}${e(oddCause(u.note))}.`;
    }).join(" ");
  }

  function standingPara(standing) {
    if (!standing || !standing.of_items || standing.revenue_share_percent === undefined) return "";
    const share = Math.round(Number(standing.revenue_share_percent) || 0);
    const rank = Number(standing.revenue_rank);
    return `About ${share}% of takings over the last 90 days, ${rank === 1 ? `the biggest seller of ${num(standing.of_items)}` : `ranked ${num(rank)} of ${num(standing.of_items)}`}.`;
  }

  // Multiplies a per-item amount up to the batch. A range stays a range
  // ("1 to 2 patties" for 81 is "81 to 162 patties"), a weight climbs a unit
  // when it passes one, and an amount with no number stays blank.
  const COUNTABLE = { slice: "slices", piece: "pieces", patty: "patties", bun: "buns", egg: "eggs",
    set: "sets", portion: "portions", box: "boxes", bag: "bags", carton: "cartons", scoop: "scoops",
    coat: "coats", plate: "plates", wrap: "wraps", sleeve: "sleeves", ball: "balls", lid: "lids",
    straw: "straws", cup: "cups", pinch: "pinches", leaf: "leaves", basket: "baskets", tray: "trays",
    sheet: "sheets", shot: "shots", pump: "pumps", dash: "dashes", handful: "handfuls", strip: "strips",
    rasher: "rashers", fillet: "fillets", breast: "breasts", thigh: "thighs", wing: "wings" };

  function scaledUnit(total, unitText) {
    let unit = String(unitText || "").toLowerCase().trim();
    let value = total;
    const words = unit.split(/\s+/);
    const first = words[0] || "";
    if (first === "g" && total >= 1000) { value = total / 1000; unit = "kg"; }
    else if (first === "ml" && total >= 1000) { value = total / 1000; unit = "l"; }
    else if (first === "oz" && total >= 16) { value = total / 16; unit = "lb"; }
    else if (words.length === 1) {
      const singular = Object.keys(COUNTABLE).find((key) => first === key || first === COUNTABLE[key]);
      if (singular) unit = Math.round(value) === 1 ? singular : COUNTABLE[singular];
    }
    const rounded = value >= 10 ? Math.round(value) : Math.round(value * 10) / 10;
    return { value: rounded.toLocaleString("en-US"), unit };
  }

  function scaleQuantity(quantity, batch) {
    const text = String(quantity || "").trim();
    const match = text.match(/^(?:about|roughly|around)?\s*([\d.]+)(?:\s*(?:to|-)\s*([\d.]+))?\s*(.*)$/i);
    const count = Number(batch || 0);
    if (!match || !count || /share/i.test(match[3])) return "";
    const lo = Number(match[1]) * count;
    const hi = match[2] ? Number(match[2]) * count : null;
    if (!Number.isFinite(lo) || lo <= 0) return "";
    const a = scaledUnit(lo, match[3]);
    if (hi === null || !Number.isFinite(hi) || hi <= lo) return `${a.value} ${a.unit}`.trim();
    const b = scaledUnit(hi, match[3]);
    return a.unit === b.unit ? `${a.value} to ${b.value} ${b.unit}`.trim() : `${a.value} ${a.unit} to ${b.value} ${b.unit}`.trim();
  }

  // Region-local wiring. The tapped row lends its name to the loading state,
  // Adjust from the sheet asks for the sheet back, a link to Settings closes
  // the sheet first, and the tickets fold loads its page when opened.
  document.addEventListener("click", (event) => {
    const opener = event.target.closest("[data-item-sheet]");
    if (opener) {
      const cell = opener.tagName === "TR" ? opener.querySelector("td.name b") : null;
      const name = opener.dataset.name || (cell ? cell.textContent : "") || "";
      const context = opener.closest(".sheet[data-date]");
      S.sheetHint = { id: opener.dataset.itemSheet, name: String(name).trim(),
        date: opener.dataset.date || (context && context.dataset.date) || (S.view === "today" ? S.date : todayISO()) };
    }
    const moreTickets = event.target.closest("[data-tickets-more]");
    if (moreTickets) {
      const box = moreTickets.closest("details[data-tickets]");
      if (box) loadTickets(box, { before: moreTickets.dataset.before, skip: moreTickets.dataset.skip });
      return;
    }
    const sheet = event.target.closest(".sheet.item");
    if (!sheet) return;
    if (event.target.closest("a[data-stab]")) closeLayer();
  }, true);

  document.addEventListener("toggle", (event) => {
    const box = event.target;
    if (box && box.matches && box.matches("details[data-tickets]") && box.open && !box.dataset.loaded) loadTickets(box, null);
  }, true);

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
      body: "Each closed day shows what sold and how close the opening call was. If no opening call was saved, History labels the comparison as reconstructed. Add your actual pay and payroll costs to see what the day kept.",
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
  // One closed day: what sold against what was expected, where the call
  // missed, and the tickets behind it, loaded only when asked for.
  function daySheetShell(dateISO, sub, body) {
    return `<div class="scrim" data-do="close-layer"></div>
      <aside class="sheet day" role="dialog" aria-modal="true" aria-label="Day" data-date="${e(dateISO)}">
        <div class="sheet-head"><div><h2>${e(dLong(dateISO))}</h2><p>${sub}</p></div>
          <button class="icon-btn" data-do="close-layer" aria-label="Close">${icon("close")}</button></div>
        <div class="sheet-body">${body}</div>
      </aside>`;
  }

  async function openDaySheet(dateISO) {
    openLayer(daySheetShell(dateISO, "Loading", ""));
    const token = openLayer._seq, location = S.locationId;
    try {
      const d = await API.get(`/api/history/day?location_id=${encodeURIComponent(location)}&date=${dateISO}`);
      if (token !== openLayer._seq || location !== S.locationId) return;
      const closed = d.closed === true || (!Number(d.units) && !Number(d.orders));
      const sub = closed ? "Not open" : `${money(d.sales)} · ${noun(d.orders, "ticket")}`;
      openLayer(daySheetShell(dateISO, sub, closed
        ? `<section class="isec"><p class="lede">Nothing was recorded on this day.</p></section>`
        : daySheetBody(d, dateISO)));
    } catch (error) {
      if (token !== openLayer._seq || location !== S.locationId) return;
      toast(plainError(error), "error");
      closeLayer();
    }
  }

  function daySheetBody(d, dateISO) {
    const r = d.review || {};
    const day = d.weekday || weekday(dateISO);
    const normal = Number(d.normal_sales);
    let sold = `${money(d.sales)} sold`;
    if (Number.isFinite(normal) && normal > 0) {
      const diff = Number(d.sales) - normal;
      sold += Math.abs(diff) < normal * 0.02
        ? `, about the same as a normal ${day}.`
        : `, about ${money(Math.abs(diff))} ${diff > 0 ? "more" : "less"} than a normal ${day}.`;
    } else {
      sold += ` on ${noun(d.orders, "ticket")}.`;
    }
    const items = d.predicted_units !== null && d.predicted_units !== undefined
      ? `${num(d.units)} items sold against ${num(d.predicted_units)} expected.`
      : `${num(d.units)} items sold.`;
    const c = d.costs;
    const kept = c && c.left_after_costs !== undefined && c.left_after_costs !== null
      ? `Kept about ${money(c.left_after_costs)} after ${money(c.cogs)} in food and ${money(c.labour)} in wages.`
      : `Wages and money kept need your actual pay and employer costs. <a href="/app" data-stab="costs">Add them in Settings > Costs</a>.`;
    const went = `<section class="isec"><h2>How the day went</h2>
      <p class="lede">${e(sold)} ${e(items)}</p>
      ${kept ? `<p class="lede isec-note">${kept}</p>` : ""}
      ${r.matters ? `<p class="lede isec-note">${e(r.matters)}</p>` : ""}
    </section>`;

    const scores = Array.isArray(d.item_scores) ? d.item_scores : [];
    const scored = scores.map((row) => {
      const predicted = Math.round(Number(row.predicted) || 0), actual = Math.round(Number(row.actual) || 0);
      return { ...row, predicted, actual, gap: actual - predicted };
    });
    const missed = scored.filter((row) => Math.abs(row.gap) >= 5 || row.sold_out)
      .sort((a, b) => Math.abs(b.gap) - Math.abs(a.gap));
    const rest = scored.length - missed.length;
    const missedPart = scored.length ? `<section class="isec"><h2>Where the call missed</h2>
      ${missed.length ? `<table class="dt tight"><thead><tr>
          <th>Item</th><th class="num right">Expected</th><th class="num right">Sold</th><th class="num right">Gap</th></tr></thead><tbody>
        ${missed.map((row) => `<tr class="clickable" tabindex="0" data-item-sheet="${e(row.item_id)}" data-name="${e(row.name)}" data-date="${e(dateISO)}">
          <td class="name"><b>${e(row.name)}</b>${row.sold_out ? `<small class="down">Ran out during service</small>` : ""}</td>
          <td class="num right">${num(row.predicted)}</td><td class="num right">${num(row.actual)}</td>
          <td class="num right ${row.gap > 0 ? "up" : row.gap < 0 ? "down" : ""}">${row.gap > 0 ? "+" : ""}${num(row.gap)}</td></tr>`).join("")}
        </tbody></table>
        ${rest > 0 ? `<p class="small muted">The other ${noun(rest, "item")} ${rest === 1 ? "was" : "were"} within 4.</p>` : ""}`
        : `<p class="lede">Every item landed within 4 of expected.</p>`}
    </section>` : "";

    const busiest = d.busiest_hour;
    const busyPart = busiest && busiest.label ? `<section class="isec"><h2>Busiest hour</h2>
      <p class="lede">${e(busiest.label)} was the busiest hour, with ${num(busiest.actual)} items sold${busiest.predicted !== undefined && busiest.predicted !== null ? ` against ${num(busiest.predicted)} expected for that hour` : ""}.</p></section>` : "";

    const paragraphs = [channelSentence(d), weatherLine(d.conditions), reasonLine(r)].filter(Boolean);
    const more = paragraphs.length ? `<details class="fold"><summary>More about this day</summary>
      <div class="fold-body">${paragraphs.map((text) => `<p>${text}</p>`).join("")}</div></details>` : "";

    const tickets = `<details class="fold" data-tickets="${e(dateISO)}"><summary>Tickets</summary><div class="fold-body"></div></details>`;
    return went + missedPart + busyPart + more + tickets;
  }

  // Where the tickets came from, as a share of tickets, in one sentence.
  function channelSentence(d) {
    const rows = (d.channels || []).filter((c) => Number(c.orders) > 0).sort((a, b) => Number(b.orders) - Number(a.orders));
    const total = rows.reduce((n, c) => n + Number(c.orders), 0);
    if (!total) return "";
    const words = { counter: "at the counter", pickup: "pickup", delivery: "delivery", "dine in": "dine in",
      online: "online", phone: "by phone", kiosk: "at the kiosk" };
    const parts = rows.map((c, index) => {
      const key = String(c.channel || "").toLowerCase();
      return `${Math.round(Number(c.orders) / total * 100)}%${index === 0 ? " were" : ""} ${e(words[key] || key)}`;
    });
    return `Of ${noun(total, "ticket")}, ${listWords(parts)}.`;
  }

  function weatherLine(cond) {
    if (!cond || !cond.weather) return "";
    let text = String(cond.weather);
    if (cond.high !== undefined && cond.high !== null && cond.low !== undefined && cond.low !== null) {
      text += `, high of ${Math.round(cond.high)} and low of ${Math.round(cond.low)}`;
    }
    const rain = Number(cond.rain_mm) || 0;
    text += rain >= 1 ? `, ${Math.round(rain)} mm of rain.` : ".";
    if (cond.occasion) text += ` ${cond.occasion}.`;
    if (Number(cond.events) > 0) text += ` ${noun(cond.events, "event")} nearby.`;
    return e(text);
  }

  // The reason only when it names a cause.
  function reasonLine(r) {
    const text = String((r && r.likely_reason) || "").trim();
    if (!text || /nothing in the day|ordinary variation|no clear cause|not explain|nothing explains/i.test(text)) return "";
    return e(text);
  }

  function ticketRow(order) {
    const lines = (order.lines || []).map((line) => `${line.name}${Number(line.quantity) > 1 ? ` x${num(line.quantity)}` : ""}`).join(", ");
    const head = [clock(order.time), order.number, order.channel].filter(Boolean).join(" · ");
    return `<div class="ticket"><div class="t-main"><b>${e(head)}</b><span>${e(lines)}</span></div>
      <span class="t-amt">${money(order.total, true)}</span></div>`;
  }

  // Twenty tickets at a time, newest first, for the day the sheet shows.
  async function loadTickets(box, cursor) {
    const date = box.dataset.tickets;
    const body = box.querySelector(".fold-body");
    if (!date || !body) return;
    const more = body.querySelector("[data-tickets-more]");
    if (more) more.disabled = true;
    try {
      const before = cursor && cursor.before ? cursor.before : date;
      const skip = cursor ? Number(cursor.skip) || 0 : 0;
      const page = await API.get(`/api/history/orders?location_id=${encodeURIComponent(S.locationId)}&start=${date}&before=${before}&skip=${skip}&limit=20`);
      if (more) more.closest(".btn-row").remove();
      const rows = (page.orders || []).map(ticketRow).join("");
      if (rows) body.insertAdjacentHTML("beforeend", rows);
      else if (!body.querySelector(".ticket")) body.insertAdjacentHTML("beforeend", `<p class="small muted">No tickets were recorded for this day.</p>`);
      if (page.has_more && page.next_before_date) {
        body.insertAdjacentHTML("beforeend", `<div class="btn-row"><button class="btn sm" data-tickets-more data-before="${e(page.next_before_date)}" data-skip="${Number(page.next_skip) || 0}">Show 20 more</button></div>`);
      } else if (rows) {
        body.insertAdjacentHTML("beforeend", `<p class="small muted">Totals include tax.</p>`);
      }
      box.dataset.loaded = "1";
    } catch (error) {
      if (more) more.disabled = false;
      toast(plainError(error), "error");
    }
  }

  /* ---------- layers ---------- */
  // Every sheet and modal opens through here so it gets focus on open and
  // gives it back on close. Screens still setting layer.innerHTML directly
  // are converted by their own region; new code uses openLayer.
  function openLayer(html) {
    openLayer._seq = (openLayer._seq || 0) + 1;
    if (!openLayer._from) openLayer._from = document.activeElement;
    layer.innerHTML = html;
    const first = layer.querySelector("[autofocus], input:not([type=hidden]), textarea, select, button:not(.modal-close):not(.scrim)");
    if (first) { try { first.focus({ preventScroll: true }); } catch (_) { /* nothing focusable */ } }
  }

  function closeLayer() {
    if (S.tour) return endTour(false);
    openLayer._seq = (openLayer._seq || 0) + 1;
    layer.innerHTML = "";
    S.adjustReturn = null;
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
    updatesUI?.reset();
    S.date = todayISO();
    S.costs = null; S.menu = null; S.supply = null; S.attention = null; S.outlook = null; S.data = null;
    S.order.edits = {}; S.order.extras = [];
    S.narrativeTried = ""; S.itemCache = {}; S.open = new Set();
    S.history.costs = null; S.pulse.version = null; S.pulse.pending = false;
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
      if (S.view === "ordering" && S.date < todayISO()) {
        S.date = todayISO();
        S.order.edits = {};
      }
      store.set("quantify.view", S.view);
      window.scrollTo(0, 0);
      return loadView();
    }
    if (target.dataset.htab) { S.historyTab = target.dataset.htab; return loadView(); }
    if (target.dataset.owin) { S.order.days = Number(target.dataset.owin); S.order.edits = {}; return loadView(); }
    if (target.dataset.stab) {
      if (layer.contains(target)) closeLayer();
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
        head.querySelectorAll("[data-pane]").forEach((node) => {
          const selected = node.dataset.pane === S.todayPane;
          node.classList.toggle("on", selected);
          node.setAttribute("aria-pressed", String(selected));
        });
        while (head.nextSibling) head.nextSibling.remove();
        head.insertAdjacentHTML("afterend", todayPane(S.data));
      } else render(true);
      if (S.todayPane === "ahead") loadOutlook();
      return;
    }
    if (target.dataset.drange) { S.history.range = target.dataset.drange; return loadView(); }
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
      case "days-earlier": return historyPage(1);
      case "days-later": return historyPage(-1);
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
      case "billing-checkout": return billingRedirect("/api/billing/checkout", target.dataset.plan);
      case "location-add": return openAddLocation();
      case "billing-trial-plan":
        if (target.disabled) return;
        target.disabled = true;
        try {
          const result = await API.send("/api/billing/plan", "POST", { plan: target.dataset.plan });
          toast(result.message); S.data = null; await loadView(true);
        } catch (error) { toast(plainError(error), "error"); }
        finally { target.disabled = false; }
        return;
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
    if (["f-name", "f-password", "f-square", "f-new-location"].includes(form.id)) return;
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
        const location = S.locationId;
        // One Save for the whole tab: the location, then the morning email.
        // The time zone box shows a readable label; the id behind it is only
        // replaced when somebody typed something else.
        const setup = (S.data && S.data.setup) || {};
        const shownLabel = (setup.timezone && setup.timezone.label) || (setup.location && setup.location.timezone) || "";
        const typed = String(data.timezone_text || "").trim();
        const place = { name: data.name, concept: data.concept, city: data.city, region: data.region,
          open_hour: data.open_hour, close_hour: data.close_hour,
          timezone: typed && typed !== shownLabel ? typed : data.timezone };
        const saved = await API.send(`/api/location?location_id=${encodeURIComponent(location)}`, "POST", place);
        const enabled = !!(form.elements.enabled && form.elements.enabled.checked);
        const address = String(data.owner_email || "").trim();
        await API.send(`/api/email/preferences?location_id=${encodeURIComponent(location)}`, "POST",
          { owner_email: address, send_time: data.send_time || "05:30", enabled, include_week_ahead: true });
        if (location !== S.locationId || !form.isConnected) return;
        if (saved && saved.timezone && saved.timezone.confident === false && typed && typed !== shownLabel) {
          toast("Saved, but that time zone was not recognised. Try a city or ZIP code.", "error");
        } else toast("Saved");
        const bootstrap = await API.get(`/api/bootstrap?location_id=${encodeURIComponent(location)}`);
        if (location !== S.locationId || !form.isConnected) return;
        S.boot = bootstrap;
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
        const back = S.adjustReturn, token = openLayer._seq, location = S.locationId;
        await API.send(`/api/forecast/override?location_id=${encodeURIComponent(location)}`, "POST",
          { item_id: data.item_id, date: data.date, quantity: Number(data.quantity), reason: String(data.reason || "").trim() });
        forgetItem(data.item_id);
        if (token !== openLayer._seq || location !== S.locationId) return;
        closeLayer();
        const closedToken = openLayer._seq;
        toast("Adjusted");
        if (S.view === "today") await loadView(true);
        if (back && closedToken === openLayer._seq && location === S.locationId) openItemSheet(back.item, back.name, back.date);
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
    const location = S.locationId, view = S.view, tab = S.settingsTab;
    target.disabled = true;
    target.textContent = "Working";
    try {
      await API.send(`/api/integrations/${provider}/sync?location_id=${encodeURIComponent(location)}`, "POST",
        { days: provider === "pos" ? 1095 : provider === "events" ? 90 : 16, backfill_days: 1095 });
      if (location !== S.locationId || view !== S.view || tab !== S.settingsTab || !target.isConnected) return;
      toast("Synced");
      // Settings may contain a draft even after its fields lose focus. The
      // next view reads fresh data without replacing this form underneath it.
      S.pulse.pending = true;
      if (view !== "settings") { S.data = null; await loadView(true); }
    } catch (error) {
      if (location === S.locationId && view === S.view && tab === S.settingsTab && target.isConnected) toast(plainError(error), "error");
    } finally {
      if (target.isConnected) { target.disabled = false; target.textContent = label; }
    }
  }

  async function previewEmail() {
    openLayer(`<div class="scrim" data-do="close-layer"></div>
      <div class="modal-wrap"><div class="modal wide" role="dialog" aria-modal="true" aria-label="Morning email">
        <button class="modal-close" data-do="close-layer" aria-label="Close">${icon("close")}</button>
        <div class="modal-head"><h2>Morning email</h2></div>
        <div class="modal-body"><div class="skel" style="height:60vh;border-radius:10px"></div></div></div></div>`);
    const token = openLayer._seq, location = S.locationId;
    try {
      const result = await API.get(`/api/email/preview?location_id=${encodeURIComponent(S.locationId)}&date=${S.date}`);
      if (token !== openLayer._seq || location !== S.locationId) return;
      openLayer(`<div class="scrim" data-do="close-layer"></div>
        <div class="modal-wrap"><div class="modal wide" role="dialog" aria-modal="true" aria-label="Morning email">
          <button class="modal-close" data-do="close-layer" aria-label="Close">${icon("close")}</button>
          <div class="modal-head"><h2>${e(result.subject)}</h2></div>
          <div class="modal-body"><iframe class="emailframe" title="Email preview"></iframe></div>
        </div></div>`);
      layer.querySelector("iframe").srcdoc = result.html;
    } catch (error) { if (token === openLayer._seq && location === S.locationId) { closeLayer(); toast(plainError(error), "error"); } }
  }

  async function sendTest() {
    try {
      const result = await API.send(`/api/email/send-test?location_id=${encodeURIComponent(S.locationId)}`, "POST", { date: S.date });
      toast(result.status === "outbox" ? "Saved, not sent" : "Test sent");
    } catch (error) { toast(plainError(error), "error"); }
  }

  // The adjust modal works from the button's own data attributes, so it opens
  // from the make list, from a "do this" row and from the item sheet on any
  // page, whether or not the brief is in memory.
  function openAdjust(target) {
    const d = target.dataset;
    const sheet = target.closest(".sheet.item");
    const date = d.date || (sheet && sheet.dataset.date) || S.date || todayISO();
    if (date < todayISO()) return;
    S.adjustReturn = sheet ? { item: sheet.dataset.item, date, name: d.name || "" } : null;
    const item = ((S.data && S.data.items) || []).find((row) => row.item_id === d.item) || null;
    const ok = (v) => v !== undefined && v !== null && v !== "" && Number.isFinite(Number(v));
    const qty = ok(d.qty) ? Number(d.qty) : Number((item && (item.make ?? item.expected)) || 0);
    const expected = ok(d.expected) ? Number(d.expected) : Number((item && (item.model_expected ?? item.expected)) ?? qty);
    const override = ok(d.override) ? Number(d.override) : (item && item.override ? Number(item.override.quantity) : null);
    const suggested = ok(d.suggested) ? Number(d.suggested) : (override === null ? qty : null);
    const reason = d.reason || (item && item.override && item.override.reason) || "";
    const lead = override !== null
      ? `You set ${num(override)}. About ${num(expected)} are expected to sell.`
      : `Quantify suggested ${num(suggested)}. About ${num(expected)} are expected to sell.`;
    openLayer(`<div class="scrim" data-do="close-layer"></div>
      <div class="modal-wrap"><div class="modal" role="dialog" aria-modal="true" aria-labelledby="adjust-title">
        <button class="modal-close" data-do="close-layer" aria-label="Close">${icon("close")}</button>
        <div class="modal-head"><h2 id="adjust-title">Adjust ${e(d.name || (item && item.name) || "")}</h2>
          <p>${e(lead)}</p></div>
        <form id="f-adjust" class="modal-body">
          <input type="hidden" name="item_id" value="${e(d.item)}">
          <input type="hidden" name="date" value="${e(date)}">
          <div class="field"><span class="field-label">Make</span>
            <div class="stepper">
              <button type="button" class="step" data-adjstep="-5" aria-label="Five fewer">-5</button>
              <button type="button" class="step" data-adjstep="-1" aria-label="One fewer">-1</button>
              <input id="adjust-qty" name="quantity" type="number" inputmode="numeric" min="0" max="100000" value="${e(qty)}" required autofocus aria-label="How many to make">
              <button type="button" class="step" data-adjstep="1" aria-label="One more">+1</button>
              <button type="button" class="step" data-adjstep="5" aria-label="Five more">+5</button>
            </div></div>
          <label class="field"><span>Why</span><textarea name="reason" rows="2" placeholder="Catering for 30">${e(reason)}</textarea>
            <small>Optional. Shown to whoever opens this next.</small></label>
          <div class="modal-foot" style="margin:6px -22px -20px">
            ${override !== null ? `<button class="btn ghost" type="button" data-do="clear-adjust" data-item="${e(d.item)}">Use ${suggested !== null ? num(suggested) : "Quantify's number"}</button>` : ""}
            <button class="btn accent" type="submit">Save</button>
          </div>
        </form>
      </div></div>`);
  }

  async function clearAdjust(target) {
    target.disabled = true;
    const sheet = target.closest(".sheet.item"), form = target.closest("form");
    const date = target.dataset.date || (sheet && sheet.dataset.date) || (form && form.elements.date && form.elements.date.value) || S.date;
    const back = sheet ? { item: sheet.dataset.item, date, name: "" } : S.adjustReturn;
    const token = openLayer._seq, location = S.locationId;
    try {
      await API.send(`/api/forecast/override?location_id=${encodeURIComponent(location)}`, "DELETE",
        { item_id: target.dataset.item, date });
      forgetItem(target.dataset.item);
      if (token !== openLayer._seq || location !== S.locationId) return;
      closeLayer();
      const closedToken = openLayer._seq;
      toast("Back to Quantify's number");
      if (S.view === "today") await loadView(true);
      if (back && closedToken === openLayer._seq && location === S.locationId) openItemSheet(back.item, back.name, back.date);
    } catch (error) {
      target.disabled = false;
      toast(plainError(error), "error");
    }
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
        void getUpdatesUI().start();
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

  async function billingRedirect(path, plan) {
    if (billingRedirect.pending) return;
    billingRedirect.pending = true;
    const controls = [...document.querySelectorAll('[data-do="billing-checkout"], [data-do="billing-portal"]')];
    controls.forEach(node => { node.disabled = true; });
    try {
      const result = await API.send(path, "POST", { plan: plan || S.data?.billing?.plan?.code || "solo" });
      if (result.url) window.location.href = result.url;
      else toast(result.message || "Billing is not switched on for this account yet", "error");
    } catch (error) { toast(plainError(error), "error"); }
    finally { billingRedirect.pending = false; controls.forEach(node => { if (node.isConnected) node.disabled = false; }); }
  }

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && layer.innerHTML) closeLayer();
    if ((event.key === "Enter" || event.key === " ") && event.target.matches("tr[data-item-sheet]")) {
      event.preventDefault();
      event.target.click();
    }
    if (event.key === "Tab" && layer.querySelector('[aria-modal="true"]')) {
      const controls = [...layer.querySelectorAll('button:not([disabled]), a[href], input:not([type="hidden"]):not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex="0"]')].filter((node) => node.getClientRects().length);
      if (!controls.length) return;
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && (document.activeElement === first || !layer.contains(document.activeElement))) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || !layer.contains(document.activeElement))) { event.preventDefault(); first.focus(); }
    }
  });

  boot();
})();
