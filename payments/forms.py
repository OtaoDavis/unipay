import re
from decimal import Decimal

from django import forms

from .models import Payment

# Adjust this to whatever CUZ student numbers actually look like.
STUDENT_ID_RE = re.compile(r"^\d{1,12}$")   # digits only, up to 12

# Minimum and maximum a student may pay in one transaction, per currency.
AMOUNT_LIMITS = {
    "ZMW": (Decimal("1.00"), Decimal("500000.00")),
    "USD": (Decimal("1.00"), Decimal("25000.00")),
}


class PaymentDetailsForm(forms.ModelForm):
    """Collects the non-card details. Card entry happens later, inside
    CyberSource Unified Checkout, so nothing sensitive is handled here."""

    class Meta:
        model = Payment
        fields = [
            "first_name",
            "last_name",
            "student_id",
            "purpose",
            "amount",
            "currency",
        ]
        widgets = {
            "first_name": forms.TextInput(
                attrs={"placeholder": "Enter first name", "autocomplete": "given-name"}
            ),
            "last_name": forms.TextInput(
                attrs={"placeholder": "Enter last name", "autocomplete": "family-name"}
            ),
            "student_id": forms.TextInput(
                attrs={"placeholder": "Enter Your Student ID", "autocapitalize": "characters"}
            ),
            "purpose": forms.Select(),
            "amount": forms.NumberInput(
                attrs={"placeholder": "0.00", "step": "0.01", "min": "1", "inputmode": "decimal"}
            ),
            "currency": forms.Select(),
        }

    def __init__(self, *args, currencies=None, **kwargs):
        super().__init__(*args, **kwargs)

        # Currency options are scoped to whichever bank the student already
        # picked -- passed in by the view, not tied to the model globally.
        currencies = currencies or [c for c, _ in Payment.Currency.choices]
        self.fields["currency"].choices = [
            (code, label) for code, label in Payment.Currency.choices if code in currencies
        ]
        self.fields["currency"].initial = currencies[0]

        self.fields["purpose"].choices = [("", "Select purpose of payment")] + list(
            Payment.Purpose.choices
        )

        # Mark invalid fields so the red border rule in style.css applies.
        if self.is_bound:
            for name in self.errors:
                if name in self.fields:
                    widget = self.fields[name].widget
                    existing = widget.attrs.get("class", "")
                    widget.attrs["class"] = (existing + " has-error").strip()

    def clean_student_id(self):
        value = self.cleaned_data["student_id"].strip()
        if not STUDENT_ID_RE.match(value):
            raise forms.ValidationError("Please enter a valid student ID number.")
        return value

    def clean_first_name(self):
        return self.cleaned_data["first_name"].strip()

    def clean_last_name(self):
        return self.cleaned_data["last_name"].strip()

    def clean(self):
        cleaned = super().clean()
        amount = cleaned.get("amount")
        currency = cleaned.get("currency")

        if amount is None or not currency:
            return cleaned

        if amount != amount.quantize(Decimal("0.01")):
            self.add_error("amount", "Amount may have at most 2 decimal places.")
            return cleaned

        minimum, maximum = AMOUNT_LIMITS[currency]
        if amount < minimum:
            self.add_error("amount", f"Minimum payment is {minimum} {currency}.")
        elif amount > maximum:
            self.add_error(
                "amount",
                f"Maximum online payment is {maximum} {currency}. "
                "Please contact the Finance Office for larger amounts.",
            )

        return cleaned
