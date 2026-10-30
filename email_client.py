"""Notificacoes por e-mail (SMTP, ex.: Gmail com senha de app).

Mantem a mesma interface async `notificar(texto)` usada no fluxo. A primeira
linha do texto vira o assunto (sem marcacoes); o restante vai no corpo.

Variaveis de ambiente:
- SMTP_HOST (padrao smtp.gmail.com)
- SMTP_PORT (padrao 587, STARTTLS)
- SMTP_USER (conta que envia; no Gmail, o proprio e-mail)
- SMTP_APP_PASSWORD (senha de app do Gmail, 16 caracteres)
- ALERTA_EMAIL_DESTINO (quem recebe; padrao = SMTP_USER)
"""

import os
import ssl
import asyncio
import smtplib
from email.message import EmailMessage

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_APP_PASSWORD = os.environ.get("SMTP_APP_PASSWORD", "")
EMAIL_DESTINO = os.environ.get("ALERTA_EMAIL_DESTINO", "") or SMTP_USER


def _assunto_e_corpo(texto: str) -> tuple[str, str]:
    linhas = texto.strip().splitlines()
    primeira = linhas[0] if linhas else "Aviso SDR Vonix"
    assunto = primeira.replace("*", "").strip() or "Aviso SDR Vonix"
    return assunto, texto.strip()


def _enviar(texto: str) -> bool:
    assunto, corpo = _assunto_e_corpo(texto)
    msg = EmailMessage()
    msg["Subject"] = f"[SDR Vonix] {assunto}"
    msg["From"] = SMTP_USER
    msg["To"] = EMAIL_DESTINO
    msg.set_content(corpo)

    ctx = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as smtp:
        smtp.starttls(context=ctx)
        smtp.login(SMTP_USER, SMTP_APP_PASSWORD)
        smtp.send_message(msg)
    return True


async def notificar(texto: str) -> bool:
    """Envia o aviso por e-mail. Nao derruba o fluxo em caso de erro."""
    if not (SMTP_USER and SMTP_APP_PASSWORD and EMAIL_DESTINO):
        print("[email] credenciais SMTP ausentes; notificacao ignorada")
        return False
    try:
        return await asyncio.to_thread(_enviar, texto)
    except Exception as e:  # noqa: BLE001
        print(f"[email] erro ao notificar: {type(e).__name__}")
        return False
