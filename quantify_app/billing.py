"""Subscription and payment handling.

Stripe is the payment processor. It is the right choice here: it handles card
storage, SCA, tax, dunning, and invoices, so this application never touches a
card number and never stores one. Everything below talks to Stripe's REST API
over HTTPS with the standard library, which keeps the install free of
dependencies.

Without Stripe keys the module runs in local mode. Plan state, the cancellation
survey, and every screen still work, and the interface says plainly that no
payment processor is connected. That way the cancellation flow can be reviewed
and tested before a single card is charged.

Cancelling is deliberately not hidden. It sits at the bottom of Account and
security, in plain sight, with a real button. What it is not is a single click
that silently ends a paid relationship: it asks why, offers a conversation, and
only then cancels.
"""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

STRIPE_API = "https://api.stripe.com/v1"
SUPPORT_EMAIL = os.getenv("QUANTIFY_SUPPORT_EMAIL", "support@quantify.app")

PLANS: dict[str, dict[str, Any]] = {
    "solo": {
        "code": "solo", "name": "One location", "monthly": 39,
        "annual_monthly": None, "max_locations": 1, "legacy": False,
        "blurb": "All operating tools for one active location.",
    },
    "team": {
        "code": "team", "name": "Up to three locations", "monthly": 99,
        "annual_monthly": None, "max_locations": 3, "legacy": False,
        "blurb": "All operating tools for up to three active locations.",
    },
    "standard": {
        "code": "standard",
        "name": "Standard",
        "monthly": 79,
        "annual_monthly": 69,
        "blurb": "Everything in Quantify, one location.",
        "max_locations": None, "legacy": True,
    },
    "founding": {
        "code": "founding",
        "name": "Founding",
        "monthly": 49,
        "annual_monthly": 44,
        "blurb": "Launch pricing for the first group of locations. Held for the life of the account.",
        "max_locations": None, "legacy": True,
    },
}

CANCELLATION_REASONS: list[dict[str, str]] = [
    {
        "code": "accuracy",
        "label": "The forecasts did not match what actually happened",
        "follow_up": "Which days or items were furthest off? We can pull yours and look.",
    },
    {
        "code": "unused",
        "label": "We are not getting enough use out of it",
        "follow_up": "What were you hoping it would do that it is not doing?",
    },
    {
        "code": "price",
        "label": "It costs too much for what we use",
        "follow_up": "What would the right price be for how you use it?",
    },
    {
        "code": "switched",
        "label": "We moved to a different product",
        "follow_up": "Which one, and what does it do better?",
    },
    {
        "code": "closing",
        "label": "The location is closing or changing hands",
        "follow_up": "Anything we should know so the handover is clean?",
    },
    {
        "code": "other",
        "label": "Something else",
        "follow_up": "Tell us what happened.",
    },
]


# The one sentence every payment action says when no processor is connected.
# It never names the processor or a key: the person reading it cannot fix that.
NOT_SET_UP = "Payments are not set up on this account yet"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _trial_over(record: dict[str, Any]) -> bool:
    if record.get("status") != "trialing" or not record.get("trial_end"):
        return False
    try:
        end = datetime.fromisoformat(str(record["trial_end"]))
    except ValueError:
        return False
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return end < datetime.now(timezone.utc)


def _secret_key() -> str:
    return (os.getenv("STRIPE_SECRET_KEY") or "").strip()


def _price_id(plan: str) -> str:
    if plan in {"solo", "team"}:
        return (os.getenv(f"STRIPE_PRICE_ID_{plan.upper()}") or "").strip()
    if plan == "founding":
        return (os.getenv("STRIPE_PRICE_ID_FOUNDING") or "").strip()
    return (os.getenv("STRIPE_PRICE_ID_STANDARD") or os.getenv("STRIPE_PRICE_ID") or "").strip()


def connected() -> bool:
    return bool(_secret_key())


def public_catalog() -> list[dict[str, Any]]:
    """The same monthly offer is used by the public page and Account."""
    return [{**plan, "checkout_ready": connected() and bool(_price_id(code))}
            for code, plan in PLANS.items() if not plan.get("legacy")]


def provider_status() -> dict[str, Any]:
    if not connected():
        return {
            "provider": "stripe",
            "connected": False,
            "mode": "local",
            "detail": "Billing is not switched on for this account yet.",
        }
    key = _secret_key()
    return {
        "provider": "stripe",
        "connected": True,
        "mode": "test" if key.startswith("sk_test") else "live",
        "publishable_key": (os.getenv("STRIPE_PUBLISHABLE_KEY") or "").strip() or None,
        "detail": "Cards, invoices, and receipts are handled by Stripe.",
    }


def _request(path: str, data: dict[str, Any] | None = None, method: str = "POST") -> dict[str, Any]:
    key = _secret_key()
    if not key:
        raise RuntimeError(NOT_SET_UP)
    url = f"{STRIPE_API}{path}"
    body = None
    if data is not None:
        flat: list[tuple[str, str]] = []
        def encode(name: str, value: Any) -> None:
            if value is None:
                return
            if isinstance(value, dict):
                for inner_name, inner_value in value.items():
                    encode(f"{name}[{inner_name}]", inner_value)
            elif isinstance(value, list):
                for index, inner_value in enumerate(value):
                    encode(f"{name}[{index}]", inner_value)
            elif isinstance(value, bool):
                flat.append((name, "true" if value else "false"))
            else:
                flat.append((name, str(value)))
        for name, value in data.items():
            encode(name, value)
        body = urllib.parse.urlencode(flat).encode("utf-8")
    if method == "GET" and body:
        url = f"{url}?{body.decode('utf-8')}"
        body = None
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Authorization", f"Bearer {key}")
    request.add_header("Stripe-Version", os.getenv("STRIPE_API_VERSION", "2024-06-20"))
    if body:
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        try:
            message = json.loads(detail)["error"]["message"]
        except Exception:  # noqa: BLE001 - fall back to the raw body
            message = detail[:280]
        raise RuntimeError(f"Stripe rejected the request: {message}") from error
    except urllib.error.URLError as error:
        raise RuntimeError("Could not reach Stripe. Check the network connection") from error


# ---------------------------------------------------------------------------
# Local state
# ---------------------------------------------------------------------------

def _record_event(conn: sqlite3.Connection, organization_id: str, event_type: str, detail: dict[str, Any] | None = None) -> None:
    conn.execute(
        "INSERT INTO billing_events(id,organization_id,event_type,detail_json,created_at) VALUES(?,?,?,?,?)",
        (f"bev-{uuid.uuid4().hex}", organization_id, event_type, json.dumps(detail or {}, separators=(",", ":")), _utc_now()),
    )


def ensure_subscription(conn: sqlite3.Connection, organization_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM subscriptions WHERE organization_id=?", (organization_id,)).fetchone()
    if row is not None:
        return dict(row)
    trial_end = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat(timespec="seconds")
    # Existing workspaces without a billing row retain their previous offer.
    # New onboarding creates this row before its first location is seeded.
    existing = conn.execute("SELECT 1 FROM locations WHERE organization_id=? AND active=1 LIMIT 1", (organization_id,)).fetchone()
    initial_plan = "standard" if existing else "solo"
    conn.execute(
        """INSERT INTO subscriptions(
              organization_id,status,plan,seats,provider,current_period_end,trial_end,
              cancel_at_period_end,updated_at)
           VALUES(?,?,?,?,?,?,?,0,?)""",
        (organization_id, "trialing", initial_plan, 1, "stripe" if connected() else "local", trial_end, trial_end, _utc_now()),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM subscriptions WHERE organization_id=?", (organization_id,)).fetchone())


def _plan_view(record: dict[str, Any]) -> dict[str, Any]:
    plan = PLANS.get(record.get("plan") or "standard", PLANS["standard"])
    seats = int(record.get("seats") or 1)
    return {
        **plan,
        "seats": seats,
        "monthly_total": plan["monthly"] * seats,
        "annual_total": plan["annual_monthly"] * seats * 12 if plan.get("annual_monthly") else None,
    }


def location_allowance(conn: sqlite3.Connection, organization_id: str) -> dict[str, Any]:
    record = ensure_subscription(conn, organization_id)
    plan = PLANS.get(record["plan"], PLANS["standard"])
    used = conn.execute("SELECT COUNT(*) FROM locations WHERE organization_id=? AND active=1", (organization_id,)).fetchone()[0]
    limit = plan.get("max_locations")
    return {"plan": plan["code"], "used": used, "limit": limit,
            "remaining": max(0, limit - used) if limit is not None else None}


def require_location_capacity(conn: sqlite3.Connection, organization_id: str, additional: int = 1) -> None:
    """Call inside the location write transaction; existing locations stay readable."""
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    allowance = location_allowance(conn, organization_id)
    if allowance["limit"] is not None and allowance["used"] + additional > allowance["limit"]:
        raise ValueError(f"This plan includes {allowance['limit']} active location(s). Choose a larger plan in Account before adding another.")


def overview(conn: sqlite3.Connection, organization_id: str) -> dict[str, Any]:
    record = ensure_subscription(conn, organization_id)
    invoices: list[dict[str, Any]] = []
    if connected() and record.get("provider_customer_id"):
        try:
            payload = _request("/invoices", {"customer": record["provider_customer_id"], "limit": 6}, method="GET")
            for invoice in payload.get("data", []):
                invoices.append({
                    "number": invoice.get("number"),
                    "amount": (invoice.get("amount_paid") or 0) / 100,
                    "currency": (invoice.get("currency") or "usd").upper(),
                    "status": invoice.get("status"),
                    "date": datetime.fromtimestamp(invoice.get("created", 0), timezone.utc).date().isoformat(),
                    "url": invoice.get("hosted_invoice_url"),
                })
        except RuntimeError:
            invoices = []

    return {
        # A trial whose date has passed is reported as over. The row itself is
        # left alone: the processor's webhook is the only thing that moves it.
        "status": "trial_ended" if _trial_over(record) else record.get("status"),
        "plan": _plan_view(record),
        "plans": public_catalog(),
        "location_allowance": location_allowance(conn, organization_id),
        "has_subscription": bool(record.get("provider_subscription_id")),
        "can_select_trial_plan": not record.get("provider_subscription_id") and record.get("status") == "trialing" and not _trial_over(record),
        "checkout_ready": connected() and bool(_price_id(record["plan"])),
        "seats": int(record.get("seats") or 1),
        "current_period_end": record.get("current_period_end"),
        "trial_end": record.get("trial_end"),
        "cancel_at_period_end": bool(record.get("cancel_at_period_end")),
        "canceled_at": record.get("canceled_at"),
        "payment_method": (
            {"brand": record.get("payment_brand"), "last4": record.get("payment_last4"), "expires": record.get("payment_expires")}
            if record.get("payment_last4") else None
        ),
        "invoices": invoices,
        "provider": provider_status(),
        "cancellation_reasons": CANCELLATION_REASONS,
        "support_email": SUPPORT_EMAIL,
    }


# ---------------------------------------------------------------------------
# Stripe flows
# ---------------------------------------------------------------------------

def _ensure_customer(conn: sqlite3.Connection, organization_id: str, email: str, name: str) -> str:
    record = ensure_subscription(conn, organization_id)
    if record.get("provider_customer_id"):
        return str(record["provider_customer_id"])
    customer = _request("/customers", {"email": email, "name": name, "metadata": {"organization_id": organization_id}})
    conn.execute(
        "UPDATE subscriptions SET provider_customer_id=?,updated_at=? WHERE organization_id=?",
        (customer["id"], _utc_now(), organization_id),
    )
    conn.commit()
    return str(customer["id"])


def start_checkout(conn: sqlite3.Connection, organization_id: str, email: str, name: str, plan: str, return_url: str) -> dict[str, Any]:
    if plan not in PLANS:
        raise ValueError("Choose one of the listed plans")
    record = ensure_subscription(conn, organization_id)
    if PLANS[plan].get("legacy") and record.get("plan") != plan:
        raise ValueError("That plan is only available to accounts already using it")
    if record.get("provider_subscription_id") and record.get("status") not in {"canceled", "incomplete_expired"}:
        raise ValueError("Use Manage billing to change your existing subscription")
    _check_plan_capacity(conn, organization_id, plan)
    if not connected():
        raise RuntimeError(NOT_SET_UP)
    price = _price_id(plan)
    if not price:
        raise RuntimeError(NOT_SET_UP)
    configured = _request(f"/prices/{price}", method="GET")
    recurring = configured.get("recurring") or {}
    if (configured.get("active") is not True or configured.get("currency") != "usd"
            or configured.get("unit_amount") != PLANS[plan]["monthly"] * 100
            or recurring.get("interval") != "month" or recurring.get("interval_count", 1) != 1):
        raise RuntimeError("This plan's payment setup does not match its listed monthly price. Contact support.")
    customer = _ensure_customer(conn, organization_id, email, name)
    session = _request("/checkout/sessions", {
        "mode": "subscription",
        "customer": customer,
        "line_items": [{"price": price, "quantity": 1}],
        "success_url": f"{return_url}?billing=done",
        "cancel_url": f"{return_url}?billing=cancelled",
        "allow_promotion_codes": True,
        "subscription_data": {"metadata": {"organization_id": organization_id, "plan": plan}},
    })
    _record_event(conn, organization_id, "checkout_started", {"plan": plan})
    conn.commit()
    return {"url": session.get("url"), "pending": True, "plan": plan}


def payment_portal(conn: sqlite3.Connection, organization_id: str, email: str, name: str, return_url: str) -> dict[str, Any]:
    """Stripe's hosted portal handles card changes, invoices, and receipts."""
    if not connected():
        raise RuntimeError(NOT_SET_UP)
    customer = _ensure_customer(conn, organization_id, email, name)
    session = _request("/billing_portal/sessions", {"customer": customer, "return_url": return_url})
    _record_event(conn, organization_id, "portal_opened", {})
    conn.commit()
    return {"url": session.get("url")}


def record_cancellation_intent(
    conn: sqlite3.Connection,
    organization_id: str,
    user_id: str,
    reason_code: str,
    reason_detail: str,
    wants_contact: bool,
) -> dict[str, Any]:
    """Save why before anything is cancelled, so the answer is never lost."""
    valid = {row["code"] for row in CANCELLATION_REASONS}
    if reason_code not in valid:
        raise ValueError("Choose a reason so we know what went wrong")
    conn.execute(
        """INSERT INTO cancellation_feedback(id,organization_id,user_id,reason_code,reason_detail,wants_contact,created_at)
           VALUES(?,?,?,?,?,?,?)""",
        (f"cxf-{uuid.uuid4().hex}", organization_id, user_id, reason_code, reason_detail.strip()[:2000], int(wants_contact), _utc_now()),
    )
    _record_event(conn, organization_id, "cancellation_reason_recorded", {"reason": reason_code, "wants_contact": wants_contact})
    conn.commit()
    reason = next(row for row in CANCELLATION_REASONS if row["code"] == reason_code)
    offer = {
        "accuracy": "Your accuracy record can be pulled day by day to show exactly where the calls missed.",
        "price": "If the price is the problem, say so on the call and it will be looked at.",
        "unused": "Fifteen minutes with someone who sets these up is usually enough to tell whether it can do what you need.",
        "switched": "It helps to know what the other product does better.",
        "closing": "Your history can be exported so it goes with you.",
        "other": "A short call often turns up a fix.",
    }.get(reason_code, "A short call often turns up a fix.")
    return {"recorded": True, "reason": reason, "offer": offer, "support_email": SUPPORT_EMAIL}


def cancel_plan(conn: sqlite3.Connection, organization_id: str, immediate: bool = False) -> dict[str, Any]:
    """Cancel at the end of the paid period. Access continues until then."""
    record = ensure_subscription(conn, organization_id)
    subscription_id = record.get("provider_subscription_id")
    if connected() and subscription_id:
        if immediate:
            _request(f"/subscriptions/{subscription_id}", method="DELETE")
        else:
            _request(f"/subscriptions/{subscription_id}", {"cancel_at_period_end": True})
    status = "canceled" if immediate else record.get("status") or "active"
    conn.execute(
        """UPDATE subscriptions SET cancel_at_period_end=?,canceled_at=?,status=?,updated_at=?
           WHERE organization_id=?""",
        (0 if immediate else 1, _utc_now(), status, _utc_now(), organization_id),
    )
    _record_event(conn, organization_id, "subscription_canceled", {"immediate": immediate})
    conn.commit()
    updated = ensure_subscription(conn, organization_id)
    return {
        "cancelled": True,
        "immediate": immediate,
        "access_until": updated.get("current_period_end"),
        "message": (
            "Your plan is cancelled and access has ended."
            if immediate else
            f"Your plan will end on {(updated.get('current_period_end') or '')[:10]}. Everything keeps working until then, and you can turn it back on any time before that date."
        ),
    }


def resume_plan(conn: sqlite3.Connection, organization_id: str) -> dict[str, Any]:
    record = ensure_subscription(conn, organization_id)
    subscription_id = record.get("provider_subscription_id")
    if connected() and subscription_id:
        _request(f"/subscriptions/{subscription_id}", {"cancel_at_period_end": False})
    conn.execute(
        "UPDATE subscriptions SET cancel_at_period_end=0,canceled_at=NULL,status='active',updated_at=? WHERE organization_id=?",
        (_utc_now(), organization_id),
    )
    _record_event(conn, organization_id, "subscription_resumed", {})
    conn.commit()
    return {"resumed": True, "message": "Your plan is active again. Nothing was interrupted."}


def _check_plan_capacity(conn: sqlite3.Connection, organization_id: str, plan: str) -> None:
    limit = PLANS[plan].get("max_locations")
    used = conn.execute("SELECT COUNT(*) FROM locations WHERE organization_id=? AND active=1", (organization_id,)).fetchone()[0]
    if limit is not None and used > limit:
        raise ValueError(f"You have {used} active locations. This plan includes {limit}.")


def change_plan(conn: sqlite3.Connection, organization_id: str, plan: str) -> dict[str, Any]:
    if plan not in PLANS:
        raise ValueError("Unknown plan")
    record = ensure_subscription(conn, organization_id)
    if PLANS[plan].get("legacy") and record.get("plan") != plan:
        raise ValueError("That plan is only available to accounts already using it")
    if record.get("provider_subscription_id") or record.get("status") != "trialing" or _trial_over(record):
        raise ValueError("Choose a paid plan through checkout or Manage billing")
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    _check_plan_capacity(conn, organization_id, plan)
    conn.execute(
        "UPDATE subscriptions SET plan=?,updated_at=? WHERE organization_id=?",
        (plan, _utc_now(), organization_id),
    )
    _record_event(conn, organization_id, "plan_changed", {"plan": plan})
    conn.commit()
    return {"plan": _plan_view(ensure_subscription(conn, organization_id)), "status": "trialing", "message": "Trial plan changed. No payment was taken."}


def verify_webhook_signature(raw: bytes, signature_header: str | None, tolerance_seconds: int = 300) -> None:
    """Prove the webhook really came from Stripe before acting on it.

    Anyone can POST to a public webhook URL. Without this check a stranger could
    mark any subscription paid, cancelled, or past due.
    """
    import hashlib
    import hmac as hmac_module

    secret = (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()
    if not secret:
        raise PermissionError("Payment webhooks are not switched on for this server")
    if not signature_header:
        raise PermissionError("This request carries no Stripe signature")

    parts = dict(
        piece.split("=", 1) for piece in signature_header.split(",") if "=" in piece
    )
    timestamp = parts.get("t", "")
    signatures = [value for key, value in
                  (piece.split("=", 1) for piece in signature_header.split(",") if "=" in piece)
                  if key == "v1"]
    if not timestamp or not signatures:
        raise PermissionError("That Stripe signature is malformed")
    try:
        age = abs(int(datetime.now(timezone.utc).timestamp()) - int(timestamp))
    except ValueError as exc:
        raise PermissionError("That Stripe signature is malformed") from exc
    if age > tolerance_seconds:
        raise PermissionError("That Stripe webhook is too old to accept")

    expected = hmac_module.new(
        secret.encode("utf-8"),
        f"{timestamp}.".encode("utf-8") + raw,
        hashlib.sha256,
    ).hexdigest()
    if not any(hmac_module.compare_digest(expected, candidate) for candidate in signatures):
        raise PermissionError("That Stripe signature does not match")


def apply_stripe_event(conn: sqlite3.Connection, event: dict[str, Any]) -> dict[str, Any]:
    """Keep local state in step with Stripe. Stripe is the source of truth."""
    kind = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}
    customer_id = obj.get("customer")
    if not customer_id:
        return {"ignored": kind}
    row = conn.execute("SELECT organization_id FROM subscriptions WHERE provider_customer_id=?", (customer_id,)).fetchone()
    if row is None:
        return {"ignored": "unknown customer"}
    organization_id = row["organization_id"]

    if kind.startswith("customer.subscription"):
        period_end = obj.get("current_period_end")
        items = (obj.get("items") or {}).get("data") or []
        price = (items[0].get("price") or {}).get("id") if items else None
        matched_plan = next((code for code in PLANS if _price_id(code) and _price_id(code) == price), None)
        conn.execute(
            """UPDATE subscriptions SET provider_subscription_id=?,status=?,cancel_at_period_end=?,
                   current_period_end=?,updated_at=? WHERE organization_id=?""",
            (
                obj.get("id"), obj.get("status") or "active", int(bool(obj.get("cancel_at_period_end"))),
                datetime.fromtimestamp(period_end, timezone.utc).isoformat(timespec="seconds") if period_end else None,
                _utc_now(), organization_id,
            ),
        )
        if matched_plan:
            conn.execute("UPDATE subscriptions SET plan=? WHERE organization_id=?", (matched_plan, organization_id))
    elif kind == "payment_method.attached":
        card = obj.get("card") or {}
        conn.execute(
            "UPDATE subscriptions SET payment_brand=?,payment_last4=?,payment_expires=?,updated_at=? WHERE organization_id=?",
            (card.get("brand"), card.get("last4"), f"{card.get('exp_month')}/{card.get('exp_year')}", _utc_now(), organization_id),
        )
    _record_event(conn, organization_id, f"stripe:{kind}", {"id": obj.get("id")})
    conn.commit()
    return {"applied": kind}
