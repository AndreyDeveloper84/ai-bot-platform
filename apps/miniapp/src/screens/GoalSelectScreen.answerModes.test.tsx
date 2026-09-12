/**
 * Типы ответа по смыслу (DRF-1746, макет C03 P11).
 *
 * Экран рисует компонент по `mode` из документа и ничего не выводит.
 * Сторожа — по одному на режим, плюс два «не так»: multi не шлёт по
 * тапу, незнакомый mode рисуется как single (к простому, не к пустому).
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/customer-goals", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-goals")>();
  return {
    ...original,
    fetchDecisionContext: vi.fn(),
    postGoalSelect: vi.fn(),
  };
});

import { MULTI_CONTINUE_LABEL, TEXT_SUBMIT_LABEL } from "../components/AnketaStepInput";
import {
  fetchDecisionContext,
  postGoalSelect,
  type DecisionContext,
  type MissingItem,
} from "../lib/customer-goals";
import { GoalSelectScreen } from "./GoalSelectScreen";

const mockedFetch = vi.mocked(fetchDecisionContext);
const mockedPost = vi.mocked(postGoalSelect);

const PROMPT = "Что беспокоит сейчас?";

function docWith(item: Partial<MissingItem>): DecisionContext {
  return {
    version: 2,
    known: {
      goal: {
        goal_key: "relax",
        goal_text: null,
        selected_at: "2026-09-12T10:00:00Z",
        source_channel: "miniapp",
      },
    },
    missing: [
      {
        kind: "goal_anketa",
        prompt: PROMPT,
        step: "signals",
        options: [
          { key: "dry", label: "Сухость" },
          { key: "tired", label: "Усталость" },
          { key: "none", label: "Ничего из этого" },
        ],
        allow_free_text: false,
        ...item,
      },
    ],
    suggestions: [],
    intents: [],
    next: null,
  };
}

function renderScreen() {
  render(
    <MemoryRouter initialEntries={["/customer/goal-select"]}>
      <Routes>
        <Route path="/customer/goal-select" element={<GoalSelectScreen />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockedPost.mockImplementation(async () => docWith({ mode: "single" }));
});

describe("single (по умолчанию)", () => {
  it("без mode — фишки, тап шлёт option_key", async () => {
    mockedFetch.mockResolvedValue(docWith({}));
    renderScreen();
    fireEvent.click(await screen.findByRole("button", { name: "Сухость" }));
    await waitFor(() => expect(mockedPost).toHaveBeenCalledTimes(1));
    expect(mockedPost).toHaveBeenCalledWith({
      answer: { step: "signals", option_key: "dry" },
      source_channel: "miniapp",
    });
  });

  it("незнакомый mode рисуется как single — не пустой экран", async () => {
    mockedFetch.mockResolvedValue(docWith({ mode: "hologram" }));
    renderScreen();
    fireEvent.click(await screen.findByRole("button", { name: "Усталость" }));
    await waitFor(() => expect(mockedPost).toHaveBeenCalledTimes(1));
    expect(mockedPost.mock.calls[0]?.[0]).toEqual({
      answer: { step: "signals", option_key: "tired" },
      source_channel: "miniapp",
    });
  });
});

describe("multi", () => {
  it("тапы копят выбор, «Продолжить» шлёт массив одним запросом в порядке вариантов", async () => {
    mockedFetch.mockResolvedValue(docWith({ mode: "multi" }));
    renderScreen();
    const cont = await screen.findByRole("button", { name: MULTI_CONTINUE_LABEL });
    expect(cont).toBeDisabled();
    // Порядок тапов обратный порядку вариантов — в ответе порядок сервера.
    fireEvent.click(screen.getByRole("checkbox", { name: "Усталость" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Сухость" }));
    expect(mockedPost).not.toHaveBeenCalled();
    expect(screen.getByRole("checkbox", { name: "Сухость" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    fireEvent.click(cont);
    await waitFor(() => expect(mockedPost).toHaveBeenCalledTimes(1));
    expect(mockedPost).toHaveBeenCalledWith({
      answer: { step: "signals", option_keys: ["dry", "tired"] },
      source_channel: "miniapp",
    });
  });

  it("«Ничего из этого» — обычная опция, повторный тап снимает отметку", async () => {
    mockedFetch.mockResolvedValue(docWith({ mode: "multi" }));
    renderScreen();
    const none = await screen.findByRole("checkbox", { name: "Ничего из этого" });
    fireEvent.click(none);
    expect(none).toHaveAttribute("aria-checked", "true");
    fireEvent.click(none);
    expect(none).toHaveAttribute("aria-checked", "false");
    expect(screen.getByRole("button", { name: MULTI_CONTINUE_LABEL })).toBeDisabled();
  });
});

describe("scale", () => {
  it("деления в порядке сервера, подписи концов, тап шлёт option_key", async () => {
    mockedFetch.mockResolvedValue(
      docWith({
        mode: "scale",
        prompt: "Насколько это мешает?",
        options: [
          { key: "1", label: "1" },
          { key: "2", label: "2" },
          { key: "3", label: "3" },
        ],
        scale: { low_label: "Почти нет", high_label: "Очень" },
      }),
    );
    renderScreen();
    const scale = await screen.findByTestId("anketa-scale");
    expect(scale).toHaveTextContent("Почти нет");
    expect(scale).toHaveTextContent("Очень");
    const radios = screen.getAllByRole("radio");
    expect(radios.map((r) => r.textContent)).toEqual(["1", "2", "3"]);
    fireEvent.click(radios[2] as HTMLElement);
    await waitFor(() => expect(mockedPost).toHaveBeenCalledTimes(1));
    expect(mockedPost).toHaveBeenCalledWith({
      answer: { step: "signals", option_key: "3" },
      source_channel: "miniapp",
    });
  });
});

describe("text", () => {
  it("короткое поле с лимитом сервера, «Отправить» шлёт text; нижнего поля цели нет", async () => {
    mockedFetch.mockResolvedValue(
      docWith({
        mode: "text",
        prompt: "Одним словом — что хочется?",
        options: [],
        allow_free_text: true,
        text_limit: 20,
      }),
    );
    renderScreen();
    const field = await screen.findByRole("textbox", { name: "Одним словом — что хочется?" });
    expect(field).toHaveAttribute("maxlength", "20");
    expect(screen.getByTestId("anketa-text")).toHaveTextContent("0 / 20");
    // Ровно одно текстовое поле на экране: нижнее поле цели не дублируется.
    expect(screen.getAllByRole("textbox")).toHaveLength(1);
    const send = screen.getByRole("button", { name: TEXT_SUBMIT_LABEL });
    expect(send).toBeDisabled();
    fireEvent.change(field, { target: { value: "  свежести  " } });
    fireEvent.click(send);
    await waitFor(() => expect(mockedPost).toHaveBeenCalledTimes(1));
    expect(mockedPost).toHaveBeenCalledWith({
      answer: { step: "signals", text: "свежести" },
      source_channel: "miniapp",
    });
  });
});
