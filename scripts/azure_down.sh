#!/usr/bin/env bash
# Видаляє групу ресурсів PARCS разом з усіма VM, дисками, IP та мережею.
set -euo pipefail
RG=${RG:-parcs-rg}
az group delete -n "$RG" --yes
az group list --query "[?name=='$RG']" -o table
