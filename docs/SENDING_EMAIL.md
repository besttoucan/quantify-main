# Turning on real email

Right now Quantify does not send anything. Confirmation codes and morning briefs are
written to `data/outbox` as real `.eml` files you can open, and during signup the
confirmation code is shown on screen so you can finish creating an account.

That is deliberate for an unlaunched product. The moment you set the credentials
below, the code stops appearing on screen and only arrives by email. There is no
switch to flip and no code to change.

---

## Pick one

**Postmark** is the recommendation. It is built for exactly this kind of mail
(codes, receipts, alerts), its deliverability is the best in the category, and
Quantify already speaks to it directly.

**Resend** is the easier signup and has a free tier that covers early use. Quantify
talks to it over plain SMTP.

Either is fine. Do not use a personal Gmail account: consumer mailboxes are rate
limited, get flagged as spam, and will silently stop delivering once you have a few
customers.

| | Postmark | Resend |
|---|---|---|
| Site | postmarkapp.com | resend.com |
| Free allowance | 100 emails a month | 3,000 a month, 100 a day |
| Then | about $15 a month for 10,000 | about $20 a month for 50,000 |
| Setup time | 15 minutes plus DNS wait | 10 minutes plus DNS wait |
| Quantify uses | its own API | SMTP |

---

## What you need first

A domain you control, for example `quantify.app`. You cannot send from
`something@gmail.com`. If you do not have one yet, buy one at any registrar
(Namecheap, Cloudflare, Porkbun) for about $12 a year. That is the only unavoidable
cost.

---

## Postmark, step by step

1. Sign up at **postmarkapp.com**.
2. It asks what you are sending. Choose **transactional**.
3. Go to **Sender Signatures**, then **Add Domain**, and type your domain.
4. Postmark shows you two DNS records, a **DKIM** record and a **Return-Path**
   record. Both are CNAME or TXT entries.
5. Open your registrar's DNS page and add them exactly as shown. Copy and paste;
   a single wrong character means it never verifies.
6. Wait. Usually 10 minutes, occasionally a few hours. Postmark shows a green tick
   when it is done.
7. Go to **Servers**, open the default server, then the **API Tokens** tab, and copy
   the **Server API token**. It starts with a long hex string.
8. Put it in your config (see below).

## Resend, step by step

1. Sign up at **resend.com**.
2. Go to **Domains**, then **Add Domain**, and type your domain.
3. Add the DNS records it shows you at your registrar, same as above.
4. Wait for the green tick.
5. Go to **API Keys**, create one with **Sending access**, and copy it. It starts
   with `re_`.
6. Put it in your config (see below).

---

## Where the credentials go

On Windows, open `config.bat` next to `start.bat` (copy `config.example.bat` if it is
not there yet) and add the lines for whichever you chose.

**Postmark**

```bat
set "QUANTIFY_FROM_EMAIL=hello@yourdomain.com"
set "POSTMARK_SERVER_TOKEN=paste-the-server-token-here"
```

**Resend**

```bat
set "QUANTIFY_FROM_EMAIL=hello@yourdomain.com"
set "SMTP_HOST=smtp.resend.com"
set "SMTP_PORT=587"
set "SMTP_USERNAME=resend"
set "SMTP_PASSWORD=re_paste_your_api_key_here"
set "SMTP_STARTTLS=1"
```

On macOS or Linux the same names go in `.env`.

`QUANTIFY_FROM_EMAIL` must be at the domain you just verified. Sending from an
address at an unverified domain fails, and it is the mistake people make most.

Restart the app. That is the whole job.

---

## Checking it worked

1. Open **Settings**, then **Daily email**, and press **Send me a test**.
2. If it lands in your inbox, you are done.
3. If the toast says it was written to `data/outbox`, the credentials are not being
   read. Check the spelling of the variable names and that you restarted.
4. Create a throwaway account. If the six-digit code no longer appears on the signup
   screen, real sending is live. That box only exists while no provider is configured.

---

## Before you take real customers

- **Send yourself a code and check it is not in spam.** If it is, your DNS records
  are incomplete. Both providers have a checker.
- **Add a DMARC record.** `v=DMARC1; p=none; rua=mailto:you@yourdomain.com` is enough
  to start and tells you who is sending as you.
- **Warm up gradually.** A brand new domain that suddenly sends a thousand emails
  looks exactly like a spammer. A few dozen a day for the first week is plenty.
- **Watch the bounce rate** in whichever dashboard you chose. Above about 5% and the
  provider will start throttling you.

---

## What still needs building before launch

Confirmation codes and the morning brief are wired. These are not, and each one is a
small piece of work on top of what is already there:

- **Password reset by email.** Today a forgotten password is fixed from the command
  line with `python server.py --set-password EMAIL`. That is fine for you and not
  fine for a customer. It needs the same code mechanism as email confirmation, which
  already exists in `quantify_app/auth.py` and can be reused almost as is.
- **Changing the email address on an account**, which needs confirmation on both the
  old and the new address.
- **A real Stripe account.** See the payments section of the README. Until
  `STRIPE_SECRET_KEY` is set, the plan screens run locally and nothing is charged.
