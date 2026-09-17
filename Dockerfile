FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV SQLITE_PATH=/data/tickets.db
ENV OPENAI_BASE_URL=https://api.proxyapi.ru/v1
ENV OPENAI_MODEL=gpt-4o-mini
ENV RATE_LIMIT_PER_MINUTE=10

RUN mkdir -p /data
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
