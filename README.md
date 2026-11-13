# prazos-oab

Acompanhamento automático das intimações da OAB/ES 43.793 no DJEN (Comunica/CNJ), com cálculo conservador de prazos e alertas no Telegram.

**É ferramenta de conferência. O prazo vale pelo PJe e pela análise do advogado.**

## Onde roda

O Comunica bloqueia acessos de fora do Brasil (o GitHub Actions recebeu HTTP 403). Por isso a rotina roda como **Cloud Run Job no Google Cloud, em São Paulo** (`southamerica-east1`), disparada de hora em hora pelo Cloud Scheduler. O estado fica num bucket privado do Cloud Storage. Para instalar ou atualizar, rode `bash implantar.sh ID_DO_PROJETO` no Cloud Shell. O GitHub Actions roda só os testes.

## Como funciona

- O Cloud Scheduler dispara `python -m prazos.rodar` de hora em hora.
- A rotina consulta a API pública do Comunica pela OAB e detecta comunicações que ainda não tinha visto.
- Para cada comunicação nova, identifica o prazo no texto e calcula o prazo fatal em dias úteis.
- O alerta vai para o Telegram.
- Todo dia, a partir das 8h, chega um resumo com os prazos em aberto. **Se o resumo não chegar, a rotina parou: confira manualmente.**
- Se a consulta falhar, chega um alerta de falha, repetido a cada 6 horas enquanto durar.
- O estado fica em `data/` e o quadro atualizado em `PAINEL.md`.

## Regras de contagem (`prazos/calendario.py`)

- **Publicação:** primeiro dia útil depois da disponibilização no DJEN. O prazo começa no dia útil seguinte (Lei 11.419/06, art. 4º).
- **Dias não úteis:** fins de semana, feriados nacionais (incluindo 20/11), Sexta-feira Santa, feriado municipal de Vitória (08/09) e recesso de 20/12 a 20/01 (CPC, art. 220).
- **Dias duvidosos:** Carnaval, Quarta de Cinzas, Quinta Santa, Corpus Christi, 28/10 e 08/12. São contados como **úteis**, para o fatal ficar mais cedo, e aparecem sinalizados com a data alternativa.
- **Identificação do prazo:**
  - Só usa o número de dias quando o ato diz expressamente.
  - Réplica "no prazo legal" é presumida como 15 dias úteis, sinalizada para conferir.
  - Nos demais casos, a comunicação é marcada como "conferir".

## Limites

- Expedientes intimados só pelo sistema PJe, fora do DJEN, não aparecem. Faça a conferência semanal do painel do PJe.
- Feriados municipais de outras comarcas ainda não estão cadastrados.

## Configuração

Rode `TELEGRAM_TOKEN=... TELEGRAM_CHAT_ID=... bash implantar.sh ID_DO_PROJETO` no Cloud Shell:

- `TELEGRAM_TOKEN`: token do bot (criado no @BotFather).
- `TELEGRAM_CHAT_ID`: id do seu chat com o bot.

## Testes

```
pip install -r requirements.txt
python -m pytest -q
```
