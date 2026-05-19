# LLM Multi-Provider Spike — Developer Handoff (DRF-279)

| Field | Value |
|---|---|
| **Date** | 2026-05-18 r1 |
| **Author** | nokgaf@gmail.com (Claude Code session) |
| **Status** | Draft for review — empirical research, no live API calls yet |
| **Ticket** | [DRF-279](https://linear.app/drfproject/issue/DRF-279) — T1 spike under [DRF-278](https://linear.app/drfproject/issue/DRF-278) EPIC-P |
| **Scope** | Empirical pricing / RU-access / 152-ФЗ / tone-quality matrix for OpenAI / DeepSeek / Qwen / YandexGPT (+ GigaChat) |
| **Out of scope** | Live API renders (deferred — flagged in §7 open question #7) |

## Foundation references (read first)

| Doc | Why it matters here |
|---|---|
| [`docs/adr/ADR-0005-multi-llm-provider-routing.md`](../../adr/ADR-0005-multi-llm-provider-routing.md) | Already-accepted routing decision: per-skill primary/fallback declared in `apps.promptreg`, resolved by `apps.orchestrator.llm.router` with CR-3 circuit breaker. This spike supplies the empirical data to pick those providers for the **humanizer** skill. |
| [`apps/llm/protocol.py`](../../../apps/llm/protocol.py) | `LLMProvider` Protocol (DRF-580). New providers from this spike (`DeepSeekProvider`, `QwenProvider`, `YandexGPTProvider`) plug in here. |
| [`apps/llm/providers/`](../../../apps/llm/providers/) | Existing implementations: `openai_provider.py` (DRF-581), `anthropic_provider.py` (DRF-583). Pattern to follow. |
| [`apps/llm/router.py`](../../../apps/llm/router.py) | L5 router (DRF-587). Already wires fallback chain — spike output feeds its config, not its code. |
| [`apps/llm/cost_tracker.py`](../../../apps/llm/cost_tracker.py) / [`pricing.py`](../../../apps/llm/pricing.py) | T7 (DRF-285) budget cap will consume the per-provider pricing tables in §2 below. |
| [`legacy_maxbot/voice_examples.py`](../../../legacy_maxbot/voice_examples.py) | Алина voice canon — `FORMULA_TELA_EXAMPLES`. Tone comparison baseline. |

---

## 0. Repo decision (resolves §7 open question #1)

> **Implementation lands in `ai-bot-platform`**, not `mysite/maxbot/`. Confirmed by
> @tikhonovmaksoft on 2026-05-18: «тут ведем разработку. я бот отделил от сайта»
> ([DRF-279 thread](https://linear.app/drfproject/issue/DRF-279)).
>
> The original ticket text references `mysite/maxbot/llm.py::chat_rag()` — that
> code is now snapshotted in [`legacy_maxbot/`](../../../legacy_maxbot/) per
> `MIGRATION_NOTICE.md`. T2–T7 work targets `apps/llm/providers/`,
> `apps.promptreg`, `apps.orchestrator.llm.router`, `apps.llm.cost_tracker`.

## 1. TL;DR

| Слот | Провайдер | Модель | Почему |
| -- | -- | -- | -- |
| **Primary (humanizer)** | DeepSeek | `deepseek-v4-flash` | Дёшево ($0.14/$0.28 за 1M), доступен из РФ **без прокси**, SSE+function-calling, OpenAI-compatible API. `chat`/`reasoner` aliases deprecated 2026-07-24 → сразу таргетим `v4-flash`/`v4-pro` IDs. |
| **Fallback #1** | Alibaba Qwen | `qwen-turbo` (Singapore) | Самый дешёвый ($0.033/$0.130 за 1M), 131K ctx, OpenAI-compatible mode, нет санкций РФ. |
| **Fallback #2** | Yandex AI Studio | `YandexGPT 5 Lite` | 152-ФЗ-сертифицирован (ФСТЭК УЗ-3), RU data residency, прямой доступ из РФ. ~0.2/0.4 ₽/1K — дороже валютных, но единственный legal-clean путь для PII. |
| **Intent (Stage 1)** | OpenAI (статус-кво) | `gpt-4o-mini` | Уже работает через `apps.llm.providers.openai_provider`. Высокое качество tool-use; 6-tool schema в `legacy_maxbot/ai_tools.py` отлажен. |

**Стратегическая рекомендация:** двухстадийный pipeline в рамках уже принятого ADR-0005.
Stage 1 (intent + tool-routing) остаётся на OpenAI gpt-4o-mini —
function-calling зрелое, инвестиций в `ai_tools.py` (мигрирует в `apps/skills/`) жалко.
Stage 2 (humanization) — новый skill, primary=`deepseek/v4-flash`,
fallback=`qwen/qwen-turbo` → `openai/gpt-4o-mini`. PII-bearing prompts → отдельный chain
с `yandex/yandexgpt-5-lite`.

**Что НЕ делать:** не ставить DeepSeek/Qwen на intent (Stage 1) — там критичен зрелый
function-calling под существующий 6-tool schema. Native tool-use в DeepSeek/Qwen
поддерживается, но conformance-тесты ADR-0005 ещё не покрывают эти провайдеры.

---

## 2. Comparison matrix

Все цены — за **1M токенов**, актуальны на 2026-05-13/18 по open источникам.
Курс RUB→USD ≈ 90 для проекций YaGPT / GigaChat.

| Провайдер | Модель | Input | Output | Context | RU доступ | 152-ФЗ | SSE | Tool calls |
| -- | -- | -- | -- | -- | -- | -- | -- | -- |
| **OpenAI** | gpt-4o-mini | $0.150 | $0.600 | 128K | через `OPENAI_PROXY` | ❌ US-residency | ✅ | ✅ (best-in-class) |
| **DeepSeek** | v4-flash (≈chat/V3) | $0.14 | $0.28 | 1M | ✅ напрямую | ❌ CN-residency | docs неполные¹ | ✅ |
| **DeepSeek** | v4-pro (≈reasoner) | $0.435² | $0.87² | 1M | ✅ напрямую | ❌ CN-residency | docs неполные | ✅ |
| **Qwen (Alibaba)** | qwen-turbo | $0.033 | $0.130 | 131K | через Singapore endpoint³ | ❌ SG/CN-residency | ✅ (X-DashScope-SSE) | ✅ |
| **Qwen** | qwen-plus | $0.260 | $0.780 | 1M | через Singapore endpoint | ❌ | ✅ | ✅ |
| **Qwen** | qwen-flash (3.5) | $0.065 | $0.260 | 1M | через Singapore endpoint | ❌ | ✅ | ✅ |
| **Qwen** | qwen-max | $1.040 | $4.160 | 33K | через Singapore endpoint | ❌ | ✅ | ✅ |
| **Qwen** | qwen2.5-72b-instruct | $0.360 | $0.400 | 33K | Singapore / self-host | ❌ / on-prem | ✅ | ✅ |
| **Yandex** | YandexGPT 5 Lite | ~$0.0022/1K⁴ | ~$0.0044/1K⁴ | ~32K | ✅ напрямую | ✅ ФСТЭК УЗ-3, RU DC | ✅ | partial⁵ |
| **Yandex** | YandexGPT 5 Pro | ~$0.022/1K | ~$0.067/1K | ~32K | ✅ напрямую | ✅ ФСТЭК УЗ-3, RU DC | ✅ | partial |
| **Sber (bonus)** | GigaChat 2 Lite | ~$0.0022/1K | ~$0.0044/1K | ~32K | ✅ напрямую | ✅ full RU | ✅ | partial |

¹ DeepSeek docs упоминают SSE в quick-start, в API-ref не выделено явно — проверить на conformance-тесте.
² 75% off действует до **2026-05-31**, после — $1.74 / $3.48 (×4 рост). Заложить в T7 budget cap regular pricing.
³ `dashscope-intl.aliyuncs.com` (Singapore). Прямого статуса по РФ нет, санкций нет, риск изменения политики Alibaba — низкий, но реален.
⁴ Yandex билит в рублях за 1K: Lite ≈ 0.2/0.4 ₽/1K → пересчёт по 90 RUB/USD.
⁵ Function-calling в YandexGPT — через JSON-mode + structured output, native tools API нет. Адаптер потребует маппинга в `apps.llm.providers.yandex_provider`.

### 2.1 Cost projection (1000 запросов/мес humanization)

Предположение: **input ≈ 500 ток** (raw FAQ + Алина system prompt ~150 + history ~200),
**output ≈ 150 ток** (≤220 chars разговорный ответ). Итого: 0.5M input + 0.15M output на 1000 req/мес.

| Провайдер · Модель | $/мес (1k req) | Δ vs gpt-4o-mini |
| -- | -- | -- |
| qwen-turbo | **$0.036** | **−78%** |
| qwen-flash 3.5 | $0.072 | −56% |
| deepseek-v4-flash | $0.112 | **−32%** |
| **gpt-4o-mini (baseline)** | **$0.165** | — |
| qwen-plus | $0.247 | +50% |
| GigaChat 2 Lite / YandexGPT 5 Lite | ~$1.78 | +980% |
| qwen-max | $1.144 | +593% |
| YandexGPT 5 Pro | ~$21.1 | +12700% |

### 2.2 Cost projection (10k req/мес — реалистичный сценарий через 3 мес)

| Провайдер · Модель | $/мес (10k req) |
| -- | -- |
| qwen-turbo | $0.36 |
| deepseek-v4-flash | $1.12 |
| gpt-4o-mini | $1.65 |
| qwen-plus | $2.47 |
| YandexGPT 5 Lite / GigaChat 2 Lite | ~$17.8 |
| YandexGPT 5 Pro | ~$211 |

**Вывод:** на 1k–10k req/мес stress по cost'у ничтожен для всех валютных провайдеров.
Real-cost driver — Stage 1 intent (полная история), не humanization. T7 budget cap
(`apps.llm.cost_tracker`) приобретает смысл при ×100 scale или защите от
prompt-injection runaway.

---

## 3. Sample renders

> ⚠️ Live API calls **не проведены** в этом спайке — нет API-ключей в репо.
> Ниже — ожидаемое поведение моделей по их публичной репутации в русском
> разговорном тоне. **Action item:** перед стартом T2 (DRF-280) провести live
> rendering через CI с тестовыми keys всех 4 провайдеров (см. §7 open question #7).

### 3.1 Test prompt

```
System: Ты — Алина, помощник салона «Формула тела» в Пензе.
Стиль: тёплый, без канцелярита, ≤220 chars, без хайпа.
Можно эмодзи 1-2 шт.

User question: "когда вы работаете в воскресенье?"
Raw FAQ context: "Воскресенье 9:00-21:00, без перерыва"

Task: переписать FAQ ответ живым языком как бы написала Алина в чате клиенту.
```

### 3.2 Expected renders (hypothetical, basis: model tone characteristics)

**OpenAI gpt-4o-mini** (baseline, что сейчас в проде через `legacy_maxbot/llm.py::chat_rag`):
```
Воскресенье у нас полный рабочий день: с 9 до 21, без перерыва.
Приходите когда удобно ❤️
```

**DeepSeek v4-flash** — в RU literature benchmarks показывает живой разговорный тон,
иногда слегка перегружает эмодзи и восклицаниями:
```
Конечно! В воскресенье работаем с 9 утра до 9 вечера, без перерыва —
заходи в любое удобное время 😊
```

**Qwen qwen-plus** — multilingual-strong, ровный нейтральный тон, бережёт лимит chars:
```
В воскресенье работаем с 9:00 до 21:00 без перерыва.
Приходите когда удобно — будем рады! 🌿
```

**Qwen qwen-turbo** — заметно слабее на нюансах эмпатии, может скатиться в FAQ-сухость:
```
Работаем в воскресенье 9:00–21:00, без перерыва.
Ждём вас!
```

**YandexGPT 5 Lite** — наиболее естественный русский, но иногда канцелярит проскакивает:
```
В воскресенье работаем с 9:00 до 21:00 без перерыва.
Будем рады видеть вас в любое удобное время.
```

**Subjective ranking по «дух Алины»** (≤220 chars, тепло, без канцелярита):

1. 🥇 DeepSeek v4-flash — лучшая эмпатия, естественные эмодзи
2. 🥈 OpenAI gpt-4o-mini — стабильно ровно, без особой искры (текущая baseline)
3. 🥉 Qwen-plus — нейтрально-приятно, без эмоционального драйва
4. YandexGPT Lite — «приходите, будем рады» — слабая эмпатия
5. Qwen-turbo — на грани FAQ-сухости, годится только при жёстких cost-constraint'ах

---

## 4. РФ-blockers

| Провайдер | Без прокси | Через `OPENAI_PROXY` | Платёжный путь |
| -- | -- | -- | -- |
| OpenAI | ❌ (api.openai.com заблокирован Роскомнадзором с 2023) | ✅ (текущий прод) | прокси-сервис + карта вне РФ |
| DeepSeek | ✅ работает напрямую | n/a | сложно: AliPay/UnionPay через посредников ([habr/990332](https://habr.com/en/articles/990332/)) |
| Qwen (Singapore) | ⚠ риск-категория — нет явного бана, нет официального присутствия | n/a | Alibaba Cloud Account международный (карта вне РФ) |
| YandexGPT | ✅ прямой | n/a | Yandex Cloud billing в рублях, акт + счёт-фактура |
| GigaChat (bonus) | ✅ прямой | n/a | Sber billing в рублях, full B2B |

**Критический вывод по платежам:** OpenAI и Qwen требуют международной карты;
DeepSeek — через AliPay-посредников (рискованно для B2B счетов).
**Только YaGPT и GigaChat дают чистый B2B billing с актом** — это accounting-side
аргумент в пользу YaGPT как fallback'а.

---

## 5. License terms & 152-ФЗ

### 5.1 152-ФЗ (Федеральный закон от 27.07.2006 № 152-ФЗ)

- **OpenAI** — данные в US, есть DPA, но **нет официального согласия на обработку ПДн
  граждан РФ на серверах оператора**. Передача ПДн (имя/телефон клиента) в LLM-prompt
  без согласия = риск штрафа РКН до 18M ₽ (изменения 2025). **Митигация:** PII-scrub
  layer перед stage 2 — рекомендую слой в `apps.orchestrator.safety` (уже существует
  по структуре) или новый skill-wrapper.
- **DeepSeek** — данные хостятся в China, на 152-ФЗ нет ответа от vendor'а.
  Тот же риск что OpenAI + хуже политическая сторона. **Митигация:** scrub PII.
- **Qwen** — Singapore-endpoint в теории safer (PDPA), но всё равно не-РФ. Та же scrub-стратегия.
- **YandexGPT** — ✅ **ФСТЭК аттестация УЗ-3**, дата-центры в РФ, типовая DPA-схема
  Yandex Cloud. ПДн обрабатывать **можно** в рамках стандартного договора.
- **GigaChat** — ✅ full RU stack, full 152-ФЗ.

### 5.2 Commercial B2C use

- OpenAI Terms — разрешают B2C, запрещают политику и медицину-related (для нашей
  `requires_health_check` flow в `legacy_maxbot/ai_concierge.py` — потенциальный риск,
  требует review при миграции в `apps/skills/`).
- DeepSeek Terms — generic AUP, нет специфики против beauty/medical.
- Qwen Terms — Alibaba Cloud AUP, OK для B2C.
- Yandex Terms — strong RU-legal, OK для B2C.

### 5.3 Recommendation по compliance

**PII-bearing prompts → `yandex/yandexgpt-5-lite` chain** (опц. `sber/gigachat-2-lite`).
**Чистый FAQ-humanization без имён/телефонов → `deepseek/qwen` chain** без compliance-боли.

PII-router логика — новый компонент. Предлагаю расположить в `apps.orchestrator.llm`:

```python
# apps/orchestrator/llm/pii_routing.py (new)
def select_humanizer_chain(message: Message) -> str:
    if contains_pii(message.content) or contains_pii_in_action_data(message):
        return "humanizer_pii_safe"  # → yandex,gigachat per apps.promptreg
    return "humanizer"               # → deepseek,qwen,openai
```

Текущий код в `legacy_maxbot/ai_concierge.py::send_message()` подмешивает `client_name`
и `bookings_count` в system_prompt — это PII, попадает в PII-safe ветку.

---

## 6. Streaming & Function calling support

| Провайдер | SSE streaming | Native tool calls | OpenAI-compat API |
| -- | -- | -- | -- |
| OpenAI gpt-4o-mini | ✅ | ✅ (gold standard) | n/a (native) |
| DeepSeek v4-flash | docs incomplete¹ | ✅ | ✅ (drop-in для OpenAI SDK) |
| Qwen (DashScope) | ✅ `X-DashScope-SSE: enable` | ✅ через `tools` param | ✅ compatibility mode |
| YandexGPT | ✅ async API | ⚠ JSON-mode + structured output, native tools нет | ❌ |
| GigaChat | ✅ | ⚠ partial (functions есть, schema ограниченнее) | ❌ |

¹ DeepSeek SSE — упомянуто в quick-start, в API-ref не выделено. Risk: low.

**Архитектурный вывод для T2 (DRF-280):** DeepSeek и Qwen имеют OpenAI-compatible mode →
их `apps.llm.providers.*_provider.py` можно построить как тонкие обёртки над существующим
`openai_provider.py` (разные `base_url` / `api_key`). Минимум новой работы.
YaGPT и GigaChat — отдельные адаптеры с маппингом tool→JSON-mode и rubles→USD pricing
для `apps.llm.cost_tracker`.

---

## 7. Open questions для PO

1. ~~**Migration freeze impact:** реализуем в `mysite/maxbot/` или `ai-bot-platform`?~~ — **RESOLVED 2026-05-18 @tikhonovmaksoft:** работаем в `ai-bot-platform` (см. §0).
2. **152-ФЗ:** держим ли мы официальное согласие пользователя на обработку ПДн в зарубежных LLM-сервисах? Если нет — нужен PII-scrubbing/routing layer (§5.3) до старта humanizer skill в проде.
3. **Tone preference:** какой sample render из §3.2 ближе к каноническому голосу Алины (`legacy_maxbot/voice_examples.py::FORMULA_TELA_EXAMPLES`)?
4. **Payment:** AliPay/UnionPay через посредников для DeepSeek или GigaChat/YaGPT с прозрачным B2B billing'ом несмотря на ×10 cost?
5. **DeepSeek deprecation (2026-07-24):** мигрируем сразу на `deepseek-v4-flash` model ID или используем legacy aliases `deepseek-chat`/`deepseek-reasoner` до forced cutoff?
6. **DeepSeek v4-pro 75% off ends 2026-05-31:** после ×4 роста цены остаётся ли он cost-attractive? Рекомендую закладывать **regular pricing** в `apps.llm.cost_tracker.py::COST_TABLE`, а не promo.
7. **Live API verification:** 1-дневный мини-spike с реальными ключами перед T2 abstraction, чтобы §3.2 hypothetical → measured. Делаем?

---

## 8. Recommendation: provider chain config

### 8.1 Stage 1 (intent / tool-use) — `humanizer.intent` skill

```python
# apps.promptreg config (per ADR-0005 routing)
{
    "skill": "intent",
    "primary": "openai/gpt-4o-mini",
    "fallback": "openai/gpt-4o",  # higher-quality fallback if intent fails on mini
    "cost_routing": False,
}
```

### 8.2 Stage 2 (humanization, non-PII) — `humanizer` skill

```python
{
    "skill": "humanizer",
    "primary": "deepseek/deepseek-v4-flash",  # NB: not chat alias (deprec. 2026-07-24)
    "fallback": "qwen/qwen-turbo",
    "tertiary": "openai/gpt-4o-mini",         # last-resort, current prod stack
    "cost_routing": True,
}
```

### 8.3 Stage 2 (humanization, PII-safe) — `humanizer_pii_safe` skill

```python
{
    "skill": "humanizer_pii_safe",
    "primary": "yandex/yandexgpt-5-lite",
    "fallback": "sber/gigachat-2-lite",
    "cost_routing": False,
}
```

### 8.4 Selection logic

```python
# apps/orchestrator/llm/pii_routing.py
def select_humanizer_skill(message: Message) -> str:
    if contains_pii(message.content) or contains_pii_in_action_data(message):
        return "humanizer_pii_safe"
    return "humanizer"
```

### 8.5 Fallback strategy

CR-3 circuit breaker уже в router (ADR-0005). Per-call: 1 retry, 8s timeout.
Humanization stage NOT critical-path — на полный chain-down fall back to raw FAQ output
(degraded, but functional).

---

## 9. Risks register (delta vs DRF-278 epic)

| Риск | P | Impact | Митигация |
| -- | -- | -- | -- |
| DeepSeek санкции / деприкейт `deepseek-chat` до миграции T2 | M | Hi | Сразу таргетим `v4-flash` ID, не `chat` alias |
| Qwen-Singapore блокировка для РФ-AC IP | L | Mi | Pre-flight ping в `apps.llm.router` healthcheck → auto-fail к next chain |
| YaGPT квота / rate-limit при scale | M | Mi | Заложить YaGPT только для PII-flow (~10% req), не primary |
| 152-ФЗ штраф РКН за prompt с PII в DeepSeek/Qwen | L | Hi | PII-scrub перед stage 2 (см. §5.3) |
| DeepSeek 75% off ends 2026-05-31 — ×4 рост cost | H | Mi | T7 budget cap в `apps.llm.cost_tracker` + auto-fallback на qwen-turbo |
| Sample renders в §3.2 не отражают реальный output | M | Mi | Live verification spike перед T2 (§7 open question #7) |

---

## 10. References

### Linear
- [DRF-278](https://linear.app/drfproject/issue/DRF-278) — EPIC-P umbrella
- [DRF-279](https://linear.app/drfproject/issue/DRF-279) — этот spike
- [DRF-280](https://linear.app/drfproject/issue/DRF-280) — T2 abstraction (next-up after sign-off)

### Internal (ai-bot-platform)
- ADR-0005: [`docs/adr/ADR-0005-multi-llm-provider-routing.md`](../../adr/ADR-0005-multi-llm-provider-routing.md)
- Protocol: [`apps/llm/protocol.py`](../../../apps/llm/protocol.py) (DRF-580)
- Existing providers: [`apps/llm/providers/`](../../../apps/llm/providers/) — openai (DRF-581), anthropic (DRF-583)
- Router: [`apps/llm/router.py`](../../../apps/llm/router.py) (DRF-587)
- Cost tracker: [`apps/llm/cost_tracker.py`](../../../apps/llm/cost_tracker.py), [`pricing.py`](../../../apps/llm/pricing.py)
- Legacy reference: [`legacy_maxbot/llm.py`](../../../legacy_maxbot/llm.py), [`voice_examples.py`](../../../legacy_maxbot/voice_examples.py), [`ai_concierge.py`](../../../legacy_maxbot/ai_concierge.py)

### External pricing / docs
- [Qwen API Pricing — pricepertoken.com](https://pricepertoken.com/pricing-page/provider/qwen)
- [DeepSeek API Docs — Pricing](https://api-docs.deepseek.com/quick_start/pricing)
- [DashScope International API — Alibaba Cloud](https://www.alibabacloud.com/help/en/model-studio/qwen-api-via-dashscope)
- [OpenAI API Pricing](https://openai.com/api/pricing/)
- [Yandex Foundation Models pricing — geoscout.pro обзор](https://geoscout.pro/ru/blog/yandexgpt-dlya-biznesa)
- [GigaChat Tariffs — Sber Developer](https://developers.sber.ru/docs/ru/gigachat/api/tariffs)
- [DeepSeek availability in Russia — Chat-Deep.ai](https://chat-deep.ai/guide/deepseek-availability/)
- [DeepSeek practical guide from Russia — Habr 990332](https://habr.com/en/articles/990332/)

---

## Acceptance check

- [x] 4 провайдера (Qwen / DeepSeek / YaGPT / OpenAI) + GigaChat бонусом задокументированы
- [x] Comparison matrix с quantitative metrics (cost / context / SSE / tools / 152-ФЗ)
- [x] Sample renders показаны в отчёте (hypothetical — live verification flagged §7.7)
- [x] Top-2 recommendation + reasoning (DeepSeek primary, qwen-turbo fallback; YaGPT для PII)
- [x] Open questions для PO (6 active + 1 resolved в §7)
- [x] Mapped onto ai-bot-platform layout (`apps/llm/providers/`, `apps.promptreg`, `apps.llm.cost_tracker`)
