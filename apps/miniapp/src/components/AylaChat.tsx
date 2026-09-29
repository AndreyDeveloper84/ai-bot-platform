/**
 * Диалог с Ayla — один каркас на мастера и на администратора (DRF-2119).
 *
 * Лента реплик, поле ввода, карточка предложения. Что за ассистент и куда
 * ходить — решает вызывающий экран через `api` (тройка history / ask /
 * confirm): мастерский (`/master/ayla`, `lib/master-api`) и админский
 * (`/admin/ayla`, `lib/admin-api`) экраны — два субъекта одного каркаса,
 * не два каркаса. До DRF-2119 всё это жило внутри `MasterAylaScreen`;
 * вынесено без смены поведения.
 *
 * # Два вида подтверждения
 *
 * Предложение (`pending_action`) — это то, что Ayla собирается сделать, но
 * не сделала (DRF-1180). У мастера оно всегда с талоном: «Подтвердить» →
 * `POST /assistant/confirm`. У администратора есть второй вид —
 * `confirm_kind: "open"`: сервер ничего не делает, кнопка открывает форму
 * Mini App по `open_url` с предзаполнением (черновик записи), и саму
 * запись создаёт человек в форме. Без `onOpen` такой карточке некуда
 * вести — вызывающий экран обязан его дать, если его API умеет `open`.
 *
 * # Карточки (DRF-2153, М-5, макет DRF-1187)
 *
 * Ответ может нести `cards` — структуру вместо абзаца (окна свободного
 * времени, день, уточнение клиента, «занято» с вариантами, «последние
 * известные данные»). Карточки рисуются под репликой, к которой пришли;
 * выбор из карточки — новая фраза с уточнением `select`. Предложение
 * записи (`pending_action.details`) — карточка «Проверьте запись» с
 * четырьмя строками; результат — ✓ «Запись создана» + «Открыть запись».
 * `startScreen` — стартовый экран пустого диалога (контекст дня + чипы);
 * админский экран его не передаёт и видит прежнее приглашение.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { ApiError } from "../lib/api";
import type {
  AylaBookingDetails,
  AylaCard,
  AylaDayOffDetails,
  AylaSelect,
} from "../lib/master-api";
import { hapticImpact, hapticSelection, signalReady } from "../lib/max-sdk";
import {
  AylaCards,
  BookingCreatedCard,
  BookingReviewRows,
  CARD_COPY,
  InfoHint,
} from "./AylaCards";

/** Тот же потолок, что у сервера (`views_assistant.MAX_QUESTION_CHARS`). */
export const MAX_QUESTION_CHARS = 1000;

/**
 * Жив ли ещё талон подтверждения (DRF-2373).
 *
 * Сервер говорит это в `details.retriable` (`master_api/views_assistant.py`,
 * `admin_api/views_assistant.py` — оба вида, словарь слагов у них общий).
 * Умолчание здесь — «мёртв», и оно выбрано нарочно: ошибка в эту сторону
 * стоит человеку лишнего вопроса, ошибка в обратную возвращает кнопку,
 * которая не может сработать.
 *
 * Поэтому `true` требуется буквально: отсутствующее поле (старый сервер,
 * не-JSON 5xx, сетевой сбой) живучестью не считается.
 */
function isOfferStillLive(err: unknown): boolean {
  return err instanceof ApiError && err.details?.retriable === true;
}

/** Карточки, приложенные к отказу сервером; их не было — пусто. */
function refusalCards(err: unknown): AylaCard[] {
  if (!(err instanceof ApiError)) return [];
  const cards = err.details?.cards;
  return Array.isArray(cards) ? (cards as AylaCard[]) : [];
}

const FAILED_TEXT = "Не получилось отправить. Попробуйте ещё раз.";
const DECLINED_TEXT = "Хорошо, ничего не меняю.";

export interface AylaChatMessage {
  id: string;
  /** "user" | "assistant" */
  role: string;
  content: string;
  tool: string;
  created_at: string;
}

export interface AylaChatPendingAction {
  action: string;
  summary: string;
  confirm_label: string;
  token: string;
  expires_in_sec: number;
  /** "token" (умолчание, подтверждение на сервере) | "open" (дверь в Mini App). */
  confirm_kind?: string;
  /** Куда ведёт дверь при `confirm_kind === "open"`. */
  open_url?: string;
  /** Строки карточки предложения (DRF-2153): запись или рабочий день. */
  details?: AylaBookingDetails | AylaDayOffDetails;
}

export interface AylaChatConfirmResult {
  answer: string;
  /** `false` — не выполнено («занято», «проверяем результат»). */
  executed?: boolean;
  open?: { url: string; label: string } | null;
  cards?: AylaCard[];
  details?: AylaBookingDetails | AylaDayOffDetails | null;
}

export interface AylaChatApi {
  history: () => Promise<{ messages: AylaChatMessage[] }>;
  ask: (
    text: string,
    select?: AylaSelect,
  ) => Promise<{
    answer: string;
    pending_action: AylaChatPendingAction | null;
    cards?: AylaCard[];
  }>;
  confirm: (token: string) => Promise<AylaChatConfirmResult>;
}

export interface AylaChatProps {
  api: AylaChatApi;
  /** Приглашение на пустом экране. */
  greeting: string;
  /** Дверь для `confirm_kind === "open"` — переход внутри Mini App. */
  onOpen?: (url: string) => void;
  /** `aria-label` ленты — какой это диалог. */
  logLabel?: string;
  /** Подпись отказа от предложения: у мастера — «Отмена» (макет DRF-1187). */
  declineLabel?: string;
  /** Стартовый экран пустого диалога (контекст дня + чипы); чип шлёт фразу через `send`. */
  startScreen?: (send: (text: string) => void) => ReactNode;
}

/** Черновая реплика, ещё не подтверждённая сервером. */
interface LocalMessage extends AylaChatMessage {
  pending?: boolean;
  /** Карточки под репликой (DRF-2153). */
  cards?: AylaCard[];
  /** Результат подтверждения — карточка ✓ «Запись создана» вместо пузыря. */
  created?: {
    details: AylaBookingDetails | null;
    open: { url: string; label: string } | null;
  };
}

function isBookingDetails(
  details: AylaBookingDetails | AylaDayOffDetails | null | undefined,
): details is AylaBookingDetails {
  return !!details && !("kind" in details);
}

let localSeq = 0;
// DRF-2151 — второй слой поверх фильтра бэкенда: команда или токен
// приглашения на экране не рисуются никогда, даже если история пришла
// со старого бэкенда. Формы — те же, что читает салонный бот.
const HIDDEN_TURN =
  /^\/|master_invite_[0-9a-fA-F-]{8,}|\binv_[A-Za-z0-9]{4,}\b/;
// Код сотрудника — ровно четыре знака алфавита кодов после AYLA в любом
// регистре (staff_invites.CODE_ALPHABET); «AYLA Beauty» — не код.
const TYPED_CODE = /\bAYLA[-_ ]?[23456789ABCDEFGHJKMNPQRSTUVWXYZ]{4}\b/i;

export function isHiddenTurn(content: string): boolean {
  const text = (content || "").trim();
  return HIDDEN_TURN.test(text) || TYPED_CODE.test(text);
}

export function visibleMessages<T extends { content: string }>(
  messages: T[],
): T[] {
  return messages.filter((m) => !isHiddenTurn(m.content));
}

function localMessage(role: string, content: string): LocalMessage {
  localSeq += 1;
  return {
    id: `local-${localSeq}`,
    role,
    content,
    tool: "",
    created_at: "",
    pending: true,
  };
}

export function AylaChat({
  api,
  greeting,
  onOpen,
  logLabel = "Диалог с Ayla",
  declineLabel = "Не надо",
  startScreen,
}: AylaChatProps) {
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [sending, setSending] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState<AylaChatPendingAction | null>(null);
  const [error, setError] = useState("");

  const listEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .history()
      .then((res) => {
        if (!alive) return;
        setMessages(visibleMessages(res.messages));
      })
      .catch(() => {
        // История — не условие разговора. Пустой экран с приглашением
        // честнее, чем ошибка на месте диалога, которого ещё нет.
        if (alive) setMessages([]);
      })
      .finally(() => {
        if (!alive) return;
        setLoadingHistory(false);
        signalReady();
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `api` — объект экрана, на одном монтировании не меняется; история грузится один раз. Где перезапуск нужен, зависимость стоит: см. `[api, sending]` ниже
  }, []);

  useEffect(() => {
    // `scrollIntoView` есть не везде (jsdom в тестах, старые webview) —
    // прокрутка к последней реплике удобна, но диалог не должен от неё
    // зависеть.
    const end = listEndRef.current;
    if (end && typeof end.scrollIntoView === "function") {
      end.scrollIntoView({ block: "end" });
    }
  }, [messages.length, pending]);

  // Последний вопрос — для «Проверить снова» (данные могли устареть).
  const lastQuestion = useRef<{ text: string; select?: AylaSelect } | null>(
    null,
  );

  const send = useCallback(
    async (rawText: string, select?: AylaSelect) => {
      const text = rawText.trim();
      if (!text || sending) return;

      hapticSelection();
      setDraft("");
      setError("");
      // Новый вопрос отменяет висящее предложение: подтверждать сводку,
      // на которую сверху лёг другой разговор, человек не должен.
      setPending(null);
      // DRF-2151: команда/токен на экране не рисуется и до перезахода.
      if (!isHiddenTurn(text)) {
        setMessages((prev) => [...prev, localMessage("user", text)]);
      }
      lastQuestion.current = select ? { text, select } : { text };
      setSending(true);
      try {
        const res = await api.ask(text, select);
        // Предложение показывается карточкой, а не пузырём: иначе один и
        // тот же текст стоял бы на экране дважды — сводкой и репликой.
        // На сервере он записан репликой в любом случае, так что после
        // перезахода диалог читается целиком.
        if (res.pending_action === null) {
          setMessages((prev) => [
            ...prev,
            {
              ...localMessage("assistant", res.answer),
              cards: res.cards ?? [],
            },
          ]);
        }
        setPending(res.pending_action);
      } catch (err) {
        // DRF-2451: здесь `detail` НЕ снимается. Это намеренный носитель
        // согласованного русского: отказы действий ассистента пишутся
        // словами владельца на сервере (`assistant.py`, `assistant_actions.py`
        // — «подтверждение устарело — спросите заново»), и текст отказа тут
        // же говорит человеку, что делать (DRF-2373).
        const detail =
          err instanceof ApiError && err.detail ? err.detail : FAILED_TEXT;
        setError(detail);
      } finally {
        setSending(false);
      }
    },
    [api, sending],
  );

  const onSend = useCallback(() => void send(draft), [send, draft]);

  const onRecheck = useCallback(() => {
    const last = lastQuestion.current;
    if (last) void send(last.text, last.select);
  }, [send]);

  const onConfirm = useCallback(async () => {
    if (pending === null || confirming) return;
    hapticImpact("heavy");
    if (pending.confirm_kind === "open") {
      // Дверь: сервер ничего не делает, форму открывает Mini App, а запись
      // создаст человек уже там — кнопкой формы.
      const url = pending.open_url ?? "";
      setPending(null);
      setMessages((prev) => [
        ...prev,
        localMessage("assistant", pending.summary),
      ]);
      if (url && onOpen) onOpen(url);
      return;
    }
    setConfirming(true);
    setError("");
    try {
      const res = await api.confirm(pending.token);
      setPending(null);
      const created =
        res.executed !== false && isBookingDetails(res.details)
          ? { details: res.details, open: res.open ?? null }
          : undefined;
      setMessages((prev) => [
        ...prev,
        {
          ...localMessage("assistant", res.answer),
          cards: res.cards ?? [],
          created,
        },
      ]);
    } catch (err) {
      // DRF-2451: `detail` не снимается — носитель согласованного русского
      // (отказы действий ассистента, DRF-2373). Подробнее — выше по файлу.
      const detail =
        err instanceof ApiError && err.detail ? err.detail : FAILED_TEXT;
      if (isOfferStillLive(err)) {
        // Талон цел, отказало исполнение («занято», салон отклонил). Карточка
        // остаётся — повтор осмыслен, и человек вправе нажать ещё раз.
        setError(detail);
      } else {
        // DRF-2373. Талон мёртв: устарел, не читается или выписан не этому
        // человеку. Его аргументы лежат внутри подписи, поэтому второй нажим
        // пошлёт ровно то же самое и получит ровно тот же отказ.
        //
        // До этой правки карточка здесь ОСТАВАЛАСЬ, и на экране был виден
        // единственный обречённый выход. Это не отсутствие выхода, а
        // нарисованный выход, которого нет, — молчаливая кнопка была бы
        // честнее.
        //
        // Карточку снимаем, текст отказа кладём обычной репликой: он сам
        // говорит, что делать («спросите заново»), и поле ввода на месте,
        // так что совет исполним. Заодно FAILED_TEXT («Попробуйте ещё раз»)
        // перестаёт попадать туда, где повторять нечем.
        setPending(null);
        setMessages((prev) => [
          ...prev,
          {
            ...localMessage("assistant", detail),
            cards: refusalCards(err),
          },
        ]);
      }
    } finally {
      setConfirming(false);
    }
  }, [api, pending, confirming, onOpen]);

  const onDecline = useCallback(() => {
    hapticSelection();
    setPending(null);
    setMessages((prev) => [...prev, localMessage("assistant", DECLINED_TEXT)]);
  }, []);

  const overLimit = draft.length > MAX_QUESTION_CHARS;

  return (
    <>
      <div className="ayla-list" role="log" aria-label={logLabel}>
        {/* Стартовый экран — всегда сверху: история общая с ботом, и мастер,
            который уже говорил с Ayla, иначе не увидел бы контекст и чипы. */}
        {startScreen && !loadingHistory
          ? startScreen((text) => void send(text))
          : null}
        {loadingHistory ? (
          <p className="ayla-empty" aria-live="polite">
            Загружаю диалог…
          </p>
        ) : messages.length === 0 && !startScreen ? (
          <p className="ayla-empty">{greeting}</p>
        ) : (
          messages.map((m) =>
            m.created ? (
              <BookingCreatedCard
                key={m.id}
                details={m.created.details}
                open={m.created.open}
              />
            ) : (
              <div key={m.id}>
                {/* Карточка «занято» сама несёт заголовок — тот же текст пузырём был бы дважды. */}
                {m.cards?.some((c) => c.kind === "slot_taken") &&
                m.content === CARD_COPY.slotTakenTitle ? null : (
                  <div
                    className={
                      m.role === "user"
                        ? "ayla-bubble ayla-bubble--mine"
                        : "ayla-bubble ayla-bubble--ayla"
                    }
                  >
                    <p className="ayla-bubble__content">{m.content}</p>
                  </div>
                )}
                {m.cards && m.cards.length > 0 ? (
                  <AylaCards
                    cards={m.cards}
                    onSelect={(text, select) => void send(text, select)}
                    onRecheck={onRecheck}
                  />
                ) : null}
              </div>
            ),
          )
        )}

        {sending ? (
          <p className="ayla-typing" aria-live="polite">
            Ayla думает…
          </p>
        ) : null}

        {pending !== null ? (
          <section
            className="ayla-confirm"
            aria-labelledby="ayla-confirm-title"
          >
            <h2 className="ayla-confirm__title" id="ayla-confirm-title">
              {isBookingDetails(pending.details)
                ? CARD_COPY.reviewTitle
                : pending.details?.kind === "day_off"
                  ? pending.details.title
                  : pending.confirm_kind === "open"
                    ? "Черновик готов"
                    : "Подтвердите действие"}
            </h2>
            {isBookingDetails(pending.details) ? (
              // Макет 3A: четыре строки с иконками — не сводка одной строкой.
              <BookingReviewRows details={pending.details} />
            ) : pending.details?.kind === "day_off" ? (
              <ul className="ayla-card__list ayla-review">
                <li className="ayla-review__row">
                  <span aria-hidden="true">📅</span> {pending.details.date}
                </li>
                <li className="ayla-review__row">{pending.details.change}</li>
              </ul>
            ) : (
              <p className="ayla-confirm__summary">{pending.summary}</p>
            )}
            <div className="ayla-confirm__actions">
              <button
                type="button"
                className="ayla-btn ayla-btn--primary"
                onClick={onConfirm}
                disabled={confirming}
              >
                {confirming ? "Отправляю…" : pending.confirm_label}
              </button>
              <button
                type="button"
                className="ayla-btn ayla-btn--ghost"
                onClick={onDecline}
                disabled={confirming}
              >
                {declineLabel}
              </button>
            </div>
            {isBookingDetails(pending.details) ? (
              <InfoHint text={CARD_COPY.reviewHint} />
            ) : null}
          </section>
        ) : null}

        {error ? (
          <p className="ayla-error" role="alert">
            {error}
          </p>
        ) : null}

        <div ref={listEndRef} className="ayla-list__end" />
      </div>

      <form
        className="ayla-compose"
        onSubmit={(e) => {
          e.preventDefault();
          void onSend();
        }}
      >
        <div className="ayla-compose__row">
          <textarea
            className="ayla-compose__textarea"
            aria-label="Вопрос к Ayla"
            placeholder="Спросите Ayla…"
            value={draft}
            rows={1}
            onChange={(e) => setDraft(e.target.value)}
          />
          <button
            type="submit"
            className="ayla-compose__send"
            aria-label="Отправить"
            disabled={sending || draft.trim().length === 0 || overLimit}
          >
            ➤
          </button>
        </div>
        {overLimit ? (
          <p className="ayla-compose__counter ayla-compose__counter--over">
            {draft.length} / {MAX_QUESTION_CHARS}
          </p>
        ) : null}
      </form>
    </>
  );
}
