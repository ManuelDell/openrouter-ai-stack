"""
whisper/app.py — Self-hosted audio transcription service.

OpenAI-compatible endpoint: POST /v1/audio/transcriptions
Runs faster-whisper on CPU (int8 quantized) — no GPU needed.
Model is downloaded once and cached in /data/models.
"""

import io
import logging
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from faster_whisper import WhisperModel

log = logging.getLogger("whisper-svc")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

MODEL_SIZE    = os.getenv("WHISPER_MODEL", "small")
MODEL_DIR     = os.getenv("WHISPER_MODEL_DIR", "/data/models")
DEVICE        = os.getenv("WHISPER_DEVICE", "cpu")
COMPUTE_TYPE  = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
MAX_FILE_SIZE = int(os.getenv("AUDIO_MAX_SIZE", str(25 * 1024 * 1024)))  # 25 MB

_model: Optional[WhisperModel] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model
    log.info("Loading Whisper model '%s' (%s/%s)...", MODEL_SIZE, DEVICE, COMPUTE_TYPE)
    Path(MODEL_DIR).mkdir(parents=True, exist_ok=True)
    _model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE,
                          download_root=MODEL_DIR)
    log.info("Whisper model ready.")
    yield


app = FastAPI(title="Whisper Transcription Service", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL_SIZE, "device": DEVICE}


@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form(default="whisper-1"),       # ignored — we use our model
    language: Optional[str] = Form(default=None),
    response_format: str = Form(default="json"),
    temperature: float = Form(default=0.0),
    timestamp_granularities: Optional[str] = Form(default=None),
):
    """OpenAI-compatible transcription endpoint."""
    if _model is None:
        raise HTTPException(503, detail="Model not loaded yet")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(413, detail=f"File too large (max {MAX_FILE_SIZE // 1024 // 1024}MB)")

    # Write to temp file — faster-whisper needs a file path
    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        segments, info = _model.transcribe(
            tmp_path,
            language=language,
            temperature=temperature,
            beam_size=5,
            vad_filter=True,
        )
        text = " ".join(seg.text.strip() for seg in segments)
    except Exception as e:
        log.error("Transcription failed: %s", e)
        raise HTTPException(500, detail=str(e))
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    log.info("Transcribed %.1fs audio (%s) → %d chars",
             info.duration, info.language, len(text))

    if response_format == "text":
        return PlainTextResponse(text)
    if response_format == "verbose_json":
        return JSONResponse({"task": "transcribe", "language": info.language,
                             "duration": info.duration, "text": text})
    return JSONResponse({"text": text})
