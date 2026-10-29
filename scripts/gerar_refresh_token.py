"""Gera o refresh token OAuth do Google (escopo Calendar). Uso unico, local.

Pre-requisitos:
1. Criar um cliente OAuth do tipo "App para computador" no Google Cloud Console
   e baixar o JSON das credenciais (client_secret_XXXX.json).
2. Instalar a dependencia do fluxo:  pip install google-auth-oauthlib

Como rodar (na sua maquina):
    python scripts/gerar_refresh_token.py caminho/para/client_secret.json

Abre o navegador para consentimento com a conta Google que recebera as reunioes.
Ao final, imprime CLIENT_ID, CLIENT_SECRET e REFRESH_TOKEN para colar no Render.
O refresh token NAO deve ser commitado nem colado em arquivo versionado.
"""

import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def main() -> None:
    if len(sys.argv) < 2:
        print("Uso: python scripts/gerar_refresh_token.py <client_secret.json>")
        sys.exit(1)

    client_secret_file = sys.argv[1]
    flow = InstalledAppFlow.from_client_secrets_file(client_secret_file, SCOPES)
    # access_type=offline + prompt=consent garante que o refresh_token venha.
    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
    )

    if not creds.refresh_token:
        print("\nATENCAO: nao veio refresh_token. Revogue o acesso em")
        print("https://myaccount.google.com/permissions e rode de novo.")
        sys.exit(1)

    print("\n=== Cole estes valores nas variaveis de ambiente do Render ===\n")
    print(f"GOOGLE_OAUTH_CLIENT_ID={creds.client_id}")
    print(f"GOOGLE_OAUTH_CLIENT_SECRET={creds.client_secret}")
    print(f"GOOGLE_OAUTH_REFRESH_TOKEN={creds.refresh_token}")
    print("\n(Nao commite estes valores.)")


if __name__ == "__main__":
    main()
