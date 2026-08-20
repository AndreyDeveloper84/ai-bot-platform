/**
 * Tests for `GoalSelectScreen` (DRF-1190) — the screen is a dumb
 * renderer over the server decision-context document: it renders only
 * what the document carries and POSTs every user action back,
 * replacing state with the returned document. Lib functions mocked
 * with the verbatim contract shapes.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/customer-goals", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/customer-goals")>();
  return {
    ...original,
    fetchDecisionContext: vi.fn(),
    postGoalSelect: vi.fn(),
  };
});

import {
  fetchDecisionContext,
  postGoalSelect,
  type DecisionContext,
} from "../lib/customer-goals";
import { GoalSelectScreen } from "./GoalSelectScreen";

const mockedFetch = vi.mocked(fetchDecisionContext);
const mockedPost = vi.mocked(postGoalSelect);

const BASE_DOC: DecisionContext = {
  version: 1,
  known: { goal: null },
  missing: [{ kind: "goal", prompt: "Что хочешь получить от визита?" }],
  suggestions: [
    { key: "relax", label: "Расслабиться и восстановиться" },
    { key: "beauty", label: "Ухоженный вид" },
  ],
  intents: [
    { id: "choose_suggested", label: "Выбери из вариантов" },
    { id: "formulate_own", label: "Опиши своими словами" },
    { id: "need_guidance", label: "Не понимаю, чего хочу" },
  ],
};

function renderScreen() {
  render(
    <MemoryRouter>
      <GoalSelectScreen />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("GoalSelectScreen (dumb renderer over the decision document)", () => {
  it("loading → ok: renders chips and prompt texts from the document", async () => {
    mockedFetch.mockResolvedValue(BASE_DOC);
    renderScreen();

    const group = await screen.findByRole("radiogroup");
    const chips = within(group).getAllByRole("radio");
    expect(chips).toHaveLength(2);
    expect(chips[0]).toHaveTextContent("Расслабиться и восстановиться");
    expect(chips[1]).toHaveTextContent("Ухоженный вид");

    expect(
      screen.getByText("Что хочешь получить от визита?"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Не понимаю, чего хочу" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: "Опиши своими словами" }),
    ).toBeInTheDocument();
  });

  it("chip click → postGoalSelect({goal_key}) → re-renders the returned document", async () => {
    const user = userEvent.setup();
    mockedFetch.mockResolvedValue(BASE_DOC);
    const updated: DecisionContext = {
      ...BASE_DOC,
      known: {
        goal: {
          goal_key: "relax",
          goal_text: null,
          selected_at: "2026-08-19T07:00:00Z",
          source_channel: "miniapp",
        },
      },
      missing: [],
    };
    mockedPost.mockResolvedValue(updated);
    renderScreen();

    const group = await screen.findByRole("radiogroup");
    await user.click(within(group).getByRole("radio", { name: "Расслабиться и восстановиться" }));

    expect(mockedPost).toHaveBeenCalledWith({
      goal_key: "relax",
      source_channel: "miniapp",
    });
    // New document replaces state: current-goal block shows the label,
    // the old missing prompt is gone.
    const current = await screen.findByRole("region", { name: "Текущая цель" });
    expect(
      within(current).getByText("Расслабиться и восстановиться"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Что хочешь получить от визита?"),
    ).not.toBeInTheDocument();
  });

  it("free-form submit → postGoalSelect({goal_text})", async () => {
    const user = userEvent.setup();
    mockedFetch.mockResolvedValue(BASE_DOC);
    mockedPost.mockResolvedValue({
      ...BASE_DOC,
      known: {
        goal: {
          goal_key: null,
          goal_text: "Хочу избавиться от напряжения в плечах",
          selected_at: "2026-08-19T07:00:00Z",
          source_channel: "miniapp",
        },
      },
      missing: [],
    });
    renderScreen();

    const textarea = await screen.findByRole("textbox", {
      name: "Опиши своими словами",
    });
    await user.type(textarea, "Хочу избавиться от напряжения в плечах");
    await user.click(screen.getByRole("button", { name: "Отправить" }));

    expect(mockedPost).toHaveBeenCalledWith({
      goal_text: "Хочу избавиться от напряжения в плечах",
      source_channel: "miniapp",
    });
    expect(
      await screen.findByText("Хочу избавиться от напряжения в плечах"),
    ).toBeInTheDocument();
  });

  it("need_guidance button → postGoalSelect({intent}) → renders the guidance prompt from missing", async () => {
    const user = userEvent.setup();
    mockedFetch.mockResolvedValue(BASE_DOC);
    mockedPost.mockResolvedValue({
      ...BASE_DOC,
      missing: [
        {
          kind: "goal_guidance",
          prompt: "Давай разберёмся вместе. Что сейчас беспокоит больше всего?",
        },
      ],
    });
    renderScreen();

    await user.click(
      await screen.findByRole("button", { name: "Не понимаю, чего хочу" }),
    );

    expect(mockedPost).toHaveBeenCalledWith({
      intent: "need_guidance",
      source_channel: "miniapp",
    });
    expect(
      await screen.findByText(
        "Давай разберёмся вместе. Что сейчас беспокоит больше всего?",
      ),
    ).toBeInTheDocument();
  });

  it("framework control: a different document changes the render with no code change", async () => {
    const otherDoc: DecisionContext = {
      version: 1,
      known: { goal: null },
      missing: [
        { kind: "goal_clarification", prompt: "Какая зона требует внимания?" },
      ],
      suggestions: [
        { key: "posture", label: "Здоровая осанка" },
        { key: "sleep", label: "Наладить сон" },
        { key: "energy", label: "Больше энергии" },
      ],
      intents: [
        { id: "choose_suggested", label: "Выбери из вариантов" },
        { id: "need_guidance", label: "Помоги определиться" },
      ],
    };
    mockedFetch.mockResolvedValue(otherDoc);
    renderScreen();

    const group = await screen.findByRole("radiogroup");
    const chips = within(group).getAllByRole("radio");
    expect(chips).toHaveLength(3);
    expect(chips[0]).toHaveTextContent("Здоровая осанка");
    expect(
      screen.getByText("Какая зона требует внимания?"),
    ).toBeInTheDocument();
    // No formulate_own intent in the document → no textarea rendered.
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Помоги определиться" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Расслабиться и восстановиться"),
    ).not.toBeInTheDocument();
  });

  it("error state → StateError + retry reloads the document", async () => {
    const user = userEvent.setup();
    mockedFetch.mockRejectedValueOnce(new Error("[500] http_error"));
    renderScreen();

    expect(await screen.findByRole("alert")).toBeInTheDocument();

    mockedFetch.mockResolvedValue(BASE_DOC);
    await user.click(screen.getByRole("button", { name: "Попробовать снова" }));
    expect(await screen.findByRole("radiogroup")).toBeInTheDocument();
  });

  it("failed POST keeps the old document and shows an inline error", async () => {
    const user = userEvent.setup();
    mockedFetch.mockResolvedValue(BASE_DOC);
    mockedPost.mockRejectedValue(new Error("[500] http_error"));
    renderScreen();

    const group = await screen.findByRole("radiogroup");
    await user.click(
      within(group).getByRole("radio", { name: "Расслабиться и восстановиться" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Не получилось отправить",
    );
    // Old document still rendered.
    expect(
      screen.getByText("Что хочешь получить от визита?"),
    ).toBeInTheDocument();
  });
});
