# prazos-oab

Acompanhamento automático das intimações da OAB/ES 43.793 no DJEN (Comunica/CNJ), com cálculo conservador de prazos e alertas no Telegram.

**É ferramenta de conferência. O prazo vale pelo PJe e pela análise do advogado.**

## Onde roda

O Comunica bloqueia acessos de fora do Brasil (o GitHub Actions recebeu HTTP 403). Por isso a rotina roda como **Cloud Run Job no Google Cloud, em São Paulo** (`southamerica-east1`), disparada de hora em hora pelo Cloud Scheduler. O estado fica num bucket privado do Cloud Storage. Para instalar ou atualizar, rode `bash implantar.sh ID_DO_PROJETO` no Cloud Shell. O GitHub Actions roda só os testes.

## Aplicativo (PWA)

O serviço `prazos-oab-app`, no Cloud Run, publica um aplicativo web instalável. Ele exige o código de acesso, gerado na primeira implantação e guardado em `config/app.json`, no bucket privado. O aplicativo:

- lista os prazos em aberto, as comunicações a conferir e as cumpridas;
- mostra o teor e a contagem de cada prazo;
- tem o botão "Marcar cumprido";
- envia notificações push (Web Push/VAPID).

No iPhone, as notificações exigem que o app esteja adicionado à Tela de Início. O Telegram continua opcional, como canal reserva.


## Recursos (versão de 13/11/2026)

- **Prazo manual:** em cada intimação há "Definir prazo", com os dias (úteis ou corridos) ou a data fatal exata. "Voltar ao automático" desfaz.
- **Prazo avulso:** cadastra expedientes que só saíram no PJe, com processo, descrição e data fatal.
- **Notas:** anotação livre em cada intimação, que aparece também nos lembretes.
- **Busca e abas:** busca por processo, parte, vara, texto ou nota, nas abas Pendentes, Histórico e Cumpridos.
- **Lembretes:** push 5 dias antes, 2 dias antes e no dia do fatal, entre 8h e 21h. Não lembra o que já foi marcado como cumprido.
- **Leitura do prazo no texto:**
  - prazos em horas ou em "dias corridos" usam contagem corrida, sempre o resultado mais curto, e são sinalizados;
  - prazos de citação da outra parte e os que correm "findo o prazo" são desconsiderados.
- **Busca reserva:** além da OAB, a rotina busca pelo nome do advogado.
- **Segurança:** no app há "Trocar código e sair dos outros aparelhos".
- **Backup:** o bucket guarda versões antigas por 60 dias.
- **Vigia** (`.github/workflows/vigia.yml`): a cada 3 horas o GitHub consulta `/api/saude`. Se a última consulta ao DJEN tiver mais de 3 horas, abre uma issue e o GitHub avisa por e-mail. Ativo.
- **Implantação automática:** depois que os testes passam, cada envio à branch `main` é implantado no Google Cloud, por federação de identidade, sem chaves. Ativa; só reimplanta quando mudam `prazos/`, `app/`, `Dockerfile` ou `requirements-cloud.txt`.

## Versão de 13/11/2026 (tarde): redesenho

- **Abas inferiores:** Prazos, Calendário, Histórico e Ajustes, com botão **+** para cadastrar prazo avulso.
- **Tela Prazos:** destaque do próximo prazo e grupos Esta semana, Próxima semana, Depois e A conferir. Mostra os **dias úteis** restantes e o dia da semana.
- **Cartões:**
  - faixa de urgência na lateral;
  - nome das partes;
  - botão para copiar o número do processo;
  - trecho do prazo destacado no teor;
  - deslizar para a direita marca como cumprido (com "Desfazer").
- **Calendário mensal** com marcadores nos dias de fatal.
- **Sem internet:** o app abre e mostra os últimos dados salvos.
- **Feed de calendário** `/cal/<token>.ics` para o Google Agenda e o iPhone. O token é secreto e pode ser trocado em Ajustes.
- **Relatório** `/relatorio.html` com os próximos 14 dias, pronto para imprimir ou salvar em PDF. Toda segunda às 8h chega um aviso.
- **Leitura por IA** (`prazos/ia.py`): ativada com `bash ativar_ia.sh ID_DO_PROJETO`, que guarda a chave no Secret Manager.
  - Cada intimação ganha resumo, providência e prazo sugerido.
  - A sugestão nunca altera o prazo sozinha: aplica-se com um toque.

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
