# Развёртывание

Ansible-плейбук, поднимающий сбор на чистой Ubuntu: docker с Postgres,
`watch`, бот, таймеры на `derive && alerts` и на копии.

```bash
ansible-galaxy install -r requirements.yml
cp inventory.ini.example inventory.ini            # вписать адрес и пользователя
cp group_vars/secrets.vault.yml.example group_vars/secrets.vault.yml
$EDITOR group_vars/secrets.vault.yml
ansible-vault encrypt group_vars/secrets.vault.yml
$EDITOR group_vars/all.yml                        # пути к сессии и дампу

ansible-playbook playbook.yml --ask-vault-pass
```

Перед первым запуском снимите свежий дамп на рабочей машине — `watch` всё
ещё пишет, и вчерашний уже отстал:

```bash
make backup-full
```

## Чего плейбук не делает намеренно

**Не запускает `itgraph login`.** Файл сессии копируется с рабочей машины,
потому что он несёт кэш сущностей, из которого `cached_peer` берёт пиров без
траты суточной квоты. На свежей сессии кэша нет: все seed-каналы уйдут в
«нет кэшированного пира», а восстановление через `resolve` при паре сотен
резолвов в сутки — трое суток остановленного сбора. Ловушка коварна тем, что
`login` выглядит как обычный шаг настройки.

**Не восстанавливает поверх непустой базы.** Единственный разрушительный шаг
защищён проверкой: если в `channels` есть строки, плейбук останавливается.
Перезапуск не должен быть способом потерять ручную разметку. Осознанно —
`-e force_restore=true`.

**Не гасит сбор на рабочей машине.** Это решение человека, и принимать его
стоит после того, как пройдёт проверка в конце.

## Что проверяется в конце

Три вещи, каждая ловит отказ, который иначе выглядит как успех:

| проверка | что ловит |
|---|---|
| в логе нет `Telegram route: direct` | `.env` не доехал, и коллектор ходит с адреса, который прокси должен был скрыть |
| в логе нет `no cached peer` | приехала не та сессия |
| `channels` со статусом `seed` не пусто | восстановление не легло |

Плейбук падает на любой из них — до того, как вы выключите рабочий сбор.

## Секреты

`group_vars/secrets.vault.yml` шифруется ansible-vault и **коммитится в
таком виде**. Не коммитятся пароль от хранилища и любые незашифрованные
переменные — это в `.gitignore`:

```
deploy/.vault-pass
deploy/group_vars/secrets.yml
```

Файл сессии в репозиторий не попадает никогда: он в `.gitignore` по маске
`*.session`, а плейбук берёт его по пути с вашей машины.

## Отдельные части

```bash
ansible-playbook playbook.yml --tags services   # только unit-файлы и перезапуск
ansible-playbook playbook.yml --tags config     # только .env
ansible-playbook playbook.yml --tags verify     # только проверки
```

## Что остаётся руками

**Прокси.** Плейбук кладёт настройки и проверяет, что маршрут не прямой, но
сам эндпоинт вы получаете у провайдера VPN. Проверить до запуска:

```bash
curl -s --socks5 ХОСТ:ПОРТ https://api.ipify.org
```

Адрес должен быть купленный, а не адрес виртуалки.

**Не заворачивайте весь трафик в WireGuard на удалённой машине** — полный
туннель меняет маршрут по умолчанию и рвёт вашу же SSH-сессию. SOCKS5 в
настройках гоняет через прокси только MTProto; SSH и Bot API идут напрямую,
и заблокировать себя так невозможно.

**Копии инвентаря.** 172 КБ, которые стоят недель разметки, и они не в дампе
отдельным файлом:

```bash
docker exec itgraph-postgres psql -U itgraph -d itgraph -c "\copy (
  SELECT tg_id, username, title, is_chat, status, kind, kind_note,
         reject_reason, reject_note, discovered_via, linked_to
  FROM channels) TO STDOUT WITH CSV HEADER" > ~/itgraph-inventory.csv
```
