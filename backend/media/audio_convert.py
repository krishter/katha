from __future__ import annotations

import asyncio


async def convert_wav_to_ogg(wav_bytes: bytes) -> bytes:
    """
    Convert WAV bytes to OGG/Opus using ffmpeg subprocess.
    Required because Sarvam TTS returns WAV but WhatsApp expects OGG/Opus.
    ffmpeg must be available in the environment (installed via apt / Docker).
    """
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-f",
        "wav",
        "-i",
        "pipe:0",
        "-c:a",
        "libopus",
        "-f",
        "ogg",
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=wav_bytes), timeout=10.0
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError("ffmpeg conversion timed out after 10s") from None

    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg conversion failed: {stderr.decode()[:200]}")
    return stdout
