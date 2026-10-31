"""Integracao com o Google Calendar (OAuth refresh token, conta Gmail pessoal).

Regras de agendamento do SDR:
- Janela: 09:00 as 16:00 (inicios validos de 09:00 a 15:00; reunioes de 1h).
- Somente dias uteis (segunda a sexta), excluindo feriados nacionais.
- Fuso configuravel (padrao America/Sao_Paulo).

As funcoes sao sincronas; chame-as via asyncio.to_thread no webhook.
"""

import os
from typing import Optional
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import holidays
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar"]

CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
REFRESH_TOKEN = os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN", "")
CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "primary")
TIMEZONE = os.environ.get("TIMEZONE", "America/Sao_Paulo")

HORA_INICIO = 9   # primeiro inicio possivel (09:00)
HORA_FIM = 16     # fim da janela (ultimo inicio as 15:00)
DURACAO_MIN = 60

_tz = ZoneInfo(TIMEZONE)

# Feriados nacionais do Brasil (inclui moveis: Carnaval, Sexta-Feira Santa,
# Corpus Christi). O objeto expande os anos sob demanda no lookup.
_feriados = holidays.Brazil()


def _service():
    if not (CLIENT_ID and CLIENT_SECRET and REFRESH_TOKEN):
        raise RuntimeError("Credenciais OAuth do Google ausentes (CLIENT_ID/SECRET/REFRESH_TOKEN).")
    creds = Credentials(
        token=None,
        refresh_token=REFRESH_TOKEN,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _intervalos_ocupados(service, inicio: datetime, fim: datetime):
    body = {
        "timeMin": inicio.isoformat(),
        "timeMax": fim.isoformat(),
        "timeZone": TIMEZONE,
        "items": [{"id": CALENDAR_ID}],
    }
    resp = service.freebusy().query(body=body).execute()
    ocupados = resp["calendars"][CALENDAR_ID]["busy"]
    return [
        (datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"]))
        for b in ocupados
    ]


def _livre(slot_ini: datetime, slot_fim: datetime, ocupados) -> bool:
    for b_ini, b_fim in ocupados:
        if slot_ini < b_fim and slot_fim > b_ini:  # ha sobreposicao
            return False
    return True


def listar_horarios_livres(max_por_dia: int = 2, max_dias: int = 4):
    """Retorna horarios livres (datetimes tz-aware) espalhados por varios dias
    uteis (sem feriados), ate max_por_dia por dia e max_dias dias com vaga, na
    janela 09:00-16:00, a partir de agora + 1h de antecedencia.

    Espalhar por dia (em vez de encher o primeiro dia) garante que o cliente veja
    opcoes de dias diferentes."""
    service = _service()
    agora = datetime.now(_tz)
    limite = agora + timedelta(hours=1)  # antecedencia minima

    # Olha ate 14 dias corridos para encontrar ate max_dias dias uteis com vaga.
    janela_ini = agora.replace(minute=0, second=0, microsecond=0)
    janela_fim = (agora + timedelta(days=14)).replace(
        hour=HORA_FIM, minute=0, second=0, microsecond=0
    )
    ocupados = _intervalos_ocupados(service, janela_ini, janela_fim)

    slots = []
    dias_com_vaga = 0
    dia = agora.date()
    for _ in range(15):
        data = datetime(dia.year, dia.month, dia.day, tzinfo=_tz)
        if data.weekday() < 5 and dia not in _feriados:  # dia util, sem feriado
            livres_dia = []
            for hora in range(HORA_INICIO, HORA_FIM):  # 9..15
                ini = data.replace(hour=hora)
                fim = ini + timedelta(minutes=DURACAO_MIN)
                if ini < limite:
                    continue
                if _livre(ini, fim, ocupados):
                    livres_dia.append(ini)
            # Espalha os horarios do dia (ex.: manha e tarde) em vez de pegar so os
            # primeiros, dando variedade de periodo ao cliente.
            if len(livres_dia) > max_por_dia:
                passo = (len(livres_dia) - 1) / (max_por_dia - 1) if max_por_dia > 1 else 0
                do_dia = [livres_dia[round(i * passo)] for i in range(max_por_dia)]
            else:
                do_dia = livres_dia
            if do_dia:
                slots.extend(do_dia)
                dias_com_vaga += 1
                if dias_com_vaga >= max_dias:
                    break
        dia = dia + timedelta(days=1)
    return slots


def criar_evento(inicio: datetime, titulo: str, descricao: str,
                 convidado_email: Optional[str] = None) -> dict:
    """Cria um evento de 1h e retorna o recurso criado (com htmlLink).

    Se convidado_email vier, adiciona o cliente como convidado e pede ao Google
    para enviar o convite por e-mail (sendUpdates=all)."""
    service = _service()
    fim = inicio + timedelta(minutes=DURACAO_MIN)
    evento = {
        "summary": titulo,
        "description": descricao,
        "start": {"dateTime": inicio.isoformat(), "timeZone": TIMEZONE},
        "end": {"dateTime": fim.isoformat(), "timeZone": TIMEZONE},
    }
    params = {"calendarId": CALENDAR_ID, "body": evento}
    if convidado_email:
        evento["attendees"] = [{"email": convidado_email}]
        params["sendUpdates"] = "all"
    return service.events().insert(**params).execute()


_DIAS = ["segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"]


def formatar_slot(dt: datetime) -> str:
    return f"{_DIAS[dt.weekday()]} {dt.strftime('%d/%m')} as {dt.strftime('%H:%M')}"
