"""The server over HTTP: the sign-in gate, the CSRF header, location scoping,
plain error sentences, password change and reset, the register credentials
route, static files, keep-alive, and the location-local date.

A real socket on port 0, a temporary database with two accounts in two
workspaces, no sample history (nothing here needs a forecast), so the whole
file runs in a few seconds.
"""

from __future__ import annotations

import http.client
import json
import os
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import server
from quantify_app.database import connect, initialize

PASSWORD = "Strong-Passphrase-2026!"
NEW_PASSWORD = "Another-Passphrase-2027!"


def _insert_location(conn, location_id: str, organization_id: str, name: str, tz: str) -> None:
    conn.execute(
        """INSERT INTO locations(id,organization_id,name,concept,address,city,region,postal_code,
              latitude,longitude,timezone,open_hour,close_hour,currency,active)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'USD',1)""",
        (location_id, organization_id, name, "Cafe", "1 Main St", "Austin", "TX", "78701",
         30.27, -97.74, tz, 7, 21),
    )


class _Client:
    """One signed-in account. Every call opens its own connection unless told otherwise."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.cookie = ""
        self.csrf = ""

    def call(self, method: str, path: str, body=None, headers=None, conn=None, with_csrf=True):
        own = conn is None
        conn = conn or http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        head = {"Content-Type": "application/json"}
        if self.cookie:
            head["Cookie"] = self.cookie
        if with_csrf and self.csrf:
            head["X-CSRF-Token"] = self.csrf
        head.update(headers or {})
        raw = json.dumps(body).encode("utf-8") if body is not None else None
        if raw is not None:
            head["Content-Length"] = str(len(raw))
        conn.request(method, path, body=raw, headers=head)
        response = conn.getresponse()
        payload = response.read()
        cookie = response.getheader("Set-Cookie")
        if cookie:
            self.cookie = cookie.split(";", 1)[0]
        try:
            data = json.loads(payload.decode("utf-8")) if payload else None
        except json.JSONDecodeError:
            data = payload
        if own:
            conn.close()
        return response, data


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.db_path = root / "data" / "quantify.db"
        initialize(cls.db_path)
        cls.env = patch.dict(os.environ, {"QUANTIFY_QUIET": "1"})
        cls.env.start()
        for key in ("QUANTIFY_AUTH_BYPASS", "POSTMARK_SERVER_TOKEN", "SMTP_HOST", "SQUARE_ACCESS_TOKEN",
                    "SQUARE_LOCATION_ID", "STRIPE_SECRET_KEY", "QUANTIFY_TRUST_PROXY"):
            os.environ.pop(key, None)
        cls.patches = [patch.object(server, "DB_PATH", cls.db_path), patch.object(server, "ROOT", root)]
        for item in cls.patches:
            item.start()
        cls.httpd = server.QuantifyServer(("127.0.0.1", 0), server.QuantifyHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

        # Two accounts, two workspaces, one location each, no history.
        cls.one = cls._account("one@quantify.test", "One")
        cls.two = cls._account("two@quantify.test", "Two")
        with connect(cls.db_path) as conn:
            orgs = {
                row["email"]: row["organization_id"]
                for row in conn.execute("SELECT email,organization_id FROM users").fetchall()
            }
            cls.org_one = orgs["one@quantify.test"]
            cls.org_two = orgs["two@quantify.test"]
            _insert_location(conn, "loc-one", cls.org_one, "One Cafe", "Pacific/Honolulu")
            _insert_location(conn, "loc-two", cls.org_two, "Two Cafe", "Asia/Tokyo")
            conn.commit()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        for item in cls.patches:
            item.stop()
        cls.env.stop()
        cls.tmp.cleanup()

    @classmethod
    def _account(cls, email: str, name: str) -> _Client:
        client = _Client(cls.port)
        response, data = client.call("POST", "/api/auth/setup", {"email": email, "display_name": name, "password": PASSWORD}, with_csrf=False)
        assert response.status == 201, data
        client.csrf = data["csrf_token"]
        code = data["verification"]["preview_code"]
        response, data = client.call("POST", "/api/auth/email/confirm", {"code": code})
        assert response.status == 200, data
        return client

    def _login(self, email: str, password: str) -> tuple[int, dict, _Client]:
        client = _Client(self.port)
        response, data = client.call("POST", "/api/auth/login", {"email": email, "password": password}, with_csrf=False)
        if response.status == 200 and data.get("csrf_token"):
            client.csrf = data["csrf_token"]
        return response.status, data, client

    # -- gates ---------------------------------------------------------------

    def test_the_api_needs_a_session(self) -> None:
        response, data = _Client(self.port).call("GET", "/api/bootstrap")
        self.assertEqual(response.status, 403)
        self.assertEqual(data["error"], "Sign in to continue")

    def test_writes_need_the_csrf_header(self) -> None:
        response, data = self.one.call("PUT", "/api/costs?location_id=loc-one", {"hourly_wage": 18}, with_csrf=False)
        self.assertEqual(response.status, 403)
        self.assertNotIn("csrf", data["error"].lower())
        response, data = self.one.call("PUT", "/api/costs?location_id=loc-one", {"hourly_wage": 18})
        self.assertEqual(response.status, 200, data)
        self.assertEqual(data["settings"]["hourly_wage"], 18)

    def test_another_workspaces_location_is_refused_in_a_sentence(self) -> None:
        response, data = self.one.call("GET", "/api/pulse?location_id=loc-two")
        self.assertEqual(response.status, 400)
        self.assertEqual(data["error"], "That location is not on this account")
        response, data = self.one.call("GET", "/api/setup?location_id=loc-two")
        self.assertEqual(response.status, 400)
        response, data = self.one.call("GET", "/api/setup")
        self.assertEqual(response.status, 400)
        self.assertEqual(data["error"], "Choose a location first")

    # -- error wording ---------------------------------------------------------

    def test_a_bad_number_is_a_plain_sentence_not_python(self) -> None:
        response, data = self.one.call("GET", "/api/outlook?location_id=loc-one&days=abc")
        self.assertEqual(response.status, 400)
        self.assertEqual(data["error"], "Days must be a whole number between 1 and 14")
        response, data = self.one.call("GET", "/api/outlook?location_id=loc-one&days=99")
        self.assertEqual(response.status, 400)
        self.assertEqual(data["error"], "Days must be a whole number between 1 and 14")
        response, data = self.one.call(
            "POST", "/api/forecast/override?location_id=loc-one",
            {"item_id": "x", "date": "2026-08-10", "quantity": "ten", "reason": "catering"},
        )
        self.assertEqual(response.status, 400)
        for tell in ("int(", "invalid literal", "NoneType"):
            self.assertNotIn(tell, data["error"])
        response, data = self.one.call("POST", "/api/onboarding", {"company": "Blue Door", "add_location": True,
                                                                     "location_name": "Blue Door", "latitude": "abc"})
        self.assertEqual(response.status, 400)
        self.assertIn("Latitude must be a number", data["error"])

    def test_the_wrong_shape_of_body_is_a_400_not_a_crash(self) -> None:
        response, data = self.one.call("PUT", "/api/costs?location_id=loc-one", {"categories": [1, "x"], "recurring": "abc"})
        self.assertEqual(response.status, 200, data)
        response, data = self.one.call("PUT", "/api/menu/composition?location_id=loc-one", {"item_id": 5, "components": 3})
        self.assertEqual(response.status, 400)
        self.assertNotIn("object", data["error"])

    def test_cost_validation_speaks_plainly(self) -> None:
        response, data = self.one.call("PUT", "/api/costs?location_id=loc-one", {"hourly_wage": 3})
        self.assertEqual(response.status, 400)
        self.assertEqual(data["error"], "Enter hourly pay of at least $5, or leave the field blank")
        response, data = self.one.call("PUT", "/api/costs?location_id=loc-one",
                                       {"categories": [{"category": "Coffee", "percent": 0}]})
        self.assertEqual(response.status, 400)
        self.assertEqual(data["error"], "Food cost cannot be 0%. Leave it blank to use the estimate")
        response, data = self.one.call("PUT", "/api/costs?location_id=loc-one",
                                       {"recurring": [{"name": "Rent", "amount": 0}, {"name": "Power", "amount": 300}]})
        self.assertEqual(response.status, 200, data)
        self.assertEqual(data["skipped"], ["Rent"])
        self.assertEqual([row["name"] for row in data["recurring"]], ["Power"])
        self.assertNotIn("gross_profit", json.dumps(data))

    # -- passwords -------------------------------------------------------------

    def test_change_password_keeps_this_session_and_ends_the_others(self) -> None:
        status, _, other = self._login("one@quantify.test", PASSWORD)
        self.assertEqual(status, 200)
        response, data = self.one.call("POST", "/api/auth/password/change",
                                       {"current_password": "not-it", "new_password": NEW_PASSWORD})
        self.assertEqual(response.status, 400)
        self.assertEqual(data["error"], "The current password is not right")
        response, data = self.one.call("POST", "/api/auth/password/change",
                                       {"current_password": PASSWORD, "new_password": "short"})
        self.assertEqual(response.status, 400)
        response, data = self.one.call("POST", "/api/auth/password/change",
                                       {"current_password": PASSWORD, "new_password": NEW_PASSWORD})
        self.assertEqual(response.status, 200, data)
        self.assertEqual(self.one.call("GET", "/api/bootstrap")[0].status, 200)
        self.assertEqual(other.call("GET", "/api/bootstrap")[0].status, 403)
        self.assertEqual(self._login("one@quantify.test", PASSWORD)[0], 403)
        status, _, fresh = self._login("one@quantify.test", NEW_PASSWORD)
        self.assertEqual(status, 200)
        # Put it back so the other tests keep working.
        response, data = fresh.call("POST", "/api/auth/password/change",
                                    {"current_password": NEW_PASSWORD, "new_password": PASSWORD})
        self.assertEqual(response.status, 200, data)
        status, _, self.__class__.one = self._login("one@quantify.test", PASSWORD)
        self.assertEqual(status, 200)

    def test_forgotten_password_is_reset_with_an_emailed_code(self) -> None:
        anon = _Client(self.port)
        response, data = anon.call("POST", "/api/auth/password/reset/start", {"email": "nobody@quantify.test"}, with_csrf=False)
        self.assertEqual(response.status, 200)
        self.assertTrue(data["ok"])
        self.assertNotIn("preview_code", data)
        response, data = anon.call("POST", "/api/auth/password/reset/start", {"email": "Two@quantify.test"}, with_csrf=False)
        self.assertEqual(response.status, 200)
        code = data["preview_code"]
        self.assertEqual(len(code), 6)
        self.assertNotIn("SMTP", data["preview_note"])
        wrong = "000000" if code != "000000" else "111111"
        response, data = anon.call("POST", "/api/auth/password/reset/complete",
                                   {"email": "two@quantify.test", "code": wrong, "new_password": NEW_PASSWORD}, with_csrf=False)
        self.assertEqual(response.status, 400)
        self.assertEqual(data["error"], "That code is not right. Check the email and try again")
        response, data = anon.call("POST", "/api/auth/password/reset/complete",
                                   {"email": "two@quantify.test", "code": code, "new_password": NEW_PASSWORD}, with_csrf=False)
        self.assertEqual(response.status, 200, data)
        # The code is spent, every session is out, and the new password works.
        response, data = anon.call("POST", "/api/auth/password/reset/complete",
                                   {"email": "two@quantify.test", "code": code, "new_password": NEW_PASSWORD}, with_csrf=False)
        self.assertEqual(response.status, 400)
        self.assertEqual(self.two.call("GET", "/api/bootstrap")[0].status, 403)
        self.assertEqual(self._login("two@quantify.test", PASSWORD)[0], 403)
        status, _, self.__class__.two = self._login("two@quantify.test", NEW_PASSWORD)
        self.assertEqual(status, 200)

    # -- the register ----------------------------------------------------------

    def test_register_credentials_are_stored_per_location(self) -> None:
        response, data = self.one.call("POST", "/api/integrations/pos/credentials?location_id=loc-one",
                                       {"access_token": "short", "location_id": "L1"})
        self.assertEqual(response.status, 400)
        self.assertIn("access token", data["error"])
        response, data = self.one.call("POST", "/api/integrations/pos/sync?location_id=loc-one", {})
        self.assertEqual(response.status, 502)
        self.assertEqual(data["error"], "Connect Square in Settings > Location first")
        response, data = self.one.call("POST", "/api/integrations/pos/credentials?location_id=loc-one",
                                       {"access_token": "EAAAl-sandbox-token-1234567890", "location_id": "LOC12345", "environment": "sandbox"})
        self.assertEqual(response.status, 200, data)
        self.assertEqual(data["status"], "configured")
        self.assertTrue(data["register"]["connected"])
        response, data = self.one.call("GET", "/api/setup?location_id=loc-one")
        self.assertEqual(response.status, 200, data)
        self.assertTrue(data["register"]["connected"])
        self.assertEqual(data["register"]["mode"], "live")
        self.assertEqual(data["register"]["environment"], "sandbox")
        self.assertNotIn("writer", data)
        with connect(self.db_path) as conn:
            stored = {row["key"]: row["value"] for row in conn.execute(
                "SELECT key,value FROM settings WHERE location_id='loc-one'").fetchall()}
        self.assertEqual(stored["square_location_id"], "LOC12345")
        # The other workspace's location is untouched.
        response, data = self.two.call("GET", "/api/setup?location_id=loc-two")
        self.assertEqual(response.status, 200, data)
        self.assertFalse(data["register"]["connected"])
        self.assertEqual(data["register"]["mode"], "sample")

    # -- email settings --------------------------------------------------------

    def test_email_settings_read_without_writing_and_use_the_locations_zone(self) -> None:
        response, data = self.two.call("GET", "/api/setup?location_id=loc-two")
        self.assertEqual(response.status, 200, data)
        self.assertFalse(data["email"]["configured"])
        self.assertEqual(data["email"]["owner_email"], "")
        self.assertEqual(data["email"]["enabled"], 0)
        self.assertFalse(data["email"]["provider_connected"])
        self.assertNotIn("timezone", data["email"])
        with connect(self.db_path) as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM email_preferences WHERE location_id='loc-two'").fetchone())
        response, data = self.two.call("POST", "/api/email/send-test?location_id=loc-two", {})
        self.assertEqual(response.status, 400)
        self.assertEqual(data["error"], "Add an address for the morning email first")
        response, data = self.two.call("POST", "/api/email/preferences?location_id=loc-two",
                                       {"owner_email": "two@quantify.test", "send_time": "06:00", "timezone": "Europe/Paris"})
        self.assertEqual(response.status, 200, data)
        self.assertTrue(data["configured"])
        self.assertNotIn("timezone", data)
        with connect(self.db_path) as conn:
            row = conn.execute("SELECT timezone FROM email_preferences WHERE location_id='loc-two'").fetchone()
        self.assertEqual(row["timezone"], "Asia/Tokyo")
        response, data = self.two.call("POST", "/api/email/send-test?location_id=loc-two", {"recipient": "stranger@example.org"})
        self.assertEqual(response.status, 400)
        self.assertEqual(data["error"], "Test emails only go to an address already on this account")

    def test_billing_says_when_the_trial_is_over(self) -> None:
        response, data = self.one.call("GET", "/api/billing")
        self.assertEqual(response.status, 200, data)
        self.assertEqual(data["status"], "trialing")
        self.assertFalse(data["provider"]["connected"])
        self.assertNotIn("Stripe", data["provider"]["detail"])
        past = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
        with connect(self.db_path) as conn:
            conn.execute("UPDATE subscriptions SET trial_end=? WHERE organization_id=?", (past, self.org_one))
            conn.commit()
        response, data = self.one.call("GET", "/api/billing")
        self.assertEqual(data["status"], "trial_ended")
        response, data = self.one.call("POST", "/api/billing/portal", {})
        self.assertEqual(response.status, 502)
        self.assertEqual(data["error"], "Payments are not set up on this account yet")

    # -- static files and the connection -----------------------------------------

    def test_unknown_assets_are_404_and_screens_get_the_shell(self) -> None:
        response, data = _Client(self.port).call("GET", "/nope.js")
        self.assertEqual(response.status, 404)
        self.assertEqual(data["error"], "Not found")
        response, data = _Client(self.port).call("GET", "/some-screen")
        self.assertEqual(response.status, 200)
        self.assertIn("text/html", response.getheader("Content-Type"))
        response, data = _Client(self.port).call("GET", "/../server.py")
        self.assertNotEqual(response.status, 200)

    def test_static_files_revalidate_with_an_etag(self) -> None:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", "/app.js")
        response = conn.getresponse()
        body = response.read()
        self.assertEqual(response.status, 200)
        self.assertEqual(int(response.getheader("Content-Length")), len(body))
        self.assertEqual(response.getheader("Cache-Control"), "no-cache")
        etag = response.getheader("ETag")
        self.assertTrue(etag)
        conn.request("GET", "/app.js", headers={"If-None-Match": etag})
        response = conn.getresponse()
        response.read()
        self.assertEqual(response.status, 304)
        self.assertEqual(response.getheader("ETag"), etag)
        conn.close()

    def test_the_connection_is_kept_alive_on_http_11(self) -> None:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        first = None
        for _ in range(3):
            conn.request("GET", "/api/health")
            response = conn.getresponse()
            response.read()
            self.assertEqual(response.version, 11)
            self.assertEqual(response.status, 200)
            self.assertNotEqual((response.getheader("Connection") or "").lower(), "close")
            first = first or conn.sock
            self.assertIs(conn.sock, first, "a new socket was opened for a kept-alive connection")
        # A refused write on the same connection leaves it usable: the body is drained.
        response, data = self.one.call("POST", "/api/auth/password/change", {"current_password": "x", "new_password": "y"},
                                       conn=conn, with_csrf=False)
        self.assertEqual(response.status, 403)
        response, data = self.one.call("GET", "/api/bootstrap", conn=conn)
        self.assertEqual(response.status, 200, data)
        conn.close()

    # -- the date ----------------------------------------------------------------

    def test_pulse_and_bootstrap_carry_the_locations_own_date(self) -> None:
        response, data = self.one.call("GET", "/api/pulse?location_id=loc-one")
        self.assertEqual(response.status, 200, data)
        with connect(self.db_path) as conn:
            expected = server._location_today(conn, "loc-one").isoformat()
        self.assertEqual(data["today"], expected)
        self.assertLessEqual(abs((date.fromisoformat(data["today"]) - datetime.now(timezone.utc).date()).days), 1)
        self.assertEqual(len(data["version"]), 16)
        response, data = self.one.call("GET", "/api/bootstrap?location_id=loc-one")
        self.assertEqual(response.status, 200, data)
        self.assertEqual(data["today"], expected)
        self.assertEqual([row["id"] for row in data["locations"]], ["loc-one"])


if __name__ == "__main__":
    unittest.main()
