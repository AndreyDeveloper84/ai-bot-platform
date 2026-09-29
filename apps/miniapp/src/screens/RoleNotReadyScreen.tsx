/**
 * «Роль ещё не подтверждена» — видимая замена молчаливому подлогу
 * (DRF-1434).
 *
 * `CustomerRoutes` заканчивается `<Route path="*" element={<HelloScreen />} />`.
 * Любой адрес, которого там нет, рисовал клиентское приветствие — включая
 * `/master/dashboard` и `/admin/team`. Человек с ролью мастера, попавший
 * туда до того, как бэкенд отдал ему роль, видел «Помогу записаться в
 * студию» и не имел ни одного признака, что что-то пошло не так: пусто
 * было неотличимо от работает.
 *
 * Этот экран занимает `/master/*` и `/admin/*` в клиентском дереве и
 * говорит вслух: роли пока нет. «Обновить» перечитывает `/api/v1/me` —
 * ровно тот запрос, ответ которого решает, какое дерево смонтировано,
 * поэтому кнопка не косметическая: если роль уже выдана, следующий ответ
 * переключит поверхность.
 */

import { useNavigate } from "react-router-dom";
import { ScreenLayout } from "../components/ScreenLayout";
import { useReloadMe } from "../state/boot";
import { backTo } from "../lib/screen-back";

/** Возврат (DRF-1493): в клиентскую часть — то же, что делает кнопка
 * «Открыть клиентскую часть» в теле экрана. */
const BACK = backTo("/");

export type PendingSurface = "master" | "admin";

const COPY: Record<PendingSurface, { title: string; body: string }> = {
  master: {
    title: "Доступ мастера ещё не подтверждён",
    body: "Мы пока не видим у вас роль мастера в этой студии. Если вы только что приняли приглашение — нажмите «Обновить». Если не помогает, напишите в студию: приглашение могло не дойти до конца.",
  },
  admin: {
    title: "Доступ администратора ещё не подтверждён",
    body: "Мы пока не видим у вас прав администратора в этой студии. Если доступ выдали только что — нажмите «Обновить». Если не помогает, напишите владельцу студии.",
  },
};

export function RoleNotReadyScreen({ surface }: { surface: PendingSurface }) {
  const reloadMe = useReloadMe();
  const navigate = useNavigate();
  const copy = COPY[surface];
  return (
    <ScreenLayout back={BACK} title={copy.title}>
      <div className="hello-error" role="alert">
        <p>{copy.body}</p>
        <div
          style={{
            display: "flex",
            gap: "var(--s-2)",
            marginTop: "var(--s-3)",
            flexWrap: "wrap",
          }}
        >
          <button type="button" className="btn-secondary" onClick={reloadMe}>
            Обновить
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => navigate("/", { replace: true })}
          >
            Открыть клиентскую часть
          </button>
        </div>
      </div>
    </ScreenLayout>
  );
}
