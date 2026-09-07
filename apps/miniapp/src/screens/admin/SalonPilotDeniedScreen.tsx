/**
 * Отказ на пилотной поверхности для ресепшн (DRF-1235).
 *
 * Отдельный экран, а не `AdminSectionDeniedScreen`. Тот рисует под собой
 * `AdminTabBar` — панель моста; здесь она правильная, потому что
 * ресепшн остаётся на мосту и ей туда возвращаться. Но её текст
 * говорит про раздел («Раздел «Чаты» открыт владельцу…»), а отказан
 * здесь не раздел, а вся поверхность целиком, и причина у отказа другая.
 * Склеить их одним пропом значило бы получить фразу, которая верна для
 * одного случая и врёт про второй.
 *
 * # Почему отказ, а не 403 с бэкенда
 *
 * Права это не подменяет: `require_admin_role` по-прежнему ответит 403
 * тому, кто придёт на салонные ручки. Здесь просто не доводят до
 * запроса — адрес остаётся в закладках и в старых сообщениях бота, и
 * человек, пришедший по прямой ссылке, должен увидеть отказ, а не
 * карточку ошибки загрузки.
 *
 * # Почему открытым вопросом, а не «пока нельзя»
 *
 * Какие салонные ручки открывать ресепшн — незакрытое решение
 * владельца. Формулировка не обещает, что доступ появится, и не
 * утверждает, что не появится.
 */

import { useNavigate } from "react-router-dom";

import { AdminTabBar } from "../../components/AdminTabBar";
import type { MeResponse } from "../../lib/admin-api";
import { adminLandingPath } from "../../lib/admin-tabs";
import { hapticSelection } from "../../lib/max-sdk";

export function SalonPilotDeniedScreen({ me }: { me: MeResponse }) {
  const navigate = useNavigate();
  const home = adminLandingPath(me);
  return (
    <div className="screen">
      <h1 className="screen__title">Салонная админка</h1>
      <div className="callout callout--danger" role="alert">
        <p style={{ margin: 0 }}>
          Салонная админка открыта владельцу и администратору салона. У вас
          доступ ресепшн — он сюда не пускает.
        </p>
      </div>
      <div style={{ marginTop: "var(--s-4)" }}>
        <button
          type="button"
          className="btn-secondary"
          onClick={() => {
            hapticSelection();
            navigate(home, { replace: true });
          }}
        >
          Вернуться в «День»
        </button>
      </div>
      <AdminTabBar me={me} />
    </div>
  );
}
