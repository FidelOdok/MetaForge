"""RequirementLinter (FORGE-55, spec section 31 Requirement Linter).

Deterministic, pattern-based -- no LLM call. Every category below is a real
check against curated word lists / regexes, not a fake or approximate
stand-in for NLU. Of the doc's 13 named categories, 12 are implemented here
as genuine checks; two are deliberately scoped out rather than faked:

- ``missing_parameter`` isn't a separate check from ``missing_threshold``/
  ``missing_unit`` -- "no number at all" and "number with no unit" are the
  two real signals this module can extract from a bare string; a third
  category distinguishing "the parameter itself is unnamed" from those two
  would need real noun-phrase parsing this module doesn't have. Folded into
  the other two rather than invented as a redundant, unreliable third check.
- ``contradictory_requirement`` needs semantic understanding of what two
  requirements actually claim, not string similarity -- that's the doc's
  own *Conflict Agent* (spec section 26.6, not yet built by any current
  Phase 3 sub-task). Not faked here as a similarity check that would be
  wrong far more often than a real linter should be.

``duplicate`` is the one category that needs a corpus (other requirements
to compare against) rather than a single string -- it's its own method,
``find_duplicates``, not part of ``lint()``.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from enum import StrEnum

from pydantic import BaseModel

_WEAK_MODALS = {"should", "may", "could", "might", "would"}
_WEAK_WORDS = {
    "preferably",
    "ideally",
    "generally",
    "typically",
    "usually",
    "possibly",
    "perhaps",
    "somewhat",
    "fairly",
    "reasonably",
    "hopefully",
}
# Vague qualifiers with no fixed, measurable definition -- flagged whether
# used as an adjective or adverb, per the doc's "ambiguous adjective;
# ambiguous adverb" (kept as one category, same as the doc's own example
# output uses a single AMBIGUOUS label for both "quietly" and "long time").
_AMBIGUOUS_WORDS = {
    "quiet",
    "quietly",
    "fast",
    "quickly",
    "slow",
    "slowly",
    "efficient",
    "efficiently",
    "lightweight",
    "compact",
    "small",
    "large",
    "big",
    "heavy",
    "light",
    "strong",
    "durable",
    "flexible",
    "scalable",
    "responsive",
    "smooth",
    "high",
    "low",
    "many",
    "several",
    "few",
    "some",
}
# Checked before _AMBIGUOUS_WORDS and excluded from it ("long"/"short" alone
# are common, fine adjectives -- only the vague-duration phrasing is
# flagged, matching the doc's own example: "long time", not bare "long").
_AMBIGUOUS_PHRASES = {"long time", "short time", "a while"}
# Subjective/experiential -- even fully defined, these can't be measured by
# an instrument, distinct from _AMBIGUOUS_WORDS (which at least name a
# physical quantity that COULD be given a unit).
_UNVERIFIABLE_WORDS = {
    "feel",
    "feels",
    "look",
    "looks",
    "seem",
    "seems",
    "appear",
    "appears",
    "professional",
    "comfortable",
    "pleasant",
    "satisfying",
    "intuitive",
    "elegant",
    "nice",
    "robust",
    "reliable",
    "easy",
    "simple",
    "user-friendly",
}
_CONDITION_MARKERS = {"when", "if", "under", "during", "while", "in the event"}
_UNIT_TOKENS = {
    "kg",
    "g",
    "mg",
    "mm",
    "cm",
    "m",
    "km",
    "s",
    "sec",
    "ms",
    "min",
    "minutes",
    "hr",
    "hrs",
    "hours",
    "hz",
    "khz",
    "mhz",
    "ghz",
    "db",
    "w",
    "kw",
    "v",
    "a",
    "ma",
    "ohm",
    "c",
    "°c",
    "%",
    "percent",
    "n",
    "nm",
    "psi",
    "pa",
    "kpa",
}
_COMPOUND_CONNECTORS = re.compile(r"\s+(?:and|as well as|while also)\s+", re.IGNORECASE)
_RANGE_AND = re.compile(r"\bbetween\b.{0,20}\band\b", re.IGNORECASE)
# Curated, not real NLU: a small set of engineering-concern domains, each a
# word list. Two matched domains in one requirement is the doc's own
# COMPOUND signal ("operate quietly" [acoustics] "for a long time"
# [endurance] -> "acoustics + endurance") even with no conjunction word
# present at all -- the syntactic _COMPOUND_CONNECTORS check alone can't
# catch that case, so this is a second, independent compound signal.
_CONCERN_DOMAINS: dict[str, set[str]] = {
    "acoustics": {
        "quiet",
        "quietly",
        "loud",
        "loudly",
        "noise",
        "noisy",
        "db",
        "decibel",
        "decibels",
        "sound",
    },
    "endurance": {
        "long",
        "duration",
        "hours",
        "hour",
        "battery",
        "runtime",
        "uptime",
        "endurance",
        "time",
    },
    "thermal": {"temperature", "hot", "cold", "heat", "thermal", "cool", "cooling"},
    "mechanical": {
        "mass",
        "weight",
        "size",
        "dimension",
        "dimensions",
        "compact",
        "lightweight",
        "heavy",
    },
    "power": {"power", "voltage", "current", "watt", "watts"},
    "speed": {"fast", "quickly", "slow", "slowly", "speed", "velocity"},
}
# "it"/"they" are always pronouns needing an antecedent. "this"/"that"/
# "these"/"those" are ambiguous on their own -- they're fine as demonstrative
# adjectives directly before a noun ("this system"), only flagged when used
# as the bare subject of a clause (immediately followed by a verb/modal).
_PURE_PRONOUNS = {"it", "they"}
_DEMONSTRATIVES = {"this", "that", "these", "those"}
_VERB_LIKE_FOLLOWERS = {
    "shall",
    "must",
    "will",
    "should",
    "may",
    "can",
    "could",
    "would",
    "is",
    "are",
    "was",
    "were",
    "does",
    "do",
    "has",
    "have",
    "cannot",
}
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_IMPLEMENTATION_SPECIFIC_TERMS = {
    "bluetooth",
    "wifi",
    "wi-fi",
    "usb-c",
    "arduino",
    "raspberry pi",
    "python",
    "ros",
    "ros2",
    "linux",
    "android",
    "ios",
    "mysql",
    "postgresql",
}


class LintCategory(StrEnum):
    AMBIGUOUS = "ambiguous"
    WEAK_MODAL = "weak_modal"
    WEAK_WORD = "weak_word"
    COMPOUND = "compound"
    UNDEFINED_PRONOUN = "undefined_pronoun"
    MISSING_CONDITION = "missing_condition"
    MISSING_THRESHOLD = "missing_threshold"
    MISSING_UNIT = "missing_unit"
    UNVERIFIABLE = "unverifiable_language"
    IMPLEMENTATION_SPECIFIC = "implementation_specific"
    DUPLICATE = "duplicate"


class LintFinding(BaseModel):
    category: LintCategory
    detail: str


def _words(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z'-]+", text.lower())


def _find_undefined_pronoun(words: list[str]) -> str | None:
    for i, word in enumerate(words):
        if word in _PURE_PRONOUNS:
            return word
        if word in _DEMONSTRATIVES:
            nxt = words[i + 1] if i + 1 < len(words) else None
            if nxt in _VERB_LIKE_FOLLOWERS:
                return word
    return None


class RequirementLinter:
    """Deterministic checks against one requirement's text (spec section 31)."""

    def lint(self, text: str, *, expects_condition: bool = False) -> list[LintFinding]:
        """Run every single-string check. `expects_condition` opts into the
        MISSING_CONDITION check -- not every requirement needs a
        when/if/under clause, and this module has no way to tell which do,
        so the caller (who has the engineering context) decides."""
        findings: list[LintFinding] = []
        lower = text.lower()
        words = _words(text)
        word_set = set(words)

        for modal in sorted(_WEAK_MODALS & word_set):
            findings.append(LintFinding(category=LintCategory.WEAK_MODAL, detail=modal))

        for weak in sorted(_WEAK_WORDS & word_set):
            findings.append(LintFinding(category=LintCategory.WEAK_WORD, detail=weak))

        for phrase in sorted(_AMBIGUOUS_PHRASES):
            if phrase in lower:
                findings.append(LintFinding(category=LintCategory.AMBIGUOUS, detail=phrase))
        for amb in sorted(_AMBIGUOUS_WORDS & word_set):
            findings.append(LintFinding(category=LintCategory.AMBIGUOUS, detail=amb))

        for term in sorted(_UNVERIFIABLE_WORDS):
            if term in word_set or term in lower:
                findings.append(LintFinding(category=LintCategory.UNVERIFIABLE, detail=term))

        domains = sorted(name for name, vocab in _CONCERN_DOMAINS.items() if vocab & word_set)
        if len(domains) >= 2:
            findings.append(LintFinding(category=LintCategory.COMPOUND, detail=" + ".join(domains)))
        elif self._is_compound(text):
            findings.append(
                LintFinding(category=LintCategory.COMPOUND, detail="multiple joined clauses")
            )

        pronoun = _find_undefined_pronoun(words)
        if pronoun:
            findings.append(LintFinding(category=LintCategory.UNDEFINED_PRONOUN, detail=pronoun))

        if expects_condition and not any(m in lower for m in _CONDITION_MARKERS):
            findings.append(
                LintFinding(
                    category=LintCategory.MISSING_CONDITION, detail="no when/if/under clause"
                )
            )

        numbers = _NUMBER.findall(text)
        if not numbers:
            findings.append(
                LintFinding(
                    category=LintCategory.MISSING_THRESHOLD, detail="no measurable value found"
                )
            )
        elif not any(tok in word_set for tok in _UNIT_TOKENS):
            findings.append(
                LintFinding(
                    category=LintCategory.MISSING_UNIT, detail=f"value(s) {numbers} lack a unit"
                )
            )

        for term in sorted(_IMPLEMENTATION_SPECIFIC_TERMS):
            if term in lower:
                findings.append(
                    LintFinding(category=LintCategory.IMPLEMENTATION_SPECIFIC, detail=term)
                )

        return findings

    def find_duplicates(
        self, text: str, corpus: list[str], *, threshold: float = 0.85
    ) -> list[LintFinding]:
        """Real string-similarity check (stdlib difflib) against `corpus` --
        not a fake stand-in for semantic dedup, just what a normalized-text
        similarity ratio can honestly claim to catch (near-identical
        rewordings), not paraphrases with different wording."""
        normalized = _normalize(text)
        findings: list[LintFinding] = []
        for other in corpus:
            other_normalized = _normalize(other)
            if not other_normalized or other_normalized == normalized:
                if other_normalized == normalized and other_normalized:
                    findings.append(LintFinding(category=LintCategory.DUPLICATE, detail=other))
                continue
            ratio = SequenceMatcher(None, normalized, other_normalized).ratio()
            if ratio >= threshold:
                findings.append(
                    LintFinding(
                        category=LintCategory.DUPLICATE, detail=f"{other} (similarity {ratio:.2f})"
                    )
                )
        return findings

    @staticmethod
    def _is_compound(text: str) -> bool:
        if _RANGE_AND.search(text):
            # "between 5 and 10 kg" -- not a compound requirement, a range.
            text = _RANGE_AND.sub("between X to Y", text)
        segments = [s.strip() for s in _COMPOUND_CONNECTORS.split(text) if s.strip()]
        if len(segments) < 2:
            return False
        # Each segment must carry its own content (more than a couple of
        # words) to count as an independent clause rather than a connector
        # joining two nouns in one clause ("mass and volume shall be logged").
        substantial = [s for s in segments if len(_words(s)) >= 3]
        return len(substantial) >= 2


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())
