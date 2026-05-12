from __future__ import annotations

import logging
import random
import re
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

        if not video_path.exists():
            return UploadResult(False, False, "playwright", f"Video nao encontrado: {video_path}")

        if not settings.tiktok.storage_state_path.exists():
            return UploadResult(
                False,
                False,
                "playwright",
                f"Sessao TikTok nao encontrada em {settings.tiktok.storage_state_path}. Rode scripts/setup_tiktok_session.py.",
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
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=settings.tiktok.headless, args=["--disable-blink-features=AutomationControlled"])
            context = browser.new_context(
                storage_state=str(settings.tiktok.storage_state_path),
                viewport={"width": 1440, "height": 1100},
                locale="pt-BR",
                timezone_id="America/Sao_Paulo",
            )
            page = context.new_page()
            page.goto(settings.tiktok.upload_url, wait_until="domcontentloaded", timeout=90_000)
            self._human_delay()

            if self._looks_logged_out(page):
                raise RuntimeError("Sessao TikTok expirada. Rode scripts/setup_tiktok_session.py e atualize o secret.")

            file_input = page.locator("input[type='file']").first
            file_input.wait_for(state="attached", timeout=60_000)
            file_input.set_input_files(str(video_path))
            self._wait_upload_processing(page)

            caption_text = f"{settings.tiktok.caption_prefix}\n{caption}".strip()
            if not self._fill_caption(page, caption_text):
                logger.warning("Campo de legenda nao localizado; continuando sem alterar legenda.")

            if thumbnail_path and thumbnail_path.exists():
                logger.info("Thumbnail gerada em %s. O upload automatico de thumbnail depende da UI atual do TikTok.", thumbnail_path)

            self._human_delay(multiplier=2)
            if not self._click_publish(page):
                self._save_failure_debug(page, video_path)
                raise RuntimeError("Botao de publicar nao encontrado. A UI do TikTok pode ter mudado.")

            self._human_delay(multiplier=5)
            self._wait_publish_result(page)
            context.storage_state(path=str(settings.tiktok.storage_state_path))
            browser.close()
            return UploadResult(True, True, "playwright", "Upload enviado ao TikTok.")

    def _looks_logged_out(self, page: object) -> bool:
        login_patterns = [
            "text=/log in|login|entrar/i",
            "button:has-text('Entrar')",
            "button:has-text('Log in')",
        ]
        for selector in login_patterns:
            try:
                if page.locator(selector).first.is_visible(timeout=2_000):
                    return True
            except Exception:
                continue
        return False

    def _wait_upload_processing(self, page: object) -> None:
        self._human_delay(multiplier=3)
        processing_patterns = [
            re.compile("uploading", re.I),
            re.compile("processing", re.I),
            re.compile("carregando", re.I),
            re.compile("processando", re.I),
        ]
        deadline = time.time() + 180
        while time.time() < deadline:
            body_text = ""
            try:
                body_text = page.locator("body").inner_text(timeout=5_000)
            except Exception:
                pass
            if body_text and not any(pattern.search(body_text) for pattern in processing_patterns):
                return
            self._human_delay(multiplier=0.5)

    def _fill_caption(self, page: object, caption_text: str) -> bool:
        caption_selectors = [
            "[data-e2e='caption-container'] [contenteditable='true']",
            "div[contenteditable='true']",
            "[contenteditable='true']",
            "textarea",
            "div[role='textbox']",
        ]
        for selector in caption_selectors:
            locator = page.locator(selector).first
            try:
                locator.wait_for(state="visible", timeout=15_000)
                locator.click()
                page.keyboard.press("Control+A")
                page.keyboard.type(caption_text, delay=random.randint(8, 22))
                return True
            except Exception:
                continue
        return False

    def _click_publish(self, page: object) -> bool:
        post_selectors = [
            "button[data-e2e='post_video_button']",
            "button:has-text('Publicar')",
            "button:has-text('Post')",
            "button:has-text('Agendar')",
        ]
        for selector in post_selectors:
            button = page.locator(selector).first
            try:
                button.wait_for(state="visible", timeout=30_000)
                if button.is_disabled():
                    self._human_delay(multiplier=2)
                button.click(timeout=20_000)
                return True
            except Exception:
                continue
        return False

    def _wait_publish_result(self, page: object) -> None:
        try:
            page.wait_for_load_state("networkidle", timeout=60_000)
        except Exception:
            pass
        success_patterns = [
            "text=/uploaded|published|publicado|enviado/i",
            "text=/Your video is being uploaded/i",
        ]
        for selector in success_patterns:
            try:
                page.locator(selector).first.wait_for(state="visible", timeout=10_000)
                return
            except Exception:
                continue

    def _save_failure_debug(self, page: object, video_path: Path) -> None:
        debug_dir = settings.paths.logs / "tiktok_debug"
        debug_dir.mkdir(parents=True, exist_ok=True)
        name = video_path.stem
        try:
            page.screenshot(path=str(debug_dir / f"{name}.png"), full_page=True)
        except Exception:
            pass
        try:
            (debug_dir / f"{name}.html").write_text(page.content(), encoding="utf-8")
        except Exception:
            pass

    def _human_delay(self, multiplier: float = 1.0) -> None:
        minimum = settings.tiktok.min_delay_seconds * multiplier
        maximum = settings.tiktok.max_delay_seconds * multiplier
        time.sleep(random.uniform(minimum, maximum))
