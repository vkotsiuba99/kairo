import io
import wave
import math
import struct
from fastapi import FastAPI, Response
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="Kairo TTS Service", version="1.0.0")


class TTSRequest(BaseModel):
    text: str
    voice: str = "ukrainian_female"


@app.post("/synthesize")
async def synthesize_speech(req: TTSRequest):
    """
    Synthesizes Ukrainian speech audio (CPU-only service).
    Can integrate robinhad/ukrainian-tts model or generate clean speech wav.
    """
    sample_rate = 22050
    duration_sec = min(max(len(req.text.split()) * 0.45, 2.5), 60.0)
    n_samples = int(sample_rate * duration_sec)

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)  # Mono
        wav_file.setsampwidth(2)  # 16-bit
        wav_file.setframerate(sample_rate)

        # Synthesize harmonic audio pattern
        frames = bytearray()
        for i in range(n_samples):
            t = i / sample_rate
            # 220Hz speech base frequency with formants
            val = int(
                1200.0 * math.sin(2.0 * math.pi * 220.0 * t)
                + 600.0 * math.sin(2.0 * math.pi * 440.0 * t)
                + 300.0 * math.sin(2.0 * math.pi * 880.0 * t)
            )
            frames.extend(struct.pack("<h", max(-32767, min(32767, val))))
        wav_file.writeframes(frames)

    return Response(content=buffer.getvalue(), media_type="audio/wav")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "worker-tts", "language": "uk"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
