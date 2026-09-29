/**
 * DRF-1625 — легаси-адрес имеет право быть только псевдонимом.
 *
 * # Почему сторож вообще нужен
 *
 * Этот путь уже возвращали. DRF-1480 закрывал «возвраты в legacy после
 * оценки и после переноса»: четыре живых экрана нового поколения вели
 * человека обратно в `/my-visits/*` и `/catalog`, и заметить это можно
 * было только замером — адреса были смонтированы, переход «работал».
 * Код умеет находить дорогу обратно в старое пространство имён, и
 * удаление экранов само по себе её не закрывает: маршрут вернётся
 * вместе с новым экраном, и никто не назовёт это регрессом.
 *
 * # Что именно проверяется
 *
 * Не «нет строки `/my-visits`» — такая проверка красна от комментария и
 * зелена от переименования. Проверяется СВОЙСТВО: у каждого адреса из
 * легаси-пространств имён смонтирован компонент, который смонтирован
 * ТАКЖЕ и под `/customer/*`. То есть легаси-адрес показывает ту же
 * поверхность, что канонический, — он псевдоним. Компонент, которого
 * под `/customer/*` нет, — это своя поверхность прежнего поколения, и
 * ровно это критерий DRF-1485, записанный автором у алиасов в
 * `App.tsx`: «алиасами они быть не могли — каждый показывал СВОЙ
 * экран».
 *
 * Вернуть `<Route path="/my-visits" element={<MyVisitsScreen />} />` —
 * значит смонтировать компонент, которого под `/customer/*` нет, и этот
 * тест назовёт его поимённо.
 *
 * # Почему рядом стоит точная опись
 *
 * Свойство выше пропустит НОВЫЙ легаси-адрес, если он ведёт на
 * канонический экран: `/my-visits/history -> CustomerRecordsScreen`
 * прошло бы молча. Само по себе это не дефект, но расширение мёртвого
 * пространства имён — решение, а не мелочь, и оно должно быть видно в
 * диффе. Поэтому состав легаси-адресов зафиксирован поимённо.
 *
 * # Почему проверка умеет видеть
 *
 * Регулярка по исходнику слепнет молча: переименуйте `CustomerRoutes`
 * или поменяйте форму `element={...}` — и разбор вернёт пустоту, а
 * пустота проходит любое «ни одного нарушения». Поэтому первым делом
 * идёт положительный контроль: разбор обязан увидеть ВСЕ теги `<Route>`
 * из тела функции (число сверяется) и обязан найти в каноническом
 * наборе заведомо живой экран. Ноль нарушений имеет смысл только после
 * этого.
 */
import { describe, expect, it } from "vitest";

const APP = import.meta.glob("./App.tsx", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/** Тело `CustomerRoutes` — клиентское дерево адресов целиком. */
function customerRoutesBody(): string {
  const app = Object.values(APP)[0];
  expect(app, "App.tsx не прочитан — проверка ослепла").toBeTypeOf("string");
  const start = app!.indexOf("export function CustomerRoutes()");
  expect(
    start,
    "CustomerRoutes переименован — проверка ослепла",
  ).toBeGreaterThan(0);
  const rest = app!.slice(start);
  const end = start + rest.search(/^\}/m);
  expect(end, "не нашёл конец CustomerRoutes").toBeGreaterThan(start);
  return app!.slice(start, end);
}

type Mounted = { path: string; component: string };

/**
 * Пары «адрес → компонент» из тела `CustomerRoutes`.
 *
 * Разбиение идёт по `<Route`, но НЕ по `<Routes` — иначе первым куском
 * оказывается всё содержимое обёртки, и первый же найденный в нём
 * `path=` приписывается несуществующему маршруту.
 */
function mountedRoutes(): Mounted[] {
  const body = customerRoutesBody();
  const chunks = body.split(/<Route(?![A-Za-z])/).slice(1);
  const out: Mounted[] = [];
  for (const chunk of chunks) {
    const p = /path="([^"]+)"/.exec(chunk);
    const e = /element=\{<([A-Z][A-Za-z0-9_]*)/.exec(chunk);
    if (p && e) out.push({ path: p[1]!, component: e[1]! });
  }
  // Каждый тег обязан быть разобран: маршрут, у которого регулярка не
  // нашла ни адреса, ни экрана, молча выпал бы из проверки — и именно
  // он был бы самым интересным.
  expect(
    out.length,
    "не все теги <Route> разобраны — форма записи изменилась, " +
      "и невидимый маршрут проверку не проходит, а обходит",
  ).toBe(chunks.length);
  return out;
}

/** Пространства имён прежнего поколения. */
const LEGACY_PREFIXES = ["/my-visits", "/catalog", "/book", "/me"] as const;

function isLegacy(path: string): boolean {
  return LEGACY_PREFIXES.some((p) => path === p || path.startsWith(`${p}/`));
}

/**
 * Точная опись легаси-адресов на сегодня.
 *
 * Каждая строка — псевдоним: справа стоит компонент, смонтированный
 * также и под `/customer/*`. Появление новой строки — решение расширить
 * мёртвое пространство имён; исчезновение — снятие псевдонима, за
 * которым могли остаться внешние ссылки. И то и другое видно в диффе.
 */
const EXPECTED_LEGACY: Mounted[] = [
  { path: "/catalog/:serviceId", component: "ServiceDetailScreen" },
  { path: "/book/master", component: "MasterPickerScreen" },
  { path: "/book/when", component: "BookingWhenScreen" },
  { path: "/my-visits/:bookingId/reschedule", component: "RescheduleScreen" },
];

describe("DRF-1625 · легаси-адрес — только псевдоним канонического экрана", () => {
  it("разбор видит дерево маршрутов (положительный контроль)", () => {
    const routes = mountedRoutes();
    // Присутствие: дерево разобрано и оно непустое.
    expect(routes.length).toBeGreaterThan(20);
    const canonical = routes
      .filter((r) => r.path.startsWith("/customer/"))
      .map((r) => r.component);
    // …и канонический набор непустой и содержит заведомо живой экран.
    expect(canonical).toContain("CustomerRecordsScreen");
    // Только теперь осмысленно утверждение, что легаси-набор мал.
    expect(routes.filter((r) => isLegacy(r.path)).length).toBeGreaterThan(0);
  });

  it("каждый легаси-адрес показывает экран, смонтированный и под /customer/*", () => {
    const routes = mountedRoutes();
    const canonical = new Set(
      routes.filter((r) => r.path.startsWith("/customer/")).map((r) => r.component),
    );
    const ownSurface = routes
      .filter((r) => isLegacy(r.path))
      .filter((r) => !canonical.has(r.component));
    expect(
      ownSurface,
      "Легаси-адрес монтирует СВОЙ экран, а не канонический по старому " +
        "адресу. Это не псевдоним, это возвращённая поверхность прежнего " +
        "поколения (DRF-1485, DRF-1625). Либо ведите старый адрес на " +
        "канонический экран, либо не заводите старый адрес: " +
        JSON.stringify(ownSurface),
    ).toEqual([]);
  });

  it("состав легаси-адресов ровно тот, что объявлен", () => {
    expect(mountedRoutes().filter((r) => isLegacy(r.path))).toEqual(
      EXPECTED_LEGACY,
    );
  });
});
