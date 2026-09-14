"""Opt-in browser regressions for workspace navigation.

Run QUANTIFY_BROWSER_TESTS=1 python -m pytest tests/test_browser_navigation.py.
Requires Playwright and Chromium. Uses a temporary database, real local HTTP,
controlled failure responses, and no external services.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import server
from quantify_app import localtime
from quantify_app.intraday import trading_date
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

    def test_incomplete_history_keeps_sales_without_inventing_expected_zero(self):
        target = date(2026, 8, 1)
        with connect(self.db) as conn:
            for identifier, name in [("coverage-old", "House coffee"), ("coverage-new", "New pastry")]:
                conn.execute("INSERT INTO menu_items(id,location_id,name,category,price) VALUES(?,'loc-a',?,'Cafe',10)", (identifier, name))
            for offset in range(7):
                day = (target - timedelta(days=offset)).isoformat()
                conn.execute("INSERT INTO sales(location_id,item_id,date,quantity,revenue) VALUES('loc-a','coverage-new',?,7,70)", (day,))
            conn.execute("INSERT INTO sales(location_id,item_id,date,quantity,revenue) VALUES('loc-a','coverage-old',?,10,100)", (target.isoformat(),))
            conn.execute("""INSERT INTO forecast_calls(location_id,date,item_id,expected,lower,upper,price,model_version,locked_at,locked_local)
                VALUES('loc-a',?,'coverage-old',10,5,15,10,'fixture','2026-08-01T05:00:00Z','05:00')""", (target.isoformat(),))
            conn.commit()
        # Read the real earlier calendar page directly, then use that response
        # for the browser's first History page to avoid many pagination taps.
        response = self.context.request.get(self.base + "/api/history/days?location_id=loc-a&before=2026-08-02&limit=14")
        self.assertEqual(response.status, 200)
        payload = response.json()
        self.page.route("**/api/history/days?*", lambda route: route.fulfill(json=payload))
        self.page.locator('.nav-item[data-view="history"]').click()
        self.settled()
        row = self.page.locator('[data-day-detail="2026-08-01"]')
        self.assertIn("Not scored", row.inner_text())
        self.assertNotIn("0 expected", row.inner_text())
        self.assertIn("Ticket count unavailable", row.inner_text())
        row.click()
        self.page.get_by_role("heading", name="How the day went", exact=True).wait_for()
        sheet = self.page.locator(".sheet-body")
        self.assertIn("17 items sold.", sheet.inner_text())
        self.assertNotIn("against 0 expected", sheet.inner_text())
        self.assertNotIn("0 tickets", self.page.locator(".sheet.day").inner_text())
        self.assertIn("Ticket count unavailable", self.page.locator(".sheet-head").inner_text())
        self.assertEqual(sheet.get_by_role("heading", name="Busiest hour", exact=True).count(), 0)
        section = sheet.locator("section").filter(has=self.page.get_by_role("heading", name="Items without an expectation", exact=True))
        self.assertIn("New pastry", section.inner_text())
        self.assertIn("Not enough history", section.inner_text())
        self.assertIn("7", section.inner_text())
        self.page.keyboard.press("Escape")
        self.page.locator('[data-htab="accuracy"]').click()
        self.settled()
        self.assertTrue(self.page.get_by_role("heading", name="No complete days to score", exact=True).count()
            or self.page.get_by_text("No complete days to score", exact=True).count())
        self.assertEqual(self.page.locator(".linechart").count(), 0)

    def test_today_uses_the_same_overnight_trading_date_as_the_server(self):
        cases = [
            ("America/New_York", 26, "2026-09-14T02:30:00+00:00"),
            ("America/New_York", 26, "2026-09-14T05:30:00+00:00"),
            ("America/New_York", 26, "2026-09-14T06:00:00+00:00"),
            ("America/New_York", 24, "2026-09-14T04:30:00+00:00"),
            ("America/Denver", 47, "2026-09-14T20:00:00+00:00"),
            ("America/Los_Angeles", 21, "2026-09-14T02:00:00+00:00"),
        ]
        for zone, closes, timestamp in cases:
            with self.subTest(zone=zone, closes=closes, timestamp=timestamp):
                moment = datetime.fromisoformat(timestamp)
                expected = trading_date({"close_hour": closes}, moment.astimezone(localtime.zone(zone))).isoformat()

                def bootstrap(route):
                    response = route.fetch()
                    payload = response.json()
                    for location in payload["locations"]:
                        if location["id"] == "loc-a":
                            location.update(timezone=zone, open_hour=23 if closes == 47 else 17, close_hour=closes)
                    payload["today"] = expected
                    route.fulfill(json=payload)

                self.page.route("**/api/bootstrap*", bootstrap)
                self.page.clock.set_fixed_time(moment)
                self.page.goto(self.base + "/app")
                self.page.locator("#f-location").wait_for()
                self.page.locator('.nav-item[data-view="today"]').click()
                self.settled()
                self.assertEqual(self.page.locator("#date-picker").input_value(), expected)
                self.assertEqual(self.page.get_by_role("button", name="Back to today", exact=True).count(), 0)
                self.page.unroute("**/api/bootstrap*", bootstrap)

    def test_rebuilt_baskets_are_not_shown_as_register_tickets(self):
        with connect(self.db) as conn:
            conn.execute("INSERT INTO menu_items(id,location_id,name,category,price) VALUES('ticket-item','loc-b','Bread','Cafe',10)")
            conn.execute("INSERT INTO sales(location_id,item_id,date,quantity,revenue) VALUES('loc-b','ticket-item','2026-08-02',10,100)")
            conn.execute("INSERT INTO sales_hourly(location_id,item_id,date,hour,quantity,revenue) VALUES('loc-b','ticket-item','2026-08-02',10,10,100)")
            conn.commit()
        response = self.context.request.get(self.base + "/api/history/orders?location_id=loc-b&start=2026-08-02&before=2026-08-02&limit=20")
        self.assertEqual(response.status, 200)
        baskets = response.json()["orders"]
        self.assertTrue(baskets)
        self.assertTrue(all(row["source"] == "rebuilt" for row in baskets))
        payload = self.context.request.get(self.base + "/api/history/days?location_id=loc-b&before=2026-08-03&limit=14").json()
        self.page.locator('[data-do="switch-location"]:visible').first.click()
        self.page.locator('[data-pick-location="loc-b"]').click()
        self.settled()
        self.page.route("**/api/history/days?*", lambda route: route.fulfill(json=payload))
        self.page.locator('.nav-item[data-view="history"]').click()
        self.settled()
        self.page.locator('[data-day-detail="2026-08-02"]').click()
        self.page.get_by_role("heading", name="How the day went", exact=True).wait_for()
        self.page.locator('details[data-tickets] summary').click()
        self.page.wait_for_function("() => document.querySelector('details[data-tickets]')?.dataset.loaded === '1'")
        drawer = self.page.locator('details[data-tickets]')
        self.assertIn("Individual tickets are unavailable", drawer.inner_text())
        self.assertEqual(drawer.locator(".ticket").count(), 0)
        self.assertNotIn("Totals include tax", drawer.inner_text())
        with connect(self.db) as conn:
            conn.execute("""INSERT INTO pos_orders(provider_order_id,location_id,provider,order_number,sale_date,
                sale_time,sale_hour,channel,payment_type,subtotal,tax,total,updated_at)
                VALUES('receipt-1','loc-b','square','#R1','2026-08-02','10:20:00',10,'Counter','Cash',100,8,108,'2026-08-02')""")
            conn.execute("""INSERT INTO pos_order_lines(provider,location_id,provider_order_id,provider_line_id,
                item_id,sale_date,sale_hour,quantity,revenue,channel,order_state,payload_hash,updated_at)
                VALUES('square','loc-b','receipt-1','line-1','ticket-item','2026-08-02',10,10,100,'Counter','COMPLETED','fixture','2026-08-02')""")
            conn.commit()
        self.page.keyboard.press("Escape")
        self.page.locator('[data-day-detail="2026-08-02"]').click()
        self.page.get_by_role("heading", name="How the day went", exact=True).wait_for()
        self.page.locator('details[data-tickets] summary').click()
        self.page.wait_for_function("() => document.querySelector('details[data-tickets]')?.dataset.loaded === '1'")
        drawer = self.page.locator('details[data-tickets]')
        self.assertEqual(drawer.locator(".ticket").count(), 1)
        self.assertIn("#R1", drawer.inner_text())
        self.assertIn("Bread x10", drawer.inner_text())
        self.assertIn("$108.00", drawer.inner_text())
        self.assertNotIn("Individual tickets are unavailable", drawer.inner_text())
