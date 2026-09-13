"""Suppliers, pack sizes, shelf counts, run-out dates, and sent orders."""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from quantify_app import supply
from quantify_app.database import connect, initialize
from quantify_app.localtime import zone
from quantify_app.ordering import merge_key, order_plan
from quantify_app.seed import seed_demo

TODAY = date(2026, 8, 10)
LOCATION = "loc-burger"
ROOT = Path(__file__).resolve().parents[1]

# Words that must never reach an operator, and the em dash. Same rule the rest
# of the product is held to.
BANNED = re.compile(r"—|seamless|effortless|unlock|empower|streamline|supercharge|built for you|game-changing", re.I)
# Python copy is held to the plain-language rules as well: nothing that
# answers an objection nobody raised, nothing about folders on the server.
PYTHON_BANNED = re.compile(r"—|rather than|no recipe speaks|data/outbox|seamless|effortless|streamline", re.I)

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _now(hour: int = 10, minute: int = 0, day: date = TODAY) -> datetime:
    return datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute, tzinfo=zone("America/New_York"))


def _line(plan: dict, name: str) -> dict:
    return next(row for row in plan["lines"] if row["name"].lower() == name.lower())


class SupplyTests(unittest.TestCase):
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

    def setUp(self) -> None:
        with connect(self.db_path) as conn:
            for table in ("purchase_orders", "stock_counts", "supplier_items", "suppliers"):
                conn.execute(f"DELETE FROM {table} WHERE location_id=?", (LOCATION,))
            conn.commit()
        supply._CACHE.clear()
        # No test may reach a real mail server, whatever the machine has set.
        self._env = patch.dict(os.environ)
        self._env.start()
        for key in ("POSTMARK_SERVER_TOKEN", "SMTP_HOST", "QUANTIFY_FROM_EMAIL", "SMTP_FROM"):
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        self._env.stop()

    def _sysco(self, conn, **extra) -> dict:
        payload = {"name": "Sysco", "delivery_days": ["mon", "thu"], "cutoff_time": "15:00", "lead_days": 1, "order_email": "orders@example.com"}
        payload.update(extra)
        return supply.save_supplier(conn, LOCATION, payload)

    # -- suppliers -----------------------------------------------------------

    def test_supplier_round_trip_and_validation(self) -> None:
        with connect(self.db_path) as conn:
            saved = supply.save_supplier(conn, LOCATION, {
                "name": "  Sysco ", "rep_name": "Dana", "order_email": "Orders@Example.com",
                "website": "shop.sysco.com", "delivery_days": ["thu", "mon", "bogus"],
                "cutoff_time": "15:00", "lead_days": 1, "account_number": "A-1",
            })
            self.assertEqual(saved["name"], "Sysco")
            self.assertEqual(saved["order_email"], "orders@example.com")
            self.assertEqual(saved["website"], "https://shop.sysco.com")
            self.assertEqual(saved["delivery_days"], ["mon", "thu"])
            self.assertEqual(saved["delivers"], "Mon, Thu")
            self.assertEqual(saved["cutoff_label"], "3 PM")
            self.assertIn("next_delivery", saved["schedule"])

            again = supply.save_supplier(conn, LOCATION, {"id": saved["id"], "name": "Sysco NY", "lead_days": 2})
            self.assertEqual(again["id"], saved["id"])
            self.assertEqual(again["name"], "Sysco NY")
            self.assertEqual(len(supply.list_suppliers(conn, LOCATION, _now())), 1)

            with self.assertRaises(ValueError):
                supply.save_supplier(conn, LOCATION, {"name": "x"})
            with self.assertRaises(ValueError):
                supply.save_supplier(conn, LOCATION, {"name": "Fine", "cutoff_time": "late"})
            # A wrong shape for the delivery days is ignored, not a crash.
            odd = supply.save_supplier(conn, LOCATION, {"id": saved["id"], "name": "Sysco NY", "delivery_days": 5})
            self.assertEqual(odd["delivery_days"], [])

            supply.delete_supplier(conn, LOCATION, saved["id"])
            self.assertEqual(supply.list_suppliers(conn, LOCATION, _now()), [])

    def test_supplier_email_and_website_are_checked(self) -> None:
        with connect(self.db_path) as conn:
            with self.assertRaises(ValueError) as caught:
                supply.save_supplier(conn, LOCATION, {"name": "Sysco", "order_email": "dana"})
            self.assertIn("orders@supplier.com", str(caught.exception))
            for bad in ("not a url at all", "javascript:alert(1)", "ftp://x", "https://", "localhost"):
                with self.assertRaises(ValueError, msg=bad) as caught:
                    supply.save_supplier(conn, LOCATION, {"name": "Sysco", "website": bad})
                self.assertIn("shop.supplier.com", str(caught.exception))
            good = supply.save_supplier(conn, LOCATION, {"name": "Sysco", "website": "shop.sysco.com", "order_email": "Orders@Sysco.com"})
            self.assertEqual(good["website"], "https://shop.sysco.com")
            self.assertEqual(good["order_email"], "orders@sysco.com")
            self.assertEqual(supply.save_supplier(conn, LOCATION, {"name": "Depot", "website": "http://depot.example/shop?x=1"})["website"], "http://depot.example/shop?x=1")

    def test_unknown_supplier_ids_are_refused_on_save_and_delete(self) -> None:
        with connect(self.db_path) as conn:
            with self.assertRaises(ValueError):
                supply.save_supplier(conn, LOCATION, {"id": "gone", "name": "Ghost"})
            with self.assertRaises(ValueError):
                supply.delete_supplier(conn, LOCATION, "gone")
            self.assertEqual(supply.list_suppliers(conn, LOCATION, _now()), [])

    def test_supplier_belongs_to_its_location(self) -> None:
        with connect(self.db_path) as conn:
            saved = supply.save_supplier(conn, "loc-bakery", {"name": "Bakery only"})
            self.assertIsNone(supply.get_supplier(conn, LOCATION, saved["id"]))
            with self.assertRaises(ValueError):
                supply.save_item(conn, LOCATION, {"ingredient": "flour", "supplier_id": saved["id"]})
            supply.delete_supplier(conn, "loc-bakery", saved["id"])

    # -- delivery arithmetic ---------------------------------------------------

    def test_schedule_respects_cutoff_lead_and_delivery_days(self) -> None:
        # TODAY is a Monday. Delivers Mon and Thu, order by 3 PM the day before.
        sysco = {"delivery_days": ["mon", "thu"], "cutoff_time": "15:00", "lead_days": 1}
        before = supply.schedule(sysco, _now(10))
        self.assertEqual(before["next_delivery"], "2026-08-13")  # Thursday, order by Wed 3 PM
        self.assertEqual(before["order_by"], "2026-08-12T15:00")
        self.assertEqual(before["order_by_label"], "Wednesday by 3 PM")

        # After Wednesday's cutoff, Thursday is gone and the next is Monday.
        late = supply.schedule(sysco, _now(16, day=date(2026, 8, 12)))
        self.assertEqual(late["next_delivery"], "2026-08-17")

        # No delivery days and no cutoff: any day, order today for tomorrow.
        anyday = supply.schedule({"delivery_days": [], "cutoff_time": "", "lead_days": 1}, _now(23))
        self.assertEqual(anyday["next_delivery"], "2026-08-11")
        self.assertEqual(anyday["order_by_label"], "today")

    def test_order_by_works_back_from_the_run_out_day(self) -> None:
        sysco = {"delivery_days": ["mon", "thu"], "cutoff_time": "15:00", "lead_days": 1}
        # Runs out Saturday the 15th: the last delivery that lands in time is Thursday the 13th.
        plan = supply.order_by_for_runout(sysco, date(2026, 8, 15), _now(10))
        self.assertFalse(plan["late"])
        self.assertEqual(plan["arrives"], "2026-08-13")
        self.assertEqual(plan["order_by"], "2026-08-12T15:00")
        # Runs out today at 10 AM on a Monday: Monday's truck needed Sunday's order. Too late.
        late = supply.order_by_for_runout(sysco, TODAY, _now(10))
        self.assertTrue(late["late"])
        self.assertEqual(late["arrives"], "2026-08-13")
        self.assertEqual(late["days_short"], 3)
        self.assertEqual(late["order_by_label"], "now")

    # -- pack sizes, counts, and the order list --------------------------------

    def test_counts_turn_usage_into_a_shortfall_in_packs(self) -> None:
        with connect(self.db_path) as conn:
            now = _now(9)
            plan = order_plan(conn, LOCATION, TODAY, 3)
            self.assertTrue(plan["ready"])
            patty = next(line for line in plan["lines"] if line["name"].lower() == "beef patty")
            sysco = supply.save_supplier(conn, LOCATION, {"name": "Sysco", "delivery_days": ["mon", "thu"], "cutoff_time": "15:00", "lead_days": 1, "order_email": "orders@example.com"})
            supply.save_item(conn, LOCATION, {"ingredient": "Beef Patty", "supplier_id": sysco["id"], "pack_size": 80, "pack_unit": "patties", "pack_label": "case", "product_code": "12345"})

            # Nothing counted yet: honest usage, packs for the whole window.
            attached = supply.attach(conn, LOCATION, order_plan(conn, LOCATION, TODAY, 3), now)
            line = next(row for row in attached["lines"] if row["name"].lower() == "beef patty")
            self.assertEqual(line["supplier_name"], "Sysco")
            self.assertEqual(line["pack_size"], 80)
            self.assertEqual(line["pack_unit"], "patty")
            self.assertIsNone(line["on_hand"])
            self.assertEqual(line["suggested"]["unit"], "case")
            self.assertEqual(line["suggested"]["quantity"], -(-patty["typical"] // 80))
            self.assertEqual(attached["counts_taken"], 0)

            # Two cases on the shelf, counted in packs.
            supply.save_count(conn, LOCATION, {"ingredient": "beef patty", "on_hand": 2, "unit": "pack"}, "Ofir")
            attached = supply.attach(conn, LOCATION, order_plan(conn, LOCATION, TODAY, 3), now)
            line = next(row for row in attached["lines"] if row["name"].lower() == "beef patty")
            self.assertEqual(line["on_hand"], 160)
            self.assertEqual(line["on_hand_packs"], 2)
            self.assertEqual(line["short"], max(0, line["typical"] - 160))
            self.assertEqual(line["packs_short"], -(-line["short"] // 80))
            self.assertLess(line["days_of_cover"], 3)
            self.assertTrue(line["runs_out_on"])
            self.assertIn("order_by", line["order"])
            self.assertFalse(line["no_supplier"])
            self.assertTrue(line["needs_action"])
            self.assertTrue(line["whole"])
            self.assertEqual(line["on_order"], 0)
            self.assertEqual(line["count_age_days"], 0)
            self.assertEqual(attached["counts_taken"], 1)

            # Counting more than the window needs means nothing to order.
            supply.save_count(conn, LOCATION, {"ingredient": "beef patty", "on_hand": 5000, "unit": "patty"})
            attached = supply.attach(conn, LOCATION, order_plan(conn, LOCATION, TODAY, 3), now)
            line = next(row for row in attached["lines"] if row["name"].lower() == "beef patty")
            self.assertEqual(line["short"], 0)
            self.assertEqual(line["suggested"]["quantity"], 0)
            self.assertFalse(line["needs_action"])

            # An empty count clears it, which is not the same as counting zero.
            supply.save_count(conn, LOCATION, {"ingredient": "beef patty", "on_hand": "", "unit": "patty"})
            self.assertNotIn("beef patty", supply.stock_counts(conn, LOCATION))

    def test_counts_in_pounds_add_up_with_usage_in_grams(self) -> None:
        with connect(self.db_path) as conn:
            saved = supply.save_count(conn, LOCATION, {"ingredient": "shredded cheese", "on_hand": 2, "unit": "lb", "kind": "mass"})
            self.assertAlmostEqual(saved["on_hand_base"], 907.184, places=2)
            self.assertAlmostEqual(supply.from_base(saved["on_hand_base"], "kg", "mass"), 0.907, places=3)
            with self.assertRaises(ValueError):
                supply.save_count(conn, LOCATION, {"ingredient": "shredded cheese", "on_hand": 1, "unit": "pack"})

    def test_a_count_has_to_be_a_number_and_not_below_zero(self) -> None:
        with connect(self.db_path) as conn:
            for bad in ("abc", "twelve", "1.2.3", True, "nan", "inf"):
                with self.assertRaises(ValueError, msg=repr(bad)) as caught:
                    supply.save_count(conn, LOCATION, {"ingredient": "beef patty", "on_hand": bad, "unit": "patty"})
                self.assertEqual(str(caught.exception), "Enter a number, like 12 or 2.5")
            with self.assertRaises(ValueError) as caught:
                supply.save_count(conn, LOCATION, {"ingredient": "beef patty", "on_hand": -50, "unit": "patty"})
            self.assertEqual(str(caught.exception), "A count cannot be below zero")
            # Nothing was written by any of those.
            self.assertNotIn("beef patty", supply.stock_counts(conn, LOCATION))
            saved = supply.save_count(conn, LOCATION, {"ingredient": "beef patty", "on_hand": "1,200", "unit": "patty"})
            self.assertEqual(saved["on_hand_base"], 1200.0)
            self.assertEqual(supply.save_count(conn, LOCATION, {"ingredient": "beef patty", "on_hand": "2.5", "unit": "patty"})["on_hand_base"], 2.5)

    def test_a_pack_count_uses_the_kind_of_the_pack_unit(self) -> None:
        with connect(self.db_path) as conn:
            supply.save_item(conn, LOCATION, {"ingredient": "potato", "pack_size": 20, "pack_unit": "lb", "pack_label": "sack"})
            # The client says "count" because the screen was counting sacks;
            # the sack is still twenty pounds.
            saved = supply.save_count(conn, LOCATION, {"ingredient": "potato", "on_hand": 2, "unit": "pack", "kind": "count"})
            self.assertEqual(saved["kind"], "mass")
            self.assertAlmostEqual(saved["on_hand_base"], 2 * 20 * 453.592, places=1)
            line = _line(supply.attach(conn, LOCATION, order_plan(conn, LOCATION, TODAY, 3), _now()), "potato")
            self.assertEqual(line["on_hand"], 40)
            self.assertEqual(line["on_hand_packs"], 2)

    def test_the_count_returns_the_line_the_screen_can_patch(self) -> None:
        with connect(self.db_path) as conn:
            supply.save_count(conn, LOCATION, {"ingredient": "beef patty", "on_hand": 50, "unit": "patty"})
            line, missing = supply.lines_for(conn, LOCATION, ["beef patty", "saffron"], 3, _now())
            self.assertIsNone(missing)
            self.assertEqual(line["name"], "Beef patty")
            self.assertEqual(line["on_hand"], 50)
            same = _line(supply.attach(conn, LOCATION, order_plan(conn, LOCATION, TODAY, 3), _now()), "beef patty")
            for key in ("per_day", "days_of_cover", "short", "suggested", "runs_out_on", "needs_action"):
                self.assertEqual(line[key], same[key], key)

    def test_an_old_count_has_had_its_days_of_use_taken_off(self) -> None:
        with connect(self.db_path) as conn:
            supply.save_count(conn, LOCATION, {"ingredient": "beef patty", "on_hand": 600, "unit": "patty"})
            fresh = _line(supply.attach(conn, LOCATION, order_plan(conn, LOCATION, TODAY, 3), _now()), "beef patty")
            two_days_ago = (_now() - timedelta(days=2)).astimezone(timezone.utc).isoformat(timespec="seconds")
            conn.execute("UPDATE stock_counts SET counted_at=? WHERE location_id=? AND ingredient=?", (two_days_ago, LOCATION, "beef patty"))
            conn.commit()
            supply._CACHE.clear()
            aged = _line(supply.attach(conn, LOCATION, order_plan(conn, LOCATION, TODAY, 3), _now()), "beef patty")
            self.assertEqual(aged["count_age_days"], 2.0)
            self.assertEqual(aged["on_hand"], 600, "the count as typed is kept")
            self.assertAlmostEqual(aged["days_of_cover"], max(0.0, fresh["days_of_cover"] - 2.0), delta=0.15)
            self.assertGreater(aged["short"], fresh["short"])
            strip = supply.attention(conn, LOCATION, _now())["lines"][0]
            self.assertEqual(strip["count_age_days"], 2.0)
            self.assertEqual(strip["counted_at"], two_days_ago)

    def test_a_huge_count_never_breaks_the_order_page(self) -> None:
        with connect(self.db_path) as conn:
            supply.save_count(conn, LOCATION, {"ingredient": "flavour syrup", "on_hand": 10_000_000, "unit": "lb", "kind": "mass"})
            line = _line(supply.attach(conn, LOCATION, order_plan(conn, LOCATION, TODAY, 3), _now()), "flavour syrup")
            self.assertLessEqual(line["days_of_cover"], 3650)
            self.assertEqual(line["runs_out_label"], "more than ten years")
            self.assertEqual(line["short"], 0)
            self.assertEqual(supply.attention(conn, LOCATION, _now())["lines"], [])

    def test_countable_things_are_suggested_whole(self) -> None:
        with connect(self.db_path) as conn:
            supply.save_count(conn, LOCATION, {"ingredient": "beef patty", "on_hand": 10.5, "unit": "patty"})
            plan = supply.attach(conn, LOCATION, order_plan(conn, LOCATION, TODAY, 3), _now())
            for row in plan["lines"]:
                if row["kind"] == "count":
                    self.assertIsInstance(row["typical"], int, row["name"])
                    self.assertIsInstance(row["short"], int, row["name"])
                    self.assertIsInstance(row["suggested"]["quantity"], int, row["name"])
                    self.assertTrue(row["whole"], row["name"])
                self.assertIn("needs_action", row)
                self.assertIn("no_supplier", row)
                self.assertIn("on_order", row)
                self.assertIn("arrives", row)

    def test_two_spellings_of_one_ingredient_are_one_line(self) -> None:
        self.assertEqual(merge_key("Burger bun", "buns"), merge_key("Bun", "bun"))
        self.assertEqual(merge_key("Beef patties", "patty"), merge_key("beef patty", "patties"))
        self.assertNotEqual(merge_key("Wrap and box", "set"), merge_key("Wrap", "wrap"))
        self.assertNotEqual(merge_key("Bread", "slice"), merge_key("Bread or roll", "roll"))
        with connect(self.db_path) as conn:
            plan = order_plan(conn, LOCATION, TODAY, 3)
            buns = [row for row in plan["lines"] if "bun" in row["name"].lower()]
            self.assertEqual(len(buns), 1, [row["name"] for row in buns])
            self.assertEqual(buns[0]["name"], "Burger bun")
            self.assertIn("bun", buns[0]["aliases"])
            self.assertIn("burger bun", buns[0]["aliases"])
            # A count saved under the other spelling still lands on the line.
            supply.save_count(conn, LOCATION, {"ingredient": "bun", "on_hand": 40, "unit": "bun"})
            line = _line(supply.attach(conn, LOCATION, order_plan(conn, LOCATION, TODAY, 3), _now()), "burger bun")
            self.assertEqual(line["on_hand"], 40)

    # -- attention ---------------------------------------------------------------

    def test_attention_is_empty_until_something_is_counted(self) -> None:
        with connect(self.db_path) as conn:
            self.assertEqual(supply.attention(conn, LOCATION, _now()), {"lines": [], "counted": 0, "as_of": ""})
            supply.save_count(conn, LOCATION, {"ingredient": "beef patty", "on_hand": 50, "unit": "patty"})
            result = supply.attention(conn, LOCATION, _now())
            self.assertEqual(len(result["lines"]), 1)
            row = result["lines"][0]
            self.assertEqual(row["name"], "Beef patty")
            self.assertEqual(row["on_hand"], 50)
            self.assertLess(row["days_of_cover"], 1)
            self.assertEqual(row["runs_out_on"], TODAY.isoformat())
            self.assertEqual(result["counted"], 1)
            self.assertTrue(result["as_of"])
            # A count that lasts past the week is not a warning.
            supply.save_count(conn, LOCATION, {"ingredient": "beef patty", "on_hand": 100000, "unit": "patty"})
            self.assertEqual(supply.attention(conn, LOCATION, _now())["lines"], [])

    def test_attention_has_exactly_the_shape_the_running_low_card_reads(self) -> None:
        with connect(self.db_path) as conn:
            sysco = self._sysco(conn)
            supply.save_item(conn, LOCATION, {"ingredient": "beef patty", "supplier_id": sysco["id"], "pack_size": 80, "pack_unit": "patty", "pack_label": "case"})
            supply.save_count(conn, LOCATION, {"ingredient": "beef patty", "on_hand": 500, "unit": "patty"})
            supply.save_count(conn, LOCATION, {"ingredient": "burger bun", "on_hand": 10, "unit": "bun"})
            result = supply.attention(conn, LOCATION, _now())
            self.assertEqual(set(result), {"lines", "counted", "as_of"})
            self.assertEqual(result["counted"], 2)
            self.assertEqual(len(result["lines"]), 2)
            for row in result["lines"]:
                self.assertEqual(set(row), set(supply.ATTENTION_KEYS), row["name"])
                self.assertIsInstance(row["late"], bool)
                self.assertIsInstance(row["no_supplier"], bool)
                self.assertTrue(row["order_by_date"] == "" or DATE.match(row["order_by_date"]), row["order_by_date"])
                self.assertTrue(row["arrives"] == "" or DATE.match(row["arrives"]), row["arrives"])
                self.assertTrue(DATE.match(row["runs_out_on"]))
                self.assertNotIn("T", row["order_by_date"])
                self.assertEqual(set(row["suggested"]), {"quantity", "unit"})
            patty = next(row for row in result["lines"] if row["name"] == "Beef patty")
            self.assertEqual(patty["supplier"], "Sysco")
            self.assertEqual(patty["supplier_id"], sysco["id"])
            self.assertFalse(patty["no_supplier"])
            self.assertTrue(patty["order_by_label"])
            self.assertTrue(patty["arrives_label"])
            self.assertEqual(patty["suggested"]["unit"], "case")
            keys = [(row["runs_out_on"], row["days_of_cover"]) for row in result["lines"]]
            self.assertEqual(keys, sorted(keys))

    def test_attention_agrees_with_the_order_page(self) -> None:
        with connect(self.db_path) as conn:
            supply.save_count(conn, LOCATION, {"ingredient": "beef patty", "on_hand": 300, "unit": "patty"})
            strip = supply.attention(conn, LOCATION, _now())["lines"][0]
            for days in (2, 3, 5, 7):
                page = _line(supply.attach(conn, LOCATION, order_plan(conn, LOCATION, TODAY, days), _now()), "beef patty")
                self.assertEqual(strip["per_day"], page["per_day"], days)
                self.assertEqual(strip["days_of_cover"], page["days_of_cover"], days)
                self.assertEqual(strip["runs_out_on"], page["runs_out_on"], days)

    def test_a_line_with_no_supplier_gets_no_invented_delivery(self) -> None:
        with connect(self.db_path) as conn:
            supply.save_count(conn, LOCATION, {"ingredient": "beef patty", "on_hand": 0, "unit": "patty"})
            row = supply.attention(conn, LOCATION, _now())["lines"][0]
            self.assertTrue(row["no_supplier"])
            self.assertEqual(row["supplier"], "")
            self.assertEqual((row["order_by_date"], row["order_by_label"], row["arrives"], row["arrives_label"]), ("", "", "", ""))
            self.assertFalse(row["late"])
            line = _line(supply.attach(conn, LOCATION, order_plan(conn, LOCATION, TODAY, 3), _now()), "beef patty")
            self.assertTrue(line["order"]["no_supplier"])
            self.assertEqual(line["order"]["arrives"], "")

    def test_the_truck_is_aimed_at_the_day_before_the_shelf_is_empty(self) -> None:
        with connect(self.db_path) as conn:
            # Delivers any day, no cutoff, one day ahead.
            anyday = supply.save_supplier(conn, LOCATION, {"name": "Cash and carry", "lead_days": 1})
            supply.save_item(conn, LOCATION, {"ingredient": "beef patty", "supplier_id": anyday["id"]})
            page = _line(supply.attach(conn, LOCATION, order_plan(conn, LOCATION, TODAY, 7), _now()), "beef patty")
            per_day = page["per_day"]
            supply.save_count(conn, LOCATION, {"ingredient": "beef patty", "on_hand": per_day * 3 + 1, "unit": "patty"})
            line = _line(supply.attach(conn, LOCATION, order_plan(conn, LOCATION, TODAY, 7), _now()), "beef patty")
            runs_out = date.fromisoformat(line["runs_out_on"])
            self.assertEqual(runs_out, TODAY + timedelta(days=3))
            self.assertEqual(line["order"]["arrives"], (runs_out - timedelta(days=1)).isoformat())
            self.assertFalse(line["order"]["late"])

    def test_attention_sorts_soonest_first(self) -> None:
        with connect(self.db_path) as conn:
            supply.save_count(conn, LOCATION, {"ingredient": "beef patty", "on_hand": 700, "unit": "patty"})
            supply.save_count(conn, LOCATION, {"ingredient": "burger bun", "on_hand": 10, "unit": "bun"})
            lines = supply.attention(conn, LOCATION, _now())["lines"]
            self.assertEqual([row["name"] for row in lines][:2], ["Burger bun", "Beef patty"])
            self.assertLessEqual(lines[0]["runs_out_on"], lines[1]["runs_out_on"])

    def test_attention_cache_is_per_location_and_bounded(self) -> None:
        with connect(self.db_path) as conn:
            supply.save_count(conn, LOCATION, {"ingredient": "beef patty", "on_hand": 10, "unit": "patty"})
            self.assertEqual(len(supply.attention(conn, LOCATION, _now())["lines"]), 1)
            self.assertEqual(supply.attention(conn, "loc-bakery", _now())["lines"], [])
            for hour in range(8, 20):
                supply.attention(conn, LOCATION, _now(hour))
            self.assertLessEqual(len(supply._CACHE), 1, "one fresh answer per location")

    # -- sending -----------------------------------------------------------------

    def test_order_goes_to_outbox_and_is_remembered(self) -> None:
        with connect(self.db_path) as conn:
            sysco = supply.save_supplier(conn, LOCATION, {"name": "Sysco", "rep_name": "Dana", "order_email": "orders@example.com", "delivery_days": ["thu"], "lead_days": 1, "account_number": "A-1"})
            lines = [{"name": "Beef patty", "quantity": 6, "unit": "case", "pack_size": 80, "pack_unit": "patty", "product_code": "12345"},
                     {"name": "Burger bun", "quantity": 0, "unit": "bag"}]
            result = supply.record_order(conn, self.root, LOCATION, {
                "supplier_id": sysco["id"], "channel": "email", "lines": lines,
                "window_start": "2026-08-10", "window_end": "2026-08-12",
            }, "Ofir")
            self.assertEqual(result["order"]["status"], "outbox")
            self.assertTrue(result["recorded"])
            self.assertEqual(result["order"]["line_count"], 1)
            self.assertIn("Hi Dana", result["text"])
            self.assertIn("account A-1", result["text"])
            self.assertIn("Beef patty: 6 cases (80 patties each), code 12345", result["text"])
            self.assertIn("Ofir", result["text"])
            self.assertEqual(result["message"], "Email is not connected on this account yet, so the order was saved, not sent.")
            self.assertTrue(list((self.root / "data" / "outbox").glob("*.eml")))

            recent = supply.recent_orders(conn, LOCATION)
            self.assertEqual(len(recent), 1)
            self.assertEqual(recent[0]["supplier_name"], "Sysco")
            self.assertEqual(recent[0]["channel"], "email")
            self.assertEqual(recent[0]["lines"][0]["name"], "Beef patty")
            self.assertEqual(supply.last_orders(conn, LOCATION)[sysco["id"]]["id"], recent[0]["id"])
            self.assertEqual(supply.list_suppliers(conn, LOCATION, _now())[0]["last_order"]["id"], recent[0]["id"])
            # The log follows a rename; it does not keep the old name.
            supply.save_supplier(conn, LOCATION, {"id": sysco["id"], "name": "Sysco Yonkers"})
            self.assertEqual(supply.recent_orders(conn, LOCATION)[0]["supplier_name"], "Sysco Yonkers")

    def test_a_failed_send_says_so_and_a_delivered_one_says_sent(self) -> None:
        with connect(self.db_path) as conn:
            sysco = self._sysco(conn)
            payload = {"supplier_id": sysco["id"], "channel": "email", "lines": [{"name": "Beef patty", "quantity": 6, "unit": "case"}]}
            broken = {"provider": "outbox", "delivered": False, "path": "", "error": "Postmark said no"}
            with patch("quantify_app.email_brief.send_transactional", return_value=broken), patch("quantify_app.supply._mail_provider", return_value="postmark"):
                result = supply.record_order(conn, self.root, LOCATION, payload, "Ofir")
            self.assertEqual(result["order"]["status"], "failed")
            self.assertEqual(result["message"], "The email did not go through. The order is saved; try again or copy it.")
            self.assertNotIn("mail service", result["message"])
            with patch("quantify_app.email_brief.send_transactional", return_value={"provider": "postmark", "delivered": True, "id": "x"}):
                sent = supply.record_order(conn, self.root, LOCATION, payload, "Ofir")
            self.assertEqual(sent["order"]["status"], "sent")
            self.assertTrue(sent["message"].startswith("Sent to Sysco."))
            self.assertIn("Lands", sent["message"])
            with patch("quantify_app.email_brief.send_transactional", return_value={"provider": "blocked", "delivered": False, "error": "This address is on the never-send list"}):
                with self.assertRaises(ValueError):
                    supply.record_order(conn, self.root, LOCATION, payload, "Ofir")
            self.assertEqual([row["status"] for row in supply.recent_orders(conn, LOCATION)], ["sent", "failed"])

    def test_mail_app_records_nothing_until_the_operator_confirms(self) -> None:
        with connect(self.db_path) as conn:
            sysco = supply.save_supplier(conn, LOCATION, {"name": "Sysco", "order_email": "orders@example.com"})
            payload = {"supplier_id": sysco["id"], "channel": "mail-app", "lines": [{"name": "Beef patty", "quantity": 6, "unit": "case"}]}
            result = supply.record_order(conn, self.root, LOCATION, payload, "Ofir")
            self.assertFalse(result["recorded"])
            self.assertEqual(result["order"]["status"], "drafted")
            self.assertEqual(result["order"]["id"], "")
            self.assertTrue(result["mailto"].startswith("mailto:orders%40example.com?subject="))
            self.assertIn("Beef%20patty%3A%206%20cases", result["mailto"])
            self.assertEqual(supply.recent_orders(conn, LOCATION), [])

            confirmed = supply.record_order(conn, self.root, LOCATION, {**payload, "confirmed": True}, "Ofir")
            self.assertTrue(confirmed["recorded"])
            self.assertEqual(confirmed["order"]["status"], "sent")
            self.assertTrue(confirmed["message"].startswith("Noted."))
            recent = supply.recent_orders(conn, LOCATION)
            self.assertEqual([(row["channel"], row["status"]) for row in recent], [("mail-app", "sent")])

    def test_mail_app_needs_an_address_too(self) -> None:
        with connect(self.db_path) as conn:
            sysco = supply.save_supplier(conn, LOCATION, {"name": "Sysco"})
            with self.assertRaises(ValueError):
                supply.record_order(conn, self.root, LOCATION, {"supplier_id": sysco["id"], "channel": "mail-app", "lines": [{"name": "Beef patty", "quantity": 6, "unit": "case"}]})
            self.assertEqual(supply.recent_orders(conn, LOCATION), [])

    def test_copy_is_not_a_channel(self) -> None:
        with connect(self.db_path) as conn:
            sysco = self._sysco(conn)
            self.assertNotIn("copy", supply.CHANNELS)
            with self.assertRaises(ValueError) as caught:
                supply.record_order(conn, self.root, LOCATION, {"supplier_id": sysco["id"], "channel": "copy", "lines": [{"name": "Beef patty", "quantity": 6, "unit": "case"}]})
            self.assertIn("Copying does not send an order", str(caught.exception))
            with self.assertRaises(ValueError):
                supply.record_order(conn, self.root, LOCATION, {"supplier_id": sysco["id"], "channel": "fax", "lines": [{"name": "Beef patty", "quantity": 6, "unit": "case"}]})
            self.assertEqual(supply.recent_orders(conn, LOCATION), [])

    def test_an_order_cannot_name_a_supplier_that_is_not_here(self) -> None:
        with connect(self.db_path) as conn:
            elsewhere = supply.save_supplier(conn, "loc-bakery", {"name": "Bakery only", "order_email": "b@example.com"})
            try:
                for supplier_id in ("nope", elsewhere["id"]):
                    with self.assertRaises(ValueError) as caught:
                        supply.record_order(conn, self.root, LOCATION, {"supplier_id": supplier_id, "channel": "site", "lines": [{"name": "Flour", "quantity": 1, "unit": "sack"}]})
                    self.assertEqual(str(caught.exception), "That supplier is not on this location. Refresh and try again.")
                self.assertEqual(supply.recent_orders(conn, LOCATION), [])
                with self.assertRaises(ValueError):
                    supply.record_order(conn, self.root, LOCATION, {"supplier_id": "", "channel": "site", "lines": 5})
            finally:
                supply.delete_supplier(conn, "loc-bakery", elsewhere["id"])

    def test_a_placed_order_reduces_the_shortfall_until_it_lands(self) -> None:
        with connect(self.db_path) as conn:
            now = _now(9)
            sysco = self._sysco(conn)  # Mon and Thu, TODAY is a Monday, 9 AM is past Sunday's cutoff
            supply.save_item(conn, LOCATION, {"ingredient": "beef patty", "supplier_id": sysco["id"], "pack_size": 80, "pack_unit": "patty", "pack_label": "case"})
            supply.save_count(conn, LOCATION, {"ingredient": "beef patty", "on_hand": 100, "unit": "patty"})
            before = _line(supply.attach(conn, LOCATION, order_plan(conn, LOCATION, TODAY, 3), now), "beef patty")
            self.assertTrue(before["order"]["late"])
            self.assertEqual(before["on_order"], 0)

            placed = supply.record_order(conn, self.root, LOCATION, {
                "supplier_id": sysco["id"], "channel": "site", "requested_delivery": TODAY.isoformat(),
                "lines": [{"name": "Beef patty", "quantity": before["suggested"]["quantity"], "unit": "case", "pack_size": 80, "pack_unit": "patty"}],
            }, "Ofir")
            self.assertEqual(placed["order"]["status"], "opened")
            after = _line(supply.attach(conn, LOCATION, order_plan(conn, LOCATION, TODAY, 3), now), "beef patty")
            self.assertEqual(after["on_order"], before["suggested"]["quantity"] * 80)
            self.assertEqual(after["on_order_packs"], before["suggested"]["quantity"])
            self.assertEqual(after["arrives"], TODAY.isoformat())
            self.assertEqual(after["arrives_label"], "today")
            self.assertEqual(after["short"], 0)
            self.assertEqual(after["suggested"]["quantity"], 0)
            self.assertGreater(after["days_of_cover"], before["days_of_cover"])
            self.assertFalse(after["needs_action"])
            strip = supply.attention(conn, LOCATION, now)["lines"]
            patty = [row for row in strip if row["name"] == "Beef patty"]
            self.assertTrue(not patty or patty[0]["on_order"] == after["on_order"])

            # A count taken after the truck came already includes it.
            conn.execute("UPDATE purchase_orders SET sent_at=? WHERE id=?", ("2026-08-10T08:00:00-04:00", placed["order"]["id"]))
            conn.commit()
            supply.save_count(conn, LOCATION, {"ingredient": "beef patty", "on_hand": 700, "unit": "patty"})
            recounted = _line(supply.attach(conn, LOCATION, order_plan(conn, LOCATION, TODAY, 3), now), "beef patty")
            self.assertEqual(recounted["on_order"], 0)

    def test_last_orders_is_the_newest_per_supplier(self) -> None:
        with connect(self.db_path) as conn:
            sysco = self._sysco(conn)
            depot = supply.save_supplier(conn, LOCATION, {"name": "Depot", "order_email": "d@example.com"})
            ids = []
            for supplier in (sysco, depot, sysco):
                ids.append(supply.record_order(conn, self.root, LOCATION, {"supplier_id": supplier["id"], "channel": "site", "lines": [{"name": "Beef patty", "quantity": 1, "unit": "case"}]})["order"]["id"])
                conn.execute("UPDATE purchase_orders SET sent_at=? WHERE id=?", (f"2026-08-1{len(ids)}T10:00:00-04:00", ids[-1]))
                conn.commit()
            last = supply.last_orders(conn, LOCATION)
            self.assertEqual(set(last), {sysco["id"], depot["id"]})
            self.assertEqual(last[sysco["id"]]["id"], ids[2])
            self.assertEqual(last[depot["id"]]["id"], ids[1])

    def test_supplier_view_shape(self) -> None:
        with connect(self.db_path) as conn:
            view = supply.supplier_view(conn, LOCATION)
            for key in ("suppliers", "items", "recent_orders", "mail_provider", "pack_labels", "weekdays", "today"):
                self.assertIn(key, view)
            self.assertEqual(len(view["weekdays"]), 7)

    # -- copy ------------------------------------------------------------------------

    def test_operator_facing_text_has_no_marketing_words_or_em_dashes(self) -> None:
        for name in ("supply.py", "ordering.py"):
            source = (ROOT / "quantify_app" / name).read_text(encoding="utf-8")
            self.assertIsNone(PYTHON_BANNED.search(source), name)
        app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        start = app.index("/* ---------- ordering ---------- */")
        end = app.index("/* ---------- settings ---------- */")
        self.assertIsNone(BANNED.search(app[start:end]), "ordering block of app.js")
        self.assertNotIn("prompt(", app[start:end])
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        self.assertIn("/* ---------- supply ---------- */", css)


if __name__ == "__main__":
    unittest.main()
