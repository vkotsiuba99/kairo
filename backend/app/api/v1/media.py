"""
Сервіс Media: ілюстрації, відео й озвучення.

Генерація медіа — найважча частина системи (дифузійна модель або мережевий
генератор, ffmpeg), тому вона винесена в окремий сервіс, який масштабується
незалежно від Content і може працювати на вузлах з GPU. Файли лежать у сховищі
медіа (том media), звідки їх віддає /media і читає Publishing для завантаження в мережі.
"""

import asyncio
import io
import math
import os
import struct
import time
import wave

from fastapi import APIRouter, Response
from pydantic import BaseModel

from app.core.config import settings

router = APIRouter(prefix="/media", tags=["Media"])


class ImageRequest(BaseModel):
    folder: str              # каталог матеріалу в сховищі (номер завдання генерації)
    prompt: str
    topic: str = ""
    title: str = ""


class VideoRequest(BaseModel):
    folder: str
    script_text: str
    image: str               # шлях до ключового кадру в сховищі


class TTSRequest(BaseModel):
    text: str
    voice: str = "ukrainian_female"


# Обмеження одночасних генерацій на процес (зворотний тиск): без нього під навантаженням
# кожен процес брав десятки рендерів і відео паралельно, пам'ять росла без меж, а
# контейнери впиралися в ліміт. Семафор свій для кожного циклу подій (у тестах цикли різні).
_slots: dict = {}


def _slot() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if id(loop) not in _slots:
        _slots[id(loop)] = asyncio.Semaphore(settings.MEDIA_CONCURRENCY)
    return _slots[id(loop)]


def _media_dir(folder: str) -> str:
    # каталог — лише ім'я без шляху, щоб запит не вийшов за межі сховища
    path = os.path.join(settings.DATA_MEDIA_PATH, os.path.basename(folder) or "misc")
    os.makedirs(path, exist_ok=True)
    return path


@router.post("/images")
async def generate_image(req: ImageRequest):
    from app.generation.image_generator import ImageGenerator

    folder = os.path.basename(req.folder) or "misc"
    img_path = os.path.join(_media_dir(folder), "keyframe_1.jpg")
    # синхронний генератор (мережевий запит до 75 с, PIL) — в окремому потоці
    async with _slot():
        await asyncio.to_thread(
            ImageGenerator().generate_image,
            prompt=req.prompt,
            output_path=img_path,
            topic=req.topic,
            title=req.title,
        )
    return {
        "image": img_path,
        # параметр t скидає кеш браузера після повторної генерації
        "image_url": f"/media/{folder}/keyframe_1.jpg?t={int(time.time())}",
        "media_prompt": req.prompt,
    }


@router.post("/videos")
async def generate_video(req: VideoRequest):
    from app.generation.video_composer import VideoComposer

    folder = os.path.basename(req.folder) or "misc"
    vid_path = os.path.join(_media_dir(folder), "video.mp4")
    async with _slot():
        await VideoComposer().compose_video(
            script_text=req.script_text,
            keyframe_image_paths=[req.image],
            output_mp4_path=vid_path,
            is_vertical_shorts=True,
        )
    return {"video": vid_path, "video_url": f"/media/{folder}/video.mp4"}


@router.post("/synthesize")
async def synthesize_speech(req: TTSRequest):
    """
    Озвучення українською (CPU). Тут може працювати модель robinhad/ukrainian-tts;
    без неї генерується гармонійний сигнал потрібної тривалості.
    """
    sample_rate = 22050
    duration_sec = min(max(len(req.text.split()) * 0.45, 2.5), 60.0)
    n_samples = int(sample_rate * duration_sec)

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n_samples):
            t = i / sample_rate
            val = int(
                1200.0 * math.sin(2.0 * math.pi * 220.0 * t)
                + 600.0 * math.sin(2.0 * math.pi * 440.0 * t)
                + 300.0 * math.sin(2.0 * math.pi * 880.0 * t)
            )
            frames.extend(struct.pack("<h", max(-32767, min(32767, val))))
        wav_file.writeframes(frames)
    return Response(content=buffer.getvalue(), media_type="audio/wav")
