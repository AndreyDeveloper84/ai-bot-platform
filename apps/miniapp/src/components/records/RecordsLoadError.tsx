/**
 * Сбой загрузки в разделе «Записи» — один разбор на список, карточку записи
 * и перенос. Макет «Записи.png» (DRF-2952), кадры 14, 15, 16.
 *
 * Какой кадр когда:
 *
 *   - отказ транспорта (нет/битый/протухший вход) — не кадр, а «Открой Ayla
 *     из MAX» с возвратом в мессенджер (DRF-1893): повтор тут не поможет;
 *   - отказ входа — раскладка кадра 16, слова из `authErrorCopy` (DRF-1319
 *     D-1: отказ входа называется своим именем). Кнопок макета «Открыть
 *     настройки» и «Продолжить без данных» нет: настройки мессенджера
 *     приложение открыть не может, а без входа данных не бывает;
 *   - устройство без сети — кадр 14. Только по `navigator.onLine`: упавший
 *     `fetch` сам по себе отсутствия интернета не доказывает;
 *   - всё прочее — кадр 15, с дорогой в поддержку.
 *
 * Серверный `detail` человеку не показывается (DRF-2446) — он уже в журнале:
 * его пишет сам клиент API при каждом отказе.
 */
import { ApiError } from "../../lib/api";
import { authErrorCopy, isAuthRefusalSlug, isTransportRefusalSlug } from "../../lib/auth-error-copy";
import { SUPPORT_DEEPLINK } from "../../lib/customer-profile";
import { OpenFromMaxBody } from "../OpenFromMaxScreen";
import { RecordButton, RecordsState } from "./RecordsState";

export const RECORDS_ERROR_COPY = {
  offlineTitle: "Нет подключения к интернету",
  offlineBody: "Проверь подключение и попробуй ещё раз.",
  offlineRetry: "Повторить",
  failedBody: "Попробуй ещё раз. Если проблема повторится, напиши в поддержку.",
  retry: "Попробовать снова",
  support: "Написать в поддержку",
} as const;

interface Props {
  err: unknown;
  /** Есть ли у устройства сеть — `useOnline()` экрана. */
  online: boolean;
  onRetry: () => void;
  /** Что именно не загрузилось: «Не удалось загрузить записи». */
  title: string;
}

export function RecordsLoadError({ err, online, onRetry, title }: Props) {
  if (err instanceof ApiError && isTransportRefusalSlug(err.slug)) return <OpenFromMaxBody />;

  if (err instanceof ApiError && isAuthRefusalSlug(err.slug)) {
    const copy = authErrorCopy(err.slug);
    return (
      <RecordsState
        icon="lock"
        title={copy.title}
        body={<p>{copy.body}</p>}
        quiet
        actions={
          copy.retryLabel && (
            <RecordButton variant="primary" onClick={onRetry}>
              {copy.retryLabel}
            </RecordButton>
          )
        }
      />
    );
  }

  if (!online) {
    return (
      <RecordsState
        icon="offline"
        title={RECORDS_ERROR_COPY.offlineTitle}
        body={<p>{RECORDS_ERROR_COPY.offlineBody}</p>}
        quiet
        actions={
          <RecordButton variant="primary" onClick={onRetry}>
            {RECORDS_ERROR_COPY.offlineRetry}
          </RecordButton>
        }
      />
    );
  }

  return (
    <RecordsState
      icon="alert"
      tone="danger"
      title={title}
      body={<p>{RECORDS_ERROR_COPY.failedBody}</p>}
      quiet
      actions={
        <>
          <RecordButton variant="primary" onClick={onRetry}>
            {RECORDS_ERROR_COPY.retry}
          </RecordButton>
          <RecordButton variant="soft" href={SUPPORT_DEEPLINK}>
            {RECORDS_ERROR_COPY.support}
          </RecordButton>
        </>
      }
    />
  );
}
