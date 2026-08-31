"""Verify CyberSource credentials are loaded and well-formed.

Run:  python manage.py check_cybersource

Makes no network calls. Secrets are masked, so the output is safe to share.
"""

from django.conf import settings
from django.core.management.base import BaseCommand

from payments.services import cybersource


def _mask(value):
    """Show enough to identify a credential, never enough to use it."""
    if not value:
        return "(empty)"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]} ({len(value)} chars)"


class Command(BaseCommand):
    help = "Check that CyberSource merchant accounts are configured correctly."

    def handle(self, *args, **options):
        conf = getattr(settings, "CYBERSOURCE", {})
        ok = True

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("CyberSource configuration"))
        self.stdout.write(
            f"  Default/fallback: {conf.get('ENVIRONMENT', 'test')}"
        )

        # A common failure: .env exists but load_dotenv() was never called.
        if not any(
            a.get("MERCHANT_ID") for a in conf.get("ACCOUNTS", {}).values()
        ):
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "  No merchant IDs found at all. If your .env is filled in, "
                    "check that settings.py calls load_dotenv(BASE_DIR / '.env')."
                )
            )

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Merchant accounts"))
        for slug, passed, message in cybersource.validate_all():
            account = cybersource.get_account(slug)
            if passed:
                self.stdout.write(
                    f"  {self.style.SUCCESS('PASS')}  {slug:<8} "
                    f"env={account.environment:<10} "
                    f"host={account.host:<27} "
                    f"mid={account.merchant_id:<20} "
                    f"key={_mask(account.key_id):<22} "
                    f"secret={_mask(account.shared_secret):<22}"
                )
            else:
                ok = False
                self.stdout.write(f"  {self.style.ERROR('FAIL')}  {slug:<8} {message}")

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Currencies per bank"))
        banks = cybersource.available_banks()
        if not banks:
            ok = False
            self.stdout.write(self.style.ERROR("  No banks are configured."))
        for bank in banks:
            if not bank["currencies"]:
                ok = False
                self.stdout.write(f"  {self.style.ERROR('FAIL')}  {bank['slug']:<8} no currencies configured")
            else:
                self.stdout.write(f"  {bank['slug']:<8} {', '.join(bank['currencies'])}")

        self.stdout.write("")
        if ok:
            self.stdout.write(self.style.SUCCESS("Configuration looks good."))
        else:
            self.stdout.write(
                self.style.ERROR("Configuration has problems (see above).")
            )
