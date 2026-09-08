"""«Не задано» перестаёт выглядеть как Москва — DRF-1606.

У ``BotUser.timezone`` умолчанием стоял **настоящий часовой пояс**
(``Europe/Moscow``). Поэтому «никто никогда не выбирал» и «человек живёт
в Москве» хранились одной и той же строкой, а
``nutrition_proactive.prefs.resolve_timezone`` относил явный московский
ответ к «не задано» и проваливался на пояс тенанта.

Комментарий рядом с сентинелом говорил об этом прямо: «naive „is it
filled?“ check on the pilot reports 100% and means 0%».

### Почему backfill выполняется именно сейчас и безопасен

Осознанно заданного значения не существует **по построению**, а не
только по сегодняшнему срезу базы:

* единственный писатель колонки — ``identity.services.profile.update_profile``
  (``_EDITABLE_USER_FIELDS``); других присваиваний в ``apps/`` нет;
* единственный вход в него — ``PATCH /me``;
* **ни один клиентский экран его не шлёт** — слова ``timezone`` нет ни в
  ``customer-profile.ts``, ни в ``CustomerProfileScreen.tsx``.

Замер главного окна на пилоте 08.09.2026 (контейнер
``ayla-bot-staging-web-1``) это подтверждает срезом: ``TZ_DIST =
{'Europe/Moscow': 26}``, 26 из 26 на нетронутом умолчании.

**Окно для однозначного backfill закрывается вместе с DRF-1477.** Как
только экран научится слать пояс, первый же москвич запишет
``Europe/Moscow`` осознанно и станет неотличим от молчащих — и снять
неоднозначность будет уже нечем. Поэтому миграция едет первой, а экран
вторым.

### Обратный ход

Возвращает ``Europe/Moscow`` пустым строкам — то есть ровно то
состояние, в котором они были до миграции. Обратный ход честен, но
необратим по смыслу: различие, которое эта миграция вводит, он снова
теряет.
"""

from django.db import migrations, models

#: Значение, которое стояло умолчанием и играло роль «не выбирали».
_OLD_DEFAULT = "Europe/Moscow"


def unset_becomes_empty(apps, schema_editor):
    """``Europe/Moscow`` → ``""`` для всех строк.

    Затирать нечего: осознанно заданного значения в колонке быть не
    может — см. докстринг модуля.
    """
    BotUser = apps.get_model("identity", "BotUser")
    BotUser.objects.filter(timezone=_OLD_DEFAULT).update(timezone="")


def empty_becomes_the_old_default(apps, schema_editor):
    """Обратный ход: пустым строкам возвращается прежнее умолчание."""
    BotUser = apps.get_model("identity", "BotUser")
    BotUser.objects.filter(timezone="").update(timezone=_OLD_DEFAULT)


class Migration(migrations.Migration):
    dependencies = [
        ("identity", "0022_botuser_blocked_at_botuser_blocked_by_username_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="botuser",
            name="timezone",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "IANA-пояс человека для отрисовки времени в сообщениях. "
                    "ПУСТО означает «не задано» — и это единственное, что здесь "
                    "означает отсутствие ответа. Умолчанием стоял `Europe/Moscow` "
                    "(DRF-1606): настоящий пояс в роли «никто не выбирал», из-за "
                    "чего молчание 26 из 26 человек на пилоте было неотличимо от "
                    "осознанного выбора москвича. Кто читает пояс — "
                    "`apps.nutrition_proactive.prefs.resolve_timezone`; кто пишет — "
                    "только `apps.identity.services.profile.update_profile`."
                ),
                max_length=64,
            ),
        ),
        migrations.RunPython(unset_becomes_empty, empty_becomes_the_old_default),
    ]
