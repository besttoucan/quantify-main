"""Suppliers, pack sizes, shelf counts, run-out dates, and sent orders."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from quantify_app import supply
from quantify_app.database import connect, initialize
from quantify_app.localtime import zone
from quantify_app.ordering import order_plan
from quantify_app.seed import seed_demo

TODAY = date(2026, 8, 10)
LOCATION = "loc-burger"
ROOT = Path(__file__).resolve().parents[1]

# Words that must never reach an operator, and the em dash. Same rule the rest
# of the product is held to.
BANNED = re.compile(r"—|seamless|effortless|unlock|empower|streamline|supercharge|built for you|game-changing", re.I)


def _now(hour: int = 10, minute: int = 0, day: date = TODAY) -> datetime:
    return datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute, tzinfo=zone("America/New_York"))


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

            supply.delete_supplier(conn, LOCATION, saved["id"])
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
            self.assertEqual(attached["counts_taken"], 1)

            # Counting more than the window needs means nothing to order.
            supply.save_count(conn, LOCATION, {"ingredient": "beef patty", "on_hand": 5000, "unit": "patty"})
            attached = supply.attach(conn, LOCATION, order_plan(conn, LOCATION, TODAY, 3), now)
            line = next(row for row in attached["lines"] if row["name"].lower() == "beef patty")
            self.assertEqual(line["short"], 0)
            self.assertEqual(line["suggested"]["quantity"], 0)

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

    # -- attention ---------------------------------------------------------------

    def test_attention_is_empty_until_something_is_counted(self) -> None:
        with connect(self.db_path) as conn:
            self.assertEqual(supply.attention(conn, LOCATION, _now())["lines"], [])
            supply.save_count(conn, LOCATION, {"ingredient": "beef patty", "on_hand": 50, "unit": "patty"})
            result = supply.attention(conn, LOCATION, _now())
            self.assertEqual(len(result["lines"]), 1)
            row = result["lines"][0]
            for key in ("name", "unit", "on_hand", "per_day", "days_of_cover", "runs_out_on", "order_by", "supplier"):
                self.assertIn(key, row)
            self.assertEqual(row["name"], "Beef patty")
            self.assertEqual(row["on_hand"], 50)
            self.assertLess(row["days_of_cover"], 1)
            self.assertEqual(row["runs_out_on"], TODAY.isoformat())
            # A count that lasts past the week is not a warning.
            supply.save_count(conn, LOCATION, {"ingredient": "beef patty", "on_hand": 100000, "unit": "patty"})
            self.assertEqual(supply.attention(conn, LOCATION, _now())["lines"], [])

    def test_attention_sorts_soonest_first(self) -> None:
        with connect(self.db_path) as conn:
            supply.save_count(conn, LOCATION, {"ingredient": "beef patty", "on_hand": 700, "unit": "patty"})
            supply.save_count(conn, LOCATION, {"ingredient": "burger bun", "on_hand": 10, "unit": "bun"})
            lines = supply.attention(conn, LOCATION, _now())["lines"]
            self.assertEqual([row["name"] for row in lines][:2], ["Burger bun", "Beef patty"])
            self.assertLessEqual(lines[0]["runs_out_on"], lines[1]["runs_out_on"])

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
            self.assertEqual(result["order"]["line_count"], 1)
            self.assertIn("Hi Dana", result["text"])
            self.assertIn("account A-1", result["text"])
            self.assertIn("Beef patty: 6 case (80 patty each), code 12345", result["text"])
            self.assertIn("Ofir", result["text"])
            self.assertTrue(list((self.root / "data" / "outbox").glob("*.eml")))

            recent = supply.recent_orders(conn, LOCATION)
            self.assertEqual(len(recent), 1)
            self.assertEqual(recent[0]["supplier_name"], "Sysco")
            self.assertEqual(recent[0]["channel"], "email")
            self.assertEqual(recent[0]["lines"][0]["name"], "Beef patty")
            self.assertEqual(supply.last_orders(conn, LOCATION)[sysco["id"]]["id"], recent[0]["id"])
            self.assertEqual(supply.list_suppliers(conn, LOCATION, _now())[0]["last_order"]["id"], recent[0]["id"])

    def test_mail_app_order_returns_a_mailto_link(self) -> None:
        with connect(self.db_path) as conn:
            sysco = supply.save_supplier(conn, LOCATION, {"name": "Sysco", "order_email": "orders@example.com"})
            result = supply.record_order(conn, self.root, LOCATION, {
                "supplier_id": sysco["id"], "channel": "mail-app",
                "lines": [{"name": "Beef patty", "quantity": 6, "unit": "case"}],
            }, "Ofir")
            self.assertEqual(result["order"]["status"], "drafted")
            self.assertTrue(result["mailto"].startswith("mailto:orders%40example.com?subject="))
            self.assertIn("Beef%20patty%3A%206%20case", result["mailto"])

    def test_email_without_an_address_is_refused_and_nothing_is_logged(self) -> None:
        with connect(self.db_path) as conn:
            sysco = supply.save_supplier(conn, LOCATION, {"name": "Sysco"})
            with self.assertRaises(ValueError):
                supply.record_order(conn, self.root, LOCATION, {
                    "supplier_id": sysco["id"], "channel": "email",
                    "lines": [{"name": "Beef patty", "quantity": 6, "unit": "case"}],
                })
            with self.assertRaises(ValueError):
                supply.record_order(conn, self.root, LOCATION, {"supplier_id": sysco["id"], "channel": "copy", "lines": []})
            self.assertEqual(supply.recent_orders(conn, LOCATION), [])

    def test_supplier_view_shape(self) -> None:
        with connect(self.db_path) as conn:
            view = supply.supplier_view(conn, LOCATION)
            for key in ("suppliers", "items", "recent_orders", "mail_provider", "pack_labels", "weekdays", "today"):
                self.assertIn(key, view)
            self.assertEqual(len(view["weekdays"]), 7)

    # -- copy ------------------------------------------------------------------------

    def test_operator_facing_text_has_no_marketing_words_or_em_dashes(self) -> None:
        source = (ROOT / "quantify_app" / "supply.py").read_text(encoding="utf-8")
        self.assertIsNone(BANNED.search(source), "supply.py")
        app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        start = app.index("/* ---------- ordering ---------- */")
        end = app.index("/* ---------- settings ---------- */")
        self.assertIsNone(BANNED.search(app[start:end]), "ordering block of app.js")
        self.assertNotIn("prompt(", app[start:end])
        css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        self.assertIn("/* ---------- supply ---------- */", css)


if __name__ == "__main__":
    unittest.main()
