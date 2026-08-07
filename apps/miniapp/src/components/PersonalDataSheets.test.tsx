/**
 * Tests for the C5 (152-ФЗ) personal-data sheets — pilot phase 2a.
 *
 * Sheets replace the deferred support-route (SupportEntrySheet) for the
 * «Запросить данные» / «Удалить аккаунт» profile CTAs and call the real
 * W3 endpoints via `lib/personal-data.ts` (mocked here). Contract:
 * PILOT_CONTRACTS_2026-08-15 §6 — UI idempotency (repeat taps never
 * spawn repeat requests), honest partial-delete status, support
 * deeplink as the error-state fallback (#949).
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/personal-data", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/personal-data")>();
  return {
    ...original,
    exportPersonalData: vi.fn(),
    deletePersonalData: vi.fn(),
    triggerDownload: vi.fn(),
  };
});

import {
  DELETE_CONFIRMATION_TOKEN,
  deletePersonalData,
  exportPersonalData,
  PersonalDataPartialDeleteError,
  triggerDownload,
} from "../lib/personal-data";
import {
  PersonalDataDeleteSheet,
  PersonalDataExportSheet,
} from "./PersonalDataSheets";

const mockedExport = vi.mocked(exportPersonalData);
const mockedDelete = vi.mocked(deletePersonalData);
const mockedDownload = vi.mocked(triggerDownload);

function renderExport(onClose = vi.fn()) {
  const triggerRef = createRef<HTMLButtonElement>();
  render(
    <>
      <button ref={triggerRef} type="button">
        Запросить данные
      </button>
      <PersonalDataExportSheet open triggerRef={triggerRef} onClose={onClose} />
    </>,
  );
  return onClose;
}

function renderDelete(onClose = vi.fn()) {
  const triggerRef = createRef<HTMLButtonElement>();
  render(
    <>
      <button ref={triggerRef} type="button">
        Удалить аккаунт
      </button>
      <PersonalDataDeleteSheet open triggerRef={triggerRef} onClose={onClose} />
    </>,
  );
  return onClose;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("PersonalDataExportSheet", () => {
  it("explains the export and focuses Cancel first (never the primary CTA)", () => {
    renderExport();
    expect(screen.getByRole("dialog")).toHaveAttribute("aria-modal", "true");
    expect(screen.getByText(/один файл/i)).toBeInTheDocument();
    const cancel = screen.getByRole("button", { name: "Отмена" });
    expect(document.activeElement).toBe(cancel);
  });

  it("downloads the file on confirm and offers a calm done state", async () => {
    const user = userEvent.setup();
    const onClose = renderExport();
    mockedExport.mockResolvedValue({
      blob: new Blob(["{}"], { type: "application/json" }),
      filename: "personal-data-export.json",
    });
    await user.click(screen.getByRole("button", { name: "Скачать данные" }));
    expect(await screen.findByText(/Файл скачан/)).toBeInTheDocument();
    expect(mockedDownload).toHaveBeenCalledTimes(1);
    const [blob, filename] = mockedDownload.mock.calls[0]!;
    expect(blob).toBeInstanceOf(Blob);
    expect(filename).toBe("personal-data-export.json");
    await user.click(screen.getByRole("button", { name: "Закрыть" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("never spawns a second request while one is in flight (UI idempotency)", async () => {
    const user = userEvent.setup();
    renderExport();
    let resolveExport: ((v: { blob: Blob; filename: string }) => void) | undefined;
    mockedExport.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveExport = resolve;
        }),
    );
    const primary = screen.getByRole("button", { name: "Скачать данные" });
    await user.click(primary);
    await user.click(primary);
    await user.keyboard("{Enter}");
    expect(mockedExport).toHaveBeenCalledTimes(1);
    resolveExport!({ blob: new Blob(), filename: "personal-data-export.json" });
    expect(await screen.findByText(/Файл скачан/)).toBeInTheDocument();
  });

  it("shows an honest error with retry and the support deeplink on failure", async () => {
    const user = userEvent.setup();
    renderExport();
    mockedExport.mockRejectedValueOnce(new Error("[502] upstream_unavailable"));
    await user.click(screen.getByRole("button", { name: "Скачать данные" }));
    expect(await screen.findByText(/Не получилось подготовить файл/)).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Написать в поддержку" }),
    ).toHaveAttribute("href", "https://max.me/aylasupport");
    mockedExport.mockResolvedValueOnce({
      blob: new Blob(),
      filename: "personal-data-export.json",
    });
    await user.click(screen.getByRole("button", { name: "Попробовать ещё раз" }));
    expect(await screen.findByText(/Файл скачан/)).toBeInTheDocument();
    expect(mockedExport).toHaveBeenCalledTimes(2);
  });

  it("closes on Escape in idle state", async () => {
    const user = userEvent.setup();
    const onClose = renderExport();
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

/** Type the destructive token, then tap the primary button. */
async function confirmDelete(user: ReturnType<typeof userEvent.setup>) {
  await user.type(
    screen.getByLabelText(/Чтобы подтвердить/),
    DELETE_CONFIRMATION_TOKEN,
  );
  await user.click(screen.getByRole("button", { name: "Удалить данные" }));
}

describe("PersonalDataDeleteSheet", () => {
  it("asks for confirmation and states the retention boundary honestly", () => {
    renderDelete();
    expect(screen.getByText("Удалить мои данные?")).toBeInTheDocument();
    // Retention per contract §6: transactional records may be kept by law.
    expect(screen.getByText(/записи и оплаты/i)).toBeInTheDocument();
    // No grace-period / timeframe promises (founder-locked anti-pattern).
    expect(screen.queryByText(/30 дней/)).not.toBeInTheDocument();
    const cancel = screen.getByRole("button", { name: "Отмена" });
    expect(document.activeElement).toBe(cancel);
    expect(document.activeElement).not.toBe(
      screen.getByRole("button", { name: "Удалить данные" }),
    );
  });

  it("keeps the destructive button inert until the token is typed", async () => {
    const user = userEvent.setup();
    renderDelete();
    const primary = screen.getByRole("button", { name: "Удалить данные" });
    expect(primary).toBeDisabled();

    // A near-miss must not arm it — the server would reject it anyway.
    await user.type(screen.getByLabelText(/Чтобы подтвердить/), "удалить");
    expect(primary).toBeDisabled();
    await user.click(primary);
    expect(mockedDelete).not.toHaveBeenCalled();

    await user.clear(screen.getByLabelText(/Чтобы подтвердить/));
    await user.type(
      screen.getByLabelText(/Чтобы подтвердить/),
      DELETE_CONFIRMATION_TOKEN,
    );
    expect(primary).toBeEnabled();
  });

  it("passes the typed token to the backend, which verifies it", async () => {
    const user = userEvent.setup();
    renderDelete();
    mockedDelete.mockResolvedValueOnce({ status: "deleted" });
    await confirmDelete(user);
    expect(mockedDelete).toHaveBeenCalledWith(DELETE_CONFIRMATION_TOKEN);
  });

  it("runs the delete cascade once even on repeated taps, then shows status", async () => {
    const user = userEvent.setup();
    renderDelete();
    let resolveDelete: ((v: { status: "deleted" }) => void) | undefined;
    mockedDelete.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveDelete = resolve;
        }),
    );
    await confirmDelete(user);
    await user.click(screen.getByRole("button", { name: "Удалить данные" }));
    expect(mockedDelete).toHaveBeenCalledTimes(1);
    resolveDelete!({ status: "deleted" });
    expect(await screen.findByText(/Данные удалены/)).toBeInTheDocument();
  });

  it("reports a partial delete with humanised steps, retry and support link", async () => {
    const user = userEvent.setup();
    renderDelete();
    mockedDelete.mockRejectedValueOnce(
      new PersonalDataPartialDeleteError(["memory_delete", "consent_withdraw"]),
    );
    await confirmDelete(user);
    expect(await screen.findByText(/Не всё удалено/)).toBeInTheDocument();
    expect(screen.getByText(/очистить память/)).toBeInTheDocument();
    expect(screen.getByText(/отозвать согласия/)).toBeInTheDocument();
    // Raw backend slugs never reach the UI.
    expect(screen.queryByText(/memory_delete/)).not.toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Написать в поддержку" }),
    ).toHaveAttribute("href", "https://max.me/aylasupport");
    mockedDelete.mockResolvedValueOnce({ status: "deleted" });
    await user.click(screen.getByRole("button", { name: "Попробовать ещё раз" }));
    expect(await screen.findByText(/Данные удалены/)).toBeInTheDocument();
  });

  it("shows a generic honest error on unexpected failures", async () => {
    const user = userEvent.setup();
    renderDelete();
    mockedDelete.mockRejectedValueOnce(new Error("[503] http_error"));
    await confirmDelete(user);
    expect(await screen.findByText(/Не получилось удалить данные/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Попробовать ещё раз" })).toBeInTheDocument();
  });

  it("ignores Escape while a delete request is in flight", async () => {
    const user = userEvent.setup();
    const onClose = renderDelete();
    mockedDelete.mockImplementation(() => new Promise(() => undefined));
    await confirmDelete(user);
    await user.keyboard("{Escape}");
    expect(onClose).not.toHaveBeenCalled();
  });

  it("clears the typed token when the sheet is reopened", async () => {
    const user = userEvent.setup();
    const triggerRef = createRef<HTMLButtonElement>();
    const { rerender } = render(
      <>
        <button ref={triggerRef} type="button">
          Удалить аккаунт
        </button>
        <PersonalDataDeleteSheet open triggerRef={triggerRef} onClose={vi.fn()} />
      </>,
    );
    await user.type(
      screen.getByLabelText(/Чтобы подтвердить/),
      DELETE_CONFIRMATION_TOKEN,
    );
    rerender(
      <>
        <button ref={triggerRef} type="button">
          Удалить аккаунт
        </button>
        <PersonalDataDeleteSheet
          open={false}
          triggerRef={triggerRef}
          onClose={vi.fn()}
        />
      </>,
    );
    rerender(
      <>
        <button ref={triggerRef} type="button">
          Удалить аккаунт
        </button>
        <PersonalDataDeleteSheet open triggerRef={triggerRef} onClose={vi.fn()} />
      </>,
    );
    expect(screen.getByLabelText(/Чтобы подтвердить/)).toHaveValue("");
    expect(screen.getByRole("button", { name: "Удалить данные" })).toBeDisabled();
  });
});
