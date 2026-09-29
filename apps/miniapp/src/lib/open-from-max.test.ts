/**
 * DRF-1893 — отказ транспорта называется одним экраном и одной копией.
 *
 * Сервер отвечает 401 `no_init_data` на пустой / испорченный / чужой /
 * просроченный initData (одним кодом, причина — в логе сервера). Старые слаги
 * (`malformed`, `bad_signature`, `stale`) клиент читает так же: закэшированный
 * бандл может прийти раньше нового сервера или наоборот.
 *
 * Здесь: копия отказа транспорта — «Открой Ayla из MAX» без кнопки повтора
 * (повтор без MAX не поможет); классификатор загрузки узнаёт отказ; в
 * исходниках не осталось OAuth-регистрации; подписанный dev-initData не
 * читается вне режима разработки.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./api";
import * as authCopy from "./auth-error-copy";

const TITLE = "Открой Ayla из MAX";

const TRANSPORT_SLUGS = ["no_init_data", "malformed", "bad_signature", "stale"];

describe("копия отказа транспорта", () => {
  it.each(TRANSPORT_SLUGS)("%s → «Открой Ayla из MAX», без повтора", (slug) => {
    const copy = authCopy.authErrorCopy(slug);
    expect(copy.title).toBe(TITLE);
    expect(copy.retryLabel).toBeUndefined();
    const isTransport = (authCopy as Record<string, unknown>).isTransportRefusalSlug as
      | ((s: string) => boolean)
      | undefined;
    expect(isTransport, "нет isTransportRefusalSlug").toBeTypeOf("function");
    expect(isTransport?.(slug)).toBe(true);
  });

  it("классификатор загрузки узнаёт no_init_data как отказ входа", () => {
    expect(authCopy.loadErrorReason(new ApiError(401, "no_init_data", ""))).toEqual({
      kind: "auth",
      slug: "no_init_data",
    });
  });
});

const SOURCES = import.meta.glob(
  ["../screens/**/*.tsx", "../components/**/*.tsx", "../hooks/**/*.ts", "../lib/**/*.ts", "../App.tsx"],
  { query: "?raw", import: "default", eager: true },
) as Record<string, string>;

describe("регистрации и OAuth в Mini App нет (раздел U)", () => {
  it("исходники не зовут VITE_MAX_OAUTH_URL, не рисуют «Зарегистрироваться» и гейт анонима", () => {
    const offenders = Object.entries(SOURCES)
      .filter(([path]) => !/\.test\.tsx?$/.test(path))
      .filter(([, src]) => /VITE_MAX_OAUTH_URL|Зарегистрироваться|AnonymousGate/.test(src))
      .map(([path]) => path);
    expect(Object.keys(SOURCES).length).toBeGreaterThan(50); // сторож видит исходники
    expect(offenders).toEqual([]);
  });
});

describe("подписанный dev-initData — только в режиме разработки", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("вне DEV getInitData() не отдаёт VITE_DEV_INIT_DATA", async () => {
    vi.stubEnv("DEV", false);
    vi.stubEnv("VITE_DEV_INIT_DATA", "user=%7B%7D&hash=dev-signed-1893");
    vi.resetModules();
    const sdk = await import("./max-sdk");

    expect(sdk.getInitData()).toBe("");
  });
});
