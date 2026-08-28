from __future__ import annotations

import base64
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quantify_app.connectors import provider_readiness  # noqa: E402
from quantify_app.database import connect, initialize  # noqa: E402
from quantify_app.email_brief import preferences  # noqa: E402
from quantify_app.intelligence import daily_brief, forecast_range, performance  # noqa: E402
from quantify_app.menu_intelligence import menu_intelligence_view  # noqa: E402
from quantify_app.seed import seed_demo  # noqa: E402

TODAY = date(2026, 8, 10)
LOCATION = "loc-burger"


def compact(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def fixtures(db_path: Path) -> dict[str, object]:
    with connect(db_path) as conn:
        locations = [dict(row) for row in conn.execute("SELECT * FROM locations ORDER BY name").fetchall()]
        organization = dict(conn.execute("SELECT * FROM organizations WHERE id='org-demo'").fetchone())
        integrations = []
        for row in conn.execute("SELECT * FROM integrations WHERE location_id=? ORDER BY provider", (LOCATION,)).fetchall():
            item = dict(row)
            item["details"] = json.loads(item["details"] or "{}")
            integrations.append(item)
        auth = {
            "setup_required": False,
            "authenticated": True,
            "mfa_setup_required": False,
            "user": {
                "id": "visual", "user_id": "visual", "email": "owner@foundry.example",
                "display_name": "Alex Morgan", "organization_id": "org-demo",
                "csrf_token": "visual", "totp_enabled": True,
                "expires_at": "2026-08-11T09:00:00+00:00",
            },
        }
        bootstrap = {
            "version": "2.0.0", "organization": organization, "locations": locations,
            "default_location_id": LOCATION, "today": TODAY.isoformat(),
            "providers": provider_readiness(),
            "pricing": {"standard_monthly": 79, "standard_annual_equivalent": 69, "founding_monthly": 49, "currency": "USD", "basis": "per location"},
            "product_boundary": "Automatic item-demand intelligence. Exact inventory is shown only when a verified inventory source is connected.",
        }
        setup = {
            "location": next(row for row in locations if row["id"] == LOCATION),
            "integrations": integrations,
            "providers": provider_readiness(),
            "email": preferences(conn, LOCATION),
            "menu": menu_intelligence_view(conn, LOCATION)["summary"],
            "security": {"mfa_enabled": True, "session_expires_at": "2026-08-11T09:00:00+00:00"},
        }
        return {
            "/api/auth/state": auth,
            "/api/bootstrap": bootstrap,
            "/api/brief": daily_brief(conn, LOCATION, TODAY),
            "/api/outlook": forecast_range(conn, LOCATION, TODAY, 14),
            "/api/results": performance(conn, LOCATION, TODAY, 30),
            "/api/setup": setup,
            "/api/menu": menu_intelligence_view(conn, LOCATION),
        }


def html_document(data: dict[str, object]) -> str:
    css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
    api_js = (ROOT / "web" / "api.js").read_text(encoding="utf-8")
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    logo = (ROOT / "web" / "assets" / "quantify-mark.svg").read_bytes()
    logo_uri = "data:image/svg+xml;base64," + base64.b64encode(logo).decode("ascii")
    app_js = app_js.replace('"/assets/quantify-mark.svg"', json.dumps(logo_uri))
    app_js = app_js.replace("'/assets/quantify-mark.svg'", json.dumps(logo_uri))
    harness = f"""
<script>
(() => {{
  const memory = new Map();
  const storage = {{
    getItem: key => memory.has(String(key)) ? memory.get(String(key)) : null,
    setItem: (key, value) => memory.set(String(key), String(value)),
    removeItem: key => memory.delete(String(key)), clear: () => memory.clear(),
    key: index => [...memory.keys()][index] ?? null,
    get length() {{ return memory.size; }},
  }};
  try {{ Object.defineProperty(window, "localStorage", {{value: storage, configurable: true}}); }} catch (_) {{}}
  const fixtures = {compact(data)};
  window.fetch = async (input, options = {{}}) => {{
    const parsed = new URL(String(input), "https://quantify.local");
    const payload = fixtures[parsed.pathname];
    if (payload === undefined) return new Response(JSON.stringify({{error: "Not found"}}), {{status: 404, headers: {{"Content-Type": "application/json"}}}});
    return new Response(JSON.stringify(payload), {{status: 200, headers: {{"Content-Type": "application/json"}}}});
  }};
}})();
</script>"""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Quantify visual validation</title><style>{css}</style></head><body><div id="app"></div><div id="toast" class="toast"></div>{harness}<script>{api_js}</script><script>{app_js}</script></body></html>"""


def main() -> None:
    output = ROOT / "docs" / "screenshots"
    output.mkdir(parents=True, exist_ok=True)
    for path in output.glob("*.png"):
        path.unlink()
    with tempfile.TemporaryDirectory() as temp:
        db_path = Path(temp) / "quantify.db"
        initialize(db_path)
        with connect(db_path) as conn:
            seed_demo(conn, TODAY)
        document = html_document(fixtures(db_path))
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path="/usr/bin/chromium", args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = browser.new_context(viewport={"width": 1440, "height": 1024}, service_workers="block")
            page = context.new_page()
            errors: list[str] = []
            page.on("console", lambda message: errors.append(f"console {message.type}: {message.text}") if message.type == "error" else None)
            page.on("pageerror", lambda error: errors.append(f"pageerror: {error}"))
            page.set_content(document, wait_until="load")
            page.wait_for_selector(".brief-hero", timeout=30_000)
            page.screenshot(path=str(output / "brief.png"), full_page=True)
            page.click('[data-view="outlook"]')
            page.wait_for_selector(".outlook-ledger")
            page.screenshot(path=str(output / "outlook.png"), full_page=True)
            page.click('[data-view="results"]')
            page.wait_for_selector(".results-summary")
            page.screenshot(path=str(output / "results.png"), full_page=True)
            page.click('[data-view="setup"]')
            page.wait_for_selector(".setup-layout")
            page.screenshot(path=str(output / "setup-connections.png"), full_page=True)
            page.click('[data-setup-tab="email"]')
            page.screenshot(path=str(output / "setup-email.png"), full_page=True)
            page.click('[data-setup-tab="menu"]')
            page.screenshot(path=str(output / "setup-menu.png"), full_page=True)
            page.click('[data-view="brief"]')
            page.wait_for_selector(".brief-hero")
            page.set_viewport_size({"width": 820, "height": 1180})
            page.wait_for_timeout(180)
            page.screenshot(path=str(output / "brief-ipad.png"), full_page=True)
            browser.close()
            if errors:
                raise RuntimeError("Browser validation errors:\n" + "\n".join(errors))
    print(f"Rendered 7 validated screenshots to {output}")


if __name__ == "__main__":
    main()
