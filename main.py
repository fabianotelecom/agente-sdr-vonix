"""SDR Vonix: webhook do bloco Agente de IA (Fluxos de Conversa do VonixCC).

Fluxo sincrono (200): recebe o turno, gera a resposta com o Google Gemini,
executa acoes de backend (agendamento no Google Calendar e aviso por e-mail) e
devolve no formato Vonix (messages / tags / variables / exit).

Contrato: https://vonix.vonixcc.com.br/v1/api-docs/agente-ia
"""

import os
import re
import json
import time
import secrets
import asyncio
import traceback
from datetime import datetime
from typing import List, Optional, Dict, Any, Literal

from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

from prompt import construir_system_instruction
import calendar_client
import email_client

app = FastAPI(title="SDR Vonix - Agente de IA")

client = genai.Client()

VONIX_TOKEN = os.environ.get("VONIX_TOKEN", "")
MODELS_TO_TRY = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-flash-lite-latest"]
TIME_BUDGET = 25

SYSTEM_INSTRUCTION = construir_system_instruction()

TOOLS_QUALIFICADO = ("agendar_demo", "transferir_vendedor")

# Link do material institucional (enviado como texto na conversa, pois o "Segue o
# fluxo" do bloco nao dispara com exit=null). Configuravel por env.
MATERIAL_URL = os.environ.get(
    "MATERIAL_URL",
    "https://drive.google.com/file/d/1yyFko-aEMEiFzRbrmtr1P5ia-5a56hkt/view?usp=sharing",
)


# --- Modelos de entrada (contrato Vonix) -----------------------------------
class Contact(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    user_id: Optional[str] = None


class IncomingMessage(BaseModel):
    turn_id: str
    chat_id: int
    node_id: str
    channel: str
    is_test: bool
    contact: Contact
    text: str
    messages: List[Dict[str, Any]]
    history: List[Dict[str, Any]]
    variables: Dict[str, Any]
    return_variables: List[str]
    tools: List[Dict[str, Any]]
    callback_url: Optional[str] = None
    callback_deadline: Optional[str] = None


# --- Schema interno do Gemini (structured output) --------------------------
class GeminiExit(BaseModel):
    tool: Optional[str] = Field(default=None, description="ID da ferramenta de saida (tool.id)")
    note: Optional[str] = Field(default=None, description="Resumo do caso para o vendedor")


class GeminiAgentResponse(BaseModel):
    messages: List[str] = Field(description="Textos para enviar ao contato")
    tags: Optional[List[str]] = Field(default=[], description="Tags do atendimento")
    exit: Optional[GeminiExit] = Field(default=None, description="Saida do bloco; null para continuar")
    intencao: Literal["conversando", "agendar", "confirmar", "desqualificado", "urgente"] = Field(
        default="conversando",
        description=(
            "conversando: segue a conversa. agendar: lead quer marcar e deve receber horarios. "
            "confirmar: lead escolheu um horario (preencha horario_escolhido). "
            "desqualificado: nao e prospect. urgente: lead pediu atendimento urgente."
        ),
    )
    horario_escolhido: Optional[str] = Field(
        default=None, description="ISO exato do horario escolhido, dentre os oferecidos no contexto"
    )
    resumo_lead: Optional[str] = Field(default=None, description="Resumo do lead para o vendedor")
    enviar_material: bool = Field(
        default=False,
        description=(
            "true apenas no turno em que o lead qualificado aceita receber o material. "
            "O fluxo da Vonix envia o arquivo; a conversa continua normalmente (exit nulo)."
        ),
    )
    nome_contato: Optional[str] = Field(default=None, description="Nome da pessoa com quem fala")
    empresa: Optional[str] = Field(default=None, description="Nome da empresa do lead")
    email_contato: Optional[str] = Field(default=None, description="E-mail do contato para o convite")
    n_operadores: Optional[str] = None
    canal_atual: Optional[str] = None
    volume_dia: Optional[str] = None
    dor_principal: Optional[str] = None
    concorrente_atual: Optional[str] = None


# --- Montagem do prompt do turno -------------------------------------------
def montar_user_prompt(payload: IncomingMessage, slots: List[datetime]) -> str:
    user_message = payload.text
    if not user_message and payload.messages:
        user_message = payload.messages[-1].get("text", "")
    if not user_message:
        user_message = "Ola"

    historico = (
        "\n".join(
            f"{h.get('role', 'contact')}: {h.get('text') or h.get('content', '')}"
            for h in payload.history
        )
        if payload.history
        else "Sem historico anterior."
    )

    ferramentas = (
        "\n".join(
            f"- id: {t.get('id')} | name: {t.get('name')} | type: {t.get('type')} | "
            f"description: {t.get('description', '')}"
            for t in payload.tools
        )
        if payload.tools
        else "Nenhuma ferramenta disponivel."
    )

    contato = payload.contact.name if payload.contact and payload.contact.name else "Desconhecido"

    bloco_slots = ""
    if slots:
        linhas = "\n".join(
            f"- {calendar_client.formatar_slot(s)} (ISO: {s.isoformat()})" for s in slots
        )
        bloco_slots = (
            "\n\nHorarios disponiveis para agendar (ofereca apenas estes; ao confirmar, "
            "use intencao=confirmar e preencha horario_escolhido com o ISO exato):\n" + linhas
        )

    return f"""
Dados do contato: {contato}
Variaveis atuais: {payload.variables}
Ferramentas disponiveis (use o id da ferramenta em exit.tool):
{ferramentas}

Historico recente:
{historico}

Nova mensagem do contato: {user_message}{bloco_slots}
""".strip()


async def chamar_gemini(user_prompt: str):
    start = time.monotonic()
    last_error = None
    max_retries = 3
    for model_name in MODELS_TO_TRY:
        for attempt in range(max_retries):
            if time.monotonic() - start >= TIME_BUDGET:
                return None, last_error
            try:
                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=model_name,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_INSTRUCTION,
                        response_mime_type="application/json",
                        response_schema=GeminiAgentResponse,
                        temperature=0.4,
                    ),
                )
                return response, None
            except Exception as api_err:  # noqa: BLE001
                last_error = api_err
                err = str(api_err)
                retryable = any(c in err for c in ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED"))
                elapsed = time.monotonic() - start
                if retryable and attempt < max_retries - 1:
                    delay = min((2 ** attempt) * 2, TIME_BUDGET - elapsed)
                    if delay <= 0:
                        break
                    await asyncio.sleep(delay)
                else:
                    break
    return None, last_error


# --- Acoes de backend ------------------------------------------------------
def _add_tag(data: dict, tag: str):
    tags = data.get("tags") or []
    if tag not in tags:
        tags.append(tag)
    data["tags"] = tags


_bg_tasks: set = set()


def _notificar_bg(texto: str):
    """Dispara o e-mail de aviso em segundo plano (nao bloqueia a resposta)."""
    try:
        task = asyncio.create_task(email_client.notificar(texto))
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)
    except RuntimeError:
        # Sem loop em execucao (ex.: testes sincronos): envia direto.
        asyncio.run(email_client.notificar(texto))


def _add_msg(data: dict, texto: str):
    data.setdefault("messages", [])
    data["messages"].append(texto)


def _remove_msgs(data: dict):
    """Descarta as mensagens que a IA gerou neste turno (ex.: confirmacao falsa)."""
    data["messages"] = []


def _resumo(data: dict, payload: IncomingMessage) -> str:
    partes = [data.get("resumo_lead") or ""]
    campos = {
        "Empresa": data.get("empresa"),
        "Contato": data.get("nome_contato") or payload.contact.name,
        "E-mail": data.get("email_contato"),
        "Operadores": data.get("n_operadores"),
        "Canais": data.get("canal_atual"),
        "Volume/dia": data.get("volume_dia"),
        "Dor": data.get("dor_principal"),
        "Concorrente": data.get("concorrente_atual"),
    }
    detalhes = ", ".join(f"{k}: {v}" for k, v in campos.items() if v)
    if detalhes:
        partes.append(detalhes)
    return " | ".join(p for p in partes if p).strip()


def _aviso(tipo: str, payload: IncomingMessage, resumo: str, extra: str = "", data: Optional[dict] = None) -> str:
    data = data or {}
    nome = data.get("nome_contato") or payload.contact.name or "sem nome"
    empresa = data.get("empresa")
    email = data.get("email_contato")
    fone = payload.contact.phone or "sem telefone"
    cabecalho = f"Contato: {nome}" + (f" ({empresa})" if empresa else "") + f" / {fone}"
    linhas = [f"Lead {tipo} ({payload.channel})", cabecalho]
    if email:
        linhas.append(f"E-mail: {email}")
    if resumo:
        linhas.append(f"Resumo: {resumo}")
    if extra:
        linhas.append(extra)
    return "\n".join(linhas)


def _tool_id(payload: IncomingMessage, nome_logico: str) -> Optional[str]:
    """A Vonix envia o id da tool como UUID; o 'name' e o rotulo que configuramos.
    Resolve o id real procurando pelo name (ou pelo proprio id) igual ao logico."""
    alvo = nome_logico.strip().lower()
    for t in payload.tools:
        if str(t.get("name", "")).strip().lower() == alvo:
            return t.get("id")
    for t in payload.tools:
        if str(t.get("id", "")).strip().lower() == alvo:
            return t.get("id")
    return None


def _match_slot(escolhido: Optional[datetime], slots: List[datetime]) -> Optional[datetime]:
    """Casa o horario escolhido (ISO do Gemini) com um slot oferecido, tolerando
    diferencas de formato/fuso. Compara ano/mes/dia/hora no fuso do slot."""
    if not escolhido:
        return None
    for s in slots:
        e = escolhido if escolhido.tzinfo else escolhido.replace(tzinfo=s.tzinfo)
        e = e.astimezone(s.tzinfo)
        if (s.year, s.month, s.day, s.hour) == (e.year, e.month, e.day, e.hour):
            return s
    return None


_DIAS_SEMANA = {"segunda": 0, "terca": 1, "terça": 1, "quarta": 2, "quinta": 3, "sexta": 4}


def _match_slot_from_text(texto: str, slots: List[datetime]) -> Optional[datetime]:
    """Tenta identificar, no texto do contato, qual slot ele escolheu. Reconhece
    hora (11, 11h, 11:00, '11 horas'), dia da semana e data dd/mm."""
    if not texto or not slots:
        return None
    t = texto.lower()
    # hora: padroes explicitos primeiro
    horas = set(int(h) for h in re.findall(r'(\d{1,2})\s*(?:h|:\s*\d{2}|horas|hrs|hs)', t))
    data_dm = None
    m = re.search(r'\b(\d{1,2})/(\d{1,2})', t)
    if m:
        data_dm = (int(m.group(1)), int(m.group(2)))
    if not horas:  # fallback: numeros isolados plausiveis como hora comercial
        for n in re.findall(r'\b(\d{1,2})\b', t):
            h = int(n)
            if 7 <= h <= 18 and (not data_dm or h not in data_dm):
                horas.add(h)
    dia_idx = next((v for k, v in _DIAS_SEMANA.items() if k in t), None)
    if not horas:
        return None
    candidatos = []
    for s in slots:
        if s.hour not in horas:
            continue
        if data_dm and (s.day, s.month) != data_dm:
            continue
        if dia_idx is not None and s.weekday() != dia_idx:
            continue
        candidatos.append(s)
    return candidatos[0] if candidatos else None


def _texto_oferta(slots: List[datetime]) -> str:
    return (
        "Tenho estes horarios disponiveis:\n"
        + "\n".join(f"- {calendar_client.formatar_slot(s)}" for s in slots)
        + "\nQual fica melhor pra voce?"
    )


def _parse_iso(s: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


async def aplicar_acoes(data: dict, payload: IncomingMessage, fase: str, slots: List[datetime]) -> str:
    """Executa as acoes de backend conforme a intencao. Retorna a nova fase."""
    intencao = data.get("intencao") or "conversando"
    resumo = _resumo(data, payload)

    # Envio do material pela propria conversa (link em texto). Ordem garantida:
    # 1) link, 2) pedido de e-mail. (O "Segue o fluxo" do bloco nao dispara com
    # exit=null, por isso o envio vai por aqui, com mensagens deterministicas.)
    if data.get("enviar_material") and MATERIAL_URL:
        _remove_msgs(data)
        _add_msg(data, f"Perfeito! Segue o nosso material institucional: {MATERIAL_URL}")
        _add_msg(
            data,
            "Para que eu possa enviar o convite da nossa apresentacao com a agenda, "
            "qual e o seu melhor e-mail?",
        )
        data["exit"] = None
        return fase

    if intencao == "urgente":
        tid = _tool_id(payload, "transferir_vendedor")
        data["exit"] = {"tool": tid or "transferir_vendedor", "note": resumo or None}
        _add_tag(data, "urgente")
        _notificar_bg(_aviso("URGENTE", payload, resumo, data=data))
        return "triagem"

    if intencao == "desqualificado":
        _add_tag(data, "desqualificado")
        _remove_msgs(data)
        _add_msg(
            data,
            "Pelo que entendi, neste momento a Vonix não seria o melhor encaixe para "
            "o que você precisa. Agradeço bastante o seu contato!\n"
            "Vou encerrar este atendimento por aqui; se precisar no futuro, é só enviar "
            "uma nova mensagem que inicio um novo atendimento. Até breve!\n\n"
            "#AtendimentoEncerrado",
        )
        tid = _tool_id(payload, "encerrar_atendimento")
        data["exit"] = {"tool": tid, "note": resumo or "Lead sem fit"} if tid else None
        _notificar_bg(_aviso("DESQUALIFICADO", payload, resumo, data=data))
        return "triagem"

    # --- Agendamento: conduzido pelo backend (a IA nao lista nem confirma) ----
    # 1) Inicio: lead qualificado quer marcar e ja temos o e-mail. O backend
    #    descarta qualquer horario inventado pela IA e injeta a lista real.
    if fase != "agendando" and intencao == "agendar":
        email = data.get("email_contato") or payload.variables.get("email_contato")
        if not email:
            data["exit"] = None
            return fase  # ainda sem e-mail: o agente pede o e-mail primeiro
        livres = slots
        if not livres:
            try:
                livres = await asyncio.to_thread(calendar_client.listar_horarios_livres)
            except Exception as e:  # noqa: BLE001
                print(f"[calendar] erro ao listar horarios: {type(e).__name__}")
                livres = []
        _remove_msgs(data)
        data["exit"] = None
        if livres:
            _add_msg(data, _texto_oferta(livres))
            return "agendando"
        _add_msg(data, "No momento nao encontrei horarios livres. Qual dia e periodo ficam melhores pra voce?")
        return fase

    # 2) Em agendamento: o backend identifica o horario (campo da IA ou texto do
    #    contato) e so ai confirma de verdade. Nunca deixa a IA "agendar" sozinha.
    if fase == "agendando":
        escolhido = _parse_iso(data.get("horario_escolhido") or "")
        alvo = _match_slot(escolhido, slots) or _match_slot_from_text(payload.text, slots)
        if alvo is not None:
            nome = data.get("nome_contato") or payload.contact.name or "Lead"
            empresa = data.get("empresa")
            titulo = f"Apresentacao Vonix - {empresa or nome}"
            try:
                evento = await asyncio.to_thread(
                    calendar_client.criar_evento,
                    alvo, titulo, resumo or "Apresentacao Vonix",
                    data.get("email_contato"),
                )
            except Exception as e:  # noqa: BLE001
                print(f"[calendar] erro ao criar evento: {type(e).__name__}")
                _remove_msgs(data)
                data["exit"] = None
                _add_msg(data, "Tive um problema ao confirmar o agendamento. Pode escolher outro horario?")
                return "agendando"
            quando = calendar_client.formatar_slot(alvo)
            tid = _tool_id(payload, "agendar_demo")
            data["exit"] = {"tool": tid or "agendar_demo", "note": f"Agendado: {quando}"}
            data["data_agendada"] = quando
            _add_tag(data, "agendado")
            _remove_msgs(data)
            _add_msg(
                data,
                f"Perfeito! Sua apresentação está agendada para {quando}. "
                "Enviei o convite com os detalhes para o seu e-mail.\n"
                "Vou encerrar esse Atendimento por aqui, se você enviar uma nova "
                "mensagem eu inicio um novo atendimento, ok? Até lá!\n\n"
                "#AtendimentoEncerrado",
            )
            link = evento.get("htmlLink", "")
            _notificar_bg(
                _aviso("QUALIFICADO (agendado)", payload, resumo, f"Reuniao: {quando}\n{link}", data=data)
            )
            return "triagem"
        # Nao identificou horario valido: nao deixa a IA confirmar sozinha; reoferece.
        _remove_msgs(data)
        data["exit"] = None
        if slots:
            _add_msg(data, "Esse horario nao esta na lista. " + _texto_oferta(slots))
        else:
            _add_msg(data, "Qual dia e periodo ficam melhores pra voce?")
        return "agendando"

    return fase


def formatar_resposta(data: dict, payload: IncomingMessage, nova_fase: str) -> dict:
    exit_data = data.get("exit") or {}

    if payload.return_variables:
        variaveis = {}
        mapa_simples = {
            "note": data.get("resumo_lead") or exit_data.get("note"),
            "nome_contato": data.get("nome_contato"),
            "empresa": data.get("empresa"),
            "email_contato": data.get("email_contato"),
            "n_operadores": data.get("n_operadores"),
            "canal_atual": data.get("canal_atual"),
            "volume_dia": data.get("volume_dia"),
            "dor_principal": data.get("dor_principal"),
            "concorrente_atual": data.get("concorrente_atual"),
            "data_agendada": data.get("data_agendada"),
            "fase": nova_fase,
        }
        for nome in payload.return_variables:
            valor = mapa_simples.get(nome)
            if valor not in (None, ""):
                variaveis[nome] = valor
        if "qualificado" in payload.return_variables and exit_data.get("tool"):
            variaveis["qualificado"] = exit_data.get("tool") in TOOLS_QUALIFICADO
        if "enviar_material" in payload.return_variables:
            # "Sim"/"Nao" batendo com a regra Decidir da Vonix ("que contem Sim").
            variaveis["enviar_material"] = "Sim" if data.get("enviar_material") else "Nao"
        if variaveis:
            data["variables"] = variaveis

    if isinstance(data.get("messages"), list):
        data["messages"] = [
            {"type": "TEXT", "text": m}
            for m in data["messages"]
            if m and m.strip()
        ][:10]

    # Remove campos internos que nao fazem parte do contrato Vonix.
    for chave in ("intencao", "horario_escolhido", "resumo_lead", "enviar_material",
                  "nome_contato", "empresa", "email_contato",
                  "n_operadores", "canal_atual", "volume_dia", "dor_principal",
                  "concorrente_atual", "data_agendada"):
        data.pop(chave, None)

    return data


@app.post("/webhook/sdr")
async def handle_turn(
    payload: IncomingMessage,
    authorization: str = Header(default=""),
    x_vonix_turn_id: Optional[str] = Header(default=None),
    x_vonix_attempt: Optional[int] = Header(default=None),
):
    if not payload.is_test:
        recebido = authorization.removeprefix("Bearer ").strip()
        if not VONIX_TOKEN or not secrets.compare_digest(recebido, VONIX_TOKEN):
            raise HTTPException(status_code=401, detail="Token invalido")

    if x_vonix_attempt == 2:
        print(f"[retry] retentativa do turno {x_vonix_turn_id or payload.turn_id}")

    try:
        fase = str(payload.variables.get("fase") or "triagem")

        # Em fase de agendamento, injeta os horarios livres atuais no contexto.
        slots: List[datetime] = []
        if fase == "agendando":
            try:
                slots = await asyncio.to_thread(calendar_client.listar_horarios_livres)
            except Exception as e:  # noqa: BLE001
                print(f"[calendar] erro ao listar horarios: {type(e).__name__}")

        response, last_error = await chamar_gemini(montar_user_prompt(payload, slots))

        if not response:
            err = str(last_error) if last_error else "erro desconhecido"
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                raise HTTPException(status_code=429, detail=err, headers={"Retry-After": "60"})
            if "503" in err or "UNAVAILABLE" in err:
                raise HTTPException(status_code=503, detail=err, headers={"Retry-After": "1"})
            raise HTTPException(status_code=500, detail=err)

        data = json.loads(response.text)
        nova_fase = await aplicar_acoes(data, payload, fase, slots)
        return formatar_resposta(data, payload, nova_fase)

    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
def health_check():
    return {"status": "online", "service": "SDR Vonix - Agente de IA"}
