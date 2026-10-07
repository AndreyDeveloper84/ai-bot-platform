# DRF-2845 — новый тип согласия ``ai_food_estimation``. Только choices: строк не пишет.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("consent", "0007_preference_inference_consent_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="consentrecord",
            name="consent_type",
            field=models.CharField(
                choices=[
                    ("personal_data", "Personal data (152-ФЗ)"),
                    ("marketing", "Marketing"),
                    ("photo_biometric", "Photo / biometric"),
                    ("health", "Health"),
                    ("memory_green", "Memory — green zone"),
                    ("memory_yellow", "Memory — yellow zone"),
                    ("memory_red", "Memory — red zone (special category)"),
                    (
                        "food_diary_processing",
                        "Food diary processing (food, drinks, photos, voice)",
                    ),
                    (
                        "personal_calculation",
                        "Personal calculation (weight, height, age, sex, activity, goal)",
                    ),
                    (
                        "preference_inference",
                        "Preference inference (Ayla proposes preferences to remember)",
                    ),
                    (
                        "ai_food_estimation",
                        "AI food estimation (dish name goes to an external model)",
                    ),
                ],
                help_text="Which consent this row is about. See ConsentType for the 4 Phase-0 categories.",
                max_length=32,
            ),
        ),
    ]
