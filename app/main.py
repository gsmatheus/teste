"""Servidor de transcrição de voz (pt-BR) do orq: faster-whisper em CPU, uma API pequena e fechada.

Endpoint compatível com a API de transcrição da OpenAI:
    POST /v1/audio/transcriptions   (multipart: file, language, prompt, response_format)
    GET  /healthz

Regras: token por pessoa (Bearer), arquivo e duração com teto, uma transcrição por vez com fila
curta (429 quando cheia), limite por token, sem gravar áudio nem texto em disco ou log.
"""
import hmac
import os
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from collections import defaultdict, deque

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, PlainTextResponse

MODEL_NAME = os.getenv("STT_MODEL", "large-v3-turbo")
THREADS = int(os.getenv("STT_THREADS", "8"))
MAX_BYTES = int(os.getenv("STT_MAX_BYTES", str(8 * 1024 * 1024)))
MAX_SECONDS = float(os.getenv("STT_MAX_SECONDS", "90"))
MAX_QUEUE = int(os.getenv("STT_MAX_QUEUE", "2"))  # rodando + esperando
RATE_PER_MIN = int(os.getenv("STT_RATE_PER_MIN", "10"))
DEFAULT_LANG = os.getenv("STT_LANGUAGE", "pt")
SAMPLE_RATE = 16000


def parse_tokens(raw: str) -> dict:
    """STT_TOKENS="ana:segredo1,beto:segredo2" -> {"ana": "segredo1", ...}"""
    out = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        name, _, tok = part.partition(":")
        if not name or len(tok) < 16:
            raise SystemExit("STT_TOKENS: cada item é nome:token, com token de 16+ caracteres")
        out[name] = tok
    return out


TOKENS = parse_tokens(os.getenv("STT_TOKENS", ""))

_model = None
_model_lock = threading.Lock()
_slot = threading.Semaphore(1)  # uma transcrição por vez
_pending = 0
_pending_lock = threading.Lock()
_hits = defaultdict(deque)  # nome -> instantes dos últimos pedidos
_hits_lock = threading.Lock()

@asynccontextmanager
async def lifespan(_app):
    if not TOKENS:
        raise SystemExit("STT_TOKENS vazio: defina ao menos uma pessoa (nome:token)")
    await run_in_threadpool(load_model)  # modelo carregado antes de aceitar pedidos
    yield


app = FastAPI(title="orq-stt", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


def load_model():
    global _model
    with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel

            _model = WhisperModel(
                MODEL_NAME,
                device="cpu",
                compute_type="int8",
                cpu_threads=THREADS,
                download_root=os.getenv("STT_CACHE", "/data/models"),
            )
    return _model


def who(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "token ausente")
    given = authorization[7:].strip()
    found = None
    for name, tok in TOKENS.items():  # sem parar no primeiro: tempo não revela quem acertou
        if hmac.compare_digest(given.encode(), tok.encode()):
            found = name
    if found is None:
        raise HTTPException(401, "token inválido")
    return found


def check_rate(name: str):
    now = time.monotonic()
    with _hits_lock:
        q = _hits[name]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= RATE_PER_MIN:
            raise HTTPException(429, "muitos pedidos, espere um pouco", headers={"Retry-After": "10"})
        q.append(now)


def transcribe_file(path: str, language: str, prompt: str | None) -> tuple[str, float]:
    from faster_whisper.audio import decode_audio

    audio = decode_audio(path, sampling_rate=SAMPLE_RATE)
    seconds = len(audio) / SAMPLE_RATE
    if seconds > MAX_SECONDS:
        raise HTTPException(413, f"áudio de {seconds:.0f} s passa do teto de {MAX_SECONDS:.0f} s")
    model = load_model()
    segments, _ = model.transcribe(
        audio,
        language=language or None,
        initial_prompt=prompt or None,
        vad_filter=True,
        vad_parameters={"speech_pad_ms": 300},
        condition_on_previous_text=False,
    )
    text = " ".join(s.text.strip() for s in segments).strip()
    return text, seconds


def run_exclusive(path: str, language: str, prompt: str | None):
    global _pending
    with _pending_lock:
        if _pending >= MAX_QUEUE:
            raise HTTPException(429, "servidor ocupado, tente de novo", headers={"Retry-After": "5"})
        _pending += 1
    try:
        with _slot:
            return transcribe_file(path, language, prompt)
    finally:
        with _pending_lock:
            _pending -= 1


@app.get("/healthz")
def healthz():
    return {"ok": True, "model": MODEL_NAME, "loaded": _model is not None}


@app.post("/v1/audio/transcriptions")
async def transcriptions(
    file: UploadFile = File(...),
    model: str = Form("ignorado"),
    language: str = Form(DEFAULT_LANG),
    prompt: str = Form(""),
    response_format: str = Form("json"),
    authorization: str | None = Header(default=None),
):
    name = who(authorization)
    check_rate(name)
    if response_format not in ("json", "text"):
        raise HTTPException(400, "response_format: json ou text")
    prompt = prompt[:600]
    # Lê no máximo MAX_BYTES + 1 e recusa se passar: o teto vale mesmo sem Content-Length.
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(413, f"arquivo passa de {MAX_BYTES // (1024 * 1024)} MB")
    if not data:
        raise HTTPException(400, "arquivo vazio")
    fd, path = tempfile.mkstemp(prefix="stt-", suffix=".audio", dir="/tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        del data
        try:
            text, seconds = await run_in_threadpool(run_exclusive, path, language, prompt)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(400, "não consegui ler esse áudio")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if response_format == "text":
        return PlainTextResponse(text)
    return JSONResponse({"text": text, "duration": round(seconds, 2)})
