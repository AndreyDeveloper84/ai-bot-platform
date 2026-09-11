/**
 * Customer profile screen — Tier 1 Priority 6 Phase B (deferred scope).
 *
 * Route: `/customer/profile`
 *
 * Spec: `docs/screens/customer-profile-flow.md` (deferred Variant 3,
 * post commit `376784e`). Sections R1-R6; R2 export/delete are wired
 * to the C5 152-ФЗ endpoints since pilot phase 2a (PILOT_CONTRACTS
 * §6); only R3 memory stays deferred per §0 «Pilot scope & backend
 * reality» recon.
 *
 * # Section order (per spec §11.1 selected variant)
 *   R1 — header (avatar initials fallback + name; handle/scope rows
 *        render only when the real /me provides them)
 *   R2 — consent: 2 locked rows + marketing toggle (реестр
 *        `ConsentRecord(MARKETING)`, не зеркало `notify_promo`)
 *        + health-consent row + «Хранение данных» (сценарий отзыва
 *        с подтверждением, НЕ тумблер) + §4.2 accordion
 *        + «Запросить данные» / «Удалить аккаунт» → C5 sheets
 *        (PersonalDataSheets.tsx; support deeplink = error fallback)
 *   R3 — memory transparency: coming-soon card (no data, no clear)
 *   R4 — «Подсказки от Ayla»: тумблер на `me/consents/proactive-hints/`
 *   R5 — notifications: MAX channel + soft timing + entry → support
 *   R6 — states: loading skeleton / API-down with retry / offline
 *        banner / toggle-change snackbar
 *
 * # WCAG 2.2 AA inline (per spec §13 — 12 patterns)
 *   2.5.8 — all interactive ≥44dp
 *   1.4.3 — body text uses `--c-text-primary`; muted via secondary
 *   1.3.1 — `<dl>` for consent rows + role=switch on toggles
 *   2.4.3 — vertical R1→R6 focus order; row order within R2
 *   2.4.1 — skip link to R2 («К управлению приватностью»)
 *   1.4.4 — em-sized body text; resize tolerant via tokens
 *   2.3.3 — shimmer + chevron rotate gated on prefers-reduced-motion
 *   4.1.3 — toggle change announces via aria-live (Snackbar)
 *   3.1.1 — lang="ru" inherited; `Ayla` wrapped lang="en" in copy
 *   4.1.2 — explicit aria-label on every toggle
 *   2.5.5 — support sheets confirm before destructive deeplink
 *   3.3.4 — sheet primary CTA NOT auto-focused
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ComingSoonCard } from "../components/ComingSoonCard";
import { ConsentRow } from "../components/ConsentRow";
import { DisclosureSheet } from "../components/DisclosureSheet";
import { TimezoneSheet, zoneLabel } from "../components/TimezoneSheet";
import { NotificationCard } from "../components/NotificationCard";
import {
  DataStorageRevokeSheet,
  HealthConsentSheet,
  PersonalDataDeleteSheet,
  PersonalDataExportSheet,
} from "../components/PersonalDataSheets";
import { Skeleton } from "../components/Skeleton";
import { Snackbar } from "../components/Snackbar";
import { StateError } from "../components/StateError";
import {
  additionalSalonsLabel,
  avatarInitials,
  fetchConsents,
  fetchMe,
  formatConsentDate,
  setMarketingConsent,
  saveTimezone,
  setProactiveOptOut,
  type ConsentsResponse,
  type MeProfileResponse,
} from "../lib/customer-profile";
import { DELETE_CONFIRMATION_TOKEN } from "../lib/personal-data";
import {
  fetchHealthConsent,
  type HealthConsentState,
} from "../lib/health-consent";
import { SurfaceSwitchButton } from "../components/SurfaceSwitch";
import { useScreenBack } from "../hooks/useScreenBack";
import { backTo } from "../lib/screen-back";

// ---------------------------------------------------------------------------
// Реальные данные (DRF-1475 §24, DRF-1520). Экран целиком стоит на
// настоящих ручках: R1 — `/customer/me`, R2 и R4 — `me/consents/`.
//
// 05.09 владелец решил не показывать неработающие тумблеры, и две
// секции были убраны из рендера: тумблер, который человек двигает, а он
// ничего не делает, врёт про наличие контроля. DRF-1520 дал ручки —
// секции возвращаются РАБОТАЮЩИМИ, а не «скоро».
//
// Две вещи, которые здесь легко перепутать и нельзя:
//
// * «Хранение данных» — НЕ тумблер. Отзыв согласия необратим по
//   последствиям, и переключатель показал бы их после действия. Строка
//   `ConsentRow variant="action"` ведёт в лист с утверждённым текстом
//   (§35 п.7), как это уже сделано медданным;
// * отзыв согласия ≠ удаление аккаунта (§35 п.6). Аккаунт и доступ к
//   записям остаются. «Удалить аккаунт» — отдельное действие ниже, и
//   оба места говорят об этом словами, а не рассчитывают на догадку.
//
// Офлайн: баннер честный И действия выключены (правило #1421 — «баннер
// честный, а кнопки живые» было дефектом).
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Loading / error / offline state machine.
// ---------------------------------------------------------------------------

type Status =
  | { kind: "loading" }
  | { kind: "error"; err: unknown }
  | {
      kind: "ready";
      me: MeProfileResponse;
      consents: ConsentsResponse;
    };

interface ToastState {
  visible: boolean;
  message: string;
}

/**
 * Состояние согласия на хранение данных словами — тот же разбор, что у
 * медданных (`healthStatusText` ниже): ни один исход не притворяется
 * другим. Дата берётся из `granted_at` реестра; её отсутствие при
 * действующем согласии — не повод выдумать дату и не повод сказать
 * «не разрешено».
 */
function dataStorageStatusText(consents: ConsentsResponse): string {
  if (!consents.data_storage_granted) return "Не разрешено";
  return consents.data_storage_consent_at
    ? `Разрешено ${formatConsentDate(consents.data_storage_consent_at)}`
    : "Разрешено";
}

const EMPTY_TOAST: ToastState = { visible: false, message: "" };

export function CustomerProfileScreen() {
  const navigate = useNavigate();

  // Возврат (DRF-1493) — на дом клиентской поверхности.
  //
  // Профиль — вкладка со своей нижней навигацией, и стрелка у него
  // была и раньше. DRF-1493 не снимает существующие органы
  // управления, а доводит их до работающего состояния: стрелка
  // остаётся, но ведёт в заданное место, а не в историю, которой у
  // пришедшего по deep link нет. Нужна ли вкладке стрелка вообще —
  // вопрос раскладки поверхности, он у DRF-1481.
  const onBack = useScreenBack(backTo("/customer/main"));
  const [status, setStatus] = useState<Status>({ kind: "loading" });
  const [offline, setOffline] = useState<boolean>(
    typeof navigator !== "undefined" ? !navigator.onLine : false,
  );
  const [toast, setToast] = useState<ToastState>(EMPTY_TOAST);
  const [marketingBusy, setMarketingBusy] = useState(false);
  const [hintsBusy, setHintsBusy] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [storageOpen, setStorageOpen] = useState(false);
  const exportTriggerRef = useRef<HTMLButtonElement | null>(null);
  const deleteTriggerRef = useRef<HTMLButtonElement | null>(null);
  const storageTriggerRef = useRef<HTMLButtonElement | null>(null);
  // Согласие на медданные (DRF-1453) — РЕАЛЬНЫЙ эндпоинт, у него свой
  // ресурс вне общего `Status` и своя загрузка: строка обязана быть
  // видна в проде всегда. Пока состояние неизвестно (`null`) строка
  // показывает «загружаю» и не кликается — «Не разрешено» до ответа
  // сервера было бы утверждением, которого мы не проверяли.
  const [healthConsent, setHealthConsent] = useState<HealthConsentState | null>(null);
  const [healthFailed, setHealthFailed] = useState(false);
  const [healthSheetOpen, setHealthSheetOpen] = useState(false);
  const [tzSheetOpen, setTzSheetOpen] = useState(false);
  const tzTriggerRef = useRef<HTMLButtonElement | null>(null);
  const healthTriggerRef = useRef<HTMLButtonElement | null>(null);

  const load = useCallback(async () => {
    setStatus({ kind: "loading" });
    try {
      const [me, consents] = await Promise.all([fetchMe(), fetchConsents()]);
      setStatus({ kind: "ready", me, consents });
    } catch (err) {
      setStatus({ kind: "error", err });
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const loadHealthConsent = useCallback(async () => {
    setHealthFailed(false);
    try {
      setHealthConsent(await fetchHealthConsent());
    } catch {
      // Читать не смогли — строка честно говорит об этом и предлагает
      // повторить. Показать «Не разрешено» было бы удобнее и неверно.
      setHealthConsent(null);
      setHealthFailed(true);
    }
  }, []);

  useEffect(() => {
    loadHealthConsent();
  }, [loadHealthConsent]);

  // Состояние словами. Три исхода, и ни один не притворяется другим:
  // неизвестно / не смогли прочитать / известное да-нет с датой выдачи.
  const healthStatusText = healthFailed
    ? "Не удалось загрузить"
    : healthConsent === null
      ? "Загружаю"
      : healthConsent.granted
        ? `Разрешено ${
            healthConsent.granted_at
              ? formatConsentDate(healthConsent.granted_at)
              : ""
          }`.trim()
        : "Не разрешено";

  // Подпись кнопки обязана совпадать с тем, что нажатие делает. Когда
  // состояние прочитать не удалось, кнопка повторяет чтение — обещать
  // «Разрешить» в этот момент значило бы врать о результате нажатия.
  const healthActionLabel = healthFailed
    ? "Повторить"
    : healthConsent?.granted
      ? "Отозвать"
      : "Разрешить";
  const healthActionAriaLabel = healthFailed
    ? "Повторить загрузку разрешения на данные о питании"
    : healthConsent?.granted
      ? "Отозвать разрешение учитывать питание"
      : "Разрешить учитывать питание";

  /**
   * Записать пояс и обновить экран ТЕМ, ЧТО ОТВЕТИЛ СЕРВЕР.
   *
   * Не тем, что человек нажал: сервер проверяет значение (DRF-1477) и
   * он же — источник правды. Показать нажатое, не дождавшись ответа,
   * значило бы завести на экране второе состояние того же поля.
   *
   * Ошибку НЕ глотаем — её показывает сам лист, и человек видит, что
   * пояс остался прежним, а не уходит уверенным в обратном.
   */
  const onTimezoneSaved = useCallback(async (zone: string) => {
    const saved = await saveTimezone(zone);
    setStatus((prev) =>
      prev.kind === "ready" ? { ...prev, me: { ...prev.me, timezone: saved } } : prev,
    );
    setToast({
      visible: true,
      message: `Запомнила: ${zoneLabel(saved)}. Изменить можно здесь же.`,
    });
  }, []);

  const onHealthSettled = useCallback((next: HealthConsentState) => {
    setHealthConsent(next);
    setToast({
      visible: true,
      message: next.granted
        ? "Хорошо, буду учитывать питание. Отозвать можно в профиле."
        : "Разрешение отозвано. Питание в разговоре не участвует.",
    });
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const on = () => setOffline(false);
    const off = () => setOffline(true);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => {
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
    };
  }, []);

  // Functional setState pattern (adversarial CR P4 — race protection).
  // Stale-closure `setStatus({ ...status, ... })` could overwrite a
  // sibling toggle update if both writes are in flight; functional
  // form reads the freshest state at apply time.
  const onMarketingToggle = useCallback(async (next: boolean) => {
    setMarketingBusy(true);
    try {
      const updated = await setMarketingConsent(next);
      setStatus((s) =>
        s.kind === "ready" ? { ...s, consents: updated } : s,
      );
      setToast({
        visible: true,
        message: next
          ? "Хорошо, иногда буду показывать предложения от салонов."
          : "Понятно, предложений от салонов больше не будет.",
      });
    } catch {
      setToast({
        visible: true,
        message: "Не получилось сохранить. Попробуй ещё раз.",
      });
    } finally {
      setMarketingBusy(false);
    }
  }, []);

  // «Подсказки от Ayla». Контракт клиента говорит в терминах opt-out,
  // экран — в терминах «включено»; инверсия одна и лежит в lib.
  const onHintsToggle = useCallback(async (next: boolean) => {
    setHintsBusy(true);
    try {
      const updated = await setProactiveOptOut(!next);
      const enabled = !updated.proactive_messages_opt_out;
      setStatus((s) =>
        s.kind === "ready"
          ? { ...s, consents: { ...s.consents, proactive_hints_enabled: enabled } }
          : s,
      );
      setToast({
        visible: true,
        message: enabled
          ? "Хорошо, иногда буду писать первой."
          : "Поняла, первой писать не буду.",
      });
    } catch {
      setToast({
        visible: true,
        message: "Не получилось сохранить. Попробуй ещё раз.",
      });
    } finally {
      setHintsBusy(false);
    }
  }, []);

  // Отзыв состоялся. Состояние берётся из ответа сервера целиком —
  // включая «Подсказки» (§35 п.9): экран показывает то, что сказал
  // сервер, а не то, чего требует решение.
  const onStorageRevoked = useCallback((next: ConsentsResponse) => {
    setStatus((s) => (s.kind === "ready" ? { ...s, consents: next } : s));
  }, []);

  // 409 stale_disclosure — тело чинить нечего. Перечитываем состояние,
  // чтобы следующее открытие листа показало актуальное раскрытие.
  const onStorageStaleDisclosure = useCallback(async () => {
    try {
      const fresh = await fetchConsents();
      setStatus((s) => (s.kind === "ready" ? { ...s, consents: fresh } : s));
    } catch {
      // Перечитать не вышло — строка остаётся прежней, а лист уже
      // сказал, что текст обновился. Достраивать состояние нечем.
    }
  }, []);

  return (
    <div className="profile-screen">
      {/* Skip-link renders only when R2 target is in the DOM (ready
          state). During loading / error the anchor doesn't exist, so
          activating the link is a no-op confusing keyboard users
          (adversarial CR P5). */}
      {status.kind === "ready" && (
        <a className="records-screen__skip-link" href="#profile-r2-anchor">
          К управлению приватностью
        </a>
      )}

      {/*
        Путь назад из клиентской поверхности. Без него владелец,
        переключившийся «посмотреть глазами клиента», остаётся здесь
        навсегда — то есть меняет одну ловушку на другую.

        Кнопка сама себя не рисует для человека с одной ролью:
        обычному клиенту переключать нечего.
      */}
      <SurfaceSwitchButton />

      <header className="records-screen__header">
        <button
          type="button"
          className="records-screen__back"
          aria-label="Назад"
          onClick={onBack}
        >
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
            <path
              d="M12 4l-6 6 6 6"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
        <h1 className="records-screen__title">Профиль</h1>
      </header>

      {offline && (
        <div className="records-screen__offline-banner" role="status">
          Профиль может быть устаревшим. Проверь подключение.
        </div>
      )}

      <main className="profile-screen__main" id="profile-main">
        {status.kind === "loading" && <LoadingSkeleton />}
        {status.kind === "error" && (
          <div className="profile-screen__error">
            <StateError err={status.err} onRetry={load} />
          </div>
        )}
        {status.kind === "ready" && (
          <>
            {/* R1 — Header (реальный /customer/me, во всех сборках) */}
            <ProfileHeader me={status.me} />

            {/* R2 — Consent & Privacy */}
            <section
              className="profile-section"
              aria-labelledby="profile-r2-h2"
              id="profile-r2-anchor"
              tabIndex={-1}
            >
              <h2 id="profile-r2-h2" className="profile-section__heading">
                Согласия и приватность
              </h2>
              <dl className="profile-consent-list">
                <ConsentRow
                  variant="info"
                  title="Данные для записи"
                  statusText="Включено · нужно для записи"
                  description={
                    <>
                      Нужно, чтобы записывать тебя на услуги и показывать
                      мастеру детали визита.
                    </>
                  }
                />
                <ConsentRow
                  variant="info"
                  title="Данные для мастера"
                  statusText="Включено · нужно для проведения услуги"
                  description={
                    <>
                      Мастер видит: имя, услугу, время, заметку к записи
                      (если оставила). Мастер не видит память{" "}
                      <span lang="en">Ayla</span>, wellness-данные и личные
                      заметки.
                    </>
                  }
                />
                <ConsentRow
                  variant="toggle"
                  title="Акции и предложения"
                  ariaLabel="Получать акции и предложения от салонов"
                  checked={status.consents.marketing_consent}
                  busy={marketingBusy || offline}
                  onChange={onMarketingToggle}
                  description={
                    <>
                      Иногда салоны делятся специальными предложениями. По
                      умолчанию выключено.
                    </>
                  }
                />
                {/* Хранение данных (DRF-1475 §24, ручка DRF-1520).
                    Дата — `granted_at` реестра, а не `BotUser.consent_at`:
                    ту приветственный поток ставит, а отзыв не снимает.
                    Пустой `granted_at` — не пробел, а отсутствие
                    действующей выдачи, и строка говорит именно это. */}
                {status.consents.data_storage_granted ? (
                  <ConsentRow
                    variant="action"
                    title="Хранение данных"
                    statusText={dataStorageStatusText(status.consents)}
                    actionLabel="Отозвать"
                    actionAriaLabel="Отозвать согласие на хранение данных"
                    busy={offline}
                    triggerRef={storageTriggerRef}
                    onAction={() => setStorageOpen(true)}
                    description={
                      <>
                        Согласие, на котором <span lang="en">Ayla</span>{" "}
                        хранит и использует твои данные. Отозвать можно
                        здесь: аккаунт и доступ к записям останутся.
                        Удаление аккаунта — отдельное действие ниже.
                      </>
                    }
                  />
                ) : (
                  /* Согласия нет — и кнопки нет: выдача через эту ручку
                     не проходит (сервер принимает только отзыв), а
                     кнопка «Разрешить» обещала бы то, чего экран
                     сделать не может. */
                  <ConsentRow
                    variant="info"
                    title="Хранение данных"
                    statusText="Не разрешено"
                    description={
                      <>
                        Согласие на хранение данных сейчас не действует.{" "}
                        <span lang="en">Ayla</span> не сохраняет и не
                        использует данные по нему. Аккаунт и доступ к
                        записям при этом остались.
                      </>
                    }
                  />
                )}
                {/* Медданные — реальный эндпоинт, видно во всех сборках.
                    Вариант "action", а не тумблер: особая категория по
                    152-ФЗ ст. 10 не переключается одним касанием мимо
                    текста согласия (см. HealthConsentSheet). */}
                <ConsentRow
                  variant="action"
                  title="Питание и здоровье"
                  statusText={healthStatusText}
                  actionLabel={healthActionLabel}
                  actionAriaLabel={healthActionAriaLabel}
                  busy={healthConsent === null && !healthFailed}
                  triggerRef={healthTriggerRef}
                  onAction={
                    healthFailed
                      ? loadHealthConsent
                      : () => setHealthSheetOpen(true)
                  }
                  description={
                    <>
                      Отдельное разрешение: данные о питании закон относит к
                      особой категории. Без него{" "}
                      <span lang="en">Ayla</span> не учитывает дневник питания
                      в разговоре. Отозвать можно в любой момент.
                    </>
                  }
                />
              </dl>
              <p className="profile-section__caption">
                Твои данные защищены. Здесь можно посмотреть, что хранится,
                скачать копию своих данных или удалить их — прямо в
                приложении.
              </p>
              <DisclosureSheet />
              <div className="profile-section__cta-row">
                <button
                  ref={exportTriggerRef}
                  type="button"
                  className="btn-secondary"
                  onClick={() => setExportOpen(true)}
                >
                  Запросить данные
                </button>
                <button
                  ref={deleteTriggerRef}
                  type="button"
                  className="btn-secondary profile-section__cta--cautious"
                  onClick={() => setDeleteOpen(true)}
                >
                  Удалить аккаунт
                </button>
              </div>
            </section>

            {/* R3 — Memory transparency (deferred) */}
            {/* Часовой пояс (DRF-1477). Своя секция, а не строка среди
                согласий: пояс — не согласие, и складывать их вместе
                значило бы предложить человеку «разрешить» своё
                местоположение во времени.

                Рядом на этом экране — имя, согласия и отзыв хранения.
                Ничего про здоровье и питание здесь нет: пояс к особой
                категории не относится, и подмешивать соседей нельзя. */}
            <section
              className="profile-section"
              aria-labelledby="profile-tz-h2"
            >
              <h2 id="profile-tz-h2" className="profile-section__heading">
                Часовой пояс
              </h2>
              <dl className="profile-consent-list">
                <ConsentRow
                  variant="action"
                  title="Мой пояс"
                  statusText={
                    status.me.timezone
                      ? zoneLabel(status.me.timezone)
                      : "Не задан"
                  }
                  actionLabel={status.me.timezone ? "Изменить" : "Указать"}
                  actionAriaLabel={
                    status.me.timezone
                      ? `Изменить часовой пояс, сейчас ${zoneLabel(status.me.timezone)}`
                      : "Указать часовой пояс"
                  }
                  description={
                    <>
                      По нему <span lang="en">Ayla</span> считает время в
                      напоминаниях. Пока пояс не задан, время считается по
                      салону, в который ты записываешься.
                    </>
                  }
                  onAction={() => setTzSheetOpen(true)}
                  triggerRef={tzTriggerRef}
                />
              </dl>
            </section>

            <section
              className="profile-section"
              aria-labelledby="profile-r3-h2"
            >
              <h2 id="profile-r3-h2" className="profile-section__heading">
                Что <span lang="en">Ayla</span> помнит
              </h2>
              <ComingSoonCard />
            </section>

            {/* R4 — «Подсказки от Ayla» (ручка DRF-1520). Это не
                ConsentRecord, а колонка `proactive_messages_opt_out`,
                которую планировщики читают первой проверкой: до
                DRF-1520 бот решал, писать ли первым, состоянием,
                которого человек не видел и изменить не мог. */}
            <section
              className="profile-section"
              aria-labelledby="profile-r4-h2"
            >
              <h2 id="profile-r4-h2" className="profile-section__heading">
                Подсказки от <span lang="en">Ayla</span>
              </h2>
              <dl className="profile-consent-list">
                <ConsentRow
                  variant="toggle"
                  title="Подсказки от Ayla"
                  ariaLabel="Получать подсказки от Ayla"
                  checked={status.consents.proactive_hints_enabled}
                  busy={hintsBusy || offline}
                  onChange={onHintsToggle}
                  description={
                    <>
                      Иногда <span lang="en">Ayla</span> напишет первой —
                      напомнит про уход или подскажет, когда пора
                      повторить. Напоминания о твоих записях приходят
                      отдельно и от этого тумблера не зависят.
                    </>
                  }
                />
              </dl>
            </section>

            {/* Cards (C7.2 skeleton) — real screen, honest empty state
                until the W3 passthrough ships. */}
            <section
              className="profile-section"
              aria-labelledby="profile-cards-h2"
            >
              <h2 id="profile-cards-h2" className="profile-section__heading">
                Оплата
              </h2>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => navigate("/customer/cards")}
              >
                Мои карты
              </button>
            </section>

            {/* R5 — Notifications (real prefs screen, #948) */}
            <NotificationCard
              onOpenSettings={() =>
                navigate("/customer/notification-settings")
              }
            />
          </>
        )}
      </main>

      {/* Bottom nav — mirror records / wellness so the «Я» tab is selected. */}
      <nav className="wellness-dash__nav" aria-label="Основная навигация">
        <button
          type="button"
          className="wellness-dash__nav-tab"
          aria-label="Главная"
          onClick={() => navigate("/customer/main")}
        >
          <span className="wellness-dash__nav-icon" aria-hidden="true">
            🏠
          </span>
          <span className="wellness-dash__nav-label">Главная</span>
        </button>
        {/* Вкладки «День» здесь нет (DRF-1546): поверхности «День» не
            существует — её роль исполнял домашний экран, а он теперь
            «Главная». Кнопка вела бы на страницу с подсвеченной
            «Главной», то есть врала бы о том, куда ведёт. Возвращать
            вместе с самой поверхностью «День». */}
        <button
          type="button"
          className="wellness-dash__nav-tab"
          aria-label="Записи"
          onClick={() => navigate("/customer/records")}
        >
          <span className="wellness-dash__nav-icon" aria-hidden="true">
            📅
          </span>
          <span className="wellness-dash__nav-label">Записи</span>
        </button>
        <button
          type="button"
          className="wellness-dash__nav-tab"
          aria-label="Услуги"
          onClick={() => navigate("/customer/catalog")}
        >
          <span className="wellness-dash__nav-icon" aria-hidden="true">
            💅
          </span>
          <span className="wellness-dash__nav-label">Услуги</span>
        </button>
        <button
          type="button"
          className="wellness-dash__nav-tab wellness-dash__nav-tab--active"
          aria-current="page"
          aria-label="Я"
        >
          <span className="wellness-dash__nav-icon" aria-hidden="true">
            👤
          </span>
          <span className="wellness-dash__nav-label">Я</span>
        </button>
      </nav>

      {/* C5 export sheet (152-ФЗ) */}
      <HealthConsentSheet
        open={healthSheetOpen}
        triggerRef={healthTriggerRef}
        onClose={() => setHealthSheetOpen(false)}
        granted={healthConsent?.granted ?? false}
        onSettled={onHealthSettled}
      />
      <TimezoneSheet
        open={tzSheetOpen}
        triggerRef={tzTriggerRef}
        onClose={() => setTzSheetOpen(false)}
        current={status.kind === "ready" ? status.me.timezone : ""}
        onSave={onTimezoneSaved}
      />
      <PersonalDataExportSheet
        open={exportOpen}
        triggerRef={exportTriggerRef}
        onClose={() => setExportOpen(false)}
      />
      {/* C5 delete sheet (152-ФЗ) */}
      <PersonalDataDeleteSheet
        open={deleteOpen}
        triggerRef={deleteTriggerRef}
        onClose={() => setDeleteOpen(false)}
      />
      {/* Отзыв согласия на хранение данных (§35 п.6-п.9, п.16). Версия
          раскрытия — из ответа сервера, токен — общий с C5-удалением. */}
      <DataStorageRevokeSheet
        open={storageOpen && status.kind === "ready"}
        triggerRef={storageTriggerRef}
        onClose={() => setStorageOpen(false)}
        disclosureVersion={
          status.kind === "ready"
            ? status.consents.data_storage_disclosure_version
            : ""
        }
        confirmationToken={DELETE_CONFIRMATION_TOKEN}
        onRevoked={onStorageRevoked}
        onStaleDisclosure={onStorageStaleDisclosure}
      />

      {/* Toggle confirmation toast (R6 §8.3 + marketing change). */}
      <Snackbar
        visible={toast.visible}
        message={toast.message}
        durationMs={4000}
        onTimeout={() => setToast(EMPTY_TOAST)}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// R1 header card (sub-component kept in the same file — single use-site).
// ---------------------------------------------------------------------------

function ProfileHeader({ me }: { me: MeProfileResponse }) {
  const initials = avatarInitials(me.display_name);
  const tenants = me.tenant_names ?? [];
  const nearest = tenants[0] ?? "";
  const extra = Math.max(0, tenants.length - 1);
  const extraLabel = additionalSalonsLabel(extra);
  return (
    <section className="profile-header" aria-labelledby="profile-r1-h2">
      <h2 id="profile-r1-h2" className="visually-hidden">
        Профиль клиента
      </h2>
      <div className="profile-header__row">
        <div className="profile-header__avatar" aria-hidden="true">
          <span className="profile-header__initials">{initials}</span>
        </div>
        <div className="profile-header__text">
          <p className="profile-header__name">{me.display_name}</p>
          {/* Реальный /me не отдаёт MAX-хендл — строку не рисуем,
              пустой параграф был бы визуальным шумом. */}
          {me.max_handle && (
            <p className="profile-header__handle">{me.max_handle}</p>
          )}
          {nearest && (
            <p className="profile-header__scope">
              Клиент {nearest}
              {extraLabel ? ` ${extraLabel}` : ""}
            </p>
          )}
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Loading skeleton — matches the layout cadence (header card + 4 sections).
// Single shimmer surface respects `prefers-reduced-motion` via global CSS.
// ---------------------------------------------------------------------------

function LoadingSkeleton() {
  return (
    <div className="profile-screen__loading" aria-hidden="true">
      <div className="profile-header" aria-hidden="true">
        <div className="profile-header__row">
          <Skeleton width="56px" height="56px" radius="50%" />
          <div style={{ flex: 1 }}>
            <Skeleton width="50%" height="1.2em" />
            <div style={{ marginTop: "var(--s-1)" }}>
              <Skeleton width="35%" height="0.95em" />
            </div>
            <div style={{ marginTop: "var(--s-1)" }}>
              <Skeleton width="60%" height="0.9em" />
            </div>
          </div>
        </div>
      </div>
      {Array.from({ length: 4 }, (_, i) => (
        <div key={i} className="profile-section profile-section--skeleton">
          <Skeleton width="40%" height="1.1em" />
          <div style={{ marginTop: "var(--s-3)" }}>
            <Skeleton width="100%" height="0.95em" />
          </div>
          <div style={{ marginTop: "var(--s-1)" }}>
            <Skeleton width="80%" height="0.95em" />
          </div>
        </div>
      ))}
    </div>
  );
}
