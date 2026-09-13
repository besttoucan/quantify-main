from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from quantify_app import updates
from quantify_app.database import connect, initialize


class OperatingUpdatesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "updates.sqlite3"
        initialize(self.db)
        self.now = datetime(2026, 9, 13, 9, tzinfo=timezone.utc)
        with connect(self.db) as conn:
            conn.execute("INSERT INTO organizations(id,name,created_at) VALUES('org','Test shop',?)", (self.now.isoformat(),))
            for key in ("one", "two"):
                conn.execute("""INSERT INTO locations
                    (id,organization_id,name,concept,address,city,region,postal_code,latitude,longitude,timezone,open_hour,close_hour)
                    VALUES(?,'org',?,'cafe','','Test city','NY','',0,0,'UTC',8,18)""", (key, key))
            conn.execute("INSERT INTO menu_items(id,location_id,name,category,price) VALUES('coffee','one','Coffee','Drinks',4)")
            conn.commit()

    def note(self, key="event", severity="important", hours=2):
        return updates._note(key, "stock", severity, "Check coffee", "There is one bag left.",
                             "Counted this morning.", self.now, expires=self.now + timedelta(hours=hours))

    def test_expired_time_sensitive_note_is_history_and_never_an_opening_alert(self):
        with connect(self.db) as conn:
            updates.save_observations(conn, "one", [self.note()], self.now)
            current = updates.feed(conn, "one", "sam", self.now)
            self.assertIsNotNone(current["notification"])
            later = updates.feed(conn, "one", "sam", self.now + timedelta(days=2))
            self.assertIsNone(later["notification"])
            self.assertEqual(later["unread_count"], 0)
            self.assertEqual(later["notes"], [])
            self.assertEqual(later["earlier"][0]["state"], "expired")

    def test_seen_popup_does_not_repeat_but_remains_unread_in_feed(self):
        with connect(self.db) as conn:
            updates.save_observations(conn, "one", [self.note()], self.now)
            note_id = updates.feed(conn, "one", "sam", self.now)["notification"]["id"]
            updates.acknowledge(conn, "one", "sam", [note_id], now=self.now)
        with connect(self.db) as conn:
            same = updates.feed(conn, "one", "sam", self.now)
            self.assertIsNone(same["notification"])
            self.assertEqual(same["unread_count"], 1)
            self.assertIsNotNone(updates.feed(conn, "one", "alex", self.now)["notification"])
            updates.acknowledge(conn, "one", "sam", [note_id], read=True, now=self.now)
            self.assertEqual(updates.feed(conn, "one", "sam", self.now)["unread_count"], 0)

    def test_pattern_is_timely_only_within_its_window_and_never_an_opening_alert(self):
        note = updates._note("pattern", "pattern", "notice", "Coffee window", "Check coffee.",
                             "Four Sundays.", self.now, starts=self.now + timedelta(minutes=30),
                             expires=self.now + timedelta(hours=2))
        with connect(self.db) as conn:
            updates.save_observations(conn, "one", [note], self.now)
            early = updates.feed(conn, "one", "sam", self.now)
            self.assertIsNone(early["timely_notification"])
            current = updates.feed(conn, "one", "sam", self.now + timedelta(hours=1))
            self.assertIsNone(current["notification"])
            self.assertEqual(current["timely_notification"]["kind"], "pattern")
            late = updates.feed(conn, "one", "sam", self.now + timedelta(days=2))
            self.assertIsNone(late["timely_notification"])

    def test_refresh_preserves_receipt_and_resolves_removed_conditions(self):
        with connect(self.db) as conn:
            updates.save_observations(conn, "one", [self.note()], self.now)
            note_id = updates.feed(conn, "one", "sam", self.now)["notification"]["id"]
            updates.acknowledge(conn, "one", "sam", [note_id], now=self.now)
            updates.save_observations(conn, "one", [self.note()], self.now + timedelta(minutes=10))
            current = updates.feed(conn, "one", "sam", self.now)
            self.assertEqual(len(current["notes"]), 1)
            self.assertIsNone(current["notification"])
            updates.save_observations(conn, "one", [], self.now + timedelta(minutes=20))
            resolved = updates.feed(conn, "one", "sam", self.now + timedelta(minutes=20))
            self.assertIsNone(resolved["notification"])
            self.assertEqual(resolved["earlier"][0]["state"], "resolved")

    def test_escalation_gets_a_new_alert(self):
        with connect(self.db) as conn:
            updates.save_observations(conn, "one", [self.note(severity="notice")], self.now)
            note_id = updates.feed(conn, "one", "sam", self.now)["notes"][0]["id"]
            updates.acknowledge(conn, "one", "sam", [note_id], read=True, now=self.now)
            updates.save_observations(conn, "one", [self.note()], self.now)
            result = updates.feed(conn, "one", "sam", self.now)
            self.assertIsNotNone(result["notification"])
            self.assertNotEqual(result["notification"]["id"], note_id)

    def test_location_scoping_and_receipt_validation_are_atomic(self):
        with connect(self.db) as conn:
            updates.save_observations(conn, "one", [self.note()], self.now)
            note_id = updates.feed(conn, "one", "sam", self.now)["notes"][0]["id"]
            self.assertEqual(updates.feed(conn, "two", "sam", self.now)["notes"], [])
            with self.assertRaises(ValueError):
                updates.acknowledge(conn, "two", "sam", [note_id], read=True, now=self.now)
            with self.assertRaises(ValueError):
                updates.acknowledge(conn, "one", "sam", [note_id, "missing"], read=True, now=self.now)
            conn.commit()
            self.assertIsNotNone(updates.feed(conn, "one", "sam", self.now)["notification"])

    def test_time_patterns_need_repeated_evidence_and_expire_after_window(self):
        with connect(self.db) as conn:
            for week in range(1, 5):
                day = (self.now.date() - timedelta(weeks=week)).isoformat()
                conn.execute("INSERT INTO sales(location_id,item_id,date,quantity,revenue) VALUES('one','coffee',?,20,80)", (day,))
                for hour, quantity in ((10, 12), (14, 8)):
                    conn.execute("""INSERT INTO sales_hourly(location_id,item_id,date,hour,quantity,revenue)
                        VALUES('one','coffee',?,?,?,?)""", (day, hour, quantity, quantity * 4))
                if week == 3:
                    self.assertEqual(updates.time_patterns(conn, "one", self.now), [])
            morning = updates.time_patterns(conn, "one", self.now)
            self.assertTrue(any("10 AM to 12 PM" in n["title"] for n in morning))
            self.assertTrue(any("4 of the last 4 Sundays" in n["evidence"] for n in morning))
            self.assertFalse(any("customer" in n["body"] for n in morning))
            after = updates.time_patterns(conn, "one", self.now + timedelta(hours=4))
            self.assertFalse(any("10 AM to 12 PM" in n["title"] for n in after))

    def test_stale_register_suppresses_current_time_predictions(self):
        brief = {"data_health": {"latest_sale_date": "2026-08-26"},
                 "summary": {"expected_revenue": 1000, "baseline_revenue": 1000}}
        with connect(self.db) as conn, patch.object(updates, "time_patterns") as patterns:
            notes = updates.observations(conn, "one", brief, {"lines": []}, self.now)
            patterns.assert_not_called()
            self.assertEqual(notes[0]["kind"], "register")
            self.assertEqual(notes[0]["severity"], "important")

    def test_assisted_review_can_rank_only_supplied_observations(self):
        brief = {"data_health": {"latest_sale_date": "2026-08-26"},
                 "summary": {"expected_revenue": 1000, "baseline_revenue": 900}}
        with connect(self.db) as conn, patch.object(updates, "daily_brief", return_value=brief) as brief_call, \
                patch.object(updates.supply, "attention", return_value={"lines": []}), \
                patch.object(updates.ai, "available", return_value=True), \
                patch.object(updates.ai, "generate", return_value={"order": ["1", "bogus", "1"], "_writer": "claude"}):
            updates.refresh(conn, "one", force=True, now=self.now)
            result = updates.feed(conn, "one", "sam", self.now)
            self.assertEqual(len(result["notes"]), 2)
            self.assertEqual(result["notification"]["kind"], "register")
            self.assertTrue(brief_call.call_args.kwargs["refresh"])
            updates.refresh(conn, "one", now=self.now + timedelta(minutes=1))
            self.assertEqual(brief_call.call_count, 1)


if __name__ == "__main__":
    unittest.main()
