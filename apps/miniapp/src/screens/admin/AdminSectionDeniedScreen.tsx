/**
 * Честный отказ на закрытом разделе салонной поверхности (DRF-1522,
 * DRF-1552).
 *
 * Скрыть вкладку мало: адрес остаётся в адресной строке, в старых
 * диалогах бота и в закладках. Ресепшн, пришедшая по прямой ссылке на
 * «Чаты», раньше видела либо пустой экран, либо карточку ошибки после
 * 403 — и в обоих случаях не понимала, что произошло и куда идти.
 *
 * Экран называет причину и даёт выход. Нижняя панель под ним — та же,
 * что и везде, то есть уже урезанная: две вкладки, из которых ни одна
 * не ведёт обратно сюда.
 *
 * Права это не подменяет. Бэкенд по-прежнему отвечает 403 на
 * `/admin/threads/` — здесь просто не доводят до запроса. Единственное
 * исключение, сделанное осознанно, — `GET /api/v1/admin/day/`
 * (DRF-1552): его ресепшн открывает, поэтому он и не отдан этому экрану.
 */

import { useNavigate } from "react-router-dom";

import { AdminTabBar } from "../../components/AdminTabBar";
import type { MeResponse } from "../../lib/admin-api";
import { adminLandingPath } from "../../lib/admin-tabs";
import { hapticSelection } from "../../lib/max-sdk";

export function AdminSectionDeniedScreen({
  me,
  section,
}: {
  me: MeResponse;
  /** Название раздела — то же слово, что стояло бы на вкладке. */
  section: string;
}) {
  const navigate = useNavigate();
  const home = adminLandingPath(me);
  return (
    <div className="screen">
      {/*
        Без `screen__header`: у этого класса нет правила в `src/styles/`,
        он держится только на baseline контракта стилей
        (`tools/lint/miniapp_style_contract.py`), а baseline умеет
        уменьшаться, но не расти. Заголовок стоит на `screen__title` —
        у него правило есть.
      */}
      <h1 className="screen__title">{section}</h1>
      <div className="callout callout--danger" role="alert">
        <p style={{ margin: 0 }}>
          Раздел «{section}» открыт владельцу и администратору салона. У вас
          доступ ресепшн — он не даёт сюда войти.
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
