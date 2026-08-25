from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("payments", "0002_payment_bank"),
    ]

    operations = [
        migrations.AlterField(
            model_name="payment",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDING", "Pending"),
                    ("PROCESSING", "Processing"),
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
