/**
 * C5 personal-data sheets (152-ФЗ, pilot 2026-08-15) — export + delete.
 *
 * Frozen contract: `PILOT_CONTRACTS_2026-08-15` §6. Replaces the deferred
 * Variant-3 support route (SupportEntrySheet) for the profile «Запросить
 * данные» / «Удалить аккаунт» CTAs now that W3 ships the aggregating
 * endpoints (`apps/miniapp_api`):
 *
 *   GET    /api/v1/customer/me/personal-data/export/  → JSON attachment
 *   POST   /api/v1/customer/me/deletion-request/      → {status:"accepted", request}
 *
 * DRF-1699 (§7 свода владельца, 11.09.2026): удаление — не синхронный
 * каскад, а ЗАЯВКА. Лист заводит `DeletionRequest` в каталоге и показывает
 * человеку, что запрос принят, точную крайнюю дату, номер и статус.
 * Стирание делает исполнитель на сервере (срез D3), не этот лист.
 *
 * DRF-1453 добавляет сюда третий лист — согласие на медданные
 * ({@link HealthConsentSheet}). Он живёт в этом же файле, а не рядом, чтобы
 * переиспользовать SheetChrome: у листов приватности одна механика фокуса,
 * Escape и focus-trap, и разводить её по двум местам — прямой путь к тому,
 * что однажды разойдётся именно в самом чувствительном.
 *
 * # Contract obligations implemented here
 *
 * - **UI idempotency** — while a request is in flight both actions are
 *   disabled and Escape/backdrop are ignored, so repeat taps never spawn
 *   repeat requests. Backend repeats stay safe regardless (C5.2).
 * - **Honest refusal** — the server says ONE thing about the data on
 *   failure: `not_started` (§7). No «partial»: the sheet never has to
 *   explain which half happened. Retry only when the server says it can
 *   help; support deeplink on every failure view (#949).
 * - **Retention boundary and the 30-day deadline** — the confirmation
 *   lists exactly what §7 lists: account off, profile / goals / plans /
 *   diaries / addresses / personal context erased, done within 30 days,
 *   bookings and payments possibly kept limited or anonymised where the
 *   law requires, irreversible once started. The former rule «no 30-day
 *   grace wording» (spec §14) is RETIRED by the owner's §7 decision of
 *   11.09.2026 — the decision is newer than the spec, and a screen that
 *   kept obeying the old rule would be wrong by the new one.
 *
 * # A11y (mirrors SupportEntrySheet, WCAG 2.2 AA)
 *
 * role=dialog + aria-modal + aria-labelledby; initial focus on Cancel —
 * never on the primary/destructive CTA; Tab focus trap across the
 * currently-enabled buttons/links; Escape closes (except mid-request);
 * backdrop click closes (same guard); focus restores to the opener.
 */

import { Fragment, useCallback, useEffect, useRef, useState } from "react";

import {
  DATA_STORAGE_PARTIAL_PROCESSING_NOTE,
  DATA_STORAGE_REVOCATION_DISCLOSURE_TEXT,
  DataStorageRevocationFailedError,
  revokeDataStorage,
  StaleDisclosureError as DataStorageStaleDisclosureError,
  SUPPORT_DEEPLINK,
  type ConsentsResponse,
} from "../lib/customer-profile";
import {
  DELETE_CONFIRMATION_TOKEN,
  DELETION_STATUS_LABELS,
  DeletionNotStartedError,
  exportPersonalData,
  formatDeadline,
  getCurrentDeletionRequest,
  requestAccountDeletion,
  triggerDownload,
  type DeletionRequestInfo,
} from "../lib/personal-data";
import {
  grantHealthConsent,
  HEALTH_CONSENT_DOCUMENT_VERSION,
  StaleDisclosureError,
  withdrawHealthConsent,
  type HealthConsentState,
} from "../lib/health-consent";
import { useSheetKeyNav } from "../hooks/useSheetKeyNav";

// ---------------------------------------------------------------------------
// Shared sheet chrome (internal — the two sheets below are the public API).
// ---------------------------------------------------------------------------

interface SheetChromeProps {
  headlineId: string;
  headline: string;
  /** True while a request is in flight: blocks Escape/backdrop/close. */
  closeDisabled: boolean;
  triggerRef: React.RefObject<HTMLElement>;
  onClose: () => void;
  children: React.ReactNode;
}

/**
 * Общая обёртка листа: заголовок, ловушка фокуса, возврат фокуса на
 * триггер, закрытие по Esc.
 *
 * Экспортируется (DRF-1477), потому что лист часового пояса —
 * `TimezoneSheet.tsx` — обязан вести себя так же. Скопировать эту
 * обёртку значило бы завести вторую реализацию ловушки фокуса и второе
 * поведение Esc: они разойдутся не сразу и молча, а починят их порознь.
 */
export function SheetChrome({
  headlineId,
  headline,
  closeDisabled,
  triggerRef,
  onClose,
  children,
}: SheetChromeProps) {
  const dialogRef = useRef<HTMLDivElement | null>(null);

  const close = useCallback(() => {
    if (closeDisabled) return;
    onClose();
    if (triggerRef.current) {
      try {
        triggerRef.current.focus();
      } catch {
        /* opener may have unmounted */
      }
    }
  }, [closeDisabled, onClose, triggerRef]);

  // Initial focus lands on the element marked data-initial-focus (the
  // Cancel button) — WCAG 2.5.5 / spec §13.5: the primary CTA is never
  // auto-focused. Runs once per mount; per-view re-render keeps focus.
  useEffect(() => {
    const dialog = dialogRef.current;
    const initial = dialog?.querySelector<HTMLElement>("[data-initial-focus]");
    (initial ?? dialog?.querySelector("button"))?.focus();
  }, []);

  // Escape + Tab trap — shared hook (#953).
  useSheetKeyNav(dialogRef, { onClose: close, closeDisabled });

  return (
    <div
      className="profile-support-backdrop"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <div
        ref={dialogRef}
        className="profile-support-sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby={headlineId}
      >
        <h2 id={headlineId} className="profile-support-sheet__headline">
          {headline}
        </h2>
        {children}
      </div>
    </div>
  );
}

interface SheetProps {
  /** Render the sheet when true; parent holds visibility state. */
  open: boolean;
  /** Opener element — focus restores here on close. */
  triggerRef: React.RefObject<HTMLElement>;
  onClose: () => void;
}

function SupportLink() {
  return (
    <a
      href={SUPPORT_DEEPLINK}
      target="_blank"
      rel="noopener noreferrer"
      className="btn-secondary"
    >
      Написать в поддержку
    </a>
  );
}

// ---------------------------------------------------------------------------
// C5.1 — Export
// ---------------------------------------------------------------------------

type ExportView = "confirm" | "busy" | "done" | "error";

export function PersonalDataExportSheet({ open, triggerRef, onClose }: SheetProps) {
  const [view, setView] = useState<ExportView>("confirm");

  // Reset to the confirm view each time the sheet is (re)opened.
  useEffect(() => {
    if (open) setView("confirm");
  }, [open]);

  const start = useCallback(async () => {
    setView((v) => (v === "busy" ? v : "busy"));
    try {
      const file = await exportPersonalData();
      triggerDownload(file.blob, file.filename);
      setView("done");
    } catch {
      setView("error");
    }
  }, []);

  if (!open) return null;
  const busy = view === "busy";

  return (
    <SheetChrome
      headlineId="personal-data-export-headline"
      headline="Скачать мои данные"
      closeDisabled={busy}
      triggerRef={triggerRef}
      onClose={onClose}
    >
      {view === "confirm" && (
        <>
          <p className="profile-support-sheet__body">
            Соберу в один файл (JSON): твой профиль, что{" "}
            <span lang="en">Ayla</span> помнит, и твои согласия. Файл
            скачается на устройство — дальше он только в твоих руках.
          </p>
          <div className="profile-support-sheet__actions">
            <button
              type="button"
              data-initial-focus
              className="btn-secondary profile-support-sheet__cancel"
              onClick={onClose}
            >
              Отмена
            </button>
            <button
              type="button"
              className="btn-primary profile-support-sheet__primary"
              onClick={start}
            >
              Скачать данные
            </button>
          </div>
        </>
      )}
      {view === "busy" && (
        <>
          <p className="profile-support-sheet__body">Готовлю файл…</p>
          <div className="profile-support-sheet__actions">
            <button type="button" disabled className="btn-secondary">
              Отмена
            </button>
            <button type="button" disabled className="btn-primary">
              Готовлю файл…
            </button>
          </div>
        </>
      )}
      {view === "done" && (
        <>
          <p className="profile-support-sheet__body">
            Файл скачан. Если не видишь его в загрузках — напиши в
            поддержку, поможем.
          </p>
          <div className="profile-support-sheet__actions">
            <button
              type="button"
              className="btn-primary profile-support-sheet__primary"
              onClick={onClose}
            >
              Закрыть
            </button>
          </div>
        </>
      )}
      {view === "error" && (
        <>
          <p className="profile-support-sheet__body">
            Не получилось подготовить файл. Попробуй ещё раз — если снова
            не выйдет, напиши в поддержку, мы подготовим данные вручную.
          </p>
          <div className="profile-support-sheet__actions">
            <button
              type="button"
              className="btn-secondary profile-support-sheet__cancel"
              onClick={onClose}
            >
              Отмена
            </button>
            <button
              type="button"
              className="btn-primary profile-support-sheet__primary"
              onClick={start}
            >
              Попробовать ещё раз
            </button>
            <SupportLink />
          </div>
        </>
      )}
    </SheetChrome>
  );
}

// ---------------------------------------------------------------------------
// DRF-1699 — Заявка на удаление аккаунта и личных данных (§7)
// ---------------------------------------------------------------------------

type DeleteView =
  | "confirm"
  | "busy"
  | "accepted"
  // Сервер сказал единственное, что вправе сказать при отказе: удаление
  // НЕ НАЧАЛОСЬ. Данные в прежнем состоянии. Повтор — только если сервер
  // сказал, что он поможет.
  | "not_started"
  | "error";

/**
 * Пять пунктов подтверждения — §7 свода дословно, ни одним меньше и ни
 * одним больше. Экспортированы, чтобы тест сверял состав, а не угадывал
 * по обрывкам фраз.
 */
export const DELETION_CONFIRMATION_POINTS: readonly string[] = [
  "аккаунт будет отключён;",
  "профиль, цели, планы, дневники, адреса и персональный контекст будут удалены;",
  "срок завершения — не позднее 30 дней;",
  "состоявшиеся записи и оплаты могут сохраняться ограниченно или обезличенно, если этого требует закон;",
  "после начала удаления действие нельзя отменить.",
];

/** «Принято» — с датой, номером и статусом словами; сырые слаги не выходят. */
export function DeletionRequestSummary({
  request,
  created,
}: {
  request: DeletionRequestInfo;
  created: boolean;
}) {
  return (
    <>
      <p className="profile-support-sheet__body">
        {created ? "Запрос принят." : "Запрос уже был принят раньше."} Удаление будет
        завершено не позднее <b>{formatDeadline(request.deadline_at)}</b>.
      </p>
      <p className="profile-support-sheet__body">
        Номер запроса: <code>{request.request_id}</code>
        <br />
        Статус: {DELETION_STATUS_LABELS[request.status] ?? "в работе"}
      </p>
    </>
  );
}

/**
 * Строка состояния в профиле: у человека уже есть заявка → он видит номер,
 * срок и статус при каждом заходе, а не только в момент нажатия (§7:
 * «текущий статус»). Ничего нет или не удалось прочитать — ничего не
 * рисуется: отсутствие строки — не ошибка экрана.
 */
export function DeletionRequestStatus() {
  const [request, setRequest] = useState<DeletionRequestInfo | null>(null);

  useEffect(() => {
    let alive = true;
    getCurrentDeletionRequest()
      .then((r) => {
        if (alive) setRequest(r);
      })
      .catch(() => {
        /* профиль без сведений о заявке — не ошибка экрана */
      });
    return () => {
      alive = false;
    };
  }, []);

  if (!request) return null;
  return (
    <div className="profile-section__caption" role="status" data-testid="deletion-request-status">
      <DeletionRequestSummary request={request} created={false} />
    </div>
  );
}

export function PersonalDataDeleteSheet({ open, triggerRef, onClose }: SheetProps) {
  const [view, setView] = useState<DeleteView>("confirm");
  const [typed, setTyped] = useState("");
  const [accepted, setAccepted] = useState<{
    request: DeletionRequestInfo;
    created: boolean;
  } | null>(null);
  const [refusal, setRefusal] = useState<DeletionNotStartedError | null>(null);

  useEffect(() => {
    if (open) {
      setView("confirm");
      setTyped("");
      setAccepted(null);
      setRefusal(null);
    }
  }, [open]);

  // The server verifies this token too (400 confirmation_mismatch), so the
  // input is real evidence of intent, not decoration.
  const confirmed = typed.trim() === DELETE_CONFIRMATION_TOKEN;

  const start = useCallback(async () => {
    if (!confirmed) return;
    setView("busy");
    try {
      const result = await requestAccountDeletion(typed.trim());
      setAccepted(result);
      setView("accepted");
    } catch (err) {
      if (err instanceof DeletionNotStartedError) {
        setRefusal(err);
        setView("not_started");
      } else {
        setView("error");
      }
    }
  }, [confirmed, typed]);

  if (!open) return null;
  const busy = view === "busy";

  return (
    <SheetChrome
      headlineId="personal-data-delete-headline"
      headline="Удалить аккаунт и личные данные?"
      closeDisabled={busy}
      triggerRef={triggerRef}
      onClose={onClose}
    >
      {view === "confirm" && (
        <>
          <p className="profile-support-sheet__body">Что произойдёт:</p>
          <ul className="profile-support-sheet__list">
            {DELETION_CONFIRMATION_POINTS.map((point) => (
              <li key={point}>{point}</li>
            ))}
          </ul>
          <label
            className="profile-support-sheet__body"
            htmlFor="personal-data-delete-confirm"
          >
            Чтобы подтвердить, введи{" "}
            <b>{DELETE_CONFIRMATION_TOKEN}</b>:
          </label>
          <input
            id="personal-data-delete-confirm"
            type="text"
            className="profile-support-sheet__input"
            value={typed}
            autoComplete="off"
            autoCorrect="off"
            spellCheck={false}
            aria-describedby="personal-data-delete-headline"
            onChange={(e) => setTyped(e.target.value)}
          />
          <div className="profile-support-sheet__actions">
            <button
              type="button"
              data-initial-focus
              className="btn-secondary profile-support-sheet__cancel"
              onClick={onClose}
            >
              Отмена
            </button>
            <button
              type="button"
              className="btn-primary profile-support-sheet__primary"
              disabled={!confirmed}
              onClick={start}
            >
              Удалить аккаунт
            </button>
          </div>
        </>
      )}
      {view === "busy" && (
        <>
          <p className="profile-support-sheet__body">Отправляю запрос…</p>
          <div className="profile-support-sheet__actions">
            <button type="button" disabled className="btn-secondary">
              Отмена
            </button>
            <button type="button" disabled className="btn-primary">
              Отправляю…
            </button>
          </div>
        </>
      )}
      {view === "accepted" && accepted && (
        <>
          <DeletionRequestSummary request={accepted.request} created={accepted.created} />
          <p className="profile-support-sheet__body">
            Персонализация и новая обработка твоих данных прекращаются
            сразу. Отменить удаление нельзя.
          </p>
          <div className="profile-support-sheet__actions">
            <button
              type="button"
              className="btn-primary profile-support-sheet__primary"
              onClick={onClose}
            >
              Закрыть
            </button>
          </div>
        </>
      )}
      {view === "not_started" && (
        <>
          <p className="profile-support-sheet__body">
            Удаление не началось. Твои данные в прежнем состоянии — ничего
            не удалено и не изменено.
          </p>
          <p className="profile-support-sheet__body">
            {refusal?.retryable
              ? "Попробуй ещё раз. Если снова не выйдет — напиши в поддержку."
              : "Повторная попытка здесь не поможет — напиши в поддержку, мы примем запрос вручную."}
          </p>
          <div className="profile-support-sheet__actions">
            <button
              type="button"
              className="btn-secondary profile-support-sheet__cancel"
              onClick={onClose}
            >
              Отмена
            </button>
            {refusal?.retryable && (
              <button
                type="button"
                className="btn-primary profile-support-sheet__primary"
                onClick={start}
              >
                Попробовать ещё раз
              </button>
            )}
            <SupportLink />
          </div>
        </>
      )}
      {view === "error" && (
        <>
          <p className="profile-support-sheet__body">
            Удаление не началось: не удалось отправить запрос. Попробуй ещё
            раз — если снова не выйдет, напиши в поддержку.
          </p>
          <div className="profile-support-sheet__actions">
            <button
              type="button"
              className="btn-secondary profile-support-sheet__cancel"
              onClick={onClose}
            >
              Отмена
            </button>
            <button
              type="button"
              className="btn-primary profile-support-sheet__primary"
              onClick={start}
            >
              Попробовать ещё раз
            </button>
            <SupportLink />
          </div>
        </>
      )}
    </SheetChrome>
  );
}

// ---------------------------------------------------------------------------
// Health-data consent (152-ФЗ ст. 10) — DRF-1453
//
// Питание — специальная категория. Ст. 10 ч. 1 п. 1 допускает обработку по
// согласию, но согласие на специальную категорию не поглощается общим
// согласием ст. 6 — значит ни тумблера «заодно», ни строки в списке
// «принимаю всё». Отсюда лист: человек читает, что именно передаётся, зачем
// и что будет при отзыве, и подтверждает отдельным действием.
//
// Три обязательства, которые лист держит:
//
// * раскрытие перед подтверждением — CTA лежит ПОД перечнем, а не над ним,
//   и перечень не сворачивается: согласие подписывают после текста;
// * версия — выдача уходит с HEALTH_CONSENT_DOCUMENT_VERSION, то есть в
//   журнал попадает то, что человеку показали. Если сервер тем временем
//   обновил раскрытие (409 stale_disclosure), лист НЕ дожимает выдачу, а
//   честно говорит, что текст изменился и его надо перечитать;
// * симметрия — отзыв живёт в том же листе и тем же весом: разрешение,
//   которое легко дать и трудно снять, согласием не является.
//
// Chrome, фокус и Escape — общий SheetChrome выше по файлу (то же поведение,
// что у C5-листов: initial focus на «Отмена», не на CTA).
// ---------------------------------------------------------------------------

type HealthConsentView = "confirm" | "busy" | "stale" | "error";

interface HealthConsentSheetProps extends SheetProps {
  /** Текущее состояние согласия — определяет, выдача это или отзыв. */
  granted: boolean;
  /** Успешная запись: экран обновляет строку согласия и показывает снекбар. */
  onSettled: (next: HealthConsentState) => void;
}

/** Что именно уходит в обработку. Формулировки — по факту, без обещаний. */
const HEALTH_DATA_SCOPE: readonly string[] = [
  "Что ты записываешь в дневник питания: блюда, порции, время",
  "Недельная картина по белку, воде и целям — в сводном виде",
];

const HEALTH_DATA_PURPOSE: readonly string[] = [
  "Ayla учитывает питание в разговоре и в подсказках",
  "Без этого разрешения дневник остаётся у тебя, а в разговоре не участвует",
];

export function HealthConsentSheet({
  open,
  triggerRef,
  onClose,
  granted,
  onSettled,
}: HealthConsentSheetProps) {
  const [view, setView] = useState<HealthConsentView>("confirm");

  useEffect(() => {
    if (open) setView("confirm");
  }, [open]);

  const submit = useCallback(async () => {
    setView("busy");
    try {
      const next = granted
        ? await withdrawHealthConsent()
        : await grantHealthConsent(HEALTH_CONSENT_DOCUMENT_VERSION);
      onSettled(next);
      onClose();
    } catch (err) {
      // Раскрытие успели обновить — предлагать «ещё раз» было бы враньём:
      // повтор отправит ту же устаревшую версию и получит тот же отказ.
      setView(err instanceof StaleDisclosureError ? "stale" : "error");
    }
  }, [granted, onClose, onSettled]);

  if (!open) return null;
  const busy = view === "busy";

  return (
    <SheetChrome
      headlineId="health-consent-headline"
      headline={granted ? "Отозвать разрешение" : "Разрешить учитывать питание"}
      closeDisabled={busy}
      triggerRef={triggerRef}
      onClose={onClose}
    >
      {view === "confirm" && (
        <>
          {granted ? (
            <>
              <p className="profile-support-sheet__body">
                <span lang="en">Ayla</span> перестанет учитывать питание в
                разговоре. Дневник и записи в нём останутся — их видишь только
                ты.
              </p>
              <p className="profile-support-sheet__body">
                Разрешить снова можно в любой момент здесь же.
              </p>
            </>
          ) : (
            <>
              <p className="profile-support-sheet__body">
                Данные о питании закон относит к особой категории, поэтому
                разрешение на них отдельное — и его не бывает «заодно» с
                остальными.
              </p>
              <p className="profile-support-sheet__sub-heading">
                Что передаётся:
              </p>
              <ul className="profile-support-sheet__list">
                {HEALTH_DATA_SCOPE.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
              <p className="profile-support-sheet__sub-heading">Зачем:</p>
              <ul className="profile-support-sheet__list">
                {HEALTH_DATA_PURPOSE.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
              <p className="profile-support-sheet__body">
                Отозвать можно в любой момент — здесь же, одним действием.
              </p>
            </>
          )}
          <div className="profile-support-sheet__actions">
            <button
              type="button"
              data-initial-focus
              className="btn-secondary profile-support-sheet__cancel"
              onClick={onClose}
            >
              Отмена
            </button>
            <button
              type="button"
              className="btn-primary profile-support-sheet__primary"
              onClick={submit}
            >
              {granted ? "Отозвать" : "Разрешить"}
            </button>
          </div>
        </>
      )}
      {view === "busy" && (
        <>
          <p className="profile-support-sheet__body">Сохраняю…</p>
          <div className="profile-support-sheet__actions">
            <button type="button" disabled className="btn-secondary">
              Отмена
            </button>
            <button type="button" disabled className="btn-primary">
              {granted ? "Отозвать" : "Разрешить"}
            </button>
          </div>
        </>
      )}
      {view === "stale" && (
        <>
          <p className="profile-support-sheet__body">
            Текст про данные обновился, пока лист был открыт. Открой его заново
            и прочитай — разрешение записывается на тот текст, который ты
            видела.
          </p>
          <div className="profile-support-sheet__actions">
            <button
              type="button"
              data-initial-focus
              className="btn-primary profile-support-sheet__primary"
              onClick={onClose}
            >
              Понятно
            </button>
          </div>
        </>
      )}
      {view === "error" && (
        <>
          <p className="profile-support-sheet__body">
            Не получилось сохранить. Ничего не изменилось.
          </p>
          <div className="profile-support-sheet__actions">
            <button
              type="button"
              data-initial-focus
              className="btn-secondary profile-support-sheet__cancel"
              onClick={onClose}
            >
              Закрыть
            </button>
            <button
              type="button"
              className="btn-primary profile-support-sheet__primary"
              onClick={submit}
            >
              Попробовать ещё раз
            </button>
            <SupportLink />
          </div>
        </>
      )}
    </SheetChrome>
  );
}

// ---------------------------------------------------------------------------
// Отзыв согласия на хранение данных (§35 п.6-п.9, п.16) — DRF-1475
//
// Не тумблер. Отзыв согласия необратим по последствиям, и переключатель,
// который снимает его одним касанием, показал бы последствия ПОСЛЕ
// действия — то есть никогда. Поэтому тот же жанр, что у соседей по
// файлу: строка `ConsentRow variant="action"` ведёт в лист, человек
// читает утверждённый текст последствий и подтверждает отдельным
// нажатием.
//
// Три вещи, которые лист держит и которые легко потерять:
//
// * текст последствий — УТВЕРЖДЁН ВЛАДЕЛЬЦЕМ ДОСЛОВНО (§35 п.7) и живёт
//   одной константой в `lib/customer-profile.ts`. Здесь он только
//   рисуется; слова не меняются. Разметка не текст: `Ayla` оборачивается
//   в lang="en" (WCAG 3.1.1), состав и порядок слов остаются те же;
// * версия раскрытия — ИЗ ОТВЕТА СЕРВЕРА, не из константы на клиенте.
//   Смысл проверки в том, что человек нажал под тем текстом, который
//   сервер считает актуальным. На 409 лист не «дожимает» отзыв тем же
//   телом, а просит перечитать раскрытие (и экран его перечитывает);
// * §35 п.16 — при `revoked_partial_processing` лист говорит ТОЛЬКО
//   «Согласие отозвано» и не делает ни одного утверждения о полноте
//   удаления. Ни «всё удалено», ни «часть данных осталась»: второе тоже
//   формулировка, которой у нас нет. Место для будущей —
//   `DATA_STORAGE_PARTIAL_PROCESSING_NOTE` с TODO(Q-CLIENT-03).
//
// §35 п.6: отзыв НЕ закрывает аккаунт. Об этом сказано и в строке
// профиля, и здесь — рядом с «Удалить аккаунт» два действия не должны
// сливаться ни визуально, ни словами.
// ---------------------------------------------------------------------------

type DataStorageRevokeView =
  | "confirm"
  | "busy"
  // Отзыв состоялся полностью.
  | "revoked"
  // Отзыв состоялся, часть обработки накопленного не отработала.
  | "partial"
  // 409: сервер обновил текст последствий — повтор тем же телом не пройдёт.
  | "stale"
  // 502: не состоялся сам отзыв, согласие осталось действующим.
  | "failed"
  // Ответа не было или он не про отзыв — исход неизвестен, и так и сказано.
  | "unknown";

interface DataStorageRevokeSheetProps extends SheetProps {
  /** Версия раскрытия из последнего ответа сервера. Не константа клиента. */
  disclosureVersion: string;
  /** Токен подтверждения — общий с C5-удалением, второй копии нет. */
  confirmationToken: string;
  /** Отзыв состоялся: экран перечитывает состояние из ЭТОГО ответа. */
  onRevoked: (next: ConsentsResponse) => void;
  /** 409: экран обязан перечитать `me/consents/` и показать раскрытие заново. */
  onStaleDisclosure: () => void;
}

/**
 * Утверждённый текст последствий. Слова берутся из константы как есть;
 * единственное, что добавляет эта функция, — разметка языка для `Ayla`.
 */
function ApprovedRevocationDisclosure() {
  const parts = DATA_STORAGE_REVOCATION_DISCLOSURE_TEXT.split("Ayla");
  return (
    <p className="profile-support-sheet__body">
      {parts.map((part, i) => (
        <Fragment key={i}>
          {i > 0 && <span lang="en">Ayla</span>}
          {part}
        </Fragment>
      ))}
    </p>
  );
}

export function DataStorageRevokeSheet({
  open,
  triggerRef,
  onClose,
  disclosureVersion,
  confirmationToken,
  onRevoked,
  onStaleDisclosure,
}: DataStorageRevokeSheetProps) {
  const [view, setView] = useState<DataStorageRevokeView>("confirm");

  useEffect(() => {
    if (open) setView("confirm");
  }, [open]);

  const submit = useCallback(async () => {
    setView("busy");
    try {
      const result = await revokeDataStorage(
        confirmationToken,
        disclosureVersion,
      );
      // §35 п.9: состояние берётся из ответа сервера, а не достраивается
      // из решения. Что сервер сказал, то экран и покажет.
      onRevoked(result.consents);
      setView(result.status === "revoked" ? "revoked" : "partial");
    } catch (err) {
      if (err instanceof DataStorageStaleDisclosureError) {
        onStaleDisclosure();
        setView("stale");
        return;
      }
      setView(
        err instanceof DataStorageRevocationFailedError ? "failed" : "unknown",
      );
    }
  }, [confirmationToken, disclosureVersion, onRevoked, onStaleDisclosure]);

  if (!open) return null;
  const busy = view === "busy";

  return (
    <SheetChrome
      headlineId="data-storage-revoke-headline"
      headline="Отозвать согласие на хранение данных?"
      closeDisabled={busy}
      triggerRef={triggerRef}
      onClose={onClose}
    >
      {view === "confirm" && (
        <>
          <ApprovedRevocationDisclosure />
          <p className="profile-support-sheet__body">
            Это не удаление аккаунта. Аккаунт останется, записаться снова
            можно будет как обычно. Удалить аккаунт — отдельное действие в
            профиле.
          </p>
          <div className="profile-support-sheet__actions">
            <button
              type="button"
              data-initial-focus
              className="btn-secondary profile-support-sheet__cancel"
              onClick={onClose}
            >
              Не отзывать
            </button>
            <button
              type="button"
              className="btn-primary profile-support-sheet__primary"
              onClick={submit}
            >
              Отозвать согласие
            </button>
          </div>
        </>
      )}
      {view === "busy" && (
        <>
          <p className="profile-support-sheet__body">Отзываю…</p>
          <div className="profile-support-sheet__actions">
            <button type="button" disabled className="btn-secondary">
              Не отзывать
            </button>
            <button type="button" disabled className="btn-primary">
              Отзываю…
            </button>
          </div>
        </>
      )}
      {view === "revoked" && (
        <>
          <p className="profile-support-sheet__body">
            Согласие отозвано. Данные, которые можно удалить, удалены.
          </p>
          <div className="profile-support-sheet__actions">
            <button
              type="button"
              className="btn-primary profile-support-sheet__primary"
              onClick={onClose}
            >
              Закрыть
            </button>
          </div>
        </>
      )}
      {view === "partial" && (
        <>
          {/* §35 п.16: одно проверенное утверждение и ни одного лишнего.
              Про возможное сохранение части сведений человек прочитал в
              утверждённом тексте до нажатия. */}
          <p className="profile-support-sheet__body">Согласие отозвано.</p>
          {DATA_STORAGE_PARTIAL_PROCESSING_NOTE && (
            <p className="profile-support-sheet__body">
              {DATA_STORAGE_PARTIAL_PROCESSING_NOTE}
            </p>
          )}
          <div className="profile-support-sheet__actions">
            <button
              type="button"
              className="btn-primary profile-support-sheet__primary"
              onClick={onClose}
            >
              Закрыть
            </button>
          </div>
        </>
      )}
      {view === "stale" && (
        <>
          <p className="profile-support-sheet__body">
            Текст про последствия обновился, пока лист был открыт. Открой
            его заново и прочитай — отзыв записывается на тот текст,
            который ты видела.
          </p>
          <div className="profile-support-sheet__actions">
            <button
              type="button"
              data-initial-focus
              className="btn-primary profile-support-sheet__primary"
              onClick={onClose}
            >
              Понятно
            </button>
          </div>
        </>
      )}
      {view === "failed" && (
        <>
          <p className="profile-support-sheet__body">
            Не получилось отозвать согласие. Оно осталось действующим.
          </p>
          <div className="profile-support-sheet__actions">
            <button
              type="button"
              data-initial-focus
              className="btn-secondary profile-support-sheet__cancel"
              onClick={onClose}
            >
              Закрыть
            </button>
            <button
              type="button"
              className="btn-primary profile-support-sheet__primary"
              onClick={submit}
            >
              Попробовать ещё раз
            </button>
            <SupportLink />
          </div>
        </>
      )}
      {view === "unknown" && (
        <>
          <p className="profile-support-sheet__body">
            Не получилось дозвониться до сервера, и я не знаю, дошёл ли
            отзыв. Открой профиль заново и посмотри строку «Хранение
            данных» — там будет текущее состояние. Повторный отзыв
            безопасен.
          </p>
          <div className="profile-support-sheet__actions">
            <button
              type="button"
              data-initial-focus
              className="btn-secondary profile-support-sheet__cancel"
              onClick={onClose}
            >
              Закрыть
            </button>
            <button
              type="button"
              className="btn-primary profile-support-sheet__primary"
              onClick={submit}
            >
              Попробовать ещё раз
            </button>
            <SupportLink />
          </div>
        </>
      )}
    </SheetChrome>
  );
}
