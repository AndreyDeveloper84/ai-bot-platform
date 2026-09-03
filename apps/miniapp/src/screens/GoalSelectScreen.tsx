/**
 * Goal select screen (DRF-1190) — dumb renderer over the server-side
 * decision-context document.
 *
 * The screen takes NO decisions: no hardcoded chip lists, no local
 * "what to show next" computation, no next-screen branching. Every
 * user action is a POST `/goals/select`; the returned document
 * replaces state verbatim and the UI re-renders from it:
 *
 *   1. Suggestions (`suggestions`) — chips; click POSTs `{goal_key}`.
 *   2. Free-form input (intent `formulate_own`) — same surface, not a
 *      separate branch: textarea + submit POSTs `{goal_text}`.
 *   3. Guidance (intent `need_guidance`) — button POSTs
 *      `{intent: "need_guidance"}`. The user STAYS on the surface;
 *      the server answers with `missing` kind=goal_guidance and the
 *      first guiding question in `prompt`, which we render like any
 *      other missing item.
 *   4. Anketa step (DRF-1451) — a `missing` item that arrived with
 *      `options`. Its chips POST `{answer: {step, option_key}}`. The
 *      screen does not know the sequence: no question list, no order,
 *      no "is this the last one". Even the position is server-computed
 *      and arrives in `progress`. `step` is echoed back untouched so a
 *      stale answer is refused (409) rather than filed under the wrong
 *      question.
 *   5. Onward (`next`) — where to go when there is nothing left to ask.
 *      The server names the destination; this screen maps the id to a
 *      route, the same way `max-sdk.ts::_ROUTE_MAP` maps the bot's
 *      start-param slugs. Before DRF-1451 nobody decided this and the
 *      screen simply re-rendered after a goal was chosen.
 *
 * Анкета — не ворота (поправка A-1 к BOT-001, §24, условие C-2).
 * Свободный ввод стоит на поверхности ВСЕГДА, рядом с вопросами: кто
 * знает, чего хочет, называет услугу здесь же и уходит к подбору, не
 * ответив ни на один вопрос. Куда уедет введённый текст, решает не
 * экран, а сервер: пока текущий шаг не открыл свободный ввод
 * (`allow_free_text`), текст идёт как `{goal_text}` — прямая цель;
 * когда открыл — как ответ на этот шаг. Поле одно, смысл серверный.
 *
 * Sections:
 *   - `known.goal` != null → "current goal" block (goal_text, or the
 *     suggestion label resolved by goal_key, or the raw key).
 *   - `missing` non-empty → each item's `prompt` rendered as-is
 *     (clarifying and guiding questions are not distinguished here).
 *   - While a POST is in flight every control is disabled; a failed
 *     POST shows an inline error and keeps the old document.
 *
 * Экран не корневой, поэтому кнопка «назад» платформы должна быть
 * показана и заведена на роутер (канон `useBackButton`: показывать
 * везде, кроме корня). Пока входом была только стартовая сетка бота,
 * это не мешало; теперь на анкету ведёт приглашение с домашнего экрана,
 * и без выхода назад она стала бы тупиком — то есть загородила бы
 * дорогу к записи.
 */

import { useCallback, useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ScreenLayout } from "../components/ScreenLayout";
import { DelayedSkeleton, ServiceCardSkeleton } from "../components/Skeleton";
import { StateError } from "../components/StateError";
import {
  fetchDecisionContext,
  postGoalSelect,
  type DecisionContext,
  type GoalSelectBody,
  type MissingItem,
} from "../lib/customer-goals";
import { useBackButton } from "../hooks/useBackButton";

type State =
  | { kind: "loading" }
  | { kind: "ok"; doc: DecisionContext }
  | { kind: "error"; err: unknown };

const GOAL_TEXT_MAX = 500;

/**
 * `next.id` → route. A contract with the server, not a decision: the
 * server says WHERE, this table says how that place is spelled in the
 * router. Same shape as `max-sdk.ts::_ROUTE_MAP` for the bot's slugs.
 */
const NEXT_ROUTES: Record<string, string> = {
  browse_catalog: "/customer/catalog",
};

/** The anketa step currently on the surface, if the server sent one. */
function currentAnketaStep(doc: DecisionContext): MissingItem | null {
  return doc.missing.find((item) => typeof item.step === "string") ?? null;
}

interface Props {
  /**
   * Документ, уже полученный вызывающим (DRF-1451).
   *
   * `CustomerEntryScreen` читает decision-context, чтобы понять, есть
   * ли что спрашивать, и монтирует эту поверхность прямо на корне.
   * Без этого пропа она сходила бы за тем же документом второй раз —
   * два круга к серверу на первом же экране, на самом дорогом месте.
   *
   * Решений это не добавляет: документ тот же, просто не запрошенный
   * дважды. Любой POST по-прежнему заменяет состояние ответом сервера.
   */
  initialDoc?: DecisionContext;
}

export function GoalSelectScreen({ initialDoc }: Props = {}) {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [state, setState] = useState<State>(
    initialDoc ? { kind: "ok", doc: initialDoc } : { kind: "loading" },
  );
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [goalText, setGoalText] = useState("");

  const load = useCallback(() => {
    setState({ kind: "loading" });
    let cancelled = false;
    fetchDecisionContext()
      .then((doc) => {
        if (!cancelled) setState({ kind: "ok", doc });
      })
      .catch((err: unknown) => {
        if (!cancelled) setState({ kind: "error", err });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Документ пришёл готовым — второй круг к серверу не нужен. Повторная
  // загрузка остаётся доступной: `load` вызывается кнопкой «повторить»
  // на ошибке, а любой POST и так заменяет состояние.
  const hasInitial = initialDoc !== undefined;
  useEffect(() => {
    if (hasInitial) return;
    return load();
  }, [load, hasInitial]);

  // «Назад» ведёт туда, откуда пришли (домашний экран или стартовая
  // сетка бота), а не закрывает мини-апп.
  //
  // DRF-1451: с этого дня та же поверхность бывает и КОРНЕМ — первый
  // экран клиента без цели монтирует её на `/`. На корне кнопки быть не
  // должно (канон `useBackButton`: показывать везде, кроме корня), и
  // вести ей там некуда: истории за корнем нет, нажатие оставило бы
  // человека на месте. Единственное решение экрана о навигации — и оно
  // о корне роутера, а не о содержании документа.
  const goBack = useCallback(() => navigate(-1), [navigate]);
  const isRoot = pathname === "/";
  useBackButton({ onBack: isRoot ? undefined : goBack });

  const submit = useCallback((body: GoalSelectBody) => {
    setSubmitting(true);
    setSubmitError(null);
    postGoalSelect(body)
      .then((doc) => {
        setState({ kind: "ok", doc });
        setGoalText("");
      })
      .catch(() => {
        // Показать отказ И перечитать документ.
        //
        // Раньше документ оставался прежним, и человеку предлагалось
        // нажать ту же кнопку ещё раз. На протухшем документе (сервер
        // ответил 409 «шаг не тот») это воспроизводило бы ту же ошибку
        // бесконечно: экран отправлял бы ответ на вопрос, которого
        // сервер уже не ждёт. Перечитывание заменяет документ, и
        // следующее нажатие попадает в актуальный шаг.
        setSubmitError("Не получилось отправить. Попробуй снова.");
        load();
      })
      .finally(() => setSubmitting(false));
  }, [load]);

  if (state.kind === "loading") {
    return (
      <ScreenLayout title="Какая у тебя цель?">
        <DelayedSkeleton loading>
          <ServiceCardSkeleton />
          <ServiceCardSkeleton />
        </DelayedSkeleton>
      </ScreenLayout>
    );
  }

  if (state.kind === "error") {
    return (
      <ScreenLayout title="Какая у тебя цель?">
        <StateError err={state.err} onRetry={load} screenId="goal-select" />
      </ScreenLayout>
    );
  }

  const { doc } = state;
  const knownGoal = doc.known.goal;
  const knownLabel = knownGoal
    ? knownGoal.goal_text ??
      doc.suggestions.find((s) => s.key === knownGoal.goal_key)?.label ??
      knownGoal.goal_key
    : null;
  const intentLabel = (id: string) =>
    doc.intents.find((i) => i.id === id)?.label ?? null;
  const formulateOwnLabel = intentLabel("formulate_own");
  const guidanceLabel = intentLabel("need_guidance");
  const startAnketaLabel = intentLabel("start_anketa");
  const anketaStep = currentAnketaStep(doc);
  const nextStep = doc.next ?? null;
  const nextRoute = nextStep ? NEXT_ROUTES[nextStep.id] : undefined;

  // Куда уедет введённый текст, решает сервер, а не экран: пока текущий
  // шаг не открыл свободный ввод, текст — прямая цель (и это выход из
  // анкеты для того, кто знает, чего хочет); когда открыл — ответ на
  // этот шаг.
  const freeTextBody = (text: string): GoalSelectBody =>
    anketaStep?.allow_free_text && anketaStep.step
      ? { answer: { step: anketaStep.step, text }, source_channel: "miniapp" }
      : { goal_text: text, source_channel: "miniapp" };

  return (
    <ScreenLayout title="Какая у тебя цель?">
      {knownGoal && knownLabel && (
        <section aria-labelledby="goal-select-current">
          <h2 id="goal-select-current" className="goal-select__section-title">
            Текущая цель
          </h2>
          <p className="goal-select__current">{knownLabel}</p>
        </section>
      )}

      {doc.missing.length > 0 && (
        <section aria-label="Вопросы">
          {doc.missing.map((item, index) => (
            <div key={`${item.kind}-${index}`}>
              {item.progress && (
                <p className="goal-select__progress">
                  Вопрос {item.progress.index} из {item.progress.total}
                </p>
              )}
              <p className="goal-select__prompt">{item.prompt}</p>
              {item.step && item.options && item.options.length > 0 && (
                <div className="chip-row" role="group" aria-label={item.prompt}>
                  {item.options.map((option) => (
                    <button
                      key={option.key}
                      type="button"
                      className="chip"
                      disabled={submitting}
                      onClick={() =>
                        submit({
                          answer: { step: item.step as string, option_key: option.key },
                          source_channel: "miniapp",
                        })
                      }
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ))}
        </section>
      )}

      {doc.suggestions.length > 0 && (
        <section aria-labelledby="goal-select-suggestions">
          <h2
            id="goal-select-suggestions"
            className="goal-select__section-title"
          >
            {intentLabel("choose_suggested") ?? "Выбери из вариантов"}
          </h2>
          <div
            className="chip-row"
            role="radiogroup"
            aria-labelledby="goal-select-suggestions"
          >
            {doc.suggestions.map((s) => {
              const active = knownGoal?.goal_key === s.key;
              return (
                <button
                  key={s.key}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  className={`chip${active ? " chip--active" : ""}`}
                  disabled={submitting}
                  onClick={() =>
                    submit({ goal_key: s.key, source_channel: "miniapp" })
                  }
                >
                  {s.label}
                </button>
              );
            })}
          </div>
        </section>
      )}

      {formulateOwnLabel && (
        <section aria-labelledby="goal-select-own">
          <h2 id="goal-select-own" className="goal-select__section-title">
            {formulateOwnLabel}
          </h2>
          <textarea
            className="goal-select__textarea"
            value={goalText}
            onChange={(e) => setGoalText(e.target.value)}
            maxLength={GOAL_TEXT_MAX}
            rows={3}
            placeholder="Опиши своими словами"
            aria-label={formulateOwnLabel}
            disabled={submitting}
          />
          <div className="goal-select__actions">
            <button
              type="button"
              className="btn-secondary"
              disabled={submitting || goalText.trim().length === 0}
              onClick={() => submit(freeTextBody(goalText.trim()))}
            >
              Отправить
            </button>
          </div>
        </section>
      )}

      {guidanceLabel && (
        <div className="goal-select__actions">
          <button
            type="button"
            className="btn-secondary"
            disabled={submitting}
            onClick={() =>
              submit({ intent: "need_guidance", source_channel: "miniapp" })
            }
          >
            {guidanceLabel}
          </button>
        </div>
      )}

      {/* DRF-1225 / C-4 — пройти анкету заново можно сколько угодно раз.
          Показывается ровно тогда, когда намерение прислал сервер. */}
      {startAnketaLabel && (
        <div className="goal-select__actions">
          <button
            type="button"
            className="btn-secondary"
            disabled={submitting}
            onClick={() =>
              submit({ intent: "start_anketa", source_channel: "miniapp" })
            }
          >
            {startAnketaLabel}
          </button>
        </div>
      )}

      {/* Спрашивать нечего — сервер назвал, куда вести дальше. */}
      {nextStep && nextRoute && (
        <div className="goal-select__actions">
          <button
            type="button"
            className="btn-secondary goal-select__next"
            disabled={submitting}
            onClick={() => navigate(nextRoute)}
          >
            {nextStep.label}
          </button>
        </div>
      )}

      {submitError && (
        <div className="callout callout--danger" role="alert">
          <p style={{ margin: 0 }}>{submitError}</p>
        </div>
      )}
    </ScreenLayout>
  );
}
