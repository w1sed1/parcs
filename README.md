# Пошук простих чисел у великому діапазоні з PARCS

Лабораторна робота 1 з дисципліни «Хмарні обчислення». Послідовний та
розподілений паралельний пошук простих чисел у діапазоні `[start, end)` на
платформі [PARCS-Python](https://github.com/Hummer12007/parcs-python),
розгорнутій у Google Cloud Compute Engine.

## Алгоритм

Сегментоване решето Ератосфена. Сегмент містить лише непарні числа
(`bytearray`, 1 байт на число, 2²² чисел = 2 МБ), кратні базових простих
`p ≤ √end` викреслюються присвоєнням зрізу `seg[i::p]`, тобто на рівні C.
Результат: кількість простих, перші та останні 10 простих діапазону.

Паралельний режим: master ділить діапазон на рівні суміжні частини за
кількістю воркерів, асинхронно викликає `mymap(lo, hi)` на всіх воркерах через
Pyro4, потім підсумовує відповіді. Кожен воркер сам будує базові прості, тому
між вузлами передаються лише межі частин і короткий підсумок.

## Структура

| Шлях | Що це |
|---|---|
| `solution/primes_solution.py` | розв'язок для PARCS (клас `Solver`) і послідовна CLI-версія |
| `inputs/` | вхідні файли для вебінтерфейсу PARCS |
| `tests/test_primes.py` | перевірка з наївним алгоритмом і відомими значеннями π(x) |
| `scripts/gcp_up.sh`, `scripts/gcp_down.sh` | створення та видалення кластера в GCP |
| `scripts/run_experiments.py` | запуск серії задач через HTTP API PARCS і підсумкові таблиці |
| `docker-compose.yml` | локальний кластер (master + 4 воркери) для перевірки |
| `results/` | сирі відповіді, усі прогони та підсумкова таблиця |

## Запуск

```bash
python3 tests/test_primes.py                                  # тести (Python 3 або 2.7)
python3 solution/primes_solution.py inputs/input_sequential.json   # послідовно, локально

docker compose up -d --scale worker=4                         # PARCS у Docker: http://localhost:8080
python3 scripts/run_experiments.py --master http://localhost:8080 --ends 1e9,1e10 --workers 1,2,3,4 --runs 3

WORKERS=4 scripts/gcp_up.sh                                   # або окремі VM у Google Cloud
gcloud compute instances stop parcs-master parcs-worker-{1..4} --zone europe-central2-a   # зупинити після замірів
scripts/gcp_down.sh                                           # видалити VM, коли кластер більше не потрібен
```

Заміри в `results/` виконано в Google Cloud (зона europe-central2-a): master і 4 воркери,
кожен вузол на окремій VM n1-standard-1 (1 vCPU, 3,75 ГБ RAM, Container-Optimized OS).

У вебінтерфейсі PARCS (`Add Job`) як розв'язок завантажується
`solution/primes_solution.py`, як вхідні дані: файл з `inputs/`. Відповідь
(`Output`) містить JSON з кількістю простих, часом `elapsed_seconds` і часом
кожного воркера.
