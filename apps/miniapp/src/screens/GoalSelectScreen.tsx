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
 * Раскладка (DRF-1458). Три веса, а не четыре одинаковые кнопки в
 * столбик: выход (`next`) — липкий CTA внизу, «отправить» — обычная
 * вторичная кнопка при своём поле, побочные намерения сервера — тихий
 * ряд ссылок. Ряды фишек переносятся по строкам: длину списка задаёт
 * сервер, и ряд без переноса уезжает вбок при любом длинном документе.
 * Раскладка отвечает на вопрос «что здесь главное»; ЧТО показывать
 * по-прежнему отвечает документ.
 *
 * Анкета — не ворота (поправка A-1 к BOT-001, §24, условие C-2).
 * Свободный ввод стоит на поверхности ВСЕГДА, рядом с вопросами: кто
 * знает, чего хочет, называет услугу здесь же и уходит к подбору, не
 * ответив ни на один вопрос. Куда уедет введённый текст, решает не
 * экран, а сервер: пока текущий шаг не открыл свободный ввод
 * (`allow_free_text`), текст идёт как `{goal_text}` — прямая цель;
 * когда открыл — как ответ на этот шаг. Поле одно, смысл серверный.
 *
 * И это условие держит ЭКРАН, а не сервер (DRF-1483). До сих пор «поле
 * стоит всегда» было правдой ровно настолько, насколько сервер слал
 * намерение `formulate_own`: без него поля не было, а без `next` не
 * было и выхода — первый экран клиента молча становился воротами.
 * Теперь экран сам замечает документ, не оставивший ни свободного
 * ввода, ни дороги дальше, и ставит и то, и другое: поле со своей
 * подписью и выход в каталог. Fail-safe по построению — запасное
 * появляется ровно в том документе, где иначе был бы тупик, и ни в
 * каком другом. Подробности признака — у `documentIsGate` ниже.
 *
 * Sections:
 *   - `known.goal` != null → "current goal" block (goal_text, or the
 *     suggestion label resolved by goal_key, or the raw key).
 *   - `missing` non-empty → each item's `prompt` rendered as-is
 *     (clarifying and guiding questions are not distinguished here).
 *   - While a POST is in flight every control is disabled; a failed
 *     POST shows an inline error and keeps the old document.
 *
 * Выход с ПОВЕРХНОСТИ, а не только из документа (DRF-1469). Та же
 * липкая панель держит «Сменить режим» для многоролевого — владельца
 * или мастера, зашедшего посмотреть приложение глазами клиента. Своей
 * цели у него нет, поэтому анкету он встречает первым же экраном, а
 * нижней навигации у клиента нет и профиля с корня не видно: раньше
 * это чинили тем, что анкету ему просто не показывали. Одноролевому
 * `SurfaceSwitchExit` не рисует ничего — его экран не меняется.
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
import { AlreadyNoted } from "../components/AlreadyNoted";
import { AnketaStepInput, renderedMode } from "../components/AnketaStepInput";
import { ScreenLayout } from "../components/ScreenLayout";
import { DelayedSkeleton, ServiceCardSkeleton } from "../components/Skeleton";
import { StateError } from "../components/StateError";
import { StickyBar, StickyCtaButton } from "../components/StickyCta";
import { SurfaceSwitchExit, useSurfaceMode } from "../components/SurfaceSwitch";
import {
  fetchDecisionContext,
  postGoalSelect,
  type DecisionContext,
  type GoalSelectBody,
  type MissingItem,
} from "../lib/customer-goals";
import { backTo, screenRoot, type BackIntent } from "../lib/screen-back";

type State =
  | { kind: "loading" }
  | { kind: "ok"; doc: DecisionContext }
  | { kind: "error"; err: unknown };

const GOAL_TEXT_MAX = 500;

/**
 * `next.id` → route. A contract with the server, not a decision: the
 * server says WHERE, this table says how that place is spelled in the
 * router. Same shape as `max-sdk.ts::_ROUTE_MAP` for the bot's slugs.
 *
 * DRF-1481 (решение владельца §24.1): эта таблица — ЕДИНСТВЕННОЕ
 * решающее место. Тип `NextStep["id"]` больше не перечисляет известные
 * назначения (`string`), поэтому незнакомый id доезжает сюда как есть и
 * просто не находит строки — кнопки в никуда не появляется, а guard
 * DRF-1483 ниже ставит запасной выход.
 */
const NEXT_ROUTES: Record<string, string> = {
  browse_catalog: "/customer/catalog",
};

/**
 * Запасной выход и запасная подпись поля — собственность ЭКРАНА, а не
 * документа (DRF-1483). Появляются ровно тогда, когда документ не дал
 * ни того, ни другого; во всех остальных случаях слова остаются
 * серверными. Каталог выбран не произвольно: это то же место, куда
 * сервер уводит своим единственным сегодняшним `next`.
 */
const GUARD_EXIT_ROUTE = "/customer/catalog";
const GUARD_EXIT_LABEL = "Посмотреть услуги";
const FREE_TEXT_FALLBACK_LABEL = "Опиши своими словами";

/**
 * Единственное, что экран говорит о месте в проходе (DRF-1743). Числа
 * «Вопрос N из M» не рисуются: макет C03 — «формулировку „Ещё один
 * короткий вопрос“ используем только если действительно уверены, что
 * вопрос последний», а уверен в этом сервер (`progress.is_last`), не
 * экран. Сторож на отсутствие счётчика — `GoalSelectScreen.progress.test.tsx`.
 */
const LAST_QUESTION_NOTE = "Ещё один короткий вопрос";

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

/** Что объявить после успешной отправки — или `null`, если объявлять нечего.

Разные тела — разные события, и называть их одним словом значит соврать:
ответ на шаг анкеты не «цель сохранена», а `need_guidance` вообще не
сохранение, а смена документа. Функция модульная, а не внутри компонента:
она не зависит от состояния, и в замыкании `useCallback` ей делать нечего. */
function noticeFor(body: GoalSelectBody): string | null {
  // `in`, а не доступ к полю: `GoalSelectBody` — размеченное объединение,
  // и у ветки с `goal_key` поля `goal_text` не существует вовсе.
  if ("goal_key" in body || "goal_text" in body) return "Цель сохранена.";
  if ("answer" in body) return "revise" in body.answer ? "Ответ изменён." : "Ответ сохранён.";
  return null;
}

export function GoalSelectScreen({ initialDoc }: Props = {}) {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  // Единственное, что экран отсюда берёт, — есть ли у человека вторая
  // поверхность. Ни на один вопрос документа это не влияет.
  const { canSwitch } = useSurfaceMode();
  const [state, setState] = useState<State>(
    initialDoc ? { kind: "ok", doc: initialDoc } : { kind: "loading" },
  );
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  // Объявление УСПЕХА. До этого его не было вовсе: отказ говорил
  // (`role="alert"`), успех молчал, и человек у поля ввода внизу не видел
  // ни подсветки чипа, ни секции «Текущая цель» — обе выше сгиба. Жалоба
  // «не понимаю, получилось ли» была ровно про это.
  const [savedNotice, setSavedNotice] = useState<string | null>(null);
  const [goalText, setGoalText] = useState("");
  // Шаг, который человек пересматривает по «Изменить» (DRF-1744). Пока
  // он открыт, текущий вопрос уступает ему место: один экран — одна
  // задача (макет C03). Сбрасывается любым ответом сервера.
  const [revisingStep, setRevisingStep] = useState<string | null>(null);

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
  const isRoot = pathname === "/";
  // DRF-1493: место возврата задано адресом, а не историей. На корне
  // возврата нет вовсе (см. абзац выше); в остальных случаях цель
  // открывают с дома клиентской поверхности — «Записи», — и по
  // deep-link `open_goal_select` из бота, где истории нет совсем.
  const back: BackIntent = isRoot
    ? screenRoot(
        "Поверхность цели смонтирована на `/` первым экраном клиента — " +
          "истории за корнем нет, вести кнопке некуда.",
      )
    : backTo("/customer/main");

  const submit = useCallback((body: GoalSelectBody) => {
    setSubmitting(true);
    setSubmitError(null);
    setSavedNotice(null);
    postGoalSelect(body)
      .then((doc) => {
        setState({ kind: "ok", doc });
        setGoalText("");
        setRevisingStep(null);
        setSavedNotice(noticeFor(body));
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
      <ScreenLayout back={back} title="Какая у тебя цель?">
        <DelayedSkeleton loading>
          <ServiceCardSkeleton />
          <ServiceCardSkeleton />
        </DelayedSkeleton>
      </ScreenLayout>
    );
  }

  if (state.kind === "error") {
    return (
      <ScreenLayout back={back} title="Какая у тебя цель?">
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
  // DRF-1758 — «Изменить» у цели = повторный проход (start_anketa), и
  // только когда сервер его предложил; иначе кнопки нет.
  const reviseGoal = startAnketaLabel
    ? () => submit({ intent: "start_anketa", source_channel: "miniapp" })
    : null;
  const anketaStep = currentAnketaStep(doc);
  const knownAnswers = doc.known.anketa ?? [];
  // Пересматриваемый шаг берётся из документа, не из памяти экрана: если
  // сервер его уже не показывает, пересматривать нечего.
  const revising = revisingStep
    ? knownAnswers.find((a) => a.step === revisingStep && a.revisable) ?? null
    : null;
  const nextStep = doc.next ?? null;
  const nextRoute = nextStep ? NEXT_ROUTES[nextStep.id] : undefined;

  // -------------------------------------------------------------------
  // Условие C-2 держится ЭКРАНОМ, а не сервером (DRF-1483).
  //
  // Раньше держалось сервером: поле свободного ввода рисовалось только
  // под намерение `formulate_own`, выход — только под `next`. Документ
  // без обоих молча превращал первый экран клиента в ворота: на корне
  // кнопки «назад» нет, нижней навигации у клиента нет, одноролевому
  // «сменить режим» не рисуется — уйти было нельзя иначе, чем ответив
  // на вопросы. Поймать это было нечем: единственный тест на C-2 стоял
  // на фикстуре, которая `formulate_own` содержит, то есть провалиться
  // не мог.
  //
  // Признак ворот берётся по `nextRoute`, а не по `next`: назначение,
  // которого нет в таблице маршрутов, кнопки не даёт — та же ловушка,
  // только с виду заполненная.
  //
  // Свободный ввод считается по тому, что экран РИСУЕТ, а не что
  // документ разрешает: шаг с `allow_free_text` без намерения
  // `formulate_own` не давал поля вовсе, и его разрешение было
  // недостижимо. Теперь такой шаг поле получает.
  //
  // Экран по-прежнему не решает, ЧТО показывать: пока документ сам
  // оставляет проход, всё рисуется ровно как раньше — ни один
  // сегодняшний документ вида не меняет. Экран гарантирует лишь, что
  // под человеком есть пол.
  // Шаг в режиме `text` рисует своё короткое поле (DRF-1746): нижнее
  // поле цели на нём не дублируется, и пол под человеком уже есть — это
  // поле и есть дорога дальше.
  const stepOwnTextField = Boolean(anketaStep?.step && renderedMode(anketaStep.mode) === "text");
  const stepAllowsFreeText = Boolean(
    anketaStep?.allow_free_text && anketaStep.step && !stepOwnTextField,
  );
  const hasFreeText = Boolean(formulateOwnLabel) || stepAllowsFreeText;
  const hasOnward = Boolean(nextRoute) || stepOwnTextField;
  const documentIsGate = !hasFreeText && !hasOnward;
  const showFreeText = hasFreeText || documentIsGate;
  const freeTextLabel = formulateOwnLabel ?? FREE_TEXT_FALLBACK_LABEL;

  // Куда уедет введённый текст, решает сервер, а не экран: пока текущий
  // шаг не открыл свободный ввод, текст — прямая цель (и это выход из
  // анкеты для того, кто знает, чего хочет); когда открыл — ответ на
  // этот шаг.
  const freeTextBody = (text: string): GoalSelectBody =>
    anketaStep?.allow_free_text && anketaStep.step
      ? { answer: { step: anketaStep.step, text }, source_channel: "miniapp" }
      : { goal_text: text, source_channel: "miniapp" };

  // Дорога дальше по документу — главное действие экрана, и оно не
  // должно зависеть от того, сколько вопросов и подсказок прислал
  // сервер (DRF-1458). Условие C-2 не тронуто: кнопка по-прежнему
  // рисуется ровно тогда, когда `next` есть в документе, и ровно с той
  // подписью, что он назвал.
  const onward =
    nextStep && nextRoute ? (
      <StickyCtaButton disabled={submitting} onClick={() => navigate(nextRoute)}>
        {nextStep.label}
      </StickyCtaButton>
    ) : null;

  // Выход с поверхности (DRF-1469) — рядом, но НЕ вместо: `next` ведёт
  // дальше по клиентскому пути, «Сменить режим» уводит с клиентской
  // поверхности целиком. От документа не зависит намеренно: сервер
  // вправе прислать документ без `next`, и остаться без выхода в этот
  // момент — ровно та ловушка, из-за которой анкету прятали.
  const surfaceExit = canSwitch ? <SurfaceSwitchExit /> : null;

  // Выход, который экран ставит САМ, когда документ не оставил ни
  // одного. Не «ещё одна кнопка рядом с `next`»: пока `next` есть,
  // этого выхода нет — иначе экран начал бы спорить с сервером о том,
  // куда вести. Он появляется только на документе-воротах.
  const guardExit = documentIsGate ? (
    <StickyCtaButton disabled={submitting} onClick={() => navigate(GUARD_EXIT_ROUTE)}>
      {GUARD_EXIT_LABEL}
    </StickyCtaButton>
  ) : null;
  // Сохранение свободного текста — липкой кнопкой, пока в поле есть текст.
  //
  // Раньше единственная кнопка, сохраняющая формулировку человека, стояла
  // ПОСЛЕДНЕЙ содержательной на прокручиваемой странице, ниже чипов и поля
  // ввода, и была вторичной по виду. Замер: 29 целей на боевом — и НИ ОДНОЙ
  // с непустым `goal_text`. Отказа сервера там нет, есть место кнопки.
  //
  // Появляется и исчезает вместе с текстом: пустое поле — сохранять нечего,
  // и постоянная кнопка спорила бы с `next` за единственное липкое место.
  // Внутристраничная кнопка при этом не дублируется, а уступает: две кнопки
  // с одним действием на одном экране — это вопрос «в чём разница», а
  // разницы нет.
  const freeTextPending = showFreeText && goalText.trim().length > 0;
  const saveFreeText = freeTextPending ? (
    <StickyCtaButton
      disabled={submitting}
      onClick={() => submit(freeTextBody(goalText.trim()))}
    >
      Отправить
    </StickyCtaButton>
  ) : null;

  const stickyCount = [saveFreeText, onward, guardExit, surfaceExit].filter(Boolean).length;

  return (
    <ScreenLayout
      back={back}
      title="Какая у тебя цель?"
      tallCta={stickyCount > 1}
      cta={
        stickyCount > 0 ? (
          <StickyBar>
            {saveFreeText}
            {onward}
            {guardExit}
            {surfaceExit}
          </StickyBar>
        ) : undefined
      }
    >
      {/* Отказ виден сразу, а не в самом низу длинного документа. */}
      {submitError && (
        <div className="callout callout--danger" role="alert">
          <p style={{ margin: 0 }}>{submitError}</p>
        </div>
      )}

      {/* Успех рядом с отказом и той же формой — но `role="status"`, а не
          `alert`: сохранение не прерывает человека, оно его подтверждает.
          Для читающего экран это и есть починка: до сих пор отказ
          объявлялся, а успех не объявлялся никак. Для видящего экран
          вторая половина починки — липкая «Отправить» выше: она исчезает
          вместе с очищенным полем, то есть изменение происходит ТАМ, где
          человек стоит, а не одной строкой выше сгиба. */}
      {savedNotice && (
        <div className="callout callout--success" role="status">
          <p style={{ margin: 0 }}>{savedNotice}</p>
        </div>
      )}

      {/* «Уже учла» (DRF-1744) — пока идёт проход: цель (если есть) и
          ответы с «Изменить». Без ответов — прежняя секция «Текущая
          цель», чтобы документы до DRF-1744 рисовались как раньше. */}
      {knownAnswers.length > 0 ? (
        <AlreadyNoted
          goalLabel={knownLabel}
          answers={knownAnswers}
          disabled={submitting}
          onRevise={setRevisingStep}
          onReviseGoal={reviseGoal ?? undefined}
        />
      ) : (
        knownGoal &&
        knownLabel && (
          <section aria-labelledby="goal-select-current">
            <h2 id="goal-select-current" className="goal-select__section-title">
              Текущая цель
            </h2>
            <p className="goal-select__current">
              {knownLabel}
              {/* DRF-1758 — «Твоя цель: … · Изменить» (макет C02.1): кнопка
                  ровно когда сервер прислал намерение start_anketa. */}
              {reviseGoal && (
                <>
                  {" · "}
                  <button
                    type="button"
                    className="goal-select__minor-action"
                    disabled={submitting}
                    onClick={reviseGoal}
                    aria-label={`Изменить: ${knownLabel}`}
                  >
                    Изменить
                  </button>
                </>
              )}
            </p>
          </section>
        )
      )}

      {/* Пересмотр ответа занимает место вопроса: варианты — те, что
          сервер прислал в строке «Уже учла», ответ уходит с `revise`. */}
      {revising && (
        <section aria-label="Изменить ответ">
          <p className="goal-select__prompt">{revising.prompt}</p>
          <div className="chip-row" role="group" aria-label={revising.prompt}>
            {revising.options
              .filter((option) => option.role !== "escape")
              .map((option) => (
                <button
                  key={option.key}
                  type="button"
                  className="chip"
                  disabled={submitting}
                  aria-pressed={option.key === revising.option_key}
                  onClick={() =>
                    submit({
                      answer: { step: revising.step, option_key: option.key, revise: true },
                      source_channel: "miniapp",
                    })
                  }
                >
                  {option.label}
                </button>
              ))}
          </div>
          <div className="goal-select__minor">
            {/* DRF-1747 — «Не знаю» и при пересмотре стоит отдельно от
                вариантов: полноценный ответ, но не вариант. */}
            {revising.options
              .filter((option) => option.role === "escape")
              .map((option) => (
                <button
                  key={option.key}
                  type="button"
                  className="goal-select__minor-action"
                  data-testid="anketa-escape"
                  disabled={submitting}
                  onClick={() =>
                    submit({
                      answer: { step: revising.step, option_key: option.key, revise: true },
                      source_channel: "miniapp",
                    })
                  }
                >
                  {option.label}
                </button>
              ))}
            <button
              type="button"
              className="goal-select__minor-action"
              disabled={submitting}
              onClick={() => setRevisingStep(null)}
            >
              Оставить как есть
            </button>
          </div>
        </section>
      )}

      {!revising && doc.missing.length > 0 && (
        <section aria-label="Вопросы">
          {doc.missing.map((item, index) => (
            <div key={`${item.kind}-${index}`}>
              {item.progress?.is_last === true && (
                <p className="goal-select__progress">{LAST_QUESTION_NOTE}</p>
              )}
              <p className="goal-select__prompt">{item.prompt}</p>
              {/* DRF-1746 — компонент по типу ответа (`mode`); экран
                  ничего не выводит, только рисует то, что прислано. */}
              {item.step && (
                <AnketaStepInput
                  item={item}
                  submitting={submitting}
                  onAnswer={(answer) =>
                    submit({ answer, source_channel: "miniapp" } as GoalSelectBody)
                  }
                />
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

      {showFreeText && (
        <section aria-labelledby="goal-select-own">
          <h2 id="goal-select-own" className="goal-select__section-title">
            {freeTextLabel}
          </h2>
          <textarea
            className="goal-select__textarea"
            value={goalText}
            onChange={(e) => setGoalText(e.target.value)}
            maxLength={GOAL_TEXT_MAX}
            rows={2}
            placeholder="Опиши своими словами"
            aria-label={freeTextLabel}
            disabled={submitting}
          />
          {/* Пока текста нет — кнопка стоит здесь, отключённая, и показывает,
              что с полем вообще можно сделать. Как только текст появился,
              сохранение уезжает в липкую панель (см. `saveFreeText`), и
              здесь не остаётся ничего: два «Отправить» на одном экране — это
              вопрос «в чём разница». */}
          {!freeTextPending && (
            <div className="goal-select__actions">
              <button type="button" className="btn-secondary" disabled>
                Отправить
              </button>
            </div>
          )}
        </section>
      )}

      {/* Побочные намерения сервера — оба ведут ВГЛУБЬ вопросов, поэтому
          стоят в одном тихом ряду, а не двумя кнопками в столбик.
          DRF-1225 / C-4: «пройти анкету заново» показывается ровно
          тогда, когда намерение прислал сервер. */}
      {(guidanceLabel || startAnketaLabel) && (
        <div className="goal-select__minor">
          {guidanceLabel && (
            <button
              type="button"
              className="goal-select__minor-action"
              disabled={submitting}
              onClick={() =>
                submit({ intent: "need_guidance", source_channel: "miniapp" })
              }
            >
              {guidanceLabel}
            </button>
          )}
          {startAnketaLabel && (
            <button
              type="button"
              className="goal-select__minor-action"
              disabled={submitting}
              onClick={() =>
                submit({ intent: "start_anketa", source_channel: "miniapp" })
              }
            >
              {startAnketaLabel}
            </button>
          )}
        </div>
      )}
    </ScreenLayout>
  );
}
