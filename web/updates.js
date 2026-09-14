/* Updates keeps its own request and notification state. App state stays in app.js. */
window.QuantifyUpdates = (() => {
  "use strict";

  const escape = value => String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
  const stamp = value => {
    const time = Date.parse(value || "");
    return Number.isFinite(time) ? time : null;
  };
  const visible = node => !!node && !!node.getClientRects().length;
  const nodeId = id => `update-note-${encodeURIComponent(String(id))}`;

  // Level 3 of the urgency ladder. A filled triangle with the bar and dot
  // punched through by fill-rule, so it reads on any background without a
  // knockout colour. It never appears without the status text beside it.
  const HAZARD = '<svg class="hz" width="14" height="14" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path fill="currentColor" fill-rule="evenodd" d="M8.72 1.56a.83.83 0 0 0-1.44 0L.6 13.1a.83.83 0 0 0 .72 1.25h13.36a.83.83 0 0 0 .72-1.25ZM7.2 5.5h1.6v4.3H7.2ZM7.2 11h1.6v1.6H7.2Z"/></svg>';

  function create(options) {
    const { getContext, get, post, onUnread = () => {}, onNavigate = () => {}, onRender = () => {} } = options;
    let contextKey = "", epoch = 0, sequence = 0, running = false;
    let feed = null, busy = "", error = "", failedAction = null, foreground = null;
    let checkTimer = null, noticeTimer = null, expiryTimer = null, pendingNotice = null, popup = null;
    let listeners = false;
    const shown = new Set();

    function context() { return getContext() || {}; }
    function keyFor(value) { return JSON.stringify([value.userKey || "", value.locationId || ""]); }
    function removePopup() { popup?.remove(); popup = null; }
    function render() { if (context().view === "updates") onRender(); }
    function clearTimers() {
      clearTimeout(noticeTimer); clearTimeout(expiryTimer);
      noticeTimer = null; expiryTimer = null;
    }
    function clearState() {
      epoch += 1; sequence += 1;
      feed = null; busy = ""; error = ""; failedAction = null; foreground = null;
      pendingNotice = null; shown.clear(); clearTimers(); removePopup(); onUnread(0);
    }
    function synchronize() {
      const current = context(), nextKey = keyFor(current);
      if (nextKey !== contextKey) { contextKey = nextKey; clearState(); }
      return current;
    }
    function stillCurrent(ticket) {
      return ticket.epoch === epoch && ticket.key === contextKey && ticket.key === keyFor(context()) && ticket.sequence === sequence;
    }
    function ticketFor(current) { return { epoch, sequence: ++sequence, key: keyFor(current), location: current.locationId }; }
    function endpoint(current, suffix = "") { return `/api/updates${suffix}?location_id=${encodeURIComponent(current.locationId)}`; }
    function notes() {
      const all = [...(feed?.notes || []), ...(feed?.earlier || [])], ids = new Set();
      return all.filter(note => note && note.id != null && !ids.has(String(note.id)) && ids.add(String(note.id)));
    }
    function stateOf(note) {
      const now = Date.now(), end = stamp(note.expires_at), start = stamp(note.starts_at);
      if (note.state === "resolved") return "resolved";
      if (note.state === "expired" || (end !== null && end <= now)) return "expired";
      if (note.state === "scheduled" || (start !== null && start > now)) return "scheduled";
      return note.state === "active" ? "active" : "resolved";
    }
    function isLive(note) { return note && stateOf(note) === "active"; }

    // Severity on its own overstates the case. updates.py marks a 25 percent
    // revenue swing "important" as well, and a busier day than usual is not
    // today's emergency. Only a stock note already inside one day of cover is
    // work that has to happen today, so only that earns the mark.
    function levelOf(note) {
      if (!note || stateOf(note) !== "active" || note.severity !== "important") return "";
      if (note.kind === "stock") return "stop";
      if (note.kind === "register") return "warn";
      return "";
    }
    function unreadCount() {
      // The server owns unread state. Locally elapsed notes cannot keep a stale badge alive.
      const expiredUnread = (feed?.notes || []).filter(note => note.unread && ["expired", "resolved"].includes(stateOf(note))).length;
      return Math.max(0, Number(feed?.unread_count || 0) - expiredUnread);
    }
    function dateText(value, withTime = true) {
      const time = stamp(value);
      if (time === null) return "";
      const args = { month: "short", day: "numeric", ...(withTime ? { hour: "numeric", minute: "2-digit" } : {}) };
      try { return new Intl.DateTimeFormat("en-US", { ...args, timeZone: context().timezone || "UTC" }).format(time); }
      catch (_) { return new Intl.DateTimeFormat("en-US", { ...args, timeZone: "UTC" }).format(time); }
    }
    function scheduleExpiry() {
      clearTimeout(expiryTimer);
      const next = notes().map(note => stamp(note.expires_at)).filter(time => time !== null && time > Date.now()).sort((a, b) => a - b)[0];
      if (next === undefined) return;
      expiryTimer = setTimeout(() => {
        synchronize();
        if (pendingNotice && !isLive(pendingNotice)) pendingNotice = null;
        if (popup && !isLive(notes().find(note => String(note.id) === popup.dataset.updatePopup))) removePopup();
        onUnread(unreadCount()); render(); scheduleExpiry();
      }, Math.min(2147483647, Math.max(1, next - Date.now() + 30)));
    }
    function accept(value, ticket) {
      if (!stillCurrent(ticket)) return false;
      if (!value || String(value.location_id) !== String(ticket.location)) throw new Error("Wrong location");
      feed = { ...value, notes: Array.isArray(value.notes) ? value.notes : [], earlier: Array.isArray(value.earlier) ? value.earlier : [] };
      error = ""; failedAction = null;
      onUnread(unreadCount()); scheduleExpiry();
      if (popup && !isLive(notes().find(note => String(note.id) === popup.dataset.updatePopup))) removePopup();
      return true;
    }
    function blocked() {
      const focus = document.activeElement;
      return document.hidden || [...document.querySelectorAll("#layer .sheet, #layer .modal, #layer .scrim, .tour-card")].some(visible) ||
        !!focus?.matches("input, textarea, select, [contenteditable='true'], [contenteditable='']");
    }
    async function acknowledgeShown(note, ticket) {
      try {
        const value = await post(endpoint({ locationId: ticket.location }, "/read"), { ids: [String(note.id)], read: false });
        if (accept(value, ticket)) render();
      } catch (_) { /* The current session still suppresses this already displayed notice. */ }
    }
    function tryNotice() {
      clearTimeout(noticeTimer); noticeTimer = null;
      const current = synchronize(), note = pendingNotice;
      if (!running || !note || !isLive(note) || shown.has(String(note.id)) || note.seen_at || popup) return;
      if (blocked() || busy) { noticeTimer = setTimeout(tryNotice, 2500); return; }
      const holder = document.createElement("aside");
      const level = levelOf(note);
      holder.className = "updates-notice" + (level ? ` ${level}` : "");
      holder.dataset.updatePopup = String(note.id);
      holder.setAttribute("role", "status");
      holder.setAttribute("aria-live", "polite");
      holder.setAttribute("aria-atomic", "true");
      holder.innerHTML = `<div class="updates-notice-head"><span class="updates-level ${level}">${level === "stop" ? HAZARD : ""}${level === "stop" ? "Act today" : level === "warn" ? "Needs a look" : "Sales pattern"}</span>
        <button type="button" class="updates-dismiss" data-update-dismiss aria-label="Dismiss update"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg></button></div>
        <h2>${escape(note.title)}</h2><p>${escape(note.body)}</p>
        <button type="button" class="btn" data-update-view="${escape(note.id)}">View update</button>`;
      document.body.appendChild(holder); popup = holder;
      // Only an actually visible notice is recorded as shown; unread is left alone.
      shown.add(String(note.id)); pendingNotice = null;
      const ticket = { epoch, sequence, key: keyFor(current), location: current.locationId };
      void acknowledgeShown(note, ticket);
    }
    function considerNotice(timely = false) {
      const important = feed?.notification, pattern = timely ? feed?.timely_notification : null;
      pendingNotice = important && isLive(important) && important.severity === "important" ? important :
        pattern && isLive(pattern) && ["important", "notice"].includes(pattern.severity) ? pattern : null;
      tryNotice();
    }

    function load({ refresh = false, notify = false, timely = false } = {}) {
      const current = synchronize();
      if (!current.locationId) return Promise.resolve(null);
      if (foreground) {
        foreground.notify ||= notify;
        foreground.timely ||= timely;
        // A requested fresh check can replace an older read, but never another fresh check.
        if (!refresh || busy === "refresh" || busy === "read") return foreground.promise;
      }
      const ticket = ticketFor(current), work = { notify, timely, promise: null };
      busy = refresh ? "refresh" : "load"; error = ""; failedAction = null; foreground = work; render();
      work.promise = (async () => {
        try {
          const value = refresh ? await post(endpoint(current, "/refresh"), {}) : await get(endpoint(current));
          if (!accept(value, ticket)) return null;
          return feed;
        } catch (_) {
          if (stillCurrent(ticket)) {
            error = refresh ? "The check did not finish. Try again." : "Updates could not be loaded. Try again.";
            failedAction = { refresh };
          }
          return null;
        } finally {
          if (stillCurrent(ticket)) {
            busy = ""; foreground = null; render();
            if (work.notify && !error) considerNotice(work.timely);
          }
        }
      })();
      return work.promise;
    }

    function markRead(ids) {
      const current = synchronize();
      if (busy || !current.locationId || !ids.length) return Promise.resolve(null);
      const ticket = ticketFor(current), work = { notify: false, promise: null };
      busy = "read"; error = ""; failedAction = null; foreground = work; render();
      work.promise = (async () => {
        try {
          const value = await post(endpoint(current, "/read"), { ids, read: true });
          if (!accept(value, ticket)) return null;
          if (popup && ids.includes(popup.dataset.updatePopup)) removePopup();
          return feed;
        } catch (_) {
          if (stillCurrent(ticket)) { error = "The read status did not save. Try again."; failedAction = { ids }; }
          return null;
        } finally {
          if (stillCurrent(ticket)) { busy = ""; foreground = null; render(); }
        }
      })();
      return work.promise;
    }
    function card(note) {
      const state = stateOf(note), current = state === "active", ended = state === "expired" || state === "resolved";
      const level = levelOf(note);
      const status = state === "expired" ? "Ended" : state === "resolved" ? "Resolved" : state === "scheduled" ? "Upcoming"
        : level === "stop" ? "Act today" : level === "warn" ? "Needs a look" : "Update";
      const source = typeof note.evidence === "string" ? note.evidence : note.evidence ? JSON.stringify(note.evidence) : "";
      const action = note.action && ["today", "ordering", "settings"].includes(note.action.view) ? note.action : null;
      const starts = dateText(note.starts_at), expires = dateText(note.expires_at);
      const window = ended ? `Earlier update${starts ? ` from ${starts}` : ""}${expires ? ` through ${expires}` : ""}` :
        `${starts ? `From ${starts}` : "Applies now"}${expires ? ` until ${expires}` : "; until the details change"}`;
      return `<article class="updates-card${level ? ` ${level}` : ""}${ended ? " updates-ended" : ""}" id="${escape(nodeId(note.id))}" tabindex="-1" data-update-note="${escape(note.id)}">
        <div class="updates-card-meta"><span class="updates-level ${level}">${level === "stop" ? HAZARD : ""}${status}</span>
          ${note.unread ? '<span class="updates-unread">Unread</span>' : '<span class="updates-read">Read</span>'}
          ${note.created_at ? `<time datetime="${escape(note.created_at)}">${escape(dateText(note.created_at))}</time>` : ""}</div>
        <h2>${escape(note.title)}</h2><p class="updates-body">${escape(note.body)}</p>
        ${source ? `<p class="updates-evidence"><span>Based on</span> ${escape(source)}</p>` : ""}
        <p class="updates-window">${escape(window)}</p>
        <div class="updates-actions">${action && !ended ? `<button type="button" class="btn" data-update-action="${escape(note.id)}">${escape(action.label || "Open details")}</button>` : ""}
          ${note.unread ? `<button type="button" class="btn ghost" data-update-read="${escape(note.id)}" ${busy ? "disabled" : ""}>Mark read</button>` : ""}</div>
      </article>`;
    }
    function panel() {
      synchronize();
      const all = notes(), current = all.filter(note => ["active", "scheduled"].includes(stateOf(note)));
      const earlier = all.filter(note => !["active", "scheduled"].includes(stateOf(note)));
      const unread = all.filter(note => note.unread).map(note => String(note.id));
      const checking = busy === "load" || busy === "refresh";
      const subtitle = checking ? "Checking latest sales..." : feed?.checked_at ? `Checked ${dateText(feed.checked_at)}` : "Changes in sales, counts, and the next few days appear here.";
      return `<section class="updates-panel" aria-busy="${!!busy}">
        <div class="updates-toolbar"><div><h2>Latest updates</h2><p role="status" aria-live="polite">${escape(subtitle)}</p></div>
          <button type="button" class="btn accent" data-update-refresh ${busy ? "disabled" : ""}>${checking ? '<span class="updates-spinner" aria-hidden="true"></span>Checking...' : "Update me"}</button></div>
        ${error ? `<div class="updates-error" role="alert"><p>${escape(error)}</p><button type="button" class="btn" data-update-retry ${busy ? "disabled" : ""}>Try again</button></div>` : ""}
        ${unread.length ? `<div class="updates-readall"><button type="button" class="btn ghost" data-update-read-all ${busy ? "disabled" : ""}>Mark all read</button></div>` : ""}
        <div class="updates-list">${current.length ? current.map(card).join("") : checking ? '<div class="updates-empty"><p>The latest sales and counts are being checked.</p></div>' : error && !feed ? "" : '<div class="updates-empty"><h2>No current updates</h2><p>New changes will appear here after the next check.</p></div>'}</div>
        ${earlier.length ? `<details class="updates-earlier"><summary>Earlier updates (${earlier.length})</summary><div class="updates-list">${earlier.map(card).join("")}</div></details>` : ""}
      </section>`;
    }

    function click(event) {
      const control = event.target.closest("[data-update-refresh], [data-update-retry], [data-update-read], [data-update-read-all], [data-update-action], [data-update-dismiss], [data-update-view]");
      if (!control || control.disabled) return;
      if (!control.closest(".updates-panel, .updates-notice")) return;
      event.preventDefault();
      const beforeKey = contextKey;
      synchronize();
      if (contextKey !== beforeKey) return;
      if (control.hasAttribute("data-update-dismiss")) { removePopup(); pendingNotice = null; return; }
      if (control.hasAttribute("data-update-view")) {
        const id = control.dataset.updateView;
        removePopup(); pendingNotice = null;
        Promise.resolve(onNavigate({ view: "updates", update_id: id })).then(() => requestAnimationFrame(() => {
          const target = document.getElementById(nodeId(id));
          target?.focus({ preventScroll: true }); target?.scrollIntoView({ block: "nearest" });
        }));
        return;
      }
      if (control.hasAttribute("data-update-refresh")) { void load({ refresh: true }); return; }
      if (control.hasAttribute("data-update-retry")) {
        if (failedAction?.ids) void markRead(failedAction.ids);
        else void load({ refresh: !!failedAction?.refresh });
        return;
      }
      if (control.hasAttribute("data-update-read-all")) { void markRead(notes().filter(note => note.unread).map(note => String(note.id))); return; }
      if (control.hasAttribute("data-update-read")) { void markRead([control.dataset.updateRead]); return; }
      const note = notes().find(row => String(row.id) === control.dataset.updateAction);
      if (note?.action && ["today", "ordering", "settings"].includes(note.action.view) && ["active", "scheduled"].includes(stateOf(note))) onNavigate(note.action);
    }
    function check(timely = false) { if (running && !document.hidden) void load({ notify: true, timely }); }
    function visibility() { if (!document.hidden) { check(); tryNotice(); } }
    function attachListeners() {
      if (listeners) return;
      document.addEventListener("click", click); document.addEventListener("visibilitychange", visibility); listeners = true;
    }
    function start() {
      synchronize(); attachListeners();
      if (!running) { running = true; checkTimer = setInterval(() => check(true), 5 * 60 * 1000); }
      return load({ notify: true });
    }
    function stop() {
      running = false; clearInterval(checkTimer); checkTimer = null; clearTimers(); removePopup();
      pendingNotice = null; epoch += 1; sequence += 1; foreground = null; busy = "";
      document.removeEventListener("click", click); document.removeEventListener("visibilitychange", visibility); listeners = false;
    }
    function reset() { contextKey = keyFor(context()); clearState(); }
    attachListeners();
    return { load, panel, start, stop, reset };
  }
  return { create };
})();
