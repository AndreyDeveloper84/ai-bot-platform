from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("booking", "0024_remotebookingproxy_last_applied_event"),
    ]

    operations = [
        migrations.AddField(
            model_name="remotebookingproxy",
            name="appointment_version",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Последняя известная версия записи в каталоге (DRF-2785): из data.version событий booking.created, booking.rescheduled и appointment.rescheduled, только растёт. Её бот отправляет как expected_version в действиях мастера. NULL — событие с версией не приходило. Не last_applied_appointment_version: тот ведёт порядок канонических переносов, и запись в него из других событий сломала бы его проверку.",
                null=True,
                verbose_name="Версия записи для действий",
            ),
        ),
    ]
