/**
 * Collapsed «Подробнее о данных» disclosure — R2 §4.2.
 *
 * Spec: `docs/screens/customer-profile-flow.md` §4.2 (cross-border
 * disclosure) + §11.4 (placement: collapsed accordion picked over
 * top-warning + footer-fine-print).
 *
 * # Why accordion, not modal
 *
 * Founder rule per spec §11.4: a top-level warning «scares casual
 * customer»; a footer fine-print «fails 152-ФЗ disclosure spirit».
 * Collapsed «Подробнее» is accessible for those who care without
 * dominating the surface.
 *
 * # §4.2 wording status
 *
 * Draft text below is from spec §4.2 verbatim and carries the
 * LEGAL-REVIEW-REQUIRED flag (FOLLOW_UP P-6). The exact cross-border
 * wording (Anthropic mention + США clarification) MUST be reviewed by
 * legal/compliance before pilot ship. Frontend renders the draft to
 * unblock build; legal can swap copy without code changes by editing
 * this file as a single-string update.
 *
 * # Retention lines (issue #950)
 *
 * «Сообщения — 180 дней (потом анонимизируется)» REMOVED 2026-07-19:
 * the anonymiser job does not exist in the backend (verified by grep —
 * no anonymize/retention code), so the claim was unbacked. Re-add the
 * line only when the backend job ships; «Записи и оплаты — 7 лет» is
 * the statutory claim and stays.
 */

import { useId, useState } from "react";

const DRAFT_TEXT_PARAGRAPHS: readonly string[] = [
  "Основные данные хранятся на серверах в России (152-ФЗ).",
  "Для понимания твоих сообщений Ayla может использовать AI-обработку через внешних поставщиков (включая Anthropic). Передача защищена шифрованием.",
];

const DRAFT_NOT_DOING: readonly string[] = [
  "Не продаём данные третьим лицам",
  "Не используем для рекламы вне Ayla",
  "Не отдаём салонам без твоего разрешения",
];

const DRAFT_RETENTION: readonly string[] = [
  "Записи и оплаты — 7 лет (требование закона)",
];

export function DisclosureSheet() {
  const [expanded, setExpanded] = useState(false);
  const contentId = useId();
  const buttonId = useId();
  return (
    <section className="profile-disclosure">
      <button
        type="button"
        id={buttonId}
        className="profile-disclosure__trigger"
        aria-expanded={expanded}
        aria-controls={contentId}
        onClick={() => setExpanded((v) => !v)}
      >
        <span>Подробнее о данных</span>
        <span
          className={`profile-disclosure__chevron${
            expanded ? " profile-disclosure__chevron--up" : ""
          }`}
          aria-hidden="true"
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <path
              d="M4 6l4 4 4-4"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </span>
      </button>
      {expanded && (
        <div
          id={contentId}
          role="region"
          aria-labelledby={buttonId}
          className="profile-disclosure__body"
        >
          <h3 className="profile-disclosure__heading">
            Где и как обрабатываются данные
          </h3>
          {DRAFT_TEXT_PARAGRAPHS.map((p, idx) => (
            <p key={`p-${idx}`}>{p}</p>
          ))}
          <p className="profile-disclosure__sub-heading">
            Что мы не делаем:
          </p>
          <ul className="profile-disclosure__list">
            {DRAFT_NOT_DOING.map((item, idx) => (
              <li key={`nd-${idx}`}>{item}</li>
            ))}
          </ul>
          <p className="profile-disclosure__sub-heading">Сколько храним:</p>
          <ul className="profile-disclosure__list">
            {DRAFT_RETENTION.map((item, idx) => (
              <li key={`rt-${idx}`}>{item}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
