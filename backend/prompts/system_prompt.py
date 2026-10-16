from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from prompts.domains import get_domain, get_domain_sequence

if TYPE_CHECKING:
    from core.session_manager import SessionState

# Ceiling on open threads rendered into Layer 3. Retrieval fetches far more
# atoms than this (orchestrator._RETRIEVAL_TOP_K) so that older domains stay
# reachable; this is what stops that width becoming prompt bloat. 12 gives
# roughly one or two threads per life domain — enough to offer the model a
# choice, few enough that it still reads them.
_MAX_RENDERED_THREADS = 12

# Maps BCP-47 language codes to natural language names
_LANGUAGE_NAMES: dict[str, str] = {
    "hi-IN": "Hindi",
    "ta-IN": "Tamil",
    "te-IN": "Telugu",
    "kn-IN": "Kannada",
    "ml-IN": "Malayalam",
    "mr-IN": "Marathi",
    "bn-IN": "Bengali",
    "gu-IN": "Gujarati",
    "pa-IN": "Punjabi",
    "or-IN": "Odia",
    "as-IN": "Assamese",
    "ur-IN": "Urdu",
    "en-IN": "English",
}

SUPPORTED_LANGUAGES: list[str] = list(_LANGUAGE_NAMES.keys())


@dataclass
class UserProfile:
    name: str
    preferred_language: str  # BCP-47
    onboarding_context: str
    # "she/her", "he/him", "they/them", or whatever the family wrote.
    # Empty means not stated — see _layer1_persona, which then tells the
    # model to avoid gendered forms rather than infer them.
    pronouns: str = ""


@dataclass
class PriorContext:
    """
    Everything Layer 3 knows about the user. Every field here must be
    rendered by _layer3_life_context — see the render-coverage test in
    tests/test_system_prompt.py. A field that is populated but never
    rendered is invisible work, which is how `recent_stories` survived
    unnoticed until S1.5 removed it.
    """

    facts: dict = field(default_factory=dict)
    open_threads: list[str] = field(default_factory=list)
    significant_people: list[dict] = field(default_factory=list)


def _language_name(bcp47: str) -> str:
    return _LANGUAGE_NAMES.get(bcp47, bcp47)


def _pronoun_line(pronouns: str) -> str:
    """
    How to refer to the storyteller — in Layer 1, not Layer 3.

    Layer 3 is conditional: it renders the family's onboarding notes only
    while nothing else is known, so anything about the person that lived
    there survived exactly one turn. Pronouns cannot be conditional on how
    much Katha has learned, so they belong in the persona, which is
    rendered on every turn of every session.

    The empty case matters as much as the stated one, and is weaker than
    it reads. Measured on Hindi at session 7 with a transcript carrying no
    gender marking of its own: with "she/her" the reply was feminine 5/5
    and with "he/him" masculine 5/5, never the opposite — the stated path
    is solid. With nothing recorded it still gender-marked the listener,
    4 feminine and 1 masculine across 5 runs.

    That is a limit of the language more than of the instruction. Hindi
    agrees the verb with the person addressed — "aap gaye the" against
    "aap gayi thin" — so a great many ordinary sentences cannot decline to
    choose, and the model chooses differently run to run. The instruction
    below reduces how often it commits; it cannot make Hindi genderless.

    So an unrecorded value is a degraded fallback, not a safe default, and
    the real mitigation is upstream: the onboarding wizard asks for this
    explicitly rather than letting it be skipped by inattention. Existing
    profiles predate the field and are all empty.
    """
    if pronouns.strip():
        return (
            f"\nTheir pronouns are {pronouns.strip()} — use them, including "
            "the matching gendered verb and honorific forms in languages "
            "that mark gender."
        )
    return (
        "\nTheir pronouns are not recorded. Do not guess, and never infer "
        'them from their name. Address them as "you", use their name, and '
        "where a language forces a gendered verb or honorific, choose the "
        "neutral or plural-respectful form."
    )


def _layer1_persona(user_profile: UserProfile) -> str:
    lang = _language_name(user_profile.preferred_language)
    return f"""LAYER 1 — PERSONA & TONE
You are Katha, a warm and curious companion for {user_profile.name}. \
You are patient, unhurried, genuinely interested, and never judgmental. \
You speak in a mix of English and {lang} as feels natural. \
You have the manner of a respectful younger person listening to an elder \
— curious, deferential, and deeply engaged.\
{_pronoun_line(user_profile.pronouns)}"""


def _layer2_therapeutic() -> str:
    return """LAYER 2 — THERAPEUTIC PROTOCOL
Your goal is to guide the user through a structured reminiscence conversation. \
Each session focuses on one life domain. Follow these principles at all times:

1. Open before factual: Ask sensory and emotional questions before dates and facts. \
"What did your street smell like in the mornings?" before "What year did you move?" \
That is for opening a topic; once a story has begun, principle 8 picks the question.

2. Ordinary magic: Surface the richness in everyday memories. \
"You mentioned your mother's kitchen — what did she cook on Sundays?" \
is more valuable than asking about achievements.

3. Graceful repetition handling: If the user repeats a story you have heard before, \
never signal frustration. Acknowledge it warmly and redirect: \
"You've mentioned that before — today I'd love to hear what came after."

4. Mood adaptation: If the user's response is short or they say they are tired, \
shorten the session. Ask one light question. Do not push for a full session.

5. Cultural modesty reframe: When a user says "My life was ordinary, nothing special," \
respond warmly: "That's exactly the kind of life I want to learn about. \
The everyday things — the neighbourhood, the people, the small moments \
— those are the most precious stories to preserve."

6. Follow unforgettable people, wherever they appear: If the user describes someone \
— in any domain — with unusual warmth, repetition, or emotional weight, treat it as \
a signal, not a detail. Gently go deeper in the moment \
("What was it about him that stayed with you?"), and if the conversation moves on \
before it is fully explored, flag them in the extraction output so the next session \
can return to them. People who shaped a life don't respect domain boundaries.

7. Use what you already know, unprompted: Layer 3 below lists facts, open threads, \
and significant people from past sessions. You do not need to wait for the user to \
bring these up — when today's topic gives a natural opening, raise them yourself \
("You mentioned your sister Kamala before — was she there too?"). Demonstrating \
memory, not just having it, is what builds the user's trust that they are truly \
being listened to.

8. Progress through an incomplete story, don't repeat: if the user's last answer \
left out who, what, when, where, or why, ask for one of those missing pieces by \
name — "What was her name?", "Roughly what year was that?" — warmly, but \
specifically; "Tell me more about that" doesn't move the story on. Check your \
own last question in the conversation above before asking again. Never re-ask \
about the same missing piece in different words — pick a different one of the \
five each turn, until the story feels complete."""


# How much of the fact store Layer 3 shows (COST_TO_SERVE.md C2). The store
# only grows: entity extraction appends every new name, place and date after
# every session, so rendering all of it made the dialogue prompt grow with
# tenure — about 18,300 characters at session 100, paid on every turn.
#
# The earliest people are kept because entries are appended in the order they
# were first mentioned, and the first people anyone talks about are usually
# the closest family. The latest are kept because they are what she is
# talking about now. Dates keep only the latest; institutions only the first.
# (first, last) per category; 0 means nothing is kept from that end.
_FACT_CAPS: dict[str, tuple[int, int]] = {
    "people": (10, 5),
    "places": (5, 5),
    "dates": (0, 8),
    "institutions": (5, 0),
}
_OTHER_FACT_CAP = 5
_FACT_LABELS = {
    "people": "People",
    "places": "Places",
    "dates": "Dates",
    "institutions": "Institutions",
}


def _fact_value_text(value) -> str:
    """One fact as plain words: a person as "Kamala (sister)", else str()."""
    if isinstance(value, dict):
        name = str(value.get("name") or "").strip()
        relationship = str(value.get("relationship") or "").strip()
        if name and relationship:
            return f"{name} ({relationship})"
        return name or relationship
    return str(value).strip()


def _dedupe(values: list) -> list[str]:
    """Rendered text, without blanks or case-insensitive repeats, order kept."""
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = _fact_value_text(value)
        if text and text.lower() not in seen:
            seen.add(text.lower())
            out.append(text)
    return out


def _first_and_last(values: list[str], first: int, last: int) -> list[str]:
    if len(values) <= first + last:
        return values
    return values[:first] + (values[-last:] if last else [])


def render_facts(facts: dict) -> list[str]:
    """
    The fact store as compact lines, one per category, e.g.
    "People: Kamala (sister); Mr. Iyer (teacher)".

    Replaces the Python reprs Layer 3 used to show — a list of dicts with
    quotes and braces costs tokens and reads worse — and caps each category
    (see _FACT_CAPS) so the prompt stops growing with tenure. Always on: it
    is how facts are rendered, not a behaviour that needs a rollback flag.
    """
    lines: list[str] = []
    for key, value in facts.items():
        label = _FACT_LABELS.get(key, str(key).replace("_", " ").capitalize())
        if isinstance(value, list):
            values = _dedupe(value)
            first, last = _FACT_CAPS.get(key, (_OTHER_FACT_CAP, 0))
            values = _first_and_last(values, first, last)
            if values:
                lines.append(f"{label}: " + "; ".join(values))
        else:
            text = _fact_value_text(value)
            if text:
                lines.append(f"{label}: {text}")
    return lines


def _layer3_life_context(
    user_profile: UserProfile,
    session_state: SessionState,
    prior_context: PriorContext,
) -> str:
    domain = get_domain(session_state.domain)
    has_prior_context = (
        prior_context.facts
        or prior_context.significant_people
        or prior_context.open_threads
    )
    if not has_prior_context:
        context_block = (
            f"This is an early session. You don't yet know much about "
            f"{user_profile.name} beyond what their family shared below."
        )
    elif not prior_context.facts:
        context_block = (
            f"You don't yet have structured facts about {user_profile.name}, "
            f"but see below for people and threads from past sessions."
        )
    else:
        facts_formatted = "\n".join(
            f"  - {line}" for line in render_facts(prior_context.facts)
        )
        context_block = (
            f"What you already know about {user_profile.name}:\n{facts_formatted}"
        )

    # What the family wrote at onboarding, on every turn.
    #
    # This used to appear only in the branch above — i.e. only while
    # nothing else was known — so the moment the first fact or open thread
    # was extracted it vanished from the prompt for good. Everything the
    # family took the trouble to tell us about their parent survived
    # roughly one turn. It is durable background, not a placeholder for
    # the absence of facts, so it is rendered unconditionally.
    family_block = ""
    if user_profile.onboarding_context:
        family_block = (
            f"\nWhat {user_profile.name}'s family told us at the start:\n"
            f"  {user_profile.onboarding_context}"
        )

    threads = ""
    if prior_context.open_threads:
        # Bounded deliberately. Retrieval fetches 25 atoms so older domains
        # stay reachable at all (S2.5), but every thread those atoms carry
        # would put 40+ bullets in front of the model, at which point it
        # stops treating the list as a menu and starts ignoring it. The
        # incoming order is already breadth-first across domains — see
        # orchestrator._extract_open_threads — so truncating here keeps one
        # thread from each domain before it keeps a second from any.
        shown = prior_context.open_threads[:_MAX_RENDERED_THREADS]
        threads = "\nOpen story threads to revisit:\n" + "\n".join(
            f"  - {t}" for t in shown
        )

    significant_block = ""
    if prior_context.significant_people:
        # Cap to 2 to avoid prompt bloat, but order the same way
        # _pick_recall_anchor does — people known from an earlier session
        # first. Otherwise Layer 3 can name two people the anchor in Layer 4
        # does not, and the prompt argues with itself about who matters.
        people_to_show = sorted(
            prior_context.significant_people,
            key=lambda p: not is_from_earlier_session(p, session_state.session_number),
        )[:2]
        lines = "\n".join(
            f"  - {p.get('name', 'Unknown')} ({p.get('relationship', '')}) "
            f"— {p.get('why_significant', '')}. Not yet fully explored."
            for p in people_to_show
        )
        significant_block = (
            f"\nPeople who have mattered deeply to {user_profile.name}, "
            f"mentioned in past sessions and not yet fully explored:\n{lines}"
        )

    # The entry question is only a suggested opener for the very first
    # exchange of a domain — injecting it every turn kept pulling the
    # model back to it as if it were the mandatory topic, crowding out
    # Layer 4's instruction to work in already-known context instead
    # (see REMEDIATION_PLAN WS5.4 eval finding on TC-03/TC-11).
    entry_line = ""
    if session_state.exchange_count == 0:
        entry_line = f"\nDomain entry question: {domain.entry_prompt}"

    return f"""LAYER 3 — LIFE CONTEXT
{context_block}{family_block}{threads}{significant_block}

Today's focus domain: {domain.name}{entry_line}"""


# How many fact-store people join the anchor rotation. That list grows with
# every named person ever extracted; without a cap, rotation would sooner or
# later anchor on someone mentioned once in passing.
_MAX_FACT_PEOPLE_IN_ROTATION = 3

# Same shape as story_extractor's: extracted names routinely carry a
# qualifier, e.g. "Grandfather (name unknown)". Duplicated rather than
# shared so prompts/ does not import from extraction/.
_PARENTHETICAL_RE = re.compile(r"\s*\([^)]*\)")

# Words that make up a role label rather than a name, plus the modifiers that
# travel with them. "Father", "Paternal grandparents" and "Grandfather (name
# unknown)" are all descriptions of a position in a family, not people the
# model can ask after by name.
_ROLE_WORDS = {
    "a",
    "an",
    "the",
    "my",
    "his",
    "her",
    "their",
    "paternal",
    "maternal",
    "elder",
    "older",
    "younger",
    "late",
    "unnamed",
    "unknown",
    "name",
    "side",
    "father",
    "mother",
    "dad",
    "mum",
    "mom",
    "papa",
    "amma",
    "appa",
    "parent",
    "parents",
    "grandparent",
    "grandparents",
    "grandfather",
    "grandmother",
    "grandpa",
    "grandma",
    "granddad",
    "sister",
    "brother",
    "sibling",
    "siblings",
    "son",
    "daughter",
    "child",
    "children",
    "wife",
    "husband",
    "spouse",
    "uncle",
    "aunt",
    "cousin",
    "nephew",
    "niece",
    "in",
    "law",
    "teacher",
    "neighbour",
    "neighbor",
    "friend",
    "colleague",
    "boss",
}

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _is_named_person(name: str) -> bool:
    """
    Whether this is an actual name rather than a role label.

    The Layer 4 instruction asks the model to raise this person by name, so
    "ask about Father" restates the generic pointer the anchor is meant to
    replace, while "ask about Kamala (sister)" gives it a real target.
    """
    words = _WORD_RE.findall(_PARENTHETICAL_RE.sub("", name or "").lower())
    return any(word not in _ROLE_WORDS for word in words)


def _anchor_key(person: dict) -> str:
    """Identity for dedup — the same person can appear both as a curated
    significant person and as a fact-store entry."""
    return _PARENTHETICAL_RE.sub("", person.get("name", "") or "").strip().lower()


def _describe_person(person: dict) -> str:
    name = person.get("name", "")
    relationship = person.get("relationship", "")
    return f"{name}{f' ({relationship})' if relationship else ''}"


def is_from_earlier_session(person: dict, session_number: Optional[int]) -> bool:
    """
    Whether this significant person was first seen before the current
    session. Entries written before `first_seen_session` was recorded lack
    the key and count as earlier, which is correct — they are older than
    the change that introduced it.
    """
    if session_number is None:
        return True
    first_seen = person.get("first_seen_session")
    if first_seen is None:
        return True
    return first_seen < session_number


def _pick_recall_anchor(
    prior_context: PriorContext, session_number: Optional[int] = None
) -> Optional[str]:
    """
    Pick one concrete, nameable thing from prior_context to anchor the
    Layer 4 recall instruction to. A generic pointer back to "Layer 3
    above" scored 0/3 on live proactive-recall eval runs — the model
    treated it as a soft nudge satisfiable by any lexical match rather
    than the "loose connection" leap it asked for. Naming the actual
    person/fact/thread gives it something concrete to reach for.

    Order matters, and it used to be wrong: this took significant_people[0]
    unconditionally, so someone the extractor flagged during the current
    session's own first turn outranked a person known for weeks. Layer 4
    then told the model it knew them "from a past session", which was
    false, and the established person was never raised — a live run had
    Katha ignore a sister recorded in the fact store to ask about
    grandparents mentioned thirty seconds earlier.

    Someone met this session is still a usable anchor, but only after
    everything genuinely remembered has been considered.

    Two further rules, both about not starving anyone:

    Prefer a candidate who can actually be NAMED. "Father" and "Paternal
    grandparents" are role labels, and "pivot one sentence to ask about
    Father" is precisely the vague pointer this function exists to replace.
    A real name gives the model something to reach for.

    Then rotate. The old precedence never varied, so whoever came first
    held the anchor every session until they were resolved — which may be
    weeks, or never — and everyone behind them was unreachable. That is the
    same starvation the Layer 3 retrieval window had (S2.5): a fixed
    ordering into a single slot. Rotation is keyed on the session number so
    the anchor is stable within a conversation and moves between them.
    """
    fresh_people: list[dict] = []
    remembered: list[dict] = []
    for person in prior_context.significant_people or []:
        if not person.get("name"):
            continue
        if is_from_earlier_session(person, session_number):
            remembered.append(person)
        else:
            fresh_people.append(person)

    # Fact-store people are equally valid anchors — they are how a name
    # like a sister's reaches Layer 3 at all. Capped because this list
    # accumulates every named person ever extracted, and rotating over all
    # of them would eventually anchor on someone mentioned once in passing.
    fact_people = (prior_context.facts or {}).get("people")
    if isinstance(fact_people, list):
        seen = {_anchor_key(p) for p in remembered}
        for person in fact_people[:_MAX_FACT_PEOPLE_IN_ROTATION]:
            if not isinstance(person, dict) or not person.get("name"):
                continue
            if _anchor_key(person) not in seen:
                remembered.append(person)
                seen.add(_anchor_key(person))

    if remembered:
        named = [p for p in remembered if _is_named_person(p.get("name", ""))]
        pool = named or remembered
        return _describe_person(pool[(session_number or 0) % len(pool)])

    if prior_context.facts:
        for key, value in prior_context.facts.items():
            if key == "people" or not value:
                continue
            shown = value[0] if isinstance(value, list) else value
            return f"their {key.replace('_', ' ')} ({shown})"

    if prior_context.open_threads:
        return prior_context.open_threads[0]

    # Last resort: someone first flagged during this very session. Better
    # than no anchor at all, but it must lose to anything actually
    # remembered, or it recreates the bug this ordering exists to fix.
    if fresh_people:
        return _describe_person(fresh_people[0])

    return None


def _layer4_session_state(
    session_state: SessionState, prior_context: Optional[PriorContext] = None
) -> str:
    domain = get_domain(session_state.domain)
    closing_instruction = ""
    if session_state.goal_met:
        closing_instruction = (
            "\nThis domain's goal is already met as of your last reply — treat "
            "this turn as your closing exchange: wrap up warmly and let them "
            "know what you'd love to hear about tomorrow."
        )
    recall_instruction = ""
    anchor = (
        _pick_recall_anchor(prior_context, session_state.session_number)
        if prior_context
        else None
    )
    if anchor and session_state.exchange_count >= 2:
        recall_instruction = (
            f"\nYou know about {anchor} from a past session. If you haven't "
            f"already brought them/it up so far in this conversation, pivot "
            f"one sentence to ask about {anchor} now — even if the connection "
            f"to what the user just said is only loose (family, feelings, "
            f"people, and place all count as a bridge). Do this before the "
            f"conversation moves further away from the opening. Ask about "
            f"their place in the user's life and why it matters, not for "
            f"another fact about them."
        )
    return f"""LAYER 4 — SESSION STATE & CONSTRAINTS
Session number: {session_state.session_number}
Current domain: {domain.name}
Exchanges so far this session: {session_state.exchange_count}
Target story atoms for this domain: {domain.target_story_atoms}
Domain goal already met: {session_state.goal_met}\
{closing_instruction}{recall_instruction}

Hard constraints:
- Never bring up medical details or financial struggles unless the user initiates them.
- If the user mentions grief or loss: acknowledge warmly, do not probe further, \
gently offer to continue or to pause the session.
- Crisis protocol: if the user expresses acute distress or mentions harming \
themselves, immediately pause story collection, express care, and provide: \
"iCall India: 9152987821". Do not continue with story questions until \
the user indicates they are okay."""


def _layer5_output_format() -> str:
    return """LAYER 5 — OUTPUT FORMAT
Respond in exactly this format and no other:

<response>
[Your conversational response here — warm, natural, in the user's language. \
This is what will be spoken aloud.]
</response>

Keep it to two to four sentences — this is a voice note an elderly person \
listens to, not a letter they read. End on a single question, unless you are \
closing the session, which ends warmly instead.

Begin your output with <response>. No reasoning, planning or preamble before \
it, in <thinking> tags or otherwise.

Return only the <response> block above. Structured extraction is handled by a \
separate pass — do not include JSON or any other tags or commentary here."""


@dataclass
class SystemPromptParts:
    """
    The dialogue system prompt in two halves (COST_TO_SERVE.md C3).

    `stable` is Layers 1-3: the persona, the protocol, and what Katha knows
    about her. With Layer 3 frozen per session (C2) it is identical on every
    turn of a session, so it is what gets cached. `per_turn` is Layers 4-5,
    which change every turn and always come last.
    """

    stable: str
    per_turn: str

    def joined(self) -> str:
        return f"{self.stable}\n\n{self.per_turn}"


def build_system_prompt_parts(
    user_profile: UserProfile,
    session_state: SessionState,
    prior_context: PriorContext,
) -> SystemPromptParts:
    """The dialogue system prompt, split for caching; see build_system_prompt."""
    stable = "\n\n".join(
        [
            _layer1_persona(user_profile),
            _layer2_therapeutic(),
            _layer3_life_context(user_profile, session_state, prior_context),
        ]
    )
    per_turn = "\n\n".join(
        [
            _layer4_session_state(session_state, prior_context),
            _layer5_output_format(),
        ]
    )
    return SystemPromptParts(stable=stable, per_turn=per_turn)


def build_system_prompt(
    user_profile: UserProfile,
    session_state: SessionState,
    prior_context: PriorContext,
) -> str:
    """
    Assembles the dialogue system prompt — the fast, low-token-budget call
    on the critical path. Returns <response> only; see build_extraction_prompt
    for the separate, latency-tolerant structured-extraction call.

    One string, for callers that want it whole (the eval runner, tests).
    The dialogue call itself sends the two parts separately so the stable
    half can be cached.
    """
    return build_system_prompt_parts(
        user_profile, session_state, prior_context
    ).joined()


# The eight ids, rendered into the extraction prompt. The prompt used to
# name the field and give one example ("e.g. childhood") without ever
# listing the valid values, so the model invented plausible ones —
# "education_coming_of_age" for "education". Those went straight into
# story_atoms.domain, and an id the registry does not know breaks the
# domain filter, the coverage count and the next session's topic choice,
# all of which match on it. One such row also returned a 500 for the whole
# of GET /family/stories until domain_label() was made total.
_DOMAIN_ID_LIST = "\n".join(
    f"  - {d.id} ({d.name})" for d in (get_domain(i) for i in get_domain_sequence())
)


def build_extraction_prompt(
    user_profile: UserProfile,
    session_state: SessionState,
    prior_context: PriorContext,
    user_transcript: str,
    assistant_response: str,
) -> str:
    """
    Builds the standalone extraction-only prompt for the second, off-critical-
    path LLM call (see REMEDIATION_PLAN WS2.1). Given the exchange that just
    happened, extract the same structured fields the dialogue call used to
    produce inline — decoupled so a long, detailed story never gets truncated
    by a token budget sized for a warm two-sentence reply.
    """
    domain = get_domain(session_state.domain)

    known_people_block = ""
    if prior_context.significant_people:
        names = ", ".join(p.get("name", "") for p in prior_context.significant_people)
        known_people_block = (
            f"\nAlready-known significant people — do not re-flag unless there is "
            f"new emotional signal beyond what's already known: {names}"
        )

    goal_met_note = ""
    if session_state.goal_met:
        goal_met_note = (
            "\nThis domain's goal was already met before this exchange — Katha's "
            "reply above was treating this as the closing exchange, so "
            "session_end_suggested should be true unless the user's statement "
            "clearly asked to continue."
        )

    return f"""You are Katha's extraction engine. You are given one exchange from \
a reminiscence conversation with {user_profile.name}. Extract structured data \
from the USER's statement — do not extract from Katha's own reply, which is \
included only for context.

Today's focus domain: {domain.name} ({domain.id}).{known_people_block}{goal_met_note}

Every story atom's "domain" must be one of these exact ids, copied \
character for character. A story often belongs to a different chapter than \
today's focus — use whichever fits what they actually described:
{_DOMAIN_ID_LIST}

User said: {user_transcript}
Katha replied: {assistant_response}

Respond in exactly this format and no other:

<extraction>
{{
  "story_atoms": [
    {{
      "domain": "string — EXACTLY ONE of the ids listed above, nothing else",
      "title": "string — a short label for this story",
      "narrative": "string — a 1-3 sentence summary of what was said",
      "who": ["string", "..."],
      "what": "string or null",
      "when_approx": "string or null — e.g. circa 1960, the 1970s",
      "where_approx": "string or null",
      "why": "string or null — why this mattered to them",
      "verbatim_quote": "string or null — an exact quote worth preserving",
      "open_threads": ["string — a detail worth following up next session", "..."]
    }}
  ],
  "named_entities": {{}},
  "significant_people": [
    {{
      "name": "string",
      "relationship": "string",
      "why_significant": "string",
      "signal": "string — why flagged: repetition across turns or sessions, \
unusual emotional detail, explicit phrases like changed my life or I still \
think about. Mention alone does not qualify."
    }}
  ],
  "themes": [],
  "energy_signal": "high|medium|low",
  "gaps_remaining": [],
  "session_end_suggested": false
}}
</extraction>

For story_atoms: only include an entry if the user's statement actually \
contains a coherent piece of their story — do not fabricate one from a short \
or contentless reply. Each entry must be a JSON object with exactly the \
fields shown above, not a plain string. If nothing story-worthy was said, \
return an empty list.

For significant_people: this list is for the few people who shaped this life, \
not everyone who appears in it. Add an entry only when the user signals weight \
beyond the mention itself — they return to the person across turns or sessions, \
describe them with unusual emotional detail, or say something explicit like \
"changed my life" or "I still think about him". Being mentioned unprompted is \
NOT by itself a signal: in a conversation like this almost every person is \
volunteered unprompted. If the only reason you can give for why someone matters \
is that they came up, leave them out — they are already captured as a named \
entity. Most turns should add nobody at all.

Return only the <extraction> block above — no other text."""
