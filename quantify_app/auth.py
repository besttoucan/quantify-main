from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import struct
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

PBKDF2_ITERATIONS = 310_000
SESSION_HOURS = 24 * 30
SESSION_RENEW_AFTER_HOURS = 24
CHALLENGE_MINUTES = 5
EMAIL_CODE_MINUTES = 20
EMAIL_CODE_ATTEMPTS = 6


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat(timespec="seconds")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _ip_hash(ip: str | None) -> str | None:
    if not ip:
        return None
    pepper = os.getenv("QUANTIFY_SECURITY_PEPPER", "quantify-local-security")
    return hashlib.sha256(f"{pepper}|{ip}".encode("utf-8")).hexdigest()


def hash_password(password: str, salt_b64: str | None = None) -> tuple[str, str]:
    salt = base64.b64decode(salt_b64) if salt_b64 else secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return base64.b64encode(derived).decode("ascii"), base64.b64encode(salt).decode("ascii")


def verify_password(password: str, expected_hash: str, salt_b64: str) -> bool:
    actual, _ = hash_password(password, salt_b64)
    return hmac.compare_digest(actual, expected_hash)


def validate_password(password: str) -> None:
    if len(password) < 12:
        raise ValueError("Use at least 12 characters")
    if len(password) > 256:
        raise ValueError("Password is too long")
    classes = sum((
        any(char.islower() for char in password),
        any(char.isupper() for char in password),
        any(char.isdigit() for char in password),
        any(not char.isalnum() for char in password),
    ))
    if classes < 3:
        raise ValueError("Use a mix of letters, numbers, and a symbol")


def generate_totp_secret() -> str:
    # 128 bits, the floor RFC 4226 allows. That is 26 base32 characters instead
    # of 32, and it is the only part of enrolment a person might ever type by
    # hand, so the shorter key is worth having.
    return base64.b32encode(secrets.token_bytes(16)).decode("ascii").rstrip("=")


def format_secret(secret: str) -> str:
    """Group the key in fours so it can be read aloud or typed without losing place."""
    clean = (secret or "").replace(" ", "").upper()
    return " ".join(clean[index:index + 4] for index in range(0, len(clean), 4))


def otpauth_uri(email: str, secret: str, issuer: str = "Quantify") -> str:
    from urllib.parse import quote
    label = quote(f"{issuer}:{email}", safe="")
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
        "&algorithm=SHA1&digits=6&period=30"
    )


def _decode_base32(value: str) -> bytes:
    clean = value.strip().replace(" ", "").upper()
    padding = "=" * ((8 - len(clean) % 8) % 8)
    return base64.b32decode(clean + padding, casefold=True)


def totp_code(secret: str, at_time: int | None = None, period: int = 30, digits: int = 6) -> str:
    timestamp = int(time.time() if at_time is None else at_time)
    counter = timestamp // period
    digest = hmac.new(_decode_base32(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10**digits)
    return str(value).zfill(digits)


def verify_totp(secret: str, code: str, window: int = 1) -> bool:
    normalized = "".join(char for char in str(code) if char.isdigit())
    if len(normalized) != 6:
        return False
    now = int(time.time())
    return any(hmac.compare_digest(totp_code(secret, now + step * 30), normalized) for step in range(-window, window + 1))


def generate_recovery_codes(count: int = 10) -> list[str]:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return ["".join(secrets.choice(alphabet) for _ in range(4)) + "-" + "".join(secrets.choice(alphabet) for _ in range(4)) + "-" + "".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(count)]


def _record_security_event(
    conn: sqlite3.Connection,
    event_type: str,
    user_id: str | None = None,
    ip: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO security_events(id,user_id,event_type,ip_hash,details,created_at) VALUES(?,?,?,?,?,?)",
        (f"sec-{uuid.uuid4().hex}", user_id, event_type, _ip_hash(ip), json.dumps(details or {}, separators=(",", ":")), iso_now()),
    )


def user_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) AS n FROM users WHERE active=1").fetchone()["n"])


def _new_organization(conn: sqlite3.Connection, name: str = "Your company") -> str:
    organization_id = f"org-{uuid.uuid4().hex[:12]}"
    conn.execute(
        "INSERT INTO organizations(id,name,plan,created_at) VALUES(?,?,?,?)",
        (organization_id, name, "standard", iso_now()),
    )
    return organization_id


def _organization_for_new_account(conn: sqlite3.Connection) -> str:
    """The first account joins whatever workspace already exists; later ones get their own.

    On a fresh install the sample data has already been created, so the first
    person to sign up lands in a product that works. Anyone signing up after
    that gets a workspace of their own and their own sample location, because
    two businesses must never see each other's sales.
    """
    if user_count(conn) == 0:
        row = conn.execute("SELECT id FROM organizations ORDER BY created_at LIMIT 1").fetchone()
        if row is not None:
            return str(row["id"])
    return _new_organization(conn)


def create_account(
    conn: sqlite3.Connection,
    email: str,
    display_name: str,
    password: str,
    ip: str | None = None,
) -> dict[str, Any]:
    email = email.strip().lower()
    # Signup is public and each one creates a workspace with a year of generated
    # history, so it is throttled per address block.
    if ip:
        recent = conn.execute(
            """SELECT COUNT(*) AS n FROM security_events
               WHERE event_type='account_created' AND ip_hash=? AND created_at>=?""",
            (_ip_hash(ip), (utc_now() - timedelta(hours=1)).isoformat(timespec="seconds")),
        ).fetchone()
        if int(recent["n"]) >= 3:
            _record_security_event(conn, "signup_rate_limited", ip=ip, details={"email": email})
            conn.commit()
            raise PermissionError("Too many accounts created from here. Try again in an hour")
    if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        raise ValueError("An account already uses that email address. Sign in instead")
    display_name = display_name.strip()
    if "@" not in email or len(email) > 254:
        raise ValueError("Enter a valid email address")
    if len(display_name) < 2:
        raise ValueError("Enter the account holder's name")
    validate_password(password)
    password_hash, salt = hash_password(password)
    user_id = f"usr-{uuid.uuid4().hex}"
    secret = generate_totp_secret()
    now = iso_now()
    conn.execute(
        """INSERT INTO users(
            id,organization_id,email,display_name,password_hash,password_salt,
            totp_secret,totp_enabled,email_verified,active,created_at
        ) VALUES(?,?,?,?,?,?,?,0,0,1,?)""",
        (user_id, _organization_for_new_account(conn), email, display_name, password_hash, salt, secret, now),
    )
    _record_security_event(conn, "account_created", user_id=user_id, ip=ip)
    conn.commit()
    organization_id = conn.execute("SELECT organization_id FROM users WHERE id=?", (user_id,)).fetchone()["organization_id"]
    return {
        "user_id": user_id,
        "email": email,
        "display_name": display_name,
        "organization_id": organization_id,
    }


# The original name, kept so existing callers and tests keep working.
create_owner = create_account


# ---------------------------------------------------------------------------
# Confirming the email address
# ---------------------------------------------------------------------------

def generate_email_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def start_email_verification(conn: sqlite3.Connection, user_id: str, ip: str | None = None) -> dict[str, Any]:
    """Issue a fresh six-digit code and retire any earlier one."""
    user = conn.execute("SELECT email,email_verified FROM users WHERE id=? AND active=1", (user_id,)).fetchone()
    if user is None:
        raise PermissionError("Account not found")
    if int(user["email_verified"]):
        return {"already_verified": True, "email": user["email"]}

    recent = conn.execute(
        "SELECT COUNT(*) AS n FROM email_verifications WHERE user_id=? AND created_at>=?",
        (user_id, (utc_now() - timedelta(minutes=10)).isoformat(timespec="seconds")),
    ).fetchone()
    if int(recent["n"]) >= 5:
        raise PermissionError("Too many codes requested. Wait a few minutes and try again")

    conn.execute("UPDATE email_verifications SET used_at=? WHERE user_id=? AND used_at IS NULL", (iso_now(), user_id))
    code = generate_email_code()
    now = utc_now()
    conn.execute(
        """INSERT INTO email_verifications(id,user_id,email,code_hash,created_at,expires_at)
           VALUES(?,?,?,?,?,?)""",
        (
            f"evc-{uuid.uuid4().hex}", user_id, user["email"], _token_hash(code),
            now.isoformat(timespec="seconds"),
            (now + timedelta(minutes=EMAIL_CODE_MINUTES)).isoformat(timespec="seconds"),
        ),
    )
    _record_security_event(conn, "email_code_issued", user_id=user_id, ip=ip)
    conn.commit()
    return {
        "already_verified": False,
        "email": user["email"],
        "code": code,
        "expires_in_minutes": EMAIL_CODE_MINUTES,
    }


def confirm_email(conn: sqlite3.Connection, user_id: str, code: str, ip: str | None = None) -> dict[str, Any]:
    normalized = "".join(char for char in str(code) if char.isdigit())
    row = conn.execute(
        """SELECT * FROM email_verifications WHERE user_id=? AND used_at IS NULL
           ORDER BY created_at DESC LIMIT 1""",
        (user_id,),
    ).fetchone()
    if row is None:
        raise ValueError("That code has already been used. Ask for a new one")
    if datetime.fromisoformat(row["expires_at"]) < utc_now():
        raise ValueError("That code expired. Ask for a new one")
    if int(row["attempts"]) >= EMAIL_CODE_ATTEMPTS:
        raise PermissionError("Too many tries on this code. Ask for a new one")
    if not hmac.compare_digest(row["code_hash"], _token_hash(normalized)):
        conn.execute("UPDATE email_verifications SET attempts=attempts+1 WHERE id=?", (row["id"],))
        _record_security_event(conn, "email_code_failed", user_id=user_id, ip=ip)
        conn.commit()
        raise ValueError("That code is not right. Check the email and try again")

    conn.execute("UPDATE email_verifications SET used_at=? WHERE id=?", (iso_now(), row["id"]))
    conn.execute("UPDATE users SET email_verified=1 WHERE id=?", (user_id,))
    _record_security_event(conn, "email_verified", user_id=user_id, ip=ip)
    conn.commit()
    return {"verified": True, "email": row["email"]}


def issue_recovery_codes(conn: sqlite3.Connection, user_id: str, ip: str | None = None) -> list[str]:
    """Create a fresh set of single-use backup codes, replacing any unused ones.

    Backup codes are optional. Most owners have their authenticator app synced
    across devices and never need them, so asking for them during signup adds a
    step that gets skipped anyway. They live in Account and security, and this is
    called when someone actually wants them.
    """
    conn.execute("DELETE FROM recovery_codes WHERE user_id=? AND used_at IS NULL", (user_id,))
    codes = generate_recovery_codes()
    now = iso_now()
    for code in codes:
        conn.execute(
            "INSERT INTO recovery_codes(id,user_id,code_hash,created_at) VALUES(?,?,?,?)",
            (f"rc-{uuid.uuid4().hex}", user_id, _token_hash(code.replace("-", "")), now),
        )
    _record_security_event(conn, "recovery_codes_issued", user_id=user_id, ip=ip)
    conn.commit()
    return codes


def _recent_failures(conn: sqlite3.Connection, email: str, ip: str | None) -> int:
    threshold = (utc_now() - timedelta(minutes=15)).isoformat(timespec="seconds")
    ip_digest = _ip_hash(ip)
    rows = conn.execute(
        "SELECT ip_hash,details FROM security_events WHERE event_type='login_failed' AND created_at>=?",
        (threshold,),
    ).fetchall()
    count = 0
    for row in rows:
        try:
            details = json.loads(row["details"] or "{}")
        except json.JSONDecodeError:
            details = {}
        if details.get("email") == email or (ip_digest and row["ip_hash"] == ip_digest):
            count += 1
    return count


def begin_login(
    conn: sqlite3.Connection,
    email: str,
    password: str,
    ip: str | None = None,
) -> dict[str, Any]:
    email = email.strip().lower()
    if _recent_failures(conn, email, ip) >= 8:
        _record_security_event(conn, "login_rate_limited", ip=ip, details={"email": email})
        conn.commit()
        raise PermissionError("Too many attempts. Wait 15 minutes and try again")
    user = conn.execute("SELECT * FROM users WHERE email=? AND active=1", (email,)).fetchone()
    if user is None or not verify_password(password, user["password_hash"], user["password_salt"]):
        _record_security_event(conn, "login_failed", user_id=user["id"] if user else None, ip=ip, details={"email": email})
        conn.commit()
        raise PermissionError("Email or password is incorrect")
    if not int(user["totp_enabled"]):
        # Two-step sign in is optional. Without it, a correct password is enough.
        session = create_session(conn, user["id"], ip=ip)
        _record_security_event(conn, "login_succeeded", user_id=user["id"], ip=ip)
        conn.commit()
        return {
            "authenticated": True,
            "email_verified": bool(int(user["email_verified"])),
            **session,
        }
    raw = _b64url(secrets.token_bytes(32))
    challenge_id = f"ch-{uuid.uuid4().hex}"
    now = utc_now()
    conn.execute(
        "INSERT INTO auth_challenges(id,user_id,token_hash,created_at,expires_at) VALUES(?,?,?,?,?)",
        (challenge_id, user["id"], _token_hash(raw), now.isoformat(timespec="seconds"), (now + timedelta(minutes=CHALLENGE_MINUTES)).isoformat(timespec="seconds")),
    )
    _record_security_event(conn, "password_verified", user_id=user["id"], ip=ip)
    conn.commit()
    return {"authenticated": False, "mfa_required": True, "challenge": raw, "expires_in": CHALLENGE_MINUTES * 60}


def _use_recovery_code(conn: sqlite3.Connection, user_id: str, code: str) -> bool:
    digest = _token_hash(code.replace("-", "").replace(" ", "").upper())
    rows = conn.execute("SELECT id,code_hash FROM recovery_codes WHERE user_id=? AND used_at IS NULL", (user_id,)).fetchall()
    for row in rows:
        if hmac.compare_digest(row["code_hash"], digest):
            conn.execute("UPDATE recovery_codes SET used_at=? WHERE id=?", (iso_now(), row["id"]))
            return True
    return False


def complete_login(
    conn: sqlite3.Connection,
    challenge: str,
    code: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    digest = _token_hash(challenge)
    row = conn.execute(
        """SELECT c.*,u.totp_secret,u.email FROM auth_challenges c
           JOIN users u ON u.id=c.user_id
           WHERE c.token_hash=? AND c.used_at IS NULL""",
        (digest,),
    ).fetchone()
    if row is None or datetime.fromisoformat(row["expires_at"]) < utc_now():
        raise PermissionError("The verification challenge expired. Sign in again")
    if int(row["attempts"]) >= 6:
        raise PermissionError("Too many verification attempts. Sign in again")
    accepted = verify_totp(row["totp_secret"], code) or _use_recovery_code(conn, row["user_id"], code)
    if not accepted:
        conn.execute("UPDATE auth_challenges SET attempts=attempts+1 WHERE id=?", (row["id"],))
        _record_security_event(conn, "mfa_failed", user_id=row["user_id"], ip=ip)
        conn.commit()
        raise PermissionError("The verification code is not valid")
    conn.execute("UPDATE auth_challenges SET used_at=? WHERE id=?", (iso_now(), row["id"]))
    session = create_session(conn, row["user_id"], ip=ip, user_agent=user_agent)
    _record_security_event(conn, "login_succeeded", user_id=row["user_id"], ip=ip)
    conn.commit()
    return {"authenticated": True, **session}


def enable_totp(conn: sqlite3.Connection, user_id: str, code: str, ip: str | None = None) -> dict[str, Any]:
    user = conn.execute("SELECT * FROM users WHERE id=? AND active=1", (user_id,)).fetchone()
    if user is None:
        raise PermissionError("Account not found")
    if not verify_totp(user["totp_secret"], code):
        raise ValueError("Enter the current six-digit code from your authenticator app")
    conn.execute("UPDATE users SET totp_enabled=1 WHERE id=?", (user_id,))
    _record_security_event(conn, "mfa_enabled", user_id=user_id, ip=ip)
    conn.commit()
    remaining = int(conn.execute("SELECT COUNT(*) AS n FROM recovery_codes WHERE user_id=? AND used_at IS NULL", (user_id,)).fetchone()["n"])
    return {"enabled": True, "recovery_codes_remaining": remaining}


def disable_totp(conn: sqlite3.Connection, user_id: str, password: str, ip: str | None = None) -> dict[str, Any]:
    """Turning two-step sign in off needs the password, so a borrowed screen cannot do it."""
    user = conn.execute("SELECT * FROM users WHERE id=? AND active=1", (user_id,)).fetchone()
    if user is None:
        raise PermissionError("Account not found")
    if not verify_password(password, user["password_hash"], user["password_salt"]):
        raise PermissionError("That password is not right")
    # A fresh secret is issued so the old authenticator entry stops working.
    conn.execute("UPDATE users SET totp_enabled=0, totp_secret=? WHERE id=?", (generate_totp_secret(), user_id))
    conn.execute("DELETE FROM recovery_codes WHERE user_id=? AND used_at IS NULL", (user_id,))
    _record_security_event(conn, "mfa_disabled", user_id=user_id, ip=ip)
    conn.commit()
    return {"enabled": False}


def create_session(
    conn: sqlite3.Connection,
    user_id: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    raw = _b64url(secrets.token_bytes(32))
    now = utc_now()
    session_id = f"ses-{uuid.uuid4().hex}"
    csrf = _b64url(secrets.token_bytes(24))
    expires = now + timedelta(hours=SESSION_HOURS)
    conn.execute(
        """INSERT INTO sessions(
            id,user_id,token_hash,csrf_token,created_at,expires_at,ip_hash,user_agent
        ) VALUES(?,?,?,?,?,?,?,?)""",
        (session_id, user_id, _token_hash(raw), csrf, now.isoformat(timespec="seconds"), expires.isoformat(timespec="seconds"), _ip_hash(ip), (user_agent or "")[:500]),
    )
    conn.execute("UPDATE users SET last_login_at=? WHERE id=?", (now.isoformat(timespec="seconds"), user_id))
    conn.commit()
    return {"session_token": raw, "csrf_token": csrf, "expires_at": expires.isoformat(timespec="seconds")}


@dataclass(frozen=True)
class Session:
    id: str
    user_id: str
    email: str
    display_name: str
    organization_id: str
    csrf_token: str
    totp_enabled: bool
    email_verified: bool
    expires_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "email": self.email,
            "display_name": self.display_name,
            "organization_id": self.organization_id,
            "csrf_token": self.csrf_token,
            "totp_enabled": self.totp_enabled,
            "email_verified": self.email_verified,
            "expires_at": self.expires_at,
        }


def session_from_token(conn: sqlite3.Connection, token: str | None) -> Session | None:
    if not token:
        return None
    row = conn.execute(
        """SELECT s.id,s.user_id,s.csrf_token,s.expires_at,s.revoked_at,
                  u.email,u.display_name,u.organization_id,u.totp_enabled,u.email_verified,u.active
           FROM sessions s JOIN users u ON u.id=s.user_id
           WHERE s.token_hash=?""",
        (_token_hash(token),),
    ).fetchone()
    if row is None or row["revoked_at"] or not int(row["active"]):
        return None
    expires = datetime.fromisoformat(row["expires_at"])
    now = utc_now()
    if expires < now:
        return None
    # Roll the expiry forward while the session is in use. Somebody who opens
    # Quantify every morning is never asked to sign in again.
    if expires - now < timedelta(hours=SESSION_HOURS - SESSION_RENEW_AFTER_HOURS):
        expires = now + timedelta(hours=SESSION_HOURS)
        conn.execute("UPDATE sessions SET expires_at=? WHERE id=?", (expires.isoformat(timespec="seconds"), row["id"]))
        conn.commit()
        row = dict(row) | {"expires_at": expires.isoformat(timespec="seconds")}
    return Session(
        id=row["id"], user_id=row["user_id"], email=row["email"],
        display_name=row["display_name"], organization_id=row["organization_id"],
        csrf_token=row["csrf_token"], totp_enabled=bool(row["totp_enabled"]),
        email_verified=bool(row["email_verified"]), expires_at=row["expires_at"],
    )


def revoke_session(conn: sqlite3.Connection, token: str | None, ip: str | None = None) -> None:
    if not token:
        return
    row = conn.execute("SELECT id,user_id FROM sessions WHERE token_hash=?", (_token_hash(token),)).fetchone()
    if row:
        conn.execute("UPDATE sessions SET revoked_at=? WHERE id=?", (iso_now(), row["id"]))
        _record_security_event(conn, "logout", user_id=row["user_id"], ip=ip)
        conn.commit()


def onboarding_required(conn: sqlite3.Connection, organization_id: str) -> bool:
    row = conn.execute("SELECT onboarded_at FROM organizations WHERE id=?", (organization_id,)).fetchone()
    return row is None or not row["onboarded_at"]


def auth_state(conn: sqlite3.Connection, token: str | None) -> dict[str, Any]:
    count = user_count(conn)
    session = session_from_token(conn, token)
    needs_onboarding = False
    if session is not None and session.email_verified:
        needs_onboarding = onboarding_required(conn, session.organization_id)
    return {
        "setup_required": count == 0,
        "authenticated": session is not None,
        "user": session.as_dict() if session else None,
        # Confirming the email address is the one step nobody skips. Two-step
        # sign in is offered in Settings and left to the account holder.
        "email_verification_required": bool(session and not session.email_verified),
        "mfa_enabled": bool(session and session.totp_enabled),
        "onboarding_required": needs_onboarding,
    }


def cookie_header(token: str, max_age: int = SESSION_HOURS * 3600) -> str:
    secure = "; Secure" if os.getenv("QUANTIFY_SECURE_COOKIES", "0") == "1" else ""
    return f"quantify_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}{secure}"


def clear_cookie_header() -> str:
    secure = "; Secure" if os.getenv("QUANTIFY_SECURE_COOKIES", "0") == "1" else ""
    return f"quantify_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0{secure}"
