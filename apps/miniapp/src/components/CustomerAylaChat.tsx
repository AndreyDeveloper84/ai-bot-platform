/**
 * Лента разговора клиента с Ayla — макет владельца «Чат с Ayla.png» (DRF-2953).
 *
 * Отдельный компонент, а не четвёртый набор пропсов в общем `AylaChat`: тот
 * служит мастеру и салону (предложения, подтверждения, карточки — у клиента
 * их нет), а у клиента свои быстрые ответы и своя раскладка. Общее взято
 * оттуда готовым — фильтр скрытых реплик, разбор кнопки «открыть Mini App»,
 * граница длины вопроса; разметка мастера и салона не тронута по построению.
 *
 * # Раскладка с макета, слова — действующие или владельца
 *
 * Приветствие и «Ayla думает…» — прежние; «Нет подключения…», «Попробовать
 * снова» и «Написать в поддержку» — слова раздела «Записи»
 * (`RECORDS_ERROR_COPY`). Подпись поля, слова сбоя и заголовок карточки
 * сбоя — решение владельца 12.11 (DRF-2953, п.2–3): клиентский голос
 * приложения везде «ты», поэтому «Спросите Ayla…» и «Попробуйте ещё раз» от
 * чата мастера здесь не годятся. Чат мастера и салона их сохраняет.
 *
 * # Чего с макета нет — и почему
 *
 *   - микрофона и «+»: голосового тракта у Mini App нет (DRF-2427), а у хода
 *     из приложения нет вложений. Нарисованный микрофон был бы обещанием;
 *   - карточек мастеров, выбора времени, «Запись подтверждена»: сервер
 *     клиенту карточек не шлёт, запись идёт текстом и кнопками бота;
 *   - списка «Учитываю твои цели / Подбираю мастеров…»: экран не знает, чем
 *     занят сервер, и срока не обещает;
 *   - «Показать полностью»: у ответа нет структуры, а прятать часть ответа
 *     (в том числе текст безопасности) экран не вправе;
 *   - плашки «с тобой общается специалист»: признака передачи в ответе нет.
 *
 * # Пустой ответ — не пузырь
 *
 * Когда вопрос уже у сотрудника, бот молчит и `answer` пуст. Пустой пузырь
 * Ayla читался как сломанный ответ. Теперь его нет, а под репликой человека
 * появляется время — оно ставится только после ответа сервера и значит
 * «принято».
 *
 * # Повтор — тем же ключом
 *
 * Не получив ответа, экран не знает, дошёл ли вопрос. Новый ключ запустил бы
 * второй ход: вопрос в нити дважды, а то и две задачи сотруднику. Поэтому
 * «Попробовать снова» шлёт прежний ключ, и сервер отвечает, чем кончился
 * первый ход (`lib/customer-assistant.ts`):
 *
 *   - прошёл — ответ дочитывается из истории;
 *   - ещё идёт — экран ждёт и переспрашивает тем же ключом; не дождавшись,
 *     говорит нейтрально («Попробуй ещё раз…»), а не «не получилось
 *     отправить»: сервер только что подтвердил, что вопрос принят;
 *   - упал — ключ занят, вопрос уходит заново новым ключом, сразу.
 *
 * Сам экран ключ меняет только когда сервер сказал, что ход упал.
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { useOnline } from "../hooks/useOnline";
import { ApiError } from "../lib/api";
import { authErrorCopy, isAuthRefusalSlug, isTransportRefusalSlug } from "../lib/auth-error-copy";
import {
  customerAylaApi,
  DUPLICATE_REQUEST,
  newRequestId,
  TURN_FAILED,
  type AskResponse,
} from "../lib/customer-assistant";
import { formatTurnWhen } from "../lib/customer-last-topic";
import { SUPPORT_DEEPLINK } from "../lib/customer-profile";
import { hapticSelection, openExternalLink, signalReady } from "../lib/max-sdk";
import {
  isHiddenTurn,
  MAX_QUESTION_CHARS,
  quickReplyRoute,
  visibleMessages,
  type AylaChatMessage,
  type AylaQuickReply,
} from "./AylaChat";
import { OpenFromMaxBody } from "./OpenFromMaxScreen";
import { RecordIcon, type RecordIconName } from "./records/RecordIcons";
import { RECORDS_ERROR_COPY } from "./records/RecordsLoadError";
import { RecordButton } from "./records/RecordsState";

/** Слова чата клиента — решение владельца 12.11 (DRF-2953, п.2–3). */
export const CLIENT_CHAT_COPY = {
  placeholder: "Напиши сообщение…",
  failed: "Не получилось отправить. Попробуй ещё раз.",
  failedTitle: "Не удалось получить ответ от Ayla",
} as const;

interface Turn extends AylaChatMessage {
  buttons?: AylaQuickReply[];
}

/** Вопрос, исход которого экран ещё не знает. Ключ живёт, пока не узнает. */
interface Attempt {
  text: string;
  requestId: string;
  /** Реплика человека в ленте; `null` — скрытая (команда, токен). */
  localId: string | null;
}

type Failure =
  /** Вход не из MAX: повтор не поможет, дорога одна — обратно в мессенджер. */
  | { kind: "open_from_max" }
  /** Отказ, который повтором не лечится. */
  | { kind: "refused"; title?: string; text: string }
  | { kind: "retry"; title?: string; text: string };

/**
 * Исход неизвестен: сервер вопрос принял, а ответа у экрана нет (ход ещё идёт
 * или ответ не дочитался из истории). «Не получилось отправить» здесь было бы
 * неправдой — слова нейтральные, тело кадра 15 макета.
 */
const UNKNOWN_OUTCOME: Failure = { kind: "retry", text: RECORDS_ERROR_COPY.failedBody };

/** Сколько раз переспросить тем же ключом, пока первый ход ещё идёт. */
const RUNNING_POLLS = 3;
/** Пауза между такими вопросами: каждый тратит квоту (10 в минуту). */
const RUNNING_POLL_MS = 4000;

/**
 * Слаги, у которых `detail` написан для человека по-русски
 * (`views_customer_assistant.py`). У остальных он для нас и по-английски
 * («too many questions, retry in a minute») — человеку идут прежние слова
 * сбоя, а `detail` уже в журнале: его пишет клиент API (DRF-2446).
 */
const SERVER_WORDS: ReadonlySet<string> = new Set([
  TURN_FAILED,
  "empty_question",
  "question_too_long",
  "not_supported",
]);

function failureOf(err: unknown): Failure {
  if (!(err instanceof ApiError)) return { kind: "retry", text: CLIENT_CHAT_COPY.failed };
  // Слаг `malformed` есть и у отказа входа (401), и у разбора тела этой ручки
  // (400) — различает их только статус.
  if (err.status === 401 && isTransportRefusalSlug(err.slug)) return { kind: "open_from_max" };
  if (err.status !== 400 && isAuthRefusalSlug(err.slug)) {
    const copy = authErrorCopy(err.slug);
    // Повтор — там же, где его предлагает сама копия отказа (сбой сервера
    // временный, удалённый аккаунт — нет).
    return { kind: copy.retryLabel ? "retry" : "refused", title: copy.title, text: copy.body };
  }
  const text = SERVER_WORDS.has(err.slug) && err.detail ? err.detail : CLIENT_CHAT_COPY.failed;
  // 400 — вопрос не тот (пустой, длинный, не из MAX): тот же запрос даст тот же
  // отказ. Заголовка «не удалось получить ответ» у него нет: сервер ответил —
  // отказом, и говорит его словами.
  return err.status === 400 ? { kind: "refused", text } : { kind: "retry", text };
}

function isActionable(reply: AylaQuickReply): boolean {
  return Boolean(reply.url || reply.payload || quickReplyRoute(reply));
}

/** Абзацы ответа: пустая строка — граница (кадр 8, длинный ответ). */
function paragraphs(content: string): string[] {
  return content.split(/\n{2,}/).filter((p) => p.trim() !== "");
}

function pause(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

let localSeq = 0;
function nextLocalId(): string {
  localSeq += 1;
  return `local-${localSeq}`;
}

function AylaMark({ blank = false }: { blank?: boolean }) {
  return (
    <span className={blank ? "cchat-mark cchat-mark--blank" : "cchat-mark"} aria-hidden="true">
      {blank ? null : <RecordIcon name="ayla" size={18} />}
    </span>
  );
}

function Notice({
  icon,
  danger = false,
  title,
  text,
  children,
}: {
  icon: RecordIconName;
  danger?: boolean;
  title?: string;
  text: string;
  children?: ReactNode;
}) {
  // Живая область — снаружи ленты: `role="log"` читает реплики сам, и
  // вложенный в него `alert` прозвучал бы дважды. Фокус не уводится: человек
  // может быть посреди набора.
  return (
    <section className="cchat-notice" role={danger ? "alert" : "status"}>
      <span
        className={danger ? "cchat-notice__badge cchat-notice__badge--danger" : "cchat-notice__badge"}
        aria-hidden="true"
      >
        <RecordIcon name={icon} size={28} />
      </span>
      {title ? <h2 className="cchat-notice__title">{title}</h2> : null}
      <p className="cchat-notice__text">{text}</p>
      {children ? <div className="cchat-notice__actions">{children}</div> : null}
    </section>
  );
}

interface Props {
  /** Приглашение пустого разговора — пузырём Ayla. */
  greeting: string;
  /** Переход внутри Mini App по кнопке бота «открыть приложение» (DRF-2885). */
  onOpen: (route: string) => void;
  /** Пауза перед повторным вопросом, пока первый ход идёт. Меняют только тесты. */
  runningPollMs?: number;
}

export function CustomerAylaChat({ greeting, onOpen, runningPollMs = RUNNING_POLL_MS }: Props) {
  const online = useOnline();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [loadingHistory, setLoadingHistory] = useState(true);
  // Разговор начат с чистого листа: приветствие остаётся первой репликой на
  // всю сессию, а не исчезает с первым вопросом.
  const [startedEmpty, setStartedEmpty] = useState(false);
  const [sending, setSending] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);

  const attemptRef = useRef<Attempt | null>(null);
  // Брошенная попытка: человек задал новый вопрос поверх сбоя. Если её ход на
  // сервере всё же прошёл, ответ лежит в истории — и может быть тем самым,
  // который нельзя потерять (текст безопасности).
  const abandonedRef = useRef(false);
  const aliveRef = useRef(true);
  const listRef = useRef<HTMLDivElement | null>(null);
  const listEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  useEffect(() => {
    let alive = true;
    customerAylaApi
      .history()
      .then((res) => {
        if (!alive) return;
        const seen = visibleMessages(res.messages);
        setTurns(seen);
        setStartedEmpty(seen.length === 0);
      })
      .catch(() => {
        // История — не условие разговора (решение DRF-2799): приглашение
        // честнее, чем ошибка на месте диалога, которого ещё нет.
        if (!alive) return;
        setTurns([]);
        setStartedEmpty(true);
      })
      .finally(() => {
        if (!alive) return;
        setLoadingHistory(false);
        signalReady();
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    // `scrollIntoView` есть не везде (jsdom, старые webview): прокрутка к
    // последней реплике удобна, но разговор от неё не зависит.
    const end = listEndRef.current;
    if (end && typeof end.scrollIntoView === "function") end.scrollIntoView({ block: "end" });
  }, [turns.length, sending, failure, online]);

  const showAnswer = useCallback(async (attempt: Attempt, res: AskResponse) => {
    const answer = res.answer ?? "";
    const buttons = res.buttons ?? [];
    if (abandonedRef.current) {
      abandonedRef.current = false;
      try {
        // Брошенный вопрос мог пройти: его ответ — только в истории. Берём
        // ленту оттуда — там и он, и (обычно) ответ нового вопроса.
        const seen: Turn[] = visibleMessages((await customerAylaApi.history()).messages);
        if (!aliveRef.current) return;
        // Реплики Ayla после последнего вопроса — ответ нового хода. `answer`
        // склеен из обрезанных реплик хода через пустую строку и может нести
        // строку памяти, которой в истории нет (`memory_announce` дописывает её
        // после записи консьержа). Поэтому реплика ищется ЦЕЛЫМ абзацем ответа —
        // не «равна», не «хвост» и не просто подстрока: короткое чужое
        // «Хорошо.» входит в любой ответ (ревью #2463).
        const framed = `\n\n${answer}\n\n`;
        const lastAsk = seen.map((t) => t.role).lastIndexOf("user");
        const ours = seen
          .slice(lastAsk + 1)
          .filter(
            (t) =>
              t.role === "assistant" &&
              t.content.trim() !== "" &&
              framed.includes(`\n\n${t.content.trim()}\n\n`),
          );
        const anchor = ours.at(-1);
        // Кнопки рисуются только у последней реплики ленты: якорь годится,
        // только если после него в истории нет ничего видимого.
        const lastShown = seen.map((t) => t.content.trim() !== "").lastIndexOf(true);
        if (anchor !== undefined && seen[lastShown] === anchor) {
          // Чего из ответа нет в истории (строка памяти, вопрос памяти), —
          // остаток ответа: дописываем его к якорю, а не теряем. Анонс
          // говорится один раз на факт, повтора не будет (§52 В3).
          const rest = ours
            .reduce((left, t) => left.replace(t.content.trim(), ""), answer)
            .split(/\n{2,}/)
            .map((part) => part.trim())
            .filter(Boolean)
            .join("\n\n");
          const merged: Turn = {
            ...anchor,
            content: rest ? `${anchor.content}\n\n${rest}` : anchor.content,
            buttons,
          };
          setTurns(seen.slice(0, lastShown + 1).map((t) => (t === anchor ? merged : t)));
          return;
        }
        // Ответа нового хода в истории не нашлось (запись консьержа — по
        // возможности; ответ без текста, только с кнопками; после него в
        // истории легло что-то ещё): ленту берём из истории, а ответ — и
        // вопрос, если его там нет, — дописываем сами. Дубль лучше потери.
        const at = new Date().toISOString();
        setTurns((prev) => {
          const mine = prev.find((t) => t.id === attempt.localId);
          const known =
            mine !== undefined &&
            seen.some((t) => t.role === "user" && t.content.trim() === mine.content.trim());
          const question = mine !== undefined && !known ? [{ ...mine, created_at: at }] : [];
          const reply: Turn[] =
            answer.trim() !== "" || buttons.length > 0
              ? [{ id: nextLocalId(), role: "assistant", content: answer, tool: "", created_at: at, buttons }]
              : [];
          return [...seen, ...question, ...reply];
        });
        return;
      } catch {
        // Дочитать не вышло — ответ на новый вопрос всё равно показываем.
      }
    }
    const at = new Date().toISOString();
    setTurns((prev) => [
      // Время под репликой человека — только теперь: сервер её принял.
      ...prev.map((t) => (t.id === attempt.localId ? { ...t, created_at: at } : t)),
      { id: nextLocalId(), role: "assistant", content: answer, tool: "", created_at: at, buttons },
    ]);
  }, []);

  const run = useCallback(
    async (attempt: Attempt) => {
      setSending(true);
      setFailure(null);
      let polls = 0;
      // Ключ перевыпускается не больше раза за прогон: у свежего ключа
      // повтора быть не может, и второй `failed` подряд — не повод крутиться.
      let rekeyed = false;
      try {
        for (;;) {
          try {
            const res = await customerAylaApi.ask(attempt.text, attempt.requestId);
            if (!aliveRef.current) return;
            attemptRef.current = null;
            await showAnswer(attempt, res);
            return;
          } catch (err) {
            if (!aliveRef.current) return;
            if (!(err instanceof ApiError) || err.slug !== DUPLICATE_REQUEST) {
              // Ключ занят упавшим ходом: под ним ответа не будет никогда.
              if (err instanceof ApiError && err.slug === TURN_FAILED) {
                attempt.requestId = newRequestId();
              }
              const next = failureOf(err);
              if (next.kind !== "retry") attemptRef.current = null;
              setFailure(next);
              return;
            }
            // Повтор тем же ключом: сервер говорит, чем кончился первый ход.
            const outcome = err.details?.turn;
            if (outcome === "failed") {
              // Хода нет и не будет — спрашиваем заново, не заставляя жать ещё
              // раз. Ключ прежний занят упавшим ходом.
              attempt.requestId = newRequestId();
              if (rekeyed) {
                setFailure({ kind: "retry", text: CLIENT_CHAT_COPY.failed });
                return;
              }
              rekeyed = true;
              continue;
            }
            if (outcome === "running") {
              if (polls >= RUNNING_POLLS) {
                setFailure(UNKNOWN_OUTCOME);
                return;
              }
              polls += 1;
              await pause(runningPollMs);
              if (!aliveRef.current) return;
              continue;
            }
            // `done` — ответ в истории. Сюда же попадает 409 без отметки
            // (сервер до этой правки): что сервер знает, то и показываем.
            try {
              const res = await customerAylaApi.history();
              if (!aliveRef.current) return;
              attemptRef.current = null;
              abandonedRef.current = false;
              setTurns(visibleMessages(res.messages));
            } catch {
              // Ответ есть, прочитать не вышло — ключ прежний, повтор перечитает.
              if (aliveRef.current) setFailure(UNKNOWN_OUTCOME);
            }
            return;
          }
        }
      } finally {
        if (aliveRef.current) setSending(false);
      }
    },
    [runningPollMs, showAnswer],
  );

  const send = useCallback(
    (rawText: string, shownAs?: string) => {
      const text = rawText.trim();
      // До прихода истории вопрос не уходит: она заменила бы ленту вместе с ним.
      if (!text || sending || !online || loadingHistory) return;

      hapticSelection();
      setDraft("");
      // DRF-2151: команда/токен в ленте не рисуется. DRF-2799: тап быстрого
      // ответа уходит payload'ом, а на экране остаётся подписью.
      const shown = shownAs ?? text;
      let localId: string | null = null;
      if (!isHiddenTurn(shown)) {
        const id = nextLocalId();
        localId = id;
        setTurns((prev) => [
          ...prev,
          { id, role: "user", content: shown, tool: "", created_at: "" },
        ]);
      }
      if (attemptRef.current !== null) abandonedRef.current = true;
      const attempt: Attempt = { text, requestId: newRequestId(), localId };
      attemptRef.current = attempt;
      void run(attempt);
    },
    [loadingHistory, online, run, sending],
  );

  /** Нажатая кнопка сейчас исчезнет: фокус — на ленту, а не в никуда (и не в
   *  поле ввода: на телефоне это подняло бы клавиатуру поверх ответа). */
  const focusList = useCallback(() => {
    listRef.current?.focus({ preventScroll: true });
  }, []);

  const onRetry = useCallback(() => {
    const attempt = attemptRef.current;
    if (!attempt || sending || !online) return;
    hapticSelection();
    focusList();
    void run(attempt);
  }, [focusList, online, run, sending]);

  const overLimit = draft.length > MAX_QUESTION_CHARS;
  const lastId = turns.at(-1)?.id ?? null;
  const repliesOf = (t: Turn) => (t.id === lastId ? (t.buttons ?? []).filter(isActionable) : []);
  // Реплика без текста и без кнопок — молчание бота, а не сообщение.
  const shown = turns.filter((t) => t.content.trim() !== "" || repliesOf(t).length > 0);

  return (
    <>
      <div
        ref={listRef}
        tabIndex={-1}
        className="cchat-list"
        role="log"
        aria-label="Разговор с Ayla"
      >
        {loadingHistory ? (
          <p className="cchat-note" aria-live="polite">
            Загружаю диалог…
          </p>
        ) : null}

        {!loadingHistory && startedEmpty ? (
          <div className="cchat-row">
            <AylaMark />
            <div className="cchat-turn">
              <span className="visually-hidden">Ayla</span>
              <div className="cchat-bubble cchat-bubble--ayla">
                <p>{greeting}</p>
              </div>
            </div>
          </div>
        ) : null}

        {shown.map((t, i) => {
          const mine = t.role === "user";
          const prev = shown[i - 1];
          // Знак Ayla — один на подряд идущие её реплики; приветствие считается.
          const opensGroup = !mine && (prev ? prev.role === "user" : !startedEmpty);
          const when = formatTurnWhen(t.created_at);
          const replies = repliesOf(t);
          const parts = paragraphs(t.content);
          return (
            <div key={t.id} className="cchat-item">
              {parts.length > 0 ? (
                <div className={mine ? "cchat-row cchat-row--mine" : "cchat-row"}>
                  {mine ? null : <AylaMark blank={!opensGroup} />}
                  <div className="cchat-turn">
                    {opensGroup ? <span className="visually-hidden">Ayla</span> : null}
                    <div
                      className={
                        mine ? "cchat-bubble cchat-bubble--mine" : "cchat-bubble cchat-bubble--ayla"
                      }
                    >
                      {parts.map((p, n) => (
                        <p key={n}>{p}</p>
                      ))}
                    </div>
                    {when ? (
                      <time className="cchat-time" dateTime={t.created_at}>
                        {when}
                      </time>
                    ) : null}
                  </div>
                </div>
              ) : null}
              {/* DRF-2799: кнопки — только под последним ответом, как клавиатура
                  в чате бота; старые не жмутся заново. */}
              {replies.length > 0 ? (
                <ul className="cchat-replies" aria-label="Варианты ответа">
                  {replies.map((b) => (
                    <li key={`${b.label}-${b.payload ?? b.url ?? b.open_app ?? ""}`}>
                      <RecordButton
                        variant="soft"
                        disabled={sending || (!online && !b.open_app)}
                        onClick={() => {
                          if (b.url) return openExternalLink(b.url);
                          if (b.payload) {
                            focusList();
                            return send(b.payload, b.label);
                          }
                          // DRF-2885 — переход внутри Mini App, не новый ход.
                          const route = quickReplyRoute(b);
                          if (route) onOpen(route);
                        }}
                      >
                        {b.label}
                      </RecordButton>
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          );
        })}

        {sending ? (
          <div className="cchat-row">
            <AylaMark />
            <p className="cchat-bubble cchat-bubble--ayla cchat-typing" aria-live="polite">
              <span className="cchat-dots" aria-hidden="true">
                <i />
                <i />
                <i />
              </span>
              Ayla думает…
            </p>
          </div>
        ) : null}
      </div>

      {!online ? (
        <Notice
          icon="offline"
          title={RECORDS_ERROR_COPY.offlineTitle}
          text={RECORDS_ERROR_COPY.offlineBody}
        />
      ) : failure?.kind === "open_from_max" ? (
        <OpenFromMaxBody />
      ) : failure?.kind === "refused" ? (
        <Notice icon="alert" danger title={failure.title} text={failure.text} />
      ) : failure?.kind === "retry" ? (
        // Заголовок кадра 15 — у всякого сбоя, после которого есть повтор;
        // у отказа входа заголовок свой (`authErrorCopy`).
        <Notice
          icon="alert"
          danger
          title={failure.title ?? CLIENT_CHAT_COPY.failedTitle}
          text={failure.text}
        >
          <RecordButton variant="primary" onClick={onRetry} disabled={sending}>
            {RECORDS_ERROR_COPY.retry}
          </RecordButton>
          <RecordButton variant="soft" href={SUPPORT_DEEPLINK}>
            {RECORDS_ERROR_COPY.support}
          </RecordButton>
        </Notice>
      ) : null}

      {/* Метка конца — ПОСЛЕ карточки состояния: прокрутка ведёт к ней, и
          карточка с «Попробовать снова» встаёт над полем ввода, а не под ним. */}
      <div ref={listEndRef} className="cchat-list__end" />

      <form
        className="cchat-compose"
        onSubmit={(e) => {
          e.preventDefault();
          send(draft);
        }}
      >
        <div className="cchat-compose__row">
          <textarea
            className="cchat-compose__input"
            aria-label="Вопрос к Ayla"
            placeholder={CLIENT_CHAT_COPY.placeholder}
            value={draft}
            rows={1}
            onChange={(e) => setDraft(e.target.value)}
          />
          <button
            type="submit"
            className="cchat-compose__send"
            aria-label="Отправить"
            disabled={
              sending || !online || loadingHistory || draft.trim().length === 0 || overLimit
            }
          >
            <svg
              width="22"
              height="22"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M12 19V5M5.5 11.5 12 5l6.5 6.5" />
            </svg>
          </button>
        </div>
        {overLimit ? (
          <p className="cchat-compose__counter">
            {draft.length} / {MAX_QUESTION_CHARS}
          </p>
        ) : null}
      </form>
    </>
  );
}
