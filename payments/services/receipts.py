import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

from payments.models import Payment

logger = logging.getLogger(__name__)


def send_payment_receipt(payment):
    """Send one receipt after authorization without affecting payment success."""
    if (
        payment.status != Payment.Status.AUTHORIZED
        or not payment.email
        or payment.receipt_emailed_at
    ):
        return False

    context = {"payment": payment}
    message = EmailMultiAlternatives(
        subject=f"Payment receipt {payment.merchant_reference}",
        body=render_to_string("payments/email/receipt.txt", context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[payment.email],
    )
    message.attach_alternative(
        render_to_string("payments/email/receipt.html", context), "text/html"
    )

    try:
        message.send(fail_silently=False)
    except Exception:
        logger.exception(
            "Could not email receipt for payment %s", payment.merchant_reference
        )
        return False

    sent_at = timezone.now()
    marked = Payment.objects.filter(
        pk=payment.pk, receipt_emailed_at__isnull=True
    ).update(receipt_emailed_at=sent_at)
    if marked:
        payment.receipt_emailed_at = sent_at
    return bool(marked)
