/**
 * Сторож «часы из строки» (DRF-2589).
 *
 * Корень DRF-2589 — не UTC на проводе сам по себе, а то, что экраны берут
 * ЧАСЫ ПРЯМО ИЗ СТРОКИ ISO (регуляркой или срезом), отбрасывая смещение.
 * Провод отдавал `06:00+00:00` при визите в 09:00 по салону — человек видел
 * «в 06:00». Сервер теперь отдаёт время визита в поясе салона записи, и
 * разбор строки в `lib/format.ts` на это опирается.
 *
 * Правило: разбирать часы из строки ISO можно только в `lib/format.ts` — это
 * дом, у которого названа опора (пояс задаёт сервер). Новое место с таким
 * разбором — красное с `файл:строка`: следующее поле приедет с той же бедой,
 * если провод у него не в поясе салона.
 *
 * Названные исключения — места ЧУЖИХ проводов (администратор, мастер),
 * вне DRF-2589: у каждого причина. Сузить список, когда провод переведён.
 */
import { describe, expect, it } from "vitest";

const SOURCES = Object.fromEntries(
  Object.entries(
    import.meta.glob("../**/*.{ts,tsx}", { query: "?raw", import: "default", eager: true }) as Record<
      string,
      string
    >,
  ).map(([path, text]) => [path.replace(/^\.\//, "../lib/"), text]),
);

/** Часы из строки ISO: регулярка с `T(\d{2}` или срез `slice(11, 16)`. */
const WALL_CLOCK = [/T\(\\d\{2\}/, /\.slice\(\s*11\s*,\s*16\s*\)/, /\.substring\(\s*11\b/];

const HOME = "../lib/format.ts";

/** Чужие провода — названы с причиной, не чинятся здесь. */
const KNOWN: Record<string, string> = {
  "../lib/booking-time.ts":
    "час СЛОТА: /customer/slots отдаёт начало в поясе салона (views.slots — local.isoformat()), опора названа в докстринге файла",
  "../components/booking/NewBookingForm.tsx":
    "время, предложенное ассистентом сотруднику (провод master_api/assistant), вне DRF-2589",
  "../screens/admin/SalonPilotScheduleScreen.tsx":
    "день салона администратора — провод admin_api, вне DRF-2589; проверить пояс отдельно",
  "../screens/MasterWorkingHoursScreen.tsx":
    "часы работы мастера — провод master_api (время смены, не визита), вне DRF-2589",
};

function hits(): string[] {
  const out: string[] = [];
  for (const [path, text] of Object.entries(SOURCES)) {
    if (/\.test\.(ts|tsx)$/.test(path) || path === HOME) continue;
    text.split("\n").forEach((line, i) => {
      if (WALL_CLOCK.some((re) => re.test(line))) out.push(`${path}:${i + 1}`);
    });
  }
  return out;
}

describe("часы из строки ISO — только в lib/format.ts (DRF-2589)", () => {
  it("обход видит исходники и дом правила", () => {
    expect(Object.keys(SOURCES).length).toBeGreaterThan(100);
    const home = SOURCES[HOME] ?? "";
    // Положительный контроль: в доме разбор есть, сторож его узнаёт.
    expect(WALL_CLOCK.some((re) => home.split("\n").some((l) => re.test(l)))).toBe(true);
  });

  it("вне дома — только названные чужие провода", () => {
    const files = [...new Set(hits().map((h) => h.replace(/:\d+$/, "")))].sort();
    expect(files).toEqual(Object.keys(KNOWN).sort());
  });
});
