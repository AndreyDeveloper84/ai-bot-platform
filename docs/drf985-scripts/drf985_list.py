# DRF-985: read-only список всех записей тенанта formula-tela
# с пометкой, жива ли пара «мастер × услуга» после DRF-974.
# Запуск: type <этот файл> | ssh taximeter@194.87.99.126 "docker exec -i dev-web-1 python manage.py shell"
import json
from django.apps import apps

Appointment = apps.get_model("appointments", "Appointment")
SpecialistService = apps.get_model("services", "SpecialistService")
TID = "b32a057a-56c7-4bf0-ae50-e11e76ab44be"

flds = {f.name for f in Appointment._meta.fields}
print("FIELDS:", sorted(flds))

qs = (
    Appointment.objects.filter(tenant_id=TID)
    if "tenant" in flds
    else Appointment.objects.filter(salon_service__tenant_id=TID)
)
qs = qs.select_related("salon_service").order_by("pk")
print("TOTAL:", qs.count())

active = set(
    SpecialistService.objects.filter(tenant_id=TID, is_active=True).values_list(
        "specialist_id", "salon_service_id"
    )
)


def g(obj, *names):
    for n in names:
        if hasattr(obj, n):
            v = getattr(obj, n)
            if v is not None:
                return v
    return None


for a in qs:
    svc = g(a, "salon_service")
    row = {
        "id": str(a.pk),
        "status": g(a, "status", "state"),
        "start": str(g(a, "start_at", "start_time", "starts_at", "scheduled_at", "date")),
        "created": str(g(a, "created_at")),
        "master_id": str(g(a, "specialist_id")),
        "service": g(svc, "name") if svc else None,
        "price": str(g(a, "price", "total_price", "amount")),
        "client": str(g(a, "client", "user", "customer")),
        "pair_active": (g(a, "specialist_id"), g(a, "salon_service_id")) in active,
    }
    print(json.dumps(row, ensure_ascii=False, default=str))
