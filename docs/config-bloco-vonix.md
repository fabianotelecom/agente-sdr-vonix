# Como configurar o bloco Agente de IA no VonixCC

Guia para ligar este backend ao Fluxo de Conversa do VonixCC.

## 1. Deploy do backend (Render)

1. Suba este repositorio como Web Service no Render.
2. Configure as variaveis de ambiente:
   - `GOOGLE_API_KEY`: chave da API do Gemini.
   - `VONIX_TOKEN`: o segredo compartilhado (gere com `openssl rand -hex 32`).
3. A URL do webhook sera: `https://<seu-servico>.onrender.com/webhook/sdr`.

## 2. Autenticacao (segredo compartilhado)

- O mesmo valor de `VONIX_TOKEN` do servidor vai no bloco da Vonix, no atalho Bearer.
- Em turnos reais, a Vonix envia `Authorization: Bearer <token>`; o backend valida e responde 401 se nao bater (o chat cai no Fallback).
- Turnos de teste (`is_test: true`) sao isentos de token (o testador de fluxo nao envia Authorization).

## 3. Acumulador do turno (fechamento do bloco)

Sugestao inicial para WhatsApp:
- Aguardar silencio: ~8 a 10s.
- Maximo de mensagens: ~5.
- Espera maxima: ~30s.

## 4. Ferramentas (saidas) a criar no bloco

Crie no bloco as ferramentas com estes `id` (o agente preenche `exit.tool` com o id):

| id | Para onde leva |
|---|---|
| `transferir_vendedor` | Transferencia imediata para vendedor (lead qualificado urgente) |
| `agendar_demo` | Confirmacao da apresentacao agendada (evento criado pelo backend) |
| `enviar_material` | Passo do fluxo que envia o arquivo de material ao lead qualificado |

Garanta tambem uma saida de Fallback (erros, 401, timeout caem nela).

Observacao: a notificacao ao Slack (lead qualificado apos agendar e lead
desqualificado na hora) e feita pelo backend, nao e uma tool do bloco. O
agendamento no Google Calendar tambem e feito pelo backend. Veja
[integracoes.md](integracoes.md).

## 5. Variaveis de retorno (return_variables)

Liste no bloco as variaveis que o agente pode gravar. Minimo recomendado:
`note` (resumo para o vendedor), `qualificado` (booleano) e `fase` (controle
interno do agendamento). Opcionais: `n_operadores`, `canal_atual`, `volume_dia`,
`dor_principal`, `concorrente_atual`, `data_agendada`.

IMPORTANTE: inclua `fase` em `return_variables`. O backend usa `fase`
(`triagem` / `agendando`) para saber quando buscar e oferecer horarios do
Calendar entre os turnos. Sem ela, o agendamento multi-turno nao mantem estado.

> O backend hoje grava `note` e `qualificado`. As demais podem ser habilitadas
> conforme evoluirmos a extracao estruturada.

## 6. Resposta do agente (contrato)

O backend devolve JSON com `messages` (ate 10, tipo TEXT), `tags`, `variables`
(so chaves de `return_variables`) e `exit` ({tool, note} ou null). Ordem de
aplicacao na Vonix: variables, tags, messages, exit.

## 7. Teste

Use o botao "Testar requisicao" do bloco (manda `is_test: true`, sem token) para
validar o fluxo antes de ligar em producao.
