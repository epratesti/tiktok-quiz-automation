from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)


@dataclass
class UploadResult:
    attempted: bool
    success: bool
    mode: str
    message: str


class TikTokUploader:
    """TikTok uploader with conservative guardrails.

    This module does not bypass CAPTCHAs, login challenges, device checks, or
    platform limits. Use it only on accounts where automation is allowed and
    configured. The default DRY_RUN mode logs what would be uploaded.
    """

    def upload(self, video_path: Path, caption: str, thumbnail_path: Path | None = None) -> UploadResult:
        if settings.tiktok.dry_run or not settings.tiktok.upload_enabled:
            logger.info("DRY_RUN ativo. Video pronto para upload: %s", video_path)
            return UploadResult(False, True, "dry_run", f"Upload simulado: {video_path.name}")

        if not settings.tiktok.storage_state_path.exists():
            return UploadResult(
                False,
                False,
                "playwright",
                f"Sessao TikTok nao encontrada em {settings.tiktok.storage_state_path}.",
            )

        for attempt in range(1, settings.tiktok.max_retries + 2):
            try:
                return self._upload_with_playwright(video_path, caption, thumbnail_path)
            except Exception as exc:  # noqa: BLE001 - retry boundary
                logger.warning("Tentativa %s de upload falhou: %s", attempt, exc)
                if attempt > settings.tiktok.max_retries:
                    return UploadResult(True, False, "playwright", str(exc))
                self._human_delay(multiplier=attempt + 1)

        return UploadResult(True, False, "playwright", "Falha inesperada no upload.")

    def _upload_with_playwright(self, video_path: Path, caption: str, thumbnail_path: Path | None) -> UploadResult:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=settings.tiktok.headless)
            context = browser.new_context(storage_state=str(settings.tiktok.storage_state_path))
            page = context.new_page()
            page.goto(settings.tiktok.upload_url, wait_until="domcontentloaded", timeout=90_000)
            self._human_delay()

            file_input = page.locator("input[type='file']").first
            file_input.set_input_files(str(video_path))
            self._human_delay(multiplier=3)

            caption_text = f"{settings.tiktok.caption_prefix}\n{caption}".strip()
            caption_selectors = [
                "[contenteditable='true']",
                "textarea",
                "div[role='textbox']",
            ]
            filled = False
            for selector in caption_selectors:
                locator = page.locator(selector).first
                try:
                    locator.wait_for(state="visible", timeout=15_000)
                    locator.click()
                    locator.fill(caption_text)
                    filled = True
                    break
                except PlaywrightTimeoutError:
                    continue
                except Exception:
                    continue
            if not filled:
                logger.warning("Campo de legenda nao localizado; continuando sem alterar legenda.")

            if thumbnail_path and thumbnail_path.exists():
                logger.info("Thumbnail gerada em %s. O upload automatico de thumbnail depende da UI atual do TikTok.", thumbnail_path)

            self._human_delay(multiplier=2)
            post_selectors = [
                "button:has-text('Post')",
                "button:has-text('Publicar')",
                "button[data-e2e='post_video_button']",
            ]
            clicked = False
            for selector in post_selectors:
                button = page.locator(selector).first
                try:
                    button.wait_for(state="visible", timeout=20_000)
                    button.click()
                    clicked = True
                    break
                except Exception:
                    continue

            if not clicked:
                raise RuntimeError("Botao de publicar nao encontrado. A UI do TikTok pode ter mudado.")

            self._human_delay(multiplier=5)
            context.storage_state(path=str(settings.tiktok.storage_state_path))
            browser.close()
            return UploadResult(True, True, "playwright", "Upload enviado ao TikTok.")

    def _human_delay(self, multiplier: float = 1.0) -> None:
        minimum = settings.tiktok.min_delay_seconds * multiplier
        maximum = settings.tiktok.max_delay_seconds * multiplier
        time.sleep(random.uniform(minimum, maximum))
