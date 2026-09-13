from __future__ import annotations

import html
import json
import os
import smtplib
import sqlite3
import ssl
import uuid
from datetime import date, datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .localtime import zone
from .intelligence import daily_brief, utc_now

BRAND_INK = "#17201c"
BRAND_PAPER = "#f5f2ea"
BRAND_GREEN = "#1f5b46"
BRAND_OCHRE = "#c7802d"


def _money(value: float) -> str:
    return f"${value:,.0f}"


def _change(value: int | float) -> str:
    return f"{value:+.0f}%"


def _email_subject(brief: dict[str, Any]) -> str:
    summary = brief["summary"]
    weekday = datetime.fromisoformat(brief['date']).strftime('%A')
    if brief.get("no_history"):
        return f"Quantify: nothing to plan for {weekday} yet"
    movers = brief.get("top_surges") or brief.get("top_volume") or []
    lead = movers[0]["name"] if movers else "today's menu"
    weekday = datetime.fromisoformat(brief['date']).strftime('%A')
    change = int(summary.get('revenue_change_percent') or 0)
    if abs(change) < 4:
        return f"Quantify: a normal {weekday}, {_money(summary['expected_revenue'])} expected"
    word = "busier" if change > 0 else "quieter"
    return f"Quantify: {weekday} looks {abs(change)}% {word}, {_money(summary['expected_revenue'])} expected"


def _empty_message(brief: dict[str, Any]) -> str:
    return (f"{brief['location']['name']} has no sales recorded yet. Connect the register "
            "or add your menu and the first plan appears the next morning.")


def render_brief_html(brief: dict[str, Any]) -> str:
    location = brief["location"]
    summary = brief["summary"]
    generated = datetime.fromisoformat(brief["generated_at"]).astimezone(zone(location["timezone"]))
    sent = generated.strftime("%I:%M %p %Z").lstrip("0")
    text_style = "font:14px/1.5 Arial,sans-serif;color:#35433b"
    if brief.get("no_history"):
        content = f'<h1 style="font:600 24px Arial,sans-serif">Nothing to plan yet</h1><p>{html.escape(_empty_message(brief))}</p>'
    else:
        rows = []
        for item in brief.get("top_volume", [])[:10]:
            values = [html.escape(item["name"]), str(item["make"]), str(item["expected"]), str(item["baseline"])]
            cells = ''.join(f'<td style="padding:12px 8px;border-top:1px solid #d7dcd9;text-align:{"left" if i == 0 else "right"}">{value}</td>' for i,value in enumerate(values))
            rows.append(f'<tr>{cells}</tr>')
        heads = ''.join(f'<th style="padding:8px;text-align:{"left" if i == 0 else "right"};font-weight:500">{label}</th>' for i,label in enumerate(["Item", "Make", "Expected", "Normal"]))
        reasons = ''.join(f'<p><b>{html.escape(row["label"])}</b><br>{html.escape(row.get("detail", ""))}</p>' for row in brief["context"]["signals"][:4])
        actions = ''.join(f'<p><b>{html.escape(row["title"])}</b><br>{html.escape(row["detail"])}</p>' for row in brief.get("priorities", [])[:2])
        content = f"""<h1 style="font:600 24px/1.3 Arial,sans-serif;margin:16px 0">{html.escape(brief['headline'])}</h1>
<p><b>Expected sales {_money(summary['expected_revenue'])}</b><br>{_money(brief['comparison']['sales'])} on {html.escape(brief['comparison']['label'])}.</p>
<p><b>Make {summary['make_units']} items</b><br>{summary['expected_units']} expected to sell.</p>
<p><b>Busiest hour {html.escape(summary.get('peak_hour') or 'Not available')}</b><br>{summary.get('peak_units',0)} items, about {summary.get('peak_share_percent',0)}% of the day.</p>
<h2 style="font:600 18px Arial,sans-serif;margin-top:24px">What matters</h2>{actions or '<p>The make list is close to normal.</p>'}
<h2 style="font:600 18px Arial,sans-serif;margin-top:24px">What to make</h2><table width="100%" cellpadding="0" cellspacing="0" style="font:14px/1.5 Arial,sans-serif"><thead><tr>{heads}</tr></thead><tbody>{''.join(rows)}</tbody></table>
<h2 style="font:600 18px Arial,sans-serif;margin-top:24px">Why</h2>{reasons or '<p>No clear change from normal.</p>'}"""
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{html.escape(_email_subject(brief))}</title></head>
<body style="margin:0;background:#f6f7f6;{text_style}"><table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:24px 12px"><table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:640px;background:white;border:1px solid #d7dcd9"><tr><td style="padding:24px;{text_style}"><div style="font-weight:600;color:{BRAND_INK}">QUANTIFY</div><p>Morning email<br>{html.escape(location['name'])}<br>{html.escape(brief['date_label'])}</p>{content}<p style="margin-top:24px;padding-top:16px;border-top:1px solid #d7dcd9;font-size:12px">Sent {html.escape(sent)}. Register data runs through {html.escape(brief['data_health']['latest_sale_date'] or 'no sales yet')}. Change the time or turn this off in Settings.</p></td></tr></table></td></tr></table></body></html>"""


def render_brief_text(brief: dict[str, Any]) -> str:
    if brief.get("no_history"):
        return "Quantify morning email\n\nNothing to plan yet\n" + _empty_message(brief)
    summary = brief["summary"]
    lines = ["Quantify morning email", f"{brief['location']['name']} | {brief['date_label']}", "", brief["headline"],
             f"Expected sales: {_money(summary['expected_revenue'])}; {_money(brief['comparison']['sales'])} on {brief['comparison']['label']}.",
             f"Make {summary['make_units']} items; {summary['expected_units']} expected to sell.",
             f"Busiest hour: {summary.get('peak_hour') or 'Not available'}; {summary.get('peak_units',0)} items.", "", "What matters"]
    lines.extend(f"- {row['title']}: {row['detail']}" for row in brief.get("priorities", [])[:2])
    lines.extend(["", "What to make"])
    for item in brief.get("top_volume", [])[:10]:
        lines.append(f"- {item['name']}: make {item['make']}, expected {item['expected']}, normal {item['baseline']}.")
    lines.extend(["", "Why"])
    lines.extend(f"- {row['label']}: {row.get('detail', '')}" for row in brief["context"]["signals"][:4])
    lines.extend(["", f"Register data runs through {brief['data_health']['latest_sale_date'] or 'no sales yet'}.", "Change the time or turn this off in Settings."])
    return "\n".join(lines)


def build_email(conn: sqlite3.Connection, location_id: str, target_date: date) -> dict[str, Any]:
    brief = daily_brief(conn, location_id, target_date, week_days=7)
    return {
        "subject": _email_subject(brief),
        "html": render_brief_html(brief),
        "text": render_brief_text(brief),
        "brief": brief,
    }


def update_preferences(
    conn: sqlite3.Connection,
    location_id: str,
    owner_email: str,
    enabled: bool,
    send_time: str,
    timezone_name: str,
    include_week_ahead: bool = True,
) -> dict[str, Any]:
    owner_email = owner_email.strip().lower()
    if (enabled or owner_email) and ("@" not in owner_email or len(owner_email) > 254):
        raise ValueError("Enter a valid owner email address")
    try:
        hour, minute = [int(part) for part in send_time.split(":", 1)]
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except (ValueError, AttributeError):
        raise ValueError("Send time must use HH:MM") from None
    # The zone is the location's own and was checked where it was set. It is
    # kept on the row for the record; the scheduler reads the location's.
    timezone_name = (timezone_name or "").strip() or "UTC"
    now = utc_now()
    conn.execute(
        """INSERT INTO email_preferences(location_id,owner_email,enabled,send_time,timezone,include_week_ahead,updated_at)
           VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(location_id) DO UPDATE SET owner_email=excluded.owner_email,enabled=excluded.enabled,
             send_time=excluded.send_time,timezone=excluded.timezone,include_week_ahead=excluded.include_week_ahead,updated_at=excluded.updated_at""",
        (location_id, owner_email, int(enabled), send_time, timezone_name, int(include_week_ahead), now),
    )
    conn.commit()
    return preferences(conn, location_id)


def preferences(conn: sqlite3.Connection, location_id: str) -> dict[str, Any]:
    """The morning email settings for one location. A read never writes.

    With no row yet the defaults come back with `configured` False, an empty
    address and sending off, so nothing invented ever shows up in Settings or
    gets mailed to.
    """
    row = conn.execute("SELECT * FROM email_preferences WHERE location_id=?", (location_id,)).fetchone()
    if row is not None:
        return dict(row) | {"configured": True}
    location = conn.execute("SELECT timezone FROM locations WHERE id=?", (location_id,)).fetchone()
    if location is None:
        raise ValueError("That location is not on this account")
    return {
        "location_id": location_id,
        "owner_email": "",
        "enabled": 0,
        "send_time": "05:30",
        "timezone": location["timezone"],
        "include_week_ahead": 1,
        "last_sent_date": None,
        "updated_at": None,
        "configured": False,
    }


def _record_delivery(
    conn: sqlite3.Connection,
    location_id: str,
    recipient: str,
    target_date: date,
    subject: str,
    status: str,
    provider: str,
    artifact_path: str | None = None,
    provider_message_id: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    delivery_id = f"mail-{uuid.uuid4().hex}"
    created = utc_now()
    conn.execute(
        """INSERT INTO email_deliveries(id,location_id,recipient,forecast_date,subject,status,provider,
           artifact_path,provider_message_id,error,created_at,sent_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (delivery_id, location_id, recipient, target_date.isoformat(), subject, status, provider,
         artifact_path, provider_message_id, error, created, created if status in {"sent", "outbox"} else None),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM email_deliveries WHERE id=?", (delivery_id,)).fetchone())


def _send_postmark(recipient: str, subject: str, text: str, html_body: str) -> str:
    token = os.getenv("POSTMARK_SERVER_TOKEN")
    sender = os.getenv("QUANTIFY_FROM_EMAIL")
    if not token or not sender:
        raise RuntimeError("POSTMARK_SERVER_TOKEN and QUANTIFY_FROM_EMAIL are required")
    payload = json.dumps({"From": sender, "To": recipient, "Subject": subject, "TextBody": text, "HtmlBody": html_body, "MessageStream": "outbound"}).encode("utf-8")
    request = Request("https://api.postmarkapp.com/email", data=payload, method="POST", headers={"Accept": "application/json", "Content-Type": "application/json", "X-Postmark-Server-Token": token})
    try:
        with urlopen(request, timeout=20) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError) as exc:
        detail = exc.read().decode("utf-8", "ignore") if isinstance(exc, HTTPError) else str(exc)
        raise RuntimeError(f"Postmark delivery failed: {detail[:300]}") from exc
    return str(body.get("MessageID") or "postmark")


def _send_smtp(recipient: str, subject: str, text: str, html_body: str) -> str:
    host = os.getenv("SMTP_HOST")
    sender = os.getenv("QUANTIFY_FROM_EMAIL") or os.getenv("SMTP_FROM")
    if not host or not sender:
        raise RuntimeError("SMTP_HOST and QUANTIFY_FROM_EMAIL are required")
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(text)
    message.add_alternative(html_body, subtype="html")
    context = ssl.create_default_context()
    if os.getenv("SMTP_SSL", "0") == "1":
        with smtplib.SMTP_SSL(host, port, timeout=20, context=context) as client:
            if username:
                client.login(username, password or "")
            client.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=20) as client:
            client.ehlo()
            if os.getenv("SMTP_STARTTLS", "1") == "1":
                client.starttls(context=context)
                client.ehlo()
            if username:
                client.login(username, password or "")
            client.send_message(message)
    return str(message.get("Message-ID") or "smtp")


def _write_outbox(root: Path, recipient: str, subject: str, text: str, html_body: str, target_date: date) -> str:
    outbox = root / "data" / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    message = EmailMessage()
    message["From"] = os.getenv("QUANTIFY_FROM_EMAIL", "briefs@quantify.local")
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(text)
    message.add_alternative(html_body, subtype="html")
    filename = f"quantify-{target_date.isoformat()}-{uuid.uuid4().hex[:8]}.eml"
    path = outbox / filename
    path.write_bytes(bytes(message))
    return str(path)


# Addresses that must never be sent to, whatever the rest of the system decides.
# The list itself lives in the environment: QUANTIFY_EMAIL_BLOCKLIST, comma
# separated, set in the local config. Nothing personal is kept in the source.
NEVER_SEND_TO: set[str] = set()


def blocked_recipients() -> set[str]:
    extra = (os.getenv("QUANTIFY_EMAIL_BLOCKLIST") or "").split(",")
    return NEVER_SEND_TO | {value.strip().lower() for value in extra if value.strip()}


def is_blocked(recipient: str) -> bool:
    return (recipient or "").strip().lower() in blocked_recipients()


def mail_provider() -> str:
    """Which transport a message would go out on right now."""
    if os.getenv("POSTMARK_SERVER_TOKEN") and os.getenv("QUANTIFY_FROM_EMAIL"):
        return "postmark"
    if os.getenv("SMTP_HOST") and (os.getenv("QUANTIFY_FROM_EMAIL") or os.getenv("SMTP_FROM")):
        return "smtp"
    return "outbox"


def send_transactional(root: Path, recipient: str, subject: str, text: str, html_body: str) -> dict[str, Any]:
    """Send one message, falling back to a file on disk when nothing is configured.

    Every outbound message in the product passes through here, so the blocklist
    is checked once, in the one place it cannot be routed around.
    """
    if is_blocked(recipient):
        return {"provider": "blocked", "delivered": False, "error": "This address is on the never-send list"}
    provider = mail_provider()
    try:
        if provider == "postmark":
            return {"provider": "postmark", "delivered": True, "id": _send_postmark(recipient, subject, text, html_body)}
        if provider == "smtp":
            return {"provider": "smtp", "delivered": True, "id": _send_smtp(recipient, subject, text, html_body)}
    except RuntimeError as exc:
        path = _write_outbox(root, recipient, subject, text, html_body, date.today())
        return {"provider": "outbox", "delivered": False, "path": path, "error": str(exc)}
    path = _write_outbox(root, recipient, subject, text, html_body, date.today())
    return {"provider": "outbox", "delivered": False, "path": path}


def send_verification_code(root: Path, recipient: str, code: str, minutes: int = 20) -> dict[str, Any]:
    subject = f"{code} is your Quantify confirmation code"
    text = (
        f"Your Quantify confirmation code is {code}\n\n"
        f"Type it into the tab you left open. It stops working in {minutes} minutes.\n\n"
        "If you did not start creating a Quantify account, you can ignore this. "
        "Nobody can get in with this code alone.\n"
    )
    html_body = f"""<!doctype html><html><body style="margin:0;background:#f7f7f5;padding:32px 16px">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center">
<table role="presentation" width="440" cellspacing="0" cellpadding="0" style="width:440px;max-width:100%;background:#ffffff;border:1px solid #e7e6e2;border-radius:14px">
<tr><td style="padding:26px 28px 6px;font:600 13px Arial,sans-serif;letter-spacing:3px;color:#15171a">QUANTIFY</td></tr>
<tr><td style="padding:8px 28px 0;font:600 20px Arial,sans-serif;color:#15171a">Confirm your email</td></tr>
<tr><td style="padding:10px 28px 0;font:14px/1.6 Arial,sans-serif;color:#4b5058">
Type this code into the tab you left open.</td></tr>
<tr><td style="padding:20px 28px 4px">
<div style="font:700 34px/1 'Courier New',monospace;letter-spacing:10px;color:#15171a;background:#f2f2ef;border:1px solid #e7e6e2;border-radius:10px;padding:18px 0;text-align:center">{html.escape(code)}</div>
</td></tr>
<tr><td style="padding:14px 28px 26px;font:12px/1.6 Arial,sans-serif;color:#797f88">
It stops working in {minutes} minutes. If you did not start creating a Quantify account you can ignore this, and nobody can get in with this code alone.</td></tr>
</table></td></tr></table></body></html>"""
    return send_transactional(root, recipient, subject, text, html_body)


def send_password_reset_code(root: Path, recipient: str, code: str, minutes: int = 20) -> dict[str, Any]:
    subject = f"{code} is your Quantify password reset code"
    text = (
        f"Your Quantify password reset code is {code}\n\n"
        f"Type it into the tab you left open and choose a new password. It stops working in {minutes} minutes.\n\n"
        "If you did not ask to reset your password, you can ignore this. "
        "Nobody can get in with this code alone.\n"
    )
    html_body = f"""<!doctype html><html><body style="margin:0;background:#f7f7f5;padding:32px 16px">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center">
<table role="presentation" width="440" cellspacing="0" cellpadding="0" style="width:440px;max-width:100%;background:#ffffff;border:1px solid #e7e6e2;border-radius:14px">
<tr><td style="padding:26px 28px 6px;font:600 13px Arial,sans-serif;letter-spacing:3px;color:#15171a">QUANTIFY</td></tr>
<tr><td style="padding:8px 28px 0;font:600 20px Arial,sans-serif;color:#15171a">Reset your password</td></tr>
<tr><td style="padding:10px 28px 0;font:14px/1.6 Arial,sans-serif;color:#4b5058">
Type this code into the tab you left open, then choose a new password.</td></tr>
<tr><td style="padding:20px 28px 4px">
<div style="font:700 34px/1 'Courier New',monospace;letter-spacing:10px;color:#15171a;background:#f2f2ef;border:1px solid #e7e6e2;border-radius:10px;padding:18px 0;text-align:center">{html.escape(code)}</div>
</td></tr>
<tr><td style="padding:14px 28px 26px;font:12px/1.6 Arial,sans-serif;color:#797f88">
It stops working in {minutes} minutes. If you did not ask to reset your password you can ignore this, and nobody can get in with this code alone.</td></tr>
</table></td></tr></table></body></html>"""
    return send_transactional(root, recipient, subject, text, html_body)


def deliver_brief(
    conn: sqlite3.Connection,
    root: Path,
    location_id: str,
    target_date: date,
    recipient: str | None = None,
) -> dict[str, Any]:
    pref = preferences(conn, location_id)
    to = (recipient or pref["owner_email"] or "").strip().lower()
    if not to:
        raise ValueError("Add an address for the morning email in Settings first")
    if is_blocked(to):
        return {"status": "blocked", "recipient": to,
                "message": "That address cannot receive mail from Quantify, so nothing was sent"}
    built = build_email(conn, location_id, target_date)
    # One decision, made the same way everywhere a message goes out. A token
    # with no sender address is "not set up", never a send that fails.
    provider = mail_provider()
    try:
        if provider == "postmark":
            message_id = _send_postmark(to, built["subject"], built["text"], built["html"])
            result = _record_delivery(conn, location_id, to, target_date, built["subject"], "sent", "postmark", provider_message_id=message_id)
        elif provider == "smtp":
            message_id = _send_smtp(to, built["subject"], built["text"], built["html"])
            result = _record_delivery(conn, location_id, to, target_date, built["subject"], "sent", "smtp", provider_message_id=message_id)
        else:
            path = _write_outbox(root, to, built["subject"], built["text"], built["html"], target_date)
            result = _record_delivery(conn, location_id, to, target_date, built["subject"], "outbox", "local-eml", artifact_path=path)
        if pref.get("configured"):
            conn.execute("UPDATE email_preferences SET last_sent_date=?,updated_at=? WHERE location_id=?", (target_date.isoformat(), utc_now(), location_id))
        conn.commit()
        return result | {"preview": {"subject": built["subject"], "brief": built["brief"]}}
    except Exception as exc:
        _record_delivery(conn, location_id, to, target_date, built["subject"], "failed", provider, error=str(exc))
        raise RuntimeError("The morning email could not be sent. Try again in a moment") from exc


# How long after its send time a morning email is still worth sending. A
# server started at two in the afternoon must not mail a morning plan then.
SEND_WINDOW_HOURS = 3


def send_due_briefs(conn: sqlite3.Connection, root: Path, now_utc: datetime | None = None) -> list[dict[str, Any]]:
    """Send every morning email that is due. One location failing never stops the next.

    The clock is the location's own. The email row keeps a copy of the zone
    from when it was saved, but the location is where the zone is edited, so
    that is the one that counts.
    """
    now_utc = now_utc or datetime.now(tz=timezone.utc)
    rows = conn.execute(
        """SELECT p.*, l.timezone AS location_timezone FROM email_preferences p
           JOIN locations l ON l.id=p.location_id
           WHERE p.enabled=1 AND l.active=1 AND p.owner_email<>''"""
    ).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        try:
            local_now = now_utc.astimezone(zone(row["location_timezone"] or row["timezone"]))
            local_date = local_now.date()
            send_hour, send_minute = [int(part) for part in row["send_time"].split(":", 1)]
            minutes_past = (local_now.hour * 60 + local_now.minute) - (send_hour * 60 + send_minute)
            if minutes_past < 0 or row["last_sent_date"] == local_date.isoformat():
                continue
            if minutes_past > SEND_WINDOW_HOURS * 60:
                # Missed the window. Mark the day so it is not retried all afternoon.
                conn.execute(
                    "UPDATE email_preferences SET last_sent_date=? WHERE location_id=?",
                    (local_date.isoformat(), row["location_id"]),
                )
                conn.commit()
                results.append({"location_id": row["location_id"], "status": "skipped",
                                "message": "Past the send window for today"})
                continue
            results.append(deliver_brief(conn, root, row["location_id"], local_date))
        except Exception as exc:  # noqa: BLE001 - one location's failure is recorded, not spread
            results.append({"location_id": row["location_id"], "status": "failed", "error": str(exc)})
    return results
