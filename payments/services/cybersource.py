"""CyberSource merchant account configuration and REST API client.

CUZ holds two CyberSource merchant accounts, one per acquiring bank
(Zanaco and Absa). Each has its own merchant ID, key ID and shared secret,
and — critically — its own set of supported currencies.
"""

import base64
import binascii
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from email.utils import formatdate

import jwt
import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

# CyberSource API hosts. Sandbox and production are entirely separate
# accounts: test credentials will not work against the production host.
HOSTS = {
    "test": "apitest.cybersource.com",
    "production": "api.cybersource.com",
}

REQUEST_TIMEOUT = 15  # seconds


class CyberSourceError(Exception):
    """Raised when CyberSource rejects a request or is unreachable."""


@dataclass(frozen=True)
class MerchantAccount:
    """One CyberSource merchant account, tied to one acquiring bank."""

    slug: str
    label: str
    merchant_id: str
    key_id: str
    shared_secret: str
    host: str
    currencies: tuple = ()

    @property
    def base_url(self):
        return f"https://{self.host}"

    def validate(self):
        """Catch configuration mistakes at startup rather than mid-payment."""
        missing = [
            name
            for name in ("merchant_id", "key_id", "shared_secret")
            if not getattr(self, name)
        ]
        if missing:
            raise ImproperlyConfigured(
                f"CyberSource account '{self.slug}' is missing: {', '.join(missing)}. "
                f"Check your .env file."
            )

        # The shared secret is base64. A truncated copy-paste is a common
        # mistake and otherwise only surfaces as an opaque 401 later.
        try:
            decoded = base64.b64decode(self.shared_secret, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ImproperlyConfigured(
                f"CyberSource account '{self.slug}': shared secret is not valid "
                f"base64. Re-copy it from the Business Center."
            ) from exc

        if len(decoded) < 16:
            raise ImproperlyConfigured(
                f"CyberSource account '{self.slug}': shared secret looks too "
                f"short ({len(decoded)} bytes decoded). It may have been truncated."
            )

        return True


def _config():
    try:
        return settings.CYBERSOURCE
    except AttributeError as exc:
        raise ImproperlyConfigured(
            "settings.CYBERSOURCE is not defined. See SETUP.md."
        ) from exc


def get_host():
    conf = _config()
    environment = conf.get("ENVIRONMENT", "test")
    if environment not in HOSTS:
        raise ImproperlyConfigured(
            f"CYBS_ENVIRONMENT must be 'test' or 'production', got '{environment}'."
        )
    return HOSTS[environment]


def get_account(slug):
    """Return one account by slug ('zanaco' / 'absa')."""
    conf = _config()
    accounts = conf.get("ACCOUNTS", {})

    if slug not in accounts:
        raise ImproperlyConfigured(
            f"Unknown CyberSource account '{slug}'. "
            f"Configured accounts: {', '.join(sorted(accounts)) or 'none'}."
        )

    data = accounts[slug]
    return MerchantAccount(
        slug=slug,
        label=data.get("LABEL", slug.title()),
        merchant_id=data.get("MERCHANT_ID", ""),
        key_id=data.get("KEY_ID", ""),
        shared_secret=data.get("SHARED_SECRET", ""),
        host=get_host(),
        currencies=tuple(data.get("CURRENCIES", ())),
    )


def available_banks():
    """Banks a student can choose to pay through, each with the currencies
    it supports. Drives the bank-choice page and the currency dropdown on
    the details page — add a bank to ACCOUNTS in settings and it shows up
    here with no other changes.
    """
    accounts = _config().get("ACCOUNTS", {})
    return [
        {
            "slug": slug,
            "label": data.get("LABEL", slug.title()),
            "currencies": list(data.get("CURRENCIES", ())),
        }
        for slug, data in accounts.items()
    ]


def validate_all():
    """Validate every configured account. Called by the check_cybersource
    management command and safe to call at startup."""
    conf = _config()
    results = []

    for slug in sorted(conf.get("ACCOUNTS", {})):
        account = get_account(slug)
        try:
            account.validate()
            results.append((slug, True, "OK"))
        except ImproperlyConfigured as exc:
            results.append((slug, False, str(exc)))

    return results


def get_unified_checkout_js_url():
    """CDN URL for the Unified Checkout front-end SDK, same host as the API."""
    return f"https://{get_host()}/uc/v1/assets/1.0.0/UnifiedCheckout.js"


def _signature_headers(account, method, path, body_bytes):
    """Build the CyberSource HTTP Signature auth headers for one request.

    See https://developer.cybersource.com .../ch_authentication.5.3.htm —
    the signing string is host/date/(request-target)[/digest]/v-c-merchant-id,
    HMAC-SHA256'd with the base64-decoded shared secret.
    """
    host = get_host()
    date = formatdate(usegmt=True)
    signed = ["host", "date", "(request-target)"]
    lines = [
        f"host: {host}",
        f"date: {date}",
        f"(request-target): {method.lower()} {path}",
    ]

    headers = {"Host": host, "Date": date, "v-c-merchant-id": account.merchant_id}

    if body_bytes is not None:
        digest = "SHA-256=" + base64.b64encode(
            hashlib.sha256(body_bytes).digest()
        ).decode("ascii")
        signed.append("digest")
        lines.append(f"digest: {digest}")
        headers["Digest"] = digest

    signed.append("v-c-merchant-id")
    lines.append(f"v-c-merchant-id: {account.merchant_id}")

    signing_string = "\n".join(lines).encode("ascii")
    secret_key = base64.b64decode(account.shared_secret)
    signature = base64.b64encode(
        hmac.new(secret_key, signing_string, hashlib.sha256).digest()
    ).decode("ascii")

    headers["Signature"] = (
        f'keyid="{account.key_id}", algorithm="HmacSHA256", '
        f'headers="{" ".join(signed)}", signature="{signature}"'
    )
    return headers


def _call(account, method, path, payload=None):
    """POST/GET a CyberSource REST endpoint with a signed request."""
    body_bytes = None
    if payload is not None:
        body_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    headers = _signature_headers(account, method, path, body_bytes)
    headers["Content-Type"] = "application/json"
    headers["Accept"] = "application/json"

    url = f"{account.base_url}{path}"
    logger.info(
        "CyberSource request: %s",
        json.dumps({"method": method, "url": url, "account": account.slug, "body": payload}),
    )

    try:
        response = requests.request(
            method,
            url,
            headers=headers,
            data=body_bytes,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise CyberSourceError(f"Could not reach CyberSource: {exc}") from exc

    try:
        response_body = response.json()
    except ValueError:
        response_body = response.text

    logger.info(
        "CyberSource response: %s",
        json.dumps({
            "method": method,
            "url": url,
            "account": account.slug,
            "status_code": response.status_code,
            "body": response_body,
        }),
    )

    return response


def create_capture_context(payment, target_origin, account):
    """Ask CyberSource for a capture context JWT for one payment.

    The JWT is handed to the Unified Checkout JS SDK in the browser; it
    scopes that session to this amount/currency/origin only. `account` is
    the bank the student explicitly chose (payment.bank), resolved by the
    caller so there's a single source of truth for routing.
    """
    payload = {
        "targetOrigins": [target_origin],
        "clientVersion": "0.23",
        "allowedCardNetworks": ["VISA", "MASTERCARD", "AMEX"],
        "allowedPaymentTypes": ["PANENTRY"],
        "country": "ZM",
        "locale": "en_US",
        "captureMandate": {
            "billingType": "FULL",
            "requestEmail": True,
            "requestPhone": False,
            "requestShipping": False,
            "showAcceptedNetworkIcons": True,
        },
        "orderInformation": {
            "amountDetails": {
                "totalAmount": str(payment.amount),
                "currency": payment.currency,
            }
        },
    }

    response = _call(account, "POST", "/up/v1/capture-contexts", payload)
    if response.status_code >= 400:
        raise CyberSourceError(
            f"capture-contexts request failed ({response.status_code}): {response.text}"
        )
    return response.text.strip()


def get_public_key(kid, account):
    """Fetch the RSA public key (JWK) CyberSource used to sign a JWT.

    Used to verify Unified Checkout's autoProcessing "completed payment
    result" JWT — the kid in the JWT header identifies which key to fetch.
    """
    response = _call(account, "GET", f"/flex/v2/public-keys/{kid}")
    if response.status_code >= 400:
        raise CyberSourceError(
            f"Could not fetch public key '{kid}' ({response.status_code}): {response.text}"
        )
    return response.json()


def verify_unified_checkout_jwt(token, account):
    """Verify a JWT issued by Unified Checkout and return its decoded payload.

    With autoProcessing enabled, CyberSource authorizes the payment itself
    and hands the browser this signed JWT as the result — our server never
    calls the Payments API directly. We still must verify the signature
    before trusting anything in it.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise CyberSourceError(f"Malformed JWT: {exc}") from exc

    kid = header.get("kid")
    if not kid:
        raise CyberSourceError("JWT header has no 'kid' — cannot look up its verification key.")

    jwk = get_public_key(kid, account)
    public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))

    try:
        return jwt.decode(token, key=public_key, algorithms=["RS256"])
    except jwt.InvalidTokenError as exc:
        raise CyberSourceError(f"JWT signature verification failed: {exc}") from exc