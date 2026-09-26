/**
 * DRF-2455 — снимок записи дневника: аренда, отмена, освобождение.
 *
 * Всё — под `StrictMode`: приложение обёрнуто в него, и он монтирует эффект
 * дважды. Класс утечки «адрес создан, но не освобождён» у нас уже ловился
 * именно им (`screens/previewUrlOwnership.test.tsx`).
 *
 * Главный узел — счёт: после ухода всех карточек `revokeObjectURL` вызван
 * ровно столько раз, сколько `createObjectURL`.
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { StrictMode, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/max-sdk", () => ({
  getInitData: () => "test-init-data",
}));

import { resetDiaryPhotoCacheForTests } from "../lib/diary-photo";
import { useDiaryEntryPhoto } from "./useDiaryEntryPhoto";

const fetchMock = vi.fn();

/** Счётчик адресов: что создано, что отозвано и что ещё живо. */
function trackObjectUrls() {
  let n = 0;
  const created: string[] = [];
  const revoked: string[] = [];
  vi.spyOn(URL, "createObjectURL").mockImplementation(() => {
    const url = `blob:ayla/${++n}`;
    created.push(url);
    return url;
  });
  vi.spyOn(URL, "revokeObjectURL").mockImplementation((url: string) => {
    revoked.push(url);
  });
  return {
    created,
    revoked,
    live: () => created.filter((u) => !revoked.includes(u)),
  };
}

function photoResponse(): Response {
  return new Response(new Blob(["jpeg"], { type: "image/jpeg" }), { status: 200 });
}

const strict = ({ children }: { children: ReactNode }) => <StrictMode>{children}</StrictMode>;

/** Отложенная уборка — на следующем тике. */
async function flushSweep() {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

let urls: ReturnType<typeof trackObjectUrls>;

beforeEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  urls = trackObjectUrls();
});

afterEach(async () => {
  await flushSweep();
  // Каждый узел уходит, отдав всё: остаток кэша — утечка, а не «чужая забота».
  expect(resetDiaryPhotoCacheForTests()).toBe(0);
});

describe("без снимка — ни запроса, ни адреса", () => {
  it.each([
    ["has_photo=false", { id: "a", has_photo: false }],
    ["поля нет", { id: "b" }],
  ])("%s", async (_l, entry) => {
    const { result, unmount } = renderHook(() => useDiaryEntryPhoto(entry), { wrapper: strict });
    expect(result.current).toBeNull();
    unmount();
    await flushSweep();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(urls.created).toEqual([]);
  });
});

describe("со снимком", () => {
  it("один запрос даже под StrictMode; после ухода освобождено столько же, сколько создано", async () => {
    fetchMock.mockResolvedValueOnce(photoResponse());

    const { result, unmount } = renderHook(() => useDiaryEntryPhoto({ id: "log-1", has_photo: true }), {
      wrapper: strict,
    });

    await waitFor(() => expect(result.current).toBe("blob:ayla/1"));
    expect(fetchMock).toHaveBeenCalledTimes(1);

    unmount();
    await flushSweep();

    expect(urls.created.length).toBeGreaterThan(0);
    expect(urls.revoked.length).toBe(urls.created.length);
    expect(urls.live()).toEqual([]);
  });

  it("одна запись в двух карточках — один запрос и один адрес", async () => {
    fetchMock.mockResolvedValueOnce(photoResponse());
    const entry = { id: "log-2", has_photo: true };

    const a = renderHook(() => useDiaryEntryPhoto(entry), { wrapper: strict });
    const b = renderHook(() => useDiaryEntryPhoto(entry), { wrapper: strict });

    await waitFor(() => expect(b.result.current).toBe("blob:ayla/1"));
    expect(a.result.current).toBe("blob:ayla/1");
    expect(fetchMock).toHaveBeenCalledTimes(1);

    a.unmount();
    await flushSweep();
    // Вторая карточка ещё на экране — адрес жив.
    expect(urls.revoked).toEqual([]);

    b.unmount();
    await flushSweep();
    expect(urls.revoked).toEqual(urls.created);
  });

  it("уход до ответа отменяет запрос, и поздний байт не создаёт сироту", async () => {
    let resolve!: (r: Response) => void;
    fetchMock.mockImplementationOnce(
      (_url: string, init: RequestInit) =>
        new Promise<Response>((res) => {
          resolve = res;
          init.signal?.addEventListener("abort", () => undefined);
        }),
    );

    const { unmount } = renderHook(() => useDiaryEntryPhoto({ id: "log-3", has_photo: true }), {
      wrapper: strict,
    });
    const signal = (fetchMock.mock.calls[0] as [string, RequestInit])[1].signal!;

    unmount();
    await flushSweep();
    expect(signal.aborted).toBe(true);

    await act(async () => {
      resolve(photoResponse());
      await new Promise((r) => setTimeout(r, 0));
    });

    expect(urls.created).toEqual([]);
    expect(urls.live()).toEqual([]);
  });

  it("404 — «фото нет»: ответ пришёл, адреса нет, ошибки нет", async () => {
    let answered = false;
    fetchMock.mockImplementationOnce(async () => {
      answered = true;
      return new Response(JSON.stringify({ error: "not_found", detail: "" }), { status: 404 });
    });

    const { result, unmount } = renderHook(() => useDiaryEntryPhoto({ id: "log-4", has_photo: true }), {
      wrapper: strict,
    });

    await waitFor(() => expect(answered).toBe(true));
    await flushSweep();
    // Ответ разобран (иначе null был бы просто «ещё грузится»): адрес не создан.
    expect(urls.created).toEqual([]);
    expect(result.current).toBeNull();
    unmount();
  });

  it("карточка сменила запись — ни кадра чужого снимка", async () => {
    let second!: (r: Response) => void;
    fetchMock
      .mockResolvedValueOnce(photoResponse())
      .mockImplementationOnce(() => new Promise<Response>((res) => (second = res)));

    const { result, rerender, unmount } = renderHook(
      ({ id }: { id: string }) => useDiaryEntryPhoto({ id, has_photo: true }),
      { wrapper: strict, initialProps: { id: "log-5" } },
    );
    await waitFor(() => expect(result.current).toBe("blob:ayla/1"));

    rerender({ id: "log-6" });
    // Снимок log-6 ещё не пришёл — карточка пуста, а не показывает log-5.
    expect(result.current).toBeNull();

    await act(async () => {
      second(photoResponse());
      await new Promise((r) => setTimeout(r, 0));
    });
    await waitFor(() => expect(result.current).toBe("blob:ayla/2"));

    unmount();
    await flushSweep();
    expect(urls.live()).toEqual([]);
  });
});
