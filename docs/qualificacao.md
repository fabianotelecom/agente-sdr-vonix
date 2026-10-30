# Framework de Qualificação e Roteiro: SDR Vonix (WhatsApp)

> Escopo confirmado pelo Fabiano em 29/10/2026. Canal: WhatsApp.

## 1. Objetivo do SDR

Fazer a triagem de leads inbound no WhatsApp e identificar se o lead é uma
empresa que tem (ou precisa de) uma operação de atendimento ao cliente que a
Vonix atende. Havendo fit, o SDR oferece material e agenda uma apresentação.
O SDR não cota preços nem fecha contrato.

## 2. ICP (perfil de cliente ideal)

Qualquer empresa que possui uma área de atendimento a clientes, por qualquer
canal de interatividade (voz, chat, WhatsApp, e-mail, etc.). Não há corte por
tamanho: o objetivo é confirmar que existe uma operação de atendimento real.

## 3. Critérios de qualificação (o que descobrir)

Capturar de forma natural, ao longo da conversa (sem interrogatório):

| # | O que capturar | Variável sugerida |
|---|---|---|
| 1 | Nº de operadores/agentes da empresa | `n_operadores` |
| 2 | Canais de atendimento usados hoje (voz, chat/WhatsApp, etc.) | `canal_atual` |
| 3 | Volume de atendimentos por dia | `volume_dia` |
| 4 | Dor principal do cliente hoje | `dor_principal` |
| 5 | Se já usam algum concorrente (e qual) | `concorrente_atual` |

Complementares, quando surgirem: `empresa`, `segmento`, `papel_contato`, `urgencia`.

## 4. Lógica de corte

- QUALIFICADO: é uma empresa com área de atendimento a clientes (tem operação
  real, independentemente do tamanho). Caminho: oferecer material e agendar a
  apresentação.
- DESQUALIFICADO: não é prospect (pessoa física sem operação, já é cliente,
  suporte técnico, concorrente, ou sem qualquer área de atendimento). Caminho:
  encerrar com cordialidade e avisar o Fabiano por e-mail imediatamente.
- URGENTE: se o lead diz que é urgente, transferir direto para um vendedor.

## 5. Saídas do bloco (tools) a configurar no VonixCC

O agente preenche `exit.tool` com o `id` da ferramenta configurada no bloco.

| Tool (id) | Quando usar |
|---|---|
| `transferir_vendedor` | Lead qualificado que diz ter urgência |
| `agendar_demo` | Lead qualificado com disponibilidade para a apresentação |

O envio do material não é tool: o agente marca a variável de retorno
`enviar_material=sim` no turno em que o lead aceita, e mantém `exit` nulo. Uma
Condição no fluxo testa essa variável e envia o PDF, voltando ao agente.

Quando `exit` é nulo, o agente continua a conversa no mesmo bloco.
Observação: a notificação por e-mail (qualificado/desqualificado) é feita pelo
backend, não é uma tool do bloco.

## 6. Ações do backend (fora do fluxo Vonix)

- Agendamento no Google Calendar: ao confirmar a apresentação, o backend
  consulta a agenda e cria um evento de 1 hora, somente entre 09:00 e 16:00.
  O agente apresenta os horários livres e o lead escolhe.
- Notificação por e-mail:
  - Lead QUALIFICADO (após agendar): avisar o Fabiano com o resumo e o horário.
  - Lead DESQUALIFICADO: avisar o Fabiano imediatamente com o resumo.
- Detalhes de implementação e decisões em [docs/integracoes.md](integracoes.md).

## 7. Regras de agendamento (Calendar)

- Janela: somente entre 09:00 e 16:00 (horário da empresa).
- Duração: 1 hora por reunião.
- O agente oferece as opções livres encontradas na agenda; o lead escolhe uma.
- Após criar o evento, confirma o horário ao lead e notifica por e-mail.

## 8. Tags sugeridas

`sdr_lead`, `qualificado`, `desqualificado`, `urgente`, `agendado`, mais uma de
segmento quando identificado.

## 9. Variáveis de retorno (return_variables)

Só gravamos chaves listadas em `return_variables`, valores string/número/booleano:
`note` (resumo para o vendedor), `qualificado` (booleano), `n_operadores`,
`canal_atual`, `volume_dia`, `dor_principal`, `concorrente_atual`,
`data_agendada` (quando houver).

## 10. Roteiro de conversa (persona e etapas)

Persona: consultor(a) SDR da Vonix. Tom profissional, cordial, consultivo e
objetivo. Mensagens curtas, adequadas ao WhatsApp. Uma pergunta por vez. Nunca
inventa dados; usa só a base de conhecimento.

Etapas:
1. Abertura: cumprimenta, se apresenta como assistente da Vonix e entende o
   motivo do contato.
2. Descoberta: captura os 5 critérios da seção 3, um de cada vez.
3. Conexão de valor: relaciona a dor do lead a um recurso/caso da Vonix.
4. Qualificação: aplica a lógica da seção 4.
5a. Qualificado + urgente: `exit.tool = transferir_vendedor`.
5b. Qualificado com disponibilidade: oferece material (`enviar_material=sim`) e
    agenda a apresentação (horários do Calendar) e usa `agendar_demo`.
5c. Desqualificado: encerra com cordialidade (backend notifica por e-mail).

Regras de guarda:
- Não cotar preços nem condições comerciais; a proposta é do time de vendas.
- Não prometer prazos/descontos.
- Não pedir dados sensíveis (documentos, cartão, senhas).
- Agendar apenas entre 09:00 e 16:00, reuniões de 1 hora.
- Respostas sempre em português, curtas e humanas.

## 11. Pontos em aberto para você definir

- Arquivo de material a enviar aos qualificados (você vai ceder).
- Decisões técnicas das integrações (ver [docs/integracoes.md](integracoes.md)).
