"""Diagnostic: call the Payments API directly with a test card, bypassing
Unified Checkout and the browser entirely.

Run:  python manage.py test_direct_payment --bank absa
      python manage.py test_direct_payment --bank zanaco --amount 50 --currency USD

Only useful for checking whether a merchant account has a working
processing/acquiring connection at all. Uses CyberSource's own test card
number, never real card data. The full request/response is also written to
logs/cybersource.log via the normal logging in services/cybersource.py.
"""

import json

from django.core.management.base import BaseCommand, CommandError

from payments.services import cybersource


class Command(BaseCommand):
    help = "Directly call the CyberSource Payments API with a test card, bypassing the app's checkout flow."

    def add_arguments(self, parser):
        parser.add_argument("--bank", required=True, choices=["zanaco", "absa"])
        parser.add_argument("--amount", default="10.00")
        parser.add_argument("--currency", default="ZMW")

    def handle(self, *args, **options):
        try:
            account = cybersource.get_account(options["bank"])
            account.validate()
        except Exception as exc:
            raise CommandError(str(exc))

        self.stdout.write(f"Calling POST /pts/v2/payments for '{account.slug}'...")
        status_code, body = cybersource.direct_test_authorization(
            account, amount=options["amount"], currency=options["currency"]
        )

        self.stdout.write("")
        self.stdout.write(f"Status code: {status_code}")
        self.stdout.write(json.dumps(body, indent=2) if isinstance(body, (dict, list)) else str(body))

        self.stdout.write("")
        if status_code < 300:
            self.stdout.write(self.style.SUCCESS("Authorization succeeded."))
        else:
            self.stdout.write(self.style.ERROR("Authorization failed (see status/body above)."))
