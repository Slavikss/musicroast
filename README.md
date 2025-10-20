# musicroast

Проект musicroast - сервис для генерации юмористических "прожарок" на основе музыкальной библиотеки пользователя.

## Установка

1. Установите зависимости с помощью Poetry:
```bash
poetry install
```

2. Создайте файл `.env` в корне проекта со следующими переменными:
```
GOOGLE_API_KEY=ваш_ключ_google_gemini_api
TELEGRAM_BOT_TOKEN=токен_вашего_телеграм_бота(для_режима_бота)
BACKEND_API_BASE_URL=http://localhost:8000
YANDEX_OAUTH_CLIENT_ID=23cabbbdc6cd418abb4b39c32c41195d
YANDEX_OAUTH_HEADLESS=true
YANDEX_OAUTH_INTERACTIVE_HEADLESS=false
YANDEX_OAUTH_VIEWPORT_WIDTH=1280
YANDEX_OAUTH_VIEWPORT_HEIGHT=720
YANDEX_OAUTH_TIMEOUT=120
YANDEX_OAUTH_DRIVER_PATH=путь_к_chromedriver_если_нужен(опционально)
YANDEX_OAUTH_CHROME_BINARY=путь_к_chrome_или_chromium(опционально)
YANDEX_MINIAPP_URL=https://<ваш_домен>/miniapp/yandex (опционально, если отличается)
TOKEN_STORAGE_DEFAULT_TTL=86400
PROMPT_CONFIG_PATH=путь_к_json_конфигу_промптов(опционально)
```

Ключи доступа к потоковым сервисам (Яндекс Музыка, Spotify, Apple Music и др.) передаются приложению напрямую в запросах и не сохраняются на сервере.


## Запуск

### API
```bash
poetry run python main.py
```

Сервис будет доступен по адресу: http://localhost:8000

### Telegram-бот
```bash
poetry run python -m app.bot
```

Бот использует Telegram Mini App для автоматического получения access_token Yandex Music. Команда `/start` отправляет инструкцию и кнопку «🔑 Получить токен», которая открывает mini app. После успешного логина токен передается в бота автоматически, и можно жать «🔥 Прожарить» для генерации прожарки.

### Mini App: авторизация в Яндекс Музыке
- WebApp возвращается по `GET /miniapp/yandex` и транслирует реальную вкладку Chrome через WebSocket.
- `POST /auth/yandex/session` создаёт интерактивную Selenium-сессию, `WS /ws/auth/yandex/session/{session_id}` принимает события мыши/клавиатуры и отдаёт кадры.
- После успешной авторизации токен автоматически сохраняется в `TokenStorage` и доступен боту через `GET /auth/yandex/token/{telegram_user_id}` (используется как fallback).
- Старый поток `POST /auth/yandex/token` оставлен как резервный вариант для автоматизированной авторизации.
- Для интерактивного режима нужен установленный Chrome/Chromium и chromedriver. Пути можно указать через `YANDEX_OAUTH_DRIVER_PATH` и `YANDEX_OAUTH_CHROME_BINARY`. Флаг `YANDEX_OAUTH_INTERACTIVE_HEADLESS` позволяет переключать headless-режим.
- Если Mini App недоступен, бот по-прежнему принимает токен одноразовым текстовым сообщением.

## API Endpoints

- POST `/streaming/playlists` — принимает `provider` и `access_token`, возвращает список доступных плейлистов выбранного стриминга и доступные версии промптов.
- POST `/streaming/playlist-info` — принимает `provider`, `access_token` и `playlist_kind`, возвращает нормализованные данные выбранного плейлиста.
- POST `/roast` — принимает данные стриминга, идентификатор плейлиста, опциональную `prompt_version` и флаг `generate_image`, генерирует прожарку и, при необходимости, изображение.

## Заметки

- Используется Google Gemini Pro с параметрами temperature=0.9 и top_p=0.95 для оптимального баланса креативности и связности текста
- Для работы требуется ключ Google Gemini API и действующий токен выбранного потокового сервиса


## Реализованный функционал
- ООП-архитектура: единый абстрактный слой для стримингов (Яндекс, Spotify, Apple Music) плюс сервисы нормализации треков, управления промптами и генерации прожарок.
- PromptManager с версионированием и загрузкой конфигурации из JSON-файла через `PROMPT_CONFIG_PATH`.
- Безопасная работа с пользовательскими токенами: они передаются только в запросах и не сохраняются на сервере.
- FastAPI эндпоинты `/streaming/playlists`, `/streaming/playlist-info` и `/roast`, поддерживающие генерацию текста и изображений.
- Асинхронный Telegram-бот на aiogram с онбордингом, проверкой токена и генерацией прожарки одним нажатием.

## Docker
- Сборка: `docker build -t musicroast .`
- Запуск API (по умолчанию): `docker run --rm -p 8000:8000 --env-file .env musicroast`
- Запуск бота: `docker run --rm --env-file .env -e APP_MODE=bot musicroast`

## TODO(coding)
- сделать так, чтобы веб браузер в селениуме работал в тг, и авторизация была там




## TODO(non-coding)
1. выявить еще 3 стиля прожарки плейлиста юзера, включая:
    - оценка плейлиста юзера по всем каноничным критериям РЗТ + итоговая оценка + итоговое описывающее словосочетание
