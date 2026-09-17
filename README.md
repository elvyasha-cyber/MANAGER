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

Без `sqlite3` тот же файл открывается в DB Browser for SQLite.

### Docker

```powershell
copy .env.example .env
# заполните OPENAI_API_KEY
docker compose up --build
```

Сервис: http://localhost:8000

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

## Демонстрация (путь B)

Выбран локальный показ без публичного сервера.

Полный сценарий съёмки и список скриншотов: [demo/README.md](demo/README.md).

Кратко:

1. `docker compose up --build` или `uvicorn app.main:app --port 8000`
2. Браузер http://127.0.0.1:8000 → обращение → тип и черновик
3. DBeaver: `D:\MANAGER\data\tickets.db` → новая строка в `tickets`

В репозиторий: видео `demo/triage-demo.mp4` и скрины `demo/screen-*.png`.

## Развёртывание (путь A, не используется)

Позже можно выложить тот же `Dockerfile` на Render/Railway. Для этой сдачи выбран путь B.

1. Черновик не обещает фактов, которых нет во входном `text`.
2. Короткий текст вроде «Помогите» → `confidence=low`, `escalate=true`.
3. Сбой ProxyAPI/OpenAI или невалидный JSON модели → шаблон оператору, запись `error`.
4. Не больше `RATE_LIMIT_PER_MINUTE` запросов в минуту на один `client_id` (иначе 429).
5. Temperature модели: `0.2`.

Схема решения: [ARCHITECTURE.md](ARCHITECTURE.md).
