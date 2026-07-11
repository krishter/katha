from dataclasses import dataclass
from typing import List


@dataclass
class Domain:
    id: str
    name: str
    entry_prompt: str
    follow_up_prompts: List[str]
    target_story_atoms: int
    sensory_prompts: List[str]


_DOMAINS: list[Domain] = [
    Domain(
        id="childhood",
        name="Childhood & Home",
        entry_prompt="Tell me about the house you grew up in — what did it look like?",
        follow_up_prompts=[
            "What was your neighbourhood like when you were young?",
            "Who were the people you remember most from your childhood?",
            "What games did you play as a child?",
            "What was a typical day like for you growing up?",
        ],
        target_story_atoms=3,
        sensory_prompts=[
            "What did your street smell like in the mornings?",
            "What sounds do you remember from inside your childhood home?",
            "What did your kitchen look like — what was always on the stove?",
        ],
    ),
    Domain(
        id="family_ancestors",
        name="Family & Ancestors",
        entry_prompt="Tell me about your grandparents. What do you remember of them?",
        follow_up_prompts=[
            "What stories did your parents tell you about their own childhoods?",
            "Who in your family had the biggest influence on you, and why?",
            "Were there any family traditions that everyone took part in?",
            "Tell me about a relative who was known for something special.",
        ],
        target_story_atoms=4,
        sensory_prompts=[
            "When you think of your mother, what image comes to mind first?",
            "What did gatherings at your grandparents' home feel like?",
            "Is there a smell or a food that instantly takes you back to your family?",
        ],
    ),
    Domain(
        id="education",
        name="Education & Coming of Age",
        entry_prompt="What was your school like? Who was a teacher you remember?",
        follow_up_prompts=[
            "What subject did you enjoy most, and why?",
            "Tell me about your closest friend from school.",
            "What did you want to be when you grew up?",
            "Were there any moments at school that changed how you saw yourself?",
        ],
        target_story_atoms=3,
        sensory_prompts=[
            "What did your school building look like — the corridors, the classrooms?",
            "What was the walk or journey to school like?",
            "What do you remember about the school lunch or tiffin?",
        ],
    ),
    Domain(
        id="career",
        name="Career & Work Life",
        entry_prompt="How did you end up in your career? What was your first job?",
        follow_up_prompts=[
            "Tell me about a colleague who made a real impression on you.",
            "What was the proudest moment in your working life?",
            "Was there a difficult period at work that you got through?",
            "How did your work shape who you are today?",
        ],
        target_story_atoms=4,
        sensory_prompts=[
            "What did your first workplace look like?",
            "What was the energy like on a typical morning at work?",
            "Is there a particular smell or sound you associate with your career?",
        ],
    ),
    Domain(
        id="love_marriage",
        name="Love, Marriage & Family Building",
        entry_prompt="Tell me about when you and your spouse first met.",
        follow_up_prompts=[
            "What was your wedding like — what do you remember most vividly?",
            "What was the early period of your marriage like?",
            "Tell me about the day your first child was born.",
            "How did becoming a parent change you?",
        ],
        target_story_atoms=3,
        sensory_prompts=[
            "When you think of the day you met your spouse, what do you see?",
            "What did your home feel like when your children were young?",
            "Is there a moment with your family that you return to often"
            " in your memory?",
        ],
    ),
    Domain(
        id="historical_events",
        name="Historical Events Witnessed",
        entry_prompt=(
            "You have lived through remarkable times in India's history. "
            "What is one event that you remember vividly — where were you"
            " when it happened?"
        ),
        follow_up_prompts=[
            "How did that event affect your daily life at the time?",
            "What did the people around you feel about what was happening?",
            "How do you think that moment shaped the country, looking back now?",
            "Were there quieter moments during that time that you still think about?",
        ],
        target_story_atoms=2,
        sensory_prompts=[
            "What do you remember seeing or hearing in the days around that event?",
            "What was the mood in your home or neighbourhood during that time?",
        ],
    ),
    Domain(
        id="hobbies",
        name="Hobbies, Passions & Talents",
        entry_prompt=(
            "Was there something you were known for? A skill or hobby"
            " that people associated with you?"
        ),
        follow_up_prompts=[
            "How did you first get interested in that?",
            "Tell me about a time when that hobby or talent really came"
            " through for you.",
            "Did you ever teach it to anyone — a child or grandchild perhaps?",
            "Is there something creative you did that you wish you'd pursued more?",
        ],
        target_story_atoms=2,
        sensory_prompts=[
            "What did it feel like to be doing something you truly loved?",
            "Where did you usually practice or pursue that hobby?",
        ],
    ),
    Domain(
        id="wisdom",
        name="Wisdom & Life Lessons",
        entry_prompt=(
            "What is something you know now that you wish you had known at 25?"
        ),
        follow_up_prompts=[
            "Is there a piece of advice someone gave you that turned out"
            " to be completely true?",
            "What would you tell your grandchildren about how to live a good life?",
            "Looking back, what are you most grateful for?",
            "Is there something you would do differently if you had the chance?",
        ],
        target_story_atoms=2,
        sensory_prompts=[
            "When you think about a moment you learned something important,"
            " what do you picture?",
            "Is there a person whose wisdom shaped you most?",
        ],
    ),
]

_DOMAIN_MAP: dict[str, Domain] = {d.id: d for d in _DOMAINS}
_DOMAIN_SEQUENCE: list[str] = [d.id for d in _DOMAINS]


def get_domain_sequence() -> list[str]:
    """Returns the default order of domain IDs."""
    return list(_DOMAIN_SEQUENCE)


def get_domain(domain_id: str) -> Domain:
    """Lookup domain by ID. Raises ValueError if not found."""
    try:
        return _DOMAIN_MAP[domain_id]
    except KeyError:
        raise ValueError(f"Unknown domain ID: {domain_id!r}") from None


def get_next_domain(covered_domains: list[str]) -> Domain:
    """Returns the next uncovered domain in sequence.
    Falls back to first domain if all are covered (for repeat sessions)."""
    for domain_id in _DOMAIN_SEQUENCE:
        if domain_id not in covered_domains:
            return _DOMAIN_MAP[domain_id]
    return _DOMAIN_MAP[_DOMAIN_SEQUENCE[0]]
