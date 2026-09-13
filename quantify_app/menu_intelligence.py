from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Iterable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


ABBREVIATIONS = {
    "dbl": "double", "dble": "double", "trpl": "triple", "chz": "cheese",
    "chs": "cheese", "brgr": "burger", "burg": "burger", "chk": "chicken",
    "chkn": "chicken", "crispy chx": "crispy chicken", "pep": "pepperoni",
    "marg": "margherita", "margt": "margherita", "veg": "vegetable",
    "veggie": "vegetable", "slc": "slice", "sli": "slice", "lg": "large",
    "md": "medium", "sm": "small", "xl": "extra large", "iced": "iced",
    "lt": "latte", "croiss": "croissant", "crois": "croissant",
    "choc": "chocolate", "cinn": "cinnamon", "muff": "muffin",
    "sando": "sandwich", "sand": "sandwich", "fr": "fries",
    "ff": "fries", "bev": "beverage", "drk": "drink", "reg": "regular",
    "bfst": "breakfast", "bfast": "breakfast", "whl": "whole", "pc": "piece",
    "pcs": "pieces", "w/": "with", "no.": "number", "#": "number",
}

# The classifier intentionally covers broad operational families rather than a
# brittle list of exact menu names. Raw POS labels remain the source of truth.
FAMILY_RULES: list[tuple[str, tuple[str, ...], str, str, tuple[str, ...]]] = [
    ("burger", ("burger", "cheeseburger", "patty", "smash"), "lunch-dinner", "sandwich", ("protein", "bun")),
    ("chicken-sandwich", ("chicken sandwich", "crispy chicken", "grilled chicken", "chicken burger"), "lunch-dinner", "sandwich", ("chicken", "bun")),
    ("pizza-whole", ("pizza", "pie", "margherita", "pepperoni"), "lunch-dinner", "pie", ("dough", "sauce", "cheese")),
    ("pizza-slice", ("slice",), "lunch-dinner", "slice", ("dough", "sauce", "cheese")),
    ("fries-side", ("fries", "tots", "onion rings"), "lunch-dinner", "portion", ("potato",)),
    ("bread-loaf", ("sourdough", "loaf", "baguette", "bread"), "all-day", "loaf", ("flour",)),
    ("pastry", ("croissant", "danish", "muffin", "cinnamon roll", "scone", "pastry"), "breakfast", "piece", ("flour", "fat")),
    ("bagel", ("bagel",), "breakfast-lunch", "piece", ("flour",)),
    ("breakfast-sandwich", ("breakfast sandwich", "egg sandwich", "egg and cheese", "bacon egg"), "breakfast", "sandwich", ("egg", "bread")),
    ("sandwich", ("sandwich", "club", "hero", "sub", "wrap", "panini"), "lunch-dinner", "sandwich", ("bread", "filling")),
    ("salad", ("salad", "caesar", "greens"), "lunch-dinner", "bowl", ("produce",)),
    ("coffee-hot", ("coffee", "espresso", "americano", "cappuccino", "hot latte"), "breakfast-all-day", "cup", ("coffee",)),
    ("coffee-cold", ("iced coffee", "iced latte", "cold brew"), "breakfast-all-day", "cup", ("coffee", "ice")),
    ("shake", ("shake", "milkshake"), "lunch-dinner", "cup", ("dairy",)),
    ("smoothie", ("smoothie",), "all-day", "cup", ("produce",)),
    ("dessert", ("cookie", "brownie", "cake", "tart", "pie dessert", "dessert"), "all-day", "piece", ("baking",)),
    ("beverage", ("soda", "drink", "lemonade", "tea", "juice", "water"), "all-day", "cup", ("beverage",)),
    ("entree", ("plate", "bowl", "entree", "combo", "meal"), "lunch-dinner", "order", ("mixed",)),
]

CATEGORY_HINTS = {
    "burger": "burger", "burgers": "burger", "chicken": "chicken-sandwich",
    "whole pies": "pizza-whole", "pizza": "pizza-whole", "slices": "pizza-slice",
    "pastry": "pastry", "viennoiserie": "pastry", "bread": "bread-loaf",
    "breakfast": "breakfast-sandwich", "lunch": "sandwich", "salads": "salad",
    "sides": "fries-side", "shakes": "shake", "beverage": "beverage",
}


def _ascii(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def normalize_words(value: str) -> str:
    text = _ascii(value or "").lower().strip()
    text = text.replace("&", " and ")
    text = re.sub(r"[_/|]+", " ", text)
    text = re.sub(r"(?<=\d)x(?=\d)", " x ", text)
    text = re.sub(r"[^a-z0-9+#.'-]+", " ", text)
    tokens = []
    for token in text.split():
        replacement = ABBREVIATIONS.get(token, token)
        tokens.extend(replacement.split())
    cleaned = " ".join(tokens)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _display_name(normalized: str, original: str) -> str:
    # Keep genuinely distinctive names while expanding terse POS abbreviations.
    original_clean = re.sub(r"\s+", " ", original.strip())
    abbreviation_heavy = bool(re.search(r"\b(?:dbl|chz|brgr|chk|pep|slc|lg|sm|marg|croiss|choc)\b", original.lower()))
    all_caps = original_clean.isupper() and len(original_clean) > 4
    if abbreviation_heavy or all_caps or len(original_clean) <= 3:
        return normalized.title()
    return original_clean


def _phrase_score(text: str, phrase: str) -> float:
    if phrase in text:
        return 1.0 + min(0.25, len(phrase.split()) * 0.07)
    text_tokens = set(text.split())
    phrase_tokens = set(phrase.split())
    overlap = len(text_tokens & phrase_tokens) / max(1, len(phrase_tokens))
    fuzzy = SequenceMatcher(None, text, phrase).ratio()
    return max(overlap * 0.82, fuzzy * 0.58)


@dataclass(frozen=True)
class Interpretation:
    raw_name: str
    normalized_name: str
    item_family: str
    daypart: str
    production_unit: str
    confidence: float
    source: str
    inferred: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "raw_name": self.raw_name,
            "normalized_name": self.normalized_name,
            "item_family": self.item_family,
            "daypart": self.daypart,
            "production_unit": self.production_unit,
            "confidence": round(self.confidence, 3),
            "source": self.source,
            "inferred": self.inferred,
        }


def interpret_menu_item(raw_name: str, category: str = "", modifiers: Iterable[str] | None = None) -> Interpretation:
    normalized = normalize_words(raw_name)
    category_norm = normalize_words(category)
    modifier_text = " ".join(normalize_words(value) for value in (modifiers or []) if value)
    combined = " ".join(part for part in (normalized, category_norm, modifier_text) if part)

    best: tuple[float, str, str, str, tuple[str, ...]] | None = None
    for family, phrases, daypart, unit, components in FAMILY_RULES:
        score = max(_phrase_score(combined, phrase) for phrase in phrases)
        if CATEGORY_HINTS.get(category_norm) == family:
            score += 0.36
        if best is None or score > best[0]:
            best = (score, family, daypart, unit, components)

    assert best is not None
    score, family, daypart, unit, components = best
    matched = score >= 0.62
    if not matched:
        family, daypart, unit, components = "menu-item", "all-day", "unit", tuple()

    # Confidence reflects whether the label and category independently support the
    # interpretation. It never blocks item-level forecasting.
    confidence = 0.42
    if matched:
        confidence = min(0.97, 0.58 + max(0.0, score - 0.62) * 0.38)
    if category_norm and CATEGORY_HINTS.get(category_norm) == family:
        confidence = min(0.99, confidence + 0.10)
    if len(normalized.split()) >= 2:
        confidence = min(0.99, confidence + 0.04)

    display = _display_name(normalized or raw_name, raw_name)
    inferred = {
        "category_label": category or "Uncategorized",
        "semantic_tokens": normalized.split(),
        "material_families": list(components),
        "exact_recipe_known": False,
        "forecast_ready": True,
        "requires_review": confidence < 0.68,
    }
    return Interpretation(
        raw_name=raw_name,
        normalized_name=display,
        item_family=family,
        daypart=daypart,
        production_unit=unit,
        confidence=confidence,
        source="quantify-menu-interpreter-v2",
        inferred=inferred,
    )


def upsert_interpretation(
    conn: sqlite3.Connection,
    menu_item_id: str,
    raw_name: str,
    category: str = "",
    modifiers: Iterable[str] | None = None,
) -> dict[str, Any]:
    interpretation = interpret_menu_item(raw_name, category, modifiers)
    conn.execute(
        """INSERT INTO menu_interpretations(
            menu_item_id,raw_name,normalized_name,item_family,daypart,production_unit,
            confidence,source,inferred_json,reviewed,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,0,?)
        ON CONFLICT(menu_item_id) DO UPDATE SET
            raw_name=excluded.raw_name,
            normalized_name=excluded.normalized_name,
            item_family=excluded.item_family,
            daypart=excluded.daypart,
            production_unit=excluded.production_unit,
            confidence=excluded.confidence,
            source=excluded.source,
            inferred_json=excluded.inferred_json,
            updated_at=excluded.updated_at""",
        (
            menu_item_id, interpretation.raw_name, interpretation.normalized_name,
            interpretation.item_family, interpretation.daypart,
            interpretation.production_unit, interpretation.confidence,
            interpretation.source, json.dumps(interpretation.inferred, separators=(",", ":")),
            utc_now(),
        ),
    )
    return interpretation.as_dict()


def ensure_location_interpretations(conn: sqlite3.Connection, location_id: str) -> dict[str, int]:
    rows = conn.execute(
        """SELECT m.id,m.name,m.category
           FROM menu_items m LEFT JOIN menu_interpretations i ON i.menu_item_id=m.id
           WHERE m.location_id=? AND m.active=1 AND i.menu_item_id IS NULL""",
        (location_id,),
    ).fetchall()
    low = 0
    for row in rows:
        result = upsert_interpretation(conn, row["id"], row["name"], row["category"])
        low += int(result["confidence"] < 0.68)
    return {"created": len(rows), "low_confidence": low}


def menu_intelligence_view(conn: sqlite3.Connection, location_id: str) -> dict[str, Any]:
    ensure_location_interpretations(conn, location_id)
    rows = conn.execute(
        """SELECT m.id,m.name,m.category,m.price,m.pos_item_id,m.active,
                  i.raw_name,i.normalized_name,i.item_family,i.daypart,i.production_unit,
                  i.confidence,i.inferred_json,i.reviewed
           FROM menu_items m JOIN menu_interpretations i ON i.menu_item_id=m.id
           WHERE m.location_id=? AND m.active=1
           ORDER BY i.confidence ASC,m.category,m.name""",
        (location_id,),
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["inferred"] = json.loads(item.pop("inferred_json") or "{}")
        items.append(item)
    low = [item for item in items if float(item["confidence"]) < 0.68]
    return {
        "items": items,
        "summary": {
            "total": len(items),
            "interpreted": len(items),
            "high_confidence": len(items) - len(low),
            "review_optional": len(low),
            "forecast_blocked": 0,
        },
    }


NO_PRICE = "No price found, so this line is skipped"


def parse_menu_text(text: str) -> list[dict[str, Any]]:
    """Parse pasted menu text or simple CSV-like rows without requiring a template.

    A line is "name, category, price" or "name price", with a "Category:"
    line setting the category for the lines under it. The last comma-separated
    part before the price is the category and everything before it is the
    name, however long the name is. A line with no price comes back flagged
    (`ok` false) so the preview can say so and the import can skip it.
    """
    rows: list[dict[str, Any]] = []
    current_category = "Imported"
    for raw_line in text.replace("\r", "\n").split("\n"):
        line = raw_line.strip(" \t•-–—")
        if not line:
            continue
        if len(line) < 42 and not re.search(r"\d", line) and line.endswith(":"):
            current_category = line[:-1].strip() or current_category
            continue
        price_match = re.search(r"(?:\$\s*)?(\d{1,4}(?:\.\d{1,2})?)\s*$", line)
        price = float(price_match.group(1)) if price_match else 0.0
        name = line[:price_match.start()].strip(" ,.-\t") if price_match else line.strip(" ,.-\t")
        category = current_category
        if "," in name:
            parts = [part.strip() for part in name.split(",") if part.strip()]
            if len(parts) >= 2:
                if price_match:
                    category = parts[-1]
                    current_category = category
                    name = ", ".join(parts[:-1])
                else:
                    # "name, category" with nothing to price it by.
                    category = parts[-1]
                    name = ", ".join(parts[:-1])
        if len(name) < 2:
            continue
        interpretation = interpret_menu_item(name, category)
        rows.append({
            "name": name[:120],
            "category": category,
            "price": round(price, 2),
            "ok": price > 0,
            "reason": "" if price > 0 else NO_PRICE,
            "interpretation": interpretation.as_dict(),
        })
    return rows
