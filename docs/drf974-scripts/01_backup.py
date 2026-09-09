"""DRF-974 · ШАГ 1 — БЭКАП (read-only, безопасно запускать в любой момент).

Запуск:
    ssh taximeter@194.87.99.126 "docker exec -i dev-web-1 python manage.py shell" < 01_backup.py

Пишет два JSON внутрь контейнера в /tmp и печатает их в stdout (лог сессии = второй
экземпляр бэкапа). Забрать файл с хоста:
    ssh taximeter@194.87.99.126 "docker cp dev-web-1:/tmp/drf974_backup_edges.json /tmp/ && cat /tmp/drf974_backup_edges.json" > drf974_backup_edges.json
"""
import json

from services.models import SalonService, SpecialistService
from tenants.models import Tenant

t = Tenant.objects.get(slug="formula-tela")

edges = [
    {
        "id": str(r.pk),
        "specialist_id": str(r.specialist_id),
        "specialist": r.specialist.display_name,
        "salon_service_id": str(r.salon_service_id),
        "salon_service": r.salon_service.name,
        "price": str(r.price),
        "duration_minutes": r.duration_minutes,
        "requires_health_check": r.requires_health_check,
        "buffer_after_minutes": r.buffer_after_minutes,
        "is_active": r.is_active,
        "created_at": r.created_at.isoformat(),
        "updated_at": r.updated_at.isoformat(),
    }
    for r in SpecialistService.objects.filter(tenant=t).select_related("specialist", "salon_service")
]

services = [
    {
        "id": str(s.pk),
        "name": s.name,
        "category_id": str(s.category_id) if s.category_id else None,
        "category_name": s.category.name if s.category_id else None,
        "template_id": str(s.template_id) if s.template_id else None,
        "is_active": s.is_active,
    }
    for s in SalonService.objects.filter(tenant=t).select_related("category")
]

with open("/tmp/drf974_backup_edges.json", "w", encoding="utf-8") as fh:
    json.dump(edges, fh, ensure_ascii=False, indent=1)
with open("/tmp/drf974_backup_services.json", "w", encoding="utf-8") as fh:
    json.dump(services, fh, ensure_ascii=False, indent=1)

print(f"BACKUP edges={len(edges)} services={len(services)} → /tmp/drf974_backup_*.json")
print("---8<--- EDGES ---8<---")
print(json.dumps(edges, ensure_ascii=False))
print("---8<--- SERVICES ---8<---")
print(json.dumps(services, ensure_ascii=False))
