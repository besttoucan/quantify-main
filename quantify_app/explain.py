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


def sure_words(score: float) -> str:
    """How sure, said once in words."""
    if score >= 80:
        return "sure"
    if score >= 65:
        return "fairly sure"
    return "not sure"


def span_words(days: int) -> str:
    """A number of days of sales as a phrase a person would say."""
    days = int(days or 0)
    if days < 45:
        return plural(days, "day")
    if days < 330:
        return plural(round(days / 30.4), "month")
    years = round(days / 365.25)
    words = {1: "a year", 2: "two years", 3: "three years", 4: "four years", 5: "five years"}
    return words.get(years, f"{years} years")


def band_sentence(payload: dict[str, Any]) -> str:
    """One sentence on what today's number rests on. No percentage, no score."""
    confidence = payload.get("confidence", {})
    history = int(confidence.get("history_days") or 0)
    comparable = int(confidence.get("comparable_days") or 0)
    weekday = str(payload.get("weekday") or "day")
    if history == 0:
        return "No sales here yet, so there is nothing to go on."
    if history < 60:
        return f"Only {plural(history, 'day')} of sales here, so the range is wide."
    return f"Rests on {plural(comparable, 'comparable ' + weekday)} in {span_words(history)} of sales."


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
                "key": row.get("key", ""),
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

    # The headline uses the same 4% threshold as the level tag, so the two
    # never disagree on whether the day is normal.
    if abs(percent) < 4:
        headline = f"A normal {weekday}"
    else:
        word = "busier" if percent > 0 else "quieter"
        headline = f"{abs(percent)}% {word} than a normal {weekday}"
    summary = (
        f"Plan for {money(expected.get('sales'))} and {plural(expected.get('items'), 'item')}. "
        f"A normal {weekday} here does {money(normal.get('sales'))}."
    )

    factors: list[dict[str, Any]] = []
    for driver in payload.get("drivers", []):
        factors.append({
            "key": driver.get("key", ""),
            "heading": driver.get("label", "Condition"),
            "explanation": driver.get("evidence", ""),
            "confidence": driver.get("confidence", "medium"),
            "based_on": driver.get("based_on", ""),
        })
    if not factors:
        factors.append({
            "key": "pattern",
            "heading": f"A usual {weekday}",
            "explanation": (
                f"Nothing outside the restaurant moves the number today. It follows the "
                f"{weekday} pattern here, from {span_words(payload.get('confidence', {}).get('history_days', 0))} of sales."
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

def parts_sentence(name: str, parts: list[str]) -> str:
    """"Foundry classic: patty, bun, cheese, lettuce, tomato, onion, sauce, wrap and box."

    A list of what goes in, in the order it is built. Nobody who makes burgers
    needs a burger explained to them.
    """
    words = [part.strip() for part in parts if part and part.strip()]
    words = [word[0].lower() + word[1:] if word[:1].isupper() and not word[:2].isupper() else word for word in words]
    if not words:
        return f"{name}: no parts on file yet."
    if len(words) == 1:
        return f"{name}: {words[0]}."
    return f"{name}: {', '.join(words[:-1])} and {words[-1]}."


def local_composition(payload: dict[str, Any]) -> dict[str, Any]:
    family = payload.get("family") or "menu-item"
    name = payload.get("normalized_name") or payload.get("raw_name") or "This item"
    rows = FAMILY_COMPONENTS.get(family)
    if not rows:
        return {
            "summary": f"{name}: no parts on file yet.",
            "confidence": "low",
            "verify_note": "Add the recipe to fill this in.",
            "components": [],
        }
    confidence = "medium" if float(payload.get("interpretation_confidence") or 0) >= 0.68 else "low"
    return {
        "summary": parts_sentence(name, [row[0] for row in rows]),
        "confidence": confidence,
        "verify_note": "Read from the till label. Confirm it against the recipe card.",
        "components": [
            {"name": row[0], "role": row[1], "share": row[2], "quantity": row[3], "confidence": confidence}
            for row in rows
        ],
    }


# ---------------------------------------------------------------------------
# Day review
# ---------------------------------------------------------------------------

def _lower_first(name: str) -> str:
    """"Vanilla shake" -> "vanilla shake" mid-sentence; an all-caps start is left alone."""
    if name[:1].isupper() and not name[:2].isupper():
        return name[0].lower() + name[1:]
    return name


def local_day_review(payload: dict[str, Any]) -> dict[str, Any]:
    """How the day went, written about the restaurant.

    Sold 434 items, 9 more than the 425 expected. A normal Wednesday here
    does about 420. / Crispy chicken sandwich came in 12 under, vanilla shake
    11 over. Everything else was within 4. / (a reason only when there is a
    named cause) / Nothing here would have changed prep.
    """
    actual = payload.get("actual", {})
    predicted = payload.get("predicted", {})
    sold = int(round(float(actual.get("items") or 0)))
    expected = int(round(float(predicted.get("items") or 0)))
    unit_gap = sold - expected
    accuracy = float(payload.get("accuracy_percent") or 0)
    weekday = payload.get("weekday") or "day"
    normal = payload.get("normal_units")

    if unit_gap == 0:
        headline = f"Sold {plural(sold, 'item')}, exactly the {expected} expected."
    else:
        word = "more" if unit_gap > 0 else "fewer"
        headline = f"Sold {plural(sold, 'item')}, {abs(unit_gap)} {word} than the {expected} expected."
    if normal:
        headline += f" A normal {weekday} here does about {int(round(float(normal)))}."

    misses = payload.get("item_misses", [])
    others_within = payload.get("others_within")
    if misses:
        parts = []
        for index, row in enumerate(misses[:3]):
            gap = int(round(float(row.get("actual") or 0) - float(row.get("predicted") or 0)))
            name = row["name"] if index == 0 else _lower_first(row["name"])
            parts.append(f"{name}{' came in' if index == 0 else ''} {abs(gap)} {'over' if gap > 0 else 'under'}")
        where = ", ".join(parts) + "."
        if others_within is not None and len(payload.get("all_items", [])) > len(misses[:3]):
            where += f" Everything else was within {int(others_within)}."
    elif others_within is not None:
        where = f"No item was more than {int(others_within)} off."
    else:
        where = ""

    reason = payload.get("condition_note") or ""

    if accuracy >= 92 or abs(unit_gap) <= 15:
        matters = "Nothing here would have changed prep."
    elif misses and misses[0].get("sold_out"):
        matters = f"{misses[0]['name']} ran out during service, so that miss cost sales."
    else:
        matters = ""

    return {"headline": headline, "where_error_sat": where, "likely_reason": reason, "matters": matters}
