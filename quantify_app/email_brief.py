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


# The product palette, as literals. Email cannot read CSS variables, and every
# one of these has to be stated inline on the element that uses it: a client
# that strips <style> or inverts for dark mode will otherwise repaint the text
# and the figures stop being readable.
INK, INK_2, INK_3 = "#15171a", "#4b5058", "#797f88"
LINE, PAGE, CARD = "#e7e6e2", "#f7f7f5", "#ffffff"
ACCENT, UP, DOWN, WARN, STOP = "#14634f", "#17694f", "#a4402c", "#8a5a12", "#8c2f1c"
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
NUM = "font-variant-numeric:tabular-nums;font-feature-settings:'tnum'"
DASH = "&#8212;"


def _delta(expected: float, baseline: float) -> tuple[str, str]:
    """The change against normal, and the colour it earns.

    Returned already worked out, because the reader should not have to subtract
    one column from another at six in the morning. Same materiality gate the
    make table uses on screen, three items and five percent, so the email and
    the screen never disagree about which rows moved.
    """
    try:
        gap = round(float(expected) - float(baseline))
        share = abs(gap) / max(1.0, float(baseline)) * 100
    except (TypeError, ValueError):
        return "", INK_3
    if abs(gap) < 3 or share < 5:
        return "", INK_3
    return (f"+{gap}" if gap > 0 else str(gap)), (UP if gap > 0 else DOWN)


def _stat(label: str, value: str, compare: str) -> str:
    """One figure, its name, and the thing it should be read against."""
    return (
        f'<td class="sp" width="33%" valign="top" style="padding:0 14px 0 0">'
        f'<div style="font:600 11px {FONT};letter-spacing:.08em;text-transform:uppercase;color:{INK_3};padding-bottom:5px">{label}</div>'
        f'<div style="font:600 26px/1.1 {FONT};color:{INK};{NUM}">{value}</div>'
        f'<div style="font:13px/1.4 {FONT};color:{INK_2};padding-top:4px">{compare}</div></td>'
    )


def _heading(text: str) -> str:
    return (f'<h2 style="margin:30px 0 10px;font:600 15px {FONT};color:{INK};'
            f'padding-bottom:8px;border-bottom:1px solid {LINE}">{text}</h2>')


def _stock_block(lines: list[dict[str, Any]]) -> str:
    """What runs out, and by when it has to be ordered.

    This is the one part of the morning that has a deadline attached, so it sits
    above the make list rather than below it. Only lines already inside a day of
    cover reach here, because a warning that arrives every morning stops being
    read by the second week.
    """
    if not lines:
        return ""
    rows = []
    for line in lines[:5]:
        runs = html.escape(str(line.get("runs_out_label") or "soon"))
        name = html.escape(str(line.get("name") or "Something"))
        if line.get("no_supplier") or not line.get("supplier_id"):
            todo, tone = "No supplier chosen yet", STOP
        elif line.get("late"):
            todo, tone = "The order is already late", STOP
        elif line.get("order_by_label"):
            todo, tone = f"Order by {html.escape(str(line['order_by_label']))}", INK_2
        else:
            todo, tone = f"From {html.escape(str(line.get('supplier') or 'your supplier'))}", INK_2
        rows.append(
            f'<tr><td style="padding:11px 0;border-top:1px solid {LINE};font:14px/1.45 {FONT};color:{INK}">'
            f'<b style="font-weight:600">{name}</b>'
            f'<span style="color:{STOP};font-weight:600"> runs out {runs}</span><br>'
            f'<span style="font-size:13px;color:{tone}">{todo}</span></td></tr>'
        )
    return (_heading("Before anything else")
            + '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
            + "".join(rows) + "</table>")


def render_brief_html(brief: dict[str, Any], stock: list[dict[str, Any]] | None = None) -> str:
    location = brief["location"]
    summary = brief["summary"]
    generated = datetime.fromisoformat(brief["generated_at"]).astimezone(zone(location["timezone"]))
    sent = generated.strftime("%I:%M %p %Z").lstrip("0")
    body_font = f"font:15px/1.55 {FONT};color:{INK_2}"

    if brief.get("no_history"):
        content = (f'<h1 style="margin:14px 0 8px;font:600 24px/1.25 {FONT};color:{INK}">Nothing to plan yet</h1>'
                   f'<p style="margin:0;{body_font}">{html.escape(_empty_message(brief))}</p>')
    else:
        stats = "".join([
            _stat("Expected sales", _money(summary["expected_revenue"]),
                  f"{_money(brief['comparison']['sales'])} on {html.escape(brief['comparison']['label'])}"),
            _stat("To make", f"{summary['make_units']}", f"{summary['expected_units']} expected to sell"),
            _stat("Busiest hour", html.escape(summary.get("peak_hour") or "Not known"),
                  f"{summary.get('peak_units', 0)} items, {summary.get('peak_share_percent', 0)}% of the day"),
        ])

        rows = []
        for item in brief.get("top_volume", [])[:10]:
            gap, tone = _delta(item.get("expected", 0), item.get("baseline", 0))
            cell = f"padding:11px 6px;border-top:1px solid {LINE};font:14px {FONT};{NUM}"
            rows.append(
                f'<tr><td style="{cell};color:{INK};text-align:left">{html.escape(item["name"])}</td>'
                f'<td style="{cell};color:{INK};font-weight:600;text-align:right">{item["make"]}</td>'
                f'<td style="{cell};color:{INK_2};text-align:right">{item["baseline"]}</td>'
                f'<td style="{cell};color:{tone};font-weight:600;text-align:right">{gap or DASH}</td></tr>'
            )
        heads = "".join(
            f'<th style="padding:0 6px 8px;font:600 11px {FONT};letter-spacing:.06em;'
            f'text-transform:uppercase;color:{INK_3};text-align:{"left" if i == 0 else "right"}">{label}</th>'
            for i, label in enumerate(["Item", "Make", "Normal", "Change"]))

        actions = "".join(
            f'<p style="margin:0 0 13px;{body_font}"><b style="color:{INK};font-weight:600">'
            f'{html.escape(row["title"])}</b><br>{html.escape(row["detail"])}</p>'
            for row in brief.get("priorities", [])[:3])
        reasons = "".join(
            f'<p style="margin:0 0 13px;{body_font}"><b style="color:{INK};font-weight:600">'
            f'{html.escape(row["label"])}</b><br>{html.escape(row.get("detail", ""))}</p>'
            for row in brief["context"]["signals"][:4])
        weekday = html.escape(datetime.fromisoformat(brief["date"]).strftime("%A"))

        content = (
            f'<h1 style="margin:14px 0 20px;font:600 27px/1.2 {FONT};color:{INK};letter-spacing:-.02em">'
            f'{html.escape(brief["headline"])}</h1>'
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>{stats}</tr></table>'
            + _stock_block(stock or [])
            + _heading("What to do")
            + (actions or f'<p style="margin:0;{body_font}">The make list is close to a normal day.</p>')
            + _heading("What to make")
            + '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
            + f"<thead><tr>{heads}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
            + f'<p style="margin:10px 0 0;font:12px/1.5 {FONT};color:{INK_3}">'
            + f"Change is against a normal {weekday}. A dash means the day is within two items of normal.</p>"
            + _heading("Why today looks like this")
            + (reasons or f'<p style="margin:0;{body_font}">No clear change from normal.</p>')
        )

    through = brief["data_health"]["latest_sale_date"] or "no sales yet"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><meta name="supported-color-schemes" content="light">
<meta name="format-detection" content="telephone=no,date=no,address=no,email=no">
<title>{html.escape(_email_subject(brief))}</title>
<style>
/* Apple Mail and iOS turn figures, dates and times into links and paint them
   blue. Every one of those is a number this email exists to make readable, so
   they are all forced back to the colour they were given. */
a[x-apple-data-detectors]{{color:inherit!important;text-decoration:none!important;font-size:inherit!important;font-family:inherit!important;font-weight:inherit!important;line-height:inherit!important}}
/* The three figures sit in a row on a desktop and stack on a phone, which is
   where this is actually read, standing up, before service. */
@media only screen and (max-width:600px){{
  .sp{{display:block!important;width:100%!important;padding:0 0 18px 0!important}}
  .pad{{padding-left:20px!important;padding-right:20px!important}}
}}
</style></head>
<body id="body" style="margin:0;padding:0;background:{PAGE};-webkit-text-size-adjust:100%;{body_font}">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">{html.escape(brief['headline'])}. {html.escape(brief['date_label'])}.</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{PAGE}"><tr><td align="center" style="padding:28px 12px">
<table role="presentation" width="640" cellpadding="0" cellspacing="0" border="0" style="width:640px;max-width:100%;background:{CARD};border:1px solid {LINE};border-radius:14px">
<tr><td class="pad" style="padding:24px 30px 0">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
    <td style="font:600 13px {FONT};letter-spacing:.22em;color:{INK}">QUANTIFY</td>
    <td align="right" style="font:13px {FONT};color:{INK_3}">{html.escape(location['name'])}</td>
  </tr></table>
  <div style="margin-top:4px;font:13px {FONT};color:{INK_3}">{html.escape(brief['date_label'])}</div>
</td></tr>
<tr><td class="pad" style="padding:0 30px 28px">{content}</td></tr>
<tr><td class="pad" style="padding:16px 30px 22px;border-top:1px solid {LINE};font:12px/1.6 {FONT};color:{INK_3}">
  Sent {html.escape(sent)}. Register data runs through {html.escape(through)}.<br>
  Change the send time or turn this off in Settings, under Location.
</td></tr></table></td></tr></table></body></html>"""


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
    # The thing with a deadline on it was missing from the morning email
    # entirely: a line running out today appeared on the screen and nowhere in
    # the message the owner actually reads. attention() returns nothing at all
    # until somebody has counted something, so this stays quiet rather than
    # guessing, and a failure here must never cost anyone their brief.
    stock: list[dict[str, Any]] = []
    try:
        from .supply import attention

        for line in attention(conn, location_id).get("lines", []):
            cover = line.get("days_of_cover")
            if line.get("late") or (isinstance(cover, (int, float)) and cover < 1):
                stock.append(line)
    except Exception:  # noqa: BLE001 - the email ships with or without stock
        stock = []
    return {
        "subject": _email_subject(brief),
        "html": render_brief_html(brief, stock),
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
