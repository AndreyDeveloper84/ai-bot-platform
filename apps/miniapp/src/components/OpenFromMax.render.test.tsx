/**
 * DRF-1893 — отказ транспорта посреди сессии рисует тот же экран.
 *
 * Пре-проверка в App закрывает пустой initData до первого запроса. Но
 * initData может протухнуть посреди сессии (сервер ответит 401 `no_init_data`),
 * а экраны ловят ошибку сами — через `StateError` и `HelloScreen`. Им нужен тот
 * же исход: «Открой Ayla из MAX», кнопка возврата, без «Попробовать снова».
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

vi.mock("../lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../lib/api")>();
  return { ...original, authVerify: vi.fn() };
});

import { ApiError, authVerify } from "../lib/api";
import { HelloScreen } from "../screens/HelloScreen";
import { StateError } from "./StateError";

const TITLE = "Открой Ayla из MAX";
const RETURN = "Вернуться в MAX";

describe("отказ транспорта посреди сессии", () => {
  it("StateError на no_init_data → экран возврата в MAX, без повтора", () => {
    render(<StateError err={new ApiError(401, "no_init_data", "")} onRetry={() => {}} />);

    expect(screen.getByText(TITLE)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: RETURN })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Попробовать снова" })).not.toBeInTheDocument();
  });

  it("HelloScreen на no_init_data → тот же экран", async () => {
    vi.mocked(authVerify).mockRejectedValue(new ApiError(401, "no_init_data", ""));

    render(
      <MemoryRouter>
        <HelloScreen />
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByText(TITLE)).toBeInTheDocument());
    expect(screen.getByRole("button", { name: RETURN })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Попробовать снова" })).not.toBeInTheDocument();
  });
});
