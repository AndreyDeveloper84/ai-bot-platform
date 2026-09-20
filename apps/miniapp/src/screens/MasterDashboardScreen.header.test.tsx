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
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { DashboardHeader } from "./MasterDashboardScreen";

type Props = Parameters<typeof DashboardHeader>[0];

const BASE: Props = {
  salonName: "Формула тела",
  masterName: "Анна Петрова",
  photoUrl: "",
  nowIso: "2026-05-21T14:42:00+03:00",
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

  // DRF-2152 (§50 п.5, макет DRF-1182): кнопки «Диалоги» (💬 с числом) в шапке
  // больше нет — прямой переписки мастера с клиентом на экране нет вовсе.
  it("кнопки «Диалоги» в шапке нет", () => {
    renderHeader();
    // Присутствие первым: шапка отрисована — аватар на месте…
    expect(screen.getByRole("button", { name: "Меню профиля" })).toBeInTheDocument();
    // …а переписок в ней нет.
    expect(screen.queryByRole("button", { name: /Диалоги/ })).toBeNull();
    expect(screen.queryByText(/непрочитанных/)).toBeNull();
  });

  it("аватар — кнопка листа профиля, с точкой, когда владелец ждёт правок (DRF-2121)", () => {
    renderHeader({ profileHasOwnerPendingChange: true });
    const trigger = screen.getByRole("button", { name: "Меню профиля" });
    expect(trigger).toHaveTextContent("АП");
    expect(within(trigger).getByLabelText("есть изменения")).toBeInTheDocument();
  });
});
