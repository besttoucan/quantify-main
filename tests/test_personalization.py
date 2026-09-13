"""Unknown payroll stays unknown; location data cannot borrow another city's point."""
from datetime import date
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from quantify_app import connectors, costs, geography, intelligence, wages
from quantify_app.database import connect, initialize


class WageReferences(unittest.TestCase):
    def test_local_reference_is_scoped_to_state_and_effective_date(self):
        self.assertEqual(wages.minimum_wage("NY", "White Plains", date(2026, 9, 13))[0], 17)
        self.assertEqual(wages.minimum_wage("TX", "White Plains", date(2026, 9, 13))[0], 7.25)
        self.assertEqual(wages.minimum_wage("CO", "Denver", date(2026, 12, 31))[0], 19.29)
        self.assertEqual(wages.minimum_wage("CO", "Denver", date(2027, 1, 1))[0], 19.84)
        self.assertIsNone(wages.minimum_wage("NY", "White Plains", date(2027, 1, 1))[0])

    def test_midyear_changes_do_not_apply_early(self):
        for state, before, after in [("AK", 13, 14), ("DC", 17.95, 18.4), ("OR", 15.05, 15.55)]:
            self.assertEqual(wages.state_minimum_wage(state, date(2026, 6, 30)), before)
            self.assertEqual(wages.state_minimum_wage(state, date(2026, 7, 1)), after)
        self.assertEqual(wages.state_minimum_wage("FL", date(2026, 9, 29)), 14)
        self.assertEqual(wages.state_minimum_wage("FL", date(2026, 9, 30)), 15)

    def test_unknown_place_is_not_assigned_a_federal_wage(self):
        self.assertEqual(wages.resolve_state("Ontario", "Denver"), "")
        ref = wages.wage_reference("ON", "Toronto", date(2026, 9, 13))
        self.assertIsNone(ref["rate"])
        self.assertEqual(ref["status"], "unverified")
        self.assertIsNone(ref["effective_from"])
        self.assertEqual(wages.payroll_reference("ON")["components"], [])

    def test_employer_references_keep_caps_and_employee_tax_separate(self):
        ref = wages.payroll_reference("NY", date(2026, 9, 13))
        social, medicare, futa = ref["components"]
        self.assertEqual((social["percent"], social["wage_base"]), (6.2, 184500))
        self.assertEqual(medicare["percent"], 1.45)
        self.assertIsNone(medicare["wage_base"])
        self.assertIn("employee-only", medicare["detail"])
        self.assertEqual(futa["wage_base"], 7000)
        self.assertEqual(ref["status"], "reference_only")


class LocationCosts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "personalization.db"
        initialize(self.path)
        with connect(self.path) as conn:
            conn.execute("INSERT INTO organizations(id,name,created_at) VALUES('org','Test','2026-09-13')")
            conn.execute("""INSERT INTO locations(id,organization_id,name,concept,address,city,region,postal_code,
                         latitude,longitude,timezone,open_hour,close_hour)
                         VALUES('loc','org','Test','Cafe','','Denver','CO','',40.9893,-73.7974,'America/Denver',7,21)""")

    def tearDown(self):
        self.temp.cleanup()

    def test_no_payroll_input_means_no_wage_or_profit_estimate(self):
        with connect(self.path) as conn:
            output = costs.day_costs(conn, "loc", date(2026, 9, 13), [], 100)
            self.assertIsNone(output["labour"])
            self.assertIsNone(output["left_after_costs"])
            self.assertFalse(output["complete"])
            costs.save_cost_settings(conn, "loc", {"hourly_wage":20, "payroll_load_percent":""})
            self.assertIsNone(costs.cost_view(conn, "loc")["wage"]["loaded"])

    def test_actual_entered_payroll_is_used_and_can_be_cleared(self):
        with connect(self.path) as conn:
            result = costs.save_cost_settings(conn, "loc", {"hourly_wage":20, "payroll_load_percent":12.5})
            self.assertEqual(result["wage"]["loaded"], 22.5)
            self.assertEqual(result["wage"]["payroll_load_source"], "owner")
            result = costs.save_cost_settings(conn, "loc", {"hourly_wage":20, "payroll_load_percent":0})
            self.assertEqual(result["wage"]["loaded"], 20)
            result = costs.save_cost_settings(conn, "loc", {"hourly_wage":20, "payroll_load_percent":""})
            self.assertIsNone(result["wage"]["loaded"])

    def test_invalid_payroll_does_not_silently_become_a_default(self):
        with connect(self.path) as conn:
            for invalid in ["bad", "NaN", "inf", -1, 61]:
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    costs.save_cost_settings(conn, "loc", {"hourly_wage":20, "payroll_load_percent":invalid})

    def test_legacy_eighteen_percent_is_not_certified_as_owner_input(self):
        with connect(self.path) as conn:
            conn.execute("INSERT INTO cost_settings(location_id,hourly_wage,payroll_load_percent,updated_at) VALUES('loc',20,18,'2026-01-01')")
            self.assertIsNone(costs.cost_view(conn, "loc")["wage"]["loaded"])

    def test_legacy_coordinates_are_corrected_and_preserved_for_review(self):
        initialize(self.path)
        with connect(self.path) as conn:
            location = conn.execute("SELECT * FROM locations WHERE id='loc'").fetchone()
            self.assertLess(location["longitude"], -104)
            self.assertEqual(location["geography_status"], "city")
            previous = conn.execute("SELECT value FROM settings WHERE key='geography_previous_coordinates'").fetchone()
            self.assertEqual(json.loads(previous["value"])["longitude"], -73.7974)

    def test_unknown_point_blocks_outside_requests_and_old_context(self):
        with connect(self.path) as conn:
            conn.execute("UPDATE locations SET city='Unlisted city',region='ZZ',latitude=0,longitude=0 WHERE id='loc'")
            with patch.object(connectors, "_request_json") as request:
                with self.assertRaises(ValueError):
                    connectors.refresh_weather(conn, "loc")
                with self.assertRaises(ValueError):
                    connectors.refresh_events(conn, "loc")
                request.assert_not_called()
            row = conn.execute("SELECT * FROM locations WHERE id='loc'").fetchone()
            context = intelligence.build_context(row, date(2026,9,13), {"temp_high":90}, [{"name":"Old event"}])
            self.assertFalse(context["weather_available"])
            self.assertEqual(context["events"], [])
            self.assertEqual(context["daylight_hours"], 12)

    def test_weather_uses_actual_city_point_and_requires_matching_provenance(self):
        target = date(2026, 9, 13)
        payload = {"daily":{"time":[target.isoformat()],"temperature_2m_max":[70],"temperature_2m_min":[50]}}
        with connect(self.path) as conn:
            connectors._upsert_weather_payload(conn, "loc", payload, "old-provider")
            self.assertEqual(intelligence._weather_map(conn,"loc",target,target), {})
            urls = []
            def provider(url, **kwargs):
                urls.append(url)
                return payload
            with patch.object(connectors,"_request_json",side_effect=provider):
                connectors.refresh_weather(conn,"loc",start=target,days=1,backfill_days=1)
            for url in urls:
                self.assertLess(float(parse_qs(urlparse(url).query)["longitude"][0]), -104)
            self.assertIn(target.isoformat(), intelligence._weather_map(conn,"loc",target,target))
            conn.execute("UPDATE locations SET city='White Plains',region='NY' WHERE id='loc'")
            self.assertEqual(intelligence._weather_map(conn,"loc",target,target), {})

    def test_event_capacity_and_missing_coordinates_do_not_become_facts(self):
        target = date(2026, 9, 13)
        payload = {"_embedded":{"events":[{"id":"shared-event", "name":"Concert",
                    "dates":{"start":{"localDate":target.isoformat()}},
                    "_embedded":{"venues":[{"capacity":20000}]}}]}}
        with connect(self.path) as conn:
            conn.execute("""INSERT INTO locations(id,organization_id,name,concept,address,city,region,postal_code,
                          latitude,longitude,timezone,open_hour,close_hour)
                          SELECT 'second',organization_id,name,concept,address,city,region,postal_code,
                          latitude,longitude,timezone,open_hour,close_hour FROM locations WHERE id='loc'""")
            with patch.dict(os.environ, {"PREDICTHQ_ACCESS_TOKEN":"", "TICKETMASTER_API_KEY":"test"}), patch.object(connectors,"_request_json",return_value=payload):
                for location_id in ["loc", "second"]:
                    connectors.refresh_events(conn,location_id,start=target,days=1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 2)
            event = intelligence._events_map(conn,"loc",target,target)[target.isoformat()][0]
            self.assertIsNone(event["attendance"])
            self.assertIsNone(event["distance_miles"])
            self.assertEqual(intelligence.event_impact(event,7,21), 0)

    def test_predicthq_rank_never_becomes_attendance(self):
        target = date(2026, 9, 13)
        base = {"title":"Concert", "start":"2026-09-13T18:00:00Z", "end":"2026-09-13T20:00:00Z",
                "rank":99, "location":[-104.9,39.7]}
        payload = {"count":2, "results":[dict(base,id="missing"),dict(base,id="supplied",phq_attendance=4000)]}
        with connect(self.path) as conn:
            with patch.dict(os.environ, {"PREDICTHQ_ACCESS_TOKEN":"test"}), patch.object(connectors,"_request_json",return_value=payload):
                connectors.refresh_events(conn,"loc",start=target,days=1)
            events = intelligence._events_map(conn,"loc",target,target)[target.isoformat()]
            missing = next(row for row in events if row["id"].endswith("missing"))
            supplied = next(row for row in events if row["id"].endswith("supplied"))
            self.assertIsNone(missing["attendance"])
            self.assertEqual((supplied["attendance"],supplied["attendance_source"]), (4000,"provider_estimate"))
            self.assertEqual(supplied["distance_source"], "city_point")
            self.assertGreater(supplied["distance_miles"], 0)


if __name__ == "__main__":
    unittest.main()
