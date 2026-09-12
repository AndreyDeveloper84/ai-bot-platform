/**
 * Экран 01 — «[имя], всё готово» (DRF-1807, M15; макет FINAL FREEZE §7).
 *
 * Читает ОДНУ ручку — `GET /api/v1/master/onboarding/readiness` (M2) — и
 * рисует чек-лист «Осталось настроить» ровно по её пунктам. Экран не
 * решает, что настроено: это проекция по доменным фактам на сервере,
 * состояние онбординга не хранится нигде, и «продолжить позже» — просто
 * уход с экрана; возврат считается заново из тех же фактов.
 *
 * Три честности пункта (контракт M2):
 * - `done` / `missing` — факт: ✓ либо «не настроено», тап ведёт по `deep_link`;
 * - `unknown` — канон не ответил: «не удалось прочитать», а НЕ «настройте»
 *   (человеку, который настроил расписание, это была бы ложь);
 * - `unavailable` — возможности ещё нет: пункт не рисуется вовсе.
 *
 * Бар — от числа закрытых пунктов, без процентов и без «N из M» (макет:
 * «no fake percent complete»). Связь личности — отдельная строка: это
 * условие публикации, не настройки (ruling 6); до LINKED профиль остаётся
 * черновиком, и слово «опубликован» здесь не звучит (§16, §20).
 *
 * Нижней навигации на этом экране нет (макет §7).
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { DelayedSkeleton, ServiceCardSkeleton } from "../components/Skeleton";
import { StateError } from "../components/StateError";
import {
  drawnReadinessItems,
  getMasterMe,
  getOnboardingReadiness,
  readinessFill,
  type OnboardingReadiness,
  type ReadinessItem,
} from "../lib/master-api";
import { setBackButton, signalReady } from "../lib/max-sdk";

export const SETUP_ROUTE = "/solo/setup";
export const HOME_ROUTE = "/solo/my-day";

/** Подписи пунктов — по макету 1.1; профиль — по контракту readiness (D8: место решает дизайнер). */
export const READINESS_ITEM_LABELS: Record<string, string> = {
  services: "Услуги и цены",
  location: "Место работы",
  hours: "Расписание",
  profile: "Профиль для клиентов",
};

export const ITEM_STATE_TEXT = {
  done: "Настроено",
  missing: "Не настроено",
  unknown: "Не удалось прочитать",
} as const;

export const SETUP_LEAD = "Ваше рабочее пространство уже создано.";
export const SETUP_EXPLAIN = "Теперь подготовим профиль, чтобы клиенты могли записываться к вам.";
export const SETUP_RESUME_NOTE = "Настройку можно прервать и продолжить позже. Всё сохранится.";
export const START_LABEL = "Начать настройку";
export const CONTINUE_LABEL = "Продолжить настройку";
export const LATER_LABEL = "Продолжить позже";
export const ALL_DONE_TITLE = "Всё настроено";
export const IDENTITY_PENDING_NOTE = "Подтверждение личности — ожидает оператора.";
export const IDENTITY_UNLINKED_NOTE =
  "Отправить профиль на проверку можно будет после подтверждения личности.";
export const IDENTITY_REJECTED_NOTE = "Подтверждение личности отклонено — напишите в поддержку.";

type Phase =
  | { kind: "loading" }
  | { kind: "error"; err: unknown }
  | { kind: "ready"; readiness: OnboardingReadiness; name: string };

export function itemLabel(item: ReadinessItem): string {
  return READINESS_ITEM_LABELS[item.key] ?? item.key;
}

/** Первый незакрытый пункт, с которого начинается настройка. */
export function firstOpenItem(items: ReadinessItem[]): ReadinessItem | null {
  return drawnReadinessItems(items).find((item) => item.state !== "done") ?? null;
}

export function identityNote(state: string): string | null {
  if (state === "pending") return IDENTITY_PENDING_NOTE;
  if (state === "rejected") return IDENTITY_REJECTED_NOTE;
  if (state === "unlinked") return IDENTITY_UNLINKED_NOTE;
  return null;
}

export function MasterSetupLandingScreen() {
  const navigate = useNavigate();
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });

  useEffect(() => {
    setBackButton(false);
    signalReady();
  }, []);

  const load = async () => {
    setPhase({ kind: "loading" });
    try {
      const [readiness, me] = await Promise.all([
        getOnboardingReadiness(),
        // Имя — для приветствия; без него экран всё равно рисуется.
        getMasterMe().catch(() => null),
      ]);
      setPhase({ kind: "ready", readiness, name: me?.master.name?.trim() ?? "" });
    } catch (err) {
      setPhase({ kind: "error", err });
    }
  };

  useEffect(() => {
    void load();
  }, []);

  if (phase.kind === "loading") {
    return (
      <main className="screen setup-landing">
        <DelayedSkeleton loading>
          <ServiceCardSkeleton />
          <ServiceCardSkeleton />
          <ServiceCardSkeleton />
        </DelayedSkeleton>
      </main>
    );
  }

  if (phase.kind === "error") {
    return (
      <main className="screen setup-landing">
        <StateError err={phase.err} onRetry={() => void load()} />
      </main>
    );
  }

  const { readiness, name } = phase;
  const items = drawnReadinessItems(readiness.items);
  const fill = readinessFill(readiness.items);
  const next = firstOpenItem(readiness.items);
  const note = identityNote(readiness.identity.state);
  const greeting = name ? `${name}, всё готово 👋` : "Всё готово 👋";

  return (
    <main className="screen setup-landing" aria-labelledby="setup-landing-title">
      <h1 id="setup-landing-title" className="setup-landing__title">
        {readiness.ready ? ALL_DONE_TITLE : greeting}
      </h1>
      {!readiness.ready && (
        <>
          <p className="setup-landing__lead">{SETUP_LEAD}</p>
          <p className="setup-landing__lead">{SETUP_EXPLAIN}</p>
        </>
      )}

      <div
        className="setup-landing__bar"
        role="progressbar"
        aria-label="Готовность настройки"
        aria-valuemin={0}
        aria-valuemax={fill.total}
        aria-valuenow={fill.done}
        data-testid="setup-bar"
      >
        <div
          className="setup-landing__bar-fill"
          style={{ width: fill.total ? `${(fill.done / fill.total) * 100}%` : "0%" }}
        />
      </div>

      <h2 className="setup-landing__section-title">Осталось настроить</h2>
      <ul className="setup-landing__list" aria-label="Осталось настроить">
        {items.map((item) => (
          <li key={item.key} className={`setup-landing__item setup-landing__item--${item.state}`}>
            <ItemRow item={item} onOpen={() => navigate(item.deep_link)} />
          </li>
        ))}
      </ul>

      {note && (
        <p className="setup-landing__identity" data-testid="setup-identity">
          {note}
        </p>
      )}

      <p className="setup-landing__note">{SETUP_RESUME_NOTE}</p>

      <div className="setup-landing__actions">
        {next ? (
          <button
            type="button"
            className="btn-primary"
            onClick={() => navigate(next.deep_link)}
          >
            {fill.done > 0 ? CONTINUE_LABEL : START_LABEL}
          </button>
        ) : null}
        <button
          type="button"
          className={next ? "btn-secondary" : "btn-primary"}
          onClick={() => navigate(HOME_ROUTE)}
        >
          {next ? LATER_LABEL : "Открыть кабинет"}
        </button>
      </div>
    </main>
  );
}

function ItemRow({ item, onOpen }: { item: ReadinessItem; onOpen: () => void }) {
  const label = itemLabel(item);
  if (item.state === "unknown") {
    // Канон не ответил — это не «не настроено», и вести настраивать нельзя.
    return (
      <div className="setup-landing__row" data-testid={`setup-item-${item.key}`}>
        <span className="setup-landing__mark" aria-hidden="true">
          ?
        </span>
        <span className="setup-landing__label">{label}</span>
        <span className="setup-landing__state">{ITEM_STATE_TEXT.unknown}</span>
      </div>
    );
  }
  const done = item.state === "done";
  return (
    <button
      type="button"
      className="setup-landing__row setup-landing__row--tappable"
      data-testid={`setup-item-${item.key}`}
      onClick={onOpen}
    >
      <span className="setup-landing__mark" aria-hidden="true">
        {done ? "✓" : "○"}
      </span>
      <span className="setup-landing__label">{label}</span>
      <span className="setup-landing__state">
        {done ? ITEM_STATE_TEXT.done : ITEM_STATE_TEXT.missing}
      </span>
    </button>
  );
}
