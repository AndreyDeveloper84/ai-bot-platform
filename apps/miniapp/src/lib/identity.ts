/**
 * Кто перед нами — одно определение на всё мини-приложение (DRF-1319 B/E,
 * решение владельца §124).
 *
 * До этого модуля «аноним» был определён дважды и независимо:
 * `CustomerBookingConfirmScreen.isAnonymous()` и
 * `CustomerSlotsScreen.isAnonymousCustomer()` — оба `getInitData() === ""`,
 * ноль общего кода, и ни одно не было серверным понятием. Совпадали,
 * пока никто не правил; разошлись бы при первой правке.
 *
 * §124 различает два понятия, и оба здесь:
 *
 *   identified channel user   MAX `initData` достоверно назвал человека
 *   registered Ayla subject   доменный субъект, к которому канал привязан
 *
 * Первое решается на клиенте — есть ли `initData` вообще
 * (`channelIdentity`). Второе решает ТОЛЬКО сервер — блок `identity`
 * в ответе `/auth/verify` (`subjectIdentity`): привязка делается там,
 * при входе, через `ensure_ayla_link`; клиент её не вычисляет и не
 * угадывает.
 *
 * Слова «аноним» и «гость» здесь отсутствуют намеренно. Внутри MAX
 * `initData` называет человека всегда, поэтому пустой `initData` — это
 * «канал не передал данные для входа» (отказ транспорта), а не «пришёл
 * гость». Имя состояния — `no_init_data`. Что показывать человеку в этом
 * состоянии — срез 1319-D, заперт решением о MAX OAuth; здесь меняется
 * только имя, экраны и тексты остаются прежними.
 */
import type { AuthVerifyResponse } from "./api";
import { getInitData } from "./max-sdk";

/**
 * Как человек опознан каналом.
 *
 * - `identified` — `initData` есть (настоящий из MAX или dev-подстановка
 *   `VITE_DEV_INIT_DATA`; для клиента разницы нет — сервер её называет
 *   сам, см. `SubjectIdentity.channel`).
 * - `no_init_data` — канал данных для входа не передал. Не «гость».
 */
export type ChannelIdentity = "identified" | "no_init_data";

export function channelIdentity(): ChannelIdentity {
  return getInitData() === "" ? "no_init_data" : "identified";
}

/** Есть ли у нас, чем представиться серверу. */
export function hasChannelIdentity(): boolean {
  return channelIdentity() === "identified";
}

/**
 * Серверное утверждение о субъекте — как пришло из `/auth/verify`.
 * Клиент его не производит: только читает.
 */
export type SubjectIdentity = NonNullable<AuthVerifyResponse["identity"]>;

/**
 * Блок `identity` из ответа `/auth/verify`, если сервер его прислал.
 * `null` — сервер старой версии без блока; вызывающий обязан считать это
 * «неизвестно», а не «не привязан».
 */
export function subjectIdentity(resp: AuthVerifyResponse): SubjectIdentity | null {
  return resp.identity ?? null;
}
