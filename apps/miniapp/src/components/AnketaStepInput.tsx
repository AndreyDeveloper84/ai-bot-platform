/**
 * Ввод ответа на шаг анкеты по его типу (DRF-1746, макет C03 P11).
 *
 * «C03 не должен выглядеть как серия одинаковых экранов с radio-кнопками».
 * Тип ответа — `mode` — приходит с сервера в элементе `missing`; компонент
 * рисует по нему и ничего не выводит: ни какой шаг следующий, ни сколько
 * их, ни «какой режим подошёл бы лучше».
 *
 * - `single` (и отсутствие `mode`, и незнакомое значение — fail-open к
 *   простому, не к пустому экрану): фишки, тап = ответ `{option_key}`;
 * - `multi`: фишки-переключатели и «Продолжить»; ответ уходит ОДНИМ
 *   запросом `{option_keys: [...]}` — не по тапу. «Ничего из этого» —
 *   обычная опция сервера, не особая кнопка;
 * - `scale`: деления в порядке `options` с подписями концов из `scale`;
 *   тап = `{option_key}`;
 * - `text`: короткое поле с лимитом `text_limit`, «Отправить» — `{text}`;
 * - `confirm`: компонент подтверждения известного — DRF-1745; до него
 *   рисуется как `single`.
 */
import { useState } from "react";

import type { MissingItem } from "../lib/customer-goals";

export type AnketaAnswer =
  | { step: string; option_key: string }
  | { step: string; option_keys: string[] }
  | { step: string; text: string };

export const MULTI_CONTINUE_LABEL = "Продолжить";
export const TEXT_SUBMIT_LABEL = "Отправить";
/** Лимит поля, если сервер не прислал свой: тот же, что у сервера. */
export const TEXT_LIMIT_FALLBACK = 120;

interface Props {
  item: MissingItem;
  submitting: boolean;
  onAnswer: (answer: AnketaAnswer) => void;
}

/** Режим, который экран умеет рисовать; всё остальное — `single`. */
export function renderedMode(mode: string | undefined): "single" | "multi" | "scale" | "text" {
  if (mode === "multi" || mode === "scale" || mode === "text") return mode;
  return "single";
}

export function AnketaStepInput({ item, submitting, onAnswer }: Props) {
  const step = item.step ?? "";
  const options = item.options ?? [];
  const mode = renderedMode(item.mode);

  if (mode === "text") {
    return (
      <TextAnswer
        step={step}
        limit={item.text_limit ?? TEXT_LIMIT_FALLBACK}
        prompt={item.prompt}
        submitting={submitting}
        onAnswer={onAnswer}
      />
    );
  }
  if (options.length === 0) return null;
  if (mode === "multi") {
    return (
      <MultiAnswer
        step={step}
        prompt={item.prompt}
        options={options}
        submitting={submitting}
        onAnswer={onAnswer}
      />
    );
  }
  if (mode === "scale") {
    return (
      <div className="anketa-scale" data-testid="anketa-scale">
        <div className="chip-row" role="radiogroup" aria-label={item.prompt}>
          {options.map((option) => (
            <button
              key={option.key}
              type="button"
              role="radio"
              aria-checked={false}
              className="chip"
              disabled={submitting}
              onClick={() => onAnswer({ step, option_key: option.key })}
            >
              {option.label}
            </button>
          ))}
        </div>
        {item.scale && (
          <div className="anketa-scale__ends" aria-hidden="true">
            <span>{item.scale.low_label}</span>
            <span>{item.scale.high_label}</span>
          </div>
        )}
      </div>
    );
  }
  return (
    <div className="chip-row" role="group" aria-label={item.prompt}>
      {options.map((option) => (
        <button
          key={option.key}
          type="button"
          className="chip"
          disabled={submitting}
          onClick={() => onAnswer({ step, option_key: option.key })}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

function MultiAnswer({
  step,
  prompt,
  options,
  submitting,
  onAnswer,
}: {
  step: string;
  prompt: string;
  options: { key: string; label: string }[];
  submitting: boolean;
  onAnswer: (answer: AnketaAnswer) => void;
}) {
  const [picked, setPicked] = useState<string[]>([]);
  const toggle = (key: string) =>
    setPicked((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]));
  // Порядок в ответе — порядок вариантов сервера, не порядок тапов.
  const ordered = options.map((o) => o.key).filter((k) => picked.includes(k));
  return (
    <div data-testid="anketa-multi">
      <div className="chip-row" role="group" aria-label={prompt}>
        {options.map((option) => {
          const active = picked.includes(option.key);
          return (
            <button
              key={option.key}
              type="button"
              role="checkbox"
              aria-checked={active}
              className={`chip${active ? " chip--active" : ""}`}
              disabled={submitting}
              onClick={() => toggle(option.key)}
            >
              {option.label}
            </button>
          );
        })}
      </div>
      <div className="goal-select__actions">
        <button
          type="button"
          className="btn-primary"
          disabled={submitting || ordered.length === 0}
          onClick={() => onAnswer({ step, option_keys: ordered })}
        >
          {MULTI_CONTINUE_LABEL}
        </button>
      </div>
    </div>
  );
}

function TextAnswer({
  step,
  limit,
  prompt,
  submitting,
  onAnswer,
}: {
  step: string;
  limit: number;
  prompt: string;
  submitting: boolean;
  onAnswer: (answer: AnketaAnswer) => void;
}) {
  const [text, setText] = useState("");
  const trimmed = text.trim();
  return (
    <div data-testid="anketa-text">
      <input
        type="text"
        className="goal-select__textarea"
        value={text}
        maxLength={limit}
        onChange={(e) => setText(e.target.value)}
        aria-label={prompt}
        disabled={submitting}
      />
      <p className="anketa-text__limit">
        {text.length} / {limit}
      </p>
      <div className="goal-select__actions">
        <button
          type="button"
          className="btn-primary"
          disabled={submitting || trimmed.length === 0}
          onClick={() => onAnswer({ step, text: trimmed })}
        >
          {TEXT_SUBMIT_LABEL}
        </button>
      </div>
    </div>
  );
}
