"""Transparent rule-based interpretation of free-text judge feedback.

No magic phrases: ordinary descriptions are matched against small, documented lexicons
with simple modifiers (intensifiers, diminishers, negation, comparatives) and attributed
to loaves (``Loaf A``, ``the first one``, ``the warmer one``, ``B was ...``). Every
interpreted value carries a confidence and the exact evidence span(s) of the input so the
dashboard can highlight *why* the system read the feedback that way.

Scales (all 0..1, 0.5 = neutral / no information):

* ``sourness``  0 = very mild/bland ......... 1 = very sour/sharp/vinegary
* ``texture``   0 = tight/gummy/closed ...... 1 = open/airy/fluffy (crumb openness/softness)
* ``density``   0 = light ................... 1 = dense/heavy/brick-like
* ``aroma``     0 = no/bland aroma .......... 1 = strong/complex aroma (+ descriptive tags)
* ``flavor_intensity`` 0 = flat/bland ....... 1 = strong/rich flavor (generic "flavor" words)

``parse_feedback(text, setpoints=None)`` returns a JSON-serializable dict; see the
docstring of that function for the structure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

PARSER_ID = "rule_based_lexicon_v1"
DIMENSIONS = ("sourness", "texture", "density", "aroma", "flavor_intensity")
LOAVES = ("A", "B")


# ---------------------------------------------------------------------------
# Lexicons
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Term:
    dims: dict[str, float]
    conf: float
    comparative: bool = False  # "-er" forms: sourer, denser, milder ...
    tags: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    noun: bool = False  # dimension nouns (sourness, aroma, flavor): level comes from modifiers


def T(conf: float, comparative: bool = False, tags=(), flags=(), noun=False, **dims) -> Term:
    return Term(dict(dims), conf, comparative, tuple(tags), tuple(flags), noun)


LEXICON: dict[str, Term] = {
    # --- sourness: adjectives -------------------------------------------------------
    "mild": T(0.85, sourness=0.25),
    "mellow": T(0.8, sourness=0.25),
    "gentle": T(0.6, sourness=0.3),
    "bland": T(0.75, sourness=0.15, aroma=0.15, flavor_intensity=0.15),
    "sweet": T(0.6, sourness=0.2),
    "sweetish": T(0.6, sourness=0.25),
    "milky": T(0.5, sourness=0.25),
    "neutral": T(0.5, sourness=0.35),
    "balanced": T(0.5, sourness=0.5),
    "sourish": T(0.8, sourness=0.6),
    "tangy": T(0.9, sourness=0.68),
    "tart": T(0.85, sourness=0.72),
    "sour": T(0.95, sourness=0.75),
    "acidic": T(0.9, sourness=0.8),
    "sharp": T(0.8, sourness=0.85, tags=("sharp",)),
    "vinegary": T(0.9, sourness=0.88, aroma=0.7, tags=("acetic",)),
    "vinegar": T(0.8, sourness=0.85, aroma=0.7, tags=("acetic",)),
    "puckering": T(0.85, sourness=0.95),
    "mouth-puckering": T(0.9, sourness=0.95),
    "biting": T(0.6, sourness=0.82),
    "zingy": T(0.6, sourness=0.65),
    "zesty": T(0.5, sourness=0.65),
    "lactic": T(0.6, sourness=0.55, tags=("lactic",)),
    "yogurty": T(0.6, sourness=0.55, aroma=0.55, tags=("lactic",)),
    "funky": T(0.5, sourness=0.6, aroma=0.7, tags=("funky",)),
    # sourness comparatives
    "sourer": T(0.9, comparative=True, sourness=0.78),
    "tangier": T(0.9, comparative=True, sourness=0.72),
    "tarter": T(0.85, comparative=True, sourness=0.74),
    "sharper": T(0.8, comparative=True, sourness=0.82),
    "milder": T(0.85, comparative=True, sourness=0.28),
    "sweeter": T(0.6, comparative=True, sourness=0.25),
    "mellower": T(0.8, comparative=True, sourness=0.28),
    # sourness nouns
    "sourness": T(0.9, noun=True, sourness=0.65),
    "tanginess": T(0.9, noun=True, sourness=0.65),
    "tang": T(0.85, noun=True, sourness=0.65),
    "acidity": T(0.85, noun=True, sourness=0.65),
    "sour flavor": T(0.95, noun=True, sourness=0.7),
    "sour flavour": T(0.95, noun=True, sourness=0.7),
    "sour taste": T(0.95, noun=True, sourness=0.7),
    "sour note": T(0.9, noun=True, sourness=0.65),
    "sour notes": T(0.9, noun=True, sourness=0.65),
    "sourdough tang": T(0.95, noun=True, sourness=0.68),
    # --- crumb texture / density -----------------------------------------------------
    "airy": T(0.95, texture=0.88, density=0.2),
    "fluffy": T(0.95, texture=0.88, density=0.2),
    "open crumb": T(0.95, texture=0.85, density=0.25),
    "open": T(0.6, texture=0.8, density=0.3),
    "light": T(0.55, texture=0.7, density=0.25),
    "soft": T(0.7, texture=0.72),
    "tender": T(0.6, texture=0.7),
    "pillowy": T(0.9, texture=0.92, density=0.15),
    "springy": T(0.6, texture=0.7),
    "bouncy": T(0.6, texture=0.7),
    "spongy": T(0.6, texture=0.65),
    "lacy": T(0.8, texture=0.85),
    "holey": T(0.75, texture=0.8),
    "big holes": T(0.8, texture=0.85, density=0.25),
    "bubbly": T(0.6, texture=0.75),
    "custardy": T(0.6, texture=0.65),
    "nice texture": T(0.6, texture=0.72), "good texture": T(0.6, texture=0.72),
    "great texture": T(0.65, texture=0.75), "lovely texture": T(0.65, texture=0.75),
    "nice crumb": T(0.6, texture=0.72), "good crumb": T(0.6, texture=0.72), "great crumb": T(0.65, texture=0.75),
    "well risen": T(0.7, texture=0.7, density=0.25),
    "well-risen": T(0.7, texture=0.7, density=0.25),
    "good rise": T(0.7, texture=0.7, density=0.25),
    "great rise": T(0.75, texture=0.72, density=0.22),
    "tall": T(0.55, density=0.3),
    "lofty": T(0.7, texture=0.75, density=0.25),
    "dense": T(0.95, density=0.85, texture=0.25),
    "heavy": T(0.75, density=0.8, texture=0.3),
    "tight": T(0.8, texture=0.2, density=0.7),
    "tight crumb": T(0.9, texture=0.2, density=0.7),
    "closed": T(0.6, texture=0.2, density=0.65),
    "closed crumb": T(0.9, texture=0.2, density=0.7),
    "compact": T(0.8, density=0.75, texture=0.25),
    "gummy": T(0.9, texture=0.15, density=0.7, flags=("gummy_possible_underbake_or_underproof",)),
    "doughy": T(0.8, texture=0.2, density=0.7, flags=("gummy_possible_underbake_or_underproof",)),
    "chewy": T(0.6, texture=0.4, density=0.6),
    "rubbery": T(0.7, texture=0.2, density=0.65),
    "brick": T(0.9, density=0.95, texture=0.1),
    "brick-like": T(0.9, density=0.95, texture=0.1),
    "like a brick": T(0.9, density=0.95, texture=0.1),
    "solid": T(0.5, density=0.75),
    "flat": T(0.6, density=0.7, texture=0.35, flags=("flat_loaf_possible_over_or_underproof",)),
    "collapsed": T(0.7, density=0.75, flags=("possible_overproofing",)),
    "deflated": T(0.7, density=0.75, flags=("possible_overproofing",)),
    "overproofed": T(0.8, density=0.7, flags=("possible_overproofing",)),
    "over-proofed": T(0.8, density=0.7, flags=("possible_overproofing",)),
    "overproved": T(0.8, density=0.7, flags=("possible_overproofing",)),
    "underproofed": T(0.8, density=0.75, texture=0.25, flags=("possible_underproofing",)),
    "under-proofed": T(0.8, density=0.75, texture=0.25, flags=("possible_underproofing",)),
    "wet": T(0.5, texture=0.35, flags=("wet_crumb",)),
    "moist": T(0.4, texture=0.6),
    "dry": T(0.4, texture=0.45, flags=("dry_crumb",)),
    "crumbly": T(0.4, texture=0.45),
    "cakey": T(0.4, texture=0.6),
    # texture comparatives
    "denser": T(0.95, comparative=True, density=0.85, texture=0.25),
    "heavier": T(0.75, comparative=True, density=0.8, texture=0.3),
    "lighter": T(0.6, comparative=True, texture=0.72, density=0.25),
    "fluffier": T(0.95, comparative=True, texture=0.88, density=0.2),
    "airier": T(0.95, comparative=True, texture=0.88, density=0.2),
    "softer": T(0.7, comparative=True, texture=0.72),
    "tighter": T(0.8, comparative=True, texture=0.2, density=0.7),
    "chewier": T(0.6, comparative=True, texture=0.4, density=0.6),
    "gummier": T(0.85, comparative=True, texture=0.15, density=0.7),
    # texture nouns (used for "keep the texture", "without losing the airiness")
    "airiness": T(0.9, noun=True, texture=0.85, density=0.2),
    "fluffiness": T(0.9, noun=True, texture=0.85, density=0.2),
    "openness": T(0.85, noun=True, texture=0.8, density=0.3),
    "lightness": T(0.8, noun=True, texture=0.75, density=0.25),
    "rise": T(0.5, noun=True, density=0.3, texture=0.65),
    "texture": T(0.5, noun=True, texture=0.6),
    "crumb": T(0.45, noun=True, texture=0.6),
    "density": T(0.8, noun=True, density=0.65),
    # --- aroma / flavor ---------------------------------------------------------------
    "aromatic": T(0.85, aroma=0.75),
    "fragrant": T(0.85, aroma=0.75),
    "complex": T(0.7, aroma=0.72, flavor_intensity=0.72, tags=("complex",)),
    "yeasty": T(0.7, aroma=0.5, tags=("yeasty",)),
    "beery": T(0.7, aroma=0.7, tags=("alcoholic", "possible_overfermentation")),
    "boozy": T(0.7, aroma=0.7, tags=("alcoholic", "possible_overfermentation")),
    "alcoholic": T(0.7, aroma=0.7, tags=("alcoholic", "possible_overfermentation")),
    "nutty": T(0.75, aroma=0.62, tags=("nutty",)),
    "wheaty": T(0.7, aroma=0.55, tags=("wheaty",)),
    "grainy": T(0.6, aroma=0.5, tags=("wheaty",)),
    "toasty": T(0.7, aroma=0.62, tags=("toasty",)),
    "malty": T(0.7, aroma=0.62, tags=("malty",)),
    "fruity": T(0.75, aroma=0.62, tags=("fruity",)),
    "buttery": T(0.7, aroma=0.55, tags=("buttery",)),
    "earthy": T(0.7, aroma=0.6, tags=("earthy",)),
    "cheesy": T(0.6, aroma=0.65, tags=("funky",)),
    "floury": T(0.5, aroma=0.3, tags=("raw_flour",)),
    "flavorful": T(0.8, aroma=0.65, flavor_intensity=0.75),
    "flavourful": T(0.8, aroma=0.65, flavor_intensity=0.75),
    "flavorless": T(0.85, flavor_intensity=0.1, aroma=0.15),
    "flavourless": T(0.85, flavor_intensity=0.1, aroma=0.15),
    "tasteless": T(0.85, flavor_intensity=0.1, aroma=0.15),
    "one-dimensional": T(0.7, flavor_intensity=0.25, aroma=0.3),
    "one dimensional": T(0.7, flavor_intensity=0.25, aroma=0.3),
    "rich": T(0.6, flavor_intensity=0.72, aroma=0.65),
    "smelled great": T(0.7, aroma=0.72),
    "smelled amazing": T(0.75, aroma=0.78),
    "smelled wonderful": T(0.75, aroma=0.75),
    "smelled lovely": T(0.7, aroma=0.72),
    "smelled good": T(0.6, aroma=0.65),
    # aroma / flavor nouns
    "aroma": T(0.85, noun=True, aroma=0.6),
    "aromas": T(0.85, noun=True, aroma=0.6),
    "smell": T(0.8, noun=True, aroma=0.6),
    "scent": T(0.8, noun=True, aroma=0.6),
    "fragrance": T(0.8, noun=True, aroma=0.6),
    "flavor": T(0.6, noun=True, flavor_intensity=0.6),
    "flavour": T(0.6, noun=True, flavor_intensity=0.6),
    "flavors": T(0.6, noun=True, flavor_intensity=0.6),
    "flavours": T(0.6, noun=True, flavor_intensity=0.6),
    "taste": T(0.5, noun=True, flavor_intensity=0.6),
}

# Words that are never attributes on their own when they precede these nouns
LIGHT_HEAVY_EXCLUDE_NEXT = {
    "crust", "color", "colour", "brown", "browning", "tan", "bake", "baked", "sourness", "tang",
    "acidity", "flavor", "flavour", "aroma", "smell", "sour", "touch", "hint",
}

HIGH_LEVEL = {
    "strong", "stronger", "strongest", "more", "intense", "bold", "bolder", "pronounced",
    "deep", "deeper", "big", "bigger", "lots", "plenty", "high", "higher", "extra", "richer",
    "noticeable", "punchy", "punchier", "increased", "enhanced", "heightened", "added",
    "stronger-tasting", "greater",
}
LOW_LEVEL = {
    "weak", "weaker", "faint", "fainter", "subtle", "subtler", "hint", "touch", "little",
    "less", "low", "lower", "light", "lighter", "mild", "milder", "gentle", "gentler",
    "slight", "reduced", "softer", "toned-down", "fewer", "minimal",
}
COMPARATIVE_LEVEL = {
    "stronger", "more", "bolder", "deeper", "bigger", "higher", "richer", "punchier",
    "increased", "enhanced", "heightened", "greater", "weaker", "less", "lower", "lighter",
    "milder", "gentler", "fainter", "subtler", "reduced", "fewer", "softer",
}
INTENSIFIERS = {
    "very", "really", "super", "extremely", "quite", "so", "too", "incredibly", "intensely",
    "strongly", "overly", "way", "highly", "pretty", "seriously", "distinctly", "definitely",
    "noticeably", "much", "far", "even", "properly", "remarkably", "especially",
}
DIMINISHERS = {
    "slightly", "somewhat", "kinda", "mildly", "faintly", "barely", "hardly", "marginally",
    "fairly", "moderately", "partly", "lightly", "bit", "tad", "touch", "little",
}
NEGATORS = {
    "not", "no", "never", "nothing", "wasn't", "isn't", "weren't", "aren't", "didn't",
    "doesn't", "don't", "hasn't", "hadn't", "lacked", "lacking", "lacks", "missing",
    "nor", "neither", "without", "shouldn't", "wouldn't", "won't", "can't", "cannot",
    "lack", "zero",
}
MULTI_DIMINISHERS = ("a little bit", "a little", "a bit", "kind of", "sort of", "a tad", "a touch")
MULTI_HIGH = ("lots of", "a lot of", "plenty of", "tons of", "more pronounced")
MULTI_LOW = ("a hint of", "hint of", "a touch of", "touch of", "barely any", "hardly any", "not much")

DESIRE_PATTERNS = [
    r"i'?d like", r"i would like", r"i'?d love", r"i would love", r"i want(?:ed)?", r"i'?d want",
    r"i wish", r"i'?d prefer", r"i would prefer", r"would prefer",
    (r"(?:it|they|this|that|loaf [ab]|[ab]|both)"
     r" (?:should|could|would|needs? to|ought to)(?: be| have| use| get)?"),
    r"should (?:be|have|get)", r"could (?:be|use|have)", r"would be (?:nice|better|great|good|ideal)",
    r"needs? (?:to be|more|less|a)", r"next time", r"aim(?:ing)? for", r"go for", r"make (?:it|them|the|both|loaf|[ab]\b)",
    r"try (?:to|for|making|a)", r"hoping for", r"i'?m hoping", r"looking for", r"let'?s (?:get|try|make|aim|go)",
    r"ideally", r"prefer(?:ably)? (?:it|them|a|more|less)", r"i'?d rather", r"would rather",
    r"wish (?:it|they) (?:was|were|had)", r"if (?:it|they) (?:was|were|had) ", r"more of",
    r"want (?:it|them|more|less|a|the|something)", r"would like", r"please",
]
DESIRE_RE = re.compile(r"\b(?:" + "|".join(DESIRE_PATTERNS) + r")")
NEGATED_DESIRE_RE = re.compile(
    r"\b(?:i )?(?:don'?t|do not|wouldn'?t|would not|didn'?t) (?:want|like|need)|\bshouldn'?t|\bshould not|\bno more\b|\bnot want"
)
MAINTAIN_RE = re.compile(
    r"\b(?:without (?:losing|sacrificing|compromising|giving up|hurting|ruining|losing out on)|"
    r"while (?:keeping|maintaining|preserving|retaining|still having)|"
    r"(?:but|and) (?:still )?keep(?:ing)?|keep(?:ing)? (?:the|it|its|that)|retain(?:ing)?|preserv(?:e|ing)|"
    r"maintain(?:ing)?|and still (?:be|have)|but still (?:be|have)|still (?:be|have|keep))\b"
)
AVOID_RE = re.compile(
    r"\b(?:without (?:it |them |the loaf |the bread )?(?:being|getting|becoming|turning|going|making it)|"
    r"but not(?: too| so| as| overly)?|not too|not overly|nothing too|avoid(?:ing)?|"
    r"don'?t (?:make it|let it)(?: get| be| become)?)\b"
)
PREF_POS_RE = re.compile(
    r"\b(?:preferred|prefer|liked|loved|enjoyed|favou?rite|winner|better|best|perfect|"
    r"just right|spot on|delicious|excellent|amazing|great|fantastic|wonderful|nicer|tastier|"
    r"winner|superior|the one i liked)\b"
)
TOO_RE = re.compile(r"\btoo\b")
STILL_RE = re.compile(r"\bstill\b|\b(?:the )?same\b")  # "still fluffy" / "same tang, but lighter" -> preserve
INDIFFERENT_RE = re.compile(
    r"\b(?:doesn'?t|does not|don'?t|do not|didn'?t) (?:really )?matter|\b(?:don'?t|do not) (?:really )?care|"
    r"\bnot (?:that |very )?important|\bunimportant|\birrelevant|\bwhatever\b|\bi'?m (?:not fussed|easy)"
)
ENOUGH_RE = re.compile(r"\benough\b")

SENTENCE_RE = re.compile(r"[^.!?;\n]+(?:[.!?;\n]+|$)")
CLAUSE_SPLIT_RE = re.compile(r",|\bbut\b|\bwhile\b|\bwhereas\b|\bhowever\b|\balthough\b|\bthough\b|\byet\b|\bthen\b")
TOKEN_RE = re.compile(r"[a-z0-9]+(?:['’-][a-z0-9]+)*")

LOAF_WORD = r"(?:loaf|chamber|bread|sample|batch|boule|dough)"
SUBJECT_VERBS = (
    r"(?:was|were|is|are|had|has|tasted|tastes|seemed|seems|felt|feels|smelled|smells|smelt|"
    r"looked|looks|came|turned|got|ended|rose|baked|'s|’s|had|did|needs|should|could|would|"
    r"definitely|also|still|really|too|just|is|-|–|—|:|,|\)|=)"
)


@dataclass
class LoafRef:
    start: int
    end: int
    loaves: tuple[str, ...]
    conf: float
    kind: str  # explicit | ordinal | setpoint | descriptor | other | both | than
    text: str
    note: str = ""


@dataclass
class Record:
    dims: dict[str, float]
    conf: float
    mode: str  # describe | desire | maintain | avoid | implied_desire | compare_ref
    loaves: tuple[str, ...]  # () = unattributed
    attribution_conf: float
    evidence: dict[str, Any]
    rule: str
    comparative: int = 0  # +1 "more of the term's pole", -1 "less", 0 absolute
    negated: bool = False
    too: bool = False
    not_enough: bool = False
    step: float = 0.15
    compare_to: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    noun: bool = False
    level: int = 0  # for nouns: +1 high, -1 low, 0 none
    term: str = ""


# ---------------------------------------------------------------------------
# Loaf references
# ---------------------------------------------------------------------------
def _setpoint_extremes(setpoints: dict[str, float] | None) -> tuple[str | None, str | None]:
    sp = {k: v for k, v in (setpoints or {}).items() if k in LOAVES and isinstance(v, (int, float))}
    if len(sp) < 2 or sp["A"] == sp["B"]:
        return None, None
    warm = max(sp, key=sp.get)
    cool = min(sp, key=sp.get)
    return warm, cool


def find_loaf_refs(text: str, low: str, setpoints: dict[str, float] | None) -> list[LoafRef]:
    refs: list[LoafRef] = []
    taken: list[tuple[int, int]] = []

    def add(m_start, m_end, loaves, conf, kind, note=""):
        for s, e in taken:
            if m_start < e and s < m_end:
                return
        taken.append((m_start, m_end))
        refs.append(LoafRef(m_start, m_end, tuple(loaves), conf, kind, text[m_start:m_end], note))

    # both
    for m in re.finditer(r"\b(?:both(?: (?:loaves|breads|of them|samples|ones))?|neither(?: (?:loaf|one|of them|bread))?|each (?:loaf|one)|the two loaves)\b", low):
        add(m.start(), m.end(), LOAVES, 0.9, "both")
    # than X (comparison targets; not subjects)
    for m in re.finditer(rf"\bthan (?:(?:the )?{LOAF_WORD}\s*)?(?:#\s*)?(a|b|1|2|one|two)\b", low):
        tok = m.group(1)
        raw = text[m.start(1):m.end(1)]
        if tok in ("a",) and raw == "a" and not re.search(LOAF_WORD, low[m.start():m.end()]):
            continue  # "than a ..." article
        loaf = "A" if tok in ("a", "1", "one") else "B"
        add(m.start(), m.end(), (loaf,), 0.95, "than")
    for m in re.finditer(r"\bthan (?:the )?(first|second|other)(?: one| loaf)?\b", low):
        add(m.start(), m.end(), ("A",) if m.group(1) == "first" else ("B",) if m.group(1) == "second" else (), 0.8, "than",
            note="other" if m.group(1) == "other" else "")
    # explicit loaf names
    for m in re.finditer(rf"\b{LOAF_WORD}\s*(?:#\s*)?(a|b|1|2|one|two)\b", low):
        tok = m.group(1)
        loaf = "A" if tok in ("a", "1", "one") else "B"
        add(m.start(), m.end(), (loaf,), 1.0, "explicit")
    for m in re.finditer(r"#\s*([12])\b|\bnumber (one|two|1|2)\b", low):
        tok = m.group(1) or m.group(2)
        add(m.start(), m.end(), ("A",) if tok in ("1", "one") else ("B",), 0.9, "ordinal")
    # ordinals
    for m in re.finditer(rf"\b(?:the )?(first|1st|second|2nd)(?: (?:one|{LOAF_WORD}))?\b", low):
        word = m.group(1)
        span = low[m.start():m.end()]
        if span.strip() in ("first", "second", "1st", "2nd"):
            # bare "first"/"second" only counts when preceded by "the" -> handled by regex group; skip bare
            continue
        if re.match(r"(?:the )?(?:first|second) (?:time|of all|bite|impression|thing|sip|taste|glance)", low[m.start():m.end() + 12]):
            continue
        add(m.start(), m.end(), ("A",) if word in ("first", "1st") else ("B",), 0.85, "ordinal")
    # warmer / cooler
    warm, cool = _setpoint_extremes(setpoints)
    for m in re.finditer(
        rf"\b(?:the )?(warmer|hotter|warm|hot|higher[- ]temp(?:erature)?|cooler|colder|cool|cold|lower[- ]temp(?:erature)?)"
        rf"(?:[- ]temp(?:erature)?)?(?:[- ](?:fermented|proofed|proved))? (?:one|{LOAF_WORD})\b",
        low,
    ):
        w = m.group(1)
        is_warm = w.startswith(("warm", "hot", "higher"))
        target = warm if is_warm else cool
        if target is None:  # fall back to the design: B is the warm chamber
            target = "B" if is_warm else "A"
            add(m.start(), m.end(), (target,), 0.6, "setpoint", note="resolved by default design (B warmer)")
        else:
            add(m.start(), m.end(), (target,), 0.85, "setpoint", note="resolved from chamber setpoints")
    # "the 27 degree one"
    sp = {k: float(v) for k, v in (setpoints or {}).items() if k in LOAVES and isinstance(v, (int, float))}
    if not sp:
        sp = {"A": 22.0, "B": 27.0}
    for m in re.finditer(
        rf"\b(?:the )?(\d{{1,2}}(?:\.\d)?)\s*(?:°\s*c?|degrees?(?: c(?:elsius)?)?|deg(?:rees)? ?c?|c)\b(?:[- ](?:one|{LOAF_WORD}))?",
        low,
    ):
        span = low[m.start():m.end()]
        if not (span.startswith("the ") or re.search(rf"(?:one|{LOAF_WORD})$", span)):
            continue
        val = float(m.group(1))
        best = min(sp, key=lambda k: abs(sp[k] - val))
        if abs(sp[best] - val) <= 1.5:
            add(m.start(), m.end(), (best,), 0.85, "setpoint", note=f"{val:g} °C matched chamber {best} setpoint")
    # descriptor references: "the sourer one", "the milder loaf"
    for m in re.finditer(
        rf"\bthe (sourer|more sour|tangier|sharper|more acidic|milder|less sour|sweeter|mellower)(?: one| {LOAF_WORD})\b", low
    ):
        sour_word = m.group(1) in ("sourer", "more sour", "tangier", "sharper", "more acidic")
        target = (warm or "B") if sour_word else (cool or "A")
        add(m.start(), m.end(), (target,), 0.55, "descriptor",
            note="resolved by design assumption (warmer chamber expected sourer)")
    # the other one
    for m in re.finditer(rf"\bthe other(?: one| {LOAF_WORD})?\b", low):
        add(m.start(), m.end(), (), 0.75, "other")
    # bare capital letters "A was ...", "B: ...", "(A)"
    for m in re.finditer(r"(?<![A-Za-z0-9'’])([AB])(?![A-Za-z0-9'’])", text):
        letter = m.group(1)
        after = text[m.end():m.end() + 16]
        before = text[max(0, m.start() - 12):m.start()].lower()
        verb_after = re.match(r"\s*" + SUBJECT_VERBS, after.lower()) is not None
        conj_before = re.search(r"(?:\band|\bor|\bvs\.?|\bversus|\bthan|\bfor|\bwith|\bto|\(|,|;|:|^)\s*$", before) is not None
        end_after = re.match(r"\s*(?:[.,;:!?)]|$|and\b|or\b|were\b|both\b)", after) is not None
        if letter == "A" and not (verb_after or (conj_before and end_after)):
            continue  # most likely the article "A"
        if letter == "B" and not (verb_after or conj_before or end_after):
            continue
        kind = "than" if re.search(r"\bthan\s*$", before) else "explicit"
        add(m.start(), m.end(), (letter,), 0.9, kind)
    for r in refs:
        if r.kind not in ("both", "than") and re.search(r"\bthan\s*$", low[max(0, r.start - 6):r.start]):
            r.kind = "than"
    refs.sort(key=lambda r: r.start)
    return refs


# ---------------------------------------------------------------------------
# Term matching
# ---------------------------------------------------------------------------
_TERMS_SORTED = sorted(LEXICON, key=len, reverse=True)
_TERM_RE = re.compile(r"(?<![a-z0-9'’-])(" + "|".join(re.escape(t) for t in _TERMS_SORTED) + r")(?![a-z0-9'’-])")


def _find_terms(low: str, blocked: list[tuple[int, int]]) -> list[tuple[int, int, str]]:
    out = []
    for m in _TERM_RE.finditer(low):
        s, e = m.start(), m.end()
        if any(s < be and bs < e for bs, be in blocked):
            continue
        term = m.group(1)
        nxt = TOKEN_RE.search(low, e)
        nxt_word = nxt.group(0) if nxt and nxt.start() - e <= 2 else ""
        if term in ("light", "lighter", "heavy", "heavier", "mild", "milder", "gentle", "sweet", "rich") and nxt_word in LIGHT_HEAVY_EXCLUDE_NEXT:
            continue  # modifier of another noun ("light crust", "mild sourness")
        if term == "flat":
            prev = low[max(0, s - 12):s]
            prev = low[max(0, s - 30):s]
            if re.search(r"tast(?:ed|es|e)\b(?:\s+[a-z']+){0,3}\s*$|flavou?r\s*(?:was|is)?\s*$", prev):
                out.append((s, e, "bland"))
                continue
        if term == "open" and nxt_word not in ("crumb", "texture", "structure", "", "and", "with", "inside"):
            if not re.search(r"(?:was|is|more|very|nice|nicely|quite|less|an?)\s*$", low[max(0, s - 8):s]):
                continue
        if term in ("sweet",) and nxt_word in ("spot",):
            continue
        if term == "taste" and re.search(r"\b(?:to|my|in|the)\s*$", low[max(0, s - 5):s]) and nxt_word not in ("was", "is"):
            # "to my taste" etc.
            if re.search(r"\bmy\s*$", low[max(0, s - 4):s]):
                continue
        out.append((s, e, term))
    return out


def _prev_words(low: str, start: int, floor: int, max_words: int = 4) -> list[tuple[int, int, str]]:
    seg = low[floor:start]
    words = [(m.start() + floor, m.end() + floor, m.group(0)) for m in TOKEN_RE.finditer(seg)]
    return words[-max_words:]


def _modifiers(low: str, start: int, floor: int) -> dict[str, Any]:
    words = _prev_words(low, start, floor)
    toks = [w for _, _, w in words]
    joined = " ".join(toks)
    res = {"intens": False, "dimin": False, "neg": False, "too": False, "comp": 0, "level": 0,
           "mod_start": start, "mod_text": []}

    def mark(i):
        res["mod_start"] = min(res["mod_start"], words[i][0])
        res["mod_text"].append(words[i][2])

    for phrase in MULTI_DIMINISHERS:
        if joined.endswith(phrase) or (" " + phrase + " ") in (" " + joined + " "):
            res["dimin"] = True
            res["mod_text"].append(phrase)
    for phrase in MULTI_HIGH:
        if (" " + phrase + " ") in (" " + joined + " "):
            res["level"] = 1
            res["mod_text"].append(phrase)
    for phrase in MULTI_LOW:
        if (" " + phrase + " ") in (" " + joined + " "):
            res["level"] = -1
            res["mod_text"].append(phrase)
    for i, w in enumerate(toks):
        if w in NEGATORS or w.endswith("n't") or w.endswith("n’t"):
            res["neg"] = True
            mark(i)
        elif w in INTENSIFIERS:
            res["intens"] = True
            if w == "too":
                res["too"] = True
            mark(i)
        elif w in DIMINISHERS and not (w in ("little", "touch", "bit") and res["level"]):
            if w == "little" and i + 1 < len(toks) and toks[i + 1] in ("more", "less"):
                res["dimin"] = True
            elif w in ("little", "bit", "touch", "tad"):
                if i > 0 and toks[i - 1] == "a":
                    res["dimin"] = True
            else:
                res["dimin"] = True
            mark(i)
        if w in COMPARATIVE_LEVEL:
            res["comp"] = 1 if w in HIGH_LEVEL else -1
            mark(i)
        if w in HIGH_LEVEL and w not in ("light",):
            res["level"] = 1
        elif w in LOW_LEVEL and not (w == "little" and i + 1 < len(toks) and toks[i + 1] in ("more", "less", "bit")):
            if w not in ("touch",) or (i > 0 and toks[i - 1] == "a"):
                res["level"] = -1
    # "a little" before "more" is a diminisher, not a low level
    if res["comp"] == 1 and "more" in toks:
        res["level"] = 1
    if res["comp"] == -1 and "less" in toks:
        res["level"] = -1
    # "not very sour": negation precedes intensifier
    return res


def _apply(score: float, mods: dict[str, Any]) -> float:
    d = score - 0.5
    if mods["neg"] and mods["intens"] and not mods["too"]:
        d = 0.1 * d - (0.12 if d > 0 else -0.12)  # "not very sour" -> mildly sour
    elif mods["neg"]:
        d = -0.6 * d
    else:
        if mods["intens"]:
            d *= 1.35
        if mods["dimin"]:
            d *= 0.55
    return round(min(0.98, max(0.02, 0.5 + d)), 3)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def _sentences(low: str) -> list[tuple[int, int]]:
    spans = []
    for m in SENTENCE_RE.finditer(low):
        s, e = m.start(), m.end()
        if low[s:e].strip():
            spans.append((s, e))
    # merge decimal-number splits like "22.5"
    merged: list[tuple[int, int]] = []
    for s, e in spans:
        if merged and re.search(r"\d\.$", low[merged[-1][0]:merged[-1][1]]) and re.match(r"\d", low[s:e]):
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return merged


def _clauses(low: str, s: int, e: int) -> list[tuple[int, int]]:
    out = []
    cur = s
    for m in CLAUSE_SPLIT_RE.finditer(low, s, e):
        if m.start() > cur:
            out.append((cur, m.start()))
        cur = m.end()
    if cur < e:
        out.append((cur, e))
    return [(a, b) for a, b in out if low[a:b].strip()] or [(s, e)]


def _label(dim: str, score: float) -> str:
    if dim == "sourness":
        bands = [(0.2, "very mild"), (0.38, "mild"), (0.52, "lightly tangy"), (0.66, "tangy"), (0.82, "sour"), (1.01, "very sour / sharp")]
    elif dim == "texture":
        bands = [(0.25, "tight / gummy"), (0.42, "somewhat tight"), (0.58, "medium"), (0.75, "fairly open / soft"), (1.01, "open / airy / fluffy")]
    elif dim == "density":
        bands = [(0.3, "light"), (0.45, "fairly light"), (0.58, "medium"), (0.72, "somewhat dense"), (1.01, "dense / heavy")]
    else:
        bands = [(0.25, "faint / bland"), (0.45, "modest"), (0.6, "noticeable"), (0.75, "pronounced"), (1.01, "strong / complex")]
    for hi, lab in bands:
        if score < hi:
            return lab
    return bands[-1][1]


def _ev(text: str, s: int, e: int, rule: str) -> dict[str, Any]:
    return {"text": text[s:e], "start": s, "end": e, "rule": rule}


def parse_feedback(text: str, setpoints: dict[str, float] | None = None) -> dict[str, Any]:
    """Interpret free-text sensory feedback.

    Returns a dict with keys:
      ``method``, ``provenance`` ("judge_feedback"), ``raw_text``,
      ``loaves``: {"A"|"B": {"mentioned": bool, "references": [...],
                   "attributes": {dim: {"score", "label", "confidence", "evidence": [...]}}}},
      ``unattributed``: {"attributes": {...}} (statements without a loaf),
      ``comparisons``: [{"dimension", "higher", "lower", "evidence"}],
      ``desired``: {dim: {"direction": increase|decrease|maintain, "strength", "confidence",
                   "target_level", "bound", "preferred_pole", "anchor_loaf", "evidence"}},
      ``desired_summary``: human-readable sentence,
      ``primary_dimension``, ``preferred_loaf``, ``flags``, ``aroma_tags``,
      ``overall_confidence``, ``notes``.
    """
    text = text or ""
    norm = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    low = norm.lower()
    if len(low) != len(text):  # extremely rare unicode case-length change; keep offsets sane
        low = "".join(ch if len(ch.lower()) != 1 else ch.lower() for ch in norm)
    refs = find_loaf_refs(norm, low, setpoints)
    blocked = [(r.start, r.end) for r in refs if r.kind in ("descriptor",)]
    terms = _find_terms(low, blocked)
    records: list[Record] = []
    notes: list[str] = []
    preferences: list[dict[str, Any]] = []
    dislikes: list[dict[str, Any]] = []
    last_subject: tuple[str, ...] = ()
    last_subject_conf = 0.0

    for s_start, s_end in _sentences(low):
        sent = low[s_start:s_end]
        dm = DESIRE_RE.search(low, s_start, s_end)
        desire_pos = dm.start() if dm else None
        neg_desire = NEGATED_DESIRE_RE.search(low, s_start, s_end)
        if neg_desire and (desire_pos is None or neg_desire.start() <= desire_pos + 12):
            desire_pos = neg_desire.start() if desire_pos is None else min(desire_pos, neg_desire.start())
        sent_refs = [r for r in refs if s_start <= r.start < s_end]
        subject_refs = [r for r in sent_refs if r.kind not in ("than",)]
        # pronoun continuation: inherit last subject
        continuation = re.match(r"\s*(?:it|its|it's|this one|that one|this|that|they|also|plus|and|the crumb|the texture|"
                                r"the flavou?r|the taste|the smell|the aroma|the crust|the inside|crumb|texture)\b", sent)
        # preference
        for pm in PREF_POS_RE.finditer(low, s_start, s_end):
            window = low[max(s_start, pm.start() - 25):pm.start()]
            neg = re.search(r"\b(?:not|n't|didn't|don't|wasn't|less)\b[^.]*$", window) is not None
            candidates = [r for r in subject_refs if abs(r.start - pm.start()) < 60 and r.loaves]
            if not candidates:
                continue
            word = pm.group(0)
            if word in ("better", "best") and re.search(r"\b(?:would be|could be|be)\s*$", low[max(0, pm.start() - 12):pm.start()]):
                continue  # "would be better" -> desire, not preference
            # nearest reference
            r = min(candidates, key=lambda r: abs(r.start - pm.start()))
            entry = {"loaf": r.loaves[0] if len(r.loaves) == 1 else None,
                     "evidence": _ev(norm, min(r.start, pm.start()), max(r.end, pm.end()), "preference")}
            if entry["loaf"]:
                (dislikes if neg else preferences).append(entry)

        current = last_subject if (continuation or not subject_refs) else ()
        current_conf = (0.75 if continuation else 0.6) * (last_subject_conf or 1.0) if current else 0.0
        if desire_pos is not None and not subject_refs and not continuation:
            pron = re.search(r"\b(?:it|its|this one|that one|that loaf|this loaf)\b", low[desire_pos:s_end])
            if pron and last_subject:
                current, current_conf = last_subject, 0.5
            else:
                current, current_conf = (), 0.0
        if subject_refs and subject_refs[0].start - s_start > 40 and not continuation and last_subject:
            current, current_conf = last_subject, 0.6
        for c_start, c_end in _clauses(low, s_start, s_end):
            clause_refs = [r for r in sent_refs if c_start <= r.start < c_end]
            clause_terms = [t for t in terms if c_start <= t[0] < c_end]
            maint = MAINTAIN_RE.search(low, c_start, c_end)
            avoid = AVOID_RE.search(low, c_start, c_end)
            # clause-level maintain/avoid also applies if introduced right before the clause
            in_desire_clause = desire_pos is not None and c_end > desire_pos
            for t_start, t_end, term_name in clause_terms:
                term = LEXICON[term_name]
                # floor for modifier search: clause start or previous term end in clause
                prev_ends = [te for ts, te, _ in clause_terms if te <= t_start]
                floor = max([c_start] + prev_ends)
                phrase_end = None
                for mm in (maint, avoid):
                    if mm and mm.end() <= t_start:
                        phrase_end = mm.end() if phrase_end is None else max(phrase_end, mm.end())
                if phrase_end is not None:
                    floor = max(floor, phrase_end)
                mods = _modifiers(low, t_start, floor)
                nd = NEGATED_DESIRE_RE.search(low, c_start, c_end)
                if nd and nd.end() <= t_start and not (phrase_end and nd.start() < phrase_end):
                    mods["neg"] = True
                # loaf attribution
                prec = [r for r in clause_refs if r.end <= t_start and r.kind != "than"]
                foll = [r for r in clause_refs if r.start >= t_end and r.kind != "than"]
                than = [r for r in clause_refs if r.kind == "than" and r.start >= t_end]
                if prec:
                    ref = prec[-1]
                    loaves, aconf = ref.loaves, ref.conf
                    if ref.kind == "other":
                        loaves = tuple(l for l in LOAVES if l not in current) if current else ()
                elif current:
                    loaves, aconf = current, current_conf
                elif foll and not in_desire_clause:
                    loaves, aconf = foll[0].loaves, foll[0].conf * 0.85
                else:
                    loaves, aconf = (), 0.5
                compare_to = ()
                if than:
                    compare_to = than[0].loaves or tuple(l for l in LOAVES if l not in loaves)
                comparative = 0
                if term.comparative:
                    comparative = 1
                elif mods["comp"]:
                    comparative = mods["comp"]
                # mode
                mode = "describe"
                if maint and maint.start() < t_start:
                    mode = "maintain"
                elif avoid and avoid.start() < t_start and (in_desire_clause or comparative == 0):
                    mode = "avoid"
                    if "too" in avoid.group(0) or "overly" in avoid.group(0):
                        mods["too"] = False
                elif (in_desire_clause and t_start >= desire_pos and not comparative
                      and STILL_RE.search(low, max(desire_pos, c_start), t_start)):
                    mode = "maintain"
                elif in_desire_clause and t_start >= desire_pos or in_desire_clause and comparative and t_start < desire_pos and desire_pos - t_end < 15:
                    mode = "desire"
                if mode == "describe" and in_desire_clause and t_start >= (desire_pos or 0):
                    mode = "desire"
                tail = low[t_end:min(c_end, t_end + 12)]
                not_enough = bool(mods["neg"] and ENOUGH_RE.match(tail.strip()) is not None) or (
                    "enough" in low[max(c_start, t_start - 12):t_start] and mods["neg"])
                rule_bits = [f"lexicon:{term_name}"]
                if mods["mod_text"]:
                    rule_bits.append("modifiers:" + "+".join(dict.fromkeys(mods["mod_text"])))
                if comparative:
                    rule_bits.append("comparative")
                if mode != "describe":
                    rule_bits.append(f"mode:{mode}")
                ev_start = min(mods["mod_start"], t_start)
                if mode == "maintain" and maint:
                    ev_start = min(ev_start, maint.start())
                elif mode == "avoid" and avoid:
                    ev_start = min(ev_start, avoid.start())
                dims = {}
                for dim, base in term.dims.items():
                    if term.noun:
                        lvl = mods["level"]
                        if mods["neg"] and not lvl:
                            lvl = -1
                        if lvl > 0:
                            val = 0.8 if not mods["dimin"] else 0.65
                        elif lvl < 0:
                            val = 0.25 if not mods["neg"] or mods["level"] >= 0 else 0.15
                        else:
                            val = base
                        if mods["intens"] and lvl:
                            val = 0.5 + (val - 0.5) * 1.25
                        dims[dim] = round(min(0.98, max(0.02, val)), 3)
                    elif comparative and mods["comp"] == -1 and not term.comparative:
                        # "less sour" -> lower than reference on this dimension
                        dims[dim] = round(0.5 - (base - 0.5) * 0.6, 3)
                    else:
                        dims[dim] = _apply(base, mods)
                step = 0.15
                if mods["intens"]:
                    step = 0.25
                if mods["dimin"]:
                    step = 0.08
                conf = round(term.conf * (0.85 if mods["neg"] else 1.0) * (0.9 if term.noun and not mods["level"] and mode == "describe" else 1.0), 3)
                rec = Record(
                    dims=dims, conf=conf, mode=mode, loaves=loaves, attribution_conf=aconf,
                    evidence=_ev(norm, ev_start, t_end, " ".join(rule_bits)), rule=" ".join(rule_bits),
                    comparative=comparative, negated=mods["neg"], too=mods["too"], not_enough=not_enough,
                    step=step, compare_to=compare_to, tags=term.tags, flags=term.flags, noun=term.noun,
                    level=mods["level"], term=term_name,
                )
                # "texture doesn't matter" -> judge is indifferent to this dimension
                if INDIFFERENT_RE.search(low, t_end, c_end) and not mods["level"] and not comparative:
                    notes.append(f"Judge indifferent to '{term_name}' (\"{norm[t_start:c_end].strip()}\"); not used as a preference.")
                    continue
                # noun-only mentions in plain description with no level carry no information
                if term.noun and mode == "describe" and not mods["level"] and not mods["neg"]:
                    if term_name in ("texture", "crumb", "rise", "flavor", "flavour", "flavors", "flavours", "taste", "density",
                                     "aroma", "aromas", "smell", "scent", "fragrance"):
                        continue
                records.append(rec)
            if clause_refs:
                subj = [r for r in clause_refs if r.kind not in ("than",) and r.loaves]
                if subj:
                    current, current_conf = subj[-1].loaves, subj[-1].conf
        if current:
            last_subject, last_subject_conf = current, current_conf

    return _assemble(norm, refs, records, preferences, dislikes, notes)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
def _combine(items: list[tuple[float, float, dict]]) -> dict[str, Any]:
    wsum = sum(c for _, c, _ in items)
    score = sum(s * c for s, c, _ in items) / wsum if wsum else 0.5
    miss = 1.0
    for _, c, _ in items:
        miss *= 1.0 - min(0.95, c)
    return {"score": round(score, 3), "confidence": round(min(0.97, 1.0 - miss), 3),
            "evidence": [e for _, _, e in items]}


def _assemble(text, refs, records, preferences, dislikes, notes) -> dict[str, Any]:
    loaf_items: dict[str, dict[str, list]] = {l: {} for l in LOAVES}
    unattr: dict[str, list] = {}
    comparisons: list[dict[str, Any]] = []
    desire_votes: dict[str, list[dict[str, Any]]] = {}
    flags: set[str] = set()
    tags: dict[str, set[str]] = {"A": set(), "B": set(), "unattributed": set(), "desired": set()}

    for r in records:
        if r.mode == "describe":
            conf = round(r.conf * (r.attribution_conf if r.loaves else 0.75), 3)
        else:
            conf = round(r.conf * (max(0.8, r.attribution_conf) if r.loaves else 0.9), 3)
        if r.mode == "describe":
            for dim, val in r.dims.items():
                item = (val, conf, r.evidence)
                if r.loaves:
                    for l in r.loaves:
                        loaf_items[l].setdefault(dim, []).append(item)
                else:
                    unattr.setdefault(dim, []).append(item)
                # comparisons
                if r.comparative and len(r.loaves) == 1:
                    me = r.loaves[0]
                    other = r.compare_to[0] if r.compare_to else ("B" if me == "A" else "A")
                    if other != me:
                        up = (val > 0.5)
                        higher, lower = (me, other) if up else (other, me)
                        comparisons.append({"dimension": dim, "higher": higher, "lower": lower,
                                            "evidence": r.evidence})
            for l in (r.loaves or ("unattributed",)):
                tags[l].update(r.tags)
            for f in r.flags:
                flags.add(f"{f}" + (f"_{'_'.join(r.loaves)}" if r.loaves else ""))
            # implied desires: "too sour", "not sour enough", "lacked tang"
            if r.too or r.not_enough or (r.negated and r.noun and r.evidence["text"].lower().startswith(("lack", "miss"))):
                for dim, val in r.dims.items():
                    term_pole_high = (LEXICON_POLE(r, dim) > 0.5)
                    if r.too:
                        direction = "decrease" if term_pole_high else "increase"
                    else:
                        direction = "increase" if term_pole_high else "decrease"
                    desire_votes.setdefault(dim, []).append({
                        "direction": direction, "conf": round(conf * 0.7, 3), "step": r.step, "target_level": None,
                        "anchor": r.loaves[0] if len(r.loaves) == 1 else None, "evidence": r.evidence,
                        "rule": "implied:" + ("too" if r.too else "not_enough"), "pole": None, "bound": None,
                    })
        else:
            tags["desired"].update(r.tags)
            for dim, val in r.dims.items():
                pole_high = LEXICON_POLE(r, dim) > 0.5
                vote = {"conf": conf, "step": r.step, "target_level": None, "evidence": r.evidence,
                        "anchor": r.loaves[0] if len(r.loaves) == 1 else None, "rule": r.mode,
                        "pole": None, "bound": None}
                if r.mode == "maintain":
                    keep_high = pole_high != r.negated
                    vote.update(direction="maintain", pole="high" if keep_high else "low")
                elif r.mode == "avoid":
                    # avoid the term's pole: "not too sour", "without getting dense"
                    avoid_high = pole_high != r.negated
                    vote.update(direction="maintain", pole="low" if avoid_high else "high",
                                bound=round(0.5 + (LEXICON_POLE(r, dim) - 0.5) * 0.8, 3))
                else:  # desire
                    if r.noun:
                        if r.level > 0 or (r.level == 0 and not r.negated):
                            up = True
                        else:
                            up = False
                        if r.negated and r.level > 0:
                            vote.update(direction="maintain", pole="low")
                            desire_votes.setdefault(dim, []).append(vote)
                            continue
                        direction = "increase" if up else "decrease"
                        if r.level == 0 and not r.comparative:
                            vote["target_level"] = 0.62 if up else 0.35
                    elif r.comparative:
                        toward_pole = r.comparative > 0
                        if r.negated:
                            vote.update(direction="maintain", pole="low" if pole_high == toward_pole else "high")
                            desire_votes.setdefault(dim, []).append(vote)
                            continue
                        direction = "increase" if (pole_high == toward_pole) else "decrease"
                    else:
                        if r.negated or r.too:
                            if r.too and r.negated:
                                vote.update(direction="maintain", pole="low" if pole_high else "high",
                                            bound=round(0.5 + (LEXICON_POLE(r, dim) - 0.5) * 0.8, 3))
                                desire_votes.setdefault(dim, []).append(vote)
                                continue
                            direction = "decrease" if pole_high else "increase"
                        else:
                            direction = "increase" if pole_high else "decrease"
                            vote["target_level"] = val
                    vote["direction"] = direction
                desire_votes.setdefault(dim, []).append(vote)
            for f in r.flags:
                flags.add(f)

    # reconcile comparisons with absolute scores
    loaves_out: dict[str, Any] = {}
    combined: dict[str, dict[str, dict[str, Any]]] = {l: {d: _combine(v) for d, v in loaf_items[l].items()} for l in LOAVES}
    for comp in comparisons:
        d, hi, lo = comp["dimension"], comp["higher"], comp["lower"]
        if hi not in combined or lo not in combined:
            continue
        h, l_ = combined[hi].get(d), combined[lo].get(d)
        if h and l_ and h["score"] <= l_["score"] + 0.05:
            h["score"] = round(min(0.98, l_["score"] + 0.15), 3)
            h["confidence"] = round(h["confidence"] * 0.85, 3)
            h["evidence"].append({**comp["evidence"], "rule": "comparison-adjusted"})
        elif h and not l_:
            combined[lo][d] = {"score": round(max(0.02, h["score"] - 0.2), 3), "confidence": round(h["confidence"] * 0.45, 3),
                               "evidence": [{**comp["evidence"], "rule": f"inferred: {lo} lower than {hi}"}]}
        elif l_ and not h:
            combined[hi][d] = {"score": round(min(0.98, l_["score"] + 0.2), 3), "confidence": round(l_["confidence"] * 0.45, 3),
                               "evidence": [{**comp["evidence"], "rule": f"inferred: {hi} higher than {lo}"}]}
    for l in LOAVES:
        attrs = {}
        for d, v in combined[l].items():
            attrs[d] = {**v, "label": _label(d, v["score"])}
        loaf_refs = [{"text": r.text, "start": r.start, "end": r.end, "kind": r.kind, "confidence": r.conf, "note": r.note}
                     for r in refs if l in r.loaves and r.kind != "both"]
        loaves_out[l] = {
            "mentioned": bool(loaf_refs) or bool(attrs),
            "references": loaf_refs,
            "attributes": attrs,
            "aroma_tags": sorted(tags[l]),
        }
    unattr_out = {d: {**_combine(v), "label": _label(d, _combine(v)["score"])} for d, v in unattr.items()}

    # preferred loaf
    pref_score = {l: 0 for l in LOAVES}
    for p in preferences:
        pref_score[p["loaf"]] += 1
    for p in dislikes:
        pref_score[p["loaf"]] -= 1
    preferred = None
    if max(pref_score.values()) > 0 and pref_score["A"] != pref_score["B"]:
        preferred = max(pref_score, key=pref_score.get)

    # desired direction per dimension
    desired: dict[str, Any] = {}
    for dim, votes in desire_votes.items():
        weights: dict[str, float] = {}
        for v in votes:
            weights[v["direction"]] = weights.get(v["direction"], 0.0) + v["conf"]
        pool = votes
        weights = {}
        for v in pool:
            weights[v["direction"]] = weights.get(v["direction"], 0.0) + v["conf"]
        direction = max(sorted(weights), key=lambda k: weights[k])
        chosen = [v for v in pool if v["direction"] == direction]
        conflict = len(weights) > 1
        miss = 1.0
        for v in chosen:
            miss *= 1.0 - min(0.95, v["conf"])
        conf = (1.0 - miss) * (0.7 if conflict else 1.0)
        step = max(v["step"] for v in chosen)
        tl = [v["target_level"] for v in chosen if v["target_level"] is not None]
        anchors = [v["anchor"] for v in chosen if v["anchor"]]
        poles = [v["pole"] for v in chosen if v["pole"]]
        bounds = [v["bound"] for v in chosen if v["bound"] is not None]
        desired[dim] = {
            "direction": direction,
            "strength": "strong" if step >= 0.25 else "slight" if step <= 0.08 else "moderate",
            "step": step,
            "confidence": round(min(0.97, conf), 3),
            "target_level": round(sum(tl) / len(tl), 3) if tl else None,
            "preferred_pole": poles[0] if poles else None,
            "bound": bounds[0] if bounds else None,
            "anchor_loaf": anchors[0] if anchors else None,
            "implied": all(v["rule"].startswith("implied") for v in chosen),
            "conflicting_votes": conflict,
            "votes": {k: round(w, 3) for k, w in sorted(weights.items())},
            "poles": sorted({v["pole"] for v in pool if v["pole"]}),
            "evidence": [v["evidence"] for v in chosen],
        }
    # texture and density are two views of the crumb; keep both but warn on contradictions
    if "texture" in desired and "density" in desired:
        t, d = desired["texture"], desired["density"]
        if t["direction"] == d["direction"] and t["direction"] in ("increase", "decrease"):
            notes.append("texture and density desires point the same way (unusual); texture used for crumb target")

    # primary dimension
    order = list(DIMENSIONS)
    active = [(d, v) for d, v in desired.items() if v["direction"] in ("increase", "decrease")]
    primary = None
    if active:
        primary = max(active, key=lambda dv: (not dv[1]["implied"], round(dv[1]["confidence"], 2), -order.index(dv[0])))[0]

    any_loaf_data = any(loaves_out[l]["attributes"] for l in LOAVES)
    if not any_loaf_data and unattr_out:
        notes.append("No loaf was named; descriptions are kept as 'unattributed' (treated as describing both loaves).")
    if not desired and not preferred:
        notes.append("No explicit desired direction found; the policy will choose an informative exploratory experiment.")
    if not records and not preferred:
        notes.append("No sensory vocabulary recognized; interpretation is empty (low confidence).")

    all_conf = [a["confidence"] for l in LOAVES for a in loaves_out[l]["attributes"].values()]
    all_conf += [v["confidence"] for v in desired.values()]
    all_conf += [a["confidence"] for a in unattr_out.values()]
    overall = round(sum(all_conf) / len(all_conf), 3) if all_conf else 0.0

    return {
        "method": PARSER_ID,
        "provenance": "judge_feedback",
        "raw_text": text,
        "loaves": loaves_out,
        "unattributed": {"attributes": unattr_out, "aroma_tags": sorted(tags["unattributed"])},
        "comparisons": comparisons,
        "desired": desired,
        "desired_aroma_tags": sorted(tags["desired"]),
        "desired_summary": summarize_desired(desired, preferred),
        "structured_preference": structured_preference(desired),
        "primary_dimension": primary,
        "preferred_loaf": preferred,
        "preference_evidence": [p["evidence"] for p in preferences + dislikes],
        "flags": sorted(flags),
        "overall_confidence": overall,
        "notes": notes,
        "scales": {
            "sourness": "0 very mild .. 1 very sour/sharp",
            "texture": "0 tight/gummy .. 1 open/airy/fluffy",
            "density": "0 light .. 1 dense/heavy",
            "aroma": "0 faint/bland .. 1 strong/complex",
            "flavor_intensity": "0 flat .. 1 strong/rich",
        },
    }


def LEXICON_POLE(r: Record, dim: str) -> float:
    """Base lexicon value of the term behind a record (before modifiers)."""
    term = LEXICON.get(r.term)
    if term and dim in term.dims:
        return term.dims[dim]
    return r.dims.get(dim, 0.5)


DIM_WORDS = {
    "sourness": ("more sour", "milder", "sourness"),
    "texture": ("more open/airy crumb", "tighter crumb", "crumb openness"),
    "density": ("denser", "lighter", "density"),
    "aroma": ("stronger aroma", "subtler aroma", "aroma"),
    "flavor_intensity": ("stronger flavor", "milder flavor", "flavor intensity"),
}


def summarize_desired(desired: dict[str, Any], preferred: str | None = None) -> str:
    parts = []
    for dim in DIMENSIONS:
        v = desired.get(dim)
        if not v:
            continue
        up, down, noun = DIM_WORDS[dim]
        if v["direction"] == "increase":
            parts.append(f"{up} ({v['strength']})")
        elif v["direction"] == "decrease":
            parts.append(f"{down} ({v['strength']})")
        else:
            pole = v.get("preferred_pole")
            if dim == "density":
                lower = pole == "low" and "decrease" in (v.get("votes") or {})
                parts.append("lower density" if lower else "keep density low" if pole == "low" else "keep density")
            elif dim == "sourness" and pole == "low":
                parts.append("don't let it get too sour")
            else:
                parts.append(f"keep {noun}" + (" high" if pole == "high" else " low" if pole == "low" else ""))
    if preferred:
        parts.append(f"preferred Loaf {preferred}")
    if not parts:
        return "No explicit preference stated."
    s = "; ".join(parts)
    return s[0].upper() + s[1:] + "."


def structured_preference(desired: dict[str, Any]) -> list[str]:
    """Plain preference list in the PLAN_MASTER §14 style ("More sourness", "Lower density", ...).

    Uses every vote (not only the winning direction), so "Loaf B is too dense ... still fluffy"
    yields both "Lower density" and "Preserve good rise".
    """
    def votes(dim):
        return (desired.get(dim) or {}).get("votes") or {}

    def poles(dim):
        return set((desired.get(dim) or {}).get("poles") or [])

    out = []
    sv = votes("sourness")
    if "maintain" in sv and "high" in poles("sourness") and sv["maintain"] >= max(sv.get("increase", 0),
                                                                              sv.get("decrease", 0)):
        out.append("Keep the current sourness")
    elif "increase" in sv and sv.get("increase", 0) >= sv.get("decrease", 0):
        out.append("More sourness")
    elif "decrease" in sv:
        out.append("Less sourness")
    elif "sourness" in desired and "low" in poles("sourness"):
        out.append("Do not increase sourness")
    if "decrease" in votes("density") or "increase" in votes("texture"):
        out.append("Lower density")
    elif "increase" in votes("density") or "decrease" in votes("texture"):
        out.append("Denser / chewier crumb")
    if ("maintain" in votes("texture") and "high" in poles("texture")) or (
            "maintain" in votes("density") and "low" in poles("density")):
        out.append("Preserve good rise (airy/fluffy crumb)")
    av = votes("aroma")
    if "increase" in av:
        out.append("Stronger aroma")
    elif "decrease" in av:
        out.append("Subtler aroma")
    fv = votes("flavor_intensity")
    if "increase" in fv:
        out.append("More flavor")
    elif "decrease" in fv:
        out.append("Milder overall flavor")
    return out
