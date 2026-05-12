from __future__ import annotations

import argparse
import base64
from pathlib import Path

from config import settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Converte a sessao Playwright do TikTok para secret do GitHub.")
    parser.add_argument("--state", default=str(settings.tiktok.storage_state_path), help="Arquivo JSON da sessao.")
    parser.add_argument("--out", default="", help="Arquivo opcional para salvar o valor base64.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    state_path = Path(args.state)
    if not state_path.exists():
        raise SystemExit(f"Sessao nao encontrada: {state_path}. Rode scripts/setup_tiktok_session.py primeiro.")

    encoded = base64.b64encode(state_path.read_bytes()).decode("ascii")
    if args.out:
        output = Path(args.out)
        output.write_text(encoded, encoding="ascii")
        print(f"Valor salvo em: {output.resolve()}")
    else:
        print(encoded)

    print("\nCrie/atualize no GitHub o secret TIKTOK_STORAGE_STATE_B64 com esse valor.")


if __name__ == "__main__":
    main()
