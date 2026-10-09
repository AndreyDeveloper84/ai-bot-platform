import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  DestructiveConfirmation,
  OperationalSuccess,
  SYSTEM_STATE_COPY,
  SystemState,
} from "./OperationalSystemState";

describe("OperationalSystemState shared contract", () => {
  it("keeps the canonical pending/readback copy", () => {
    render(<SystemState kind="pending" onRecheck={() => {}} />);
    expect(screen.getByText(SYSTEM_STATE_COPY.pending.title)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: SYSTEM_STATE_COPY.pending.recheck })).toBeEnabled();
  });

  it("renders a neutral success result without owning domain mutation", async () => {
    const onAction = vi.fn();
    render(
      <OperationalSuccess
        title="Сохранено"
        body="Данные обновлены."
        actionLabel="Продолжить"
        onAction={onAction}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("Сохранено");
    await userEvent.click(screen.getByRole("button", { name: "Продолжить" }));
    expect(onAction).toHaveBeenCalledTimes(1);
  });

  it("destructive confirmation requires an explicit confirm or cancel callback", async () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(
      <DestructiveConfirmation
        title="Отменить запись?"
        body="Это действие изменит запись."
        confirmLabel="Отменить запись"
        onConfirm={onConfirm}
        onCancel={onCancel}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("Это действие изменит запись.");
    await userEvent.click(screen.getByRole("button", { name: "Отменить запись" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "Отмена" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
