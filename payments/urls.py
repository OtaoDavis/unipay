from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("", views.choose_bank, name="choose_bank"),
    path("checkout/<uuid:reference>/", views.checkout, name="checkout"),
    path(
        "checkout/<uuid:reference>/complete/",
        views.complete_payment,
        name="complete_payment",
    ),
    path(
        "checkout/<uuid:reference>/failed/",
        views.fail_payment,
        name="fail_payment",
    ),
    path("receipt/<uuid:reference>/", views.receipt, name="receipt"),
    # Kept last: a bare "<bank>/" would otherwise shadow the more specific
    # routes above for malformed URLs (e.g. "/pay/checkout/" with no reference).
    path("<slug:bank>/", views.payment_form, name="payment_form"),
]
