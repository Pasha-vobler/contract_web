# Сертификаты для ЕИС

Положите доверенный CA bundle в PEM-формате сюда, например `eis-ca.pem`.
Файл сертификата не хранится в Git и не попадает в Docker-образ. Compose
подключает этот каталог в контейнер как `/app/certs` только для чтения.

В серверном `.env` укажите:

```dotenv
ZAKUPKI_VERIFY_SSL=true
REQUESTS_CA_BUNDLE=/app/certs/eis-ca.pem
```

После этого выполните `docker compose up -d --force-recreate`.
