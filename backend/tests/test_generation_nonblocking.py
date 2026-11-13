import asyncio
import os
import time

import pytest

from app.generation.video_composer import VideoComposer


@pytest.mark.asyncio
async def test_compose_video_does_not_block_event_loop(tmp_path):
    """Audio fallback and video assembly are CPU/subprocess work: other requests must keep running."""
    comp = VideoComposer()
    comp.tts_url = "http://127.0.0.1:9"          # TTS unreachable -> CPU-bound tone fallback
    comp.ffmpeg_bin = "ffmpeg-missing-for-test"  # no ffmpeg -> metadata container
    gaps, stop = [], False

    async def ticker():
        last = time.perf_counter()
        while not stop:
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    tick = asyncio.create_task(ticker())
    out = await comp.compose_video("word " * 20, [], str(tmp_path / "v.mp4"))  # 8 s of audio
    stop = True
    await tick

    assert os.path.exists(out) and os.path.exists(str(tmp_path / "v_audio.wav"))
    assert len(gaps) > 5
    assert max(gaps) < 0.25, f"event loop stalled for {max(gaps):.2f} s"
