/**
 * DRF-1522 — правило «кто что видит» отдельно от разметки.
 *
 * Три места читают это правило: панель вкладок, страж закрытых адресов и
 * посадка. Тест держит их согласованными в одном месте, а экранные
 * проверки (`App.receptionSurface.test.tsx`) убеждаются, что каждое из
 * трёх мест действительно его спрашивает.
 *
 * Тест умеет падать: замените `isReceptionOnly` на голое
 * `me.is_receptionist` — и «владелец с приёмной ролью» покраснеет.
 */
import { describe, expect, it } from "vitest";

import {
  ADMIN_TABS_FULL,
  adminLandingPath,
  adminTabsFor,
  isAdminTabAllowed,
  isReceptionOnly,
  type AdminRoleFlags,
} from "./admin-tabs";

const OWNER: AdminRoleFlags = {
  is_owner: true,
  is_admin: false,
  is_receptionist: false,
};
const ADMIN: AdminRoleFlags = {
  is_owner: false,
  is_admin: true,
  is_receptionist: false,
};
const RECEPTION: AdminRoleFlags = {
  is_owner: false,
  is_admin: false,
  is_receptionist: true,
};

describe("isReceptionOnly", () => {
  it("узнаёт ресепшн", () => {
    expect(isReceptionOnly(RECEPTION)).toBe(true);
  });

  it("не считает ресепшн владельца и администратора", () => {
    expect(isReceptionOnly(OWNER)).toBe(false);
    expect(isReceptionOnly(ADMIN)).toBe(false);
  });

  it("владелец с приёмной ролью остаётся владельцем", () => {
    // Управляющая роль поверх приёмной — не повод урезать поверхность.
    expect(isReceptionOnly({ ...OWNER, is_receptionist: true })).toBe(false);
    expect(isReceptionOnly({ ...ADMIN, is_receptionist: true })).toBe(false);
  });
});

describe("adminTabsFor", () => {
  it("у ресепшн три вкладки, без «Чатов» и «Настроек»", () => {
    expect(adminTabsFor(RECEPTION)).toEqual(["day", "team", "services"]);
  });

  it("у владельца и администратора пять — состав не изменился", () => {
    expect(adminTabsFor(OWNER)).toEqual([
      "day",
      "team",
      "services",
      "chats",
      "settings",
    ]);
    expect(adminTabsFor(ADMIN)).toEqual(ADMIN_TABS_FULL);
  });
});

describe("isAdminTabAllowed", () => {
  it("закрывает ресепшн «Чаты» и «Настройки»", () => {
    expect(isAdminTabAllowed(RECEPTION, "chats")).toBe(false);
    expect(isAdminTabAllowed(RECEPTION, "settings")).toBe(false);
  });

  it("оставляет ресепшн «День», «Команду» и «Услуги»", () => {
    expect(isAdminTabAllowed(RECEPTION, "day")).toBe(true);
    expect(isAdminTabAllowed(RECEPTION, "team")).toBe(true);
    expect(isAdminTabAllowed(RECEPTION, "services")).toBe(true);
  });

  it("владельцу открыто всё", () => {
    for (const tab of ADMIN_TABS_FULL) {
      expect(isAdminTabAllowed(OWNER, tab)).toBe(true);
    }
  });
});

describe("adminLandingPath", () => {
  it("ресепшн садится на «День»", () => {
    expect(adminLandingPath(RECEPTION)).toBe("/admin/day");
  });

  it("владелец и администратор — на «Команду», как и было", () => {
    expect(adminLandingPath(OWNER)).toBe("/admin/team");
    expect(adminLandingPath(ADMIN)).toBe("/admin/team");
  });
});
