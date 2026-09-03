/**
 * Согласие на обработку медданных — клиент (DRF-1453).
 *
 * Данные о питании — специальная категория по 152-ФЗ ст. 10. Согласие на неё
 * не поглощается общим согласием ст. 6, поэтому у него отдельный ресурс, а не
 * поле в общем объекте согласий: единый PATCH «сохрани все галочки» снова
 * сделал бы медданные строкой в списке «принимаю всё».
 *
 *   GET    /api/v1/customer/me/health-consent/  → HealthConsentState
 *   POST   /api/v1/customer/me/health-consent/  → HealthConsentState
 *     Тело: `{"document_version": "<версия показанного раскрытия>"}`.
 *     Версия обязательна и сверяется сервером: в журнал попадает то, ЧТО
 *     человеку показали, а не абстрактное «да». Расхождение → 409
 *     `stale_disclosure`.
 *   DELETE /api/v1/customer/me/health-consent/  → HealthConsentState
 *     Отзыв. Идемпотентен; строки согласий на сервере не удаляются.
 *
 * Ручка оперирует ровно фактом согласия — телефон и другие идентификаторы
 * по этому пути не читаются и не передаются (DRF-1039).
 *
 * В отличие от `customer-profile.ts` это НЕ заглушка: эндпоинт существует,
 * и экран показывает состояние в проде тоже. Именно поэтому строка согласия
 * на медданные в профиле живёт вне `STUB_SURFACES_ENABLED`.
 */

import { ApiError, request } from "./api";

/**
 * Версия раскрытия, которое показывает мини-приложение. Меняется ВМЕСТЕ с
 * текстом раскрытия и с `HEALTH_CONSENT_DOCUMENT_VERSION` в
 * `apps/consent/health.py` — сервер отвергает версию, которой не знает.
 * Поднятие версии = согласия, выданные под старый текст, перестают проходить
 * version-строгую проверку, то есть человека спрашивают заново.
 */
export const HEALTH_CONSENT_DOCUMENT_VERSION = "health-data-v1";

export interface HealthConsentState {
  granted: boolean;
  /** ISO-время действующей выдачи; null когда согласия нет. */
  granted_at: string | null;
  /** Версия, под которой согласие СТОИТ (может отставать от текущей). */
  document_version: string;
  /** Версия, которую сервер считает актуальной прямо сейчас. */
  current_document_version: string;
}

/**
 * Раскрытие обновилось между показом и нажатием. Не ошибка ввода: человеку
 * нужно прочитать новый текст, поэтому это отдельный тип, а не общий сбой.
 */
export class StaleDisclosureError extends Error {
  constructor() {
    super("health-data disclosure version is stale");
    this.name = "StaleDisclosureError";
  }
}

const PATH = "/me/health-consent/";

/** Текущее состояние согласия. */
export function fetchHealthConsent(): Promise<HealthConsentState> {
  return request<HealthConsentState>(PATH);
}

/**
 * Выдать согласие под ту версию раскрытия, которую человек только что
 * прочитал. Идемпотентно.
 *
 * @throws {StaleDisclosureError} сервер обновил текст раскрытия — экран
 * обязан перечитать состояние и показать новый текст, а не «дожать» выдачу.
 */
export async function grantHealthConsent(
  documentVersion: string = HEALTH_CONSENT_DOCUMENT_VERSION,
): Promise<HealthConsentState> {
  try {
    return await request<HealthConsentState>(PATH, {
      method: "POST",
      body: JSON.stringify({ document_version: documentVersion }),
    });
  } catch (err) {
    if (err instanceof ApiError && err.slug === "stale_disclosure") {
      throw new StaleDisclosureError();
    }
    throw err;
  }
}

/** Отозвать согласие. Идемпотентно. */
export function withdrawHealthConsent(): Promise<HealthConsentState> {
  return request<HealthConsentState>(PATH, { method: "DELETE" });
}
