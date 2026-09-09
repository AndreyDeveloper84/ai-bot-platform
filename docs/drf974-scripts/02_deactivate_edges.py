"""DRF-974 · ШАГ 2 — гашение лишних рёбер SpecialistService по матрице.

DRY-RUN ПО УМОЛЧАНИЮ. Ничего не пишет, пока не выставлен DRF974_APPLY=yes.

    # прогон вхолостую (безопасно):
    ssh taximeter@194.87.99.126 "docker exec -i dev-web-1 python manage.py shell" < 02_deactivate_edges.py

    # применение (ТОЛЬКО после GO владельца и после 01_backup.py):
    ssh taximeter@194.87.99.126 "docker exec -i -e DRF974_APPLY=yes dev-web-1 python manage.py shell" < 02_deactivate_edges.py

Инварианты, при нарушении которых скрипт ОТКАЗЫВАЕТСЯ писать:
  * каждая пара (мастер, услуга) матрицы резолвится ровно в одно ребро;
  * итоговое число активных рёбер совпадает с ожидаемым (84);
  * матрица + UNKNOWN покрывают ровно 58 услуг каталога.
"""
import os

from django.db import transaction

from services.models import SalonService, SpecialistService
from tenants.models import Tenant

APPLY = os.environ.get("DRF974_APPLY") == "yes"
EXPECTED_KEEP = 95   # 84 (REPLY №3) + 11 услуг лица за Ольгой (REPLY №4 п.2)
EXPECTED_TOTAL = 232

D = "522574ff-c325-4a43-8312-bdf0ec0d085f"  # Архипкин Денис
I = "d66b5a6f-1479-4ff1-9d94-aceef5e6a0df"  # Сазонова Инна
T = "fb3efd6b-3302-4614-a5f6-6bb62b36be2d"  # Татьяна Паламарчук
O = "a8f3608c-34d6-4085-a078-abbf613c9941"  # Тихонова Ольга
NAMES = {D: "Архипкин Денис", I: "Сазонова Инна", T: "Татьяна Паламарчук", O: "Тихонова Ольга"}
M = (D, I, T)  # массажисты

MATRIX = [
    ("Классический массаж", M, "CONFIRMED"),
    ("Классический массаж задней поверхности тела", M, "CONFIRMED"),
    ("Массаж в 4 руки", M, "INFERRED"),
    ("Парный массаж", M, "INFERRED"),
    ("Лимфодренажный массаж всего тела (60 минут)", M, "CONFIRMED"),
    ("Лимфодренажный массаж — снятие отёков (экспресс 30 минут)", M, "CONFIRMED"),
    ("Антицеллюлитный массаж", (D, T), "CONFIRMED"),
    ("Глубокая проработка проблемной зоны (60 минут)", M, "INFERRED"),
    ("Массаж ног — глубокое восстановление и лёгкость (60 минут)", M, "INFERRED"),
    ("Массаж ног — глубокое расслабление и лимфодренаж (45 минут)", M, "INFERRED"),
    ("Массаж ног — снятие усталости и отёков (30 минут)", M, "INFERRED"),
    ("Массаж спины премиум — максимальная проработка (60 минут)", M, "INFERRED"),
    ("Массаж спины — глубокая проработка (45 минут)", M, "INFERRED"),
    ("Массаж спины — снятие боли и зажимов (30 минут)", M, "INFERRED"),
    ("Массаж стоп", M, "INFERRED"),
    ("Массаж шейно-воротниковой зоны", M, "INFERRED"),
    ("Спортивный массаж", M, "INFERRED"),
    ("Биоэнергетический массаж", M, "INFERRED"),
    ("Биоэнергетический массаж детский", M, "INFERRED"),
    ("Спина без боли - комплекс массажа", M, "INFERRED"),
    ("VelaShape", (O,), "CONFIRMED"),
    ("УЗ-кавитация — 1 зона", (O,), "CONFIRMED"),
    ("Вибромассаж — Бёдра/ягодицы", (O,), "INFERRED"),
    ("Вибромассаж — Всё тело", (O,), "INFERRED"),
    ("Вибромассаж — Живот", (O,), "INFERRED"),
    ("Вибромассаж — Спина/руки", (O,), "INFERRED"),
    ("RF-лифтинг — Лицо/шея", (O,), "INFERRED"),
    ("RF-лифтинг — Лицо/шея/декольте", (O,), "INFERRED"),
    ("Бикини  по линии белья", (O,), "CONFIRMED"),
    ("Бикини глубокое", (O,), "CONFIRMED"),
    ("Бикини тотальное", (O,), "CONFIRMED"),
    ("Бёдра  полностью", (O,), "CONFIRMED"),
    ("Верхняя губа", (O,), "CONFIRMED"),
    ("Всё тело (руки полностью,ноги полностью,тотальное бикини,подмышки.", (O,), "CONFIRMED"),
    ("Голени с коленями", (O,), "CONFIRMED"),
    ("Лазерная эпиляция бёдер (передняя/задняя/боковая часть)", (O,), "CONFIRMED"),
    ("Лазерная эпиляция ног полностью", (O,), "CONFIRMED"),
    ("Лазерная эпиляция подбородка", (O,), "CONFIRMED"),
    ("Подмышки", (O,), "CONFIRMED"),
    ("Руки до локтя", (O,), "CONFIRMED"),
    ("Руки полностью", (O,), "CONFIRMED"),
    ("Ягодицы", (O,), "CONFIRMED"),
    ("Must Have (подмышки+ глубокое бикини)", (O,), "CONFIRMED"),
    ("Super (ноги полностью + глубокое бикини + подмышки)", (O,), "CONFIRMED"),
    ("Классика (голени с коленями + глубокое бикини + подмышки)", (O,), "CONFIRMED"),
    # --- Услуги лица → Ольга (REPLY №4 п.2: пятого мастера не заводим) -----
    ("Массаж лица пластический / скульптурный", (O,), "CONFIRMED"),
    ("Альгинатная маска", (O,), "CONFIRMED"),
    ("Карбокситерапия", (O,), "CONFIRMED"),
    ("Карбокситерапия+пилинг", (O,), "CONFIRMED"),
    ("Комбинированная чистка лица", (O,), "CONFIRMED"),
    ("УЗ-чистка лица", (O,), "CONFIRMED"),
    ("Чистка лица + пилинг", (O,), "CONFIRMED"),
    ("Пилинг AZELAIC PEEL", (O,), "CONFIRMED"),
    ("Пилинг FERULIC PEEL C+", (O,), "CONFIRMED"),
    ("Пилинг PROBIO PEEL", (O,), "CONFIRMED"),
    ("Пилинг Миндальный", (O,), "CONFIRMED"),
]

# Сознательно СНЯТЫ С ПРОДАЖИ (REPLY №4 п.3): остаются без активных рёбер.
# SalonService не трогаем — только рёбра.
WITHDRAWN = [
    'Комплекс "Гладкая кожа": антицеллюлитный массаж (45 мин) + VelaShape (60 минут)',
    'Комплекс "Лёгкие ноги": лимфодренажный массаж + вибромассаж (60 минут)',
]
UNKNOWN = WITHDRAWN

t = Tenant.objects.get(slug="formula-tela")
catalog = {s.name for s in SalonService.objects.filter(tenant=t)}
covered = {n for n, _, _ in MATRIX}

problems = []
if covered | set(UNKNOWN) != catalog:
    problems.append(f"матрица+UNKNOWN != каталог: лишние={sorted((covered | set(UNKNOWN)) - catalog)} "
                    f"непокрытые={sorted(catalog - (covered | set(UNKNOWN)))}")
if covered & set(UNKNOWN):
    problems.append(f"услуга и в матрице, и в UNKNOWN: {sorted(covered & set(UNKNOWN))}")

keep_ids = set()
for name, masters, _conf in MATRIX:
    for spec_id in masters:
        rows = list(SpecialistService.objects.filter(
            tenant=t, specialist_id=spec_id, salon_service__name=name))
        if len(rows) != 1:
            problems.append(f"пара ({NAMES[spec_id]}, {name!r}) → {len(rows)} рёбер, ожидалось 1")
            continue
        keep_ids.add(rows[0].pk)

total = SpecialistService.objects.filter(tenant=t).count()
if total != EXPECTED_TOTAL:
    problems.append(f"всего рёбер {total}, ожидалось {EXPECTED_TOTAL} — данные изменились, перепроверь матрицу")
if len(keep_ids) != EXPECTED_KEEP:
    problems.append(f"KEEP={len(keep_ids)}, ожидалось {EXPECTED_KEEP}")

drop_qs = SpecialistService.objects.filter(tenant=t, is_active=True).exclude(pk__in=keep_ids)
drop_n = drop_qs.count()

print(f"всего рёбер: {total} | KEEP: {len(keep_ids)} | к гашению: {drop_n}")
for spec_id, nm in NAMES.items():
    k = SpecialistService.objects.filter(tenant=t, specialist_id=spec_id, pk__in=keep_ids).count()
    print(f"   {nm:22s} остаётся активных: {k}")

if problems:
    print("\n!!! ПРОВЕРКИ НЕ ПРОЙДЕНЫ — записи не будет:")
    for p in problems:
        print("   -", p)
    raise SystemExit(1)

if not APPLY:
    print("\nDRY-RUN: ничего не записано. Для применения: -e DRF974_APPLY=yes (после GO и бэкапа).")
    raise SystemExit(0)

with transaction.atomic():
    changed = drop_qs.update(is_active=False)
    active_after = SpecialistService.objects.filter(tenant=t, is_active=True).count()
    if active_after != EXPECTED_KEEP:
        raise RuntimeError(f"после гашения активных {active_after}, ожидалось {EXPECTED_KEEP} — откат")
print(f"\nПРИМЕНЕНО: погашено {changed}, активных осталось {EXPECTED_KEEP}.")
