@echo off
rem Copy to config.bat. Keep config.bat private.

rem --- Local server and security ---
set "QUANTIFY_HOST=127.0.0.1"
set "QUANTIFY_PORT=8787"
rem set "QUANTIFY_DB=C:\Users\osavi\Downloads\QUANTIFY-Full-Platform\data\quantify.db"
set "QUANTIFY_SECURITY_PEPPER=replace-with-a-long-random-secret"
set "QUANTIFY_SECURE_COOKIES=0"
set "QUANTIFY_TRUST_PROXY=0"
set "QUANTIFY_DEBUG=0"

rem --- Weather ---
set "OPEN_METEO_API_KEY="
set "OPEN_METEO_BASE_URL=https://api.open-meteo.com/v1/forecast"
set "OPEN_METEO_ARCHIVE_URL=https://archive-api.open-meteo.com/v1/archive"

rem --- Broad local context ---
set "PREDICTHQ_ACCESS_TOKEN="
set "PREDICTHQ_EVENTS_URL=https://api.predicthq.com/v1/events/"
set "TICKETMASTER_API_KEY="
set "QUANTIFY_EVENT_RADIUS_MILES=15"

rem --- Square ---
set "SQUARE_ACCESS_TOKEN="
set "SQUARE_LOCATION_ID="
set "SQUARE_ENVIRONMENT=production"
set "SQUARE_VERSION=2026-07-15"
set "SQUARE_MAX_PAGES=250"
set "SQUARE_WEBHOOK_SIGNATURE_KEY="
set "SQUARE_WEBHOOK_NOTIFICATION_URL="
set "QUANTIFY_SQUARE_INTERNAL_LOCATION="

rem --- Daily owner email: Postmark or SMTP ---
set "QUANTIFY_FROM_EMAIL=briefs@yourdomain.com"
set "POSTMARK_SERVER_TOKEN="
set "SMTP_HOST="
set "SMTP_PORT=587"
set "SMTP_USERNAME="
set "SMTP_PASSWORD="
set "SMTP_STARTTLS=1"
set "SMTP_SSL=0"
