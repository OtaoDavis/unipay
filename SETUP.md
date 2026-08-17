# unipay — how the CyberSource integration works

This is the reference for explaining the setup to someone else (a boss, the
bank, CyberSource support) or getting your own bearings back after time away
from the code. `DEPLOY.md` covers getting this onto a server — this doc
covers what the code actually does.

## The payment flow, in plain terms

1. A student fills in their name, student ID, purpose, and amount on the
   payment form. Nothing card-related happens here.
2. That creates a `Payment` row in our database with status `PENDING` —
   there's always a local record, even if CyberSource is never reached.
3. The student is sent to a checkout page. Before rendering it, our server
   asks CyberSource for a **capture context**: a signed, short-lived token
   scoped to exactly this amount/currency/origin. This is a server-to-server
   call — the student's browser is not involved yet.
4. The checkout page loads CyberSource's own **Unified Checkout** JavaScript
   with `autoProcessing: true`, handing it that capture context. CyberSource's
   own iframe renders the card entry form — the card number never touches our
   server or our code at all.
5. When the student submits, CyberSource **authorizes the charge itself**
   (we never see the card, and our server never calls a "charge this card"
   API directly) and hands the browser a signed **completed payment result
   JWT** describing the outcome.
6. The browser posts that JWT to our server, which verifies its signature
   (proving CyberSource actually issued it and it wasn't tampered with),
   reads the outcome, updates the `Payment` row, and shows a receipt.

Steps 3 and 6 are the only two places our code talks to CyberSource
directly. Everything else is a normal Django form flow. Step 6 is a
signature *verification* (fetching CyberSource's public key and checking
the JWT), not a payment-authorization API call — CyberSource already did
the authorization itself in step 5.

## Where each piece lives

| What | Where |
|---|---|
| Which banks exist, their credentials, which currency routes to which bank | `config/settings.py`, the `CYBERSOURCE` dict |
| The actual secret values | `.env` (never committed — see `env.example` for the shape) |
| All CyberSource-specific logic: picking an account, signing requests, calling the API | `payments/services/cybersource.py` |
| The Django views wiring the flow together | `payments/views.py` |
| The page that loads CyberSource's JS SDK | `payments/templates/payments/checkout.html` |
| The `Payment` database model | `payments/models.py` |

### `config/settings.py` — the `CYBERSOURCE` dict

```python
CYBERSOURCE = {
    "ENVIRONMENT": ...,       # "test" -> apitest.cybersource.com, "production" -> api.cybersource.com
    "ACCOUNTS": {
        "zanaco": {"MERCHANT_ID": ..., "KEY_ID": ..., "SHARED_SECRET": ...},
        "absa":   {"MERCHANT_ID": ..., "KEY_ID": ..., "SHARED_SECRET": ...},
    },
    "CURRENCY_ROUTING": {
        "ZMW": "zanaco",   # which bank settles which currency
        "USD": "absa",
    },
}
```

This is the one place that would change if a third bank were ever added, or
if a currency needed to move to a different acquirer.

### `payments/services/cybersource.py` — the CyberSource client

Read top to bottom, this file is:

- `get_account(slug)` / `get_account_for_currency(currency)` — resolve which
  bank's credentials to use for a given payment.
- `_signature_headers(...)` — builds CyberSource's required HTTP Signature
  auth headers (HMAC-SHA256 over host/date/request-path/digest/merchant-id).
  Every request to CyberSource goes through this.
- `create_capture_context(payment, target_origin)` — step 3 above. Calls
  `POST /up/v1/capture-contexts`.
- `verify_unified_checkout_jwt(token, account)` — step 6 above. Looks up the
  signing key via `GET /flex/v2/public-keys/{kid}` and verifies the JWT's
  RS256 signature, returning its decoded payload.
- `validate_all()` — checks credentials are present and well-formed, no
  network call. Powers the command below.

### `payments/views.py` — the four views

`payment_form` → `checkout` → `complete_payment` → `receipt`, matching
steps 1–2, 3–4, 5–6, and the final page, respectively.

## Checking your own setup

```bash
python manage.py check_cybersource
```

This validates both accounts' credentials (masked in the output, safe to
share) and shows the effective `ZMW -> bank` / `USD -> bank` routing, with no
network calls to CyberSource at all. Run this first whenever something looks
wrong — it rules out a whole category of "is it my `.env`?" questions in
one shot.

## Known current blocker (as of Aug 2026)

Both merchant accounts can successfully complete step 3 (`capture-contexts`
returns a valid signed JWT — credentials and request-signing are correct),
but step 6 (`POST /pts/v2/payments`) returns an instant, plain-text
`404 Resource not found` for **both** accounts — including when tested with
a plain manually-entered test card number, with no Unified Checkout token
involved at all. CyberSource's Business Center for these accounts shows only
**Unified Checkout** and **Payer Authentication Configuration** under
Payment Configuration — no Transaction Search, Virtual Terminal, or
Processing Connections menu, which normally exist for any merchant with an
active card-processing connection.

**What this means:** these merchant IDs appear to be provisioned for card
tokenization only, without a processing/acquiring connection attached for
actually authorizing a charge.

**What to tell CyberSource support or the bank:**

- Merchant IDs: `zanaco_cavendishunifiedc` (Zanaco), `abz_cavendishuni_1286376_zmw` (Absa)
- Environment: test/sandbox (`apitest.cybersource.com`)
- Works: `POST /up/v1/capture-contexts` → valid signed JWT returned
- Fails: `POST /pts/v2/payments` → instant `404`, plain-text `"Resource not found"` body (not CyberSource's usual structured JSON error), for both a Unified-Checkout transient token and a plain test card number
- The ask: confirm whether the Payments API / a processing connection is enabled and attached to these merchant IDs — right now they look scoped to Unified Checkout tokenization only.

Once that's resolved, this should just work — the capture-context call and
the JWT-verification mechanism are both already proven correct (tested live
against CyberSource's sandbox). CyberSource's own public documentation
doesn't spell out the exact field names inside the completed-payment JWT
payload (which field says AUTHORIZED vs DECLINED, which holds the
transaction ID) — `payments/views.py`'s `complete_payment` logs the full
decoded payload for every attempt, so **the first real test transaction
should be checked against the server log** (`journalctl -u unipay` or
wherever gunicorn's output goes) to confirm those field names match what
`AUTHORIZED_STATUSES` in `views.py` expects, and adjusted if not. Until then,
anything the code can't confidently interpret as a success is treated as not
authorized — it will never mark a payment AUTHORIZED on a guess.
