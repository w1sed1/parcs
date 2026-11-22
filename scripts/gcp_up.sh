#!/usr/bin/env bash
# Розгортання PARCS-Python у Google Cloud Compute Engine:
# 1 master + WORKERS воркерів, кожен вузол - окрема VM з Container-Optimized OS,
# на якій запущено Docker-контейнер hummer12007/parcs-node.
#
#   WORKERS=4 MACHINE=n1-standard-1 ZONE=europe-central2-a scripts/gcp_up.sh
set -euo pipefail

PROJECT=${PROJECT:-parcs-kostenko}
ZONE=${ZONE:-europe-central2-a}
MACHINE=${MACHINE:-n1-standard-1}
WORKERS=${WORKERS:-4}
IMAGE=hummer12007/parcs-node
MY_IP=${MY_IP:-$(curl -s https://api.ipify.org)}

gc() { gcloud --quiet --project "$PROJECT" "$@"; }

# Скрипт запуску VM (виконується при кожному завантаженні): дочекатися Docker і
# підняти вузол PARCS у мережі хоста, щоб вузол реєструвався під внутрішньою
# IP-адресою VM. Вбудований фаєрвол Container-Optimized OS пропускає лише SSH,
# а PARCS слухає 8080 (master) і випадкові порти Flask/Pyro4 (воркери), тому
# відкриваємо 8080 і весь трафік із внутрішньої мережі VPC; зовнішній доступ
# і далі обмежують правила фаєрвола GCP нижче.
startup() {
  printf '#!/bin/bash\n'
  printf 'iptables -C INPUT -p tcp --dport 8080 -j ACCEPT 2>/dev/null || iptables -I INPUT -p tcp --dport 8080 -j ACCEPT\n'
  printf 'iptables -C INPUT -s 10.128.0.0/9 -j ACCEPT 2>/dev/null || iptables -I INPUT -s 10.128.0.0/9 -j ACCEPT\n'
  printf 'until docker info >/dev/null 2>&1; do sleep 1; done\n'
  printf 'docker inspect parcs >/dev/null 2>&1 || docker run -d --restart=always --net=host --name parcs -e PARCS_ARGS="%s" %s\n' "$1" "$IMAGE"
}

gc services enable compute.googleapis.com

# Вебінтерфейс PARCS без автентифікації приймає й виконує довільний Python-код,
# тому порт 8080 відкрито лише для IP-адреси автора. Обмін між VM дозволяє
# стандартне правило default-allow-internal мережі default.
if ! gc compute firewall-rules describe parcs-web >/dev/null 2>&1; then
  gc compute firewall-rules create parcs-web --network default --direction INGRESS \
    --allow tcp:8080 --source-ranges "$MY_IP/32" --target-tags parcs-master
fi

common=(--zone "$ZONE" --machine-type "$MACHINE" --image-family cos-stable
        --image-project cos-cloud --boot-disk-size 10GB)

gc compute instances create parcs-master "${common[@]}" --tags parcs-master \
  --metadata startup-script="$(startup master)"
MASTER_INTERNAL=$(gc compute instances describe parcs-master --zone "$ZONE" \
  --format 'value(networkInterfaces[0].networkIP)')
MASTER_EXTERNAL=$(gc compute instances describe parcs-master --zone "$ZONE" \
  --format 'value(networkInterfaces[0].accessConfigs[0].natIP)')

workers=()
for i in $(seq 1 "$WORKERS"); do workers+=("parcs-worker-$i"); done
gc compute instances create "${workers[@]}" "${common[@]}" \
  --metadata startup-script="$(startup "worker $MASTER_INTERNAL")"

echo "Waiting for $WORKERS workers to register on http://$MASTER_EXTERNAL:8080 ..."
n=0
for _ in $(seq 1 90); do
  n=$(curl -s --max-time 3 "http://$MASTER_EXTERNAL:8080/api/worker" \
      | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["workers"]))' 2>/dev/null || echo 0)
  [ "$n" -ge "$WORKERS" ] && break
  sleep 5
done
gc compute instances list --format "table(name,zone.basename(),machineType.basename(),networkInterfaces[0].networkIP,networkInterfaces[0].accessConfigs[0].natIP,status)"
echo "PARCS master: http://$MASTER_EXTERNAL:8080  (registered workers: $n of $WORKERS)"
[ "$n" -ge "$WORKERS" ]
