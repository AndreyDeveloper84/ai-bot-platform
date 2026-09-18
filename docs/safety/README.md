# Ayla S1 clinical safety review package — индекс

Документальная публикация пакета для независимой pre-validation медицинской safety-модели Ayla S1. Источник — рабочее пространство `Ayla/docs` (локальный git, ветка `safety/physician-review-package-2026-09-17`, commit `8432550`); файлы перенесены **байт в байт**, SHA-256 — в описании PR. Смысл, owner rulings и clinical status не менялись.

| Файл здесь | Что это | В `Ayla/docs` лежал как |
|---|---|---|
| `docs/safety/F0-C3-safety-matrix.md` | Safety Matrix v0.12-reviewfix1 — `WORKING DRAFT` | `docs/safety/F0-C3-safety-matrix.md` |
| `docs/safety/reviews/AYLA_CLINICAL_SAFETY_REVIEW_PACK_v0.1_2026-09-16.md` | Clinical Review Pack v0.1-reviewfix1 — интерфейс врача | `docs/AYLA_CLINICAL_SAFETY_REVIEW_PACK_v0.1_2026-09-16.md` (корень) |
| `docs/safety/reviews/S1_CLINICAL_DETECTOR_FIXTURES_v0.1.md` | 35 базовых fixtures (contract layer над `apps/skills/health_screening/tests/s1_fixtures.py`) | тот же путь |
| `docs/safety/reviews/S1_CONTEXT_RECHECK_ADVERSARIAL_FIXTURES_v0.2.md` | 69 контекстных / многоходовых fixtures (v0.2-reviewfix1) | тот же путь |
| `docs/safety/reviews/OWNER_RULINGS_OD-SAF-11-22_IMMUTABLE_RECORD.md` | immutable owner record OD-SAF-11…22 — не редактируется ([OD-BOT §158]) | тот же путь |
| `docs/safety/reviews/WAVE1_OWNER_DECISIONS_F0-C3.md` | Wave 1 owner decision pack — PARTIALLY RESOLVED | тот же путь |
| `docs/Q1.md` | слово владельца 16.09.2026 — источник [OD-BOT §156] (стр. 16) и [OD-BOT §157] (стр. 10–14) | `docs/Q1.md` |
| `docs/safety/reviews/OWNER_RULINGS_S1_AI_CLINICAL_PRE_REVIEW_2026-09-18.md` | immutable owner record 18.09 — пять решений по AI clinical pre-review + question contracts; `DO NOT EDIT — SUPERSEDE WITH A NEW RECORD`; RECORD SHA-256 `230236a92b8c4e2e562874409e73b0e5e13a318e469d6b13a57da9d7ea34b8b4` ([OD-BOT §159–§165]) | — (создан в этом репозитории) |
| `docs/safety/reviews/AYLA_S1_AI_CLINICAL_PRE_REVIEW_DELTA_v0.1_2026-09-18.md` | рабочий delta-документ: решения → границы, fixture delta v0.1 → v0.1.1 / v0.2 → v0.2.1, supporting evidence, engineering follow-ups | — |

Реестр решений владельца — `docs/OPEN_DECISIONS.md` (ключ `[OD-BOT §N]` в документах; §154–§158 — safety). Ссылки внутри пакета на `Ayla/docs/OPEN_DECISIONS.md` описывают устаревшую копию реестра в другом рабочем пространстве и намеренно не переписаны. `docs/CURRENT_DECISIONS_2026-09-16.md` в пакет не включён (операционный документ с инфраструктурными деталями); его safety-строки дословно воспроизведены в §156 / §157.

**Версии после owner delta 18.09 (PR docs/safety-s1-ai-clinical-pre-review-delta-2026-09-18):** матрица v0.12-reviewfix2; Review Pack v0.1-reviewfix2; fixtures v0.1.1 (35) и v0.2.1 (69); опубликованные PR #1829 SHA этих файлов больше не актуальны — old → new SHA в описании PR и в шапках файлов. Решения владельца 18.09 — `OWNER APPROVED — PENDING PHYSICIAN CONFIRMATION`, `AI CLINICAL PRE-REVIEW INCORPORATED`.

**Статус.** Physician sign-off отсутствует: clinical status всех 104 fixtures — `PENDING_CLINICAL_EXPERT`. Публикация пакета **не означает** `CLINICAL APPROVED`, `PHYSICIAN PASS` или `SAFE FOR PILOT`; Safety Matrix остаётся `WORKING DRAFT`; end-to-end S1 runtime — NOT PASS; Controlled Pilot S1 gate — NOT READY.
