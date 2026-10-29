"""Notificacoes para o Slack via Incoming Webhook (canal dedicado)."""

import os
import httpx

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")


async def notificar(texto: str) -> bool:
    """Posta uma mensagem no canal do webhook. Nao derruba o fluxo em caso de erro."""
    if not SLACK_WEBHOOK_URL:
        print("[slack] SLACK_WEBHOOK_URL ausente; notificacao ignorada")
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.post(SLACK_WEBHOOK_URL, json={"text": texto})
            return resp.status_code == 200
    except Exception as e:  # noqa: BLE001
        print(f"[slack] erro ao notificar: {type(e).__name__}")
        return False
