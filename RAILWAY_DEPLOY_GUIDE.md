# Контролируемое обновление Railway

Ниже — инструкция владельцу, не выполненные production-действия.
Состояние Dashboard, Variables, Volume и старого образа не было доступно агенту.
В репозитории проверены `.python-version` (3.12), `requirements.txt`
(aiogram 3.15.0 / aiosqlite 0.20.0), `Procfile` (`worker: python main.py`).
Веб-сервер, порт и HTTP healthcheck для этого worker не нужны.

## 1. Зафиксировать исходное состояние

Записать deployment ID/commit старой версии, фактические абсолютные DB_PATH,
DATA_DIR, пути JSON и наличие WAL. Не считать, что текущая production-база
уже находится в /data. Проверить Variables в Dashboard без публикации значений.
Зафиксировать autodeploy branch; отключить непреднамеренный autodeploy на
время подготовки. Не запускать новый сервис тем же токеном параллельно старому.

## 2. Backup и проверка

Согласовать окно обслуживания. Остановить все писатели приложения, включая
рассылки, внешние административные скрипты и старый polling. Убедиться, что
нет второго экземпляра. Открытые idle SQLite-соединения допустимы для backup
API, но записи JSON/SQLite во время общего снимка должны быть остановлены.

Владельцу выполнить в среде с фактическими файлами (пути заменить проверенными):

```text
python tools/snapshot_data.py --db /actual/users.db --data-dir /actual/data --output /external/release-backup-UTC --writers-stopped
```

Скрипт не изменяет источник, не берёт `.env`, создаёт новый каталог, получает
SQLite через backup API (учитывает WAL), копирует известные JSON и записывает
`backup-manifest.json`: integrity/FK, количества и SHA-256. При раздельных
старых путях /actual/users.db и /actual/data указываются отдельно. Не применять
обычный `cp users.db` к открытому WAL-файлу. Сохранить архив результата вне
production Volume и проверить контрольные суммы после переноса.

Дополнительно создать ручной Railway Volume backup и проверить его доступность.
Восстановление платформенного backup ограничено тем же project/environment;
для staging нужен отдельный экспорт. Возможности и ограничения описаны в
[Railway Backups](https://docs.railway.com/volumes/backups).

## 3. Миграционный preflight на копии

На локальном экспортированном backup, не на подключённом рабочем Volume:

```text
python tools/migration_preflight.py --source-db /copies/release-backup/users.db --source-data /copies/release-backup --output /copies/preflight-UTC
```

Нужен новый пустой путь output вне source-data. Скрипт открывает source только
для чтения, снова использует backup API, мигрирует только output, не загружает
production `.env`, не запускает Telegram. Проверяет все старые таблицы/строки
по прежним столбцам, ID, integrity/FK, исходные поля сериалов, постеры/TikTok,
JSON-настройки и второй запуск миграций. Результат `preflight-report.json`.
Исходные данные не заменяются. Ошибка означает остановку выпуска, а не
разрешение пропустить проверку. Автотест использовал синтетическую старую
базу: фактическая production-копия пока не предоставлялась.

## 4. Staging

Выполнить [STAGING_TEST_PLAN.md](STAGING_TEST_PLAN.md), включая живой Telegram,
Inline Mode и рестарт Linux с независимыми секретами/Volume. Мигрированную
production-копию не подключать к активному staging, чтобы исключить рассылки
реальным пользователям. Не дублировать production Variables автоматически.

## 5. Проверить настройки deployment

| Настройка | Требуемое значение |
|---|---|
| Python | 3.12, проверить build log; если builder игнорирует .python-version — задать версию в его настройках |
| Start Command | `python main.py` |
| Replicas/polling | Один процесс на токен, без запуска локальной копии production |
| Volume | Постоянный, смонтирован в /data |
| DATA_DIR | `/data` |
| DB_PATH / LOGS_DIR | `/data/users.db` / `/data/logs`, либо убрать явные overrides |
| BOT_TOKEN | Только production-токен в production Variables |
| SUBSCRIPTION_REQUIRED | `true`, если подписка обязательна |
| SUBSCRIPTION_CHANNEL_ID / URL | Фактический канал; бот назначен администратором |
| ADMIN_IDS / activation | Проверенный allowlist; сохранённые is_admin не сбрасываются |
| ENV_FILE | Не задавать для обычного Railway; секреты из Variables |
| Serverless / sleep | Выключить для постоянно работающего polling worker |
| Restart policy | Always для оплаченного worker; проверить фактический выбор владельцем |
| RAILWAY_DEPLOYMENT_DRAINING_SECONDS | Рекомендуется `30` для завершения задач и SQLite |
| HTTP healthcheck / cron | Не назначать этому worker |

Railway допускает небольшой overlap обычных deployment; Volume ограничивает
одновременное подключение, но это не заменяет проверку единственного polling.
При выпуске сначала убедиться в остановке старого процесса.
[Deployment lifecycle](https://docs.railway.com/deployments/reference),
[Volumes](https://docs.railway.com/volumes/reference).

Serverless определяется исходящим трафиком; long polling не является HTTP
endpoint для пробуждения. Выключение sleep здесь — рекомендация для worker,
а не утверждение о текущем Dashboard.
[Serverless](https://docs.railway.com/deployments/serverless).
Перезапуск регулируется отдельно: [Restart policy](https://docs.railway.com/deployments/restart-policy).

Не добавляли railway.json/toml, который мог бы неожиданно переопределить
Dashboard. Проверка конфигурации в репозитории не подтверждает настройки
реального Railway-сервиса. Не запускать миграции в build/pre-deploy среде без
production Volume; они выполняются startup приложения после подключения путей.

## 6. Выпуск после одобрения

Владелец сверяет результаты preflight/live-матрицы, backup и rollback, затем
одобряет конкретный feature-коммит. Переносит подготовленные данные в /data,
если пути меняются, **до** первого запуска, сохраняя SQLite+JSON согласованно.
После этого развёртывает утверждённый commit штатным способом. Агент не
выполнял push/merge/deploy и не запускал миграции рабочей базы.

## 7. После запуска

Проверить build Python/dependencies; в логах нет Conflict, busy-loop, traceback,
ошибок путей, пустого неожиданного каталога или токенов. Сопоставить количества
из backup/preflight с production. Пройти /start, старый аккаунт, карточку,
inline, одно подтверждение с повтором, профиль, отзыв и рулетку с повтором.
Проверить restart persistence. Логи ротируются (5 MiB × текущий + 3 копии),
ошибки/traceback форматируются с маскировкой текущих секретов.

При проблеме остановить изменения и следовать [ROLLBACK_PLAN.md](ROLLBACK_PLAN.md).
Не восстанавливать старую SQLite поверх новых начислений автоматически.
