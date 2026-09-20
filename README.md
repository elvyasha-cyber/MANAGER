# ИИ-сервис первичной обработки обращений (с журналом и развёртыванием)

- **Проблема:** хаос в обращениях и нагрузка на менеджеров.
- **Решение:** программный интерфейс + языковая модель + журналирование + развёртывание + эскалация при сбоях.
- **Результат:** быстрее реакция, ниже нагрузка, прозрачная история.

# Support Triage API

Сервис первичной сортировки обращений интернет-магазина: классификация, черновик ответа, аудит в SQLite.

Обращения приходят обычным текстом. Живые почта, форма и чат не подключаются.

OpenAI вызывается через [ProxyAPI](https://proxyapi.ru/docs/overview): тот же OpenAI SDK, другой `base_url` и ключ ProxyAPI.

## Стек

- Python 3.10+
- FastAPI
- OpenAI SDK → ProxyAPI (`https://api.proxyapi.ru/v1`)
- SQLite
- Docker

## Контракт

`POST /triage`

Вход:

```json
{
  "text": "строка 1–2000 символов",
  "channel": "email",
  "client_id": "user-123"
}
```

`channel` — метка источника (`email` / `form` / `chat` или другая короткая строка), не реальный канал.

Выход:

```json
{
  "category": "billing",
  "draft_reply": "…",
  "confidence": "high",
  "escalate": false
}
```

- `category`: `billing` | `support` | `complaint` | `other`
- `draft_reply`: 1–6 предложений, только по входному тексту
- `confidence`: `high` | `medium` | `low`
- `escalate`: `true`, если мало данных, низкая уверенность, ошибка модели или сломанный JSON

Дополнительно: `GET /health` → `{"status":"ok"}`.

При ошибке LLM ответ всё равно 200: `escalate=true`, `draft_reply` = `Ваше обращение передано оператору.`, причина пишется в `tickets.error`.

Форма для менеджера: http://127.0.0.1:8000 (`GET /`).

## Поведение

1. Черновик не обещает фактов, которых нет во входном `text`.
2. Короткий текст вроде «Помогите» → `confidence=low`, `escalate=true`.
3. Сбой ProxyAPI/OpenAI или невалидный JSON модели → шаблон оператору, запись `error`.
4. Не больше `RATE_LIMIT_PER_MINUTE` запросов в минуту на один `client_id` (иначе 429).
5. Temperature модели: `0.2`.

## Локальный запуск

1. Python 3.10+ и ключ с [proxyapi.ru](https://proxyapi.ru/).
2. Создайте окружение и зависимости:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

3. В `.env` укажите ключ ProxyAPI:

```env
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.proxyapi.ru/v1
OPENAI_MODEL=gpt-4o-mini
RATE_LIMIT_PER_MINUTE=10
SQLITE_PATH=data/tickets.db
```

`OPENAI_API_KEY` здесь — ключ ProxyAPI, не ключ platform.openai.com. Можно вместо этого задать `PROXY_API_KEY`.

4. Запуск:

```powershell
uvicorn app.main:app --reload --port 8000
```

5. Проверка:

```powershell
curl http://localhost:8000/health
curl -X POST http://localhost:8000/triage -H "Content-Type: application/json" -d "{\"text\":\"С карты списали оплату, чек не пришёл.\",\"channel\":\"email\",\"client_id\":\"demo-1\"}"
```

Либо откройте `examples.http` в REST Client / используйте Postman: `POST http://localhost:8000/triage`.

Аудит:

```powershell
sqlite3 data/tickets.db "SELECT id, client_name, client_email, text, category, draft_reply FROM tickets;"
sqlite3 data/tickets.db "SELECT * FROM clients;"
```

Без `sqlite3` тот же файл открывается в DB Browser for SQLite или DBeaver: `data/tickets.db`.

### Docker (локально)

На компьютере должен быть запущен Docker Desktop. Порт 8000 не должен быть занят другим процессом (например `uvicorn`).

```powershell
copy .env.example .env
# заполните OPENAI_API_KEY
docker compose up --build
```

Сервис: http://localhost:8000  
Остановка: Ctrl+C в том же терминале.

Образ собирается из `Dockerfile` на вашей машине. В Docker Hub он сам не публикуется.

## Развёртывание на сервере

На сервере крутится **тот же** Docker-образ, что и локально. Код берётся из GitHub, ключ ProxyAPI задаётся секретом окружения и в репозиторий не попадает.

База SQLite на сервере — отдельный файл, не ваш `D:\MANAGER\data\tickets.db`. На бесплатном хостинге после сна или редеплоя журнал может обнулиться — для демо это нормально.

Проверка после деплоя:

```bash
curl https://ВАШ-ХОСТ/health
curl -X POST https://ВАШ-ХОСТ/triage -H "Content-Type: application/json" -d "{\"text\":\"С карты списали оплату, чек не пришёл.\",\"channel\":\"email\",\"client_id\":\"demo-1\"}"
```

Форма: `https://ВАШ-ХОСТ/`

### Вариант 1. Render (готовый `render.yaml`)

1. Аккаунт на [render.com](https://render.com), репозиторий уже на GitHub: https://github.com/elvyasha-cyber/MANAGER
2. Dashboard → **New** → **Web Service** → подключить этот репозиторий.
3. Runtime: **Docker**. Render подхватит `Dockerfile` и `render.yaml` (сервис `support-triage`, проверка `GET /health`).
4. Environment → добавьте секрет `OPENAI_API_KEY` (ключ с proxyapi.ru). Остальные переменные из `render.yaml` подставятся сами: `OPENAI_BASE_URL`, `OPENAI_MODEL`, `RATE_LIMIT_PER_MINUTE`, `SQLITE_PATH=/data/tickets.db`.
5. **Create Web Service** / **Deploy**. Когда статус Live, откройте выданный URL.

То же можно сделать на Railway: New Project → Deploy from GitHub → Dockerfile, секрет `OPENAI_API_KEY`.

### Вариант 2. Свой сервер (VPS) и Docker Compose

Нужны: Linux-сервер с Docker и Docker Compose, открытый порт 8000 (или 80/443, если поставите прокси).

```bash
git clone https://github.com/elvyasha-cyber/MANAGER.git
cd MANAGER
cp .env.example .env
```

В `.env` на сервере укажите ключ (файл на сервере, не в git):

```env
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.proxyapi.ru/v1
OPENAI_MODEL=gpt-4o-mini
RATE_LIMIT_PER_MINUTE=10
SQLITE_PATH=/data/tickets.db
```

Запуск в фоне:

```bash
docker compose up -d --build
```

Сайт: `http://IP-СЕРВЕРА:8000`

Логи: `docker compose logs -f`  
Остановка: `docker compose down` (том `./data` с базой останется на диске).

Чтобы открыть по домену и HTTPS, поставьте nginx или Caddy перед контейнером (прокси на `127.0.0.1:8000`). Образ в Docker Hub для этого не обязателен: сервер сам собирает его из `Dockerfile`.

### Тесты без живой модели

```powershell
pytest -q
```

Покрывают контракт, валидацию, лимит, fallback при поломке LLM и запись в SQLite.

## Зависимости

- Python 3.10+ (локальный запуск без Docker)
- Docker Desktop (запуск из `Dockerfile`)
- Ключ [ProxyAPI](https://proxyapi.ru/)
- По желанию: DBeaver — просмотр `data/tickets.db`

Пакеты Python (файл `requirements.txt`): FastAPI, Uvicorn, OpenAI SDK, pydantic-settings, python-dotenv, httpx, pytest.
