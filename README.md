# Agente SDR Vonix

Agente de pre-vendas (SDR) para WhatsApp, construido sobre a feature de
Fluxos de Conversa (bloco Agente de IA) do VonixCC. O backend expoe um webhook
que o VonixCC aciona a cada turno; a resposta e gerada com o Google Gemini e
devolvida no formato Vonix.

Objetivo: captar e qualificar leads e, havendo fit, agendar uma demonstracao
com o time de vendas.

## Arquitetura

- FastAPI expondo `POST /webhook/sdr` (fluxo sincrono, responde em ate 30s).
- Google Gemini com structured output (schema: messages, tags, exit).
- Autenticacao por segredo compartilhado (Bearer), isentando turnos de teste.
- Base de conhecimento de produto em `knowledge/vonix_sdr_kb.md`, carregada na
  instrucao de sistema (editavel sem mexer no codigo).
- Deploy no Render via `Procfile`.

## Estrutura

```
main.py                     webhook + integracao Gemini + formatacao Vonix
prompt.py                   persona, regras e carregamento da base de conhecimento
knowledge/vonix_sdr_kb.md   base de conhecimento (produto Vonix) [rascunho]
docs/qualificacao.md        framework de qualificacao e roteiro [rascunho]
docs/config-bloco-vonix.md  como configurar o bloco no VonixCC
```

## Configuracao

Variaveis de ambiente (veja `.env.example`):
- `GOOGLE_API_KEY`: chave da API do Gemini.
- `VONIX_TOKEN`: segredo compartilhado com o bloco da Vonix (`openssl rand -hex 32`).

## Execucao local

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

## Observacoes

- Os nomes de modelo em `MODELS_TO_TRY` (main.py) devem ser confirmados conforme
  a disponibilidade atual do Gemini.
- Os headers `X-Vonix-Turn-Id` e `X-Vonix-Attempt` sao tratados como opcionais
  (o contrato oficial marca apenas `Authorization` como obrigatorio). A confirmar
  com o time da Vonix.
- Contrato da feature: https://vonix.vonixcc.com.br/v1/api-docs/agente-ia
- Referencia: https://github.com/franciellelopescosta/AgenteIAEcommerce
