"""Change classification: what a request would change, before anything changes.

Before a chat turn in a project thread reaches the agent, the request is
classified (spec section 5), so "make it red" never rebuilds CAD:

=================================== ================================================
class                               handling
=================================== ================================================
``appearance``                      update the appearance record only; no CAD
                                    rebuild, no manufacturing context write
``ambiguous_appearance_or_material`` ask the person: the look or the actual material?
``geometric``                       the design change, parametric and exact paths;
                                    dimensions are proposed, never invented
``engineering_geometry``            a new editable feature, then the relevant checks
``material_engineering``            propose ``selected_material`` on the
                                    manufacturing context; the person confirms
``performance_objective``           propose alternatives and how to evaluate them
``ambiguous_style``                 explore alternatives before any authoritative
                                    change
``not_a_change``                    a question: nothing changes
=================================== ================================================

Two stages. :func:`classify_rules` is deterministic: keywords, the request
text and the selected component. When it is not sure (confidence below
:data:`UNCERTAIN_BELOW`) and the LLM pass is switched on
(``METAFORGE_CHANGE_CLASSIFIER_LLM=on``), :func:`classify_change` asks the
configured model once, with a closed answer schema. Any failure of that call
keeps the rule result.

**The guard.** The turn's classification is held in a context variable for
the length of the turn (:func:`turn_classification`). Tool calls and the
service functions that rebuild CAD or write the manufacturing context call
:func:`check_change_guard`, which refuses them when the turn's class forbids
it: an appearance (or not yet clarified look-or-material) request never
rebuilds CAD or writes the manufacturing context, and a performance or style
request never rebuilds CAD before an alternative was chosen.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum
from typing import Any

import structlog
from pydantic import BaseModel, Field

from observability.tracing import get_tracer

logger = structlog.get_logger(__name__)
tracer = get_tracer("api_gateway.twin.change_classification")

__all__ = [
    "AppearanceValues",
    "ChangeClass",
    "ChangeClassification",
    "ChangeGuardRefusedError",
    "LLM_ENV",
    "UNCERTAIN_BELOW",
    "agent_instruction",
    "check_change_guard",
    "classify_change",
    "classify_rules",
    "current_turn_classification",
    "guard_tool_call",
    "llm_enabled",
    "operation_message",
    "strip_client_suffixes",
    "turn_classification",
]


class ChangeClass(StrEnum):
    """What a request would change (spec section 5)."""

    APPEARANCE = "appearance"
    GEOMETRIC = "geometric"
    ENGINEERING_GEOMETRY = "engineering_geometry"
    MATERIAL_ENGINEERING = "material_engineering"
    PERFORMANCE_OBJECTIVE = "performance_objective"
    AMBIGUOUS_STYLE = "ambiguous_style"
    AMBIGUOUS_APPEARANCE_OR_MATERIAL = "ambiguous_appearance_or_material"
    NOT_A_CHANGE = "not_a_change"


#: Below this the rule pass is "not sure", and the LLM pass (when on) is asked.
UNCERTAIN_BELOW = 0.65
LLM_ENV = "METAFORGE_CHANGE_CLASSIFIER_LLM"

#: How each class is handled, as a short routing code.
HANDLING: dict[ChangeClass, str] = {
    ChangeClass.APPEARANCE: "update_appearance",
    ChangeClass.AMBIGUOUS_APPEARANCE_OR_MATERIAL: "ask_look_or_material",
    ChangeClass.GEOMETRIC: "route_design_change",
    ChangeClass.ENGINEERING_GEOMETRY: "route_feature_change",
    ChangeClass.MATERIAL_ENGINEERING: "propose_material_update",
    ChangeClass.PERFORMANCE_OBJECTIVE: "propose_alternatives",
    ChangeClass.AMBIGUOUS_STYLE: "explore_alternatives",
    ChangeClass.NOT_A_CHANGE: "answer",
}

#: The plain-language line Create shows, per class.
PLAIN_MESSAGE: dict[ChangeClass, str] = {
    ChangeClass.APPEARANCE: "Appearance change only. Your engineering model stays the same.",
    ChangeClass.AMBIGUOUS_APPEARANCE_OR_MATERIAL: (
        "Do you mean the look, or the actual material it is made from?"
    ),
    ChangeClass.GEOMETRIC: (
        "Shape change. The new dimensions are proposed for you to review before the CAD is rebuilt."
    ),
    ChangeClass.ENGINEERING_GEOMETRY: (
        "New feature. It is added as an editable feature, then the relevant checks run."
    ),
    ChangeClass.MATERIAL_ENGINEERING: (
        "Material change. This changes the engineering model: confirm the new material, "
        "and the checks that depend on it are marked for a re-run."
    ),
    ChangeClass.PERFORMANCE_OBJECTIVE: (
        "Performance goal. Alternatives and how to evaluate them come first; your design "
        "stays as it is until you choose one."
    ),
    ChangeClass.AMBIGUOUS_STYLE: (
        "Style direction. Options are explored first; your design stays as it is until "
        "you choose one."
    ),
    ChangeClass.NOT_A_CHANGE: "A question. Nothing in the design changes.",
}

#: A request no rule recognised: not claimed to be a question.
UNRECOGNISED_MESSAGE = "Not recognised as a design change; handled as a normal request."

#: What the agent is told to do, per class.
_AGENT_RULE: dict[ChangeClass, str] = {
    ChangeClass.APPEARANCE: (
        "This is a visual change only. Do not rebuild CAD, do not change dimensions or "
        "features, do not write the manufacturing context and do not mark anything "
        "stale. The appearance record holds colour, finish, render material and "
        "textures; the viewer applies it to every mesh of the component."
    ),
    ChangeClass.AMBIGUOUS_APPEARANCE_OR_MATERIAL: (
        "It is not clear whether the person means the look or the physical material. "
        "Ask exactly that ('the look, or the actual material?') and change nothing "
        "until they answer."
    ),
    ChangeClass.GEOMETRIC: (
        "A dimensional or placement change. Use the design-change, parametric and "
        "exact CAD paths (twin.propose_change, then a reviewed build). Do not invent "
        "dimensions the person did not give: propose them as assumptions to confirm."
    ),
    ChangeClass.ENGINEERING_GEOMETRY: (
        "A new or changed engineering feature. Add it as an editable feature through "
        "the design-change and parametric paths, then run the relevant checks. Do not "
        "invent dimensions the person did not give."
    ),
    ChangeClass.MATERIAL_ENGINEERING: (
        "A physical material change. Propose the new selected_material for the "
        "manufacturing context for the person to confirm in Create; once confirmed, "
        "the dependent checks are marked stale by the impact walk. Do not rebuild "
        "geometry for it."
    ),
    ChangeClass.PERFORMANCE_OBJECTIVE: (
        "A performance goal, not a specific change. Propose two or three geometry "
        "alternatives and the evaluation each needs. Do not edit authoritative "
        "geometry or rebuild CAD in this turn."
    ),
    ChangeClass.AMBIGUOUS_STYLE: (
        "A style direction. Explore alternatives (concept variants) for the person to "
        "choose from. Do not edit authoritative geometry or rebuild CAD in this turn."
    ),
    ChangeClass.NOT_A_CHANGE: "A question. Answer it; change nothing.",
}


class AppearanceValues(BaseModel):
    """The look a request names, when it names one."""

    colour: str | None = Field(None, description="sRGB hex, e.g. #c62828")
    colour_name: str | None = None
    finish: str | None = None
    render_material: str | None = None
    texture_refs: list[str] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.colour or self.finish or self.render_material or self.texture_refs)


class ChangeClassification(BaseModel):
    """One request's class, how sure, what it is about and how it is handled."""

    change_class: ChangeClass
    confidence: float = Field(ge=0.0, le=1.0)
    #: ``rules``, ``llm``, or ``person`` (the person chose the look or the material).
    stage: str = "rules"
    #: Other classes the request also touched (a mixed request).
    also: list[ChangeClass] = Field(default_factory=list)
    target_component: str | None = None
    target_component_id: str | None = None
    target_feature: str | None = None
    handling: str
    message: str
    #: The words that decided it.
    signals: list[str] = Field(default_factory=list)
    appearance: AppearanceValues | None = None
    #: For ``material_engineering``: the manufacturing context fields proposed.
    proposed_context_update: dict[str, Any] | None = None
    #: For the ambiguous cases: the choices offered to the person.
    choices: list[str] = Field(default_factory=list)
    #: The dimensions the request gave, verbatim ("120 mm"). Never invented.
    stated_dimensions: list[str] = Field(default_factory=list)

    @property
    def uncertain(self) -> bool:
        return self.confidence < UNCERTAIN_BELOW


# ---------------------------------------------------------------------------
# Lexicon
# ---------------------------------------------------------------------------

COLOURS: dict[str, str] = {
    "red": "#c62828",
    "dark red": "#8e0000",
    "blue": "#1565c0",
    "light blue": "#64b5f6",
    "navy": "#1a237e",
    "green": "#2e7d32",
    "dark green": "#1b5e20",
    "lime": "#9ccc65",
    "olive": "#827717",
    "yellow": "#f9a825",
    "orange": "#ef6c00",
    "purple": "#6a1b9a",
    "violet": "#7e57c2",
    "pink": "#ec407a",
    "magenta": "#c2185b",
    "black": "#151515",
    "white": "#f5f5f5",
    "grey": "#8a8a8a",
    "gray": "#8a8a8a",
    "dark grey": "#424242",
    "dark gray": "#424242",
    "light grey": "#cfcfcf",
    "light gray": "#cfcfcf",
    "charcoal": "#36454f",
    "brown": "#6d4c41",
    "beige": "#d7c4a3",
    "cream": "#f3ead3",
    "ivory": "#fffff0",
    "tan": "#c8a97e",
    "teal": "#00897b",
    "turquoise": "#26c6da",
    "cyan": "#00acc1",
    "maroon": "#6d1b1b",
    "mint": "#a5d6a7",
    "sand": "#d8c8a8",
}

#: Words that make a request about appearance (with or without a colour).
_APPEARANCE_WORDS = (
    "colour",
    "color",
    "coloured",
    "colored",
    "paint",
    "painted",
    "tint",
    "shade",
    "hue",
    "pastel",
    "render",
    "rendering",
    "texture",
    "decal",
    "logo colour",
)

FINISHES: dict[str, str] = {
    "matte": "matte",
    "matt": "matte",
    "glossy": "gloss",
    "gloss": "gloss",
    "satin": "satin",
    "shiny": "gloss",
    "frosted": "frosted",
    "soft-touch": "soft_touch",
    "soft touch": "soft_touch",
    "pearlescent": "pearlescent",
    "speckled": "speckled",
}

#: Words that could be the look or the physical material (section 5: "metallic").
_LOOK_OR_MATERIAL = (
    "metallic",
    "metal",
    "chrome",
    "chromed",
    "gold",
    "golden",
    "silver",
    "bronze",
    "copper",
    "brass",
    "wooden",
    "wood look",
    "wood-look",
    "woodgrain",
    "wood grain",
    "marble",
    "carbon look",
    "carbon-look",
    "brushed",
    "anodised",
    "anodized",
    "transparent",
    "translucent",
    "see-through",
    "glass-like",
)

#: Metals that are also colours: alone they are ambiguous, with a material verb a material.
_COLOUR_METALS = ("gold", "silver", "bronze", "copper", "brass", "chrome")

#: Material names, normalised to the name the manufacturing context records.
MATERIALS: dict[str, str] = {
    "aluminium": "Aluminium",
    "aluminum": "Aluminium",
    "stainless steel": "Stainless steel",
    "stainless": "Stainless steel",
    "steel": "Steel",
    "titanium": "Titanium",
    "magnesium": "Magnesium",
    "zinc": "Zinc",
    "brass": "Brass",
    "copper": "Copper",
    "bronze": "Bronze",
    "plastic": "Plastic",
    "abs": "ABS",
    "pla": "PLA",
    "petg": "PETG",
    "asa": "ASA",
    "nylon": "Nylon",
    "polycarbonate": "Polycarbonate",
    "acrylic": "Acrylic",
    "pmma": "PMMA",
    "polypropylene": "Polypropylene",
    "tpu": "TPU",
    "silicone": "Silicone",
    "rubber": "Rubber",
    "resin": "Resin",
    "wood": "Wood",
    "plywood": "Plywood",
    "birch": "Birch plywood",
    "oak": "Oak",
    "walnut": "Walnut",
    "bamboo": "Bamboo",
    "mdf": "MDF",
    "carbon fibre": "Carbon fibre",
    "carbon fiber": "Carbon fibre",
    "glass": "Glass",
    "ceramic": "Ceramic",
    "concrete": "Concrete",
    "leather": "Leather",
    "fabric": "Fabric",
    "cork": "Cork",
}

_MATERIAL_VERBS = (
    r"\buse\b",
    r"\busing\b",
    r"\binstead of\b",
    r"\bswitch(?:ed)?\b.*\bto\b",
    r"\bfrom\b.*\bto\b",
    r"\bchange (?:the )?material\b",
    r"\bmaterial to\b",
    r"\bmade (?:of|from|out of)\b",
    r"\bmake it (?:out )?of\b",
    r"\bmanufacture(?:d)? (?:it )?(?:in|from)\b",
    r"\bin solid\b",
    r"\bmachined from\b",
    r"\bprint(?:ed)? (?:it )?in\b",
    r"\bmould(?:ed)? in\b",
    r"\bmold(?:ed)? in\b",
    r"\bcast in\b",
)

#: Say it is about the look ("wood look", "chrome effect"): appearance, not material.
_LOOK_QUALIFIERS = (
    r"\blook(?:s|ing)?\b",
    r"-look\b",
    r"\beffect\b",
    r"\bstyle finish\b",
    r"\bfinish\b",
    r"\bwrap\b",
    r"\bvinyl\b",
    r"\bcoloured\b",
    r"\bcolored\b",
    r"\bpaint(?:ed)?\b",
    r"\blike\b",
)

_SIZE_WORDS = (
    "wider",
    "narrower",
    "taller",
    "shorter",
    "longer",
    "thicker",
    "thinner",
    "bigger",
    "smaller",
    "larger",
    "deeper",
    "shallower",
    "higher",
    "slimmer",
    "widen",
    "narrow",
    "lengthen",
    "shorten",
    "shrink",
    "enlarge",
    "resize",
    "rescale",
    "scale",
    "extend",
    "stretch",
    "flatten",
    "compact",
    "wide",
    "tall",
    "thick",
    "width",
    "height",
    "length",
    "depth",
    "diameter",
    "thickness",
)
_MOVE_WORDS = (
    "move",
    "moved",
    "shift",
    "relocate",
    "reposition",
    "raise",
    "rotate",
    "flip",
    "mirror",
    "align",
    "offset",
    "swap sides",
)
_SHAPE_WORDS = (
    "round",
    "rounded",
    "rounder",
    "fillet",
    "chamfer",
    "bevel",
    "radius",
    "curve",
    "curved",
    "taper",
    "tapered",
    "sharpen",
    "square off",
    "squarer",
    "angled",
)
#: Feature nouns: what a person adds or moves on a part.
_FEATURES = (
    "ventilation holes",
    "ventilation",
    "vent",
    "vents",
    "hole",
    "holes",
    "rib",
    "ribs",
    "boss",
    "bosses",
    "slot",
    "slots",
    "cutout",
    "cut-out",
    "opening",
    "openings",
    "standoff",
    "standoffs",
    "gusset",
    "gussets",
    "snap fit",
    "snap-fit",
    "clip",
    "clips",
    "mount",
    "mounting",
    "pocket",
    "groove",
    "thread",
    "threads",
    "hinge",
    "port",
    "ports",
    "grille",
    "grill",
    "handle",
    "lip",
    "flange",
    "fillet",
    "chamfer",
    "screw",
    "screws",
    "fastener",
    "fasteners",
    "cable channel",
    "channel",
    "louvre",
    "louver",
    "window",
    "button",
    "edges",
    "edge",
    "corner",
    "corners",
    "wall",
    "walls",
)
_ADD_WORDS = (
    "add",
    "adding",
    "put",
    "include",
    "insert",
    "create",
    "drill",
    "cut",
    "punch",
    "need",
    "needs",
    "give it",
    "with some",
    "remove",
    "delete",
)
_PERFORMANCE_WORDS = (
    "aerodynamic",
    "aerodynamics",
    "drag",
    "airflow",
    "stronger",
    "strength",
    "stiffer",
    "stiffness",
    "more rigid",
    "lighter weight",
    "lightweight",
    "weigh less",
    "less weight",
    "reduce weight",
    "cooler",
    "run cooler",
    "heat",
    "thermal",
    "overheat",
    "quieter",
    "noise",
    "vibration",
    "efficient",
    "efficiency",
    "durable",
    "durability",
    "waterproof",
    "water resistant",
    "water-resistant",
    "dustproof",
    "rugged",
    "robust",
    "sturdier",
    "sturdy",
    "more stable",
    "stability",
    "tip over",
    "faster",
    "brighter",
    "louder",
    "longer battery",
    "battery life",
    "cheaper",
    "lower cost",
    "easier to print",
    "easier to make",
    "easier to assemble",
    "grip",
    "ergonomic",
    "comfortable",
    "safer",
)
_STYLE_WORDS = (
    "futuristic",
    "modern",
    "sleek",
    "retro",
    "vintage",
    "classic",
    "minimal",
    "minimalist",
    "elegant",
    "friendly",
    "playful",
    "organic",
    "premium",
    "luxurious",
    "luxury",
    "cute",
    "aggressive",
    "sporty",
    "bold",
    "stylish",
    "cooler looking",
    "cool looking",
    "cool-looking",
    "prettier",
    "beautiful",
    "nicer",
    "softer look",
    "industrial look",
    "scandinavian",
    "steampunk",
    "cyberpunk",
    "art deco",
    "bauhaus",
    "high-end",
    "high end",
    "less boxy",
    "more fun",
)

_QUESTION_START = re.compile(
    r"^\s*(what|why|how|which|when|where|who|whose|is|are|does|do|did|has|have|"
    r"was|were|should|shall|explain|tell me|show me)\b",
    re.IGNORECASE,
)
_POLITE_REQUEST = re.compile(
    r"\b(can|could|would|will) you\b|\bplease\b|\bi want\b|\bi'd like\b|\bi would like\b|"
    r"\blet's\b|\blets\b|\bcan we\b|\bcould we\b|\bmake\b",
    re.IGNORECASE,
)
_DIMENSION = re.compile(
    r"\b\d+(?:[.,]\d+)?\s?(?:mm|cm|m|in|inch|inches|\"|deg|degrees|°|%)(?![a-z])",
    re.IGNORECASE,
)
_HEX = re.compile(r"#[0-9a-fA-F]{6}\b")

#: Markers the Create view appends to an instruction (create-store.ts), and
#: the person's own look-or-material choice.
_CLIENT_SUFFIX = re.compile(
    r"\n\nSelected twin object \(context snapshot\):|\n\nAttached twin work products|"
    r"\n\nCurrent sketch revision:|\nDiscuss using the supplied context\.|"
    r"\nPropose a reviewable change using|\nUse only the explicitly selected tools|"
    r"\n\nChange kind \(chosen by the person\):"
)
CHOSEN_MARKER = "Change kind (chosen by the person):"
_CHOSEN = re.compile(r"Change kind \(chosen by the person\):\s*(appearance|material)", re.I)
_ORIGINAL_REQUEST = re.compile(r"\nRequest:\s*(.+)", re.S)


def clarification_answer(label: str, kind: str, request: str) -> str:
    """The reply a "the look or the material?" choice sends, carrying the request."""
    return f"{label}.\n\n{CHOSEN_MARKER} {kind}\nRequest: {strip_client_suffixes(request)[:380]}"


def strip_client_suffixes(text: str) -> str:
    """The person's words, without the context the Create view appends."""
    cut = _CLIENT_SUFFIX.search(text or "")
    return (text[: cut.start()] if cut else (text or "")).strip()


def _has(text: str, word: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", text) is not None


def _found(text: str, words: tuple[str, ...] | list[str]) -> list[str]:
    return [w for w in words if _has(text, w)]


def _any_re(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(p, text) for p in patterns)


def _colour_in(text: str) -> tuple[str | None, str | None]:
    """``(hex, name)`` of the colour named, longest name first."""
    hex_match = _HEX.search(text)
    if hex_match:
        return hex_match.group(0).lower(), None
    for name in sorted(COLOURS, key=len, reverse=True):
        if _has(text, name):
            return COLOURS[name], name
    return None, None


def _finish_in(text: str) -> str | None:
    for word in sorted(FINISHES, key=len, reverse=True):
        if _has(text, word):
            return FINISHES[word]
    return None


def _materials_in(text: str) -> list[str]:
    """Material words, longest first, without a shorter word inside a longer one."""
    found: list[str] = []
    for word in sorted(MATERIALS, key=len, reverse=True):
        if _has(text, word) and not any(word in longer for longer in found):
            found.append(word)
    return found


def _feature_in(text: str) -> str | None:
    """The feature phrase the request is about ("front edges", "speaker opening")."""
    for noun in sorted(_FEATURES, key=len, reverse=True):
        match = re.search(rf"(?:\b(\w+)\s+)?\b{re.escape(noun)}\b", text)
        if match:
            qualifier = match.group(1)
            if qualifier and qualifier not in {
                "the",
                "a",
                "an",
                "some",
                "more",
                "it",
                "its",
                "add",
                "move",
                "of",
                "to",
                "and",
                "with",
                "few",
            }:
                return f"{qualifier} {noun}"
            return noun
    return None


# ---------------------------------------------------------------------------
# Stage 1: rules
# ---------------------------------------------------------------------------


def _result(
    change_class: ChangeClass,
    confidence: float,
    *,
    signals: list[str],
    stage: str = "rules",
    also: list[ChangeClass] | None = None,
    component: str | None = None,
    component_id: str | None = None,
    feature: str | None = None,
    appearance: AppearanceValues | None = None,
    context_update: dict[str, Any] | None = None,
    dimensions: list[str] | None = None,
) -> ChangeClassification:
    message = PLAIN_MESSAGE[change_class]
    if change_class is ChangeClass.APPEARANCE and appearance is not None:
        if appearance.colour and not appearance.finish:
            message = "Colour change only. Your engineering model stays the same."
        elif appearance.finish and not appearance.colour:
            message = "Finish change only. Your engineering model stays the same."
    if change_class is ChangeClass.MATERIAL_ENGINEERING and context_update:
        material = context_update.get("selected_material")
        if material:
            message = (
                f"Material change to {material}. This changes the engineering model: "
                "confirm it, and the checks that depend on the material are marked for "
                "a re-run."
            )
    choices = (
        ["The look", "The material"]
        if change_class is ChangeClass.AMBIGUOUS_APPEARANCE_OR_MATERIAL
        else []
    )
    return ChangeClassification(
        change_class=change_class,
        confidence=round(min(max(confidence, 0.0), 1.0), 2),
        stage=stage,
        also=[c for c in (also or []) if c is not change_class],
        target_component=component,
        target_component_id=component_id,
        target_feature=feature,
        handling=HANDLING[change_class],
        message=message,
        signals=signals[:12],
        appearance=appearance,
        proposed_context_update=context_update,
        choices=choices,
        stated_dimensions=dimensions or [],
    )


def classify_rules(
    text: str,
    *,
    component: str | None = None,
    component_id: str | None = None,
) -> ChangeClassification:
    """The deterministic pass: keywords, the request text and the selected component."""
    chosen = _CHOSEN.search(text or "")
    # The answer to "the look or the material?" carries the original request.
    asked = _ORIGINAL_REQUEST.search(text or "") if chosen else None
    raw = strip_client_suffixes(asked.group(1) if asked else text)
    low = " ".join(raw.lower().split())
    feature = _feature_in(low)
    dimensions = [m.group(0).strip() for m in _DIMENSION.finditer(raw)][:6]
    common: dict[str, Any] = {
        "component": component,
        "component_id": component_id,
        "dimensions": dimensions,
    }

    colour_hex, colour_name = _colour_in(low)
    finish = _finish_in(low)
    look_words = _found(low, _APPEARANCE_WORDS)
    look_or_material = _found(low, _LOOK_OR_MATERIAL)
    materials = _materials_in(low)
    material_verb = _any_re(low, _MATERIAL_VERBS)
    look_qualified = _any_re(low, _LOOK_QUALIFIERS)

    def appearance_values() -> AppearanceValues:
        render = None
        for word in look_or_material:
            render = {
                "chrome": "chrome",
                "chromed": "chrome",
                "gold": "gold",
                "golden": "gold",
                "silver": "brushed_metal",
                "metallic": "metallic_paint",
                "metal": "brushed_metal",
                "brushed": "brushed_metal",
                "bronze": "bronze",
                "copper": "copper",
                "brass": "brass",
                "wooden": "wood",
                "wood look": "wood",
                "wood-look": "wood",
                "woodgrain": "wood",
                "wood grain": "wood",
                "marble": "marble",
                "carbon look": "carbon_fibre",
                "carbon-look": "carbon_fibre",
                "transparent": "clear",
                "translucent": "translucent",
                "see-through": "clear",
                "glass-like": "clear",
                "anodised": "anodised_metal",
                "anodized": "anodised_metal",
            }.get(word, render)
            if render:
                break
        if render is None and materials and look_qualified:
            render = materials[0].replace(" ", "_")
        return AppearanceValues(
            colour=colour_hex, colour_name=colour_name, finish=finish, render_material=render
        )

    # The person already said which they meant.
    if chosen:
        kind = chosen.group(1).lower()
        if kind == "appearance":
            return _result(
                ChangeClass.APPEARANCE,
                1.0,
                signals=["person chose: the look"],
                stage="person",
                appearance=appearance_values(),
                **common,
            )
        material = _material_name(low, materials, look_or_material)
        return _result(
            ChangeClass.MATERIAL_ENGINEERING,
            1.0,
            signals=["person chose: the material"],
            stage="person",
            context_update={"selected_material": material} if material else None,
            **common,
        )

    question = bool(_QUESTION_START.match(low)) and not re.match(
        r"^\s*(can|could|would|will) you\b", low
    )
    polite = bool(_POLITE_REQUEST.search(low))
    if question and not polite:
        return _result(ChangeClass.NOT_A_CHANGE, 0.85, signals=["question"], **common)

    # Collect every class the request touches, with its evidence and weight.
    hits: dict[ChangeClass, tuple[float, list[str]]] = {}

    def hit(cls: ChangeClass, weight: float, signals: list[str]) -> None:
        old = hits.get(cls)
        if old is None or weight > old[0]:
            hits[cls] = (weight, (old[1] if old else []) + signals)
        else:
            hits[cls] = (old[0], old[1] + signals)

    # Material: a material name with a material verb, or a plain material name.
    pure_materials = [m for m in materials if m not in _COLOUR_METALS]
    if materials and material_verb and not look_qualified:
        hit(ChangeClass.MATERIAL_ENGINEERING, 0.92, materials)
    elif pure_materials and not look_qualified and not look_or_material:
        hit(ChangeClass.MATERIAL_ENGINEERING, 0.75, pure_materials)

    elif not materials and material_verb and re.search(r"\bmaterial\b", low):
        hit(ChangeClass.MATERIAL_ENGINEERING, 0.8, ["material"])

    # Look or material: "metallic", "chrome", "wood look", "aluminium finish".
    # Section 5: ask, rather than guess which one the person means.
    if ChangeClass.MATERIAL_ENGINEERING not in hits and (
        look_or_material or (materials and look_qualified)
    ):
        hit(ChangeClass.AMBIGUOUS_APPEARANCE_OR_MATERIAL, 0.9, look_or_material or materials)

    # Appearance: a colour, a finish, colour words.
    shade_only = bool(re.search(r"\b(lighter|darker|brighter|paler|deeper)\s+\w*", low))
    if colour_hex or finish or look_words:
        signals = [s for s in (colour_name or colour_hex, finish) if s] + look_words
        hit(ChangeClass.APPEARANCE, 0.92 if (colour_hex or finish) else 0.8, signals)
    elif re.search(r"\b(darker|paler)\b", low):
        hit(ChangeClass.APPEARANCE, 0.7, ["shade"])

    # Geometry: sizes, moves, shapes; a feature being added is engineering geometry.
    sizes = [w for w in _found(low, _SIZE_WORDS) if not (shade_only and w in ("deeper",))]
    moves = _found(low, _MOVE_WORDS)
    shapes = _found(low, _SHAPE_WORDS)
    adds = _found(low, _ADD_WORDS)
    if feature and adds and feature not in ("edge", "edges", "corner", "corners", "wall", "walls"):
        hit(ChangeClass.ENGINEERING_GEOMETRY, 0.9, [*adds, feature])
    if sizes or moves or shapes or dimensions:
        weight = 0.9 if (sizes or moves or shapes) else 0.7
        hit(ChangeClass.GEOMETRIC, weight, sizes + moves + shapes + dimensions)

    # Performance goals and style directions.
    performance = _found(low, _PERFORMANCE_WORDS)
    if re.search(r"\blighter\b", low) and not (colour_hex or look_words):
        performance.append("lighter")
    if performance:
        # "brighter" with a colour is a shade, not lamp output.
        if not (colour_hex and set(performance) <= {"brighter"}):
            hit(ChangeClass.PERFORMANCE_OBJECTIVE, 0.85, performance)
    style = _found(low, _STYLE_WORDS)
    if style:
        hit(ChangeClass.AMBIGUOUS_STYLE, 0.85, style)

    if not hits:
        unknown = _result(ChangeClass.NOT_A_CHANGE, 0.4, signals=[], **common)
        return unknown.model_copy(update={"message": UNRECOGNISED_MESSAGE})

    ranked = sorted(hits.items(), key=lambda kv: (_PRIORITY.index(kv[0]), -kv[1][0]))
    top, (weight, signals) = ranked[0]
    also = [c for c, _ in ranked[1:]]
    confidence = weight if not also else min(weight, 0.75)
    if also and set(also) <= {ChangeClass.APPEARANCE} and top is ChangeClass.GEOMETRIC:
        # "make it red and wider": the geometric part decides, the look is kept.
        confidence = min(weight, 0.8)
    context_update = None
    if top is ChangeClass.MATERIAL_ENGINEERING:
        material = _material_name(low, materials, look_or_material)
        context_update = {"selected_material": material} if material else None
    appearance = (
        appearance_values()
        if top in (ChangeClass.APPEARANCE, ChangeClass.AMBIGUOUS_APPEARANCE_OR_MATERIAL)
        or ChangeClass.APPEARANCE in also
        else None
    )
    return _result(
        top,
        confidence,
        signals=signals,
        also=also,
        feature=feature,
        appearance=appearance,
        context_update=context_update,
        **common,
    )


#: When a request touches several classes, the one with the most engineering
#: consequence decides; the rest are listed in ``also``.
_PRIORITY: list[ChangeClass] = [
    ChangeClass.MATERIAL_ENGINEERING,
    ChangeClass.ENGINEERING_GEOMETRY,
    ChangeClass.GEOMETRIC,
    ChangeClass.PERFORMANCE_OBJECTIVE,
    ChangeClass.AMBIGUOUS_STYLE,
    ChangeClass.AMBIGUOUS_APPEARANCE_OR_MATERIAL,
    ChangeClass.APPEARANCE,
    ChangeClass.NOT_A_CHANGE,
]


def _material_name(low: str, materials: list[str], look_or_material: list[str]) -> str | None:
    """The material the request asks for, as the manufacturing context records it.

    "aluminium instead of plastic" and "from plastic to aluminium" both name
    the new one; a request naming no specific material gets ``None``, never a
    guess.
    """

    def at(word: str) -> int:
        match = re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", low)
        return match.start() if match else len(low)

    ordered = sorted(materials, key=at)
    instead = re.search(r"\binstead of\b|\brather than\b|\bnot\b", low)
    to_word = re.search(r"\b(?:switch(?:ed)?|change[d]?|go|move|from)\b.*?\bto\b", low)
    if instead:
        before = [m for m in ordered if at(m) < instead.start()]
        if before:
            return MATERIALS[before[0]]
    if to_word:
        after = [m for m in ordered if at(m) >= to_word.end()]
        if after:
            return MATERIALS[after[0]]
    if ordered:
        return MATERIALS[ordered[0]]
    for word in look_or_material:
        if word in MATERIALS:
            return MATERIALS[word]
    return None


# ---------------------------------------------------------------------------
# Stage 2: the model, only when the rules are not sure
# ---------------------------------------------------------------------------

#: ``prompt -> reply text``. Injectable, so tests never reach a provider.
LlmCall = Callable[[str], Awaitable[str]]


def llm_enabled() -> bool:
    """``METAFORGE_CHANGE_CLASSIFIER_LLM=on`` (default off): ask a model when unsure."""
    return (os.environ.get(LLM_ENV) or "").strip().lower() in ("1", "on", "true", "yes")


def _llm_prompt(text: str, rules: ChangeClassification) -> str:
    classes = ", ".join(c.value for c in ChangeClass)
    return (
        "Classify one request about a hardware product design before anything is changed. "
        f'Answer with one JSON object only: {{"class": one of [{classes}], '
        '"confidence": 0..1, "target_feature": string or null}.\n'
        "appearance = colour, finish, render look only. geometric = dimensions or "
        "placement. engineering_geometry = add or change a functional feature (holes, "
        "vents, ribs). material_engineering = the physical material. "
        "performance_objective = a goal such as stronger, cooler, more aerodynamic. "
        "ambiguous_style = a style direction such as futuristic. "
        "ambiguous_appearance_or_material = could be the look or the material "
        "(metallic, chrome, wood look). not_a_change = a question.\n"
        f"A keyword pass suggested {rules.change_class.value} "
        f"(confidence {rules.confidence}).\n"
        f"Request: {text[:600]}"
    )


def _parse_llm(reply: str) -> tuple[ChangeClass, float, str | None] | None:
    match = re.search(r"\{.*\}", reply or "", re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        cls = ChangeClass(str(data.get("class") or data.get("change_class")))
        confidence = min(max(float(data.get("confidence") or 0.0), 0.0), 1.0)
    except (ValueError, TypeError):
        return None
    feature = data.get("target_feature")
    return cls, confidence, str(feature)[:120] if isinstance(feature, str) and feature else None


def make_llm_call(*, timeout_s: float = 8.0, max_tokens: int = 120) -> LlmCall:
    """One short text completion over the gateway's configured providers."""

    async def call(prompt: str) -> str:
        import asyncio

        from api_gateway.chat.harness_backend import provider_config_from_env
        from orchestrator.harness.providers import (
            ProviderPipeline,
            RoleModelSlots,
            default_invoke,
        )
        from orchestrator.harness.providers.usage import usage_scope

        config = provider_config_from_env()
        candidates = list(config.slots.candidates("generator"))

        async def _invoke(spec: Any, _request: Any) -> Any:
            request = {
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.0,
            }
            return await default_invoke(spec, request)

        pipeline = ProviderPipeline(
            RoleModelSlots(slots={"change_classifier": candidates}), retry_policy=config.retry
        )
        with usage_scope(role="change_classifier"):
            result = await asyncio.wait_for(
                pipeline.complete("change_classifier", None, _invoke), timeout_s
            )
        return str(result.get("text", "") if isinstance(result, dict) else result or "")

    return call


async def classify_change(
    text: str,
    *,
    component: str | None = None,
    component_id: str | None = None,
    use_llm: bool | None = None,
    llm: LlmCall | None = None,
) -> ChangeClassification:
    """The rule pass, then the model when the rules are not sure. Never raises."""
    with tracer.start_as_current_span("change_classification.classify") as span:
        result = classify_rules(text, component=component, component_id=component_id)
        ask = llm_enabled() if use_llm is None else use_llm
        span.set_attribute("change.rules_class", result.change_class.value)
        span.set_attribute("change.rules_confidence", result.confidence)
        if result.uncertain and ask and result.stage == "rules":
            try:
                reply = await (llm or make_llm_call())(
                    _llm_prompt(strip_client_suffixes(text), result)
                )
                parsed = _parse_llm(reply)
            except Exception as exc:  # noqa: BLE001 - the rule result stands
                span.record_exception(exc)
                logger.warning("change_classification_llm_failed", error=str(exc))
                _count("llm_failed", result)
                parsed = None
            if parsed is not None:
                cls, confidence, feature = parsed
                rules_signals = [*result.signals, f"rules suggested {result.change_class.value}"]
                result = _result(
                    cls,
                    confidence,
                    signals=rules_signals,
                    stage="llm",
                    component=component,
                    component_id=component_id,
                    feature=feature or result.target_feature,
                    appearance=result.appearance
                    if cls in (ChangeClass.APPEARANCE, ChangeClass.AMBIGUOUS_APPEARANCE_OR_MATERIAL)
                    else None,
                    context_update=result.proposed_context_update
                    if cls is ChangeClass.MATERIAL_ENGINEERING
                    else None,
                    dimensions=result.stated_dimensions,
                )
        span.set_attribute("change.class", result.change_class.value)
        span.set_attribute("change.confidence", result.confidence)
        span.set_attribute("change.stage", result.stage)
        _count("classified", result)
        logger.info(
            "change_classified",
            change_class=result.change_class.value,
            confidence=result.confidence,
            stage=result.stage,
            handling=result.handling,
            also=[c.value for c in result.also],
            target_component=result.target_component,
            target_feature=result.target_feature,
        )
        return result


def confidence_band(confidence: float) -> str:
    if confidence >= 0.85:
        return "high"
    if confidence >= UNCERTAIN_BELOW:
        return "medium"
    return "low"


def _collector() -> Any:
    from observability.metrics import collector_for

    return collector_for("metaforge-gateway")


def _count(event: str, result: ChangeClassification) -> None:
    try:
        if event == "classified":
            _collector().record_change_classification(
                result.change_class.value, confidence_band(result.confidence), result.stage
            )
        else:
            _collector().record_change_classification(
                result.change_class.value, confidence_band(result.confidence), "llm_failed"
            )
    except Exception as exc:  # noqa: BLE001 - a counter never fails a turn
        logger.debug("change_classification_metric_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Messages: the operation event and the agent context
# ---------------------------------------------------------------------------


def operation_message(result: ChangeClassification) -> str:
    """The ``classified`` event's line: plain first, then class, confidence, routing."""
    also = f", also {', '.join(c.value for c in result.also)}" if result.also else ""
    return (
        f"{result.message} [{result.change_class.value}, confidence "
        f"{result.confidence:.2f} ({result.stage}){also}; routing: {result.handling}]"
    )


def agent_instruction(result: ChangeClassification, *, applied: str | None = None) -> str:
    """The block the agent is given for this turn, so it follows the handling."""
    lines = [
        "Change classification for this turn (decided before you act; follow it):",
        f"- class: {result.change_class.value} (confidence {result.confidence:.2f}, "
        f"{result.stage})",
        f"- handling: {result.handling}",
    ]
    if result.also:
        lines.append(f"- also touches: {', '.join(c.value for c in result.also)}")
    if result.target_component:
        lines.append(f"- target component: {result.target_component}")
    if result.target_feature:
        lines.append(f"- target feature: {result.target_feature}")
    if result.stated_dimensions:
        lines.append(f"- dimensions the person gave: {', '.join(result.stated_dimensions)}")
    elif result.change_class in (ChangeClass.GEOMETRIC, ChangeClass.ENGINEERING_GEOMETRY):
        lines.append("- the person gave no dimensions: propose them as assumptions")
    if result.proposed_context_update:
        lines.append(
            "- proposed manufacturing context update (the person confirms it): "
            + json.dumps(result.proposed_context_update)
        )
    if applied:
        lines.append(f"- already done: {applied}")
    lines.append(f"- rule: {_AGENT_RULE[result.change_class]}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------

_current: ContextVar[ChangeClassification | None] = ContextVar(
    "metaforge_turn_change_classification", default=None
)

#: What each guarded action is, and the classes that refuse it.
GUARDED_ACTIONS: dict[str, frozenset[ChangeClass]] = {
    "cad_rebuild": frozenset(
        {
            ChangeClass.APPEARANCE,
            ChangeClass.AMBIGUOUS_APPEARANCE_OR_MATERIAL,
            ChangeClass.PERFORMANCE_OBJECTIVE,
            ChangeClass.AMBIGUOUS_STYLE,
        }
    ),
    "manufacturing_context_write": frozenset(
        {ChangeClass.APPEARANCE, ChangeClass.AMBIGUOUS_APPEARANCE_OR_MATERIAL}
    ),
}

#: Tool ids that rebuild authoritative CAD or write the manufacturing context.
GUARDED_TOOLS: dict[str, str] = {
    "twin.build_exact_cad": "cad_rebuild",
    "twin.commit_geometry": "cad_rebuild",
    "twin.update_manufacturing_context": "manufacturing_context_write",
}

_REFUSAL: dict[ChangeClass, str] = {
    ChangeClass.APPEARANCE: (
        "this turn was classified as an appearance change, so it does not {action}. "
        "Your engineering model stays the same; ask for a shape or material change "
        "separately if you want one."
    ),
    ChangeClass.AMBIGUOUS_APPEARANCE_OR_MATERIAL: (
        "it is not yet clear whether the person means the look or the material, so "
        "this turn does not {action}. Ask them first."
    ),
    ChangeClass.PERFORMANCE_OBJECTIVE: (
        "this turn is a performance goal: propose alternatives and how to evaluate "
        "them first, and {action} only once the person chose one."
    ),
    ChangeClass.AMBIGUOUS_STYLE: (
        "this turn is a style direction: explore alternatives first, and {action} only "
        "once the person chose one."
    ),
}
_ACTION_WORDS = {
    "cad_rebuild": "rebuild CAD",
    "manufacturing_context_write": "write the manufacturing context",
}


class ChangeGuardRefusedError(RuntimeError):
    """A guarded action refused because of the turn's classification."""

    def __init__(self, action: str, result: ChangeClassification) -> None:
        self.action = action
        self.change_class = result.change_class
        reason = _REFUSAL[result.change_class].format(action=_ACTION_WORDS.get(action, action))
        super().__init__(f"Held: {reason}")


def current_turn_classification() -> ChangeClassification | None:
    return _current.get()


@contextmanager
def turn_classification(result: ChangeClassification | None) -> Iterator[None]:
    """Hold ``result`` as this turn's classification for the code run inside."""
    token = _current.set(result)
    try:
        yield
    finally:
        _current.reset(token)


def check_change_guard(action: str) -> None:
    """Refuse ``action`` when this turn's classification forbids it."""
    result = _current.get()
    if result is None:
        return
    if result.change_class not in GUARDED_ACTIONS.get(action, frozenset()):
        return
    error = ChangeGuardRefusedError(action, result)
    try:
        _collector().record_change_guard_refusal(result.change_class.value, action)
    except Exception as exc:  # noqa: BLE001
        logger.debug("change_guard_metric_failed", error=str(exc))
    logger.warning(
        "change_guard_refused",
        action=action,
        change_class=result.change_class.value,
        confidence=result.confidence,
    )
    raise error


def guard_tool_call(tool_id: str) -> None:
    """:func:`check_change_guard` for a tool id; a tool that is not guarded passes."""
    action = GUARDED_TOOLS.get(tool_id)
    if action is not None:
        check_change_guard(action)
