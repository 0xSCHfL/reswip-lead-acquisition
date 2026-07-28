"""Solve reCAPTCHA v2 by switching to audio challenge.

Port of dessant/buster's approach:
click audio button → download MP3 → ffmpeg → speech_recognition

Provides both sync (`solve_recaptcha_v2_sync`) and async (`solve_recaptcha_v2`) versions.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import tempfile
import time

# Monkey-patch aifc removed in Python 3.14
import types as _types
_aifc = _types.ModuleType("aifc")
_aifc.open = type("_AifcFile", (), {"__init__": lambda *a: (_ for _ in ()).throw(ImportError("aifc unavailable"))})  # noqa
_aifc.Error = Exception
sys.modules["aifc"] = _aifc

import speech_recognition as sr

log = logging.getLogger("recaptcha_solver")


def _wait_for_frame_sync(page, pattern: str, timeout_ms: int = 15000):
    """Poll for a frame matching regex pattern (sync Playwright)."""
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for frame in page.frames:
            if re.search(pattern, frame.url or ""):
                return frame
        page.wait_for_timeout(200)
    return None


def _check_token_sync(page) -> bool:
    try:
        token = page.evaluate(
            "() => { const ta = document.querySelector('textarea[name=\"g-recaptcha-response\"]'); return ta ? ta.value : null; }"
        )
        return bool(token and len(token) > 20)
    except Exception:
        return False


def _transcribe(mp3_bytes: bytes) -> str:
    mp3_path = tempfile.mktemp(suffix=".mp3")
    wav_path = mp3_path.replace(".mp3", ".wav")
    try:
        with open(mp3_path, "wb") as f:
            f.write(mp3_bytes)

        subprocess.run(
            ["ffmpeg", "-y", "-i", mp3_path,
             "-ar", "16000", "-ac", "1",
             "-sample_fmt", "s16",
             wav_path],
            capture_output=True,
            timeout=30,
        )

        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio_data = recognizer.record(source)
        return recognizer.recognize_google(audio_data)
    except sr.UnknownValueError:
        log.warning("speech recognition could not understand audio")
        return ""
    except Exception as exc:
        log.debug("transcription failed: %s", exc)
        return ""
    finally:
        for p in [mp3_path, wav_path]:
            try:
                os.unlink(p)
            except Exception:
                pass


def _hide_consent(page) -> None:
    """Hide cookie consent overlay that blocks clicks."""
    try:
        page.evaluate("""() => {
            const el = document.querySelector('#__abconsent-cmp');
            if (el) el.style.display = 'none';
        }""")
    except Exception:
        pass


def solve_recaptcha_v2_sync(page, timeout_ms: int = 120_000) -> bool:
    """Solve reCAPTCHA v2 using audio challenge (sync Playwright)."""
    _hide_consent(page)
    try:
        anchor = _wait_for_frame_sync(page, r"/recaptcha/(api2|enterprise)/anchor")
        if not anchor:
            log.debug("no anchor frame")
            return False

        try:
            cb = anchor.get_by_role("checkbox")
            if cb.is_visible() and cb.is_checked():
                log.info("already solved")
                return True
        except Exception:
            pass

        log.info("clicking checkbox")
        cb = anchor.get_by_role("checkbox")
        cb.click()
        page.wait_for_timeout(3_000)

        if _check_token_sync(page):
            log.info("solved without challenge")
            return True

        bframe = _wait_for_frame_sync(page, r"/recaptcha/(api2|enterprise)/bframe")
        if not bframe:
            log.warning("no bframe")
            return False

        page.wait_for_timeout(1_000)

        # Switch to audio mode before the attempt loop
        log.info("switching to audio mode")
        try:
            audio_btn = bframe.locator("#recaptcha-audio-button")
            audio_btn.wait_for(timeout=8_000)
            audio_btn.click()
            page.wait_for_timeout(2_000)
        except Exception:
            log.debug("audio button not found")

        try:
            play_btn = bframe.locator(".rc-audiochallenge-play-button > button")
            play_btn.wait_for(timeout=5_000)
            play_btn.click()
            page.wait_for_timeout(2_000)
        except Exception:
            log.debug("play button not found")

        max_attempts = 5
        for attempt in range(max_attempts):
            log.info("--- attempt %d/%d ---", attempt + 1, max_attempts)

            try:
                audio_url = bframe.evaluate("""() => {
                    const el = document.querySelector('audio#audio-source');
                    return el ? (el.src || null) : null;
                }""")
            except Exception:
                log.info("bframe gone, page may have navigated")
                return "/Landing/Abuse" not in (page.url or "")

            if not audio_url:
                log.warning("no audio URL, reloading")
                try:
                    reload_btn = bframe.locator("#recaptcha-reload-button")
                    if reload_btn.is_visible():
                        reload_btn.click()
                        page.wait_for_timeout(3_000)
                except Exception:
                    pass
                try:
                    play_btn = bframe.locator(".rc-audiochallenge-play-button > button")
                    play_btn.click()
                    page.wait_for_timeout(2_000)
                except Exception:
                    pass
                continue

            log.info("downloading audio...")
            resp = page.request.get(audio_url)
            if resp.ok and resp.body():
                audio_bytes = list(resp.body())
            else:
                audio_bytes = None

            if not audio_bytes:
                log.warning("audio download failed")
                try:
                    reload_btn = bframe.locator("#recaptcha-reload-button")
                    if reload_btn.is_visible():
                        reload_btn.click()
                        page.wait_for_timeout(3_000)
                except Exception:
                    pass
                continue

            text = _transcribe(bytes(audio_bytes))
            if not text:
                log.warning("transcription empty, reloading")
                try:
                    reload_btn = bframe.locator("#recaptcha-reload-button")
                    if reload_btn.is_visible():
                        reload_btn.click()
                        page.wait_for_timeout(3_000)
                except Exception:
                    pass
                continue

            log.info("transcribed: %r", text)

            try:
                input_el = bframe.locator("#audio-response")
                input_el.fill(text)
                page.wait_for_timeout(500)
            except Exception as exc:
                log.warning("fill failed: %s", exc)
                continue

            try:
                verify_btn = bframe.locator("#recaptcha-verify-button")
                if verify_btn.is_visible():
                    verify_btn.click()
            except Exception as exc:
                log.warning("verify click failed: %s", exc)
                continue

            page.wait_for_timeout(4_000)

            # Check if page navigated away from abuse page
            if "/Landing/Abuse" not in (page.url or ""):
                log.info("page navigated away from abuse — likely solved!")
                return True

            # Check token (still on abuse page)
            try:
                if _check_token_sync(page):
                    log.info("SOLVED!")
                    return True
            except Exception:
                log.debug("token check failed")
                continue

            try:
                error = bframe.locator(".rc-audiochallenge-error-message")
                if error.is_visible():
                    log.warning("wrong: %s", error.inner_text())
            except Exception:
                pass

            try:
                reload_btn = bframe.locator("#recaptcha-reload-button")
                if reload_btn.is_visible():
                    reload_btn.click()
                    page.wait_for_timeout(2_000)
            except Exception:
                pass

        log.warning("failed after %d attempts", max_attempts)
        return False
    except Exception as exc:
        log.warning("solver error: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Async wrapper (for use with async Playwright)
# ---------------------------------------------------------------------------

async def solve_recaptcha_v2(page, timeout_ms: int = 120_000) -> bool:
    """Async wrapper around solve_recaptcha_v2_sync."""
    return solve_recaptcha_v2_sync(page, timeout_ms=timeout_ms)
