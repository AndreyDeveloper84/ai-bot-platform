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


import { AdminTabBar } from "../../components/AdminTabBar";
import type { MeResponse } from "../../lib/admin-api";
import { SurfaceSwitchButton } from "../../components/SurfaceSwitch";
import { useSalonSectionBack } from "../../hooks/useSalonSectionBack";

export function AdminSettingsPlaceholderScreen({ me }: { me: MeResponse }) {
  // DRF-2115: «Настройки» открываются из аватара — системная «назад»
  // ведёт в «Сегодня». Вход в пилот отсюда снят: пилот и есть посадка.
  useSalonSectionBack(me);
  return (
    <div className="screen">
      <header className="screen__header">
        <h1 className="screen__title">Настройки</h1>
      </header>
      <div className="callout" role="status">
        <p style={{ margin: 0 }}>Скоро здесь будут настройки салона.</p>
      </div>
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
