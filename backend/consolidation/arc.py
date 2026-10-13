"""
The arc test: what separates a Story from a pile of fragments (§4).

The five W's describe a fact; an arc describes an account. An atom that
names a person, a year and a town scores 3 on completeness with no
beginning, middle or end — which is the whole problem this replaces.
`completeness_score` stays on atoms as an extraction-quality signal; it is
not the bar for what a family sees.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

# Ordered as they appear in a telling. Resolution is last because it is the
# one a conversation most often hasn't reached yet.
SETTING = "setting"
EVENT = "event"
TURN = "turn"
RESOLUTION = "resolution"

ARC_ELEMENTS: tuple[str, ...] = (SETTING, EVENT, TURN, RESOLUTION)

# Decided in §13 D1: Setting + Event + Turn. Resolution is strongly
# preferred but not required — a great many real tellings never state how
# it ended, and refusing those would leave the shelf empty. Phase A data is
# meant to show whether this bar is right, so it is one named constant
# rather than a condition spread across the code.
REQUIRED_FOR_STORY: tuple[str, ...] = (SETTING, EVENT, TURN)


@dataclass
class ArcElement:
    """
    One element of the arc, and the turns that evidence it.

    `turn_ids` is not bookkeeping. §4: "An element the model inferred but
    the person did not say does not count." An element claiming to be
    present with no turn behind it is the model filling in a gap, so
    `is_evidenced` treats it as absent rather than trusting the flag.
    """

    present: bool = False
    summary: str = ""
    turn_ids: list[str] = field(default_factory=list)

    @property
    def is_evidenced(self) -> bool:
        return bool(self.present and self.turn_ids)

    @classmethod
    def from_json(cls, raw: object) -> "ArcElement":
        if not isinstance(raw, dict):
            return cls()
        turn_ids = raw.get("turn_ids") or []
        if not isinstance(turn_ids, list):
            turn_ids = []
        return cls(
            present=bool(raw.get("present")),
            summary=str(raw.get("summary") or ""),
            turn_ids=[str(t) for t in turn_ids if t],
        )

    def to_json(self) -> dict:
        return {
            "present": self.present,
            "summary": self.summary,
            "turn_ids": list(self.turn_ids),
        }


@dataclass
class Arc:
    """The four elements of one incident."""

    setting: ArcElement = field(default_factory=ArcElement)
    event: ArcElement = field(default_factory=ArcElement)
    turn: ArcElement = field(default_factory=ArcElement)
    resolution: ArcElement = field(default_factory=ArcElement)

    def element(self, name: str) -> ArcElement:
        return getattr(self, name)

    @property
    def evidenced(self) -> set[str]:
        return {name for name in ARC_ELEMENTS if self.element(name).is_evidenced}

    @property
    def missing(self) -> list[str]:
        """Elements not yet evidenced, in telling order — this is what
        Katha would go on to ask about (§9)."""
        return [name for name in ARC_ELEMENTS if name not in self.evidenced]

    @property
    def is_complete(self) -> bool:
        """Does this clear the bar for a Story (§4, D1)?"""
        return set(REQUIRED_FOR_STORY).issubset(self.evidenced)

    @property
    def source_turn_ids(self) -> list[str]:
        """Every turn evidencing any element, de-duplicated, order kept.

        This is the source set the compose step reads transcripts from, so
        an incident assembled across three sessions composes from all
        three — the point of the whole exercise (§4)."""
        seen: list[str] = []
        for name in ARC_ELEMENTS:
            for turn_id in self.element(name).turn_ids:
                if turn_id not in seen:
                    seen.append(turn_id)
        return seen

    def merged_with(self, other: "Arc") -> "Arc":
        """
        Fold a later session's arc into an earlier one (§6.2: a session
        that finally supplies the ending completes last week's thread).

        An element already evidenced is kept rather than overwritten: the
        first telling is the one closest to how they actually said it, and
        a later pass re-describing the same element tends to drift toward
        summary. Turn ids accumulate either way, so the source trail keeps
        both tellings.
        """
        merged = Arc()
        for name in ARC_ELEMENTS:
            mine, theirs = self.element(name), other.element(name)
            if mine.is_evidenced:
                keep = ArcElement(
                    present=True,
                    summary=mine.summary,
                    turn_ids=list(dict.fromkeys([*mine.turn_ids, *theirs.turn_ids])),
                )
            elif theirs.is_evidenced:
                keep = ArcElement(
                    present=True,
                    summary=theirs.summary,
                    turn_ids=list(dict.fromkeys([*mine.turn_ids, *theirs.turn_ids])),
                )
            else:
                keep = ArcElement(
                    present=False,
                    summary=mine.summary or theirs.summary,
                    turn_ids=list(dict.fromkeys([*mine.turn_ids, *theirs.turn_ids])),
                )
            setattr(merged, name, keep)
        return merged

    @classmethod
    def from_json(cls, raw: object) -> "Arc":
        raw = raw if isinstance(raw, dict) else {}
        return cls(
            **{name: ArcElement.from_json(raw.get(name)) for name in ARC_ELEMENTS}
        )

    def to_json(self) -> dict:
        return {name: self.element(name).to_json() for name in ARC_ELEMENTS}


def describe_missing(arc: Arc) -> str:
    """Human-readable gap list for the review report."""
    missing = arc.missing
    return ", ".join(missing) if missing else "none"


def any_complete(arcs: Iterable[Arc]) -> bool:
    return any(arc.is_complete for arc in arcs)


def first_present_summary(arc: Arc) -> Optional[str]:
    for name in ARC_ELEMENTS:
        element = arc.element(name)
        if element.is_evidenced:
            return element.summary
    return None
