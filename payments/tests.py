import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Payment
from .services.cybersource import create_capture_context


def make_payment(**overrides):
    values = {
        "first_name": "Test",
        "last_name": "Student",
        "email": "student@example.com",
        "student_id": "12345",
        "purpose": Payment.Purpose.TUITION,
        "bank": "zanaco",
        "amount": Decimal("100.00"),
        "currency": Payment.Currency.ZMW,
    }
    values.update(overrides)
    return Payment.objects.create(**values)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="payments@example.com",
)
class ReceiptEmailTests(TestCase):
    def test_authorized_payment_sends_one_receipt(self):
        payment = make_payment(status=Payment.Status.PROCESSING)
        url = reverse("payments:complete_payment", args=[payment.reference])
        result = {"status": "AUTHORIZED", "id": "cybs-123"}

        first = self.client.post(url, {"payment_result": json.dumps(result)})
        second = self.client.post(url, {"payment_result": json.dumps(result)})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["student@example.com"])
        payment.refresh_from_db()
        self.assertEqual(payment.status, Payment.Status.AUTHORIZED)
        self.assertIsNotNone(payment.receipt_emailed_at)

    def test_declined_payment_does_not_send_receipt(self):
        payment = make_payment(status=Payment.Status.PROCESSING)
        url = reverse("payments:complete_payment", args=[payment.reference])

        self.client.post(url, {"payment_result": json.dumps({"status": "DECLINED"})})

        self.assertEqual(len(mail.outbox), 0)


class CaptureContextTests(TestCase):
    @patch("payments.services.cybersource._call")
    def test_prefills_stored_email_in_billing_data(self, call):
        call.return_value = SimpleNamespace(status_code=200, text="signed-jwt")
        payment = make_payment()
        account = SimpleNamespace(allowed_card_networks=("VISA",))

        create_capture_context(payment, "https://payments.example.com", account)

        payload = call.call_args.args[3]
        self.assertEqual(payload["orderInformation"]["billTo"]["email"], payment.email)
        self.assertFalse(payload["captureMandate"]["requestEmail"])
