# Agente SDR Vonix

Agente de pre-vendas (SDR) para WhatsApp, construido sobre a feature de
Fluxos de Conversa (bloco Agente de IA) do VonixCC. O backend expoe um webhook
que o VonixCC aciona a cada turno; a resposta e gerada com o Google Gemini e
devolvida no formato Vonix.

Objetivo: captar e qualificar leads e, havendo fit, agendar uma demonstracao
com o time de vendas.

## Arquitetura

- FastAPI expondo `POST /webhook/sdr` (fluxo sincrono, responde em ate 30s).
- Google Gemini com structured output (messages, tags, exit + campos de controle).
- Autenticacao por segredo compartilhado (Bearer), isentando turnos de teste.
- Base de conhecimento de produto em `knowledge/vonix_sdr_kb.md`, carregada na
  instrucao de sistema (editavel sem mexer no codigo).
- Agendamento da apresentacao no Google Calendar (OAuth refresh token, janela
  09:00-16:00, reunioes de 1h, dias uteis).
- Avisos por e-mail (SMTP/Gmail): lead qualificado apos agendar e lead
  desqualificado na hora.
- Deploy no Render via `Procfile`.

## Estrutura

```
main.py                     webhook + Gemini + orquestracao (Calendar/E-mail) + formato Vonix
prompt.py                   persona, regras e carregamento da base de conhecimento
calendar_client.py          Google Calendar: listar horarios livres e criar evento
email_client.py             E-mail: notificacoes via SMTP (Gmail)
knowledge/vonix_sdr_kb.md   base de conhecimento (produto Vonix) [rascunho]
docs/qualificacao.md        framework de qualificacao e roteiro
docs/integracoes.md         design e decisoes das integracoes Calendar/E-mail
docs/config-bloco-vonix.md  como configurar o bloco no VonixCC
```

## Configuracao

Variaveis de ambiente (veja `.env.example`):
- `GOOGLE_API_KEY`: chave da API do Gemini.
- `VONIX_TOKEN`: segredo compartilhado com o bloco da Vonix (`openssl rand -hex 32`).
- `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` / `GOOGLE_OAUTH_REFRESH_TOKEN`:
  credenciais OAuth da conta Google que recebe as reunioes. `GOOGLE_CALENDAR_ID`
  (padrao `primary`) e `TIMEZONE` (padrao `America/Sao_Paulo`).
- `SMTP_USER` / `SMTP_APP_PASSWORD` / `ALERTA_EMAIL_DESTINO`: envio dos avisos
  por e-mail (Gmail com senha de app). `SMTP_HOST` (padrao `smtp.gmail.com`) e
  `SMTP_PORT` (padrao `587`).

O refresh token OAuth e gerado uma vez com o consentimento da conta Google
(escopo `calendar`). Posso te guiar nesse passo quando formos ligar em producao.

## Execucao local

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

## Observacoes

- `MODELS_TO_TRY` (main.py) usa `gemini-3.5-flash` (principal), com fallback para
  `gemini-3.5-flash-lite` e `gemini-flash-lite-latest`. Modelos confirmados em
  30/10/2026 (os 2.x sairam de linha para generateContent). Structured output
  validado com o schema do agente.
- Os headers `X-Vonix-Turn-Id` e `X-Vonix-Attempt` sao tratados como opcionais
  (o contrato oficial marca apenas `Authorization` como obrigatorio). A confirmar
  com o time da Vonix.
- Contrato da feature: https://vonix.vonixcc.com.br/v1/api-docs/agente-ia
- Referencia: https://github.com/franciellelopescosta/AgenteIAEcommerce
