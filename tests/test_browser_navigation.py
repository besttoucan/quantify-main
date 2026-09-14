"""Opt-in browser regressions for workspace navigation.

Run QUANTIFY_BROWSER_TESTS=1 python -m pytest tests/test_browser_navigation.py.
Requires Playwright and Chromium. Uses a temporary database, real local HTTP,
controlled failure responses, and no external services.
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import server
from quantify_app.database import connect, initialize


@unittest.skipUnless(os.getenv("QUANTIFY_BROWSER_TESTS") == "1", "Opt-in Chromium browser checks")
class BrowserNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright

        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.db = Path(cls.tmp.name) / "navigation.sqlite3"
        initialize(cls.db)
        with connect(cls.db) as conn:
            conn.execute("INSERT INTO organizations(id,name,plan,created_at) VALUES('org-demo','Navigation fixture','standard','2026-01-01')")
            for identifier, name in [("loc-a", "Alpha Cafe"), ("loc-b", "Beta Cafe")]:
                conn.execute("""INSERT INTO locations(id,organization_id,name,concept,address,city,region,postal_code,
                    latitude,longitude,timezone,open_hour,close_hour)
                    VALUES(?,'org-demo',?,'Cafe','1 Main St','Austin','TX','78701',30.27,-97.74,'America/Chicago',7,21)""", (identifier, name))
            conn.commit()
        cls.env = patch.dict(os.environ, {"QUANTIFY_AUTH_BYPASS": "1", "QUANTIFY_QUIET": "1",
            "QUANTIFY_DISABLE_SCHEDULER": "1", "ANTHROPIC_API_KEY": "", "POSTMARK_SERVER_TOKEN": "",
            "SMTP_HOST": "", "SQUARE_ACCESS_TOKEN": "", "SQUARE_LOCATION_ID": "", "STRIPE_SECRET_KEY": ""})
        cls.env.start()
        cls.addClassCleanup(cls.env.stop)
        cls.db_patch = patch.object(server, "DB_PATH", cls.db)
        cls.db_patch.start()
        cls.addClassCleanup(cls.db_patch.stop)
        cls.httpd = server.QuantifyServer(("127.0.0.1", 0), server.QuantifyHandler)
        cls.addClassCleanup(cls.httpd.server_close)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.httpd.shutdown)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        cls.browser = cls.playwright.chromium.launch()
        cls.addClassCleanup(cls.browser.close)

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1024, "height": 768},
            has_touch=True, reduced_motion="reduce")
        self.context.add_init_script("""localStorage.setItem('quantify.tour','done');
            localStorage.setItem('quantify.view','settings'); localStorage.setItem('quantify.stab','location');
            localStorage.setItem('quantify.location','loc-a');""")
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.goto(self.base + "/app")
        self.page.locator("#f-location input[name=name]").wait_for()

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])

    def settled(self):
        self.page.wait_for_function("() => !document.querySelector('.progress.on')")

    def test_failed_location_switch_removes_old_form_and_can_retry(self):
        def failure(route):
            if parse_qs(urlparse(route.request.url).query).get("location_id") == ["loc-b"]:
                route.fulfill(status=503, json={"error": "Location could not be loaded. Try again."})
            else:
                route.continue_()

        self.page.route("**/api/setup?*", failure)
        self.page.locator('[data-do="switch-location"]:visible').first.click()
        self.page.locator('[data-pick-location="loc-b"]').click()
        self.settled()
        self.assertEqual(self.page.evaluate("localStorage.getItem('quantify.location')"), "loc-b")
        self.assertEqual(self.page.locator("#f-location").count(), 0)
        self.assertEqual(self.page.locator(".rail .locpick b").inner_text(), "Beta Cafe")
        self.assertTrue(self.page.get_by_role("button", name="Try again", exact=True).is_visible())
        self.page.unroute("**/api/setup?*", failure)
        self.page.get_by_role("button", name="Try again", exact=True).click()
        self.settled()
        self.assertEqual(self.page.locator("#f-location input[name=name]").input_value(), "Beta Cafe")

    def test_pending_navigation_cannot_submit_previous_screen(self):
        held = []
        self.page.route("**/api/ordering?*", lambda route: held.append(route))
        with self.page.expect_request(lambda request: urlparse(request.url).path == "/api/ordering"):
            self.page.locator('.nav-item[data-view="ordering"]').click()
        self.page.wait_for_function("() => document.querySelector('.content')?.inert === true")
        self.assertEqual(self.page.locator("#f-location input[name=name]").input_value(), "Alpha Cafe")
        self.assertEqual(len(held), 1)
        held[0].fulfill(status=503, json={"error": "The order could not be loaded. Try again."})
        self.settled()
        self.assertEqual(self.page.locator("#f-location").count(), 0)
        self.assertEqual(self.page.locator('.nav-item[aria-current="page"]').get_attribute("data-view"), "ordering")
        self.page.unroute("**/api/ordering?*")
        self.page.get_by_role("button", name="Try again", exact=True).click()
        self.settled()
        self.assertFalse(self.page.locator(".content").evaluate("node => node.inert"))
        self.assertEqual(self.page.get_by_role("button", name="Try again", exact=True).count(), 0)

    def test_order_starts_today_after_viewing_a_past_day(self):
        self.page.locator('.nav-item[data-view="today"]').click()
        self.settled()
        today = self.page.locator("#date-picker").input_value()
        self.page.locator("#date-picker").fill("2020-01-01")
        self.page.locator("#date-picker").dispatch_event("change")
        self.settled()
        with self.page.expect_request(lambda request: urlparse(request.url).path == "/api/ordering") as request:
            self.page.locator('.nav-item[data-view="ordering"]').click()
        self.settled()
        self.assertEqual(parse_qs(urlparse(request.value.url).query)["start"], [today])
