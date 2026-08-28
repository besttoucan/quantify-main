"""Turns a forecast record into sentences.

Every number shown to an operator has to arrive with three things: the value,
what it is being compared against, and how that difference lands in real units.
A percentage on its own is not an answer. This module enforces that, and it does
so for both writers, so the product reads the same whether or not an Anthropic
key is present.
"""

from __future__ import annotations

from typing import Any

ROLE_LABELS = {
    "base": "Base",
    "protein": "Protein",
    "dairy": "Dairy",
    "produce": "Produce",
    "bread": "Bread",
    "sauce": "Sauce",
    "sweetener": "Sweetener",
    "beverage": "Beverage",
    "packaging": "Packaging",
    "other": "Other",
}


def money(value: float) -> str:
    value = float(value or 0)
    if abs(value) >= 1000:
        return f"${value:,.0f}"
    return f"${value:,.0f}" if abs(value) >= 10 else f"${value:,.2f}"


def plural(count: float, singular: str, many: str | None = None) -> str:
    count = round(float(count or 0))
    word = singular if abs(count) == 1 else (many or f"{singular}s")
    return f"{count:,} {word}"


def band(score: float) -> str:
    if score >= 80:
        return "high"
    if score >= 65:
        return "medium"
    return "low"


def band_sentence(payload: dict[str, Any]) -> str:
    confidence = payload.get("confidence", {})
    score = int(confidence.get("score") or 0)
    history = int(confidence.get("history_days") or 0)
    tested = int(confidence.get("days_tested") or 0)
    error = float(confidence.get("recent_error_percent") or 0)
    comparable = int(confidence.get("comparable_days") or 0)
    if history < 60:
        return (
            f"Confidence is {band(score)} at {score}%. There are only {plural(history, 'day')} of sales history "
            "here, so the range is wide. It narrows as more days close."
        )
    if tested < 5:
        return (
            f"Confidence is {band(score)} at {score}%, based on {plural(comparable, 'comparable day')} in "
            f"{plural(history, 'day')} of sales."
        )
    return (
        f"Confidence is {band(score)} at {score}%. Across the last {plural(tested, 'closed day')} the forecast "
        f"has been off by {error:.1f}% on average here, and today is built from "
        f"{plural(comparable, 'comparable day')} in {plural(history, 'day')} of sales."
    )


# ---------------------------------------------------------------------------
# Day narrative
# ---------------------------------------------------------------------------

def build_day_payload(brief: dict[str, Any]) -> dict[str, Any]:
    """Flatten a forecast into the record both writers read.

    Nothing is computed here. This only selects the figures that belong in a
    written summary and puts a comparison next to each one.
    """
    summary = brief.get("summary", {})
    comparison = brief.get("comparison", {})
    trust = brief.get("trust", {})
    location = brief.get("location", {})
    weather = (brief.get("context") or {}).get("weather", {})
    events = (brief.get("context") or {}).get("material_events", [])

    movers = [
        {
            "name": row["name"],
            "expected": row["expected"],
            "normal": row["baseline"],
            "difference": row["vs_baseline_units"],
            "percent": row["vs_baseline_percent"],
            "range_low": row["lower"],
            "range_high": row["upper"],
            "confidence": row["confidence"],
        }
        for row in brief.get("items", [])
        if abs(int(row.get("vs_baseline_units") or 0)) >= 2
    ]
    movers.sort(key=lambda row: abs(row["difference"]), reverse=True)

    return {
        "location": {
            "name": location.get("name"),
            "concept": location.get("concept"),
            "city": location.get("city"),
            "region": location.get("region"),
            "opens": location.get("open_hour"),
            "closes": location.get("close_hour"),
        },
        "date": brief.get("date"),
        "weekday": (brief.get("date_label") or "").split(",")[0] or "today",
        "expected": {
            "sales": summary.get("expected_revenue"),
            "items": summary.get("expected_units"),
            "orders": summary.get("expected_orders"),
            "average_order": summary.get("average_order"),
        },
        "normal_day": {
            "sales": comparison.get("sales"),
            "items": comparison.get("units"),
            "orders": comparison.get("orders"),
            "based_on_days": comparison.get("based_on_days"),
            "label": comparison.get("label"),
        },
        "difference": {
            "sales": summary.get("difference_sales"),
            "items": summary.get("difference_units"),
            "orders": summary.get("difference_orders"),
            "percent": summary.get("revenue_change_percent"),
        },
        "busiest_hour": {
            "label": summary.get("peak_hour"),
            "sales": summary.get("peak_revenue"),
            "items": summary.get("peak_units"),
            "share_percent": summary.get("peak_share_percent"),
        },
        "confidence": {
            "score": trust.get("score"),
            "band": band(float(trust.get("score") or 0)),
            "history_days": trust.get("history_days"),
            "comparable_days": trust.get("comparable_days"),
            "days_tested": trust.get("days_tested"),
            "recent_error_percent": trust.get("recent_error_percent") or 0,
        },
        "drivers": [
            {
                "label": row["label"],
                "percent": row["effect"],
                "units": row["units"],
                "sales": row["sales"],
                "evidence": row["detail"],
                "based_on": row.get("based_on", ""),
                "confidence": band(float(trust.get("score") or 0)),
            }
            for row in (brief.get("context") or {}).get("signals", [])
        ],
        "conditions": {
            "weather": weather,
            "occasion": (brief.get("context") or {}).get("occasion_name"),
            "events": [
                {
                    "name": event.get("name"),
                    "distance_miles": round(float(event.get("distance_miles") or 0), 1),
                    "attendance": int(event.get("attendance") or 0),
                    "start": event.get("start_time"),
                }
                for event in events
            ],
        },
        "items_moving": movers[:6],
        "actions": brief.get("actions", []),
        "prep_families": brief.get("material_pressure", [])[:5],
    }


def local_day_narrative(payload: dict[str, Any]) -> dict[str, Any]:
    expected = payload.get("expected", {})
    normal = payload.get("normal_day", {})
    difference = payload.get("difference", {})
    weekday = payload.get("weekday", "today")
    percent = int(difference.get("percent") or 0)
    sales_gap = float(difference.get("sales") or 0)
    item_gap = int(difference.get("items") or 0)
    direction = "above" if percent > 0 else "below"

    if abs(percent) < 4:
        headline = f"An ordinary {weekday}, close to what this location normally does"
        summary = (
            f"Plan for {money(expected.get('sales'))} and {plural(expected.get('items'), 'item')}. "
            f"A normal {weekday} here runs {money(normal.get('sales'))}, so today sits inside the usual range. "
            f"{payload.get('busiest_hour', {}).get('label', 'The busiest hour')} is still the hour to staff for, "
            f"carrying about {money(payload.get('busiest_hour', {}).get('sales'))}."
        )
    else:
        top_item = (payload.get("items_moving") or [{}])[0]
        lead = top_item.get("name") or "the core menu"
        headline = f"{weekday} runs {abs(percent)}% {direction} normal, {money(abs(sales_gap))} {'more' if percent > 0 else 'less'}"
        summary = (
            f"Plan for {money(expected.get('sales'))} against a normal {weekday} of {money(normal.get('sales'))}. "
            f"That is {money(abs(sales_gap))} {'more' if percent > 0 else 'less'} and about "
            f"{plural(abs(item_gap), 'item')} {'more' if percent > 0 else 'fewer'} across the menu. "
        )
        if top_item:
            summary += (
                f"{lead} carries most of it: {plural(top_item.get('expected'), 'unit')} against a normal "
                f"{plural(top_item.get('normal'), 'unit')}."
            )

    factors: list[dict[str, Any]] = []
    for driver in payload.get("drivers", []):
        factors.append({
            "heading": driver.get("label", "Signal"),
            "explanation": driver.get("evidence", ""),
            "confidence": driver.get("confidence", "medium"),
            "based_on": driver.get("based_on", ""),
        })
    if not factors:
        factors.append({
            "heading": "Recurring pattern",
            "explanation": (
                f"Nothing outside the restaurant moved the number today. The forecast is the location's own "
                f"{weekday} pattern, built from {plural(payload.get('confidence', {}).get('history_days', 0), 'day')} of sales."
            ),
            "confidence": band(payload.get("confidence", {}).get("score", 70)),
            "based_on": f"{plural(normal.get('based_on_days', 0), 'comparable ' + str(weekday))}",
        })

    actions: list[dict[str, Any]] = []
    for row in payload.get("actions", []):
        actions.append({"title": row.get("title", ""), "detail": row.get("detail", ""), "metric": row.get("metric", "")})

    return {
        "headline": headline,
        "summary": summary,
        "confidence_note": band_sentence(payload),
        "factors": factors[:5],
        "actions": actions[:3],
    }


# ---------------------------------------------------------------------------
# Item composition
# ---------------------------------------------------------------------------

# What a family is physically made of when no model is available. These are
# deliberately coarse: they are labelled as estimates in the interface and are
# replaced the moment a key or a recipe is connected.
FAMILY_COMPONENTS: dict[str, list[tuple[str, str, int, str]]] = {
    "burger": [("Beef patty", "protein", 42, "1 to 2 patties"), ("Burger bun", "bread", 18, "1 bun"), ("Cheese slice", "dairy", 12, "1 to 2 slices"), ("Lettuce, tomato, onion", "produce", 12, "1 portion"), ("Sauce", "sauce", 8, "1 portion"), ("Wrap and box", "packaging", 8, "1 set")],
    "chicken-sandwich": [("Chicken fillet", "protein", 45, "1 fillet"), ("Bun", "bread", 18, "1 bun"), ("Pickles and slaw", "produce", 13, "1 portion"), ("Sauce", "sauce", 10, "1 portion"), ("Breading", "base", 8, "1 coat"), ("Wrap and box", "packaging", 6, "1 set")],
    "pizza-whole": [("Dough ball", "base", 30, "1 ball"), ("Tomato sauce", "sauce", 15, "about 4 oz"), ("Mozzarella", "dairy", 32, "about 7 oz"), ("Toppings", "protein", 15, "1 portion"), ("Pizza box", "packaging", 8, "1 box")],
    "pizza-slice": [("Dough", "base", 30, "1 slice portion"), ("Tomato sauce", "sauce", 15, "about 1 oz"), ("Mozzarella", "dairy", 35, "about 1.5 oz"), ("Toppings", "protein", 12, "1 portion"), ("Paper plate", "packaging", 8, "1 plate")],
    "fries-side": [("Potato", "produce", 62, "about 5 oz"), ("Frying oil", "base", 18, "1 basket share"), ("Salt and seasoning", "other", 5, "1 pinch"), ("Carton", "packaging", 15, "1 carton")],
    "bread-loaf": [("Flour", "base", 52, "about 500 g"), ("Water", "base", 18, "about 350 ml"), ("Starter or yeast", "other", 12, "1 portion"), ("Salt", "other", 4, "about 10 g"), ("Bag", "packaging", 14, "1 bag")],
    "pastry": [("Flour", "base", 34, "about 120 g"), ("Butter", "dairy", 32, "about 60 g"), ("Sugar", "sweetener", 14, "about 25 g"), ("Egg", "protein", 10, "part of 1 egg"), ("Paper sleeve", "packaging", 10, "1 sleeve")],
    "bagel": [("Flour", "base", 55, "about 110 g"), ("Malt and yeast", "other", 12, "1 portion"), ("Topping seeds", "other", 12, "1 pinch"), ("Bag", "packaging", 21, "1 bag")],
    "breakfast-sandwich": [("Egg", "protein", 28, "1 to 2 eggs"), ("Bread or roll", "bread", 22, "1 roll"), ("Cheese", "dairy", 18, "1 slice"), ("Bacon or sausage", "protein", 22, "1 portion"), ("Wrap", "packaging", 10, "1 wrap")],
    "sandwich": [("Bread", "bread", 26, "2 slices or 1 roll"), ("Filling", "protein", 40, "about 4 oz"), ("Salad", "produce", 16, "1 portion"), ("Spread", "sauce", 8, "1 portion"), ("Wrap", "packaging", 10, "1 wrap")],
    "salad": [("Leaves", "produce", 34, "about 3 oz"), ("Vegetables", "produce", 22, "1 portion"), ("Protein", "protein", 24, "about 3 oz"), ("Dressing", "sauce", 10, "1 cup"), ("Bowl and lid", "packaging", 10, "1 set")],
    "coffee-hot": [("Coffee beans", "base", 46, "about 18 g"), ("Milk", "dairy", 26, "up to 8 oz"), ("Cup, lid and sleeve", "packaging", 28, "1 set")],
    "coffee-cold": [("Coffee beans", "base", 40, "about 20 g"), ("Milk", "dairy", 22, "up to 8 oz"), ("Ice", "base", 10, "1 scoop"), ("Cup, lid and straw", "packaging", 28, "1 set")],
    "shake": [("Ice cream base", "dairy", 48, "about 8 oz"), ("Milk", "dairy", 16, "about 3 oz"), ("Flavour syrup", "sweetener", 16, "about 1.5 oz"), ("Cup, lid and straw", "packaging", 20, "1 set")],
    "smoothie": [("Fruit", "produce", 46, "about 6 oz"), ("Yoghurt or juice", "dairy", 22, "about 4 oz"), ("Ice", "base", 10, "1 scoop"), ("Cup, lid and straw", "packaging", 22, "1 set")],
    "dessert": [("Flour and base", "base", 32, "1 portion"), ("Butter or fat", "dairy", 22, "1 portion"), ("Sugar", "sweetener", 22, "1 portion"), ("Flavouring", "other", 12, "1 portion"), ("Wrapper", "packaging", 12, "1 wrapper")],
    "beverage": [("Concentrate or leaf", "beverage", 40, "1 portion"), ("Water or soda", "beverage", 30, "up to 16 oz"), ("Ice", "base", 8, "1 scoop"), ("Cup, lid and straw", "packaging", 22, "1 set")],
    "entree": [("Protein", "protein", 40, "about 6 oz"), ("Starch", "base", 22, "1 portion"), ("Vegetables", "produce", 18, "1 portion"), ("Sauce", "sauce", 10, "1 portion"), ("Container", "packaging", 10, "1 container")],
}

FAMILY_DESCRIPTIONS: dict[str, str] = {
    "burger": "a griddled beef sandwich served in a bun",
    "chicken-sandwich": "a breaded or grilled chicken fillet served in a bun",
    "pizza-whole": "a whole pizza built on a dough base",
    "pizza-slice": "a single slice cut from a whole pizza",
    "fries-side": "a fried potato side",
    "bread-loaf": "a baked loaf sold whole",
    "pastry": "a laminated or enriched baked good",
    "bagel": "a boiled and baked roll",
    "breakfast-sandwich": "an egg-based sandwich served in the morning",
    "sandwich": "a made-to-order sandwich",
    "salad": "a cold assembled bowl",
    "coffee-hot": "a hot espresso or filter drink",
    "coffee-cold": "a cold coffee drink served over ice",
    "shake": "a blended ice cream drink",
    "smoothie": "a blended fruit drink",
    "dessert": "a sweet baked or plated item",
    "beverage": "a poured cold drink",
    "entree": "a plated or bowled main",
}


def local_composition(payload: dict[str, Any]) -> dict[str, Any]:
    family = payload.get("family") or "menu-item"
    name = payload.get("normalized_name") or payload.get("raw_name") or "This item"
    rows = FAMILY_COMPONENTS.get(family)
    if not rows:
        return {
            "summary": f"{name} is on the menu, but what it is made of is not on file yet.",
            "confidence": "low",
            "verify_note": "Add the recipe to fill this in.",
            "components": [],
        }
    description = FAMILY_DESCRIPTIONS.get(family, "a menu item")
    confidence = "medium" if float(payload.get("interpretation_confidence") or 0) >= 0.68 else "low"
    return {
        "summary": f"{name} reads as {description}. ",
        "confidence": confidence,
        "verify_note": "Confirm against the recipe card before ordering to these numbers.",
        "components": [
            {"name": row[0], "role": row[1], "share": row[2], "quantity": row[3], "confidence": confidence}
            for row in rows
        ],
    }


# ---------------------------------------------------------------------------
# Day review
# ---------------------------------------------------------------------------

def local_day_review(payload: dict[str, Any]) -> dict[str, Any]:
    actual = payload.get("actual", {})
    predicted = payload.get("predicted", {})
    unit_gap = int(round(float(actual.get("items") or 0) - float(predicted.get("items") or 0)))
    sales_gap = float(actual.get("sales") or 0) - float(predicted.get("sales") or 0)
    accuracy = float(payload.get("accuracy_percent") or 0)
    direction = "under" if unit_gap > 0 else "over"

    # Accuracy is measured item by item, so a day whose totals match can still
    # score poorly if two items missed in opposite directions. Saying both keeps
    # that from reading like a contradiction.
    if abs(unit_gap) <= 2:
        headline = (
            f"Item by item the forecast landed {accuracy:.1f}% right. The day total was almost exact, "
            f"{plural(predicted.get('items'), 'item')} called against {plural(actual.get('items'), 'item')} sold, "
            "so what error there was cancelled out across the menu."
        )
    else:
        headline = (
            f"Item by item the forecast landed {accuracy:.1f}% right. On the day total it called "
            f"{plural(predicted.get('items'), 'item')} against {plural(actual.get('items'), 'item')} sold, "
            f"{plural(abs(unit_gap), 'item')} {direction} on {money(abs(sales_gap))}."
        )

    misses = payload.get("item_misses", [])
    if misses:
        parts = [
            f"{row['name']} ({int(row['predicted'])} called, {int(row['actual'])} sold)"
            for row in misses[:3]
        ]
        where = "Most of the gap sat in " + ", ".join(parts) + "."
    else:
        where = "The gap was spread evenly across the menu rather than concentrated in one item."

    reason = payload.get("condition_note") or "Nothing in the day's conditions explains the gap, so it reads as ordinary variation."

    if accuracy >= 92 or abs(unit_gap) <= 15:
        matters = "A gap this size would not have changed prep or staffing."
    elif misses and misses[0].get("sold_out"):
        matters = f"{misses[0]['name']} ran out during service, so this one did cost sales."
    else:
        matters = "Worth a look if the same item keeps missing in the same direction, which usually means the recipe or portion changed."

    return {"headline": headline, "where_error_sat": where, "likely_reason": reason, "matters": matters}
