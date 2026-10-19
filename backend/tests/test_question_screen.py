"""
core/question_screen.py (CONVERSATION_CARDS.md §10.2). Mocked; TC-18
measures the real labels. When in doubt a question is held for a person.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core.question_screen import FALLBACK, LABELS, screen


def _reply(content):
    return SimpleNamespace(content=content, input_tokens=1, output_tokens=1)


@pytest.fixture
def model():
    mock = AsyncMock()
    with patch("core.question_screen.llm.chat", new=mock):
        yield mock


@pytest.mark.parametrize(
    "text", ["stop", "STOP", " Stop. ", "unsubscribe", "band karo"]
)
async def test_stop_works_without_the_model(model, text):
    result = await screen(text, "Amma")
    assert result.stop is True
    assert result.label == "other"
    model.assert_not_awaited()


async def test_stop_inside_a_sentence_is_not_an_opt_out(model):
    model.return_value = _reply(json.dumps({"label": "ok"}))
    result = await screen("Ask her why the bus didn't stop at their village", "Amma")
    assert result.stop is False
    assert result.label == "ok"


@pytest.mark.parametrize("label", LABELS)
async def test_known_labels_pass_through(model, label):
    model.return_value = _reply(json.dumps({"label": label}))
    result = await screen("Which film was it?", "Amma")
    assert result.label == label
    assert model.await_args.kwargs["purpose"] == "question_screen"


async def test_a_failed_call_holds_the_question_for_the_operator(model):
    model.side_effect = RuntimeError("Anthropic API error: overloaded")
    result = await screen("Who gets the house?", "Amma")
    assert result.label == FALLBACK == "sensitive"


async def test_an_unreadable_answer_holds_the_question(model):
    model.return_value = _reply("It's probably fine")
    assert (await screen("What was Ammachi like?", "Amma")).label == "sensitive"
