"""Language normalization and conversation-language retention (A05).

Provider language output is normalized to short BCP-47-compatible tags
(``en``, ``pl``). Confidence values are NEVER invented: the provider's own
probability is carried through, and a retained conversation-language hint
keeps the original (low/absent) detection confidence.
"""

from __future__ import annotations

# Detection probability at or above which we trust the provider's own tag.
CONFIDENT_DETECTION_THRESHOLD = 0.5

#: Very short utterances that are approval-shaped in EN/PL and carry almost no
#: acoustic language signal ("yes"/"tak" sound similar across languages).
AMBIGUOUS_SHORT_APPROVALS = frozenset(
    {
        "yes", "y", "yeah", "yep", "yup", "no", "n", "nope", "nah",
        "ok", "okay", "k", "sure", "alright", "fine",
        "tak", "ta", "nie", "dobrze", "jasne", "wporzadku", "okej",
    }
)

#: Only the ISO-639-2/T codes plausibly emitted by our providers.
_ISO3_TO_SHORT = {"eng": "en", "pol": "pl"}


def normalize_language_tag(raw: str | None) -> str | None:
    """Normalize a provider language label to a short BCP-47 tag.

    Accepts ``EN``, ``en-US``, ``eng``, ``pl``, ``Polish``-style values and
    returns ``en``/``pl`` where recognized; anything else is ``None`` (never
    guessed)."""
    if raw is None:
        return None
    token = str(raw).strip().lower().split("-")[0].split("_")[0]
    if not token:
        return None
    token = _ISO3_TO_SHORT.get(token, token)
    if len(token) == 2 and token.isalpha():
        return token
    return None


def is_ambiguous_short_approval(text: str | None) -> bool:
    """True for approval-shaped one/two-word utterances with negligible
    language signal (EN/PL demo vocabulary)."""
    if not text:
        return False
    cleaned = "".join(ch for ch in text.lower() if ch.isalnum()).strip()
    # Also accept the two-word space-joined form after punctuation stripping.
    compact = cleaned.replace(" ", "")
    return compact in AMBIGUOUS_SHORT_APPROVALS


def resolve_language(
    *,
    detected: str | None,
    confidence: float | None,
    conversation_hint: str | None,
    text: str | None,
) -> tuple[str | None, float | None]:
    """Decide the transcript language tag.

    Rules (in order):

    1. A confident detection stands - a hint NEVER overrides a confident,
       longer utterance.
    2. For very short ambiguous approval words where detection is unknown or
       low-confidence, the trusted prior conversation-language hint wins
       (the transcript TEXT itself is never altered here). The original
       confidence is carried unchanged - nothing is fabricated.
    3. Otherwise the detected tag stands (possibly ``None`` -> the service
       reports ``und``, BCP-47 undetermined).
    """
    det = normalize_language_tag(detected)
    hint = normalize_language_tag(conversation_hint)
    confident = det is not None and (
        confidence is None or confidence >= CONFIDENT_DETECTION_THRESHOLD
    )
    if confident:
        return det, confidence
    if hint is not None and is_ambiguous_short_approval(text):
        return hint, confidence
    return det, confidence
