import logging

from django.core.exceptions import ImproperlyConfigured
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .forms import PaymentDetailsForm
from .models import Payment
from .services import cybersource
from .services.cybersource import CyberSourceError

logger = logging.getLogger(__name__)

# Statuses CyberSource's completed-payment JWT can report as success. Not yet
# confirmed against a real transaction (see SETUP.md) -- once one goes
# through, check the logged raw payload and adjust this if needed.
AUTHORIZED_STATUSES = {"AUTHORIZED", "COMPLETED", "ACCEPT", "ACCEPTED"}


def choose_bank(request):
    """Step 1 - the student picks which bank to pay through.

    Currency is a separate choice made on the next page -- either bank can
    settle either currency, so this step is purely about settlement.
    """
    return render(
        request, "payments/choose_bank.html", {"banks": cybersource.available_banks()}
    )


def payment_form(request, bank):
    """Step 2 - collect student details, currency, and the amount.

    The Payment row is written as PENDING *before* CyberSource is ever
    contacted, so there is always a local record even if the API call fails.
    """
    banks_by_slug = {b["slug"]: b for b in cybersource.available_banks()}
    if bank not in banks_by_slug:
        raise Http404("Unknown bank.")
    bank_info = banks_by_slug[bank]

    instance = Payment(bank=bank)

    if request.method == "POST":
        form = PaymentDetailsForm(
            request.POST, instance=instance, currencies=bank_info["currencies"]
        )
        if form.is_valid():
            payment = form.save()
            return redirect("payments:checkout", reference=payment.reference)
    else:
        form = PaymentDetailsForm(instance=instance, currencies=bank_info["currencies"])

    return render(
        request, "payments/payment_form.html", {"form": form, "bank": bank_info}
    )


def checkout(request, reference):
    """Step 2 - card entry via CyberSource Unified Checkout.

    A capture context JWT is fetched server-side using payment.amount and
    payment.currency (read from the DB, never from the browser) and handed
    to the Unified Checkout SDK, which mounts the card entry UI in-page.
    """
    payment = get_object_or_404(Payment, reference=reference)

    if payment.status != Payment.Status.PENDING:
        return redirect("payments:receipt", reference=payment.reference)

    target_origin = f"{request.scheme}://{request.get_host()}"
    capture_context = None
    client_library = None
    client_library_integrity = None
    config_error = None
    try:
        account = cybersource.get_account(payment.bank)
        account.validate()
        capture_context = cybersource.create_capture_context(payment, target_origin, account)
        client_library, client_library_integrity = cybersource.extract_client_library(capture_context)
    except (ImproperlyConfigured, CyberSourceError) as exc:
        config_error = str(exc)

    context = {
        "payment": payment,
        "capture_context": capture_context,
        "config_error": config_error,
        "client_library": client_library,
        "client_library_integrity": client_library_integrity,
    }
    return render(request, "payments/checkout.html", context)


@require_POST
def complete_payment(request, reference):
    """Called by the browser once Unified Checkout's autoProcessing has
    already authorized (or declined) the payment.

    With autoProcessing on, CyberSource runs the whole authorization itself
    and hands the browser a signed "completed payment result" JWT -- our
    server never calls the Payments API directly. We verify that JWT's
    signature here, then record the outcome.
    """
    payment = get_object_or_404(Payment, reference=reference)

    if payment.status != Payment.Status.PENDING:
        return JsonResponse(
            {"redirect": reverse("payments:receipt", args=[payment.reference])}
        )

    result_jwt = request.POST.get("payment_result", "")
    if not result_jwt:
        return JsonResponse({"error": "Missing payment result."}, status=400)

    try:
        account = cybersource.get_account(payment.bank)
        result = cybersource.verify_unified_checkout_jwt(result_jwt, account)
    except (ImproperlyConfigured, CyberSourceError) as exc:
        return JsonResponse({"error": str(exc)}, status=502)

    logger.info("Unified Checkout result for %s: %r", payment.reference, result)

    status = str(result.get("status") or result.get("decision") or "").upper()
    if not status:
        logger.warning(
            "Could not find a status/decision field in Unified Checkout result for %s: %r",
            payment.reference, result,
        )
        return JsonResponse(
            {"error": f"Payment could not be confirmed automatically. Reference: {payment.reference}"},
            status=502,
        )

    payment.cybs_transaction_id = str(result.get("id") or result.get("transactionId") or "")
    payment.cybs_response_code = status
    payment.status = (
        Payment.Status.AUTHORIZED if status in AUTHORIZED_STATUSES else Payment.Status.DECLINED
    )
    payment.save()

    return JsonResponse(
        {"redirect": reverse("payments:receipt", args=[payment.reference])}
    )


def receipt(request, reference):
    """Step 3 - outcome page."""
    payment = get_object_or_404(Payment, reference=reference)
    return render(request, "payments/receipt.html", {"payment": payment})
