#!/usr/bin/env bash
# Видаляє всі VM PARCS і правило firewall, щоб не витрачати кредити.
set -euo pipefail
PROJECT=${PROJECT:-parcs-kostenko}
ZONE=${ZONE:-europe-central2-a}

vms=$(gcloud --project "$PROJECT" compute instances list --filter "name~^parcs-" --format "value(name)")
[ -n "$vms" ] && gcloud --quiet --project "$PROJECT" compute instances delete $vms --zone "$ZONE"
gcloud --quiet --project "$PROJECT" compute firewall-rules delete parcs-web 2>/dev/null || true
gcloud --project "$PROJECT" compute instances list
