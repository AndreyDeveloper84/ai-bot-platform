"""Free a TEST account for another pilot check-through (B-R, DRF-1617).

    python manage.py reset_test_account --account max:83146139 --mode client-onboarding
    python manage.py reset_test_account --account max:83146139 --mode client-onboarding --apply

Without ``--apply`` the command only READS: it prints what a reset would
remove, relation by relation, zeros included, and what would block it. That
is what makes it runnable on the pilot under the «только чтение» rule — a
test in ``apps/identity/tests/test_account_reset.py`` captures the SQL and
refuses a single ``DELETE`` or ``UPDATE`` in the dry run.

``--apply`` is refused for any account not on ``ACCOUNT_RESET_ALLOWLIST``,
whatever else is passed. On the pilot that list is empty.

This is the BOT half. The catalog has its own ``reset_test_account`` with
the same discipline; there is no shared transaction between the two
databases and this command does not pretend there is. Run the catalog half
first: if it fails, the bot is still whole and the next ``/start`` is safe.
Bot-only reset was measured to leave the catalog proxy in place, so the
next ``/start`` resolves to the OLD catalog user — the exact half-reset
this command exists to prevent (measurement, 11.09).
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.identity.services import account_reset as reset


class Command(BaseCommand):
    help = "Free a test account for another check-through. Reads unless --apply."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--account",
            action="append",
            required=True,
            metavar="CHANNEL:ID",
            help="channel:channel_user_id, e.g. max:83146139. Repeatable.",
        )
        parser.add_argument(
            "--mode",
            required=True,
            choices=sorted(reset.MODES),
            help=" | ".join(f"{m.name} — {m.frees}" for m in reset.MODES.values()),
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually free the account. Without it nothing is written.",
        )

    def handle(self, *args, **options) -> None:
        mode = reset.MODES[options["mode"]]
        specs: list[str] = options["account"]
        for spec in specs:
            try:
                reset.parse_account(spec)
            except ValueError as exc:
                raise CommandError(str(exc)) from exc

        allowlist = reset.allowlist()
        self.stdout.write(f"режим        {mode.name} — {mode.frees}")
        self.stdout.write(
            f"allowlist    {len(allowlist)} аккаунт(ов)"
            + ("" if allowlist else " — ПУСТ: --apply откажет всем")
        )
        self.stdout.write(
            f"действие     {'ПРИМЕНЕНИЕ' if options['apply'] else 'сухой прогон, только чтение'}"
        )

        refused: list[str] = []
        for spec in specs:
            self.stdout.write("")
            self.stdout.write(f"=== {spec}")
            if options["apply"] and not reset.allowed(spec):
                self.stdout.write(
                    f"ОТКАЗ  {spec} нет в ACCOUNT_RESET_ALLOWLIST — попадание в список "
                    "отдельное действие, подтверждением не заменяется"
                )
                refused.append(spec)
                continue

            p = reset.plan(spec, mode.name)
            self._print_plan(p)
            if not p.bot_user_ids:
                continue
            if p.blockers:
                refused.append(spec)
                continue
            if not options["apply"]:
                continue

            report = reset.apply(spec, mode.name)
            if report.leftovers:
                self.stdout.write("ОТКАЗ  после удаления в базе осталось — транзакция откачена:")
                for lo in report.leftovers:
                    self.stdout.write(f"       {lo.label:60} {lo.rows}")
                refused.append(spec)
                continue
            self.stdout.write("удалено (по моделям):")
            for label, n in sorted(report.removed.items()):
                self.stdout.write(f"       {label:60} {n}")
            self.stdout.write(self.style.SUCCESS(f"ГОТОВО {spec}: {mode.frees}"))

        if refused:
            raise CommandError("отказано: " + ", ".join(refused))

    def _print_plan(self, p: reset.Plan) -> None:
        if not p.bot_user_ids:
            self.stdout.write("аккаунт не найден — 0 оболочек BotUser; освобождать нечего")
            return
        self.stdout.write(
            f"BotUser      {len(p.bot_user_ids)}: " + ", ".join(map(str, p.bot_user_ids))
        )
        self.stdout.write(
            f"ayla_user_id {len(p.ayla_user_ids)}: " + (", ".join(map(str, p.ayla_user_ids)) or "—")
        )
        self.stdout.write("")
        self.stdout.write("связи на BotUser (все, включая нулевые):")
        titles = {
            "cascade": "уходит",
            "set_null": "остаётся, указатель снимается",
            "dismantled": "разбирается по замыслу режима",
            "protect_empty": "PROTECT, 0 строк — не блокирует",
            "blocks": "БЛОКИРУЕТ",
        }
        for ln in p.lines:
            self.stdout.write(
                f"  {ln.on_delete:9} {ln.label:52} {ln.rows:6}  {titles[ln.disposition]}"
            )
            for model_label, n in sorted(ln.removes.items()):
                if ln.disposition == "blocks":
                    self.stdout.write(f"            держит: {model_label} {n}")
                elif model_label != ln.label.rsplit(".", 1)[0]:
                    self.stdout.write(f"            + {model_label} {n}")
        m = p.memory
        self.stdout.write(f"  {'—':9} {m.label:52} {m.rows:6}  {titles[m.disposition]}")
        for model_label, n in sorted(m.removes.items()):
            if model_label != "identity.UserPersonalContext":
                self.stdout.write(f"            + {model_label} {n}")

        self.stdout.write("")
        self.stdout.write(
            "остаётся по замыслу (не FK, в проверку полноты не входит — причина рядом):"
        )
        for k in p.kept:
            self.stdout.write(f"  {k.label:62} {k.rows:6}  {k.reason}")

        self.stdout.write("")
        if p.blockers:
            self.stdout.write(
                "ОТКАЗ  PROTECT-связи с данными, которые режим не разбирает — не обход, решение владельца:"
            )
            for ln in p.blockers:
                self.stdout.write(f"       {ln.label:52} {ln.rows}")
        else:
            self.stdout.write("блокеров нет")


__all__ = ["Command"]
