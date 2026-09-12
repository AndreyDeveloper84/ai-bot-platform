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
    requestAccountDeletion: vi.fn(),
    getCurrentDeletionRequest: vi.fn(),
    triggerDownload: vi.fn(),
  };
});

import {
  DELETE_CONFIRMATION_TOKEN,
  DeletionNotStartedError,
  exportPersonalData,
  getCurrentDeletionRequest,
  requestAccountDeletion,
  triggerDownload,
  type DeletionRequestInfo,
} from "../lib/personal-data";
import {
  DELETION_CONFIRMATION_POINTS,
  DeletionRequestStatus,
  PersonalDataDeleteSheet,
  PersonalDataExportSheet,
} from "./PersonalDataSheets";

const mockedExport = vi.mocked(exportPersonalData);
const mockedRequest = vi.mocked(requestAccountDeletion);
const mockedCurrent = vi.mocked(getCurrentDeletionRequest);
const mockedDownload = vi.mocked(triggerDownload);

const REQUEST: DeletionRequestInfo = {
  request_id: "7a1b2c3d-0000-4000-8000-000000001699",
  status: "DELETION_REQUESTED",
  requested_at: "2026-09-11T14:00:00+00:00",
  deadline_at: "2026-10-11T14:00:00+00:00",
  completed_at: null,
  is_open: true,
};

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
        Удалить аккаунт и личные данные
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
  await user.click(screen.getByRole("button", { name: "Удалить аккаунт" }));
}

describe("PersonalDataDeleteSheet", () => {
  it("lists the five §7 points — the 30-day deadline included — and focuses Cancel first", () => {
    renderDelete();
    expect(screen.getByText("Удалить аккаунт и личные данные?")).toBeInTheDocument();
    // Состав подтверждения — §7 свода дословно: пять пунктов, каждый на экране.
    expect(DELETION_CONFIRMATION_POINTS).toHaveLength(5);
    for (const point of DELETION_CONFIRMATION_POINTS) {
      expect(screen.getByText(point)).toBeInTheDocument();
    }
    // Срок — на экране. Прежнее правило «no 30-day wording» снято решением
    // владельца от 11.09.2026 (правило поменялось — код не должен быть
    // верен прежнему).
    expect(screen.getByText(/не позднее 30 дней/)).toBeInTheDocument();
    expect(screen.getByText(/записи и оплаты/i)).toBeInTheDocument();
    expect(screen.getByText(/нельзя отменить/)).toBeInTheDocument();
    const cancel = screen.getByRole("button", { name: "Отмена" });
    expect(document.activeElement).toBe(cancel);
    expect(document.activeElement).not.toBe(
      screen.getByRole("button", { name: "Удалить аккаунт" }),
    );
  });

  it("keeps the destructive button inert until the token is typed", async () => {
    const user = userEvent.setup();
    renderDelete();
    const primary = screen.getByRole("button", { name: "Удалить аккаунт" });
    expect(primary).toBeDisabled();

    await user.type(screen.getByLabelText(/Чтобы подтвердить/), "удалить");
    expect(primary).toBeDisabled();
    await user.click(primary);
    expect(mockedRequest).not.toHaveBeenCalled();

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
    mockedRequest.mockResolvedValueOnce({ request: REQUEST, created: true });
    await confirmDelete(user);
    expect(mockedRequest).toHaveBeenCalledWith(DELETE_CONFIRMATION_TOKEN);
  });

  it("shows «принято» with the exact deadline date, request id and status", async () => {
    const user = userEvent.setup();
    renderDelete();
    mockedRequest.mockResolvedValueOnce({ request: REQUEST, created: true });
    await confirmDelete(user);

    expect(await screen.findByText(/Запрос принят\./)).toBeInTheDocument();
    // Точная крайняя дата словами, не ISO-строка и не «через 30 дней».
    expect(screen.getByText(/11 октября 2026/)).toBeInTheDocument();
    expect(screen.getByText(REQUEST.request_id)).toBeInTheDocument();
    expect(screen.getByText(/принят, ожидает выполнения/)).toBeInTheDocument();
    // Сырой слаг статуса наружу не выходит.
    expect(screen.queryByText(/DELETION_REQUESTED/)).not.toBeInTheDocument();
    expect(screen.getByText(/прекращаются сразу/)).toBeInTheDocument();
  });

  it("says «уже был принят» on a repeat (created=false) with the same number", async () => {
    const user = userEvent.setup();
    renderDelete();
    mockedRequest.mockResolvedValueOnce({ request: REQUEST, created: false });
    await confirmDelete(user);
    expect(await screen.findByText(/уже был принят раньше/)).toBeInTheDocument();
    expect(screen.getByText(REQUEST.request_id)).toBeInTheDocument();
  });

  it("sends the request once even on repeated taps (UI idempotency)", async () => {
    const user = userEvent.setup();
    renderDelete();
    let resolveRequest: ((v: { request: DeletionRequestInfo; created: boolean }) => void) | undefined;
    mockedRequest.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveRequest = resolve;
        }),
    );
    await user.type(
      screen.getByLabelText(/Чтобы подтвердить/),
      DELETE_CONFIRMATION_TOKEN,
    );
    const primary = screen.getByRole("button", { name: "Удалить аккаунт" });
    await user.click(primary);
    await user.click(primary);
    expect(mockedRequest).toHaveBeenCalledTimes(1);
    resolveRequest!({ request: REQUEST, created: true });
    expect(await screen.findByText(/Запрос принят/)).toBeInTheDocument();
  });

  it("says plainly that deletion did NOT start on a retryable refusal, and offers retry", async () => {
    const user = userEvent.setup();
    renderDelete();
    mockedRequest.mockRejectedValueOnce(
      new DeletionNotStartedError("upstream_unavailable", true, "Удаление не началось."),
    );
    await confirmDelete(user);

    expect(await screen.findByText(/Удаление не началось/)).toBeInTheDocument();
    expect(screen.getByText(/ничего не удалено и не изменено/)).toBeInTheDocument();
    // Нет ни «частично», ни «удалено»: состояние данных одно, и оно названо.
    expect(screen.queryByText(/Не всё удалено/)).not.toBeInTheDocument();
    expect(screen.queryByText(/upstream_unavailable/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Написать в поддержку" })).toBeInTheDocument();

    mockedRequest.mockResolvedValueOnce({ request: REQUEST, created: true });
    await user.click(screen.getByRole("button", { name: "Попробовать ещё раз" }));
    expect(await screen.findByText(/Запрос принят/)).toBeInTheDocument();
  });

  it("offers no retry when the server says a retry cannot help (not_linked)", async () => {
    const user = userEvent.setup();
    renderDelete();
    mockedRequest.mockRejectedValueOnce(
      new DeletionNotStartedError("not_linked", false, "Удаление не началось."),
    );
    await confirmDelete(user);

    expect(await screen.findByText(/Удаление не началось/)).toBeInTheDocument();
    expect(screen.getByText(/не поможет/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Попробовать ещё раз" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Написать в поддержку" })).toBeInTheDocument();
    expect(screen.queryByText(/not_linked/)).not.toBeInTheDocument();
  });

  it("shows an honest «не началось» on unexpected failures too", async () => {
    const user = userEvent.setup();
    renderDelete();
    mockedRequest.mockRejectedValueOnce(new Error("[503] http_error"));
    await confirmDelete(user);
    expect(await screen.findByText(/Удаление не началось/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Попробовать ещё раз" })).toBeInTheDocument();
  });

  it("ignores Escape while the request is in flight", async () => {
    const user = userEvent.setup();
    const onClose = renderDelete();
    mockedRequest.mockImplementation(() => new Promise(() => undefined));
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
          Удалить аккаунт и личные данные
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
          Удалить аккаунт и личные данные
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
          Удалить аккаунт и личные данные
        </button>
        <PersonalDataDeleteSheet open triggerRef={triggerRef} onClose={vi.fn()} />
      </>,
    );
    expect(screen.getByLabelText(/Чтобы подтвердить/)).toHaveValue("");
    expect(screen.getByRole("button", { name: "Удалить аккаунт" })).toBeDisabled();
  });
});

describe("DeletionRequestStatus", () => {
  it("renders nothing when there is no request or the read fails", async () => {
    mockedCurrent.mockResolvedValueOnce(null);
    const { unmount } = render(<DeletionRequestStatus />);
    await vi.waitFor(() => expect(mockedCurrent).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId("deletion-request-status")).not.toBeInTheDocument();
    unmount();

    mockedCurrent.mockRejectedValueOnce(new Error("[500] boom"));
    render(<DeletionRequestStatus />);
    await vi.waitFor(() => expect(mockedCurrent).toHaveBeenCalledTimes(2));
    expect(screen.queryByTestId("deletion-request-status")).not.toBeInTheDocument();
  });

  it("shows number, deadline and status for a person with a request", async () => {
    mockedCurrent.mockResolvedValueOnce({ ...REQUEST, status: "DELETION_PROCESSING" });
    render(<DeletionRequestStatus />);
    expect(await screen.findByTestId("deletion-request-status")).toBeInTheDocument();
    expect(screen.getByText(REQUEST.request_id)).toBeInTheDocument();
    expect(screen.getByText(/11 октября 2026/)).toBeInTheDocument();
    expect(screen.getByText(/Статус: выполняется/)).toBeInTheDocument();
  });
});
