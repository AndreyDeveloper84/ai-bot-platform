/**
 * Раздел «Ayla» — экран (DRF-1180).
 *
 * Главное, что здесь судится, — не разметка, а правило владельца:
 * действие, меняющее данные, показывается, подтверждается и только
 * потом выполняется. Поэтому обе половины стоят рядом:
 *
 *   - предложение пришло, карточка видна, `confirmAylaAction` НЕ звался;
 *   - нажали «Отправить заявку» — позвался, ровно с тем талоном, что
 *     пришёл с сервера.
 *
 * Отрицание без положительной половины зеленело бы и на экране, который
 * вообще ничего не отрисовал (DRF-1411).
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/master-api", async (importOriginal) => {
  const original =
    await importOriginal<typeof import("../lib/master-api")>();
  return {
    ...original,
    getAylaHistory: vi.fn(),
    askAyla: vi.fn(),
    confirmAylaAction: vi.fn(),
  };
});

import {
  askAyla,
  confirmAylaAction,
  getAylaHistory,
  type AylaAskResponse,
  type AylaPendingAction,
} from "../lib/master-api";
import { MasterAylaScreen } from "./MasterAylaScreen";

const mockedHistory = vi.mocked(getAylaHistory);
const mockedAsk = vi.mocked(askAyla);
const mockedConfirm = vi.mocked(confirmAylaAction);

const PROPOSAL: AylaPendingAction = {
  action: "block_time",
  summary:
    "Собираюсь отправить администратору заявку на нерабочее время: " +
    "12 сентября, 09:00–18:00. Причина: отпуск. " +
    "Пока вы не подтвердите, ничего не меняется.",
  confirm_label: "Отправить заявку",
  token: "signed-token-1",
  expires_in_sec: 900,
};

function plainAnswer(text: string): AylaAskResponse {
  return { answer: text, tool: "", pending_action: null, message_id: "m1" };
}

function renderScreen() {
  return render(
    <MemoryRouter initialEntries={["/master/ayla"]}>
      <MasterAylaScreen />
    </MemoryRouter>,
  );
}

async function ask(text: string) {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Вопрос к Ayla"), text);
  await user.click(screen.getByLabelText("Отправить"));
  return user;
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedHistory.mockResolvedValue({ messages: [] });
});

describe("MasterAylaScreen · диалог", () => {
  it("открывается с приглашением, когда истории нет", async () => {
    renderScreen();

    expect(
      await screen.findByText(/Спросите про день, загрузку/),
    ).toBeInTheDocument();
  });

  it("показывает историю, общую с ботом", async () => {
    mockedHistory.mockResolvedValue({
      messages: [
        {
          id: "1",
          role: "user",
          content: "что у меня завтра",
          tool: "",
          created_at: "2026-09-06T08:00:00+03:00",
        },
        {
          id: "2",
          role: "assistant",
          content: "Завтра три записи.",
          tool: "my_day",
          created_at: "2026-09-06T08:00:02+03:00",
        },
      ],
    });

    renderScreen();

    expect(await screen.findByText("что у меня завтра")).toBeInTheDocument();
    expect(screen.getByText("Завтра три записи.")).toBeInTheDocument();
  });

  it("вопрос уходит на сервер и ответ показывается", async () => {
    mockedAsk.mockResolvedValue(plainAnswer("В четверг окно с 14:00."));
    renderScreen();
    await screen.findByText(/Спросите про день/);

    await ask("когда у меня окно");

    expect(mockedAsk).toHaveBeenCalledWith("когда у меня окно");
    expect(
      await screen.findByText("В четверг окно с 14:00."),
    ).toBeInTheDocument();
    // Вопрос человека тоже остаётся на экране.
    expect(screen.getByText("когда у меня окно")).toBeInTheDocument();
  });

  it("ошибка сервера не съедает экран", async () => {
    mockedAsk.mockRejectedValue(new Error("boom"));
    renderScreen();
    await screen.findByText(/Спросите про день/);

    await ask("что у меня завтра");

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /Не получилось отправить/,
    );
  });
});

describe("MasterAylaScreen · подтверждение действия (DRF-1180)", () => {
  it("предложение показывается словами, а не выполняется", async () => {
    mockedAsk.mockResolvedValue({
      answer: PROPOSAL.summary,
      tool: "block_time",
      pending_action: PROPOSAL,
      message_id: "m2",
    });
    renderScreen();
    await screen.findByText(/Спросите про день/);

    await ask("хочу выходной в пятницу");

    // Положительная половина: карточка со сводкой на экране.
    expect(await screen.findByText("Подтвердите действие")).toBeInTheDocument();
    expect(
      screen.getByText(/заявку на нерабочее время/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Отправить заявку" }),
    ).toBeInTheDocument();
    // И только теперь отрицание: ничего не выполнено.
    expect(mockedConfirm).not.toHaveBeenCalled();
  });

  it("по подтверждению выполняется — тем самым талоном", async () => {
    mockedAsk.mockResolvedValue({
      answer: PROPOSAL.summary,
      tool: "block_time",
      pending_action: PROPOSAL,
      message_id: "m2",
    });
    mockedConfirm.mockResolvedValue({
      answer: "Готово. Заявка отправлена администратору салона.",
      action: "block_time",
      executed: true,
      message_id: "m3",
    });
    renderScreen();
    await screen.findByText(/Спросите про день/);
    const user = await ask("хочу выходной в пятницу");
    await screen.findByText("Подтвердите действие");

    await user.click(screen.getByRole("button", { name: "Отправить заявку" }));

    expect(mockedConfirm).toHaveBeenCalledWith("signed-token-1");
    expect(
      await screen.findByText(
        "Готово. Заявка отправлена администратору салона.",
      ),
    ).toBeInTheDocument();
    // Карточка уходит: развилка пройдена.
    await waitFor(() =>
      expect(screen.queryByText("Подтвердите действие")).toBeNull(),
    );
  });

  it("«Не надо» ничего не выполняет и убирает карточку", async () => {
    mockedAsk.mockResolvedValue({
      answer: PROPOSAL.summary,
      tool: "block_time",
      pending_action: PROPOSAL,
      message_id: "m2",
    });
    renderScreen();
    await screen.findByText(/Спросите про день/);
    const user = await ask("хочу выходной в пятницу");
    await screen.findByText("Подтвердите действие");

    await user.click(screen.getByRole("button", { name: "Не надо" }));

    expect(mockedConfirm).not.toHaveBeenCalled();
    expect(
      await screen.findByText("Хорошо, ничего не меняю."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Подтвердите действие")).toBeNull();
  });

  it("новый вопрос снимает висящее предложение", async () => {
    mockedAsk.mockResolvedValueOnce({
      answer: PROPOSAL.summary,
      tool: "block_time",
      pending_action: PROPOSAL,
      message_id: "m2",
    });
    mockedAsk.mockResolvedValueOnce(plainAnswer("Завтра три записи."));
    renderScreen();
    await screen.findByText(/Спросите про день/);
    await ask("хочу выходной в пятницу");
    await screen.findByText("Подтвердите действие");

    await ask("а что у меня завтра");

    expect(await screen.findByText("Завтра три записи.")).toBeInTheDocument();
    expect(screen.queryByText("Подтвердите действие")).toBeNull();
    expect(mockedConfirm).not.toHaveBeenCalled();
  });
});
