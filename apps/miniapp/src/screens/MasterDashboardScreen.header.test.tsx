/**
 * DRF-1848 — шапка дашборда мастера (карта кабинета D01, D02).
 *
 * Заперто: имя мастера — видимым текстом, а не только подписью аватара;
 * значок диалогов в шапке есть всегда, число на нём — только когда есть
 * непрочитанные; число пишется тем же правилом, что на вкладке «Диалоги»
 * (один источник — `tab_badges.conversations_unread`, одно правило —
 * `unreadBadgeText`); тап ведёт туда же, куда «Все диалоги».
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { unreadBadgeText } from "../lib/unread-badge";
import { DashboardHeader } from "./MasterDashboardScreen";

type Props = Parameters<typeof DashboardHeader>[0];

const BASE: Props = {
  salonName: "Формула тела",
  masterName: "Анна Петрова",
  photoUrl: "",
  nowIso: "2026-05-21T14:42:00+03:00",
  unreadCount: 0,
  onInbox: () => {},
};

function renderHeader(overrides: Partial<Props> = {}) {
  return render(
    <MemoryRouter initialEntries={["/master/dashboard"]}>
      <DashboardHeader {...BASE} {...overrides} />
    </MemoryRouter>,
  );
}

describe("DashboardHeader", () => {
  it("имя мастера — видимым текстом", () => {
    renderHeader();
    expect(screen.getByText("Анна")).toBeInTheDocument();
  });

  it("без непрочитанных значок есть, числа нет", () => {
    renderHeader({ unreadCount: 0 });
    const inbox = screen.getByRole("button", { name: "Диалоги" });
    expect(inbox.textContent).toBe("");
  });

  it("с непрочитанными — число и подпись для читающего экран", () => {
    renderHeader({ unreadCount: 2 });
    const inbox = screen.getByRole("button", { name: "Диалоги, непрочитанных: 2" });
    expect(inbox).toHaveTextContent("2");
  });

  it("тап по значку — onInbox", async () => {
    const user = userEvent.setup();
    const onInbox = vi.fn();
    renderHeader({ unreadCount: 1, onInbox });
    await user.click(screen.getByRole("button", { name: /^Диалоги/ }));
    expect(onInbox).toHaveBeenCalledTimes(1);
  });

  // DRF-2121: вкладки «Диалоги» в панели больше нет — число живёт только в
  // шапке и пишется тем же правилом `unreadBadgeText`, что и раньше.
  it.each([1, 7, 99, 100, 120])("число в шапке — по правилу unreadBadgeText: %i", (count) => {
    const { container } = renderHeader({ unreadCount: count });
    const headerText = within(container).getByRole("button", { name: /^Диалоги/ }).textContent;
    expect(headerText).toBe(unreadBadgeText(count));
  });

  it("аватар — кнопка листа профиля, с точкой, когда владелец ждёт правок (DRF-2121)", () => {
    renderHeader({ profileHasOwnerPendingChange: true });
    const trigger = screen.getByRole("button", { name: "Меню профиля" });
    expect(trigger).toHaveTextContent("АП");
    expect(within(trigger).getByLabelText("есть изменения")).toBeInTheDocument();
  });
});
