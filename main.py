"""SDR Vonix: webhook do bloco Agente de IA (Fluxos de Conversa do VonixCC).

Fluxo sincrono (200): recebe o turno, gera a resposta com o Google Gemini e
devolve no formato Vonix (messages / tags / variables / exit) na propria
requisicao, dentro do limite de 30s.

Contrato: https://vonix.vonixcc.com.br/v1/api-docs/agente-ia
"""

import os
import json
import time
import secrets
import asyncio
import traceback
from typing import List, Optional, Dict, Any

import httpx  # noqa: F401  (reservado para integracoes futuras, ex.: agenda/CRM)
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

from prompt import construir_system_instruction

app = FastAPI(title="SDR Vonix - Agente de IA")

client = genai.Client()

# Token do "segredo compartilhado": gerado por nos, salvo como env var e colado
# no bloco da Vonix (atalho Bearer). A Vonix envia em Authorization: Bearer <token>.
VONIX_TOKEN = os.environ.get("VONIX_TOKEN", "")

# Modelos tentados em ordem (fallback). Confirme a disponibilidade atual no Gemini.
MODELS_TO_TRY = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash"]

# Orcamento de tempo: o endpoint deve responder em ate 30s. Reservamos margem.
TIME_BUDGET = 25

SYSTEM_INSTRUCTION = construir_system_instruction()


# --- Modelos de entrada (contrato Vonix) -----------------------------------
class Contact(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    user_id: Optional[str] = None


class IncomingMessage(BaseModel):
    turn_id: str = Field(description="Id do turno (igual na retentativa)")
    chat_id: int = Field(description="Id do atendimento (use como id de sessao)")
    node_id: str = Field(description="Id do bloco no fluxo")
    channel: str = Field(description="Canal do atendimento")
    is_test: bool = Field(description="True quando o turno vem do testador de fluxo")
    contact: Contact = Field(description="Dados do contato")
    text: str = Field(description="Textos e legendas unidos por quebra de linha")
    messages: List[Dict[str, Any]] = Field(description="Mensagens novas neste turno")
    history: List[Dict[str, Any]] = Field(description="Historico de mensagens anteriores")
    variables: Dict[str, Any] = Field(description="Variaveis de contexto do fluxo")
    return_variables: List[str] = Field(description="Nomes que o agente pode gravar")
    tools: List[Dict[str, Any]] = Field(description="Saidas permitidas neste turno")
    # Presentes apenas quando a forma assincrona esta ligada (nao usados no modo sincrono).
    callback_url: Optional[str] = None
    callback_deadline: Optional[str] = None


# --- Schema interno do Gemini (structured output) --------------------------
# "variables" NAO entra aqui: Dict[str, Any] quebra o structured output do Gemini.
# As variables de retorno sao montadas em Python depois da resposta.
class GeminiExit(BaseModel):
    tool: Optional[str] = Field(default=None, description="ID da ferramenta de saida (tool.id)")
    note: Optional[str] = Field(default=None, description="Resumo do caso para o vendedor")


class GeminiAgentResponse(BaseModel):
    messages: List[str] = Field(description="Textos para enviar ao contato")
    tags: Optional[List[str]] = Field(default=[], description="Tags do atendimento")
    exit: Optional[GeminiExit] = Field(default=None, description="Saida do bloco; null para continuar")


# --- Montagem do prompt do turno -------------------------------------------
def montar_user_prompt(payload: IncomingMessage) -> str:
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

    return f"""
Dados do contato: {contato}
Variaveis atuais: {payload.variables}
Ferramentas disponiveis (use o id da ferramenta em exit.tool):
{ferramentas}

Historico recente:
{historico}

Nova mensagem do contato: {user_message}
""".strip()


async def chamar_gemini(user_prompt: str):
    """Chama o Gemini com fallback de modelos e retry em erros transitorios."""
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
                    break  # tenta o proximo modelo
    return None, last_error


def formatar_resposta(result_data: dict, payload: IncomingMessage) -> dict:
    """Converte a saida do Gemini para o formato Vonix e aplica os limites do contrato."""
    exit_data = result_data.get("exit") or {}

    # Variables de retorno: so chaves listadas em return_variables, valores simples.
    if payload.return_variables:
        variaveis = {}
        if "note" in payload.return_variables and exit_data.get("note"):
            variaveis["note"] = exit_data["note"]
        if "qualificado" in payload.return_variables and exit_data.get("tool"):
            variaveis["qualificado"] = exit_data.get("tool") == "agendar_demo"
        if variaveis:
            result_data["variables"] = variaveis

    # messages: List[str] -> [{type, text}], sem vazios, no maximo 10.
    if isinstance(result_data.get("messages"), list):
        result_data["messages"] = [
            {"type": "TEXT", "text": m}
            for m in result_data["messages"]
            if m and m.strip()
        ][:10]

    return result_data


@app.post("/webhook/sdr")
async def handle_turn(
    payload: IncomingMessage,
    authorization: str = Header(default=""),
    # Opcionais de proposito: o contrato oficial marca so Authorization como required.
    x_vonix_turn_id: Optional[str] = Header(default=None),
    x_vonix_attempt: Optional[int] = Header(default=None),
):
    # Autenticacao: turnos de teste (is_test) sao isentos (o testador nao envia token).
    if not payload.is_test:
        recebido = authorization.removeprefix("Bearer ").strip()
        if not VONIX_TOKEN or not secrets.compare_digest(recebido, VONIX_TOKEN):
            raise HTTPException(status_code=401, detail="Token invalido")

    if x_vonix_attempt == 2:
        print(f"[retry] retentativa do turno {x_vonix_turn_id or payload.turn_id}")

    try:
        response, last_error = await chamar_gemini(montar_user_prompt(payload))

        if not response:
            err = str(last_error) if last_error else "erro desconhecido"
            if "429" in err or "RESOURCE_EXHAUSTED" in err:
                raise HTTPException(status_code=429, detail=err, headers={"Retry-After": "60"})
            if "503" in err or "UNAVAILABLE" in err:
                raise HTTPException(status_code=503, detail=err, headers={"Retry-After": "1"})
            raise HTTPException(status_code=500, detail=err)

        result_data = json.loads(response.text)
        return formatar_resposta(result_data, payload)

    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
def health_check():
    return {"status": "online", "service": "SDR Vonix - Agente de IA"}
