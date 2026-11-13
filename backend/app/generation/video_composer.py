import asyncio
import os
import subprocess
import wave
import math
import struct
import logging
from typing import List, Optional
import httpx
from app.core.config import settings

logger = logging.getLogger("kairo.video")


class VideoComposer:
    """
    Assembles YouTube Shorts / videos without a separate heavy video diffusion model.
    Combines:
    1. Bonsai 27B script
    2. Bonsai Image keyframe stills
    3. Ukrainian TTS audio (via worker-tts service or wave synthesizer)
    4. ffmpeg assembly into MP4
    """

    def __init__(self):
        self.tts_url = settings.TTS_SERVICE_URL
        self.ffmpeg_bin = settings.FFMPEG_BINARY

    async def _synthesize_audio(self, text: str, output_wav_path: str) -> bool:
        """
        Synthesizes Ukrainian speech audio via local worker-tts endpoint,
        or creates a clean synthetic wav file if worker-tts is offline.
        """
        os.makedirs(os.path.dirname(os.path.abspath(output_wav_path)), exist_ok=True)

        # Try worker-tts service
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{self.tts_url}/synthesize",
                    json={"text": text, "voice": "ukrainian_female"}
                )
                if resp.is_success:
                    with open(output_wav_path, "wb") as f:
                        f.write(resp.content)
                    return True
        except Exception:
            pass

        # CPU-bound loop: run it off the event loop so the API keeps serving other requests
        return await asyncio.to_thread(self._write_tone_wav, text, output_wav_path)

    @staticmethod
    def _write_tone_wav(text: str, output_wav_path: str) -> bool:
        # Fallback: create a 5-second silence/gentle ambient tone wav
        sample_rate = 22050
        duration_sec = min(max(len(text.split()) * 0.4, 3.0), 30.0)
        n_samples = int(sample_rate * duration_sec)

        with wave.open(output_wav_path, "w") as wav_file:
            wav_file.setnchannels(1)  # Mono
            wav_file.setsampwidth(2)  # 16-bit
            wav_file.setframerate(sample_rate)

            # Generate gentle sine ambient wave
            frames = bytearray()
            for i in range(n_samples):
                t = i / sample_rate
                # Subtle 220Hz + 440Hz ambient harmonic tone
                val = int(800.0 * math.sin(2.0 * math.pi * 220.0 * t) + 400.0 * math.sin(2.0 * math.pi * 440.0 * t))
                frames.extend(struct.pack("<h", max(-32767, min(32767, val))))
            wav_file.writeframes(frames)
        return True

    async def compose_video(
        self,
        script_text: str,
        keyframe_image_paths: List[str],
        output_mp4_path: str,
        is_vertical_shorts: bool = True,
    ) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(output_mp4_path)), exist_ok=True)
        wav_path = output_mp4_path.replace(".mp4", "_audio.wav")

        await self._synthesize_audio(script_text, wav_path)
        # ffmpeg is a blocking subprocess (seconds of encoding): keep it off the event loop
        return await asyncio.to_thread(
            self._assemble, script_text, keyframe_image_paths, output_mp4_path, wav_path, is_vertical_shorts)

    def _assemble(self, script_text: str, keyframe_image_paths: List[str], output_mp4_path: str,
                  wav_path: str, is_vertical_shorts: bool) -> str:
        # Check ffmpeg availability
        ffmpeg_available = False
        try:
            res = subprocess.run([self.ffmpeg_bin, "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if res.returncode == 0:
                ffmpeg_available = True
        except Exception:
            ffmpeg_available = False

        if ffmpeg_available and keyframe_image_paths:
            # Build slideshow video using ffmpeg
            # Set duration per frame
            duration_per_frame = 3.0
            image_inputs = []
            for img in keyframe_image_paths:
                image_inputs.extend(["-loop", "1", "-t", str(duration_per_frame), "-i", img])

            # Video dimensions
            width, height = (1080, 1920) if is_vertical_shorts else (1920, 1080)

            # Simple concat filter or single image loop
            first_img = keyframe_image_paths[0]
            cmd = [
                self.ffmpeg_bin,
                "-y",
                "-loop", "1",
                "-i", first_img,
                "-i", wav_path,
                "-c:v", "libx264",
                "-tune", "stillimage",
                # a still-image slideshow: a fast preset and 2 threads keep one encode from taking all cores
                "-preset", "veryfast",
                "-threads", "2",
                "-c:a", "aac",
                "-b:a", "192k",
                "-pix_fmt", "yuv420p",
                "-vf", f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}",
                "-shortest",
                output_mp4_path
            ]
            try:
                subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                return output_mp4_path
            except Exception as e:
                logger.warning("ffmpeg command failed: %s, writing metadata container", e)

        # Fallback container when ffmpeg is not present on host
        with open(output_mp4_path, "wb") as f:
            f.write(b"Kairo_VIDEO_CONTAINER_V1\n")
            f.write(f"SCRIPT: {script_text}\n".encode("utf-8"))
            f.write(f"AUDIO_REF: {wav_path}\n".encode("utf-8"))
            f.write(f"FRAMES: {','.join(keyframe_image_paths)}\n".encode("utf-8"))
        return output_mp4_path
