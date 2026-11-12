/**
 * Значок строки плана — общий для экрана «План» (DRF-2951) и блока «План на
 * сегодня» на Главной (DRF-2950): одно действие плана выглядит одинаково
 * везде, где стоит.
 */
import type { PlanLiteActionType } from "../../lib/plan-lite";

/** Значок строки плана: три действия Plan Lite и шаг нового плана. */
export type PlanMarkKind = "book" | "food" | "water" | "step";

export const ACTION_MARKS: Record<PlanLiteActionType, PlanMarkKind> = {
  book_service: "book",
  log_food: "food",
  log_water: "water",
};

/**
 * Рисунок значка. Линейный, цвет — `currentColor`. У шага нового плана типа
 * нет (каталог шлёт только подпись), поэтому значок у всех шагов один:
 * угадывать тип по словам подписи экран не вправе.
 */
export function PlanMarkIcon({ kind, size = 22 }: { kind: PlanMarkKind; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {kind === "book" && (
        <>
          <rect x="3.5" y="5" width="17" height="15.5" rx="3" />
          {/* Без галочки внутри: знак «сделано» экран плана не рисует. */}
          <path d="M3.5 10h17M8 3v4M16 3v4" />
        </>
      )}
      {kind === "food" && (
        <>
          <path d="M4 12h16a8 8 0 01-16 0z" />
          <path d="M9 4.5c0 1.2 1 1.6 1 2.8M13 4c0 1.4 1 1.8 1 3.3M8 20.5h8" />
        </>
      )}
      {kind === "water" && <path d="M12 3.5s6 6.2 6 10.5a6 6 0 01-12 0c0-4.3 6-10.5 6-10.5z" />}
      {kind === "step" && (
        <>
          <circle cx="12" cy="12" r="8.5" />
          <circle cx="12" cy="12" r="4" />
          <circle cx="12" cy="12" r="0.8" />
        </>
      )}
    </svg>
  );
}

/**
 * Значок в круге. Круг у всех действий одного цвета: в палитре есть только
 * акцент и статусные цвета, а зелёный, красный и жёлтый круг читались бы как
 * «сделано», «ошибка» и «внимание». Экран плана «сделано» не утверждает
 * вовсе; Главная утверждает (решение владельца 12.11, п.4 — «✓» у действий,
 * где сделано не меньше нужного) и рисует для этого свой круг с галочкой,
 * а не перекрашивает этот.
 */
export function PlanMark({ kind }: { kind: PlanMarkKind }) {
  return (
    <span className="plan-mark">
      <PlanMarkIcon kind={kind} />
    </span>
  );
}
