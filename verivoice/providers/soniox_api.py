"""Soniox live transcription, finalized-token language ratios and model metadata.

The token confidence field is transcription confidence, not language consistency.
No credentials or transcript text are logged or exposed to the browser.
"""
import asyncio
from collections import Counter
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
import json
import math
import re
import time

import httpx
from websockets.asyncio.client import connect


TOKEN_CONFIDENCE_THRESHOLD = .80


class SonioxError(RuntimeError):
    pass


def language_code(value):
    if not isinstance(value, str):
        return None
    code = value.lower().split('-')[0]
    return code if re.fullmatch(r'[a-z]{2,3}', code) and code not in {'und', 'zxx'} else None


@dataclass(frozen=True)
class LanguageScore:
    score: float | None
    registered_language: str
    counts: dict[str, int]
    total_tokens: int
    unknown_tokens: int
    dominant_other: str | None
    dominant_other_name: str | None
    registered_language_name: str
    aggregation_ms: float = 0
    excluded_uncertain_tokens: int = 0


def score_tokens(tokens, registered_language, names=None):
    started = time.perf_counter()
    registered = language_code(registered_language)
    if registered is None:
        raise SonioxError('Invalid registered language.')
    names = names or {}
    counts = Counter()
    total = unknown = excluded = 0
    seen = set()
    for token in tokens:
        text = token.get('text', '')
        if token.get('is_final') is not True or token.get('translation_status') == 'translation':
            continue
        if not isinstance(text, str) or text.startswith('<') or not any(char.isalnum() for char in text):
            continue
        code = language_code(token.get('language'))
        # Finalized tokens are incremental. Defend against duplicate delivery,
        # while retaining actual repeated words at different timestamps.
        if token.get('start_ms') is not None and token.get('end_ms') is not None:
            key = (token['start_ms'], token['end_ms'], text, code)
            if key in seen:
                continue
            seen.add(key)
        confidence = token.get('confidence')
        if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                or not math.isfinite(confidence) or not TOKEN_CONFIDENCE_THRESHOLD <= confidence <= 1):
            excluded += 1
            continue
        total += 1
        if code:
            counts[code] += 1
        else:
            unknown += 1
    others = {code: count for code, count in counts.items() if code != registered}
    dominant = None
    if others:
        largest = max(others.values())
        leaders = [code for code, count in others.items() if count == largest]
        if len(leaders) == 1:
            dominant = leaders[0]
    # Untagged text cannot count as registered-language evidence. Completely
    # untagged or empty output is unavailable, rather than a passing score.
    value = counts[registered] / total if total and counts else None
    return LanguageScore(value, registered, dict(counts), total, unknown, dominant,
        names.get(dominant, dominant), names.get(registered, registered),
        (time.perf_counter() - started) * 1000, excluded)


class SonioxSession:
    def __init__(self, socket, registered_language, names):
        self.socket, self.registered_language, self.names = socket, registered_language, names
        self.lock = asyncio.Lock()

    async def score(self, pcm):
        if not pcm or len(pcm) % 2:
            raise ValueError('Expected mono PCM16 audio.')
        async with self.lock:
            # Same bounded audio as Hiya, finalized independently per observation.
            await self.socket.send(pcm)
            await self.socket.send(bytes(6400))  # 200 ms trailing silence for finalization.
            await self.socket.send(json.dumps({'type': 'finalize'}))
            tokens = []
            while True:
                data = json.loads(await self.socket.recv())
                if data.get('error_code') or data.get('error_type'):
                    if data.get('error_type') == 'organization_balance_exhausted':
                        raise SonioxError('Language verification is unavailable because the Soniox account balance is exhausted. Add funds to Soniox, then call again.')
                    raise SonioxError('Soniox language verification is unavailable. Check the provider setup, then call again.')
                finalized = False
                for token in data.get('tokens', []):
                    if token.get('text') == '<fin>' and token.get('is_final'):
                        finalized = True
                    elif token.get('is_final'):
                        tokens.append(token)
                if finalized:
                    return score_tokens(tokens, self.registered_language, self.names)
                if data.get('finished'):
                    raise SonioxError('Soniox ended before finalizing the recording.')

    async def keepalive(self):
        while True:
            await asyncio.sleep(5)
            await self.socket.send(json.dumps({'type': 'keepalive'}))


class SonioxAPI:
    def __init__(self, settings):
        self.settings = settings
        self.names = None

    async def list_models(self):
        self.settings.require('soniox_api_key')
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get('https://api.soniox.com/v1/models',
                headers={'Authorization': 'Bearer ' + self.settings.soniox_api_key})
            if response.is_error:
                raise SonioxError(f'Soniox model lookup failed (HTTP {response.status_code}).')
            return response.json()['models']

    @asynccontextmanager
    async def connect(self, registered_language):
        self.settings.require('soniox_api_key', 'soniox_model')
        if self.names is None:
            models = await self.list_models()
            model = next((item for item in models if item['id'] == self.settings.soniox_model), None)
            if model is None:
                raise SonioxError('Configured Soniox model is unavailable.')
            self.names = {item['code']: item['name'] for item in model.get('languages', [])}
        if registered_language not in self.names:
            raise SonioxError('Registered language is unsupported by the selected model.')
        async with connect('wss://stt-rt.soniox.com/transcribe-websocket',
                additional_headers={'Authorization': 'Bearer ' + self.settings.soniox_api_key},
                open_timeout=15, close_timeout=3, max_size=2**20) as socket:
            await socket.send(json.dumps({'model': self.settings.soniox_model,
                'audio_format': 'pcm_s16le', 'sample_rate': 16000, 'num_channels': 1,
                'enable_language_identification': True}))
            # Do not force or restrict recognition to the registered language:
            # unexpected languages are precisely the evidence being measured.
            session = SonioxSession(socket, registered_language, self.names)
            keepalive = asyncio.create_task(session.keepalive())
            try:
                yield session
            finally:
                keepalive.cancel()
                await asyncio.gather(keepalive, return_exceptions=True)
                with suppress(Exception):
                    await socket.send('')
