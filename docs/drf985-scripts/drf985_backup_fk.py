# DRF-985 шаг 2: read-only. Полный дамп 17 записей + обзор обратных FK на Appointment
# (кто ссылается на удаляемые записи и с каким on_delete).
import json
from django.apps import apps
from django.core import serializers

Appointment = apps.get_model("appointments", "Appointment")
TID = "b32a057a-56c7-4bf0-ae50-e11e76ab44be"

qs = Appointment.objects.filter(tenant_id=TID).order_by("pk")
print("BACKUP_JSON_BEGIN")
print(serializers.serialize("json", qs))
print("BACKUP_JSON_END")

DEAD = [
    "14db7cf7-f81e-4371-a03d-b0448a847fc0",
    "426dbd0b-f241-4fce-9cf2-a46d1c9bb0f2",
    "5fc4991e-24c5-466c-affe-2d5ee5a9f682",
    "8d187640-00bf-47a0-a597-8c27adc8766e",
    "a57c8ebf-836d-4ab9-8637-ef85aa2583e1",
    "abdbf33b-4811-4645-b9ba-d3c7a26e0386",
    "af26bcaf-68a3-4f1c-8bfd-4d2bcf0a4bd4",
    "c4c49944-2141-4087-9af6-3d0a175cdc04",
    "c5c7c460-42b6-4908-8e45-973893d5cbad",
    "e6ab15a8-41fe-4ade-958f-69c9c33ed234",
    "f43362f8-b610-4c97-886d-cefbeae00192",
    "f88f5fd7-2f61-4e76-9808-89bc491631da",
    "f9f4ec1a-8fa7-45fe-8612-760c0e06784b",
]
print("DEAD_COUNT:", Appointment.objects.filter(pk__in=DEAD).count())

for rel in Appointment._meta.related_objects:
    model = rel.related_model
    accessor = rel.get_accessor_name()
    on_delete = getattr(rel, "on_delete", None) or getattr(
        rel.field.remote_field, "on_delete", None
    )
    n = model.objects.filter(**{rel.field.name + "__in": DEAD}).count()
    print(
        json.dumps(
            {
                "related_model": f"{model._meta.app_label}.{model.__name__}",
                "accessor": accessor,
                "on_delete": getattr(on_delete, "__name__", str(on_delete)),
                "rows_referencing_dead": n,
            }
        )
    )
print("FK_SURVEY_DONE")
