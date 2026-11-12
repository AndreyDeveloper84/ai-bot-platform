/**
 * Состояния экранов дневника питания (DRF-2954, кадр 17 доски «Дневник»):
 * значок в круге, прежняя фраза, кнопка.
 *
 * Вид — общих состояний клиента (`cr-state`), а не `RecordsState`: тот делает
 * фразу заголовком `<h2>` и уводит на него фокус. У дневника фраза — сообщение
 * о положении дел (`role="status"`), а не новый заголовок экрана, и фокус
 * человека она не трогает: состояния показываются с первого кадра экрана.
 *
 * Слова прежние, каждая фраза — своим абзацем.
 */
import type { ReactNode } from "react";

import { ApiError } from "../../lib/api";
import { isTransportRefusalSlug } from "../../lib/auth-error-copy";
import { OpenFromMaxBody } from "../OpenFromMaxScreen";
import { stateErrorCopy } from "../StateError";
import { RecordIcon, type RecordIconName } from "../records/RecordIcons";
import { RecordButton } from "../records/RecordsState";

interface Props {
  icon: RecordIconName;
  /** Тон круга: `danger` — сбой, остальное — нейтральное положение дел. */
  tone?: "accent" | "info" | "danger";
  /** Первая фраза — строкой покрупнее. */
  lead: string;
  /** Пояснение под ней. */
  hint?: string;
  children?: ReactNode;
}

export function DiaryState({ icon, tone = "info", lead, hint, children }: Props) {
  return (
    <div className="cr-state diary-state" role={tone === "danger" ? "alert" : "status"}>
      <span className={`cr-state__badge cr-state__badge--${tone}`}>
        <RecordIcon name={icon} size={40} />
      </span>
      <p className="diary-state__lead">{lead}</p>
      {hint ? <p className="diary-state__hint">{hint}</p> : null}
      {children ? <div className="cr-state__actions">{children}</div> : null}
    </div>
  );
}

/**
 * Сбой загрузки — копия `StateError` (DRF-1319, DRF-2446) в виде состояния:
 * отказ транспорта — «Открой из MAX» без повтора, прочее — прежняя фраза и
 * «Попробовать снова». Роль `alert`, как была.
 */
export function DiaryLoadError({ err, onRetry }: { err: unknown; onRetry: () => void }) {
  if (err instanceof ApiError && isTransportRefusalSlug(err.slug)) return <OpenFromMaxBody />;
  const copy = stateErrorCopy(err);
  return (
    <DiaryState
      icon={copy.noResponse ? "offline" : "alert"}
      tone="danger"
      lead={copy.title ?? copy.body}
      hint={copy.title ? copy.body : undefined}
    >
      <RecordButton variant="primary" onClick={onRetry}>
        Попробовать снова
      </RecordButton>
    </DiaryState>
  );
}
