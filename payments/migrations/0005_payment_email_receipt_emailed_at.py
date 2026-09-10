from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("payments", "0004_payment_failed_status")]

    operations = [
        migrations.AddField(
            model_name="payment",
            name="email",
            field=models.EmailField(blank=True, default="", max_length=254),
        ),
        migrations.AddField(
            model_name="payment",
            name="receipt_emailed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
