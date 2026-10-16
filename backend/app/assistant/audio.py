from __future__ import annotations

import array
import io
import math
import subprocess
import sys
import wave

from app.assistant.operations import refused
from app.core.config import VoiceSettings


def normalize_speech(data: bytes) -> tuple[bytes, float]:
    """OpenAI's streamed WAV may have placeholder RIFF/data lengths."""
    if len(data) > 8_000_000:
        raise refused("読み上げ音声がサイズ上限を超えています。", "VOICE_SPEECH_INVALID", 502)
    try:
        with wave.open(io.BytesIO(data), "rb") as source:
            params = source.getparams()
            frames = source.readframes(source.getnframes())
            frame_bytes = params.nchannels * params.sampwidth
            duration = len(frames) / (frame_bytes * params.framerate)
        if not frames or len(frames) % frame_bytes or duration > 90:
            raise ValueError("Invalid speech frames")
        output = io.BytesIO()
        with wave.open(output, "wb") as target:
            target.setparams(params._replace(nframes=len(frames) // frame_bytes))
            target.writeframes(frames)
        return output.getvalue(), duration
    except (ValueError, wave.Error, EOFError) as error:
        raise refused("読み上げ音声の形式が不正です。", "VOICE_SPEECH_INVALID", 502) from error


def normalize_audio(data: bytes, mime: str, settings: VoiceSettings) -> bytes:
    signatures = {
        "audio/wav": data.startswith(b"RIFF") and data[8:12] == b"WAVE",
        "audio/webm": data.startswith(b"\x1aE\xdf\xa3"),
        "audio/ogg": data.startswith(b"OggS"),
        "audio/mp4": data[4:8] == b"ftyp",
    }
    if not data or len(data) > settings.max_audio_bytes or not signatures.get(mime):
        raise refused("音声のサイズまたは内容形式が許可範囲外です。", "VOICE_AUDIO_INVALID", 422)
    if not settings.ffmpeg_path:
        raise refused(
            "録音処理用FFmpegが見つかりません。手入力を使用してください。",
            "VOICE_DECODER_MISSING",
            503,
        )
    try:
        decoded = subprocess.run(
            [
                settings.ffmpeg_path,
                "-v",
                "error",
                "-nostdin",
                "-protocol_whitelist",
                "pipe",
                "-i",
                "pipe:0",
                "-t",
                str(settings.max_record_seconds + 1),
                "-f",
                "s16le",
                "-ac",
                "1",
                "-ar",
                "16000",
                "pipe:1",
            ],
            input=data,
            capture_output=True,
            timeout=15,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        ).stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise refused(
            "音声を解読できませんでした。録り直すか手入力を使用してください。",
            "VOICE_AUDIO_INVALID",
            422,
        ) from error
    duration = len(decoded) / 32000
    if duration < 0.3 or duration > settings.max_record_seconds:
        raise refused(
            f"録音は0.3秒以上、{settings.max_record_seconds}秒以内にしてください。",
            "VOICE_DURATION_INVALID",
            422,
        )
    samples = array.array("h", decoded)
    if sys.byteorder != "little":
        samples.byteswap()
    rms = math.sqrt(sum(float(value) ** 2 for value in samples) / len(samples)) / 32768
    if rms < 0.003:
        raise refused(
            "音声が検出できません。マイクを確認して録り直してください。", "VOICE_SILENCE", 422
        )
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(decoded)
    return output.getvalue()
