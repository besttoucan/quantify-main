"""What a location is allowed to cost, and what happens when it reaches it.

The point of every test here is the same: the ceiling stops the bill, it never
stops the brief.
"""

from __future__ import annotations

import sqlite3
import unittest
from datetime import date, datetime, timedelta, timezone

from quantify_app import ai, budget

LOCATION = "loc-burger"


class Usage:
    """The shape the Anthropic SDK returns on `response.usage`."""

    def __init__(self, input_tokens=0, cache_read=0, cache_write=0, output_tokens=0):
        self.input_tokens = input_tokens
        self.cache_read_input_tokens = cache_read
        self.cache_creation_input_tokens = cache_write
        self.output_tokens = output_tokens


def memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    budget.ensure_schema(conn)
    conn.execute(
        """CREATE TABLE ai_generations (
             task TEXT NOT NULL, subject TEXT NOT NULL, fingerprint TEXT NOT NULL,
             writer TEXT NOT NULL, model TEXT NOT NULL, result_json TEXT NOT NULL,
             created_at TEXT NOT NULL, PRIMARY KEY(task, subject))"""
    )
    return conn


class Pricing(unittest.TestCase):
    def test_a_call_is_priced_with_cache_reads_at_a_tenth(self) -> None:
        # Opus 5 is $5 in and $25 out per million. A cache read is a tenth of an
        # input token, a cache write is a quarter more than one.
        cost = budget.cost_of("claude-opus-5", Usage(
            input_tokens=6_000, cache_read=2_450, cache_write=0, output_tokens=2_000,
        ))
        expected = (6_000 * 5 + 2_450 * 5 * 0.10 + 2_000 * 25) / 1_000_000
        self.assertAlmostEqual(cost, expected, places=6)

        # This is the real daily brief, and it needs to stay near eight cents.
        # If this fails, the constitution's cost table is out of date.
        self.assertLess(cost, 0.12)
        self.assertGreater(cost, 0.04)

    def test_an_unknown_model_is_costed_at_the_dearest_rate(self) -> None:
        """A model we have not priced must never look cheap."""
        unknown = budget.cost_of("claude-something-new", Usage(input_tokens=1_000_000))
        opus = budget.cost_of("claude-opus-5", Usage(input_tokens=1_000_000))
        self.assertGreater(unknown, opus)

    def test_missing_usage_never_raises(self) -> None:
        # An undercount is a reporting problem. An exception here would lose a
        # brief an operator is waiting for.
        self.assertEqual(budget.cost_of("claude-opus-5", None), 0.0)
        self.assertEqual(budget.cost_of("claude-opus-5", Usage()), 0.0)
        self.assertEqual(budget.tokens_from({"input_tokens": "not a number"})["input_tokens"], 0)

    def test_the_price_table_is_reviewed_within_a_year(self) -> None:
        reviewed = datetime.strptime(budget.PRICES_REVIEWED, "%Y-%m-%d").date()
        self.assertLess(
            date.today() - reviewed, timedelta(days=365),
            "the model price table has not been checked in a year, see budget.PRICES_SOURCE",
        )


class Ledger(unittest.TestCase):
    def test_every_call_is_attributable_to_a_location(self) -> None:
        conn = memory_db()
        budget.record(conn, LOCATION, "day_narrative", f"{LOCATION}:2026-08-10",
                      "claude-opus-5", Usage(input_tokens=6_000, output_tokens=2_000))
        budget.record(conn, "loc-other", "day_narrative", "loc-other:2026-08-10",
                      "claude-opus-5", Usage(input_tokens=6_000, output_tokens=2_000))

        # One location's spending never counts against another's.
        self.assertGreater(budget.month_to_date(conn, LOCATION), 0)
        self.assertAlmostEqual(
            budget.month_to_date(conn, LOCATION),
            budget.month_to_date(conn, "loc-other"),
            places=6,
        )

    def test_forced_refreshes_are_counted_separately_from_ordinary_ones(self) -> None:
        conn = memory_db()
        for _ in range(3):
            budget.record(conn, LOCATION, "day_narrative", "s", "claude-opus-5",
                          Usage(output_tokens=100), forced=True)
        budget.record(conn, LOCATION, "day_narrative", "s", "claude-opus-5", Usage(output_tokens=100))
        self.assertEqual(budget.forced_today(conn, LOCATION), 3)


class Ceilings(unittest.TestCase):
    def test_an_ordinary_month_is_never_blocked(self) -> None:
        conn = memory_db()
        # Thirty briefs and fifteen reviews, which is a normal month.
        for _ in range(30):
            budget.record(conn, LOCATION, "day_narrative", "s", "claude-opus-5",
                          Usage(input_tokens=6_000, cache_read=2_450, output_tokens=2_000))
        spent = budget.month_to_date(conn, LOCATION)
        self.assertLess(spent, budget.MONTHLY_CEILING_USD,
                        "a normal month must not approach the ceiling")
        self.assertTrue(budget.decide(conn, LOCATION)[0])

    def test_the_month_stops_at_the_ceiling(self) -> None:
        conn = memory_db()
        # A runaway: a thousand forced regenerations of a full day record.
        for _ in range(1_000):
            budget.record(conn, LOCATION, "day_narrative", "s", "claude-opus-5",
                          Usage(input_tokens=6_000, output_tokens=2_000), forced=True)

        allowed, reason = budget.decide(conn, LOCATION)
        self.assertFalse(allowed)
        self.assertIn("ceiling", reason)
        # And a different location is unaffected by the one that went wrong.
        self.assertTrue(budget.decide(conn, "loc-other")[0])

    def test_a_refresh_loop_is_stopped_the_same_day(self) -> None:
        conn = memory_db()
        for _ in range(budget.FORCED_PER_DAY):
            budget.record(conn, LOCATION, "day_narrative", "s", "claude-opus-5",
                          Usage(output_tokens=10), forced=True)

        # The loop is refused.
        self.assertFalse(budget.decide(conn, LOCATION, forced=True)[0])
        # The ordinary daily brief still runs, because it reads the cache anyway
        # and it is the thing the customer actually needs.
        self.assertTrue(budget.decide(conn, LOCATION, forced=False)[0])

    def test_a_pathological_record_is_refused_before_it_is_sent(self) -> None:
        conn = memory_db()
        enormous = {"items": [{"name": "x" * 200} for _ in range(4_000)]}
        allowed, reason = budget.decide(conn, LOCATION, payload=enormous)
        self.assertFalse(allowed)
        self.assertIn("over the", reason)
        # An ordinary day record passes without comment.
        self.assertTrue(budget.decide(conn, LOCATION, payload={"items": [1, 2, 3]})[0])


class TheBriefAlwaysShips(unittest.TestCase):
    """The reason the ceiling can be strict at all."""

    def test_a_location_over_its_ceiling_still_gets_a_complete_brief(self) -> None:
        conn = memory_db()
        for _ in range(1_000):
            budget.record(conn, LOCATION, "day_narrative", "s", "claude-opus-5",
                          Usage(input_tokens=6_000, output_tokens=2_000))
        self.assertFalse(budget.decide(conn, LOCATION)[0])

        written = {"headline": "A busy Tuesday", "summary": "About 40 more covers than a normal Tuesday."}
        result = ai.generate(
            conn, task="day_narrative", subject=f"{LOCATION}:2026-08-11",
            payload={"date": "2026-08-11"}, schema={},
            local_writer=lambda payload: dict(written),
            force=True,
        )

        # Complete output, the deterministic writer, and a recorded reason.
        self.assertEqual(result["headline"], written["headline"])
        self.assertEqual(result["_writer"], "local")
        self.assertIn("ceiling", result["_fallback_reason"])

    def test_the_location_is_read_from_the_subject_when_it_is_not_passed(self) -> None:
        # server.py keys the day narrative "<location>:<date>" and does not pass
        # a location. Spend has to land on the right customer regardless.
        conn = memory_db()
        for _ in range(1_000):
            budget.record(conn, LOCATION, "day_narrative", "s", "claude-opus-5",
                          Usage(input_tokens=6_000, output_tokens=2_000))

        result = ai.generate(
            conn, task="day_narrative", subject=f"{LOCATION}:2026-08-12",
            payload={"date": "2026-08-12"}, schema={},
            local_writer=lambda payload: {"headline": "ok"},
            force=True,
        )
        self.assertIn("ceiling", result.get("_fallback_reason", ""))


class Reporting(unittest.TestCase):
    def test_support_can_answer_what_a_customer_costs(self) -> None:
        conn = memory_db()
        for _ in range(10):
            budget.record(conn, LOCATION, "day_narrative", "s", "claude-opus-5",
                          Usage(input_tokens=6_000, cache_read=2_450, output_tokens=2_000))
        report = budget.summary(conn, LOCATION)

        self.assertGreater(report["spent_this_month"], 0)
        self.assertEqual(report["ceiling"], budget.MONTHLY_CEILING_USD)
        self.assertLessEqual(report["share_of_ceiling"], 1.0)
        self.assertFalse(report["needs_a_look"])


if __name__ == "__main__":
    unittest.main()
