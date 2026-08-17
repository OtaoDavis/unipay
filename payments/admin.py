from django.contrib import admin

from .models import Payment


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "merchant_reference",
        "student_id",
        "full_name",
        "purpose",
        "amount",
        "currency",
        "status",
        "created_at",
    )
    list_filter = ("status", "currency", "purpose", "created_at")
    search_fields = ("student_id", "first_name", "last_name", "cybs_transaction_id")
    readonly_fields = (
        "reference",
        "cybs_transaction_id",
        "cybs_reconciliation_id",
        "cybs_response_code",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "created_at"
