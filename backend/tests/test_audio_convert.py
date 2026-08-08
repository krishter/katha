import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from media.audio_convert import convert_wav_to_ogg


def _make_proc(returncode=0, stdout=b"ogg-bytes", stderr=b""):
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


async def test_convert_wav_to_ogg_returns_stdout_on_success():
    proc = _make_proc(returncode=0, stdout=b"ogg-bytes")

    with patch(
        "media.audio_convert.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=proc),
    ):
        result = await convert_wav_to_ogg(b"wav-bytes")

    assert result == b"ogg-bytes"


async def test_convert_wav_to_ogg_raises_on_nonzero_exit():
    proc = _make_proc(returncode=1, stderr=b"unsupported codec")

    with patch(
        "media.audio_convert.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=proc),
    ):
        with pytest.raises(RuntimeError, match="ffmpeg conversion failed"):
            await convert_wav_to_ogg(b"wav-bytes")


async def test_convert_wav_to_ogg_kills_process_and_raises_on_timeout():
    proc = _make_proc()

    async def _raise_timeout(coro, timeout):
        coro.close()  # avoid an "unawaited coroutine" warning from the mock
        raise asyncio.TimeoutError()

    with (
        patch(
            "media.audio_convert.asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=proc),
        ),
        patch(
            "media.audio_convert.asyncio.wait_for",
            new=AsyncMock(side_effect=_raise_timeout),
        ),
    ):
        with pytest.raises(RuntimeError, match="timed out"):
            await convert_wav_to_ogg(b"wav-bytes")

    proc.kill.assert_called_once()
    proc.wait.assert_awaited_once()
