# Integrações: Google Calendar e Slack

Este documento descreve o design das duas integrações do SDR e as decisões que
precisam ser confirmadas antes da implementação.

## 1. Google Calendar (agendamento da apresentação)

### O que o backend faz
1. Quando o lead qualificado quer agendar, o backend consulta a agenda e lista
   os horários livres (slots de 1h) entre 09:00 e 16:00 nos próximos dias úteis.
2. O agente apresenta 2 ou 3 opções ao lead pelo WhatsApp.
3. O lead escolhe; o backend cria o evento de 1 hora e confirma o horário.
4. O backend notifica o Slack.

### Observação sobre "usar a Gemini para acessar o Google"
O Gemini gera a conversa, mas quem acessa a agenda é o backend, via Google
Calendar API (biblioteca oficial). O fluxo é determinístico (consulta de
free/busy e criação de evento), para ser confiável e caber no tempo do turno.
Opcionalmente, podemos expor isso ao Gemini como function calling; na primeira
versão, o backend controla as etapas por estado da conversa.

### Autenticação (DECISÃO NECESSÁRIA)
A forma de autenticar o backend no Google depende do tipo da conta que vai
receber as reuniões:
- Conta Workspace (ex.: fabiano@vonix.com.br): conta de serviço com delegação
  de domínio, ou OAuth. Mais controlável para empresa.
- Gmail pessoal (ex.: ...@gmail.com): OAuth com refresh token (fluxo de consentimento
  uma vez; guardamos o refresh token como env var). Conta de serviço não acessa
  agenda de Gmail pessoal diretamente.

Escopo necessário: `https://www.googleapis.com/auth/calendar` (ou
`calendar.events` + `calendar.readonly`).

### Estado da conversa (multi-turno)
Usamos `chat_id` como id de sessão e variáveis do fluxo para guardar a fase
(ex.: `fase=aguardando_escolha_horario`) e os slots oferecidos, já que o
agendamento acontece ao longo de alguns turnos.

## 2. Slack (notificações ao Fabiano)

### Quando notifica
- Lead QUALIFICADO (após agendar): resumo do lead + horário marcado.
- Lead DESQUALIFICADO: resumo do lead, imediatamente.

### Método (DECISÃO NECESSÁRIA)
- Incoming Webhook: simples, posta em um canal fixo. Basta uma URL como env var.
  Recomendado para começar.
- Bot token (chat.postMessage): permite DM e vários canais, exige app Slack e
  escopos. Mais flexível.

### Destino (DECISÃO NECESSÁRIA)
- Um canal dedicado (ex.: #sdr-leads), ou
- DM direta para o Fabiano.

## 3. Variáveis de ambiente previstas (a adicionar)

```
# Google Calendar
GOOGLE_CALENDAR_ID=primary            # ou o id do calendário dedicado
GOOGLE_OAUTH_CLIENT_ID=...            # se OAuth
GOOGLE_OAUTH_CLIENT_SECRET=...        # se OAuth
GOOGLE_OAUTH_REFRESH_TOKEN=...        # se OAuth (gmail pessoal)
# ou, se conta de serviço:
GOOGLE_SERVICE_ACCOUNT_JSON=...       # conteúdo/credencial da service account

# Slack
SLACK_WEBHOOK_URL=...                 # se Incoming Webhook
# ou
SLACK_BOT_TOKEN=...                   # se bot
SLACK_CHANNEL=#sdr-leads              # destino
```

## 4. Decisões pendentes (resumo)

1. Conta/calendário que recebe as reuniões (Workspace vonix.com.br ou Gmail pessoal).
2. Método de auth no Google (conta de serviço com delegação ou OAuth refresh token).
3. Método do Slack (Incoming Webhook ou bot token).
4. Destino no Slack (canal dedicado ou DM).
5. Fuso horário da empresa para a janela 09:00 a 16:00 (presumo America/Sao_Paulo).
6. Dias válidos para agendar (presumo dias úteis, seg a sex).
