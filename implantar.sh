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

echo "==> Backup: versionamento do bucket (versões antigas guardadas por 60 dias)"
gcloud storage buckets update "gs://$BUCKET" --versioning >/dev/null
cat > /tmp/ciclo-vida.json <<'JSON'
{"rule": [{"action": {"type": "Delete"}, "condition": {"daysSinceNoncurrentTime": 60}}]}
JSON
gcloud storage buckets update "gs://$BUCKET" --lifecycle-file=/tmp/ciclo-vida.json >/dev/null

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

echo "==> Implantação automática a partir do GitHub (sem senhas: federação de identidade)"
REPO="henriquetorezani-maker/prazos-oab"
DEPLOY_NOME="prazos-oab-deploy"
DEPLOY_SA="${DEPLOY_NOME}@${PROJETO}.iam.gserviceaccount.com"
AUTO_OK=1
(
  set -e
  gcloud services enable iamcredentials.googleapis.com sts.googleapis.com >/dev/null
  gcloud iam workload-identity-pools describe github --location=global >/dev/null 2>&1 || \
    gcloud iam workload-identity-pools create github --location=global --display-name="GitHub" >/dev/null
  gcloud iam workload-identity-pools providers describe prazos-oab --location=global \
      --workload-identity-pool=github >/dev/null 2>&1 || \
    gcloud iam workload-identity-pools providers create-oidc prazos-oab --location=global \
      --workload-identity-pool=github --display-name="Repo prazos-oab" \
      --issuer-uri="https://token.actions.githubusercontent.com" \
      --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
      --attribute-condition="assertion.repository=='$REPO' && assertion.ref=='refs/heads/main'" >/dev/null
  gcloud iam service-accounts describe "$DEPLOY_SA" >/dev/null 2>&1 || \
    gcloud iam service-accounts create "$DEPLOY_NOME" --display-name="Implantação automática (GitHub)" >/dev/null
  sleep 10
  for papel in roles/run.admin roles/cloudbuild.builds.editor roles/artifactregistry.writer \
               roles/storage.admin roles/serviceusage.serviceUsageConsumer roles/viewer; do
    gcloud projects add-iam-policy-binding "$PROJETO" --member="serviceAccount:$DEPLOY_SA" \
      --role="$papel" --condition=None >/dev/null
  done
  for alvo in "$SA" "${NUMERO}-compute@developer.gserviceaccount.com"; do
    gcloud iam service-accounts add-iam-policy-binding "$alvo" --member="serviceAccount:$DEPLOY_SA" \
      --role="roles/iam.serviceAccountUser" >/dev/null
  done
  gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA" --role="roles/iam.workloadIdentityUser" \
    --member="principalSet://iam.googleapis.com/projects/$NUMERO/locations/global/workloadIdentityPools/github/attribute.repository/$REPO" >/dev/null
) || AUTO_OK=0
if [[ $AUTO_OK == 1 ]]; then
  echo "   OK: provedor projects/$NUMERO/locations/global/workloadIdentityPools/github/providers/prazos-oab"
  echo "   OK: conta $DEPLOY_SA"
else
  echo "   AVISO: a implantação automática não foi configurada (a rotina e o app seguem normais)."
fi

echo "==> Execução de teste da rotina"
gcloud run jobs execute "$JOB" --region "$REGIAO" --wait || true
sleep 10
echo "==> Registro da execução:"
gcloud logging read "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"$JOB\"" \
  --freshness=4m --limit=40 --order=asc --format='value(textPayload)'

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
