# Выгрузка данных контрактов ЕИС

FastAPI-приложение ищет контракты на `zakupki.gov.ru`, разбирает карточки,
UPD/LP и формирует Excel в каталоге `output`.

Одновременно приложение выполняет только одну массовую выгрузку, чтобы
снизить риск ограничений 403/429 со стороны ЕИС. Повторный запрос не запускает
второй pipeline, а перенаправляется на страницу уже выполняющейся задачи.

## Локальный запуск

Требуется Python 3.12 (поддерживаются и совместимые актуальные версии Python).

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8000 --workers 1
```

Откройте `http://localhost:8000`. Проверка состояния:
`http://localhost:8000/health`.

Не используйте несколько uvicorn workers: блокировка одной активной задачи
хранится в памяти процесса.

## Docker

Скопируйте пример настроек при необходимости:

```powershell
Copy-Item .env.example .env
docker build -t contract-web .
docker run --rm -p 8000:8000 --env-file .env -v "${PWD}/output:/app/output" contract-web
```

Через Compose:

```powershell
docker compose up -d --build
docker compose ps
```

Excel-файлы остаются в локальном каталоге `./output`, подключённом к
`/app/output` контейнера. Они не удаляются после скачивания.

## Переменные окружения

### Сертификат ЕИС в Docker

Если сервер использует дополнительный доверенный CA-сертификат, положите
полный CA bundle в PEM-формате в `./certs/eis-ca.pem` рядом с
`docker-compose.yml`. Docker Compose монтирует каталог `./certs` в
`/app/certs` только для чтения. Сертификаты из него не добавляются в Git и
Docker-образ.

В файле `.env` на сервере задайте:

```dotenv
ZAKUPKI_VERIFY_SSL=true
REQUESTS_CA_BUNDLE=/app/certs/eis-ca.pem
```

Проверьте, что файл существует, затем пересоздайте контейнер:

```bash
docker compose up -d --force-recreate
```

Установка CA только в хранилище сервера не передаёт его контейнеру.
Если сертификат вручную добавили в запущенный контейнер, он пропадёт после
пересоздания; используйте подключённый каталог `certs`.

### Список переменных

- `ZAKUPKI_VERIFY_SSL` — проверка TLS; для сервера рекомендуется `true`.
- `REQUESTS_CA_BUNDLE` — путь к собственному CA bundle организации.
- `ZAKUPKI_MIN_REQUEST_INTERVAL` — минимальный интервал между запросами, `1.2`.
- `ZAKUPKI_PROACTIVE_PAUSE_EVERY` — профилактическая пауза каждые 40 запросов.
- `ZAKUPKI_PROACTIVE_PAUSE_SECONDS` — длительность такой паузы, 20 секунд.
- `ZAKUPKI_BLOCK_RETRIES` — число повторов после 403/429, `3`.
- `APP_HOST`, `APP_PORT` — адрес и порт uvicorn в контейнере.
- `LOG_LEVEL` — уровень логирования, по умолчанию `INFO`.

Если `ZAKUPKI_VERIFY_SSL=false`, клиент отключает проверку TLS только по явно
заданной настройке. Docker-образ сам проверку сертификатов не отключает.

## Фоновые задачи

Состояние задач хранится в памяти. После перезапуска приложения история задач
пропадает, активная блокировка освобождается и можно начать новую выгрузку.
Готовые Excel при этом сохраняются в persistent-каталоге `output`.
