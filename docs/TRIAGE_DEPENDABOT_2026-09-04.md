# Разбор красных Dependabot-PR'ов — 04.09.2026

Разбор по факту заведения `dependabot.yml` (DRF-1396). Ничего не чинилось,
не мержилось и не закрывалось — только чтение журналов и диффов.

Данные собраны 04.09.2026 ~10:00 MSK через `gh pr view`, `gh run list --commit`,
`gh run view --log-failed`, `gh api`.

---

## 0. Две поправки к постановке задачи — до таблицы

**Поправка 1. «Все они красные» — неверно. Красный только бот.**

Три PR'а бэкенда **зелёные полностью**, и это не устаревшая запись, а текущее
состояние головы ветки:

```
PR #282  mergeable=MERGEABLE  state=CLEAN   lint pass 53s   test pass 18m25s
PR #283  mergeable=MERGEABLE  state=CLEAN   lint pass 51s   test pass 20m58s
PR #284  mergeable=MERGEABLE  state=CLEAN   lint pass 57s   test pass 19m24s
```

На каждый head-SHA бэкенда `gh run list --commit` отдаёт **ровно один** прогон,
и он `completed/success`. Устаревших записей, о которые можно споткнуться,
здесь нет вовсе:

```
33731065962  CI/CD  pull_request  completed/success  #282
33731115784  CI/CD  pull_request  completed/success  #283
33731246899  CI/CD  pull_request  completed/success  #284
```

Красное в репозитории бэкенда рядом — это прогоны на самой `dev`
(`33775950223 push dev completed/failure`, `33734641633 schedule dev
completed/failure`), то есть деплой/ночной, а не PR'ы Dependabot. Скорее всего
именно они и попались на глаза.

**Поправка 2. Потолок времени ни при чём — ни у кого.**

Красные прогоны бота падают через **2–3 минуты** после старта, а не на 60-й:

```
33796110191  ci      start=19:23:04  end=19:25:55   (2 мин 51 с)
33844049590  ci      start=06:21:53  end=06:24:47   (2 мин 54 с)
33796110038  replay  start=19:23:04  end=19:23:31   (27 с)
```

Зелёные прогоны бэкенда, наоборот, отработали 18–21 минуту и уложились. Ни
одного `cancelled`/серого среди семи разбираемых PR'ов нет. Версию «сгорело на
потолке» можно закрыть.

---

## 1. Таблица: PR — что обновляет — корзина — доказательство

### Репозиторий бота `AndreyDeveloper84/ai-bot-platform`

| PR | Что обновляет | Корзина | Цитата из журнала / диффа |
|---|---|---|---|
| **#1359** | `react-router-dom` 6.27.0 → 6.30.6 (runtime, 1 пакет) | **Общая причина** (python-джобы) **+ вторая общая причина** (miniapp) | `pytest`/`replay`: `##[error]GH_DEPLOY_TOKEN secret missing — ayla-ai-core fetch will fail.` — шаг `Configure git auth for private deps`, run `33796110191`.<br>`miniapp`: `npm error npm ci can only install packages when your package.json and package-lock.json ... are in sync` → `npm error Missing: esbuild@0.28.2 from lock file` |
| **#1360** | github-actions, 7 обновлений: `checkout` v4→v7, `setup-python` v5→v7, `setup-uv` v3→v7, `setup-node` v4→v7, `upload-artifact` v4→v7, `docker/setup-buildx-action` v3→v4, `docker/login-action` v3→v4 | **Общая причина** (единственная) | `##[error]GH_DEPLOY_TOKEN secret missing — ayla-ai-core fetch will fail.` — run `33796154325`.<br>`miniapp (typecheck + vitest)  COMPLETED  SUCCESS` — то есть `checkout@v7` и `setup-node@v7` уже отработали зелёными в этом же PR. |
| **#1361** | miniapp-dev, 3 обновления: `@testing-library/react` ^16.3.2→^16.3.3, `@testing-library/user-event` ^14.6.1→^14.6.6, `vitest` ^4.1.10→^4.1.11 | **Общая причина** (python-джобы) **+ вторая общая причина** (miniapp) | То же сообщение о `GH_DEPLOY_TOKEN` (run `33796157197`) и тот же `npm error Missing: esbuild@0.28.2 from lock file` |
| **#1362** | python-patch, 3 обновления: `django` 5.2.14→5.2.17, `pre-commit` 4.6.0→4.6.2, `python-dotenv` 1.2.2→1.2.3 | **Общая причина** (единственная) | Весь `--log-failed` — 31 строка, и последние две:<br>`##[error]GH_DEPLOY_TOKEN secret missing — ayla-ai-core fetch will fail.`<br>`##[error]Process completed with exit code 1.`<br>`miniapp  COMPLETED  SUCCESS` |
| **#1363** | python-minor, 11 обновлений: `anthropic` 0.101.0→0.125.0, `ast_serialize` 0.3.0→0.9.0, `mypy` 2.0.0→2.3.1, `ruff` 0.15.12→0.16.5, `djangorestframework` 3.17.1→3.18.0, `opentelemetry-api` 1.41.1→1.44.0, `pytest` 9.0.3→9.1.1, `pytest-django` 4.12.0→4.14.0, `pytest-asyncio` 1.3.0→1.4.0, `model-bakery` 1.23.4→1.24.0 | **Общая причина** (единственная) — но с оговоркой, см. §4 | `##[error]GH_DEPLOY_TOKEN secret missing` — run `33844107614`, `miniapp  COMPLETED  SUCCESS` |

### Репозиторий бэкенда `AndreyDeveloper84/beautygo_backend`

| PR | Что обновляет | Корзина | Цитата из журнала |
|---|---|---|---|
| **#282** | github-actions, 5 обновлений: `checkout` 4→7, `setup-python` 5→7, `cache` 4→6, `docker/setup-buildx-action` 3→4, `docker/login-action` 3→4 | **Не падает — зелёный** | `lint pass 53s` · `test pass 18m25s` · `deploy skipping` · `mergeStateStatus=CLEAN` |
| **#283** | python-patch, 4 обновления (`requirements.txt`, 4+/4−) | **Не падает — зелёный** | `lint pass 51s` · `test pass 20m58s` · `mergeStateStatus=CLEAN` |
| **#284** | python-minor, 16 обновлений: `django-unfold` 0.86.1→0.104.1, `boto3` 1.35.99→1.43.83, `sentry-sdk` 2.20.0→2.68.1, `openai` 1.99.9→1.109.1, `djangorestframework` 3.16.1→3.18.0, `drf-spectacular` 0.28.0→0.30.0, `celery` 5.5.3→5.6.3, `firebase-admin` 6.5.0→6.9.0, `pyjwt` 2.9.0→2.13.0, `requests` 2.32.3→2.34.2, `python-dotenv` 1.1.1→1.2.3, `django-celery-beat` 2.8.1→2.9.0, `pytest-django` 4.11.1→4.14.0, `pytest-cov` 6.1.1→6.3.0, `faker` 37.4.0→37.12.0, `yookassa` 3.10.0→3.12.1 | **Не падает — зелёный** | `lint pass 57s` · `test pass 19m24s` · `mergeStateStatus=CLEAN` |

**Итог по корзинам:** «по делу» (обновление ломает код) — **ни одного PR'а**.
«Шум» в смысле флейка / занятого исполнителя / потолка времени — **ни одного**.
Всё красное объясняется двумя системными причинами инфраструктуры, обе
воспроизводимы и обе не связаны с содержимым обновлений.

---

## 2. Общая причина №1 — `GH_DEPLOY_TOKEN` не выдан области Dependabot

**Это причина падения всех пяти PR'ов бота.** Формулировка прямая:

> У Dependabot своя, отдельная область секретов. В репозитории бота она **пуста**.
> Все секреты лежат в области Actions, куда прогон, запущенный Dependabot'ом,
> доступа не имеет.

Проверено напрямую:

```
BOT   actions/secrets    → DEV_DEPLOY_PATH, DEV_HOST, DEV_SSH_KEY, DEV_USER,
                           DJANGO_SECRET_KEY, GH_DEPLOY_TOKEN, OPENAI_API_KEY
BOT   dependabot/secrets → (пусто)
```

В журнале это видно двумя строками. Сначала подтверждение, откуда берутся
секреты:

```
Secret source: Dependabot
GITHUB_TOKEN Permissions:  Contents: read   Metadata: read   Packages: read
```

Затем — пустое значение и срабатывание стража в
`.github/workflows/ci.yml`, шаг `Configure git auth for private deps`:

```
env:
  GH_DEPLOY_TOKEN:                                    ← значение пустое
##[error]GH_DEPLOY_TOKEN secret missing — ayla-ai-core fetch will fail.
##[error]Process completed with exit code 1.
```

Шаг устроен так (цитата из `ci.yml:320-329`):

```yaml
- name: Configure git auth for private deps
  env:
    GH_DEPLOY_TOKEN: ${{ secrets.GH_DEPLOY_TOKEN }}
  run: |
    if [ -n "$GH_DEPLOY_TOKEN" ]; then
      git config --global url."https://x-access-token:${GH_DEPLOY_TOKEN}@github.com/".insteadOf "https://github.com/"
    else
      echo "::error::GH_DEPLOY_TOKEN secret missing — ayla-ai-core fetch will fail. Set the secret in repo settings."
      exit 1
    fi
```

Страж отработал ровно как задуман — упал быстро и с внятным текстом вместо
того, чтобы двадцать минут спустя выдать невнятную ошибку `uv sync`. Претензий
к нему нет; отсутствует именно секрет.

**Контроль, доказывающий, что дело в области Dependabot, а не в самом секрете.**
Обычный человеческий PR того же периода в том же репозитории проходит целиком:

```
PR #1358 (feat/drf1454-scanner-memory)
  pytest + ruff + mypy   COMPLETED/SUCCESS
  replay fixtures        COMPLETED/SUCCESS
  miniapp                COMPLETED/SUCCESS
```

и `dev` зелёная (`33843724363 push dev completed/success`). То есть секрет на
месте и работает — он просто не виден прогонам Dependabot.

### Что нужно, чтобы её снять

Один шаг, без правки кода:

> **Settings → Secrets and variables → Dependabot → New repository secret**
> в `AndreyDeveloper84/ai-bot-platform`: имя `GH_DEPLOY_TOKEN`, значение —
> тот же PAT, что уже лежит в области Actions.

Через CLI (значение не печатать, подставить из безопасного источника):

```
gh secret set GH_DEPLOY_TOKEN --app dependabot --repo AndreyDeveloper84/ai-bot-platform
```

После этого достаточно перезапустить прогоны (`@dependabot recreate` в
комментарии к PR либо `gh run rerun`), новый коммит не нужен.

### Почему бэкенд не упал, хотя у него **та же** дыра

Область Dependabot пуста и в бэкенде тоже:

```
BACKEND  actions/secrets    → GH_DEPLOY_TOKEN, SENTRY_DSN,
                              TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
BACKEND  dependabot/secrets → (пусто)
```

Разница только в том, что у бэкенда **нет стража**. Его шаг
(`ci.yml:103-112`) переписывает URL безусловно:

```yaml
- name: Configure git auth for private dependency
  env:
    GH_DEPLOY_TOKEN: ${{ secrets.GH_DEPLOY_TOKEN }}
  run: |
    git config --global url."https://${GH_DEPLOY_TOKEN}@github.com/".insteadOf "https://github.com/"
```

С пустым токеном получается перезапись на `https://@github.com/` — и клон
всё равно проходит. Из журнала бэкенда, run `33731246899`:

```
Collecting ayla-ai-core @ git+https://github.com/AndreyDeveloper84/ayla-ai-core.git@d72a5de...
  Running command git clone --filter=blob:none --quiet https://github.com/AndreyDeveloper84/ayla-ai-core.git ...
  Resolved https://github.com/AndreyDeveloper84/ayla-ai-core.git to commit d72a5de451f985d118d9449d2b17ce51bf0a6e25
  Created wheel for ayla-ai-core: filename=ayla_ai_core-0.9.0-py3-none-any.whl
```

Анонимный клон приватного репозитория пройти не может. Значит, репозиторий
не приватный — см. §5, это отдельная находка, и она важнее самого разбора.

---

## 3. Общая причина №2 — Dependabot не умеет пересобрать lock мини-аппа

**Это причина падения джобы `miniapp` в #1359 и #1361** — и только в них, там,
где Dependabot трогает `package-lock.json`. В #1360/#1362/#1363 lock не
затрагивается, и `miniapp` там `SUCCESS`.

Симптом:

```
npm error code EUSAGE
npm error npm ci can only install packages when your package.json and
          package-lock.json or npm-shrinkwrap.json are in sync.
          Please update your lock file with `npm install` before continuing.
npm error Missing: esbuild@0.28.2 from lock file
npm error Missing: @esbuild/aix-ppc64@0.28.2 from lock file
   ... (ещё 23 платформенных бинаря @esbuild/*@0.28.2)
```

Причина — в форме дерева зависимостей. В мини-аппе **два мажора vite
одновременно**: `vite ^5.4.10` для сборки приложения и восьмой vite, который
npm ставит вложенно, чтобы удовлетворить peer-требование `vitest ^4.1.10`.
Дерево на `dev` (разбор `package-lock.json@dev`, lockfileVersion 3):

```
node_modules/esbuild                     → 0.21.5   dev=True
node_modules/vite                        → 5.4.21   dev=True
node_modules/vitest                      → 4.1.10   dev=True
node_modules/vitest/node_modules/vite    → 8.1.5    dev=True
node_modules/vitest/node_modules/esbuild → 0.28.2   peer=True  dev=True
```

Во вложенном поддереве `node_modules/vitest/node_modules/*` на `dev` —
**26 записей** (vite 8.1.5, esbuild 0.28.2 и 24 платформенных `@esbuild/*`).

Dependabot, пересобирая lock, это поддерево **срезает**. На головах его веток
остаётся **2 записи**:

```
#1359  nested → ['node_modules/vitest/node_modules/@vitest/mocker',
                 'node_modules/vitest/node_modules/vite']      (vite 8.1.5)
#1361  nested → ['node_modules/vitest/node_modules/@vitest/mocker',
                 'node_modules/vitest/node_modules/vite']      (vite 8.2.2)
```

Отсюда и масштаб диффа, несоразмерный обновлению: в #1359 при подъёме одного
`react-router-dom` на патч дифф lock'а — **13+ / 525−**. В самом диффе видно,
что удаляются именно peer-записи:

```
-    "node_modules/@esbuild/netbsd-arm64": {
-      "version": "0.28.2",
-      "peer": true,
```

`npm ci` в CI пересчитывает идеальное дерево из `package.json`, снова требует
`esbuild@0.28.2` для вложенного vite — и не находит его в lock'е. Отсюда EUSAGE.

**Корзина.** Это не «обновление ломает код»: ни react-router 6.30.6, ни
vitest 4.1.11 ни к чему не притрагиваются в исходниках. Но и не флейк —
воспроизводится детерминированно и будет повторяться на **каждом** будущем
npm-PR'е, пока в дереве два vite.

### Что нужно, чтобы её снять

- **Быстро, на каждый PR:** человеку выполнить `cd apps/miniapp && npm install`
  на ветке Dependabot и закоммитить перерождённый lock. Проверка: во вложенном
  поддереве должно снова стать 26 записей, а не 2.
- **По-настоящему, один раз:** свести дерево к одному vite — поднять
  приложение с `vite ^5.4.10` до восьмого мажора (вместе с
  `@vitejs/plugin-react`). Тогда вложенное поддерево исчезает целиком и
  пересборка lock'а перестаёт быть хрупкой. Мажоры Dependabot'ом исключены
  (`ignore: version-update:semver-major`), так что это осознанная ручная
  задача — просится в Linear отдельным пунктом.

---

## 4. Что можно сливать как есть, что править, что закрыть

**Закрывать не нужно ничего.** Все семь PR'ов содержательно осмысленны.

### Сливать как есть — после проверки, три PR'а бэкенда

| PR | Основание |
|---|---|
| **#282** github-actions ×5 | Полностью зелёный, `CLEAN`. Обновляет в том числе `checkout` 4→7 и `setup-python` 5→7 — оба мажорных скачка прошли `lint` и 18-минутный `test`. |
| **#283** python-patch ×4 | Полностью зелёный, `CLEAN`, дифф 4+/4− в `requirements.txt`. |
| **#284** python-minor ×16 | Полностью зелёный, `CLEAN`, `test pass 19m24s`. Но внутри крупные скачки — `django-unfold` 0.86.1→**0.104.1**, `sentry-sdk` 2.20→**2.68**, `boto3` 1.35.99→**1.43.83**, `openai` 1.99.9→**1.109.1**. Зелёный CI накрывает это лишь настолько, насколько накрывают тесты; мерж в `dev` = выкладка на боевой пилот (так и записано в самом `dependabot.yml` бэкенда). Рекомендация: слить **после** #283, отдельным заходом и с наблюдением за пилотом, а не в одной пачке. |

### Разблокировать выдачей секрета — три PR'а бота

**#1360**, **#1362**, **#1363** упираются **только** в `GH_DEPLOY_TOKEN`.
Никакого суждения об их содержимом сделать пока нельзя — CI до кода не дошёл.
После выдачи секрета ожидания такие:

- **#1362** (django 5.2.14→5.2.17, pre-commit, python-dotenv) — патчи, риск
  минимальный, вероятнее всего сразу зелёный.
- **#1360** (github-actions ×7) — четыре из семи обновлений уже доказаны
  зелёными: `checkout@v7`, `setup-python@v7`, `docker/setup-buildx-action@v4`
  и `docker/login-action@v4` прошли CI бэкенда в #282; `setup-node@v7` прошёл
  джобу `miniapp` в самом #1360. Непроверенными остаются **`astral-sh/setup-uv`
  v3→v7** и **`actions/upload-artifact` v4→v7** — оба мажорные, оба стоит
  посмотреть глазами в первом же прогоне.
- **#1363** (python-minor ×11) — **единственный кандидат, который может
  оказаться «по делу»**, и это надо сказать честно, а не выдать за просто
  разблокированный. Внутри `ruff` 0.15.12→**0.16.5** и `mypy` 2.0.0→**2.3.1**:
  новые правила ruff и новые проверки mypy штатно делают ранее чистый код
  красным, а джоба так и называется — `pytest + ruff + mypy`. Плюс `anthropic`
  0.101.0→**0.125.0** (24 минорные версии) и `ast_serialize` 0.3.0→**0.9.0** —
  последняя особенно интересна на фоне шести AST-стражей репозитория. Доказать
  сейчас нечем: прогон умер на третьей минуте, до `ruff check` дело не дошло.
  **Вывод: #1363 разбирать повторно после выдачи секрета — он с большой
  вероятностью потребует правок в коде, и это будут законные правки.**

### Требуют правки в коде — два PR'а бота

**#1359** и **#1361** — помимо секрета, нужен перерождённый `package-lock.json`
(§3). Обновления в них безобидные (патч react-router, патчи testing-library
и vitest), но зелёными сами по себе они не станут.

### Рекомендованный порядок

1. Выдать `GH_DEPLOY_TOKEN` в область Dependabot бота → перезапустить пять PR'ов.
2. Слить #283, затем #282 (бэкенд).
3. Разобрать #1362 и #1360 по свежим журналам.
4. #284 — отдельным заходом с наблюдением за пилотом.
5. #1363 — разобрать заново, ждать законных правок под ruff/mypy.
6. #1359 и #1361 — перерождённый lock; параллельно завести задачу на сведение
   vite к одному мажору.

---

## 5. Отдельно и вне задачи: `ayla-ai-core` — публичный репозиторий

Находка попутная, но по весу важнее всего остального в этом разборе.

Оба репозитория описывают `ayla-ai-core` как приватный. В `pyproject.toml`
бота: «*ayla-ai-core is a separate private repo. Pulling it requires GitHub…*».
В `requirements.txt` бэкенда: «*Repo: https://github.com/AndreyDeveloper84/ayla-ai-core (private)*».

**Фактически репозиторий публичный.** Анонимный запрос, без какой-либо
авторизации:

```
$ curl -s -o /dev/null -w "%{http_code}" https://api.github.com/repos/AndreyDeveloper84/ayla-ai-core
200
{'full_name': 'AndreyDeveloper84/ayla-ai-core', 'private': False, 'visibility': 'public'}
```

Это же подтверждается поведением CI бэкенда: анонимный `git clone` прошёл
и собрал колесо `ayla_ai_core-0.9.0-py3-none-any.whl` (§2).

Следствия, которые стоит развести:

1. **Вопрос владельцу: так задумано?** Если репозиторий открыли намеренно —
   вся машинерия `GH_DEPLOY_TOKEN` в обоих репозиториях лишняя, и её стоит
   убрать осознанно, а не оставлять как обманывающий комментарий.
   Если ненамеренно — это открытое ядро оркестрации, и разбираться надо не
   с Dependabot'ом.
2. **На разбор это не влияет.** Даже при публичном `ayla-ai-core` страж бота
   валит сборку по факту пустой переменной, а не по факту недоступности репо.
   Выдача секрета в область Dependabot чинит бота при любом ответе на п.1 —
   поэтому рекомендация из §2 остаётся в силе и не ждёт этого решения.
3. Комментарии в `pyproject.toml` (бот) и `requirements.txt` (бэкенд), а также
   комментарий шага `Configure git auth for private deps`, сейчас утверждают
   неправду. Их надо привести в соответствие с тем, что решит владелец.

Просится в реестр открытых решений (`docs/OPEN_DECISIONS.md` / DRF-1349).

---

## 6. Не пора ли настроить Dependabot иначе

Короткий ответ: **сам по себе конфиг хороший, и менять частоту не надо.**
Шума он не создаёт — пять PR'ов в первую неделю ровно так и посчитаны
в комментарии к `dependabot.yml`, и это попадание, а не промах. Всё красное
пришло не от Dependabot'а, а от двух дыр в контуре, которые он **обнаружил**.
В этом смысле он окупился в первый же день.

Что всё же стоит подправить:

**а) Мини-апп — не трогать частоту, починить дерево.**
Ежемесячный ритм для npm выбран верно. Но пока в дереве два vite, **каждый**
npm-PR будет рождаться красным (§3). Либо чинить дерево, либо, до тех пор,
временно снять npm-экосистему — иначе раз в месяц будет прилетать PR,
заведомо требующий ручной работы. Чинить дерево лучше.

**б) `github-actions` — сгруппировано слишком крупно.**
Одна группа `patterns: ["*"]` собрала **7 мажорных скачков в один PR** (#1360),
включая `upload-artifact` v4→v7 и `setup-uv` v3→v7. Если сломается хоть один,
красным станет весь PR, и придётся руками его расщеплять. Предложение —
разделить по риску:

```yaml
groups:
  github-actions-official:      # actions/* — предсказуемые
    patterns: ["actions/*"]
  github-actions-third-party:   # docker/*, astral-sh/*, appleboy/* — рисковые
    patterns: ["*"]
    exclude-patterns: ["actions/*"]
```

Заодно это снимет странность, которую видно уже сейчас: `ignore` на мажоры
задан для `uv` и `npm`, но для `github-actions` **не задан** — поэтому мажоры
экшенов приезжают автоматически, а мажоры библиотек нет. Либо распространить
`ignore` на экшены (последовательно), либо оставить как есть, но осознанно.

**в) Секреты Dependabot — в чек-лист заведения.**
Дыра, найденная сегодня, симметрична в обоих репозиториях: `dependabot/secrets`
пуст и там, и там. Бэкенду повезло из-за §5. Стоит записать правилом: любой
секрет, нужный CI на PR, заводится **в обе** области.

**г) Умолчальная ветка.**
`dependabot.yml` обоих репозиториев честно пишет, что при заданном
`target-branch` GitHub не создаёт security-обновления, и что настоящее
лекарство — сделать `dev` умолчальной веткой. Это по-прежнему открытое
решение владельца, и сегодняшний разбор в нём ничего не меняет — только
подтверждает, что версионные обновления работают и без этого.

---

## Приложение. Временные файлы

Все временные файлы складывались в
`C:\Users\user\AppData\Local\Temp\claude\C--Users-user-PycharmProjects-Ayla\44a879b5-ed16-4a66-ae5c-5972d5fefe27\scratchpad`
и **удалены** по завершении разбора. Снято:

- `logs/bot-33796110038.log`, `logs/bot-33796110191.log` (PR #1359)
- `logs/bot-33796154325.log`, `logs/bot-33796154401.log` (PR #1360)
- `logs/bot-33796157197.log`, `logs/bot-33796157298.log` (PR #1361)
- `logs/bot-33844049590.log`, `logs/bot-33844049619.log` (PR #1362)
- `logs/bot-33844107614.log`, `logs/bot-33844108683.log` (PR #1363)
- `logs/be-33731246899.log` (PR #284 бэкенда, 31 242 строки)
- `dev-lock.json`, `lock-1359.json`, `lock-1361.json` (разбор дерева мини-аппа)

Рабочих деревьев не заводилось. Ничего не мержилось, не закрывалось,
не правилось; в репозитории записи не производились.
