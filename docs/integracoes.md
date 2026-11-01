# Integrações: Google Calendar e E-mail

Design das duas integrações de backend do SDR, com as decisões já tomadas.

## 1. Google Calendar (agendamento da apresentação)

### O que o backend faz
1. Quando o lead qualificado quer agendar (e já deu o e-mail), o backend propõe
   UMA vaga: o primeiro horário livre do próximo dia útil (slot de 1h, 09:00-16:00).
2. Se o lead pedir outro dia (hoje, uma data, um dia da semana), o backend consulta
   a agenda daquele dia e lista as opções livres dele.
3. O lead aceita a vaga ("pode ser") ou escolhe um horário; o backend cria o evento
   de 1 hora e confirma. Fim de semana: responde que só atende em dias úteis.
4. O backend notifica por e-mail (ver seção 2).

O "dia em contexto" (vaga proposta ou dia listado) viaja no próprio valor da
variável `fase` (`agendando|s|<ISO>` ou `agendando|d|<AAAA-MM-DD>`), para o turno
seguinte saber a que dia uma hora solta ("as 14h") se refere, sem variável extra.

### Sobre "usar a Gemini para acessar o Google"
O Gemini gera a conversa, mas quem acessa a agenda é o backend, via Google
Calendar API (biblioteca oficial). O fluxo é determinístico (consulta de
free/busy e criação de evento), para ser confiável e caber no tempo do turno.

### Autenticação (DEFINIDO)
Conta Gmail pessoal (fabiano.aam@gmail.com) com OAuth + refresh token. O
consentimento é feito uma vez (escopo `https://www.googleapis.com/auth/calendar`)
pelo script `scripts/gerar_refresh_token.py`; o refresh token fica como variável
de ambiente. Cliente OAuth do tipo "App para computador" no projeto
`agente-sdr-vonix` do Google Cloud.

Status: refresh token gerado e validado (listagem de horários livres testada
contra a agenda real).

### Estado da conversa (multi-turno)
Usamos `chat_id` como id de sessão e a variável de fluxo `fase`
(`triagem` / `agendando`) para saber quando buscar e oferecer horários entre os
turnos. Por isso `fase` precisa estar em `return_variables` no bloco.

## 2. E-mail (notificações ao Fabiano)

### Quando notifica
- Lead QUALIFICADO (após agendar): resumo do lead + horário marcado + link do evento.
- Lead DESQUALIFICADO: resumo do lead, imediatamente.
- Lead URGENTE: resumo do lead (além de transferir_vendedor).

### Método (DEFINIDO)
Envio por SMTP com Gmail (senha de app). Implementado em `email_client.py`
(biblioteca padrão `smtplib`, sem dependência extra). A primeira linha do aviso
vira o assunto (prefixado com `[SDR Vonix]`); o restante vai no corpo.

Decisão: trocamos o Slack por e-mail porque o workspace Slack disponível
atingiu o limite de 10 apps do plano gratuito. E-mail é autocontido e não
depende de limite de apps. WhatsApp (via API da própria Vonix) fica como
evolução futura possível.

Origem x destino: o SMTP envia pela conta fabiano.aam@gmail.com
(`SMTP_USER`), mas os avisos vão para contato@vonix.com.br
(`ALERTA_EMAIL_DESTINO`). São independentes.

## 3. Variáveis de ambiente

```
# Google Calendar (OAuth, Gmail pessoal)
GOOGLE_OAUTH_CLIENT_ID=...
GOOGLE_OAUTH_CLIENT_SECRET=...
GOOGLE_OAUTH_REFRESH_TOKEN=...
GOOGLE_CALENDAR_ID=primary
TIMEZONE=America/Sao_Paulo

# E-mail (SMTP / Gmail com senha de app)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=fabiano.aam@gmail.com       # origem (quem envia)
SMTP_APP_PASSWORD=...
ALERTA_EMAIL_DESTINO=contato@vonix.com.br   # destino dos avisos
```

## 4. Pontos a confirmar

1. Fuso horário da janela 09:00 a 16:00: adotado America/Sao_Paulo.
2. Dias válidos para agendar: dias úteis (seg a sex), excluindo feriados
   nacionais do Brasil (biblioteca `holidays`, cobre móveis como Carnaval e
   Corpus Christi).
3. Senha de app do Gmail para SMTP: a gerar (2FA + senha de app).
