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
| `agendar_demo` | Fila/agente de vendas (ou integracao de agenda) para a demonstracao |
| `nutrir_lead` | Envio de material e encerramento |
| `falar_com_humano` | Transferencia para atendente humano |
| `fora_de_escopo` | Encerramento / outro fluxo (cliente atual, suporte) |

Garanta tambem uma saida de Fallback (erros, 401, timeout caem nela).

## 5. Variaveis de retorno (return_variables)

Liste no bloco as variaveis que o agente pode gravar. Minimo recomendado:
`note` (resumo para o vendedor) e `qualificado` (booleano). Opcionais:
`empresa`, `segmento`, `n_operadores`, `canal_atual`, `volume`,
`plataforma_atual`, `dor_principal`, `objetivo`, `papel_contato`, `urgencia`.

> O backend hoje grava `note` e `qualificado`. As demais podem ser habilitadas
> conforme evoluirmos a extracao estruturada.

## 6. Resposta do agente (contrato)

O backend devolve JSON com `messages` (ate 10, tipo TEXT), `tags`, `variables`
(so chaves de `return_variables`) e `exit` ({tool, note} ou null). Ordem de
aplicacao na Vonix: variables, tags, messages, exit.

## 7. Teste

Use o botao "Testar requisicao" do bloco (manda `is_test: true`, sem token) para
validar o fluxo antes de ligar em producao.
