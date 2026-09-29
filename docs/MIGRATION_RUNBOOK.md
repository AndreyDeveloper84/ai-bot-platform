# Порядок переезда пилота

Новая машина: Ubuntu 22.04 LTS, 2 ядра, **8 ГБ**, **80 ГБ NVMe**.
Старая: `194.87.99.126`, пользователь `taximeter`.

Копии для восстановления: `PycharmProjects/Ayla/migration-backup` (проверены на целостность).
Коммиты пилота: бот **`7e61ee6`**, бэкенд **`c4efa64`**.

---

## 0. Прежде всего — прокси

**Легко забыть, ломает всё.** Прокси на `162.120.16.233:8888` пускает **только** старый адрес `194.87.99.126`. Новая машина без правки не сможет:

- ходить к Anthropic (бот онемеет),
- скачивать код с GitHub (на канале режется загрузка объектов, лечится этим прокси).

На `162.120.16.233`, файл `/etc/tinyproxy/tinyproxy.conf`:

```
Allow 194.87.99.126        # старая, снять после переезда
Allow <НОВЫЙ_АДРЕС>
```

затем `systemctl restart tinyproxy`. Проверить **с обеих машин**:

```
curl -sS --proxy http://162.120.16.233:8888 https://api.ipify.org   # → 162.120.16.233
```

и **отрицательную сторону** — с постороннего адреса должен быть отказ 403.

---

## 1. Основа (~1 ч)

```bash
apt-get update && apt-get install -y docker.io docker-compose-plugin git nginx certbot python3-certbot-nginx
useradd -m -s /bin/bash taximeter && usermod -aG docker taximeter
mkdir -p /home/taximeter/{ai-bot-platform-dev,beautygo}
```

Пользователя завести **тем же именем** — пути зашиты в compose-файлы и в выкладку.

SSH-ключ владельца (`~/.ssh/id_ed25519.pub`) в `/root/.ssh/authorized_keys` и в `/home/taximeter/.ssh/authorized_keys`. Пароль отключить **только после** проверки входа по ключу — иначе запрёмся снаружи.

## 2. Код (~15 мин)

```bash
su - taximeter
git clone https://github.com/AndreyDeveloper84/ai-bot-platform.git ai-bot-platform-dev
cd ai-bot-platform-dev && git checkout 7e61ee6
git clone https://github.com/AndreyDeveloper84/beautygo_backend.git beautygo/dev
cd beautygo/dev && git checkout c4efa64
```

Если загрузка рвётся — через прокси: `git -c http.proxy=http://162.120.16.233:8888 clone …`

**Ветка по умолчанию сменена на `dev` (03.09)** — клон придёт сразу на неё.

## 3. Настройки (~1 ч, спешить нельзя)

Распаковать `envs.tgz` в `ai-bot-platform-dev/`, `envs-be.tgz` в `beautygo/dev/`.

Проверить наличие, **не печатая значений**:

```bash
for k in ANTHROPIC_API_KEY ANTHROPIC_PROXY LLM_PROVIDER DJANGO_ALLOWED_HOSTS GH_DEPLOY_TOKEN; do
  echo "$k: $(grep -c "^$k=" .env.staging)"
done
```

`GH_DEPLOY_TOKEN` нужен сборке — без него образ не соберётся.

## 4. Данные (минуты — базы крошечные)

Поднять **только** хранилища, дождаться готовности, восстановить:

```bash
docker compose -p ayla-bot-staging -f docker-compose.yml -f docker-compose.staging.yml -f docker-compose.staging.local.yml up -d postgres redis minio
docker exec -i ayla-bot-staging-postgres-1 psql -U $POSTGRES_USER < bot.sql

cd ~/beautygo/dev
docker compose -p dev -f docker-compose.yml up -d db redis minio
docker exec -i dev-db-1 psql -U beautygo < backend.sql
```

**Проверить восстановление содержимым**, а не кодом возврата: число таблиц (80 и 79), число мастеров, число записей — сверить со старой машиной.

## 5. Сборка (10–15 мин на NVMe против 3.5 ч сейчас)

```bash
docker compose -p ayla-bot-staging -f docker-compose.yml -f docker-compose.staging.yml -f docker-compose.staging.local.yml up -d --build
cd ~/beautygo/dev && docker compose -p dev up -d --build
```

**Имя проекта указывать явно** (`-p`) — иначе compose возьмёт имя каталога и уйдёт в чужой стек.

## 6. nginx и сертификаты (~1.5 ч)

Распаковать `nginx.tgz` в `/etc/nginx/sites-available/`, включить три домена, выпустить сертификаты заново:

```bash
certbot --nginx -d api-dev.gobeauty.site -d miniapp-dev.gobeauty.site -d proapp.gobeauty.site
```

Старые сертификаты не переносим — выпустить проще и надёжнее.

`miniapp-dev` раздаёт `apps/miniapp/dist` **из рабочей копии**, а не из контейнера. Значит приложение надо собрать:

```bash
export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"; nvm use 22
cd ~/ai-bot-platform-dev/apps/miniapp && npm ci && npm run build
```

## 7. Проверка ДО переключения DNS (~1 ч)

Обращаться по адресу, подменяя имя заголовком:

```bash
curl -H 'Host: api-dev.gobeauty.site' http://<НОВЫЙ_АДРЕС>/api/v1/health/ready/   # → 200
```

**Живой вызов модели боевым путём** — не прямой к провайдеру: на этом уже обжигались, прямой вызов зелен на сломанном коде.

Проверить **внутри процессов**, а не в файлах:

```bash
docker exec ayla-bot-staging-worker-1 printenv LLM_PROVIDER ANTHROPIC_PROXY DJANGO_ALLOWED_HOSTS
```

## 8. Переключение (минуты + распространение)

A-записи трёх доменов на новый адрес. **Старая машина остаётся поднятой** — откат это возврат DNS, полчаса.

Секрет `VPS_HOST` в обоих репозиториях GitHub — на новый адрес, иначе автовыкладка бэкенда пойдёт на старую машину.

## 9. После подтверждения

- снять `Allow 194.87.99.126` с прокси;
- погасить старые контейнеры (не удалять неделю);
- сменить пароль root и ключ Anthropic — они были в переписке.

---

## Что чинится самим переездом

```
пересборка образа        3.5 ч → 10–15 мин
первый ответ бота        57 с → ожидаемо ~7 с
загрузка кода            рвётся → обычная
первый запрос к бэкенду  20 с → доли секунды
```

## Чего переезд НЕ чинит

Шесть готовых PR всё равно надо смержить и выложить. Переменная `INTENT_RESOLUTION_FROM_TOOL_CHOICE_ENABLED` (минус 4–18 с на ход) — задать отдельно.
