/**
 * Раздел «Ayla» администратора — экран (DRF-2119, половина А).
 *
 * Судится правило владельца: модель готовит, человек подтверждает.
 * Две половины рядом (DRF-1411): предложение пришло и карточка видна —
 * ничего не выполнено; нажали — выполнено ровно тем, что пришло с сервера.
 *
 *   - `confirm_kind: "token"` (изменение графика) → `POST confirm` с тем
 *     же талоном, и только после нажатия;
 *   - `confirm_kind: "open"` (черновик записи) → сервер НЕ зовётся,
 *     экран уходит на `/admin/booking/new?…` с предзаполнением.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../lib/admin-api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../lib/admin-api")>();
  return {
    ...original,
    getAdminAylaHistory: vi.fn(),
    askAdminAyla: vi.fn(),
    confirmAdminAylaAction: vi.fn(),
  };
});

import {
  askAdminAyla,
  confirmAdminAylaAction,
  getAdminAylaHistory,
  type AdminAylaAskResponse,
  type AdminAylaPendingAction,
  type MeResponse,
} from "../../lib/admin-api";
import { SalonPilotAylaScreen } from "./SalonPilotAylaScreen";

const mockedHistory = vi.mocked(getAdminAylaHistory);
const mockedAsk = vi.mocked(askAdminAyla);
const mockedConfirm = vi.mocked(confirmAdminAylaAction);

const OWNER_ME: MeResponse = {
  user: { id: "u-1", name: "Карина", phone_masked: "+7 *** **12" },
  tenant: { id: "t-1", name: "Формула тела", slug: "formula-tela" },
  role: "owner",
  capabilities: [],
  is_customer: true,
  is_master: false,
  is_receptionist: false,
  is_admin: false,
  is_owner: true,
  master_id: null,
  landing_path: "/admin/today",
};

const SCHEDULE_PROPOSAL: AdminAylaPendingAction = {
  action: "prepare_schedule_change",
  summary: "Собираюсь закрыть время у Ольги: 25 сентября, 10:00–12:00. Причина: личные дела.",
  confirm_label: "Изменить график",
  token: "signed-admin-token-1",
  expires_in_sec: 900,
  confirm_kind: "token",
  open_url: "",
};

const BOOKING_DRAFT: AdminAylaPendingAction = {
  action: "prepare_booking",
  summary: "Черновик записи: Анна к Ольге, Маникюр, 22 сентября в 11:00.",
  confirm_label: "Открыть форму записи",
  token: "",
  expires_in_sec: 0,
  confirm_kind: "open",
  open_url: "/admin/booking/new?date=2026-09-22&master_id=m-1&service_id=s-1&client_name=Анна",
};

function plainAnswer(text: string): AdminAylaAskResponse {
  return { answer: text, tool: "find_booking", pending_action: null, message_id: "m1" };
}

function LocationProbe() {
  const location = useLocation();
  return <p data-testid="location">{location.pathname + location.search}</p>;
}

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/admin/ayla"]}>
      <Routes>
        <Route path="/admin/ayla" element={<SalonPilotAylaScreen me={OWNER_ME} />} />
        <Route path="/admin/booking/new" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedHistory.mockResolvedValue({ messages: [] });
});

describe("SalonPilotAylaScreen (DRF-2119)", () => {
  it("спрашивает админскую тройку и показывает ответ", async () => {
    mockedAsk.mockResolvedValue(plainAnswer("Сегодня у Ольги две записи: Анна в 10:00, Мария в 14:00."));
    renderScreen();
    await screen.findByText(/Спросите про записи салона/);

    await userEvent.type(screen.getByLabelText("Вопрос к Ayla"), "кто у Ольги сегодня?");
    await userEvent.click(screen.getByLabelText("Отправить"));

    await screen.findByText(/две записи/);
    expect(mockedAsk).toHaveBeenCalledWith("кто у Ольги сегодня?");
    expect(mockedConfirm).not.toHaveBeenCalled();
  });

  it("изменение графика: карточка видна, confirm — только после нажатия и с тем же талоном", async () => {
    mockedAsk.mockResolvedValue({
      answer: SCHEDULE_PROPOSAL.summary,
      tool: "prepare_schedule_change",
      pending_action: SCHEDULE_PROPOSAL,
      message_id: "m2",
    });
    mockedConfirm.mockResolvedValue({
      answer: "Готово. График Ольги изменён.",
      action: "prepare_schedule_change",
      executed: true,
      message_id: "m3",
    });
    renderScreen();
    await screen.findByText(/Спросите про записи салона/);

    await userEvent.type(screen.getByLabelText("Вопрос к Ayla"), "закрой Ольге пятницу с 10 до 12");
    await userEvent.click(screen.getByLabelText("Отправить"));

    await screen.findByText(/Собираюсь закрыть время у Ольги/);
    expect(mockedConfirm).not.toHaveBeenCalled(); // положительная половина — карточка выше

    await userEvent.click(screen.getByRole("button", { name: "Изменить график" }));
    await waitFor(() => expect(mockedConfirm).toHaveBeenCalledWith("signed-admin-token-1"));
    await screen.findByText(/График Ольги изменён/);
  }, 15_000);

  it("черновик записи: сервер не зовётся, экран уходит в форму с предзаполнением", async () => {
    mockedAsk.mockResolvedValue({
      answer: BOOKING_DRAFT.summary,
      tool: "prepare_booking",
      pending_action: BOOKING_DRAFT,
      message_id: "m4",
    });
    renderScreen();
    await screen.findByText(/Спросите про записи салона/);

    await userEvent.type(screen.getByLabelText("Вопрос к Ayla"), "запиши Анну к Ольге на маникюр");
    await userEvent.click(screen.getByLabelText("Отправить"));

    await screen.findByText(/Черновик записи: Анна к Ольге/);
    await userEvent.click(screen.getByRole("button", { name: "Открыть форму записи" }));

    const probe = await screen.findByTestId("location");
    expect(probe.textContent).toContain("/admin/booking/new?");
    expect(probe.textContent).toContain("master_id=m-1");
    expect(probe.textContent).toContain("client_name=");
    expect(mockedConfirm).not.toHaveBeenCalled(); // дверь — не подтверждение на сервере
  });

  it("«Не надо» снимает карточку без вызова сервера", async () => {
    mockedAsk.mockResolvedValue({
      answer: SCHEDULE_PROPOSAL.summary,
      tool: "prepare_schedule_change",
      pending_action: SCHEDULE_PROPOSAL,
      message_id: "m5",
    });
    renderScreen();
    await screen.findByText(/Спросите про записи салона/);
    await userEvent.type(screen.getByLabelText("Вопрос к Ayla"), "закрой Ольге пятницу");
    await userEvent.click(screen.getByLabelText("Отправить"));
    await screen.findByText(/Собираюсь закрыть время/);

    await userEvent.click(screen.getByRole("button", { name: "Не надо" }));
    await screen.findByText("Хорошо, ничего не меняю.");
    expect(mockedConfirm).not.toHaveBeenCalled();
  });
});
