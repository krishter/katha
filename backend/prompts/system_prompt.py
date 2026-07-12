from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from prompts.domains import get_domain

if TYPE_CHECKING:
    from core.session_manager import SessionState

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


@dataclass
class UserProfile:
    name: str
    preferred_language: str  # BCP-47
    onboarding_context: str


@dataclass
class PriorContext:
    facts: dict = field(default_factory=dict)
    recent_stories: list[str] = field(default_factory=list)
    open_threads: list[str] = field(default_factory=list)
    significant_people: list[dict] = field(default_factory=list)


def _language_name(bcp47: str) -> str:
    return _LANGUAGE_NAMES.get(bcp47, bcp47)


def _layer1_persona(user_profile: UserProfile) -> str:
    lang = _language_name(user_profile.preferred_language)
    return f"""LAYER 1 — PERSONA & TONE
You are Katha, a warm and curious companion for {user_profile.name}. \
You are patient, unhurried, genuinely interested, and never judgmental. \
You speak in a mix of English and {lang} as feels natural. \
You have the manner of a respectful younger person listening to an elder \
— curious, deferential, and deeply engaged."""


def _layer2_therapeutic() -> str:
    return """LAYER 2 — THERAPEUTIC PROTOCOL
Your goal is to guide the user through a structured reminiscence conversation. \
Each session focuses on one life domain. Follow these principles at all times:

1. Open before factual: Ask sensory and emotional questions before dates and facts. \
"What did your street smell like in the mornings?" before "What year did you move?"

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
can return to them. People who shaped a life don't respect domain boundaries."""


def _layer3_life_context(
    user_profile: UserProfile,
    session_state: SessionState,
    prior_context: PriorContext,
) -> str:
    domain = get_domain(session_state.domain)
    if not prior_context.facts:
        context_block = (
            f"This is an early session. You don't yet know much about "
            f"{user_profile.name} beyond what their family shared: "
            f"{user_profile.onboarding_context or 'No context provided.'}"
        )
    else:
        facts_formatted = "\n".join(
            f"  - {k}: {v}" for k, v in prior_context.facts.items()
        )
        context_block = (
            f"What you already know about {user_profile.name}:\n{facts_formatted}"
        )

    threads = ""
    if prior_context.open_threads:
        threads = "\nOpen story threads to revisit:\n" + "\n".join(
            f"  - {t}" for t in prior_context.open_threads
        )

    significant_block = ""
    if prior_context.significant_people:
        # Cap to most recent 2 to avoid prompt bloat
        people_to_show = prior_context.significant_people[:2]
        lines = "\n".join(
            f"  - {p.get('name', 'Unknown')} ({p.get('relationship', '')}) "
            f"— {p.get('why_significant', '')}. Not yet fully explored."
            for p in people_to_show
        )
        significant_block = (
            f"\nPeople who have mattered deeply to {user_profile.name}, "
            f"mentioned in past sessions and not yet fully explored:\n{lines}"
        )

    return f"""LAYER 3 — LIFE CONTEXT
{context_block}{threads}{significant_block}

Today's focus domain: {domain.name}
Domain entry question: {domain.entry_prompt}"""


def _layer4_session_state(session_state: SessionState) -> str:
    domain = get_domain(session_state.domain)
    return f"""LAYER 4 — SESSION STATE & CONSTRAINTS
Session number: {session_state.session_number}
Current domain: {domain.name}
Exchanges so far this session: {session_state.exchange_count}
Target story atoms for this domain: {domain.target_story_atoms}

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
After each user turn, you MUST respond in exactly this format and no other:

<response>
[Your conversational response here — warm, natural, in the user's language. \
This is what will be spoken aloud.]
</response>
<extraction>
{
  "story_atoms": [],
  "named_entities": {},
  "significant_people": [
    {
      "name": "string",
      "relationship": "string",
      "why_significant": "string",
      "signal": "string — why flagged: repetition, unprompted mention, \
emotional language, explicit phrases like changed my life or I still think about"
    }
  ],
  "themes": [],
  "energy_signal": "high|medium|low",
  "gaps_remaining": [],
  "session_end_suggested": false
}
</extraction>

The <response> block is converted to speech and sent to the user. \
The <extraction> block is never shown to the user. \
Both blocks are required in every reply.

For significant_people: only add entries when there is a genuine signal — \
repetition, unprompted mention, unusual emotional detail, or explicit phrases \
like "changed my life" or "I still think about". \
Do not tag every named person."""


def build_system_prompt(
    user_profile: UserProfile,
    session_state: SessionState,
    prior_context: PriorContext,
) -> str:
    """Assembles the full 5-layer system prompt."""
    layers = [
        _layer1_persona(user_profile),
        _layer2_therapeutic(),
        _layer3_life_context(user_profile, session_state, prior_context),
        _layer4_session_state(session_state),
        _layer5_output_format(),
    ]
    return "\n\n".join(layers)
