# syntax=docker/dockerfile:1.7-labs

# 1) Базовый образ с Python
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# 2) Устанавливаем системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates build-essential && \
    rm -rf /var/lib/apt/lists/*

# 3) Устанавливаем Poetry
ENV POETRY_VERSION=1.8.3 \
    POETRY_HOME=/opt/poetry \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_NO_INTERACTION=1
RUN curl -sSL https://install.python-poetry.org | python3 - && \
    ln -s /opt/poetry/bin/poetry /usr/local/bin/poetry

WORKDIR /app

# 4) Кэшируем зависимости: сначала копируем только файлы декларации
COPY pyproject.toml poetry.lock ./

# 5) Устанавливаем зависимости (без установки текущего проекта)
RUN poetry install --no-root --only main

# 6) Копируем исходники проекта
COPY . .

# 7) Открываем порт для FastAPI
EXPOSE 8000

# 8) По умолчанию приложение читает .env (load_dotenv в main.py)
# Можно переопределить переменные окружения при `docker run -e KEY=VALUE`

# 9) Команда запуска через uvicorn или телеграм-бот в зависимости от переменной окружения APP_MODE
ENV APP_MODE=bot

CMD ["bash", "-lc", "if [ \"$APP_MODE\" = \"bot\" ]; then poetry run python -m app.bot; else poetry run uvicorn main:app --host 0.0.0.0 --port 8000; fi"]
