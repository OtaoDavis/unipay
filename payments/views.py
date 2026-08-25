import json
import logging

from django.core.exceptions import ImproperlyConfigured
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .forms import PaymentDetailsForm
from .models import Payment
from .services import cybersource
from .services.cybersource import CyberSourceError

logger = logging.getLogger(__name__)

# Statuses CyberSource's completed-payment response can report as success.
AUTHORIZED_STATUSES = {"AUTHORIZED", "COMPLETED", "ACCEPT", "ACCEPTED"}


def _decode_completed_payment(raw_result, payment):
    """Normalize the response variants returned by Unified Checkout v0.

    Depending on the client build, complete() can resolve to an object, a
    JSON-encoded object, or a signed completed-payment JWT. Limit recursive
    string decoding so malformed input cannot cause unbounded work.
    """
    result = raw_result
    for _ in range(3):
        if isinstance(result, dict):
            # Some client builds wrap the orchestration response.
            for key in ("completeResponse", "paymentResult", "result"):
                nested = result.get(key)
                if isinstance(nested, (dict, str)):
                    result = nested
                    break
            else:
                return result
            if isinstance(result, dict):
                return result
            continue

        if not isinstance(result, str) or not result.strip():
            raise ValueError("Malformed payment result.")

        result = result.strip()
        if result.count(".") == 2 and not result.startswith(("{", "[", '"')):
            account = cybersource.get_account(payment.bank)
            return cybersource.verify_unified_checkout_jwt(result, account)

        result = json.loads(result)

    raise ValueError("Malformed payment result.")


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


@never_cache
def checkout(request, reference):
    """Step 2 - card entry via CyberSource Unified Checkout.

    A capture context JWT is fetched server-side using payment.amount and
    payment.currency (read from the DB, never from the browser) and handed
    to the Unified Checkout SDK, which mounts the card entry UI in-page.
    """
    payment = get_object_or_404(Payment, reference=reference)
    now = timezone.now()

    # Reopening an in-progress checkout closes the interrupted attempt. The
    # student can start a new payment from its receipt instead of trying to
    # recover a one-time CyberSource context.
    Payment.objects.filter(
        pk=payment.pk,
        status=Payment.Status.PROCESSING,
    ).update(status=Payment.Status.FAILED, updated_at=now)

    # Atomic compare-and-set works on SQLite as well as databases with row
    # locking. Exactly one concurrent request can acquire this checkout.
    lock_acquired = Payment.objects.filter(
        pk=payment.pk, status=Payment.Status.PENDING
    ).update(status=Payment.Status.PROCESSING, updated_at=now)
    if not lock_acquired:
        payment.refresh_from_db()
        return redirect("payments:receipt", reference=payment.reference)
    payment.refresh_from_db()

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
        Payment.objects.filter(
            pk=payment.pk, status=Payment.Status.PROCESSING
        ).update(status=Payment.Status.PENDING)
        payment.status = Payment.Status.PENDING

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
    """Called after Unified Checkout v0 complete() authorizes or declines.

    The v0 SDK returns its completed-payment response as a JSON object. A
    CyberSource transaction-result webhook or server-side lookup should be
    the authoritative source before this flow is used in production.
    """
    payment = get_object_or_404(Payment, reference=reference)

    if payment.status not in {
        Payment.Status.PENDING,
        Payment.Status.PROCESSING,
        Payment.Status.FAILED,
    }:
        return JsonResponse(
            {"redirect": reverse("payments:receipt", args=[payment.reference])}
        )

    result_payload = request.POST.get("payment_result", "")
    if not result_payload:
        return JsonResponse({"error": "Missing payment result."}, status=400)

    try:
        result = _decode_completed_payment(result_payload, payment)
    except (TypeError, ValueError):
        return JsonResponse({"error": "Malformed payment result."}, status=400)
    except (ImproperlyConfigured, CyberSourceError) as exc:
        return JsonResponse({"error": str(exc)}, status=502)

    if not isinstance(result, dict):
        return JsonResponse({"error": "Malformed payment result."}, status=400)

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

    new_status = (
        Payment.Status.AUTHORIZED if status in AUTHORIZED_STATUSES else Payment.Status.DECLINED
    )
    Payment.objects.filter(
        pk=payment.pk,
        status__in=[
            Payment.Status.PENDING,
            Payment.Status.PROCESSING,
            Payment.Status.FAILED,
        ],
    ).update(
        cybs_transaction_id=str(result.get("id") or result.get("transactionId") or ""),
        cybs_response_code=status,
        status=new_status,
        updated_at=timezone.now(),
    )

    return JsonResponse(
        {"redirect": reverse("payments:receipt", args=[payment.reference])}
    )


@require_POST
def fail_payment(request, reference):
    """Close an interrupted checkout before offering a fresh payment."""
    payment = get_object_or_404(Payment, reference=reference)
    Payment.objects.filter(
        pk=payment.pk,
        status__in=[Payment.Status.PENDING, Payment.Status.PROCESSING],
    ).update(status=Payment.Status.FAILED, updated_at=timezone.now())
    return JsonResponse(
        {"redirect": reverse("payments:receipt", args=[payment.reference])}
    )


@never_cache
def receipt(request, reference):
    """Step 3 - outcome page."""
    payment = get_object_or_404(Payment, reference=reference)
    if payment.status == Payment.Status.PROCESSING:
        Payment.objects.filter(
            pk=payment.pk, status=Payment.Status.PROCESSING
        ).update(status=Payment.Status.FAILED, updated_at=timezone.now())
        payment.refresh_from_db()
    return render(request, "payments/receipt.html", {"payment": payment})
