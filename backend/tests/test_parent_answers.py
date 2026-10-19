"""
core/parent_answers.py (CONVERSATION_CARDS.md §9.3): when in doubt, it is
not a yes. Every model call is mocked; TC-15 measures the real labels.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core import parent_answers
from core.parent_answers import OFFER, READBACK, SHARE, classify

QUESTION = "Shall I send this to Priya?"


def _reply(**payload):
    return SimpleNamespace(content=json.dumps(payload), input_tokens=1, output_tokens=1)


@pytest.fixture
def model():
    mock = AsyncMock()
    with patch("core.parent_answers.llm.chat", new=mock):
        yield mock


async def test_an_explicit_yes_quoting_her_is_approve(model):
    model.return_value = _reply(label="approve", span="haan bhej do")
    result = await classify(READBACK, QUESTION, "Haan bhej do, bahut accha hai")
    assert result.label == "approve"
    assert result.span == "haan bhej do"
    assert model.await_args.kwargs["purpose"] == "parent_answer_classify"


async def test_a_yes_in_her_own_script_is_found(model):
    model.return_value = _reply(label="approve", span="சரி, அனுப்புங்க")
    result = await classify(READBACK, QUESTION, "சரி,  அனுப்புங்க")
    assert result.label == "approve"


@pytest.mark.parametrize(
    "payload",
    [
        {"label": "approve", "span": ""},
        {"label": "approve"},
        # The model's words, not hers.
        {"label": "approve", "span": "yes, send it"},
        {"label": "yes", "span": "sure"},
    ],
)
async def test_a_yes_that_cannot_quote_her_is_not_a_yes(model, payload):
    model.return_value = _reply(**payload)
    kind = READBACK if payload["label"] == "approve" else OFFER
    result = await classify(kind, QUESTION, "Hmm, okay okay, so then my father")
    assert result.label == parent_answers.SAFE[kind]


@pytest.mark.parametrize("kind", [OFFER, READBACK, SHARE])
async def test_every_failure_is_the_label_that_sends_nothing(model, kind):
    for failure in (
        RuntimeError("Anthropic API error: overloaded"),
        _reply(label="definitely_yes", span="yes"),
        SimpleNamespace(content="I think she agrees", input_tokens=1, output_tokens=1),
    ):
        if isinstance(failure, Exception):
            model.side_effect = failure
        else:
            model.side_effect = None
            model.return_value = failure
        result = await classify(kind, QUESTION, "yes yes")
        assert result.label == parent_answers.SAFE[kind]
        assert result.label not in parent_answers.POSITIVE


async def test_silence_is_not_an_answer_and_costs_no_call(model):
    result = await classify(SHARE, "Shall I share?", "   ")
    assert result.label == "unclear"
    model.assert_not_awaited()


@pytest.mark.parametrize("label", ["correction", "keep_private", "not_now", "unclear"])
async def test_cautious_labels_stand_without_a_span(model, label):
    model.return_value = _reply(label=label, span="")
    result = await classify(READBACK, QUESTION, "it was my uncle, not my father")
    assert result.label == label


def test_the_safe_label_never_moves_a_card():
    for kind, safe in parent_answers.SAFE.items():
        assert safe in parent_answers.LABELS[kind]
        assert safe not in parent_answers.POSITIVE


async def test_an_unknown_kind_is_a_programming_error(model):
    with pytest.raises(ValueError):
        await classify("send_now", QUESTION, "yes")
