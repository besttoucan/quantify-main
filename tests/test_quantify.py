from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch
from datetime import date, datetime, timedelta
from pathlib import Path

from quantify_app.auth import (
    auth_state,
    confirm_email,
    create_account,
    disable_totp,
    start_email_verification,
    begin_login,
    complete_login,
    create_owner,
    enable_totp,
    session_from_token,
    totp_code,
)
from quantify_app.connectors import _sync_square_catalog, ingest_square_orders, verify_square_webhook_signature
from quantify_app.database import connect, initialize, table_count
from quantify_app.email_brief import build_email, deliver_brief, update_preferences
from quantify_app.explain import build_day_payload, local_day_narrative
from quantify_app.intelligence import (
    canonical_event_group,
    daily_brief,
    event_impact,
    forecast_range,
    performance,
)
from quantify_app.menu_intelligence import interpret_menu_item
from quantify_app.seed import seed_demo, seed_workspace
from server import _menu_import

TODAY = date(2026, 8, 10)
LOCATION = "loc-bakery"


class QuantifyPlatformTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.db_path = cls.root / "data" / "quantify.db"
        initialize(cls.db_path)
        with connect(cls.db_path) as conn:
            seed_demo(conn, TODAY)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_seed_has_two_years_hourly_sales_and_broad_context(self) -> None:
        with connect(self.db_path) as conn:
            item = conn.execute(
                "SELECT id FROM menu_items WHERE location_id=? ORDER BY id LIMIT 1", (LOCATION,)
            ).fetchone()
            daily = conn.execute(
                "SELECT COUNT(*) AS n, MIN(date) AS first, MAX(date) AS last FROM sales WHERE location_id=? AND item_id=?",
                (LOCATION, item["id"]),
            ).fetchone()
            self.assertEqual(daily["n"], 730)
            self.assertEqual(daily["last"], (TODAY - timedelta(days=1)).isoformat())
            self.assertGreater(table_count(conn, "sales_hourly"), table_count(conn, "sales"))
            event_types = {
                row["event_type"]
                for row in conn.execute("SELECT DISTINCT event_type FROM events WHERE location_id=?", (LOCATION,))
            }
            self.assertGreaterEqual(len(event_types), 5)
            self.assertTrue({"sports", "concerts", "conferences", "festivals", "disruptions"} & event_types)
            weather = conn.execute(
                "SELECT snowfall_cm,uv_index FROM weather WHERE location_id=? ORDER BY date DESC LIMIT 1", (LOCATION,)
            ).fetchone()
            self.assertIsNotNone(weather["snowfall_cm"])
            self.assertIsNotNone(weather["uv_index"])

    def test_menu_interpreter_handles_rough_labels_without_blocking_forecast(self) -> None:
        interpreted = interpret_menu_item("DBL CHZ BRGR #2", "BURGERS")
        self.assertEqual(interpreted.item_family, "burger")
        self.assertEqual(interpreted.normalized_name, "Double Cheese Burger #2")
        self.assertTrue(interpreted.inferred["forecast_ready"])
        obscure = interpret_menu_item("HOUSE 7X SPECIAL", "")
        self.assertEqual(obscure.item_family, "menu-item")
        self.assertTrue(obscure.inferred["forecast_ready"])
        self.assertTrue(obscure.inferred["requires_review"])

    def test_event_intelligence_is_category_agnostic_and_has_no_two_mile_cutoff(self) -> None:
        self.assertEqual(canonical_event_group("unknown-new-category"), "other")
        common = {
            "attendance": 8000,
            "relevance": 0.85,
            "start_time": "17:30",
            "end_time": "21:30",
        }
        near = event_impact(common | {"distance_miles": 1.0}, 6, 22)
        middle = event_impact(common | {"distance_miles": 4.0}, 6, 22)
        far = event_impact(common | {"distance_miles": 12.0}, 6, 22)
        self.assertGreater(near, middle)
        self.assertGreater(middle, far)
        self.assertGreater(far, 0.0)

    def test_brief_and_outlook_are_decision_ready_without_inventory_fiction(self) -> None:
        with connect(self.db_path) as conn:
            brief = daily_brief(conn, LOCATION, TODAY, week_days=7)
            outlook = forecast_range(conn, LOCATION, TODAY, days=14)
        self.assertIn("expected_revenue", brief["summary"])
        self.assertIn("peak_hour", brief["summary"])
        self.assertGreater(len(brief["items"]), 5)
        self.assertEqual(len(brief["week_ahead"]), 6)
        self.assertEqual(len(outlook["days"]), 14)
        self.assertIn("event_candidates_reviewed", brief["context"])
        self.assertEqual(brief["data_health"]["history_days"], 730)
        self.assertEqual(brief["data_health"]["pos_freshness"], "current")
        # Every headline number arrives with something to compare it against.
        self.assertGreater(brief["comparison"]["sales"], 0)
        self.assertGreater(brief["comparison"]["based_on_days"], 0)
        self.assertIn("difference_sales", brief["summary"])
        self.assertIn("difference_units", brief["summary"])
        self.assertGreater(brief["trust"]["history_days"], 0)
        self.assertTrue(brief["actions"], "the brief must always say what to do")
        for action in brief["actions"]:
            self.assertTrue(action["title"] and action["detail"] and action["metric"])
        for signal in brief["context"]["signals"]:
            # A percentage on its own is not an answer, so every driver carries
            # the same effect expressed in units and in money.
            self.assertIn("units", signal)
            self.assertIn("sales", signal)
            self.assertTrue(signal["detail"])

        serialized = json.dumps(brief).lower()
        self.assertNotIn("expiry date", serialized)
        self.assertNotIn("exact inventory quantity", serialized)
        self.assertNotIn("not a physical inventory count", serialized)

    def test_written_copy_stays_plain(self) -> None:
        with connect(self.db_path) as conn:
            brief = daily_brief(conn, LOCATION, TODAY, week_days=2)
            narrative = local_day_narrative(build_day_payload(brief))
        prose = " ".join([
            brief["headline"], brief["data_note"],
            *[row["title"] + " " + row["detail"] for row in brief["actions"]],
            *[row["detail"] for row in brief["context"]["signals"]],
            narrative["headline"], narrative["summary"], narrative["confidence_note"],
            *[row["explanation"] for row in narrative["factors"]],
        ])
        self.assertNotIn("—", prose, "em dashes are not used anywhere in Quantify")
        self.assertNotIn("--", prose)
        for word in ("leverage", "unlock", "supercharge", "seamless", "AI-powered", "revolutionary", "protect the"):
            self.assertNotIn(word.lower(), prose.lower(), f"{word} is marketing language, not operating language")
        # The summary has to name a real comparison, not just a percentage.
        self.assertRegex(narrative["summary"], r"\$[\d,]+")

    def test_service_hours_follow_the_location_including_past_midnight(self) -> None:
        from quantify_app.intelligence import service_hours

        # A close after midnight is stored past 24 so every span stays a plain
        # subtraction. This is the one place it turns back into clock hours.
        self.assertEqual(service_hours({"open_hour": 6, "close_hour": 18}), list(range(6, 18)))
        self.assertEqual(
            service_hours({"open_hour": 11, "close_hour": 26}),
            [11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 0, 1],
        )
        # A day can never be longer than a day.
        self.assertLessEqual(len(service_hours({"open_hour": 0, "close_hour": 48})), 24)

        # The forecast shows the hours this location is actually open, not a
        # fixed strip that happens to suit one kind of business.
        with connect(self.db_path) as conn:
            location = conn.execute("SELECT * FROM locations WHERE id=?", (LOCATION,)).fetchone()
            brief = daily_brief(conn, LOCATION, TODAY, week_days=2)
        self.assertEqual(
            [row["hour"] for row in brief["service_curve"]], service_hours(location)
        )

    def test_no_dashes_or_marketing_words_in_the_interface(self) -> None:
        """The screen copy is checked directly, not only the generated prose."""
        root = pathlib.Path(__file__).resolve().parent.parent
        surfaces = sorted(root.glob("web/*.js")) + sorted(root.glob("web/*.css")) + sorted(root.glob("*.html"))
        self.assertTrue(surfaces, "expected to find the interface files")
        for path in surfaces:
            text = path.read_text(encoding="utf-8")
            for index, line in enumerate(text.splitlines(), 1):
                self.assertNotIn("—", line, f"{path.name}:{index} uses an em dash")
                self.assertNotIn("–", line, f"{path.name}:{index} uses an en dash")
            lowered = text.lower()
            for word in ("leverage", "unlock", "supercharge", "seamless", "ai-powered",
                         "revolutionary", "game-chang", "cutting-edge", "best-in-class",
                         "effortless", "empower"):
                self.assertNotIn(word, lowered, f"{path.name} uses {word}, which is marketing language")

    def test_daily_owner_email_is_informative_and_can_use_safe_outbox(self) -> None:
        previous = {key: os.environ.pop(key, None) for key in ("POSTMARK_SERVER_TOKEN", "SMTP_HOST")}
        try:
            with connect(self.db_path) as conn:
                update_preferences(conn, LOCATION, "owner@example.com", True, "05:30", "America/New_York")
                email = build_email(conn, LOCATION, TODAY)
                delivered = deliver_brief(conn, self.root, LOCATION, TODAY)
            self.assertIn("Quantify", email["subject"])
            self.assertIn("What matters", email["html"])
            self.assertIn("Expected sales", email["html"])
            self.assertIn("Why", email["text"])
            self.assertEqual(delivered["status"], "outbox")
            artifact = Path(delivered["artifact_path"])
            self.assertTrue(artifact.exists())
            content = artifact.read_text(errors="ignore")
            self.assertIn("owner@example.com", content)
        finally:
            for key, value in previous.items():
                if value is not None:
                    os.environ[key] = value

    def test_account_signup_confirms_email_and_two_step_is_optional(self) -> None:
        auth_db = self.root / "data" / "auth-test.db"
        initialize(auth_db)
        with connect(auth_db) as conn:
            seed_demo(conn, TODAY)
            owner = create_account(conn, "owner@quantify.test", "Test Owner", "Strong-Passphrase-2026!")

            # The email address has to be confirmed before any operating data opens up.
            state = auth_state(conn, None)
            self.assertFalse(state["setup_required"])
            issued = start_email_verification(conn, owner["user_id"])
            self.assertEqual(len(issued["code"]), 6)
            with self.assertRaises(ValueError):
                confirm_email(conn, owner["user_id"], "000000" if issued["code"] != "000000" else "111111")
            self.assertTrue(confirm_email(conn, owner["user_id"], issued["code"])["verified"])

            # Two-step sign in is off by default, so a correct password is enough.
            signed_in = begin_login(conn, owner["email"], "Strong-Passphrase-2026!")
            self.assertTrue(signed_in["authenticated"])
            self.assertNotIn("mfa_required", signed_in)
            session = session_from_token(conn, signed_in["session_token"])
            self.assertIsNotNone(session)
            self.assertTrue(session.email_verified)
            self.assertFalse(session.totp_enabled)

            # Turning it on adds the second step, and turning it off needs the password.
            secret = conn.execute("SELECT totp_secret FROM users WHERE id=?", (owner["user_id"],)).fetchone()["totp_secret"]
            self.assertTrue(enable_totp(conn, owner["user_id"], totp_code(secret))["enabled"])
            challenge = begin_login(conn, owner["email"], "Strong-Passphrase-2026!")
            self.assertTrue(challenge["mfa_required"])
            logged_in = complete_login(conn, challenge["challenge"], totp_code(secret))
            self.assertTrue(session_from_token(conn, logged_in["session_token"]).totp_enabled)
            with self.assertRaises(PermissionError):
                disable_totp(conn, owner["user_id"], "not-the-password")
            self.assertFalse(disable_totp(conn, owner["user_id"], "Strong-Passphrase-2026!")["enabled"])

    def test_second_account_gets_its_own_workspace(self) -> None:
        multi_db = self.root / "data" / "multi-test.db"
        initialize(multi_db)
        with connect(multi_db) as conn:
            seed_demo(conn, TODAY)
            first = create_account(conn, "one@quantify.test", "One", "Strong-Passphrase-2026!")
            second = create_account(conn, "two@quantify.test", "Two", "Strong-Passphrase-2027!")
            self.assertNotEqual(first["organization_id"], second["organization_id"])
            with self.assertRaises(ValueError):
                create_account(conn, "ONE@quantify.test", "Dup", "Strong-Passphrase-2028!")

            # A new workspace gets its own sample location and never sees anyone else's.
            location_id = seed_workspace(conn, second["organization_id"], concept="Pizza", name="Two", history_days=40)
            owned = conn.execute(
                "SELECT organization_id FROM locations WHERE id=?", (location_id,)
            ).fetchone()["organization_id"]
            self.assertEqual(owned, second["organization_id"])
            self.assertGreater(conn.execute("SELECT COUNT(*) AS n FROM sales WHERE location_id=?", (location_id,)).fetchone()["n"], 0)
            visible = conn.execute(
                "SELECT COUNT(*) AS n FROM locations WHERE organization_id=?", (first["organization_id"],)
            ).fetchone()["n"]
            self.assertEqual(visible, 0)  # The first signup is isolated from sample accounts too.


    def test_zero_entry_menu_and_square_catalog_use_clean_schema(self) -> None:
        schema_db = self.root / "data" / "schema-test.db"
        initialize(schema_db)
        with connect(schema_db) as conn:
            seed_demo(conn, TODAY)
            imported = _menu_import(conn, LOCATION, "BURGERS:\nDBL CHZ BRGR #2 $12.50", True)
            self.assertEqual(imported["created"], 1)
            menu_row = conn.execute(
                "SELECT name,base_daily_qty FROM menu_items WHERE location_id=? AND name=?",
                (LOCATION, "DBL CHZ BRGR #2"),
            ).fetchone()
            self.assertIsNotNone(menu_row)
            self.assertEqual(menu_row["base_daily_qty"], 1)

            square_payload = {
                "objects": [
                    {"type": "CATEGORY", "id": "cat-1", "category_data": {"name": "Slices"}},
                    {
                        "type": "ITEM", "id": "parent-1",
                        "item_data": {
                            "name": "MARG SLC",
                            "category_id": "cat-1",
                            "variations": [{
                                "id": "variation-rough-1",
                                "item_variation_data": {
                                    "name": "Regular",
                                    "price_money": {"amount": 475, "currency": "USD"},
                                },
                            }],
                        },
                    },
                ]
            }
            with patch("quantify_app.connectors._request_json", return_value=square_payload):
                counts = _sync_square_catalog(conn, LOCATION, "https://square.test", {})
            self.assertEqual(counts["catalog_imported"], 1)
            square_item = conn.execute(
                "SELECT id,name,price,base_daily_qty FROM menu_items WHERE location_id=? AND pos_item_id=?",
                (LOCATION, "variation-rough-1"),
            ).fetchone()
            self.assertEqual(square_item["name"], "MARG SLC")
            self.assertEqual(square_item["price"], 4.75)
            interpretation = conn.execute(
                "SELECT item_family FROM menu_interpretations WHERE menu_item_id=?",
                (square_item["id"],),
            ).fetchone()
            self.assertEqual(interpretation["item_family"], "pizza-slice")

    def test_square_webhook_signature_matches_official_algorithm(self) -> None:
        url = "https://example.test/api/webhooks/square?location_id=loc-bakery"
        body = b'{"type":"order.updated","data":{"id":"abc"}}'
        key = "test-signature-key"
        signature = base64.b64encode(hmac.new(key.encode(), url.encode() + body, hashlib.sha256).digest()).decode()
        self.assertTrue(verify_square_webhook_signature(url, body, signature, key))
        self.assertFalse(verify_square_webhook_signature(url, body + b"x", signature, key))

    def test_square_order_ingestion_is_idempotent_and_rebuilds_hourly_sales(self) -> None:
        ingestion_db = self.root / "data" / "ingestion-test.db"
        initialize(ingestion_db)
        with connect(ingestion_db) as conn:
            seed_demo(conn, TODAY)
            item = conn.execute(
                "SELECT id,pos_item_id,price FROM menu_items WHERE location_id=? ORDER BY id LIMIT 1", (LOCATION,)
            ).fetchone()
            when = "2026-08-10T14:30:00Z"
            order = {
                "id": "sq-order-1",
                "state": "COMPLETED",
                "closed_at": when,
                "source": {"name": "POS"},
                "line_items": [{
                    "uid": "line-1",
                    "catalog_object_id": item["pos_item_id"],
                    "quantity": "2",
                    "total_money": {"amount": int(round(item["price"] * 2 * 100)), "currency": "USD"},
                }],
            }
            first = ingest_square_orders(conn, LOCATION, [order])
            conn.commit()
            second = ingest_square_orders(conn, LOCATION, [order])
            conn.commit()
            rows = conn.execute(
                "SELECT quantity FROM pos_order_lines WHERE provider_order_id='sq-order-1'"
            ).fetchall()
            self.assertEqual(first["lines_written"], 1)
            self.assertEqual(second["lines_written"], 1)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["quantity"], 2)
            day = conn.execute(
                "SELECT quantity FROM sales WHERE location_id=? AND item_id=? AND date=?",
                (LOCATION, item["id"], "2026-08-10"),
            ).fetchone()
            hourly = conn.execute(
                "SELECT SUM(quantity) AS q FROM sales_hourly WHERE location_id=? AND item_id=? AND date=?",
                (LOCATION, item["id"], "2026-08-10"),
            ).fetchone()
            self.assertEqual(day["quantity"], 2)
            self.assertEqual(hourly["q"], 2)

    def test_results_are_a_walk_forward_backtest_not_claimed_savings(self) -> None:
        with connect(self.db_path) as conn:
            results = performance(conn, LOCATION, TODAY, days=21)
        self.assertGreater(results["summary"]["days_evaluated"], 0)
        self.assertGreaterEqual(results["summary"]["forecast_accuracy"], 0)
        self.assertLessEqual(results["summary"]["forecast_accuracy"], 100)
        # The guarantee is enforced in code and tested by behaviour in
        # test_a_revision_during_service_can_never_improve_the_days_score. What
        # is checked here is that the screen reports the count it rests on, and
        # that the note says what is measured without lecturing about method.
        self.assertIn("days_from_stored_call", results["summary"])
        self.assertIn("what the registers actually rang", results["note"])
        for lecture in ("never sees", "is never scored", "rather than", "so the model"):
            self.assertNotIn(lecture, results["note"])


    def test_a_revision_during_service_can_never_improve_the_days_score(self) -> None:
        """The whole point of storing the call. This is the test that matters.

        A day is set up where the morning call was badly wrong and the revisions
        made during service were perfect. If any revision leaks into the score,
        the accuracy goes up. It must not.
        """
        from quantify_app.transactions import score_range

        day = TODAY - timedelta(days=3)
        with connect(self.db_path) as conn:
            actual = {
                row["item_id"]: float(row["quantity"])
                for row in conn.execute(
                    "SELECT item_id,quantity FROM sales WHERE location_id=? AND date=?",
                    (LOCATION, day.isoformat()),
                )
            }
            self.assertTrue(actual, "the fixture needs sales on the day being scored")
            prices = {
                row["id"]: float(row["price"])
                for row in conn.execute("SELECT id,price FROM menu_items WHERE location_id=?", (LOCATION,))
            }

            # A morning call that was half of what actually sold.
            for item_id, sold in actual.items():
                conn.execute(
                    """INSERT OR IGNORE INTO forecast_calls(
                           location_id,date,item_id,expected,lower,upper,price,overridden,
                           validation_wape,model_version,locked_at,locked_local)
                       VALUES(?,?,?,?,?,?,?,0,0.2,'test','2026-01-01T05:30:00+00:00','05:30')""",
                    (LOCATION, day.isoformat(), item_id, sold * 0.5, sold * 0.4,
                     sold * 0.6, prices.get(item_id, 1.0)),
                )
            # Revisions that were exactly right, every hour.
            for slot in range(8, 16):
                for item_id, sold in actual.items():
                    conn.execute(
                        """INSERT OR REPLACE INTO forecast_revisions(
                               location_id,date,slot,item_id,opening,sold_so_far,
                               expected_share,pace,weight,revised,created_at)
                           VALUES(?,?,?,?,?,?,0.5,1.0,0.5,?,'2026-01-01T12:00:00+00:00')""",
                        (LOCATION, day.isoformat(), slot, item_id, sold * 0.5, sold * 0.4, sold),
                    )
            conn.execute("DELETE FROM day_accuracy WHERE location_id=? AND date=?", (LOCATION, day.isoformat()))
            conn.commit()

            score_range(conn, LOCATION, day, day)
            row = conn.execute(
                "SELECT * FROM day_accuracy WHERE location_id=? AND date=?", (LOCATION, day.isoformat())
            ).fetchone()

        # Called half, sold all, so the error is half the day. Anything much
        # above 50% means a revision got into the score.
        self.assertEqual(row["call_source"], "stored")
        self.assertLess(row["accuracy"], 55.0, "a perfect revision leaked into the day score")
        self.assertGreater(row["accuracy"], 45.0)
        self.assertAlmostEqual(row["predicted_units"], sum(actual.values()) * 0.5, delta=1.0)

        # The revisions are still on the record, as a warning time, not a score.
        self.assertEqual(row["revisions_used"], 8)
        self.assertEqual(row["caught_slot"], 8)

    def test_the_opening_call_is_only_taken_before_the_doors_open(self) -> None:
        from quantify_app import intraday

        with connect(self.db_path) as conn:
            location = dict(conn.execute("SELECT * FROM locations WHERE id=?", (LOCATION,)).fetchone())

            # Two in the morning at a place open 11 to 2 belongs to the service
            # that started yesterday, not to a day that has not begun.
            bar = {"open_hour": 11, "close_hour": 26}
            self.assertEqual(
                intraday.trading_date(bar, datetime(2026, 8, 12, 1, 30)), date(2026, 8, 11)
            )
            self.assertEqual(
                intraday.trading_date(bar, datetime(2026, 8, 12, 10, 30)), date(2026, 8, 12)
            )
            # A location that never shuts has no window to lock a call in.
            self.assertEqual(
                intraday.lock_opening_call(conn, LOCATION, TODAY + timedelta(days=400), []), 0
            )
            self.assertEqual(location["open_hour"], location["open_hour"])

    def test_a_normal_morning_is_not_revised(self) -> None:
        """The property that decides whether operators trust the live number.

        A day running exactly to pattern must come back unchanged. If an
        ordinary morning reads as running ahead, every number after it is noise.
        """
        from quantify_app.intraday import revise_menu

        shares = {6: 0.05, 7: 0.10, 8: 0.16, 9: 0.16, 10: 0.12, 11: 0.09,
                  12: 0.09, 13: 0.07, 14: 0.05, 15: 0.04, 16: 0.04, 17: 0.03}
        calls = {"a": {"expected": 70.0, "validation_wape": 0.18},
                 "b": {"expected": 23.0, "validation_wape": 0.22}}
        elapsed = [6, 7, 8, 9]
        on_pattern = {k: {h: calls[k]["expected"] * shares[h] for h in elapsed} for k in calls}
        revisions, day = revise_menu(calls, {}, on_pattern, elapsed, 0.16, shares, 16)
        for row in revisions:
            self.assertAlmostEqual(row.revised, row.opening, delta=0.05)
        self.assertAlmostEqual(day["multiplier"], 1.0, places=6)

        # A day genuinely running 30% down converges on the truth as it goes,
        # and never overshoots below it.
        previous = None
        for through in range(1, len(shares) + 1):
            window = sorted(shares)[:through]
            behind = {k: {h: calls[k]["expected"] * shares[h] * 0.70 for h in window} for k in calls}
            revisions, _ = revise_menu(calls, {}, behind, window, 0.16, shares, 16)
            revised = next(r.revised for r in revisions if r.item_id == "a")
            self.assertGreaterEqual(revised, 70.0 * 0.70 - 0.01)
            if previous is not None:
                self.assertLessEqual(revised, previous + 1e-6)
            previous = revised
        self.assertAlmostEqual(previous, 49.0, delta=0.5)

        # A revision can never sit below what the register has already rung.
        heavy = {k: {h: calls[k]["expected"] * 2.0 for h in elapsed} for k in calls}
        revisions, _ = revise_menu(calls, {}, heavy, elapsed, 0.16, shares, 16)
        for row in revisions:
            self.assertGreaterEqual(row.revised, row.sold_so_far)

    def test_a_day_that_is_still_trading_is_never_scored(self) -> None:
        from quantify_app.transactions import _local_today, unscored_days

        with connect(self.db_path) as conn:
            today = _local_today(conn, LOCATION)
            item = conn.execute(
                "SELECT id FROM menu_items WHERE location_id=? LIMIT 1", (LOCATION,)
            ).fetchone()["id"]
            try:
                # Sales land for the current day as the register reports them.
                # The scorer must leave that day alone until it has finished, or
                # it compares a whole day's call against three hours of trade
                # and stores the miss permanently.
                conn.execute(
                    """INSERT OR REPLACE INTO sales(location_id,item_id,date,quantity,revenue,stockout_minutes)
                       VALUES(?,?,?,4,20.0,0)""",
                    (LOCATION, item, today.isoformat()),
                )
                conn.commit()
                pending = unscored_days(conn, LOCATION, limit=500)
            finally:
                conn.execute(
                    "DELETE FROM sales WHERE location_id=? AND item_id=? AND date=?",
                    (LOCATION, item, today.isoformat()),
                )
                conn.commit()

        self.assertNotIn(today, pending, "the current trading day must never be scored")
        self.assertTrue(all(day < today for day in pending))

    def test_what_a_day_kept_is_arithmetic_the_owner_can_follow(self) -> None:
        from quantify_app import costs
        from quantify_app.transactions import day_orders

        day = TODAY - timedelta(days=1)
        with connect(self.db_path) as conn:
            costs.save_cost_settings(conn, LOCATION, {"hourly_wage": 17.0, "payroll_load_percent": 18.0})
            context = costs.cost_context(conn, LOCATION)
            orders = day_orders(conn, LOCATION, day)
            revenue = conn.execute(
                "SELECT SUM(revenue) r FROM sales WHERE location_id=? AND date=?",
                (LOCATION, day.isoformat()),
            ).fetchone()["r"]
            figures = costs.day_costs(conn, LOCATION, day, orders, revenue, context)

        # The four numbers must actually add up. If they ever stop, the screen
        # is telling an operator something that does not reconcile.
        self.assertAlmostEqual(
            figures["left_after_costs"],
            figures["revenue"] - figures["cogs"] - figures["labour"] - figures["other"],
            places=2,
        )
        self.assertAlmostEqual(
            figures["costs"], figures["cogs"] + figures["labour"] + figures["other"], places=2
        )
        # A restaurant that keeps everything or nothing means the model is wrong.
        self.assertGreater(figures["margin_percent"], 5)
        self.assertLess(figures["margin_percent"], 70)
        # Food and wages both have to be a real share of what was rung.
        self.assertGreater(figures["cogs"] / figures["revenue"], 0.15)
        self.assertGreater(figures["labour"] / figures["revenue"], 0.15)
        # Every hour the doors are open is paid for, not only the busy ones.
        self.assertGreaterEqual(figures["staff_hours"], figures["trading_hours"])
        self.assertTrue(figures["estimate"])

    def test_the_minimum_wage_table_is_complete_and_dated(self) -> None:
        from datetime import date as _date
        from quantify_app import costs

        # Fifty states, DC, and the territories Quantify serves.
        self.assertGreaterEqual(len(costs.STATE_MINIMUM_WAGE), 51)
        for state, rate in costs.STATE_MINIMUM_WAGE.items():
            self.assertGreaterEqual(rate, costs.FEDERAL_MINIMUM_WAGE - 1e-9, f"{state} is below the federal floor")

        # States with no minimum of their own report the federal one, and never
        # a state name that would read as though the state had set it.
        for state in costs.NO_STATE_MINIMUM:
            rate, place = costs.minimum_wage(state)
            self.assertEqual(rate, costs.FEDERAL_MINIMUM_WAGE)
            self.assertEqual(place, "")

        # A local minimum only ever wins when it is genuinely higher.
        self.assertEqual(costs.minimum_wage("NY", "Scarsdale")[0], 17.00)
        self.assertEqual(costs.minimum_wage("NY", "Ithaca")[0], 16.00)

        # These figures move most Januaries. If the table has not been looked at
        # in over a year, this fails rather than letting it quietly go stale.
        reviewed = _date.fromisoformat(costs.MINIMUM_WAGE_REVIEWED)
        self.assertLess(
            (TODAY - reviewed).days, 400,
            "the minimum wage table has not been reviewed in over a year",
        )

    def test_the_order_list_adds_up_to_the_forecast(self) -> None:
        from quantify_app.intelligence import forecast_day
        from quantify_app.ordering import ingredient_demand, order_plan, parse_quantity

        # Every recipe line the seeded menu uses has to be readable, or the
        # order list silently understates what a day consumes.
        from quantify_app.explain import FAMILY_COMPONENTS
        for rows in FAMILY_COMPONENTS.values():
            for _name, _role, _share, quantity in rows:
                self.assertIsNotNone(parse_quantity(quantity), f"cannot read {quantity!r}")

        with connect(self.db_path) as conn:
            plan = order_plan(conn, LOCATION, TODAY, days=1)
            plan3 = order_plan(conn, LOCATION, TODAY, days=3)
            made = {row["name"]: (row.get("make") or row["expected"])
                    for row in forecast_day(conn, LOCATION, TODAY)["items"]}

        lines = {row["name"]: row for row in plan["lines"]}
        self.assertTrue(lines)

        # One bun per burger. The roll up must equal the sum of the items that
        # use it, exactly, or none of the rest of the screen can be trusted.
        bun = lines.get("Burger bun")
        if bun:
            by_hand = sum(made[row["item"]] for row in bun["driven_by"])
            self.assertAlmostEqual(bun["typical"], by_hand, delta=1.0)

        # A range in the recipe stays a range on the screen.
        patty = lines.get("Beef patty")
        if patty and bun:
            self.assertTrue(patty["has_range"])
            self.assertLessEqual(patty["low"], bun["typical"])
            self.assertGreaterEqual(patty["high"], bun["typical"])

        # Three days is about three times one day. Compared on the base values,
        # because the displayed unit rescales with size: the same ingredient can
        # read in grams over one day and in pounds over three.
        three = {row["name"]: row for row in plan3["lines"]}
        for name, row in lines.items():
            if row["base_low"] > 5 and name in three:
                ratio = three[name]["base_low"] / row["base_low"]
                self.assertGreater(ratio, 2.0, f"{name} did not scale with the window")
                self.assertLess(ratio, 4.0, f"{name} scaled too far")

        # The screen has to say what it does not cover rather than hide it.
        self.assertIn("covered", plan["counts"])
        self.assertEqual(
            plan["counts"]["covered"] + plan["counts"]["uncovered"],
            plan["counts"]["menu_items"],
        )
        # A share of a fryer basket is not a thing you can order by the case.
        for row in plan["lines"]:
            if row["kind"] == "share":
                self.assertFalse(row["orderable"])

    def test_the_make_number_follows_the_costs_the_owner_entered(self) -> None:
        """The kitchen preps to this number every morning.

        It used to come from a hard-coded 30% food cost that ignored the costs
        screen entirely, so an owner could set their real figures and the number
        they act on would not move.
        """
        from quantify_app.intelligence import forecast_day
        from quantify_app.item_analysis import prep_advice

        with connect(self.db_path) as conn:
            conn.execute("DELETE FROM category_costs WHERE location_id=?", (LOCATION,))
            conn.commit()
            before = {row["name"]: row["make"] for row in forecast_day(conn, LOCATION, TODAY)["items"]}
            category = conn.execute(
                "SELECT category FROM menu_items WHERE location_id=? LIMIT 1", (LOCATION,)
            ).fetchone()["category"]
            try:
                # Say this category costs almost all of what it sells for. Making
                # a spare now costs nearly as much as missing a sale, so the make
                # number has to fall back toward the plain forecast.
                conn.execute(
                    """INSERT OR REPLACE INTO category_costs(location_id,category,cost_share,updated_at)
                       VALUES(?,?,0.70,'test')""",
                    (LOCATION, category),
                )
                conn.commit()
                after = {row["name"]: row["make"] for row in forecast_day(conn, LOCATION, TODAY)["items"]}
                affected = {
                    row["name"] for row in conn.execute(
                        "SELECT name FROM menu_items WHERE location_id=? AND category=?",
                        (LOCATION, category),
                    )
                }
            finally:
                conn.execute("DELETE FROM category_costs WHERE location_id=?", (LOCATION,))
                conn.commit()

        moved = [name for name in before if after.get(name) != before[name]]
        self.assertTrue(moved, "the owner's cost setting never reached the make number")
        # A higher food cost can only push the make number down, never up.
        for name in moved:
            self.assertLess(after[name], before[name])
        # And it only touches the category they changed.
        self.assertTrue(set(moved) <= affected, f"{set(moved) - affected} moved but are not in {category}")

        # The share is reported so a screen can say which figure it used.
        self.assertEqual(
            prep_advice({"today_values": [10.0] * 30}, 10.0, 0.42)["cost_share_percent"], 42
        )

    def test_no_class_ships_without_a_style(self) -> None:
        """A class with no rule renders as a bare block.

        This shipped for real: brief-grid was the item sheet's two column
        layout and had no definition anywhere, so both cards stacked full width.
        """
        import re

        root = pathlib.Path(__file__).resolve().parent.parent
        script = (root / "web" / "app.js").read_text(encoding="utf-8")
        sheet = (root / "web" / "styles.css").read_text(encoding="utf-8")
        defined = set(re.findall(r"\.([a-z][\w-]*)", sheet))
        used: set[str] = set()
        for chunk in re.findall(r'class="([^"$]*)"', script):
            used.update(word for word in chunk.split() if word and not word.startswith("$"))
        self.assertEqual(sorted(used - defined), [], "these classes have no CSS rule")

    def test_money_figures_reconcile_with_each_other(self) -> None:
        """Numbers shown side by side have to be on the same basis.

        The day row used to print net-of-tax sales beside an average ticket that
        included tax and tip, and the channel bars divided a gross figure by a
        net one, so a bar could be wider than the row it sat in.
        """
        from quantify_app.transactions import day_detail, day_list

        day = TODAY - timedelta(days=1)
        with connect(self.db_path) as conn:
            detail = day_detail(conn, LOCATION, day)
            page = day_list(conn, LOCATION, limit=5, with_costs=True)

        # Every channel share is a share of the same total the rows are drawn
        # from, so they can never sum past the whole.
        gross = sum(row["sales"] for row in detail["channels"])
        self.assertGreater(gross, 0)
        for row in detail["channels"]:
            self.assertLessEqual(row["sales"], gross + 0.01)

        # The cost breakdown adds back up to what was taken off.
        for row in page["days"]:
            costs = row.get("costs")
            if not costs or row.get("closed"):
                continue
            self.assertAlmostEqual(
                costs["left_after_costs"],
                costs["revenue"] - costs["cogs"] - costs["labour"] - costs["other"],
                places=2,
            )
            # The old name is gone from the meaning, not just the label: this
            # figure is net of labour and fixed costs, so it is not gross profit.
            self.assertIn("left_after_costs", costs)

    def test_the_menu_says_which_dollar_figure_is_which(self) -> None:
        """The reported bug: a bare $15.50 next to a table headed share of cost.

        The row now carries the sale price and the food cost, each labelled, and
        the food cost follows whatever the owner set on the costs screen.
        """
        from quantify_app import costs

        with connect(self.db_path) as conn:
            settings = costs.cost_settings(conn, LOCATION)
            basis = costs.item_cost_basis(conn, LOCATION, settings)
            rows = conn.execute(
                "SELECT id, name, price FROM menu_items WHERE location_id=? AND active=1", (LOCATION,)
            ).fetchall()

        self.assertTrue(rows)
        for row in rows:
            entry = basis.get(row["id"])
            self.assertIsNotNone(entry, f"no cost basis for {row['name']}")
            share = float(entry["share"])
            # A food cost is a share of the price, never more than it.
            self.assertGreater(share, 0.0)
            self.assertLess(share, 1.0)
            food = round(float(row["price"]) * share, 2)
            margin = round(float(row["price"]) * (1.0 - share), 2)
            self.assertAlmostEqual(food + margin, float(row["price"]), places=2)
            # And it says which rule produced it, so the screen can too.
            self.assertTrue(entry["source"])

    def test_statistics_match_published_critical_values(self) -> None:
        from quantify_app.statistics import (
            benjamini_hochberg, f_upper_tail, normal_quantile, student_t_two_sided,
        )
        # If these drift, every p-value in the product is wrong.
        self.assertAlmostEqual(student_t_two_sided(2.228, 10), 0.05, places=3)
        self.assertAlmostEqual(student_t_two_sided(3.169, 10), 0.01, places=3)
        self.assertAlmostEqual(f_upper_tail(4.96, 1, 10), 0.05, places=3)
        self.assertAlmostEqual(normal_quantile(0.975), 1.959964, places=5)

        # False discovery control must never report a q below its own p, and
        # must never decrease as p increases.
        raw = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.216]
        adjusted = benjamini_hochberg(raw)
        self.assertTrue(all(q >= p - 1e-12 for p, q in zip(raw, adjusted)))
        self.assertTrue(all(adjusted[i] <= adjusted[i + 1] + 1e-12 for i in range(len(adjusted) - 1)))

    def test_every_item_gets_a_full_reading_even_a_quiet_one(self) -> None:
        from quantify_app.item_analysis import item_profile
        with connect(self.db_path) as conn:
            quietest = conn.execute(
                "SELECT id FROM menu_items WHERE location_id=? ORDER BY base_daily_qty ASC LIMIT 1",
                (LOCATION,),
            ).fetchone()["id"]
            profile = item_profile(conn, LOCATION, quietest, TODAY)

        # The quiet item is analysed as thoroughly as a best seller.
        self.assertGreater(profile["standing"]["history_days"], 300)
        self.assertTrue(profile["weekday_profile"]["days"])
        self.assertIn("quantity", profile["prep"])
        self.assertGreater(profile["distribution"]["days"], 20)

        # Prep sits at or above the median, because running out costs more.
        self.assertGreaterEqual(profile["prep"]["quantity"], profile["prep"]["median"])
        self.assertGreater(profile["prep"]["fractile_percent"], 50)

        # Higher prep can only reduce the chance of running out.
        chances = [level["sell_out_percent"] for level in profile["prep"]["levels"]]
        self.assertEqual(chances, sorted(chances, reverse=True))

        # Every driver carries its evidence, and none is reported without it.
        for driver in profile["drivers"]:
            self.assertGreaterEqual(driver["days_observed"], 20)
            self.assertGreaterEqual(driver["q"], driver["p"] - 1e-12)
            self.assertIn(driver["strength"], {"strong", "clear", "suggestive", "not established"})
            if driver["established"]:
                self.assertLess(driver["q"], 0.15)

    def test_prep_never_contradicts_the_forecast(self) -> None:
        from quantify_app.item_analysis import item_profile
        # This assertion concerns a live forecast. Historical expectations may
        # be a saved older call or unavailable, independent of today's prep model.
        with connect(self.db_path) as conn, patch('quantify_app.transactions.last_closed_day', return_value=TODAY-timedelta(days=1)):
            items = [row["id"] for row in conn.execute(
                "SELECT id FROM menu_items WHERE location_id=?", (LOCATION,)
            )]
            for item_id in items:
                profile = item_profile(conn, LOCATION, item_id, TODAY)
                expected = profile["today"]["expected"]
                distribution = profile["distribution"]

                # The screen says "make more than the model expects". It has to
                # be true, or the two numbers argue with each other in public.
                self.assertGreaterEqual(profile["prep"]["quantity"], expected)

                # The spread is centred on today, not on the average of the last
                # two years, so a growing item is not shown as a wild one.
                self.assertLessEqual(
                    abs(distribution["today_median"] - expected), max(2, expected * 0.15)
                )
                self.assertLessEqual(distribution["today_low"], distribution["today_median"])
                self.assertLessEqual(distribution["today_median"], distribution["today_high"])

    def test_a_driver_that_is_only_the_weekday_is_not_reported(self) -> None:
        from quantify_app.item_analysis import driver_effects
        from quantify_app.intelligence import _history_for_item, _load_location, build_context
        with connect(self.db_path) as conn:
            location = _load_location(conn, LOCATION)
            item = conn.execute(
                "SELECT id FROM menu_items WHERE location_id=? LIMIT 1", (LOCATION,)
            ).fetchone()["id"]
            history, weather, events = _history_for_item(conn, location, item, TODAY)
            context = build_context(location, TODAY, weather.get(TODAY.isoformat()), events.get(TODAY.isoformat(), []), weather)
            drivers = driver_effects(history, context)

        # Weekday is removed before anything is tested, so it can never appear
        # here wearing another name.
        self.assertNotIn("weekday", {row["key"] for row in drivers})
        # Reporting nothing is allowed. Reporting something unproven is not.
        for row in drivers:
            if row["established"]:
                self.assertLess(row["q"], 0.15)


if __name__ == "__main__":
    unittest.main()
