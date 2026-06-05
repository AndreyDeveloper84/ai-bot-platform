/**
 * Empty-state surfaces for Records screen.
 *
 * Spec: `docs/screens/customer-records-flow.md` §7 (R5) — TWO cases
 * per founder explicit wording:
 *
 *   7.1 «No bookings ever» — first-time user, both tabs empty
 *       Voice: «Пока записей нет. Можно найти услугу или спросить Ayla.»
 *       CTAs: [ Найти услугу ] [ Спросить Ayla ]
 *
 *   7.2 «No upcoming, has past» — empty upcoming tab while history has items
 *       Voice: «Ближайших записей нет. Можно повторить прошлую запись
 *               или найти новую услугу.»
 *       CTAs: [ Записаться ещё ] [ История ]
 *
 *   7.3 «No history, has upcoming» — fringe variant
 *       Voice: «История накопится после первых визитов.»
 *       (No CTA — friendly note only.)
 *
 * Voice rules: no exclamation marks, no selling tone (memory
 * `project_records_voice_principles`).
 */

interface Props {
  variant: "no_bookings_ever" | "no_upcoming_has_past" | "no_history_has_upcoming";
  onFindService?: () => void;
  onAskAyla?: () => void;
  onRepeatLast?: () => void;
  onGoToHistory?: () => void;
}

export function EmptyRecordsState({
  variant,
  onFindService,
  onAskAyla,
  onRepeatLast,
  onGoToHistory,
}: Props) {
  if (variant === "no_bookings_ever") {
    return (
      <div className="records-empty" role="status" aria-live="polite">
        <p className="records-empty__text">
          Пока записей нет. Можно найти услугу или спросить{" "}
          <span lang="en">Ayla</span>.
        </p>
        <div className="records-empty__ctas">
          {onFindService && (
            <button
              type="button"
              className="btn-secondary records-empty__cta-primary"
              onClick={onFindService}
            >
              Найти услугу
            </button>
          )}
          {onAskAyla && (
            <button
              type="button"
              className="btn-secondary"
              onClick={onAskAyla}
              aria-label="Спросить у Ayla"
            >
              Спросить <span lang="en">Ayla</span>
            </button>
          )}
        </div>
      </div>
    );
  }

  if (variant === "no_upcoming_has_past") {
    return (
      <div className="records-empty" role="status" aria-live="polite">
        <p className="records-empty__text">
          Ближайших записей нет. Можно повторить прошлую запись или найти
          новую услугу.
        </p>
        <div className="records-empty__ctas">
          {onRepeatLast && (
            <button
              type="button"
              className="btn-secondary records-empty__cta-primary"
              onClick={onRepeatLast}
            >
              Записаться ещё
            </button>
          )}
          {onGoToHistory && (
            <button
              type="button"
              className="btn-secondary"
              onClick={onGoToHistory}
            >
              История
            </button>
          )}
        </div>
      </div>
    );
  }

  // no_history_has_upcoming — friendly note only.
  return (
    <div className="records-empty" role="status" aria-live="polite">
      <p className="records-empty__text">
        История накопится после первых визитов.
      </p>
    </div>
  );
}
