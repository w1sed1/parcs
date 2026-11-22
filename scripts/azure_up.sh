#!/usr/bin/env bash
# Розгортання PARCS-Python в Microsoft Azure: 1 master + WORKERS воркерів,
# кожен вузол - окрема VM (Ubuntu + Docker-контейнер hummer12007/parcs-node).
#
#   WORKERS=4 SIZE=Standard_DS1_v2 LOCATION=westeurope scripts/azure_up.sh
set -euo pipefail

RG=${RG:-parcs-rg}
LOCATION=${LOCATION:-westeurope}
SIZE=${SIZE:-Standard_DS1_v2}
WORKERS=${WORKERS:-4}
IMAGE=hummer12007/parcs-node
MASTER_IP=10.0.0.4
MY_IP=${MY_IP:-$(curl -s https://api.ipify.org)}

# cloud-init: встановити Docker і підняти вузол PARCS у мережі хоста, щоб він
# реєструвався під приватною IP-адресою VM.
cloud_init() {
  cat <<EOF
#!/bin/bash
apt-get update -y && apt-get install -y docker.io
until docker info >/dev/null 2>&1; do sleep 1; done
docker run -d --restart=always --net=host --name parcs -e PARCS_ARGS="$1" $IMAGE
EOF
}

az group create -n "$RG" -l "$LOCATION" -o none
az network vnet create -g "$RG" -n parcs-vnet --address-prefix 10.0.0.0/16 \
  --subnet-name parcs-subnet --subnet-prefix 10.0.0.0/24 -o none
# Вебінтерфейс PARCS без автентифікації виконує довільний Python-код, тому
# 8080 і SSH відкрито лише для IP-адреси автора. Обмін між VM усередині
# мережі дозволяє стандартне правило AllowVnetInBound.
az network nsg create -g "$RG" -n parcs-nsg -o none
az network nsg rule create -g "$RG" --nsg-name parcs-nsg -n allow-author --priority 100 \
  --source-address-prefixes "$MY_IP/32" --destination-port-ranges 8080 22 --protocol Tcp -o none

common=(--resource-group "$RG" --image Ubuntu2404 --size "$SIZE" --admin-username parcs
        --generate-ssh-keys --vnet-name parcs-vnet --subnet parcs-subnet --nsg parcs-nsg
        --public-ip-sku Standard --os-disk-size-gb 30 -o none)

cloud_init master > /tmp/parcs-master-init.sh
az vm create -n parcs-master "${common[@]}" --private-ip-address "$MASTER_IP" \
  --custom-data /tmp/parcs-master-init.sh
cloud_init "worker $MASTER_IP" > /tmp/parcs-worker-init.sh
for i in $(seq 1 "$WORKERS"); do
  az vm create -n "parcs-worker-$i" "${common[@]}" --custom-data /tmp/parcs-worker-init.sh --no-wait
done

MASTER_PUBLIC=$(az vm show -d -g "$RG" -n parcs-master --query publicIps -o tsv)
echo "Waiting for $WORKERS workers to register on http://$MASTER_PUBLIC:8080 ..."
n=0
for _ in $(seq 1 120); do
  n=$(curl -s --max-time 3 "http://$MASTER_PUBLIC:8080/api/worker" \
      | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["workers"]))' 2>/dev/null || echo 0)
  [ "$n" -ge "$WORKERS" ] && break
  sleep 5
done
az vm list -g "$RG" -d --query "[].{name:name, size:hardwareProfile.vmSize, privateIp:privateIps, publicIp:publicIps, state:powerState}" -o table
echo "PARCS master: http://$MASTER_PUBLIC:8080  (registered workers: $n of $WORKERS)"
[ "$n" -ge "$WORKERS" ]
