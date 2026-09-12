/**
 * Component tests for `CustomerProfileScreen` — обе секции вернулись
 * работающими (DRF-1475 §24 на ручках DRF-1520).
 *
 * Экран читает `/customer/me` (имя) и `me/consents/` (согласия,
 * подсказки, хранение данных). Мокируются `fetchProfile` и `request`:
 * `fetchProfile` держит ссылку на исходный `request` внутри `api.ts`,
 * поэтому подмена одного другого не перехватывает. `ApiError` настоящий —
 * по нему различаются 409 и 502.
 *
 * NOTE: «Запросить данные» / «Удалить аккаунт» открывают in-app C5
 * sheets (`PersonalDataSheets.tsx`), чей собственный suite мокирует
 * `lib/personal-data`.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, fetchProfile, request, type Profile } from "../lib/api";

vi.mock("../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/api")>();
  return {
    ...actual,
    fetchProfile: vi.fn(),
    updateProfile: vi.fn(),
    request: vi.fn(),
  };
});

const fetchProfileMock = vi.mocked(fetchProfile);
const requestMock = vi.mocked(request);

const CONSENTS = "/me/consents/";
const MARKETING = "/me/consents/marketing/";
const HINTS = "/me/consents/proactive-hints/";
const DATA_STORAGE = "/me/consents/data-storage/";
const HEALTH = "/me/health-consent/";

const MARKETING_SWITCH = "Получать акции и предложения от салонов";
const HINTS_SWITCH = "Получать подсказки от Ayla";
const REVOKE_ROW_BTN = "Отозвать согласие на хранение данных";

function profileFixture(overrides: Partial<Profile> = {}): Profile {
  return {
    bot_user_id: "u-1",
    display_name: "Анна Петрова",
    client_name: "Аня",
    phone_masked: "+7 ••• ••• 45 67",
    timezone: "Europe/Moscow",
    joined_at: "2026-05-14T10:30:00+03:00",
    preferences: {
      notify_reminders: true,
      notify_retention: true,
      notify_promo: false,
      notify_birthday: false,
      birthday_date: null,
    },
    favorites: { master_name: null, service_name: null },
    ...overrides,
  };
}

interface DocOptions {
  marketing?: boolean;
  storageGranted?: boolean;
  hintsEnabled?: boolean;
  revocation?: { status: string; failed_steps?: string[] };
}

/** Форма ответа `apps/consent/customer.py::read_consents`. */
function consentsDoc(o: DocOptions = {}): Record<string, unknown> {
  const marketing = o.marketing ?? false;
  const storageGranted = o.storageGranted ?? true;
  const at = storageGranted ? "2026-05-14T10:30:00+03:00" : null;
  const doc: Record<string, unknown> = {
    consents: {
      personal_data: {
        granted: storageGranted,
        granted_at: at,
        document_version: "welcome-v1",
      },
      marketing: {
        granted: marketing,
        granted_at: marketing ? "2026-06-01T09:00:00+03:00" : null,
        document_version: marketing ? "marketing-v1" : "",
      },
      health: { granted: false, granted_at: null, document_version: "" },
    },
    proactive_hints: { enabled: o.hintsEnabled ?? true },
    data_storage: {
      granted: storageGranted,
      granted_at: at,
      document_version: "welcome-v1",
      revocation: {
        disclosure_version: "data-storage-revocation-v1",
        consequences: ["ayla_delete", "memory_delete", "consent_withdraw"],
        retained: ["bookings", "payments"],
      },
    },
  };
  if (o.revocation) doc.revocation = o.revocation;
  return doc;
}

const HEALTH_STATE = {
  granted: false,
  granted_at: null,
  document_version: "",
  current_document_version: "health-data-v1",
};

/**
 * Маршрутизация мока по пути. Каждый тест переопределяет только те
 * ветки, которые ему интересны; остальные отвечают спокойным умолчанием,
 * чтобы «не дошло» нельзя было спутать с «дошло пустым».
 */
function routeRequests(
  handlers: Partial<Record<string, (init?: RequestInit) => unknown>> = {},
  base: DocOptions = {},
) {
  requestMock.mockImplementation((path: string, init?: RequestInit) => {
    const handler = handlers[path];
    if (handler) return Promise.resolve(handler(init)) as never;
    if (path === HEALTH) return Promise.resolve(HEALTH_STATE) as never;
    if (path === CONSENTS) return Promise.resolve(consentsDoc(base)) as never;
    return Promise.reject(new Error(`unexpected request: ${path}`)) as never;
  });
}

async function renderFresh() {
  vi.resetModules();
  const { CustomerProfileScreen } = await import("./CustomerProfileScreen");
  render(
    <MemoryRouter initialEntries={["/customer/profile"]}>
      <CustomerProfileScreen />
    </MemoryRouter>,
  );
}

/**
 * Render with `import.meta.env.DEV === false` — the production build
 * shape. The flag is read at module scope, so stub the env BEFORE the
 * dynamic import (vi.resetModules keeps module instances isolated).
 */
async function renderFreshProd() {
  vi.resetModules();
  vi.stubEnv("DEV", false);
  try {
    const { CustomerProfileScreen } = await import("./CustomerProfileScreen");
    render(
      <MemoryRouter initialEntries={["/customer/profile"]}>
        <CustomerProfileScreen />
      </MemoryRouter>,
    );
  } finally {
    vi.unstubAllEnvs();
  }
}

/** Офлайн на момент монтирования — экран читает `navigator.onLine`. */
function goOffline() {
  vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
}

describe("CustomerProfileScreen (настоящие ручки согласий)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    vi.clearAllMocks();
    fetchProfileMock.mockResolvedValue(profileFixture());
    routeRequests();
  }, 15000);

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("bottom nav: «День» is not offered, the four real tabs are", async () => {
    // DRF-1546 — та же уборка, что на «Записях»: поверхности «День»
    // не существует, её роль исполнял домашний экран, а он теперь
    // «Главная». Стража парная: снята одна вкладка, а не навигация.
    await renderFresh();
    const nav = within(
      await screen.findByRole("navigation", { name: "Основная навигация" }),
    );
    expect(nav.queryByRole("button", { name: "День" })).not.toBeInTheDocument();
    for (const tab of ["Главная", "Записи", "Услуги", "Я"]) {
      expect(nav.getByRole("button", { name: tab })).toBeInTheDocument();
    }
  }, 15000);

  it("загрузка: скелет вместо выдуманного состояния", async () => {
    let release: () => void = () => {};
    fetchProfileMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = () => resolve(profileFixture());
        }),
    );
    await renderFresh();
    // Ни одного утверждения о согласиях, пока их не прочитали.
    expect(screen.queryByRole("switch", { name: MARKETING_SWITCH })).toBeNull();
    expect(screen.queryByRole("switch", { name: HINTS_SWITCH })).toBeNull();
    expect(screen.queryByText("Хранение данных")).toBeNull();
    release();
    // Положительная стража: после ответа состояние появляется.
    expect(await screen.findByText("Аня")).toBeInTheDocument();
  }, 15000);

  it("ошибка чтения: честный текст и повтор, без секций-призраков", async () => {
    const user = userEvent.setup();
    fetchProfileMock.mockRejectedValueOnce(new ApiError(500, "boom", "нет"));
    await renderFresh();
    expect(
      await screen.findByText("Что-то у нас не получается прямо сейчас."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("switch", { name: HINTS_SWITCH })).toBeNull();
    expect(screen.queryByText("Хранение данных")).toBeNull();
    fetchProfileMock.mockResolvedValue(profileFixture());
    await user.click(screen.getByRole("button", { name: "Попробовать снова" }));
    expect(await screen.findByText("Аня")).toBeInTheDocument();
    expect(
      screen.getByRole("switch", { name: HINTS_SWITCH }),
    ).toBeInTheDocument();
  }, 15000);

  it("офлайн: баннер честный И действия выключены (правило #1421)", async () => {
    goOffline();
    await renderFresh();
    await screen.findByText("Аня");
    expect(
      screen.getByText("Профиль может быть устаревшим. Проверь подключение."),
    ).toBeInTheDocument();
    // Секции видны — выключены именно действия, а не содержимое.
    expect(
      screen.getByRole("switch", { name: MARKETING_SWITCH }),
    ).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("switch", { name: HINTS_SWITCH })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(screen.getByRole("button", { name: REVOKE_ROW_BTN })).toBeDisabled();
  }, 15000);

  it("R4 «Подсказки от Ayla» вернулась и пишет в ручку", async () => {
    const user = userEvent.setup();
    routeRequests(
      { [HINTS]: () => consentsDoc({ hintsEnabled: false }) },
      { hintsEnabled: true },
    );
    await renderFresh();
    const toggle = await screen.findByRole("switch", { name: HINTS_SWITCH });
    expect(toggle).toHaveAttribute("aria-checked", "true");
    await user.click(toggle);
    await waitFor(() =>
      expect(requestMock).toHaveBeenCalledWith(HINTS, {
        method: "POST",
        body: JSON.stringify({ enabled: false }),
      }),
    );
    await waitFor(() =>
      expect(toggle).toHaveAttribute("aria-checked", "false"),
    );
    expect(
      await screen.findByText("Поняла, первой писать не буду."),
    ).toBeInTheDocument();
  }, 15000);

  it("маркетинг идёт в реестр, а не в PATCH /me", async () => {
    const user = userEvent.setup();
    routeRequests({ [MARKETING]: () => consentsDoc({ marketing: true }) });
    await renderFresh();
    const toggle = await screen.findByRole("switch", { name: MARKETING_SWITCH });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    await user.click(toggle);
    await waitFor(() =>
      expect(requestMock).toHaveBeenCalledWith(MARKETING, { method: "POST" }),
    );
    await waitFor(() => expect(toggle).toHaveAttribute("aria-checked", "true"));
    // Отрицание при живой положительной страже: зеркало не трогали.
    expect(requestMock.mock.calls.map((c) => c[0])).not.toContain("/me");
  }, 15000);

  it("строка «Хранение данных» — не тумблер, а действие с датой", async () => {
    await renderFresh();
    await screen.findByText("Аня");
    expect(screen.getByText("Хранение данных")).toBeInTheDocument();
    expect(screen.getByText("Разрешено 14 мая 2026")).toBeInTheDocument();
    const revoke = screen.getByRole("button", { name: REVOKE_ROW_BTN });
    expect(revoke).toBeInTheDocument();
    expect(revoke).toHaveTextContent("Отозвать");
    // Тумблера у этой строки нет — отзыв не переключается касанием.
    expect(
      screen.queryByRole("switch", { name: /хранени/i }),
    ).not.toBeInTheDocument();
    // §35 п.6 — отзыв и удаление аккаунта не сливаются.
    expect(
      screen.getByRole("button", { name: "Удалить аккаунт и личные данные" }),
    ).toBeInTheDocument();
  }, 15000);

  it("лист отзыва показывает утверждённый текст и ровно две подписи", async () => {
    const user = userEvent.setup();
    await renderFresh();
    await user.click(await screen.findByRole("button", { name: REVOKE_ROW_BTN }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(
      within(dialog).getByText(/После подтверждения/),
    ).toHaveTextContent(
      "После подтверждения Ayla перестанет сохранять и использовать ваши " +
        "данные, основанные на этом согласии. Доступные для удаления данные " +
        "будут удалены. Сведения, которые мы обязаны хранить по закону или " +
        "для исполнения ваших действующих записей, могут сохраниться на " +
        "необходимый срок. Сам аккаунт и доступ к записям останутся. " +
        "Вернуть удалённые данные будет нельзя.",
    );
    expect(
      within(dialog).getByRole("button", { name: "Не отзывать" }),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: "Отозвать согласие" }),
    ).toBeInTheDocument();
  }, 15000);

  it("revoked: отзыв состоялся, строка и лист говорят проверенное", async () => {
    const user = userEvent.setup();
    routeRequests({
      [DATA_STORAGE]: () =>
        consentsDoc({
          storageGranted: false,
          revocation: { status: "revoked", failed_steps: [] },
        }),
    });
    await renderFresh();
    await user.click(await screen.findByRole("button", { name: REVOKE_ROW_BTN }));
    await user.click(
      await screen.findByRole("button", { name: "Отозвать согласие" }),
    );
    expect(
      await screen.findByText(
        "Согласие отозвано. Данные, которые можно удалить, удалены.",
      ),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(requestMock).toHaveBeenCalledWith(DATA_STORAGE, {
        method: "DELETE",
        body: JSON.stringify({
          confirmation: "УДАЛИТЬ",
          disclosure_version: "data-storage-revocation-v1",
        }),
      }),
    );
    await user.click(screen.getByRole("button", { name: "Закрыть" }));
    // Строка перечитана из ответа: согласия нет, кнопки отзыва тоже.
    const row = await screen.findByRole("group", { name: "Хранение данных" });
    expect(within(row).getByText("Не разрешено")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: REVOKE_ROW_BTN })).toBeNull();
  }, 15000);

  it("revoked_partial_processing: только «Согласие отозвано», ни слова о полноте", async () => {
    const user = userEvent.setup();
    routeRequests({
      [DATA_STORAGE]: () =>
        consentsDoc({
          storageGranted: false,
          revocation: {
            status: "revoked_partial_processing",
            failed_steps: ["ayla_delete"],
          },
        }),
    });
    await renderFresh();
    await user.click(await screen.findByRole("button", { name: REVOKE_ROW_BTN }));
    await user.click(
      await screen.findByRole("button", { name: "Отозвать согласие" }),
    );
    const dialog = await screen.findByRole("dialog");
    // Положительная стража: одно проверенное утверждение сказано.
    expect(within(dialog).getByText("Согласие отозвано.")).toBeInTheDocument();
    // §35 п.16 — и ни одного утверждения о полноте удаления.
    expect(within(dialog).queryByText(/удалены/)).toBeNull();
    expect(within(dialog).queryByText(/осталась/)).toBeNull();
    expect(within(dialog).queryByText(/ayla_delete/)).toBeNull();
  }, 15000);

  it("§35 п.9: подсказки после отзыва — то, что сказал сервер", async () => {
    // Решение владельца требует, чтобы подсказки погасли, и теперь их
    // гасит сервер: `revoke_data_storage` ставит
    // `proactive_messages_opt_out` по всем оболочкам человека. Экран
    // по-прежнему показывает ответ сервера, а не собственное решение, —
    // просто ответ наконец совпал с решением владельца.
    const user = userEvent.setup();
    routeRequests(
      {
        [DATA_STORAGE]: () =>
          consentsDoc({
            storageGranted: false,
            hintsEnabled: false,
            revocation: { status: "revoked", failed_steps: [] },
          }),
      },
      { hintsEnabled: true },
    );
    await renderFresh();
    // Есть чему гаснуть: до отзыва тумблер включён.
    expect(
      await screen.findByRole("switch", { name: HINTS_SWITCH }),
    ).toHaveAttribute("aria-checked", "true");
    await user.click(await screen.findByRole("button", { name: REVOKE_ROW_BTN }));
    await user.click(
      await screen.findByRole("button", { name: "Отозвать согласие" }),
    );
    await screen.findByText(/Согласие отозвано/);
    await user.click(screen.getByRole("button", { name: "Закрыть" }));
    expect(
      await screen.findByRole("switch", { name: HINTS_SWITCH }),
    ).toHaveAttribute("aria-checked", "false");
  }, 15000);

  it("409 stale_disclosure: не дожимаем тело, а перечитываем раскрытие", async () => {
    const user = userEvent.setup();
    routeRequests({
      [DATA_STORAGE]: () => {
        throw new ApiError(409, "stale_disclosure", "changed");
      },
    });
    await renderFresh();
    await user.click(await screen.findByRole("button", { name: REVOKE_ROW_BTN }));
    const before = requestMock.mock.calls.filter(
      (c) => c[0] === CONSENTS,
    ).length;
    await user.click(
      await screen.findByRole("button", { name: "Отозвать согласие" }),
    );
    expect(
      await screen.findByText(/Текст про последствия обновился/),
    ).toBeInTheDocument();
    // Перечитали состояние — именно это требует контракт на 409.
    await waitFor(() =>
      expect(
        requestMock.mock.calls.filter((c) => c[0] === CONSENTS).length,
      ).toBeGreaterThan(before),
    );
    expect(screen.queryByRole("button", { name: "Попробовать ещё раз" })).toBeNull();
  }, 15000);

  it("502: отзыв не состоялся, и так и сказано", async () => {
    const user = userEvent.setup();
    routeRequests({
      [DATA_STORAGE]: () => {
        throw new ApiError(502, "", "");
      },
    });
    await renderFresh();
    await user.click(await screen.findByRole("button", { name: REVOKE_ROW_BTN }));
    await user.click(
      await screen.findByRole("button", { name: "Отозвать согласие" }),
    );
    expect(
      await screen.findByText(
        "Не получилось отозвать согласие. Оно осталось действующим.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Попробовать ещё раз" }),
    ).toBeInTheDocument();
  }, 15000);

  it("обрыв связи посреди отзыва: исход неизвестен, и это сказано", async () => {
    const user = userEvent.setup();
    routeRequests({
      [DATA_STORAGE]: () => {
        throw new TypeError("Failed to fetch");
      },
    });
    await renderFresh();
    await user.click(await screen.findByRole("button", { name: REVOKE_ROW_BTN }));
    await user.click(
      await screen.findByRole("button", { name: "Отозвать согласие" }),
    );
    expect(
      await screen.findByText(/не знаю, дошёл ли/),
    ).toBeInTheDocument();
    // Ни одного утверждения о том, что согласие снято или осталось.
    expect(screen.queryByText(/Согласие отозвано/)).toBeNull();
    expect(screen.queryByText(/осталось действующим/)).toBeNull();
  }, 15000);

  it("отозванное согласие: кнопки выдачи нет, потому что ручки нет", async () => {
    routeRequests({}, { storageGranted: false });
    await renderFresh();
    await screen.findByText("Аня");
    // Положительная стража: строка на месте и говорит состояние.
    const row = screen.getByRole("group", { name: "Хранение данных" });
    expect(within(row).getByText("Не разрешено")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Разрешить хранение/ })).toBeNull();
    expect(screen.queryByRole("button", { name: REVOKE_ROW_BTN })).toBeNull();
  }, 15000);

  it("телефон на экране не появляется (DRF-1039)", async () => {
    await renderFresh();
    // Положительная стража: экран построен по реальным ответам.
    expect(await screen.findByText("Аня")).toBeInTheDocument();
    expect(screen.getByText("Разрешено 14 мая 2026")).toBeInTheDocument();
    expect(screen.queryByText(/\+7/)).toBeNull();
    expect(screen.queryByText(/45 67/)).toBeNull();
  }, 15000);

  it("opens the in-app C5 export sheet from «Запросить данные»", async () => {
    const user = userEvent.setup();
    await renderFresh();
    await user.click(
      await screen.findByRole("button", { name: "Запросить данные" }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByText("Скачать мои данные")).toBeInTheDocument();
  }, 15000);

  it("opens the in-app C5 delete sheet and closes it on Escape", async () => {
    const user = userEvent.setup();
    await renderFresh();
    await user.click(
      await screen.findByRole("button", { name: "Удалить аккаунт и личные данные" }),
    );
    expect(
      await screen.findByText("Удалить аккаунт и личные данные?"),
    ).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  }, 15000);
});

/**
 * Production build shape (`import.meta.env.DEV === false`): обе
 * вернувшиеся секции обязаны работать и в проде — DEV-заглушки
 * `?stub=` в прод-сборке не читаются, а prod-гарды сняты вместе с
 * заглушками.
 */
describe("CustomerProfileScreen (prod build)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
    vi.clearAllMocks();
    fetchProfileMock.mockResolvedValue(profileFixture());
    routeRequests();
  }, 15000);

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("имя, маркетинг, подсказки и хранение данных видны в проде", async () => {
    await renderFreshProd();
    expect(await screen.findByText("Аня")).toBeInTheDocument();
    expect(
      screen.getByRole("switch", { name: MARKETING_SWITCH }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("switch", { name: HINTS_SWITCH }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: REVOKE_ROW_BTN }),
    ).toBeInTheDocument();
    // Никакой выдуманной личности и никакого отказа от снятой гарды.
    expect(screen.queryByText("Анна Петрова")).not.toBeInTheDocument();
    expect(
      screen.queryByText(/Профиль ещё не подключён/),
    ).not.toBeInTheDocument();
  }, 15000);

  it("prod: тумблер подсказок шлёт реальный POST", async () => {
    const user = userEvent.setup();
    routeRequests(
      { [HINTS]: () => consentsDoc({ hintsEnabled: false }) },
      { hintsEnabled: true },
    );
    await renderFreshProd();
    await user.click(await screen.findByRole("switch", { name: HINTS_SWITCH }));
    await waitFor(() =>
      expect(requestMock).toHaveBeenCalledWith(HINTS, {
        method: "POST",
        body: JSON.stringify({ enabled: false }),
      }),
    );
  }, 15000);

  it("prod: отзыв хранения данных доходит до ручки", async () => {
    const user = userEvent.setup();
    routeRequests({
      [DATA_STORAGE]: () =>
        consentsDoc({
          storageGranted: false,
          revocation: { status: "revoked", failed_steps: [] },
        }),
    });
    await renderFreshProd();
    await user.click(await screen.findByRole("button", { name: REVOKE_ROW_BTN }));
    await user.click(
      await screen.findByRole("button", { name: "Отозвать согласие" }),
    );
    await waitFor(() =>
      expect(requestMock).toHaveBeenCalledWith(DATA_STORAGE, {
        method: "DELETE",
        body: JSON.stringify({
          confirmation: "УДАЛИТЬ",
          disclosure_version: "data-storage-revocation-v1",
        }),
      }),
    );
  }, 15000);
});
