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

from .localtime import is_known, zone
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
    movers = brief.get("top_surges") or brief.get("top_volume") or []
    lead = movers[0]["name"] if movers else "today's menu"
    weekday = datetime.fromisoformat(brief['date']).strftime('%A')
    change = int(summary.get('revenue_change_percent') or 0)
    if abs(change) < 4:
        return f"Quantify: a normal {weekday}, plan for {_money(summary['expected_revenue'])}"
    word = "busier" if change > 0 else "quieter"
    return f"Quantify: {weekday} runs {abs(change)}% {word} than normal, plan for {_money(summary['expected_revenue'])}"


def _priority_rows(brief: dict[str, Any]) -> str:
    priorities = brief.get("priorities", [])
    if not priorities:
        return '<tr><td style="padding:14px 0;border-top:1px solid #ded9cc;color:#55615b">Nothing unusual is expected. Run the normal plan and watch live sales.</td></tr>'
    rows = []
    for priority in priorities[:4]:
        rows.append(
            '<tr><td style="padding:14px 0;border-top:1px solid #ded9cc">'
            f'<table role="presentation" width="100%"><tr><td style="font:700 15px Arial,sans-serif;color:{BRAND_INK}">{html.escape(priority["title"])}</td>'
            f'<td align="right" style="font:700 14px Arial,sans-serif;color:{BRAND_GREEN};white-space:nowrap">{html.escape(str(priority.get("metric", "")))}</td></tr></table>'
            f'<div style="font:14px/1.5 Arial,sans-serif;color:#55615b;margin-top:4px">{html.escape(priority["detail"])}</div>'
            '</td></tr>'
        )
    return "".join(rows)


def _item_rows(items: list[dict[str, Any]]) -> str:
    rows = []
    for item in items[:10]:
        delta = int(item.get("vs_baseline_percent", 0))
        delta_text = f"{delta:+d}%" if delta else "normal"
        delta_color = BRAND_GREEN if delta >= 0 else "#7b4c3d"
        rows.append(
            '<tr>'
            f'<td style="padding:12px 8px 12px 0;border-top:1px solid #e4dfd3;font:600 14px Arial,sans-serif;color:{BRAND_INK}">{html.escape(item["name"])}</td>'
            f'<td align="right" style="padding:12px 8px;border-top:1px solid #e4dfd3;font:700 14px Arial,sans-serif;color:{BRAND_INK}">{item["expected"]}</td>'
            f'<td align="right" style="padding:12px 8px;border-top:1px solid #e4dfd3;font:13px Arial,sans-serif;color:#55615b">{item["lower"]} to {item["upper"]}</td>'
            f'<td align="right" style="padding:12px 0 12px 8px;border-top:1px solid #e4dfd3;font:700 13px Arial,sans-serif;color:{delta_color}">{delta_text}</td>'
            '</tr>'
        )
    return "".join(rows)


def _signal_rows(signals: list[dict[str, Any]]) -> str:
    if not signals:
        return '<div style="font:14px/1.5 Arial,sans-serif;color:#55615b">Nothing outside the restaurant is pushing today either way.</div>'
    blocks = []
    for signal in signals[:5]:
        effect = signal.get("effect", 0)
        blocks.append(
            f'<div style="padding:10px 0;border-top:1px solid #ded9cc"><span style="font:700 13px Arial,sans-serif;color:{BRAND_INK}">{html.escape(signal["label"])}</span>'
            f'<span style="float:right;font:700 13px Arial,sans-serif;color:{BRAND_GREEN}">{_change(effect)}</span>'
            f'<div style="clear:both;font:13px/1.45 Arial,sans-serif;color:#59645e;margin-top:3px">{html.escape(signal.get("detail", ""))}</div></div>'
        )
    return "".join(blocks)


def _week_rows(week: list[dict[str, Any]]) -> str:
    rows = []
    for day in week[:6]:
        label = datetime.fromisoformat(day["date"]).strftime("%a %b %-d") if os.name != "nt" else datetime.fromisoformat(day["date"]).strftime("%a %b %#d")
        rows.append(
            '<tr>'
            f'<td style="padding:10px 6px 10px 0;border-top:1px solid #e4dfd3;font:600 13px Arial,sans-serif;color:{BRAND_INK}">{html.escape(label)}</td>'
            f'<td align="right" style="padding:10px 6px;border-top:1px solid #e4dfd3;font:13px Arial,sans-serif;color:{BRAND_INK}">{_money(day["expected_revenue"])}</td>'
            f'<td align="right" style="padding:10px 0 10px 6px;border-top:1px solid #e4dfd3;font:700 13px Arial,sans-serif;color:{BRAND_GREEN}">{_change(day["change_percent"])}</td>'
            '</tr>'
        )
    return "".join(rows)


def render_brief_html(brief: dict[str, Any]) -> str:
    location = brief["location"]
    summary = brief["summary"]
    weather = brief["context"]["weather"]
    generated = datetime.fromisoformat(brief["generated_at"]).strftime("%I:%M %p UTC").lstrip("0")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{html.escape(_email_subject(brief))}</title></head>
<body style="margin:0;background:{BRAND_PAPER};color:{BRAND_INK};font-family:Arial,sans-serif">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:{BRAND_PAPER}"><tr><td align="center" style="padding:28px 14px">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:680px;background:#fff;border:1px solid #ded9cc;border-radius:14px;overflow:hidden">
<tr><td style="padding:24px 28px 18px;background:{BRAND_INK};color:#fff">
<table role="presentation" width="100%"><tr><td style="font:700 20px Georgia,serif;letter-spacing:.2px">QUANTIFY</td><td align="right" style="font:12px Arial,sans-serif;color:#cbd2ce">Daily demand brief</td></tr></table>
<div style="font:12px Arial,sans-serif;color:#b9c3bd;margin-top:18px;text-transform:uppercase;letter-spacing:1.2px">{html.escape(location['name'])} · {html.escape(brief['date_label'])}</div>
<h1 style="font:400 31px/1.18 Georgia,serif;margin:8px 0 8px;color:#fff">{html.escape(brief['headline'])}</h1>
<div style="font:14px/1.5 Arial,sans-serif;color:#cbd2ce">{html.escape(brief['comparison']['label'])} normally does {_money(brief['comparison']['sales'])} and {brief['comparison']['units']} items.</div>
</td></tr>
<tr><td style="padding:22px 28px 8px">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>
<td width="33%" style="vertical-align:top;padding-right:12px"><div style="font:11px Arial,sans-serif;color:#707972;text-transform:uppercase;letter-spacing:.8px">Expected sales</div><div style="font:700 27px Georgia,serif;color:{BRAND_INK};margin-top:5px">{_money(summary['expected_revenue'])}</div><div style="font:700 13px Arial,sans-serif;color:{BRAND_GREEN};margin-top:3px">{_change(summary['revenue_change_percent'])}, {_money(abs(summary.get('difference_sales') or 0))} against {_money(brief['comparison']['sales'])} on {html.escape(brief['comparison']['label'])}</div></td>
<td width="33%" style="vertical-align:top;padding:0 12px;border-left:1px solid #e4dfd3"><div style="font:11px Arial,sans-serif;color:#707972;text-transform:uppercase;letter-spacing:.8px">Peak hour</div><div style="font:700 27px Georgia,serif;color:{BRAND_INK};margin-top:5px">{html.escape(summary.get('peak_hour') or 'Not set')}</div><div style="font:13px Arial,sans-serif;color:#59645e;margin-top:3px">The hour to have covered</div></td>
<td width="34%" style="vertical-align:top;padding-left:12px;border-left:1px solid #e4dfd3"><div style="font:11px Arial,sans-serif;color:#707972;text-transform:uppercase;letter-spacing:.8px">Conditions</div><div style="font:700 17px Georgia,serif;color:{BRAND_INK};margin-top:7px">{html.escape(weather['condition'])}</div><div style="font:13px Arial,sans-serif;color:#59645e;margin-top:5px">{weather['high']}° / {weather['low']}° · {summary['confidence']}% confidence</div></td>
</tr></table>
</td></tr>
<tr><td style="padding:18px 28px 6px"><div style="font:700 12px Arial,sans-serif;color:{BRAND_OCHRE};text-transform:uppercase;letter-spacing:1px">What matters</div><table role="presentation" width="100%" cellspacing="0" cellpadding="0">{_priority_rows(brief)}</table></td></tr>
<tr><td style="padding:20px 28px 6px"><div style="font:700 12px Arial,sans-serif;color:{BRAND_OCHRE};text-transform:uppercase;letter-spacing:1px">Highest expected demand</div><table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><th align="left" style="padding:10px 8px 8px 0;font:11px Arial,sans-serif;color:#707972;text-transform:uppercase">Item</th><th align="right" style="padding:10px 8px 8px;font:11px Arial,sans-serif;color:#707972;text-transform:uppercase">Plan</th><th align="right" style="padding:10px 8px 8px;font:11px Arial,sans-serif;color:#707972;text-transform:uppercase">Likely range</th><th align="right" style="padding:10px 0 8px 8px;font:11px Arial,sans-serif;color:#707972;text-transform:uppercase">vs. normal</th></tr>{_item_rows(brief['top_volume'])}</table></td></tr>
<tr><td style="padding:20px 28px 8px"><table role="presentation" width="100%"><tr><td width="58%" style="vertical-align:top;padding-right:22px"><div style="font:700 12px Arial,sans-serif;color:{BRAND_OCHRE};text-transform:uppercase;letter-spacing:1px;margin-bottom:6px">Why the day looks this way</div>{_signal_rows(brief['context']['signals'])}</td><td width="42%" style="vertical-align:top;padding-left:22px;border-left:1px solid #e4dfd3"><div style="font:700 12px Arial,sans-serif;color:{BRAND_OCHRE};text-transform:uppercase;letter-spacing:1px">Next six days</div><table role="presentation" width="100%" cellspacing="0" cellpadding="0">{_week_rows(brief.get('week_ahead', []))}</table></td></tr></table></td></tr>
<tr><td style="padding:20px 28px;background:#f0ede5;border-top:1px solid #ded9cc"><div style="font:12px/1.5 Arial,sans-serif;color:#59645e">Sent {generated}. Register data runs through {brief['data_health']['latest_sale_date'] or 'no sales yet'} ({brief['data_health']['pos_freshness']}), {brief['data_health']['history_days']} days of history behind it. Change the time or turn this off in Settings.</div></td></tr>
</table></td></tr></table></body></html>"""


def render_brief_text(brief: dict[str, Any]) -> str:
    summary = brief["summary"]
    lines = [
        "QUANTIFY, YOUR MORNING BRIEF",
        f"{brief['location']['name']} · {brief['date_label']}",
        "",
        brief["headline"],
        f"Expected sales: {_money(summary['expected_revenue'])}, against {_money(brief['comparison']['sales'])} on {brief['comparison']['label']}",
        f"Items to make: {summary['expected_units']}, against {brief['comparison']['units']} on a normal day",
        f"Peak hour: {summary.get('peak_hour') or 'Not available'}",
        f"How sure: {summary['confidence']}%, built from {brief['comparison']['based_on_days']} comparable days",
        "",
        "WHAT MATTERS",
    ]
    lines.extend(f"- {row['title']}: {row['detail']}" for row in brief.get("priorities", []))
    lines.extend(["", "HIGHEST EXPECTED DEMAND"])
    for item in brief.get("top_volume", [])[:10]:
        lines.append(f"- {item['name']}: {item['expected']} (anywhere from {item['lower']} to {item['upper']}, {item['vs_baseline_percent']:+d}% against normal)")
    lines.extend(["", "WHY"])
    if brief["context"]["signals"]:
        lines.extend(f"- {row['label']}: {row['effect']:+.0f}%. {row.get('detail', '')}" for row in brief["context"]["signals"])
    else:
        lines.append("- Nothing outside the restaurant is pushing today either way.")
    lines.extend([
        "",
        f"Register data runs through {brief['data_health']['latest_sale_date'] or 'no sales yet'} "
        f"({brief['data_health']['pos_freshness']}).",
        "Change the time or turn this off in Settings.",
    ])
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
    if "@" not in owner_email or len(owner_email) > 254:
        raise ValueError("Enter a valid owner email address")
    try:
        hour, minute = [int(part) for part in send_time.split(":", 1)]
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except (ValueError, AttributeError):
        raise ValueError("Send time must use HH:MM") from None
    if not is_known(timezone_name):
        raise ValueError("We could not place that time zone. Try a city, a state, or a ZIP code")
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
    row = conn.execute("SELECT * FROM email_preferences WHERE location_id=?", (location_id,)).fetchone()
    if row is None:
        location = conn.execute("SELECT timezone FROM locations WHERE id=?", (location_id,)).fetchone()
        if location is None:
            raise ValueError("Unknown location")
        update_preferences(conn, location_id, "owner@example.com", False, "05:30", location["timezone"], True)
        row = conn.execute("SELECT * FROM email_preferences WHERE location_id=?", (location_id,)).fetchone()
    return dict(row)


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
# Entered by mistake during testing; the owner asked that nothing ever reach it.
# Add more with QUANTIFY_EMAIL_BLOCKLIST as a comma separated list.
NEVER_SEND_TO = {"srithith.chennareddy@gmail.com"}


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


def deliver_brief(
    conn: sqlite3.Connection,
    root: Path,
    location_id: str,
    target_date: date,
    recipient: str | None = None,
) -> dict[str, Any]:
    pref = preferences(conn, location_id)
    to = (recipient or pref["owner_email"]).strip().lower()
    if is_blocked(to):
        return {"status": "blocked", "recipient": to,
                "message": "That address is on the never-send list, so nothing was sent"}
    built = build_email(conn, location_id, target_date)
    try:
        if os.getenv("POSTMARK_SERVER_TOKEN"):
            message_id = _send_postmark(to, built["subject"], built["text"], built["html"])
            result = _record_delivery(conn, location_id, to, target_date, built["subject"], "sent", "postmark", provider_message_id=message_id)
        elif os.getenv("SMTP_HOST"):
            message_id = _send_smtp(to, built["subject"], built["text"], built["html"])
            result = _record_delivery(conn, location_id, to, target_date, built["subject"], "sent", "smtp", provider_message_id=message_id)
        else:
            path = _write_outbox(root, to, built["subject"], built["text"], built["html"], target_date)
            result = _record_delivery(conn, location_id, to, target_date, built["subject"], "outbox", "local-eml", artifact_path=path)
        conn.execute("UPDATE email_preferences SET last_sent_date=?,updated_at=? WHERE location_id=?", (target_date.isoformat(), utc_now(), location_id))
        conn.commit()
        return result | {"preview": {"subject": built["subject"], "brief": built["brief"]}}
    except Exception as exc:
        _record_delivery(conn, location_id, to, target_date, built["subject"], "failed", "configured", error=str(exc))
        raise


def send_due_briefs(conn: sqlite3.Connection, root: Path, now_utc: datetime | None = None) -> list[dict[str, Any]]:
    now_utc = now_utc or datetime.now(tz=timezone.utc)
    rows = conn.execute("SELECT * FROM email_preferences WHERE enabled=1").fetchall()
    results = []
    for row in rows:
        local_now = now_utc.astimezone(zone(row["timezone"]))
        local_date = local_now.date()
        send_hour, send_minute = [int(part) for part in row["send_time"].split(":", 1)]
        due = (local_now.hour, local_now.minute) >= (send_hour, send_minute)
        if not due or row["last_sent_date"] == local_date.isoformat():
            continue
        results.append(deliver_brief(conn, root, row["location_id"], local_date))
    return results
