from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("", views.payment_form, name="payment_form"),
    path("checkout/<uuid:reference>/", views.checkout, name="checkout"),
    path(
        "checkout/<uuid:reference>/complete/",
        views.complete_payment,
        name="complete_payment",
    ),
    path("receipt/<uuid:reference>/", views.receipt, name="receipt"),
]
