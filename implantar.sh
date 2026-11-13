#!/usr/bin/env bash
# Implanta a rotina no Google Cloud, em São Paulo (southamerica-east1).
# Uso no Cloud Shell, dentro da pasta do projeto:
#   bash implantar.sh ID_DO_PROJETO
# Para ligar o Telegram depois:
#   TELEGRAM_TOKEN=xxx TELEGRAM_CHAT_ID=yyy bash implantar.sh ID_DO_PROJETO
set -euo pipefail

PROJETO="${1:?Informe o ID do projeto do Google Cloud: bash implantar.sh ID_DO_PROJETO}"
REGIAO="southamerica-east1"
JOB="prazos-oab"
BUCKET="${PROJETO}-prazos-oab"
SA_NOME="prazos-oab"
SA="${SA_NOME}@${PROJETO}.iam.gserviceaccount.com"

echo "==> Projeto: $PROJETO | Região: $REGIAO"
gcloud config set project "$PROJETO" >/dev/null
NUMERO=$(gcloud projects describe "$PROJETO" --format='value(projectNumber)')

echo "==> Ativando serviços (pode levar 1-2 minutos)"
gcloud services enable run.googleapis.com cloudscheduler.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com storage.googleapis.com \
  logging.googleapis.com

echo "==> Bucket de estado (privado, em São Paulo)"
gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1 || \
  gcloud storage buckets create "gs://$BUCKET" --location="$REGIAO" --uniform-bucket-level-access

echo "==> Conta de serviço da rotina"
gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$SA_NOME" --display-name="Rotina prazos OAB"
# Conta recém-criada leva alguns segundos para ser reconhecida pelos outros serviços
for tentativa in 1 2 3 4 5 6; do
  if gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
       --member="serviceAccount:$SA" --role="roles/storage.objectAdmin" >/dev/null 2>&1; then
    break
  fi
  [[ $tentativa == 6 ]] && { echo "Não consegui dar acesso ao bucket. Rode o script de novo em 1 minuto."; exit 1; }
  echo "   aguardando a conta de serviço propagar ($tentativa/6)..."; sleep 15
done

echo "==> Permissão de build para a conta padrão (projetos novos exigem)"
gcloud projects add-iam-policy-binding "$PROJETO" \
  --member="serviceAccount:${NUMERO}-compute@developer.gserviceaccount.com" \
  --role="roles/run.builder" --condition=None >/dev/null

VARS="GCS_BUCKET=$BUCKET,OAB_NUMERO=43793,OAB_UF=ES"
if [[ -n "${TELEGRAM_TOKEN:-}" && -n "${TELEGRAM_CHAT_ID:-}" ]]; then
  VARS="$VARS,TELEGRAM_TOKEN=$TELEGRAM_TOKEN,TELEGRAM_CHAT_ID=$TELEGRAM_CHAT_ID"
  echo "==> Telegram será configurado"
fi

echo "==> Construindo e implantando o job (3-5 minutos na primeira vez)"
gcloud run jobs deploy "$JOB" --source . --region "$REGIAO" --quiet \
  --service-account "$SA" --set-env-vars "$VARS" \
  --max-retries 0 --task-timeout 300

gcloud run jobs add-iam-policy-binding "$JOB" --region "$REGIAO" \
  --member="serviceAccount:$SA" --role="roles/run.invoker" >/dev/null

echo "==> Agendamento de hora em hora (minuto 17, horário de Brasília)"
URI="https://run.googleapis.com/v2/projects/$PROJETO/locations/$REGIAO/jobs/$JOB:run"
if gcloud scheduler jobs describe "${JOB}-hora" --location "$REGIAO" >/dev/null 2>&1; then
  gcloud scheduler jobs update http "${JOB}-hora" --location "$REGIAO" \
    --schedule "17 * * * *" --time-zone "America/Sao_Paulo" --uri "$URI" \
    --http-method POST --oauth-service-account-email "$SA" >/dev/null
else
  gcloud scheduler jobs create http "${JOB}-hora" --location "$REGIAO" \
    --schedule "17 * * * *" --time-zone "America/Sao_Paulo" --uri "$URI" \
    --http-method POST --oauth-service-account-email "$SA" >/dev/null
fi

echo "==> Aplicativo (mesma imagem do job, como serviço web)"
IMAGEM=$(gcloud run jobs describe "$JOB" --region "$REGIAO" \
  --format='value(spec.template.spec.template.spec.containers[0].image)')
APP_OK=1
if [[ -n "$IMAGEM" ]]; then ORIGEM=(--image "$IMAGEM"); else ORIGEM=(--source .); fi
gcloud run deploy "${JOB}-app" "${ORIGEM[@]}" --region "$REGIAO" --quiet \
  --service-account "$SA" --set-env-vars "GCS_BUCKET=$BUCKET" \
  --allow-unauthenticated --min-instances 0 --max-instances 1 --memory 512Mi || APP_OK=0

if [[ $APP_OK == 1 ]]; then
  URL=$(gcloud run services describe "${JOB}-app" --region "$REGIAO" --format='value(status.url)')
  echo "==> Informando o endereço do app ($URL) ao serviço e à rotina (necessário para o push)"
  gcloud run services update "${JOB}-app" --region "$REGIAO" --quiet --update-env-vars "APP_URL=$URL" >/dev/null
  gcloud run jobs update "$JOB" --region "$REGIAO" --quiet --update-env-vars "APP_URL=$URL" >/dev/null
fi

echo "==> Execução de teste da rotina"
gcloud run jobs execute "$JOB" --region "$REGIAO" --wait || true
sleep 10
echo "==> Registro da execução:"
gcloud logging read "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"$JOB\"" \
  --freshness=15m --limit=40 --order=asc --format='value(textPayload)'

echo
if [[ $APP_OK == 1 ]]; then
  curl -s "$URL/api/saude" >/dev/null || true
  CODIGO=$(gcloud storage cat "gs://$BUCKET/config/app.json" | python3 -c 'import json,sys;print(json.load(sys.stdin)["codigo_acesso"])')
  echo "=================================================================="
  echo " ENDEREÇO DO APLICATIVO:  $URL"
  echo " CÓDIGO DE ACESSO:        $CODIGO"
  echo "   (guarde o código; não compartilhe. Para ver de novo, rode este script outra vez)"
  echo "=================================================================="
  echo "Envie ao Claude o registro acima, mas NÃO o código de acesso."
else
  echo "A rotina está funcionando, mas o aplicativo não foi publicado."
  echo "Copie a mensagem de erro acima (do passo 'Aplicativo') e envie ao Claude."
fi
