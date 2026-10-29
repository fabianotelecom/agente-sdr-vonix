"""Integracao com o Google Calendar (OAuth refresh token, conta Gmail pessoal).

Regras de agendamento do SDR:
- Janela: 09:00 as 16:00 (inicios validos de 09:00 a 15:00; reunioes de 1h).
- Somente dias uteis (segunda a sexta).
- Fuso configuravel (padrao America/Sao_Paulo).

As funcoes sao sincronas; chame-as via asyncio.to_thread no webhook.
"""

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

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


def listar_horarios_livres(max_slots: int = 4, dias_a_frente: int = 10):
    """Retorna ate max_slots horarios livres (datetimes tz-aware), em dias uteis,
    dentro da janela 09:00-16:00, a partir de agora + 1h de antecedencia."""
    service = _service()
    agora = datetime.now(_tz)
    limite = agora + timedelta(hours=1)  # antecedencia minima

    janela_ini = agora.replace(minute=0, second=0, microsecond=0)
    janela_fim = (agora + timedelta(days=dias_a_frente)).replace(
        hour=HORA_FIM, minute=0, second=0, microsecond=0
    )
    ocupados = _intervalos_ocupados(service, janela_ini, janela_fim)

    slots = []
    dia = agora.date()
    for _ in range(dias_a_frente + 1):
        data = datetime(dia.year, dia.month, dia.day, tzinfo=_tz)
        if data.weekday() < 5:  # 0=segunda ... 4=sexta
            for hora in range(HORA_INICIO, HORA_FIM):  # 9..15
                ini = data.replace(hour=hora)
                fim = ini + timedelta(minutes=DURACAO_MIN)
                if ini < limite:
                    continue
                if _livre(ini, fim, ocupados):
                    slots.append(ini)
                    if len(slots) >= max_slots:
                        return slots
        dia = dia + timedelta(days=1)
    return slots


def criar_evento(inicio: datetime, titulo: str, descricao: str) -> dict:
    """Cria um evento de 1h e retorna o recurso criado (com htmlLink)."""
    service = _service()
    fim = inicio + timedelta(minutes=DURACAO_MIN)
    evento = {
        "summary": titulo,
        "description": descricao,
        "start": {"dateTime": inicio.isoformat(), "timeZone": TIMEZONE},
        "end": {"dateTime": fim.isoformat(), "timeZone": TIMEZONE},
    }
    return service.events().insert(calendarId=CALENDAR_ID, body=evento).execute()


_DIAS = ["segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo"]


def formatar_slot(dt: datetime) -> str:
    return f"{_DIAS[dt.weekday()]} {dt.strftime('%d/%m')} as {dt.strftime('%H:%M')}"
