from __future__ import annotations

import argparse
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

from config import settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Abre um navegador para salvar a sessao autenticada do TikTok.")
    parser.add_argument("--state", default=str(settings.tiktok.storage_state_path), help="Arquivo JSON da sessao.")
    parser.add_argument("--headed", action="store_true", default=True, help="Abre navegador visivel.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    state_path = Path(args.state)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed, args=["--disable-blink-features=AutomationControlled"])
        context = browser.new_context(
            viewport={"width": 1440, "height": 1100},
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
        )
        page = context.new_page()
        page.goto("https://www.tiktok.com/login", wait_until="domcontentloaded", timeout=90_000)

        print("\n1. Faca login no TikTok no navegador que abriu.")
        print("2. Resolva 2FA/captcha, se aparecer.")
        print("3. Depois de logado, va para https://www.tiktok.com/upload se nao for automaticamente.")
        input("Quando a pagina de upload abrir logada, pressione ENTER aqui para salvar a sessao...")

        page.goto(settings.tiktok.upload_url, wait_until="domcontentloaded", timeout=90_000)
        context.storage_state(path=str(state_path))
        browser.close()

    print(f"\nSessao salva em: {state_path.resolve()}")
    print("Agora rode: python scripts/encode_tiktok_state.py")


if __name__ == "__main__":
    os.environ.setdefault("PLAYWRIGHT_HEADLESS", "false")
    main()
