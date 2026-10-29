"""SDR Vonix: webhook do bloco Agente de IA (Fluxos de Conversa do VonixCC).

Fluxo sincrono (200): recebe o turno, gera a resposta com o Google Gemini,
executa acoes de backend (agendamento no Google Calendar e aviso no Slack) e
devolve no formato Vonix (messages / tags / variables / exit).

Contrato: https://vonix.vonixcc.com.br/v1/api-docs/agente-ia
"""

import os
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
import slack_client

app = FastAPI(title="SDR Vonix - Agente de IA")

client = genai.Client()

VONIX_TOKEN = os.environ.get("VONIX_TOKEN", "")
MODELS_TO_TRY = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"]
TIME_BUDGET = 25

SYSTEM_INSTRUCTION = construir_system_instruction()

TOOLS_QUALIFICADO = ("agendar_demo", "transferir_vendedor", "enviar_material")


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


def _add_msg(data: dict, texto: str):
    data.setdefault("messages", [])
    data["messages"].append(texto)


def _resumo(data: dict, payload: IncomingMessage) -> str:
    partes = [data.get("resumo_lead") or ""]
    campos = {
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


def _aviso_slack(tipo: str, payload: IncomingMessage, resumo: str, extra: str = "") -> str:
    nome = payload.contact.name or "sem nome"
    fone = payload.contact.phone or "sem telefone"
    linhas = [f"*Lead {tipo}* ({payload.channel})", f"Contato: {nome} / {fone}"]
    if resumo:
        linhas.append(f"Resumo: {resumo}")
    if extra:
        linhas.append(extra)
    return "\n".join(linhas)


def _parse_iso(s: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


async def aplicar_acoes(data: dict, payload: IncomingMessage, fase: str, slots: List[datetime]) -> str:
    """Executa as acoes de backend conforme a intencao. Retorna a nova fase."""
    intencao = data.get("intencao") or "conversando"
    resumo = _resumo(data, payload)

    if intencao == "urgente":
        data["exit"] = {"tool": "transferir_vendedor", "note": resumo or None}
        _add_tag(data, "urgente")
        await slack_client.notificar(_aviso_slack("URGENTE", payload, resumo))
        return "triagem"

    if intencao == "desqualificado":
        data["exit"] = None
        _add_tag(data, "desqualificado")
        await slack_client.notificar(_aviso_slack("DESQUALIFICADO", payload, resumo))
        return "triagem"

    if intencao == "agendar" and fase != "agendando":
        try:
            novos = await asyncio.to_thread(calendar_client.listar_horarios_livres)
        except Exception as e:  # noqa: BLE001
            print(f"[calendar] erro ao listar horarios: {type(e).__name__}")
            novos = []
        if novos:
            oferta = "Tenho estes horarios disponiveis:\n" + "\n".join(
                f"- {calendar_client.formatar_slot(s)}" for s in novos
            ) + "\nQual fica melhor pra voce?"
            _add_msg(data, oferta)
            data["exit"] = None
            return "agendando"
        _add_msg(data, "Agora nao encontrei horarios livres. Qual dia e periodo ficam melhores pra voce?")
        data["exit"] = None
        return fase

    if intencao == "confirmar" and data.get("horario_escolhido"):
        escolhido = _parse_iso(data["horario_escolhido"])
        validos = {s.isoformat() for s in slots}
        if escolhido and (not validos or escolhido.isoformat() in validos):
            nome = payload.contact.name or "Lead"
            titulo = f"Apresentacao Vonix - {nome}"
            try:
                evento = await asyncio.to_thread(
                    calendar_client.criar_evento, escolhido, titulo, resumo or "Apresentacao Vonix"
                )
            except Exception as e:  # noqa: BLE001
                print(f"[calendar] erro ao criar evento: {type(e).__name__}")
                _add_msg(data, "Tive um problema ao confirmar o agendamento. Pode tentar outro horario?")
                return "agendando"
            quando = calendar_client.formatar_slot(escolhido)
            data["exit"] = {"tool": "agendar_demo", "note": f"{resumo} | Agendado: {quando}".strip(" |")}
            data["data_agendada"] = quando
            _add_tag(data, "agendado")
            link = evento.get("htmlLink", "")
            await slack_client.notificar(
                _aviso_slack("QUALIFICADO (agendado)", payload, resumo, f"Reuniao: {quando}\n{link}")
            )
            return "triagem"
        _add_msg(data, "Nao consegui confirmar esse horario. Pode escolher um dos horarios que te enviei?")
        return "agendando"

    return fase


def formatar_resposta(data: dict, payload: IncomingMessage, nova_fase: str) -> dict:
    exit_data = data.get("exit") or {}

    if payload.return_variables:
        variaveis = {}
        mapa_simples = {
            "note": data.get("resumo_lead") or exit_data.get("note"),
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
        if variaveis:
            data["variables"] = variaveis

    if isinstance(data.get("messages"), list):
        data["messages"] = [
            {"type": "TEXT", "text": m}
            for m in data["messages"]
            if m and m.strip()
        ][:10]

    # Remove campos internos que nao fazem parte do contrato Vonix.
    for chave in ("intencao", "horario_escolhido", "resumo_lead", "n_operadores",
                  "canal_atual", "volume_dia", "dor_principal", "concorrente_atual",
                  "data_agendada"):
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
