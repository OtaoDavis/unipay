from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("payments", "0003_payment_processing_status"),
    ]

    operations = [
        migrations.AlterField(
            model_name="payment",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDING", "Pending"),
                    ("PROCESSING", "Processing"),
                    ("FAILED", "Failed"),
                    ("AUTHORIZED", "Authorized"),
                    ("DECLINED", "Declined"),
                    ("REFUNDED", "Refunded"),
                    ("DISPUTED", "Disputed"),
                ],
                db_index=True,
                default="PENDING",
                max_length=16,
            ),
        ),
    ]
