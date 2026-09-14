"""Backend behavior at the database and HTTP boundaries, with isolated copies."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import http.client as http_client
import json
import sqlite3
import threading
import time
from unittest.mock import patch

import pytest

import server
from quantify_app import auth, email_brief, intelligence, ordering, supply, transactions
from quantify_app.database import connect, initialize
from quantify_app.seed import seed_workspace


TODAY = date(2026, 9, 13)
PASSWORD = "Kitchen-Contract-2026!"


@pytest.fixture(scope="module")
def original(tmp_path_factory):
    database = tmp_path_factory.mktemp("contract-source") / "sample.sqlite3"
    initialize(database)
    with connect(database) as conn:
        owner = auth.create_owner(conn, "owner@quantify.test", "Dana", PASSWORD)
        conn.execute("UPDATE users SET email_verified=1 WHERE id=?", (owner["user_id"],))
        location = seed_workspace(conn, owner["organization_id"], name="Contract kitchen", concept="Burgers",
                                  owner_email=owner["email"], history_days=65, today=TODAY)
        session = auth.create_session(conn, owner["user_id"])
    return database, location, owner, session


@pytest.fixture
def sample(original, tmp_path, monkeypatch):
    source, location, owner, session = original
    database = tmp_path / "copy.sqlite3"
    with sqlite3.connect(source) as origin, sqlite3.connect(database) as target:
        origin.backup(target)
    monkeypatch.setattr(transactions, "_local_today", lambda conn, location_id: TODAY)
    monkeypatch.setattr(server, "_location_today", lambda conn, location_id: TODAY)
    monkeypatch.setattr(server, "DB_PATH", database)
    monkeypatch.setattr(server, "ROOT", tmp_path)
    monkeypatch.setenv("QUANTIFY_AUTH_BYPASS", "0")
    monkeypatch.setenv("QUANTIFY_DISABLE_SCHEDULER", "1")
    monkeypatch.setattr(email_brief, "mail_provider", lambda: "outbox")
    monkeypatch.setattr(server, "mail_provider", lambda: "outbox")
    return database, location, owner, session


@pytest.fixture
def http(sample):
    database, location, owner, session = sample
    service = server.QuantifyServer(("127.0.0.1", 0), server.QuantifyHandler)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    client = http_client.HTTPConnection(*service.server_address, timeout=60)

    def request(path, method="GET", payload=None, signed=True, csrf=True):
        headers = {}
        if signed:
            headers["Cookie"] = "quantify_session=" + session["session_token"]
        if csrf:
            headers["X-CSRF-Token"] = session["csrf_token"]
        if payload is not None:
            headers["Content-Type"] = "application/json"
        client.request(method, path, body=json.dumps(payload) if payload is not None else None, headers=headers)
        response = client.getresponse()
        body = response.read()
        value = json.loads(body) if "application/json" in response.getheader("Content-Type", "") else body
        return response, value

    try:
        yield request, client, sample
    finally:
        client.close()
        service.shutdown()
        service.server_close()
        thread.join(timeout=5)


def test_brief_cache_is_reusable_isolated_and_runs_are_deduplicated(sample):
    database, location, _, _ = sample
    with connect(database) as conn, patch.object(intelligence, "forecast_day", wraps=intelligence.forecast_day) as build:
        first = intelligence.daily_brief(conn, location, TODAY, week_days=2)
        count = build.call_count
        first["summary"]["expected_units"] = -999
        second = intelligence.daily_brief(conn, location, TODAY, week_days=2)
        assert build.call_count == count
        assert second["summary"]["expected_units"] > 0
        intelligence.daily_brief(conn, location, TODAY, week_days=2, refresh=True)
        assert build.call_count > count
        assert conn.execute("SELECT COUNT(*) FROM forecast_runs").fetchone()[0] == 1
        item = second["items"][0]
        changed = intelligence.set_override(conn, location, item["item_id"], TODAY, 90, "", "Dana")
        assert changed["make"] == 90
        assert changed["expected"] == item["expected"]
        updated = intelligence.daily_brief(conn, location, TODAY, week_days=2)
        assert next(row for row in updated["items"] if row["item_id"] == item["item_id"])["override"]["updated_by"] == "Dana"
        assert conn.execute("SELECT COUNT(*) FROM forecast_runs").fetchone()[0] == 2


def test_simultaneous_briefs_write_one_run(sample):
    database, location, _, _ = sample
    def read(_):
        with connect(database) as conn:
            return intelligence.daily_brief(conn, location, TODAY, week_days=2)["summary"]
    with ThreadPoolExecutor(max_workers=4) as workers:
        values = list(workers.map(read, range(4)))
    assert all(value == values[0] for value in values)
    with connect(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM forecast_runs").fetchone()[0] == 1


def test_unknown_geography_does_not_publish_fallback_weather(sample):
    database, location, _, _ = sample
    with connect(database) as conn:
        conn.execute("UPDATE locations SET city='Unknown place',region='ZZ' WHERE id=?", (location,))
        brief = intelligence.daily_brief(conn, location, TODAY, week_days=1)
        assert brief["context"]["weather"]["available"] is False
        assert brief["context"]["weather"]["high"] is None
        assert brief["context"]["weather"]["low"] is None
        assert brief["context"]["material_events"] == []


def test_new_items_do_not_add_invented_demand_and_import_preserves_register(sample):
    database, location, _, _ = sample
    with connect(database) as conn:
        original = intelligence.daily_brief(conn, location, TODAY, week_days=2)
        record = conn.execute("SELECT id,name,price FROM menu_items WHERE location_id=? LIMIT 1", (location,)).fetchone()
        conn.execute("UPDATE menu_items SET pos_item_id='register-owned' WHERE id=?", (record["id"],))
        name = "The Really Very Long Double Bacon Cheeseburger With Everything On It"
        imported = server._menu_import(conn, location, f"{name}, Burgers, 15.50\nNo price sandwich\n{record['name']}, Burgers, 99.00", True)
        assert (imported["created"], imported["skipped"]) == (1, 2)
        assert conn.execute("SELECT price FROM menu_items WHERE id=?", (record["id"],)).fetchone()[0] == record["price"]
        current = intelligence.daily_brief(conn, location, TODAY, week_days=2)
        new = next(row for row in current["items"] if row["raw_name"] == name)
        assert new["category"] == "Burgers"
        assert new["new_item"] and new["expected"] == new["make"] == new["lower"] == new["upper"] == 0
        assert current["summary"]["expected_units"] == original["summary"]["expected_units"]
        assert all(row["item_id"] != new["item_id"] for row in current["actions"])


def test_empty_email_and_brief_have_no_invented_totals(sample):
    database, location, _, _ = sample
    with connect(database) as conn:
        conn.execute("DELETE FROM sales WHERE location_id=?", (location,))
        conn.execute("DELETE FROM sales_hourly WHERE location_id=?", (location,))
        conn.commit()
        built = email_brief.build_email(conn, location, TODAY)
        assert built["brief"]["no_history"] is True
        assert built["brief"]["summary"]["expected_orders"] == 0
        assert "Nothing to plan yet" in built["html"]
        assert "100% quieter" not in built["html"]
        assert "confidence" not in built["html"]


def test_history_pages_include_trailing_gaps_and_do_not_repeat(sample):
    database, location, _, _ = sample
    with connect(database) as conn:
        last = TODAY - timedelta(days=19)
        conn.execute("DELETE FROM sales WHERE location_id=? AND date>?", (location, last.isoformat()))
        conn.commit()
        first = transactions.day_list(conn, location, limit=14)
        second = transactions.day_list(conn, location, before=date.fromisoformat(first["next_before"]), limit=14)
        assert len(first["days"]) == len(second["days"]) == 14
        assert first["days"][0]["date"] == (TODAY - timedelta(days=1)).isoformat()
        assert first["newest_sale_date"] == last.isoformat()
        assert all(row["closed"] for row in first["days"])
        assert not ({row["date"] for row in first["days"]} & {row["date"] for row in second["days"]})


def test_history_and_accuracy_share_integer_predictions(sample):
    database, location, _, _ = sample
    with connect(database) as conn:
        score = intelligence.performance(conn, location, TODAY, 30)
        page = transactions.day_list(conn, location, limit=14)
        day = next(row for row in page["days"] if not row["closed"])
        detail = transactions.day_detail(conn, location, date.fromisoformat(day["date"]))
        chart = next(row for row in score["daily"] if row["date"] == day["date"])
        assert day["predicted_units"] == detail["predicted_units"] == chart["predicted"]
        assert day["accuracy"] == detail["accuracy"] == chart["accuracy"]
        assert all(isinstance(row["predicted"], int) for row in detail["item_scores"])
        assert detail["normal_sales"] > 0 and detail["normal_units"] > 0
        assert len(score["daily"]) == len(score["trend"]["series"]) == score["summary"]["days_evaluated"] == 30
        # The sample has hourly item totals, not observed ticket channels.
        assert detail['orders'] is None and day['orders'] is None
        assert detail['channels'] == []


def test_http_bad_numbers_and_recipe_shape_are_friendly(http):
    request, _, (_, location, _, _) = http
    response, body = request(f"/api/ordering?location_id={location}&days=potato")
    assert response.status == 400
    assert "Days" in body["error"] and "int()" not in body["error"]
    response, body = request(f"/api/supply/count?location_id={location}", "POST", {"ingredient": "beef patty", "on_hand": -2, "unit": "patty", "kind": "count"})
    assert response.status == 400 and "below zero" in body["error"]
    database = http[2][0]
    with connect(database) as conn:
        item = conn.execute("SELECT id FROM menu_items WHERE location_id=? LIMIT 1", (location,)).fetchone()[0]
    response, body = request(f"/api/menu/composition?location_id={location}", "PUT", {"item_id": item, "components": ["not a part"]})
    assert response.status == 400 and "recipe" in body["error"].lower()


def test_http_count_returns_the_order_line_and_mail_app_waits_for_confirmation(http):
    request, _, (_, location, _, _) = http
    response, page = request(f"/api/ordering?location_id={location}")
    assert response.status == 200 and page["ready"]
    item = next(row for row in page["lines"] if row["kind"] == "count")
    response, supplier = request(f"/api/supply/supplier?location_id={location}", "POST",
                                  {"name": "Local supplier", "order_email": "orders@example.test"})
    assert response.status == 200
    assert request(f"/api/supply/item?location_id={location}", "PUT", {"ingredient": item["name"], "supplier_id": supplier["id"]})[0].status == 200
    response, saved = request(f"/api/supply/count?location_id={location}", "POST", {"ingredient": item["name"], "on_hand": 0, "unit": item["unit"], "kind": item["kind"], "days": 3})
    assert response.status == 200 and saved["line"]["on_hand"] == 0
    assert {"short", "suggested", "needs_action", "per_day", "on_order"} <= saved["line"].keys()
    response, attention = request(f"/api/supply/attention?location_id={location}")
    assert response.status == 200 and set(attention) == {"lines", "counted", "as_of"}
    warning = next(row for row in attention["lines"] if row["name"] == item["name"])
    assert set(warning) == set(supply.ATTENTION_KEYS)
    assert warning["per_day"] == saved["line"]["per_day"]
    payload = {"supplier_id": supplier["id"], "channel": "mail-app", "lines": [{"name": item["name"], "quantity": 10, "unit": item["unit"]}]}
    response, drafted = request(f"/api/supply/order?location_id={location}", "POST", payload)
    assert response.status == 200 and drafted["recorded"] is False and drafted["mailto"].startswith("mailto:")
    assert request(f"/api/supply/orders?location_id={location}")[1]["orders"] == []
    response, sent = request(f"/api/supply/order?location_id={location}", "POST", payload | {"confirmed": True})
    assert response.status == 200 and sent["recorded"] is True
    response, after = request(f"/api/ordering?location_id={location}")
    changed = next(row for row in after["lines"] if row["name"] == item["name"])
    assert changed["on_order"] == 10 and changed["short"] < saved["line"]["short"]


def test_zero_make_reduces_ingredient_demand_and_wrong_pack_units_are_flagged(sample):
    database, location, _, _ = sample
    with connect(database) as conn:
        first = ordering.order_plan(conn, location, TODAY, 2)
        ids = [row["id"] for row in conn.execute("SELECT id FROM menu_items WHERE location_id=?", (location,))]
        for item in ids:
            for day in (TODAY, TODAY + timedelta(days=1)):
                intelligence.set_override(conn, location, item, day, 0)
        current = ordering.order_plan(conn, location, TODAY, 2)
        assert sum(row["base_high"] for row in first["lines"]) > 0
        assert sum(row["base_high"] for row in current["lines"]) == 0
        count = next(row for row in first["lines"] if row["kind"] == "count")
        supply.save_item(conn, location, {"ingredient": count["name"], "pack_size": 20, "pack_unit": "lb"})
        attached = supply.attach(conn, location, first)
        line = next(row for row in attached["lines"] if row["name"] == count["name"])
        assert line["pack_note"] and line["packs_short"] is None


def test_menu_returns_while_recipe_is_generated_and_preserves_new_owner_edit(http, monkeypatch):
    request, _, (database, location, _, _) = http
    with connect(database) as conn:
        items = [row[0] for row in conn.execute("SELECT id FROM menu_items WHERE location_id=?", (location,))]
        for item in items[1:]:
            conn.execute("INSERT OR REPLACE INTO item_composition(menu_item_id,summary,confidence,verify_note,components_json,writer,updated_at) VALUES(?,'','high','','[]','owner','2026-09-13')", (item,))
        conn.commit()
    waiting, release = threading.Event(), threading.Event()

    def generate(*args, **kwargs):
        waiting.set()
        assert release.wait(timeout=10)
        return {"summary": "Generated recipe", "components": [{"name": "Old part", "share": 100}], "_writer": "local"}

    monkeypatch.setattr(server.ai, "generate", generate)
    try:
        response, menu = request(f"/api/menu?location_id={location}")
        assert response.status == 200 and menu["pending_compositions"] == 1
        assert waiting.wait(timeout=3) and not release.is_set()
        response, saved = request(f"/api/menu/composition?location_id={location}", "PUT",
                                  {"item_id": items[0], "components": [{"name": "Owner part", "share": 100, "quantity": "1 part"}]})
        assert response.status == 200
    finally:
        release.set()
        deadline = time.monotonic() + 10
        while server._COMPOSITION_JOBS and time.monotonic() < deadline:
            time.sleep(0.02)
    assert not server._COMPOSITION_JOBS
    with connect(database) as conn:
        recipe = conn.execute("SELECT writer,components_json FROM item_composition WHERE menu_item_id=?", (items[0],)).fetchone()
        assert recipe["writer"] == "owner"
        assert json.loads(recipe["components_json"])[0]["name"] == "Owner part"


def test_disabled_morning_email_can_clear_its_address(sample):
    database, location, _, _ = sample
    with connect(database) as conn:
        saved = email_brief.update_preferences(conn, location, "", False, "05:30", "America/New_York")
        assert saved["owner_email"] == "" and saved["enabled"] == 0
        with pytest.raises(ValueError):
            email_brief.update_preferences(conn, location, "", True, "05:30", "America/New_York")


def test_sample_workspace_keeps_the_owners_city_region_hours_and_email(sample):
    database, _, owner, _ = sample
    with connect(database) as conn:
        location = seed_workspace(conn, owner["organization_id"], name="Dana's tacos", concept="Taqueria",
                                  city="Denver CO", owner_email=owner["email"], open_hour=10, close_hour=22,
                                  history_days=14, today=TODAY)
        saved = dict(conn.execute("SELECT * FROM locations WHERE id=?", (location,)).fetchone())
        assert (saved["name"], saved["concept"], saved["city"], saved["region"], saved["timezone"]) == (
            "Dana's tacos", "Taqueria", "Denver", "CO", "America/Denver")
        assert (saved["open_hour"], saved["close_hour"]) == (10, 22)
        assert saved["address"] == saved["postal_code"] == ""
        assert email_brief.preferences(conn, location)["owner_email"] == owner["email"]
