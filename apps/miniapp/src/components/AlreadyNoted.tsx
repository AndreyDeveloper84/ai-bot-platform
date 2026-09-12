/**
 * Блок «Уже учла» (DRF-1744) — что человек уже сказал в этом проходе.
 *
 * Макет C03 (DRF-1178): «человек видит, что его услышали и не заставляют
 * отвечать повторно»; любой показанный факт исправляем; блок
 * сворачивается по мере роста и не конкурирует с текущим вопросом;
 * технических формулировок («в памяти 17 фактов», confidence, labels)
 * нет.
 *
 * Компонент ничего не решает: строки приходят из документа
 * (`known.goal`, `known.anketa`), «Изменить» есть ровно там, где сервер
 * сказал `revisable`. Единственное, что здесь считается, — как показать
 * список по его длине, и это три состояния из макета:
 *
 *   ≤ 3 строк   — подробно, каждая со своим действием;
 *   4–6 строк   — первые две и «+N», раскрывается;
 *   ≥ 7 строк   — одна сводка «N ответов · Посмотреть», раскрывается.
 *
 * Пороги — константы ниже; порядок строк — порядок документа.
 */
import { useState } from "react";

import type { KnownAnketaAnswer } from "../lib/customer-goals";

export const ALREADY_NOTED_TITLE = "Уже учла";
export const REVISE_LABEL = "Изменить";
export const EXPAND_LABEL = "Посмотреть";
export const COLLAPSE_LABEL = "Свернуть";

/** До этого числа строк включительно блок подробный. */
export const FULL_ROWS_MAX = 3;
/** Сколько строк видно в среднем состоянии до «+N». */
export const COMPACT_ROWS_SHOWN = 2;
/** От этого числа строк — только сводка. */
export const SUMMARY_ROWS_MIN = 7;

export interface AlreadyNotedRow {
  /** Ключ для React и для «Изменить»; у цели — `goal`. */
  key: string;
  label: string;
  /** Шаг анкеты, который можно пересмотреть; у цели его нет. */
  reviseStep?: string;
}

interface Props {
  /** Цель, если она уже есть (повторный проход) — первой строкой. */
  goalLabel: string | null;
  answers: KnownAnketaAnswer[];
  disabled?: boolean;
  onRevise: (step: string) => void;
}

/** Строки блока из документа: цель первой, затем ответы в порядке сервера. */
export function alreadyNotedRows(
  goalLabel: string | null,
  answers: KnownAnketaAnswer[],
): AlreadyNotedRow[] {
  const rows: AlreadyNotedRow[] = [];
  if (goalLabel) rows.push({ key: "goal", label: goalLabel });
  for (const answer of answers) {
    rows.push({
      key: answer.step,
      label: answer.label,
      reviseStep: answer.revisable ? answer.step : undefined,
    });
  }
  return rows;
}

function plural(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few;
  return many;
}

/** Сводка для самого компактного состояния: «7 ответов». Число здесь —
 * число строк, которые человек сам сказал; это не счётчик прохода и не
 * «фактов в памяти». */
export function summaryLabel(count: number): string {
  return `${count} ${plural(count, "ответ", "ответа", "ответов")}`;
}

export function AlreadyNoted({ goalLabel, answers, disabled, onRevise }: Props) {
  const [expanded, setExpanded] = useState(false);
  const rows = alreadyNotedRows(goalLabel, answers);
  if (rows.length === 0) return null;

  const full = rows.length <= FULL_ROWS_MAX;
  const summary = rows.length >= SUMMARY_ROWS_MIN;
  const showAll = full || expanded;
  const visible = showAll ? rows : summary ? [] : rows.slice(0, COMPACT_ROWS_SHOWN);
  const hidden = rows.length - visible.length;

  return (
    <section className="already-noted" aria-labelledby="already-noted-title">
      <h2 id="already-noted-title" className="goal-select__section-title">
        {ALREADY_NOTED_TITLE}
      </h2>
      {visible.length > 0 && (
        <ul className="already-noted__list">
          {visible.map((row) => (
            <li key={row.key} className="already-noted__row">
              <span className="already-noted__label">{row.label}</span>
              {row.reviseStep && (
                <button
                  type="button"
                  className="goal-select__minor-action"
                  disabled={disabled}
                  onClick={() => onRevise(row.reviseStep as string)}
                  aria-label={`${REVISE_LABEL}: ${row.label}`}
                >
                  {REVISE_LABEL}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {!full && (
        <button
          type="button"
          className="goal-select__minor-action"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded
            ? COLLAPSE_LABEL
            : summary
              ? `${summaryLabel(rows.length)} · ${EXPAND_LABEL}`
              : `+${hidden}`}
        </button>
      )}
    </section>
  );
}
