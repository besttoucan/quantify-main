"""Local HTTP regressions for request framing, setup hours and register isolation."""

import http.client
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

import server
from quantify_app import auth, connectors
from quantify_app.database import connect, initialize


class InfrastructureBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "quantify.db"
        initialize(self.db_path)
        self.env = patch.dict(os.environ, {"QUANTIFY_QUIET": "1", "QUANTIFY_AUTH_BYPASS": "0",
                                         "SQUARE_ACCESS_TOKEN": "", "SQUARE_LOCATION_ID": "",
                                         "QUANTIFY_SQUARE_INTERNAL_LOCATION": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.db_patch = patch.object(server, "DB_PATH", self.db_path)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        with connect(self.db_path) as conn:
            self.owner = auth.create_account(conn, "infra@quantify.test", "Infra Owner", "Strong-Passphrase-2026!")
            conn.execute("UPDATE users SET email_verified=1 WHERE id=?", (self.owner["user_id"],))
            self.session = auth.create_session(conn, self.owner["user_id"])
        self.httpd = server.QuantifyServer(("127.0.0.1", 0), server.QuantifyHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def call(self, method, path, body=None, extra_headers=()):
        raw = json.dumps(body).encode() if body is not None else b""
        headers = [("Host", "127.0.0.1"), ("Content-Type", "application/json"),
                   ("Content-Length", str(len(raw))),
                   ("Cookie", "quantify_session=" + self.session["session_token"]),
                   ("X-CSRF-Token", self.session["csrf_token"]), *extra_headers]
        wire = (f"{method} {path} HTTP/1.1\r\n" +
                "".join(f"{key}: {value}\r\n" for key, value in headers) + "\r\n").encode() + raw
        # Send headers and body together: the bad-framing case deliberately
        # closes immediately, before a client could send a second body packet.
        with socket.create_connection(("127.0.0.1", self.httpd.server_port), timeout=5) as sock:
            sock.sendall(wire)
            response = http.client.HTTPResponse(sock)
            response.begin()
            payload = json.loads(response.read())
            response.close()
            return response.status, payload, response.getheader("Connection")

    def add_location(self, location_id, organization_id):
        with connect(self.db_path) as conn:
            conn.execute("""INSERT INTO locations(id,organization_id,name,concept,address,postal_code,city,region,
                latitude,longitude,timezone,open_hour,close_hour,currency,active)
                VALUES(?,?,?,'Cafe','','','Austin','TX',30.27,-97.74,'America/Chicago',7,21,'USD',1)""",
                         (location_id, organization_id, location_id))
            conn.commit()

    def test_ambiguous_request_framing_cannot_reach_a_mutating_route(self):
        for headers in ((('Content-Length', '1'),), (('Transfer-Encoding', 'chunked'),)):
            with self.subTest(headers=headers):
                status, payload, connection = self.call("POST", "/api/auth/profile",
                    {"display_name": "Changed through ambiguous framing"}, headers)
                self.assertEqual(status, 400, payload)
                self.assertEqual(connection, "close")
                with connect(self.db_path) as conn:
                    name = conn.execute("SELECT display_name FROM users WHERE id=?", (self.owner["user_id"],)).fetchone()[0]
                self.assertEqual(name, "Infra Owner")

    def test_onboarding_preserves_evening_hours_and_rejects_bad_hours_before_writing(self):
        base = {"company": "Night cafe", "add_location": True, "location_name": "Night cafe",
                "place": "Austin TX", "concept": "Cafe"}
        for field, value in (("open_hour", "late"), ("open_hour", 24), ("close_hour", 48)):
            with self.subTest(field=field, value=value):
                status, payload, _ = self.call("POST", "/api/onboarding",
                    base | {"open_hour": 17, "close_hour": 26, field: value})
                self.assertEqual(status, 400, payload)
                with connect(self.db_path) as conn:
                    org = conn.execute("SELECT name,onboarded_at FROM organizations WHERE id=?",
                                       (self.owner["organization_id"],)).fetchone()
                    self.assertEqual(org["name"], "Your company")
                    self.assertIsNone(org["onboarded_at"])
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM locations").fetchone()[0], 0)
        status, payload, _ = self.call("POST", "/api/onboarding", base | {"open_hour": 17, "close_hour": 2})
        self.assertEqual(status, 200, payload)
        with connect(self.db_path) as conn:
            loc = conn.execute("SELECT open_hour,close_hour FROM locations WHERE id=?", (payload["location_id"],)).fetchone()
        self.assertEqual(tuple(loc), (17, 26))

    def test_invalid_sample_onboarding_does_not_mark_the_workspace_complete(self):
        with patch.object(server, "seed_workspace") as seed:
            status, payload, _ = self.call("POST", "/api/onboarding",
                {"company": "Night cafe", "place": "Austin TX", "open_hour": "late", "close_hour": 26})
            self.assertEqual(status, 400, payload)
            seed.assert_not_called()
        with connect(self.db_path) as conn:
            org = conn.execute("SELECT name,onboarded_at FROM organizations WHERE id=?",
                               (self.owner["organization_id"],)).fetchone()
        self.assertEqual(org["name"], "Your company")
        self.assertIsNone(org["onboarded_at"])

    def test_a_full_day_location_can_open_at_23_and_close_at_47(self):
        status, payload, _ = self.call("POST", "/api/locations",
            {"name": "All night", "concept": "Cafe", "place": "Austin TX", "open_hour": 23, "close_hour": 47})
        self.assertEqual(status, 201, payload)
        self.assertEqual((payload["location"]["open_hour"], payload["location"]["close_hour"]), (23, 47))
        with connect(self.db_path) as conn:
            sample_id = server.seed_workspace(conn, self.owner["organization_id"], name="Night sample",
                city="Austin", region="TX", history_days=1, open_hour=23, close_hour=47)
            sample = conn.execute("SELECT open_hour,close_hour FROM locations WHERE id=?", (sample_id,)).fetchone()
        self.assertEqual(tuple(sample), (23, 47))

    def test_nonfinite_integer_input_is_a_400(self):
        self.add_location("loc-input", self.owner["organization_id"])
        for value in ("Infinity", "-Infinity", "1e309"):
            with self.subTest(value=value):
                status, payload, _ = self.call("GET", "/api/outlook?location_id=loc-input&days=" + value)
                self.assertEqual(status, 400, payload)
                self.assertEqual(payload["error"], "Days must be a whole number between 1 and 14")

    def test_server_square_credentials_are_only_available_to_the_mapped_location(self):
        with connect(self.db_path) as conn:
            other = auth.create_account(conn, "other-infra@quantify.test", "Other Owner", "Strong-Passphrase-2026!")
        self.add_location("loc-mapped", other["organization_id"])
        self.add_location("loc-unrelated", self.owner["organization_id"])
        environment = {"SQUARE_ACCESS_TOKEN": "synthetic-test-token-123456789", "SQUARE_LOCATION_ID": "MERCHANT-ONE",
                       "QUANTIFY_SQUARE_INTERNAL_LOCATION": "loc-mapped"}
        with patch.dict(os.environ, environment), patch.object(connectors, "_request_json") as request:
            status, payload, _ = self.call("GET", "/api/setup?location_id=loc-unrelated")
            self.assertEqual(status, 200, payload)
            self.assertFalse(payload["register"]["connected"])
            self.assertIsNone(payload["register"]["square_location_id"])
            status, payload, _ = self.call("POST", "/api/integrations/pos/sync?location_id=loc-unrelated", {})
            self.assertEqual(status, 502, payload)
            request.assert_not_called()
            with connect(self.db_path) as conn:
                self.assertEqual(connectors._square_credentials(conn, "loc-mapped")["source"], "environment")
                connectors.save_square_credentials(conn, "loc-unrelated", "synthetic-owner-token-123456789", "MERCHANT-TWO")
                self.assertEqual(connectors._square_credentials(conn, "loc-unrelated")["source"], "settings")
            with patch.dict(os.environ, {"QUANTIFY_SQUARE_INTERNAL_LOCATION": ""}):
                with connect(self.db_path) as conn:
                    self.assertIsNone(connectors._square_credentials(conn, "loc-mapped"))
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
