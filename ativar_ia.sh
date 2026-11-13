#!/usr/bin/env bash
# Ativa a leitura das intimações por IA (API da Anthropic).
# A chave é digitada aqui (não aparece na tela) e fica no Secret Manager do Google Cloud,
# acessível só pela rotina. Uso no Cloud Shell, dentro da pasta do projeto:
#   bash ativar_ia.sh project-a4f3c7af-dbb6-41b1-aa4
set -euo pipefail
PROJETO="${1:?Informe o ID do projeto: bash ativar_ia.sh ID_DO_PROJETO}"
REGIAO="southamerica-east1"
SA="prazos-oab@${PROJETO}.iam.gserviceaccount.com"
SEGREDO="anthropic-api-key"

gcloud config set project "$PROJETO" >/dev/null
gcloud services enable secretmanager.googleapis.com >/dev/null

echo "Cole a chave da API da Anthropic (começa com sk-ant-) e aperte Enter."
echo "(por segurança, nada aparece enquanto você cola)"
read -rs CHAVE
echo
[[ "$CHAVE" == sk-ant-* ]] || { echo "Isso não parece uma chave da Anthropic (deveria começar com sk-ant-)."; exit 1; }

if gcloud secrets describe "$SEGREDO" >/dev/null 2>&1; then
  printf '%s' "$CHAVE" | gcloud secrets versions add "$SEGREDO" --data-file=- >/dev/null
else
  printf '%s' "$CHAVE" | gcloud secrets create "$SEGREDO" --replication-policy=automatic --data-file=- >/dev/null
fi
unset CHAVE
gcloud secrets add-iam-policy-binding "$SEGREDO" --member="serviceAccount:$SA" \
  --role="roles/secretmanager.secretAccessor" >/dev/null

gcloud run jobs update prazos-oab --region "$REGIAO" --quiet \
  --update-secrets "ANTHROPIC_API_KEY=${SEGREDO}:latest" >/dev/null
echo "==> IA ativada. Na próxima execução (de hora em hora) a rotina lê as intimações recentes."
echo "    Para testar agora: gcloud run jobs execute prazos-oab --region $REGIAO --wait"
