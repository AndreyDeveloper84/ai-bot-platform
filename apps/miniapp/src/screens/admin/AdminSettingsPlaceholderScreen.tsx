/**
 * Admin «Настройки» tab placeholder — salon-wide settings UI ships
 * separately. This screen exists only so the bottom tab has a
 * destination during MM5 frontend rollout.
 *
 * # Здесь же — вход в пилотную салонную админку (DRF-1235)
 *
 * Решение владельца 07.09.2026: мост остаётся посадкой, а вход в пилот
 * с него — явный. Причина названа владельцем: «Сегодня» уже живой, а
 * «Расписание» и «Ayla» функциональности не имеют, и делать их
 * посадкой значило бы вести человека в наполовину собранную
 * поверхность. Когда все три раздела будут готовы — посадка
 * переключается, а мост удаляется.
 *
 * Почему вход стоит именно тут, а не на «Дне» и не шестой вкладкой:
 *
 * * Этот экран УЖЕ носит навигацию между поверхностями —
 *   `SurfaceSwitchButton` («Сменить режим») живёт здесь и на двух
 *   зеркальных экранах настроек. Вход в ещё одну поверхность рядом с
 *   ним — не новый приём, а тот же самый.
 * * DRF-1235 отвёл «Настройкам» роль контейнера для подтверждённых
 *   функций пилота — дословно: «`Настройки` остаются только
 *   контейнером для реально подтверждённых функций пилота».
 * * Шестая вкладка сломала бы пин панели
 *   (`App.receptionSurface.test.tsx` держит пять подписей у владельца)
 *   и добавила бы элемент навигации, которого нет ни на одном макете.
 * * Ссылка на каждом экране моста — это пять мест вместо одного и
 *   пять поводов разъехаться.
 *
 * Вход виден владельцу и администратору. Проверка — `canOpenSalonPilot`,
 * а НЕ «раз уж экран открылся». Правила разные: сюда пускает
 * `isAdminTabAllowed(me, "settings")`, в пилот — `canOpenSalonPilot`.
 * Сегодня оба закрыты для ресепшн, но если «Настройки» ей когда-нибудь
 * откроют, вход в пилот не должен уехать следом: он привёл бы её на
 * экран отказа.
 */

import { useNavigate } from "react-router-dom";

import { AdminTabBar } from "../../components/AdminTabBar";
import type { MeResponse } from "../../lib/admin-api";
import { SurfaceSwitchButton } from "../../components/SurfaceSwitch";
import { hapticSelection } from "../../lib/max-sdk";
import { SALON_PILOT_LANDING, canOpenSalonPilot } from "../../lib/salon-pilot";

export function AdminSettingsPlaceholderScreen({ me }: { me: MeResponse }) {
  const navigate = useNavigate();
  return (
    <div className="screen">
      <header className="screen__header">
        <h1 className="screen__title">Настройки</h1>
      </header>
      <div className="callout" role="status">
        <p style={{ margin: 0 }}>Скоро здесь будут настройки салона.</p>
      </div>
      {canOpenSalonPilot(me) ? (
        <div className="callout" role="group" aria-label="Пилотная админка">
          {/*
            Формулировка нарочно не обещает содержимого. «Расписание» и
            «Ayla» сегодня пусты, и надпись говорит это прямо: человек,
            нажавший кнопку, должен знать, что он там увидит, ДО нажатия,
            а не после.
          */}
          <p style={{ margin: 0 }}>
            Пилотная админка салона: «Сегодня», «Расписание», «Ayla». Готов
            раздел «Сегодня» — «Расписание» и «Ayla» пока пустые.
          </p>
          <div style={{ marginTop: "var(--s-3)" }}>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => {
                hapticSelection();
                navigate(SALON_PILOT_LANDING);
              }}
            >
              Открыть пилотную админку
            </button>
          </div>
        </div>
      ) : null}
      {/*
        The way back out of a surface. Renders itself away for anyone
        holding a single role, so the ordinary receptionist never sees a
        control that would only confuse her.
      */}
      <SurfaceSwitchButton />
      <AdminTabBar me={me} />
    </div>
  );
}
