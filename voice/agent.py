import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Generator, Optional

import numpy as np

from .chunker import SentenceChunker
from .llm import llm_summarize, stream_llm_tokens
from .react_agent import react_loop
from .stt import transcribe
from .tts import tts_kokoro

logger = logging.getLogger(__name__)

MAX_HISTORY_TURNS = 10
MAX_HISTORY_TOKENS = 2000


@dataclass
class VoiceConfig:
    mode: str = "chat"
    voice: str = "af_heart"
    lang: str = "a"
    temperature: float = 0.7
    max_tokens: int = 150
    wake_word: str = ""
    system_prompt: str = ""
    llm_url: str = "http://localhost:8100/v1"
    model: str = "local"
    api_key: str = ""
    whisper_model: str = "openai/whisper-large-v3-turbo"
    timezone: str = "UTC"


class VoiceAgent:
    def __init__(self):
        self.history: list[dict] = []
        self.summary: str = ""
        self.cancel = threading.Event()

    def interrupt(self):
        self.cancel.set()

    def clear_history(self):
        self.history = []
        self.summary = ""

    def _get_context(self) -> list[dict]:
        messages = []
        if self.summary:
            messages.append({
                "role": "system",
                "content": f"Previous conversation summary: {self.summary}",
            })
        recent = self.history[-(MAX_HISTORY_TURNS * 2):]
        total = sum(len(m["content"]) for m in recent)
        while total > MAX_HISTORY_TOKENS and len(recent) > 2:
            removed = recent.pop(0)
            total -= len(removed["content"])
            if recent and recent[0]["role"] == "assistant":
                total -= len(recent.pop(0)["content"])
        messages.extend(recent)
        return messages

    def _maybe_summarize(self, config: VoiceConfig):
        if len(self.history) > MAX_HISTORY_TURNS * 2:
            old = self.history[:-(MAX_HISTORY_TURNS * 2)]
            if old:
                to_sum = []
                if self.summary:
                    to_sum.append({"role": "system", "content": f"Prior summary: {self.summary}"})
                to_sum.extend(old)
                s = llm_summarize(to_sum, config.llm_url, config.model, config.api_key)
                if s:
                    self.summary = s
                    logger.info(f"[Context] Summarized: {s[:80]}...")
            self.history = self.history[-(MAX_HISTORY_TURNS * 2):]

    def process(
        self,
        audio_tuple: tuple[int, np.ndarray],
        config: VoiceConfig,
    ) -> Generator[tuple[str, object], None, None]:
        """
        Generator yielding:
          ("audio", (sr, np.ndarray))  — audio chunk to play
          ("transcript", str)          — transcription text (transcribe mode)
        """
        self.cancel.clear()
        t_start = time.time()

        # Always transcribe first
        t0 = time.time()
        try:
            user_text = transcribe(audio_tuple, config.whisper_model)
        except Exception as e:
            logger.error(f"STT error: {e}")
            return
        stt_time = time.time() - t0

        if not user_text:
            return
        logger.info(f"[STT {stt_time:.2f}s] {user_text!r}")

        # Resolve effective mode (wake_word gates to chat)
        mode = config.mode
        if mode == "wake_word":
            word = config.wake_word.strip().lower()
            if word and word not in user_text.lower():
                logger.debug(f"[Wake] No trigger in: {user_text!r}")
                return
            if word:
                idx = user_text.lower().find(word)
                user_text = user_text[idx + len(word):].strip(" ,.")
            if not user_text:
                return
            logger.info(f"[Wake] Triggered → {user_text!r}")
            mode = "chat"

        # Transcribe-only mode
        if mode == "transcribe":
            yield ("transcript", user_text)
            return

        # ReAct agent mode
        if mode == "agent":
            chunker = SentenceChunker()
            for event_type, payload in react_loop(
                user_text,
                self._get_context(),
                config.system_prompt,
                config.llm_url,
                config.model,
                config.max_tokens,
                config.temperature,
                self.cancel,
                config.api_key,
                config.timezone,
            ):
                if self.cancel.is_set():
                    break
                if event_type == "phrase":
                    sr, audio = tts_kokoro(payload, config.voice, config.lang)
                    yield ("audio", (sr, audio))
                elif event_type == "token":
                    for sentence in chunker.feed(payload):
                        if self.cancel.is_set():
                            break
                        sr, audio = tts_kokoro(sentence, config.voice, config.lang)
                        yield ("audio", (sr, audio))
                    if not self.cancel.is_set():
                        for sentence in chunker.flush():
                            if self.cancel.is_set():
                                break
                            sr, audio = tts_kokoro(sentence, config.voice, config.lang)
                            yield ("audio", (sr, audio))
                elif event_type == "history":
                    user_msg, asst_msg = payload
                    self.history.append({"role": "user", "content": user_msg})
                    self.history.append({"role": "assistant", "content": asst_msg})
                    self._maybe_summarize(config)
            return

        # Chat / skill mode — streaming pipeline
        chunker = SentenceChunker()
        full_response = ""
        ttfa = None
        interrupted = False

        for token in stream_llm_tokens(
            user_text,
            self._get_context(),
            self.cancel,
            config.system_prompt,
            config.llm_url,
            config.model,
            config.max_tokens,
            config.temperature,
            config.api_key,
        ):
            if self.cancel.is_set():
                interrupted = True
                break
            full_response += token
            for sentence in chunker.feed(token):
                if self.cancel.is_set():
                    interrupted = True
                    break
                sr, audio = tts_kokoro(sentence, config.voice, config.lang)
                if ttfa is None:
                    ttfa = time.time() - t_start
                    logger.info(f"[TTFA {ttfa:.2f}s] {sentence!r}")
                yield ("audio", (sr, audio))
            if interrupted:
                break

        if not interrupted:
            for sentence in chunker.flush():
                if self.cancel.is_set():
                    break
                sr, audio = tts_kokoro(sentence, config.voice, config.lang)
                yield ("audio", (sr, audio))

        if full_response.strip():
            self.history.append({"role": "user", "content": user_text})
            self.history.append({"role": "assistant", "content": full_response.strip()})
            self._maybe_summarize(config)

        status = "INTERRUPTED" if interrupted else "DONE"
        logger.info(
            f"[{status} {time.time() - t_start:.2f}s] "
            f"STT={stt_time:.2f}s TTFA={ttfa or 0:.2f}s "
            f"history={len(self.history)}"
        )
