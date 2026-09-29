/**
 * DRF-2539 — все читатели фото мастера рисуют его через прокси.
 *
 * Карточка витрины (`MasterCard`, её же видят профиль мастера и выбор
 * мастера) и аватар мастерской поверхности (`AvatarSheet`): картинка —
 * `blob:`-адрес байтов, взятых с подписью; пока грузится, фото нет или
 * пришёл адрес хранилища — инициалы, и `<img>` на адрес хранилища не
 * появляется ни на кадр.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/max-sdk", async (orig) => ({
  ...(await orig<typeof import("../lib/max-sdk")>()),
  getInitData: () => "test-init-data",
}));

import { AvatarSheet } from "./AvatarSheet";
import { MasterCard } from "./MasterCard";
import type { Master } from "../lib/api";
import { resetMasterPhotoCacheForTests } from "../lib/master-photo";

const PHOTO = "/api/v1/customer/media/masters/7b0c3f7e-1f7a-4a53-9a55-0d4d1f5d6a11/photo?v=3f2a9c1b7d4e";
const STORAGE_URL =
  "http://minio:9000/beautygo-media/specialists/avatars/a.jpg?AWSAccessKeyId=KEY&Signature=SIG&Expires=1790000000";

const base: Master = {
  id: "7b0c3f7e-1f7a-4a53-9a55-0d4d1f5d6a11",
  name: "Анна Петрова",
  specialization: "",
  bio: "",
  experience: "",
  rating: null,
  photo_url: "",
};

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  vi.spyOn(URL, "createObjectURL").mockImplementation(() => "blob:ayla/master-1");
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
});

afterEach(() => {
  resetMasterPhotoCacheForTests();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function photoResponse(): Response {
  return new Response(new Uint8Array([0xff, 0xd8, 0xff]), {
    status: 200,
    headers: { "Content-Type": "image/jpeg" },
  });
}

function imgSrcs(container: HTMLElement): string[] {
  return [...container.querySelectorAll("img")].map((img) => img.getAttribute("src") ?? "");
}

describe("карточка мастера", () => {
  it("путь прокси → blob-картинка, запрос с подписью", async () => {
    fetchMock.mockResolvedValueOnce(photoResponse());
    const { container } = render(<MasterCard master={{ ...base, photo_url: PHOTO }} onSelect={vi.fn()} />);
    expect(screen.getByText("АП")).toBeTruthy();
    await waitFor(() => expect(imgSrcs(container)).toEqual(["blob:ayla/master-1"]));
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(PHOTO);
    expect(new Headers(init.headers).get("Authorization")).toBe("MaxInitData test-init-data");
  });

  it("адрес хранилища → инициалы, без запроса и без <img>", async () => {
    const { container } = render(
      <MasterCard master={{ ...base, photo_url: STORAGE_URL }} onSelect={vi.fn()} />,
    );
    expect(screen.getByText("АП")).toBeTruthy();
    await new Promise((r) => setTimeout(r, 0));
    expect(imgSrcs(container)).toEqual([]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("прокси ответил 404 → инициалы остаются", async () => {
    fetchMock.mockResolvedValueOnce(new Response("{}", { status: 404 }));
    const { container } = render(<MasterCard master={{ ...base, photo_url: PHOTO }} onSelect={vi.fn()} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await new Promise((r) => setTimeout(r, 0));
    expect(imgSrcs(container)).toEqual([]);
    expect(screen.getByText("АП")).toBeTruthy();
  });
});

describe("аватар мастерской поверхности", () => {
  it("путь прокси → blob-картинка с классом аватара", async () => {
    fetchMock.mockResolvedValueOnce(photoResponse());
    const { container } = render(
      <MemoryRouter>
        <AvatarSheet
          name="Анна Петрова"
          photoUrl={PHOTO}
          items={[{ key: "profile", label: "Профиль", to: "/master/profile" }]}
        />
      </MemoryRouter>,
    );
    await waitFor(() => expect(imgSrcs(container)).toEqual(["blob:ayla/master-1"]));
    expect(container.querySelector("img")?.className).toBe("avatar-sheet__photo");
  });
});
