"""One persistent Live session: realtime mic in, streaming audio out.

Owns a single WebSocket: setup handshake, realtimeInput audio chunks,
image turns, and dispatch of model audio / transcripts / interruption /
usage. Never raises out of the receive loop; callbacks are guarded.
Tested against an in-process fake socket; live use injects a real one.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

MODEL = "gemini-3.1-flash-live-preview"
ENDPOINT = "wss://yibuapi.com/v1beta/gemini/live"
INPUT_MIME = "audio/pcm;rate=16000"


class LiveSessionError(RuntimeError):
    pass


def _noop(*args: Any) -> None:
    return None


class LiveSession:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = MODEL,
        endpoint: str = ENDPOINT,
        on_audio: Callable[[bytes], None] | None = None,
        on_input_transcript: Callable[[str], None] | None = None,
        on_output_transcript: Callable[[str], None] | None = None,
        on_interrupted: Callable[[], None] | None = None,
        on_usage: Callable[[dict], None] | None = None,
        on_turn_end: Callable[[], None] | None = None,
        on_tool_call: Callable[[str, dict, str], None] | None = None,
        connector: Callable[[], Awaitable[Any]] | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._endpoint = endpoint
        self._on_audio = on_audio or _noop
        self._on_input_transcript = on_input_transcript or _noop
        self._on_output_transcript = on_output_transcript or _noop
        self._on_interrupted = on_interrupted or _noop
        self._on_usage = on_usage or _noop
        self._on_turn_end = on_turn_end or _noop
        self._on_tool_call = on_tool_call or _noop
        self._connector = connector or self._default_connector
        self._ws: Any | None = None
        self._send_lock = asyncio.Lock()
        self._recv_task: asyncio.Task | None = None
        self.sent_frames = 0
        self.received_events = 0

    @property
    def is_open(self) -> bool:
        return self._ws is not None

    async def _default_connector(self) -> Any:
        import websockets

        from ws_tls import gateway_ssl_context
        key = self._api_key
        if not key:
            from yibu_audit import require_env_api_key
            key = require_env_api_key()
        return await websockets.connect(
            self._endpoint,
            additional_headers={"Authorization": f"Bearer {key}"},
            ssl=gateway_ssl_context(),
            proxy=None, open_timeout=30, max_size=None)

    async def connect(self, timeout: float = 20.0) -> None:
        ws = await self._connector()
        try:
            from coordinator.live_turn import HIGHLIGHT_DECL
            await ws.send(json.dumps({"setup": {
                "model": f"models/{self._model}",
                "generationConfig": {"responseModalities": ["AUDIO"], "temperature": 0.2,
                    "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {
                        "voiceName": "Kore"}}}},
                "outputAudioTranscription": {},
                "inputAudioTranscription": {},
                "tools": [{"functionDeclarations": [HIGHLIGHT_DECL]}],
            }}))
            while True:
                event = json.loads(await asyncio.wait_for(ws.recv(), timeout))
                if event.get("error"):
                    raise LiveSessionError("setup rejected")
                if "setupComplete" in event:
                    break
        except Exception:
            try:
                await ws.close()
            except Exception:
                pass
            raise
        self._ws = ws
        self._recv_task = asyncio.create_task(self._receive_loop())

    async def send_audio(self, pcm: bytes) -> None:
        ws = self._ws
        if ws is None or not pcm:
            return
        frame = {"realtimeInput": {"mediaChunks": [{
            "mimeType": INPUT_MIME, "data": base64.b64encode(pcm).decode("ascii")}]}}
        try:
            async with self._send_lock:
                await ws.send(json.dumps(frame))
            self.sent_frames += 1
        except Exception:
            self._ws = None

    async def start_image_turn(self, jpeg: bytes, pcm: bytes, text: str) -> None:
        ws = self._ws
        if ws is None:
            raise LiveSessionError("session closed")
        frame = {"clientContent": {
            "turns": [{"role": "user", "parts": [
                {"text": text},
                {"inlineData": {"mimeType": "image/jpeg",
                                "data": base64.b64encode(jpeg).decode("ascii")}},
                {"inlineData": {"mimeType": "audio/pcm;rate=16000",
                                "data": base64.b64encode(pcm).decode("ascii")}}]}],
            "turnComplete": True}}
        async with self._send_lock:
            await ws.send(json.dumps(frame))
        self.sent_frames += 1

    async def _receive_loop(self) -> None:
        ws = self._ws
        try:
            while ws is not None:
                try:
                    raw = await ws.recv()
                except Exception:
                    return
                self.received_events += 1
                try:
                    self._dispatch(json.loads(raw))
                except Exception as exc:
                    logger.info("live session dispatch failed exception_class=%s",
                                type(exc).__name__)
        finally:
            self._ws = None

    def _dispatch(self, event: dict) -> None:
        if not isinstance(event, dict):
            return
        # Type names only (never content): reveals wedges like GoAway/errors.
        known = {"setupComplete", "serverContent", "toolCall"}
        for key in event:
            if key not in known and key != "usageMetadata":
                logger.info("live session event type=%s", str(key)[:32])
        tool_call = event.get("toolCall")
        if isinstance(tool_call, dict):
            calls = tool_call.get("functionCalls")
            if isinstance(calls, list):
                for call in calls:
                    if not isinstance(call, dict):
                        continue
                    args = call.get("args")
                    self._guard(self._on_tool_call, str(call.get("name") or ""),
                                args if isinstance(args, dict) else {},
                                str(call.get("id") or ""))
        if isinstance(event.get("usageMetadata"), dict):
            self._guard(self._on_usage, dict(event["usageMetadata"]))
        server = event.get("serverContent")
        if not isinstance(server, dict):
            return
        if server.get("interrupted"):
            self._guard(self._on_interrupted)
        if server.get("turnComplete"):
            self._guard(self._on_turn_end)
        heard = server.get("inputTranscription") or {}
        if isinstance(heard, dict) and heard.get("text"):
            self._guard(self._on_input_transcript, str(heard["text"]))
        said = server.get("outputTranscription") or {}
        if isinstance(said, dict) and said.get("text"):
            self._guard(self._on_output_transcript, str(said["text"]))
        turn = server.get("modelTurn") or {}
        if isinstance(turn, dict):
            for part in turn.get("parts") or []:
                if not isinstance(part, dict):
                    continue
                if part.get("text"):
                    self._guard(self._on_output_transcript, str(part["text"]))
                inline = part.get("inlineData")
                if isinstance(inline, dict) and inline.get("data"):
                    try:
                        pcm = base64.b64decode(str(inline["data"]))
                    except Exception:
                        continue
                    self._guard(self._on_audio, pcm)

    @staticmethod
    def _guard(fn: Callable, *args: Any) -> None:
        try:
            fn(*args)
        except Exception as exc:
            logger.info("live session callback failed exception_class=%s",
                        type(exc).__name__)

    async def send_tool_response(self, call_id: str, name: str, result: dict) -> None:
        """Answer one function call so the model keeps talking (silent tool)."""
        ws = self._ws
        if ws is None or not call_id:
            return
        frame = {"toolResponse": {"functionResponses": [{
            "id": call_id, "name": name, "response": {"result": result}}]}}
        try:
            async with self._send_lock:
                await ws.send(json.dumps(frame))
        except Exception:
            pass

    async def close(self) -> None:
        ws, task = self._ws, self._recv_task
        self._ws, self._recv_task = None, None
        if task is not None:
            task.cancel()
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
