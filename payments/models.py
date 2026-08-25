import uuid

from django.db import models


class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PROCESSING = "PROCESSING", "Processing"
        AUTHORIZED = "AUTHORIZED", "Authorized"
        DECLINED = "DECLINED", "Declined"
        REFUNDED = "REFUNDED", "Refunded"
        DISPUTED = "DISPUTED", "Disputed"

    class Purpose(models.TextChoices):
        TUITION = "TUITION", "Tuition"
        EXAM_FEE = "EXAM_FEE", "Examination Fee"
        GRADUATION = "GRADUATION", "Graduation"
        REGISTRATION = "REGISTRATION", "Registration Fee"
        LIBRARY_FINE = "LIBRARY_FINE", "Library Fine"
        OTHER = "OTHER", "Other"

    class Currency(models.TextChoices):
        ZMW = "ZMW", "ZMW (K)"
        USD = "USD", "USD ($)"

    # Public, unguessable handle used in URLs. Never expose the integer pk.
    reference = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    student_id = models.CharField(max_length=32, db_index=True)
    purpose = models.CharField(max_length=32, choices=Purpose.choices)

    # Slug into settings.CYBERSOURCE["ACCOUNTS"] (e.g. "zanaco", "absa") --
    # chosen explicitly by the student on the bank-choice page, not inferred
    # from currency. Not a model-level enum on purpose: adding a bank is a
    # settings.py change only, per cybersource.available_banks().
    bank = models.CharField(max_length=32, db_index=True)

    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(
        max_length=3, choices=Currency.choices, default=Currency.ZMW
    )

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )

    # Populated once CyberSource responds / sends webhooks.
    cybs_transaction_id = models.CharField(max_length=64, blank=True, db_index=True)
    cybs_reconciliation_id = models.CharField(max_length=64, blank=True)
    cybs_response_code = models.CharField(max_length=64, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["student_id", "-created_at"])]

    def __str__(self):
        return f"{self.merchant_reference} - {self.amount} {self.currency} ({self.status})"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def merchant_reference(self):
        """Sent to CyberSource as clientReferenceInformation.code.
        Ties the transaction back to the student without putting personal
        data into merchantDefinedInformation fields."""
        return f"CUZ-{self.student_id}-{str(self.reference)[:8].upper()}"
