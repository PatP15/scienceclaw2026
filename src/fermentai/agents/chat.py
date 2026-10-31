"""Conversational front door: a Sous Chef chats with the Guest, then hands off to the Head Chef.

Kitchen-brigade language: Guest = judge, Sous Chef A/B = scientist_A/B, Head Chef = the
orchestrating pipeline (``run_taste_feedback_cycle``), line cooks = candidate simulations.

    chat_turn(session_id, text, context=None, mode="offline") -> dict
    chat_reset(session_id, context=None) -> dict

Per lab: ``context["sous_chef"]`` is the station the Guest is standing at (A or B); that Sous Chef
is responsible for ITS loaf. A deterministic state machine (never the LLM) decides what happens next:

    greeting -> collecting -> confirming -> handoff -> choosing -> done

1. greeting names the station and asks about this loaf;
2. up to 3 focused follow-ups about THIS loaf (sourness, crumb, aroma/crust/anything else),
   skipping what is already answered;
3. "Did you also taste another experiment — Station X's loaf? Want to compare?" -> if yes, the other
   loaf's impression and an explicit "which did you prefer, and why?"; if no, skipped;
4. the wish ("What would you want the next loaf to be?");
5. read-back + confirm; 6. hand-off to the Head Chef with a consolidated note that attributes each
   loaf to its letter. Single-loaf tastings hand off too.
7. the Head Chef's menu: the hand-off reply lists the top-3 distinct ``suggestions`` for this station and asks
   "Which should we try? Reply 1, 2 or 3." (the hand-off turn itself reports stage "handoff"). Any later reply
   is read deterministically as a choice ("2", "the second", a title fragment) -> stage "done" and the turn's
   result carries ``chosen_suggestion`` (the operator still applies it); anything else -> stage "choosing" with a
   gentle re-ask. The LLM never picks or words the menu/choice.

Every guest message is folded into the note and re-parsed with ``sensory.parse_feedback``; bare
answers are attributed to the loaf being discussed ("this one" -> this station, "the other one" ->
the other station). "That's all" finishes the current part; "go ahead"/"proceed" hands off now.
* ``mode="live"``: the LLM only *words* the Sous Chef's message from the structured state (strict
  JSON schema); any failure falls back to the offline templates. The hand-off cycle runs with the
  same ``mode`` (and has its own fallback).

Sessions live in memory and are appended (snapshot per turn, append-only) to
``data/experiments/chat_sessions.jsonl`` (``context["chat_sessions_path"]`` or env
``FERMENTAI_CHAT_SESSIONS``). On hand-off a ``sensory_feedback`` finding tagged "via chat" is
appended to the findings log.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from . import schedules as S
from .communication import REPO_ROOT, FindingsLog, new_finding, utc_now_iso
from .llm_adapter import LLMAdapter
from .pipeline import run_taste_feedback_cycle
from .sensory import DESIRE_RE, find_loaf_refs, parse_feedback

DEFAULT_SESSIONS_PATH = REPO_ROOT / "data" / "experiments" / "chat_sessions.jsonl"
MAX_OWN_FOLLOWUPS = 3  # focused follow-ups about this station's loaf
STAGES = ("greeting", "collecting", "confirming", "handoff", "choosing", "done")

AFFIRM_RE = re.compile(r"^\s*(?:yes|yeah|yep|yup|ya|correct|right|exactly|that'?s (?:right|correct|it)|sounds (?:good|right)|"
                       r"spot on|perfect|ok(?:ay)?|sure|absolutely|go (?:ahead|for it)|do it|confirm(?:ed)?)\b", re.IGNORECASE)
NEGATE_RE = re.compile(r"^\s*(?:no\b|nope|not (?:quite|really|exactly)|actually|wrong|that'?s not|incorrect)", re.IGNORECASE)
# strong "proceed" -> hand off now; soft "done" -> finish the current part of the conversation
PROCEED_RE = re.compile(r"\b(?:go ahead|proceed|send it|hand (?:it )?(?:off|over)|let'?s (?:go|do it|proceed)|i'?m done|"
                        r"make (?:the|a) plan|plan it|take it to the head chef)\b", re.IGNORECASE)
SOFT_DONE_RE = re.compile(r"\b(?:that'?s (?:all|it|everything)|nothing else|that'?s enough|nothing more)\b", re.IGNORECASE)
UNKNOWN_RE = re.compile(r"\b(?:i )?(?:don'?t|do not) (?:really )?know\b|\bnot sure\b|\bno idea\b|\bdunno\b|"
                        r"\bcan'?t (?:tell|say|remember)\b|\bhard to say\b|\bno clue\b", re.IGNORECASE)
SAME_RE = re.compile(r"\b(?:the )?same\b|\bno difference\b|\bidentical\b|\bcouldn'?t tell (?:them )?apart\b|\bindistinguishable\b",
                     re.IGNORECASE)
NO_CHANGE_RE = re.compile(r"\bno (?:preference|changes?)\b|\bkeep (?:it|them|everything) (?:the same|as (?:is|it is))\b|"
                          r"\bnothing (?:to change|really)\b|\bdon'?t change\b", re.IGNORECASE)
RESET_RE = re.compile(r"\b(?:start over|restart|new tasting|reset|start again)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# small text helpers
# ---------------------------------------------------------------------------
def _pick(options: list[str], *keys: Any) -> str:
    h = int(hashlib.sha256("|".join(map(str, keys)).encode()).hexdigest(), 16)
    return options[h % len(options)]


def _has_loaf_ref(text: str) -> bool:
    norm = text.replace("’", "'")
    return any(r.loaves or r.kind == "other" for r in find_loaf_refs(norm, norm.lower(), None))


def _sentence(text: str) -> str:
    t = text.strip()
    return t if t.endswith((".", "!", "?")) else t + "."


def _crumb_word(attrs: dict[str, Any]) -> str | None:
    vals = []
    if "texture" in attrs:
        vals.append(attrs["texture"]["score"])
    if "density" in attrs:
        vals.append(1 - attrs["density"]["score"])
    if not vals:
        return None
    o = sum(vals) / len(vals)
    return ("airy" if o >= 0.75 else "fairly open" if o >= 0.55 else "a bit dense" if o >= 0.3 else "dense")


def _sour_word(attrs: dict[str, Any]) -> str | None:
    s = attrs.get("sourness")
    if not s:
        return None
    v = s["score"]
    return ("very mild" if v < 0.2 else "mild" if v < 0.38 else "gently tangy" if v < 0.55 else "tangy" if v < 0.72
            else "sour" if v < 0.86 else "very sour")


def _loaf_phrase(attrs: dict[str, Any], tags: list[str]) -> str | None:
    bits = [w for w in (_sour_word(attrs), _crumb_word(attrs)) if w]
    good_tags = [t for t in tags if t in ("nutty", "fruity", "toasty", "malty", "yeasty", "buttery", "earthy", "acetic")]
    if good_tags:
        bits.append("with " + ("vinegary" if good_tags[0] == "acetic" else good_tags[0]) + " notes")
    elif "aroma" in attrs:
        a = attrs["aroma"]["score"]
        bits.append("with a strong aroma" if a >= 0.65 else "with a faint aroma" if a < 0.35 else "nicely aromatic")
    if not bits:
        return None
    if len(bits) == 1:
        return bits[0]
    return ", ".join(bits[:-1]) + " and " + bits[-1]


_SOUR_WORDS = ("very mild", "mild", "neutral", "balanced", "slightly tangy", "slightly sour", "tangy", "sour",
               "very sour", "sharp", "very sharp")
_SOUR_CACHE: dict[str, float] = {}


def _sour_word_scores() -> dict[str, float]:
    """Score the parser gives each restating word (computed from the parser itself, cached)."""
    if not _SOUR_CACHE:
        for w in _SOUR_WORDS:
            a = parse_feedback(f"Loaf A: {w}.")["loaves"]["A"]["attributes"].get("sourness")
            if a:
                _SOUR_CACHE[w] = a["score"]
    return _SOUR_CACHE


def _restate(attrs: dict[str, Any]) -> list[str]:
    """Parser-friendly words that reproduce a loaf's attributes (used for "same as the other one")."""
    words = []
    if "sourness" in attrs:
        v = attrs["sourness"]["score"]
        table = _sour_word_scores()
        words.append(min(table, key=lambda w: (abs(table[w] - v), w)))
    crumb = _crumb_word(attrs)
    if crumb:
        words.append({"airy": "airy", "fairly open": "soft", "a bit dense": "slightly dense", "dense": "dense"}[crumb])
    if "aroma" in attrs:
        words.append("aromatic" if attrs["aroma"]["score"] >= 0.5 else "with a faint aroma")
    return words


PREF_PHRASES = {
    "More sourness": "more sour", "Less sourness": "milder", "Do not increase sourness": "no sourer than now",
    "Lower density": "less dense", "Denser / chewier crumb": "chewier",
    "Preserve good rise (airy/fluffy crumb)": "keeping the airy rise", "Keep the current sourness": "just as tangy", "Stronger aroma": "more aromatic",
    "Subtler aroma": "a subtler aroma", "More flavor": "more flavorful", "Milder overall flavor": "milder overall",
}


def _second_person(text: str) -> str:
    """Echo the guest's reason back to them ("I like the tang" -> "you like the tang")."""
    for pat, rep in ((r"\bI'm\b", "you're"), (r"\bI've\b", "you've"), (r"\bI\b", "you"), (r"\bmy\b", "your"),
                     (r"\bme\b", "you"), (r"\bmyself\b", "yourself")):
        text = re.sub(pat, rep, text)
    return text


def _join(items: list[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


# ---------------------------------------------------------------------------
# session store
# ---------------------------------------------------------------------------
def _sessions_path(context: dict[str, Any] | None) -> Path:
    ctx = context or {}
    if ctx.get("chat_sessions_path"):
        return Path(ctx["chat_sessions_path"])
    env = os.environ.get("FERMENTAI_CHAT_SESSIONS")
    return Path(env) if env else DEFAULT_SESSIONS_PATH


def _new_session(session_id: str, sous_chef: str) -> dict[str, Any]:
    return {
        "session_id": session_id, "sous_chef": sous_chef, "station": sous_chef,
        "other": "B" if sous_chef == "A" else "A", "stage": "greeting", "turn": 0,
        "transcript": [], "notes": [], "last_topic": "own_open", "asked": [], "own_followups": 0,
        "own_done": False, "compare": None, "preferred": None, "preference_basis": None, "preference_reason": None,
        "flags": {"unknown_A": False, "unknown_B": False, "wish_unknown": False, "wish_no_change": False,
                  "tie": False, "same_as": None},
        "handoff_result": None, "trial_id": None, "suggestions": [], "chosen_suggestion": None, "off_topic_streak": 0, "chat_mode_label": None,
    }


class SousChefChat:
    """Chat sessions backed by memory + an append-only JSONL snapshot log."""

    def __init__(self, path: str | os.PathLike | None = None):
        self.path = Path(path) if path is not None else None
        self._sessions: dict[str, dict[str, Any]] = {}
        self._contexts: dict[str, dict[str, Any]] = {}  # in-memory only (may hold non-serializable objects)

    # -- persistence -----------------------------------------------------------------
    def _write(self, record: dict[str, Any]) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        with open(self.path, "a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.write(line)
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _load(self, session_id: str) -> dict[str, Any] | None:
        if self.path is None or not self.path.exists():
            return None
        latest = None
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("session_id") != session_id:
                    continue
                latest = None if rec.get("reset") else rec.get("session")
        return latest

    def clear_memory(self) -> None:
        """Drop in-memory sessions (state is reloaded from the JSONL log on next use)."""
        self._sessions.clear()

    def get(self, session_id: str) -> dict[str, Any] | None:
        return self._sessions.get(session_id) or self._load(session_id)

    def reset(self, session_id: str) -> dict[str, Any]:
        self._sessions.pop(session_id, None)
        self._contexts.pop(session_id, None)
        self._write({"session_id": session_id, "reset": True, "ts": utc_now_iso()})
        return {"session_id": session_id, "reset": True}

    # -- main entry ------------------------------------------------------------------
    def turn(self, session_id: str, text: str, context: dict | None = None, mode: str = "offline") -> dict[str, Any]:
        ctx = dict(self._contexts.get(session_id, {}))
        ctx.update(context or {})
        self._contexts[session_id] = ctx
        now = ctx.get("now_utc") or utc_now_iso()
        sous = str(ctx.get("sous_chef") or "A").replace("scientist_", "").replace("Sous Chef", "").strip().upper()
        sous = sous if sous in ("A", "B") else "A"
        sess = self._sessions.get(session_id) or self._load(session_id) or _new_session(session_id, sous)
        self._sessions[session_id] = sess
        text = (text or "").strip()
        sess["turn"] += 1
        if text and RESET_RE.search(text):
            self.reset(session_id)
            sess = _new_session(session_id, sous)
            self._sessions[session_id] = sess
            sess["turn"] = 1
            text = ""
        if text:
            sess["transcript"].append({"role": "guest", "text": text, "ts": now})
        action = self._advance(sess, text, ctx)
        reply, label = self._word(sess, action, ctx, mode, text)
        if action["kind"] in ("chosen", "rechoose") and sess.get("handoff_result"):
            label = sess["handoff_result"].get("reasoning_mode_label", label)
        handoff = None
        if action["kind"] == "handoff":
            handoff = self._handoff(sess, ctx, mode, now)
            narration = self._narrate(sess, handoff)
            sess["transcript"].append({"role": "sous_chef", "text": reply, "ts": now})
            sess["transcript"].append({"role": "head_chef", "text": narration, "ts": now})
            reply = f"{reply}\n\n{narration}"
            label = handoff.get("reasoning_mode_label", label)
        else:
            sess["transcript"].append({"role": "sous_chef", "text": reply, "ts": now})
        out = {
            "session_id": session_id,
            "reply": reply,
            "stage": sess["stage"],
            "understood": self._understood(sess),
            "questions_remaining": self._questions_remaining(sess, self._parse(sess, ctx)),
            "transcript": list(sess["transcript"]),
            "handoff_result": sess["handoff_result"] if sess["stage"] in ("handoff", "choosing", "done") else None,
            "suggestions": list(sess.get("suggestions") or []),
            "chosen_suggestion": sess["chosen_suggestion"] if action["kind"] == "chosen" else None,
            "reasoning_mode_label": label,
            "chat_mode_label": sess.get("chat_mode_label"),
            "sous_chef": f"Sous Chef {sess['sous_chef']}",
        }
        self._write({"session_id": session_id, "ts": now, "session": sess})
        return out

    # -- state machine ---------------------------------------------------------------
    def _note(self, sess: dict[str, Any]) -> str:
        return " ".join(_sentence(l) for l in sess["notes"])

    def _parse(self, sess: dict[str, Any], ctx: dict[str, Any] | None = None) -> dict[str, Any]:
        sp = (ctx or {}).get("setpoints")
        return parse_feedback(self._note(sess), sp if isinstance(sp, dict) else None)

    def _coverage(self, sess: dict[str, Any], interp: dict[str, Any]) -> dict[str, bool]:
        f = sess["flags"]
        cov = {l: bool(interp["loaves"][l]["attributes"]) or f[f"unknown_{l}"] for l in ("A", "B")}
        explicit = any(not v.get("implied") for v in interp["desired"].values())
        cov["wish"] = explicit or f["wish_unknown"] or f["wish_no_change"]
        return cov

    def _rewrite(self, sess: dict[str, Any], text: str, topic: str) -> str:
        """Map station language onto loaf letters and attribute bare answers to the asked loaf."""
        own, other = sess["station"], sess["other"]
        t = re.sub(r"\bstation\s+([ab])(?:'s)?(?:\s+loaf)?\b", lambda m: f"Loaf {m.group(1).upper()}", text, flags=re.IGNORECASE)
        t = re.sub(r"\bthe other (?:one|loaf|bread|experiment|station(?:'s loaf)?)\b|\bthe other\b(?! \w+ (?:was|is))",
                   f"Loaf {other}", t, flags=re.IGNORECASE)
        t = re.sub(r"\b(?:this|my|your|our) (?:one|loaf|bread|station(?:'s loaf)?)\b|\bthe one here\b|\bthis station's loaf\b",
                   f"Loaf {own}", t, flags=re.IGNORECASE)
        if topic.startswith("own_") and not _has_loaf_ref(t):
            return f"Loaf {own}: {t}"
        if topic == "other_open" and not _has_loaf_ref(t):
            return f"Loaf {other}: {t}"
        if topic == "wish" and not DESIRE_RE.search(t.lower().replace("’", "'")):
            return "I'd like " + t[0].lower() + t[1:]
        return t

    def _changed(self, sess, line, ctx) -> bool:
        def sig(p):
            return ({l: p["loaves"][l]["attributes"] for l in ("A", "B")}, p["unattributed"]["attributes"],
                    p["desired"], p["preferred_loaf"])
        before = self._parse(sess, ctx)
        after = parse_feedback(" ".join(_sentence(l) for l in sess["notes"] + [line]))
        return sig(after) != sig(before)

    def _absorb(self, sess: dict[str, Any], text: str, ctx: dict[str, Any]) -> dict[str, Any]:
        """Fold a guest message into the tasting note. Returns what kind of message it was."""
        topic = sess["last_topic"]
        f = sess["flags"]
        own, other = sess["station"], sess["other"]
        info: dict[str, Any] = {"unknown": False, "same": False, "off_topic": False, "new_info": False,
                                "no_change": False, "soft_done": False, "compare": None}
        line = self._rewrite(sess, text, topic)
        alone = parse_feedback(line)
        no_terms = not (alone["loaves"]["A"]["attributes"] or alone["loaves"]["B"]["attributes"]
                        or alone["unattributed"]["attributes"] or alone["desired"])
        # --- the comparison offer --------------------------------------------------------
        if topic == "compare_offer":
            if not no_terms and self._changed(sess, line, ctx):
                if not _has_loaf_ref(line):
                    line = f"Loaf {other}: {text}"
                sess["notes"].append(line)
                sess["compare"] = "yes"
                info.update(new_info=True, compare="yes")
                pl = parse_feedback(line).get("preferred_loaf")
                if pl and not sess["preferred"]:
                    sess["preferred"], sess["preference_basis"] = pl, "stated while comparing"
            elif NEGATE_RE.search(text) or re.search(r"\b(?:didn'?t|did not|haven'?t|only (?:this|tried|tasted)|no)\b",
                                                     text, re.IGNORECASE) or UNKNOWN_RE.search(text):
                sess["compare"] = "no"
                info["compare"] = "no"
            elif AFFIRM_RE.search(text) or re.search(r"\b(?:i did|tried it|tasted it|yes)\b", text, re.IGNORECASE):
                sess["compare"] = "yes"
                info["compare"] = "yes"
            else:
                info["off_topic"] = True
            return info
        # --- which did you prefer? ----------------------------------------------------------
        if topic == "compare_pref":
            if re.search(r"\b(?:neither|both|no preference|equally|tie|same|can'?t (?:decide|choose))\b", text, re.IGNORECASE) \
                    and not re.search(r"\bprefer(?:red)? loaf\b", line, re.IGNORECASE):
                f["tie"] = True
                info["same"] = True
                if not no_terms:
                    sess["notes"].append(line)
                return info
            if UNKNOWN_RE.search(text) and no_terms:
                f["tie"] = True
                info["unknown"] = True
                return info
            refs = {l for r in find_loaf_refs(line, line.lower(), None) if r.kind != "than" for l in r.loaves}
            if len(refs) == 1:
                x = refs.pop()
                sess["preferred"] = x
                sess["preference_basis"] = "explicit comparison"
                why = re.search(r"\b(?:because|since|as|cause|'cause)\b\s*(.+)$", text, re.IGNORECASE)
                sess["preference_reason"] = _second_person(why.group(1).strip().rstrip(".")) if why else None
                sess["notes"].append(f"I preferred Loaf {x}")
                if not no_terms:
                    sess["notes"].append(line)
                info["new_info"] = True
                return info
            if not no_terms and self._changed(sess, line, ctx):
                sess["notes"].append(line)
                info["new_info"] = True
                return info
            info["off_topic"] = True
            return info
        # --- generic answers --------------------------------------------------------------
        if UNKNOWN_RE.search(text) and no_terms:
            info["unknown"] = True
            if topic == "own_open":
                pass  # ask the specific follow-ups instead
            elif topic == "other_open":
                f[f"unknown_{other}"] = True
            elif topic == "wish":
                f["wish_unknown"] = True
            return info
        if topic == "wish" and NO_CHANGE_RE.search(text):
            f["wish_no_change"] = True
            info["no_change"] = True
            return info
        if SAME_RE.search(text) and no_terms and topic != "wish":
            info["same"] = True
            interp = self._parse(sess, ctx)
            src_loaf = own if topic == "other_open" else other
            dst_loaf = other if topic == "other_open" else own
            src = interp["loaves"][src_loaf]["attributes"]
            if src:
                sess["notes"].append(f"Loaf {dst_loaf}: {', '.join(_restate(src))}")  # "same as Loaf {src_loaf}"
                f["same_as"] = {dst_loaf: src_loaf}
                if topic == "other_open":
                    f["tie"] = f["tie"] or False
            elif topic == "other_open":
                f[f"unknown_{other}"] = True
            return info
        if topic == "own_more" and (SOFT_DONE_RE.search(text) or re.fullmatch(r"\s*(?:no|nope|nah|not really)\W*", text, re.IGNORECASE)):
            info["soft_done"] = True
            return info
        if self._changed(sess, line, ctx):
            sess["notes"].append(line)
            info["new_info"] = True
            if topic == "other_open" and alone.get("preferred_loaf") and not sess["preferred"]:
                sess["preferred"], sess["preference_basis"] = alone["preferred_loaf"], "stated while comparing"
            return info
        if SOFT_DONE_RE.search(text):
            info["soft_done"] = True
            return info
        info["off_topic"] = not (AFFIRM_RE.search(text) or NEGATE_RE.search(text))
        return info

    def _plan(self, sess: dict[str, Any], interp: dict[str, Any]) -> str:
        """Next topic to ask about, or 'confirm'. Each topic is asked at most once."""
        own, other = sess["station"], sess["other"]
        asked = set(sess["asked"])
        cov = self._coverage(sess, interp)
        attrs = interp["loaves"][own]["attributes"]
        one_shot = cov[other] and cov["wish"]
        if not sess["own_done"]:
            if "own_open" not in asked:
                return "own_open"
            missing = []
            if "sourness" not in attrs:
                missing.append("own_sourness")
            if "texture" not in attrs and "density" not in attrs:
                missing.append("own_crumb")
            for t in missing:
                if t not in asked and sess["own_followups"] < MAX_OWN_FOLLOWUPS and not one_shot:
                    return t
            if "own_more" not in asked and sess["own_followups"] < MAX_OWN_FOLLOWUPS and not one_shot:
                return "own_more"
            sess["own_done"] = True
        if cov[other] and sess["compare"] is None:
            sess["compare"] = "yes"
            if interp.get("preferred_loaf") and not sess["preferred"]:
                sess["preferred"], sess["preference_basis"] = interp["preferred_loaf"], "stated while comparing"
        if sess["compare"] is None and "compare_offer" not in asked:
            return "compare_offer"
        if sess["compare"] == "yes":
            if not cov[other] and "other_open" not in asked:
                return "other_open"
            if not sess["preferred"] and not sess["flags"]["tie"] and "compare_pref" not in asked:
                return "compare_pref"
        if not cov["wish"] and "wish" not in asked:
            return "wish"
        return "confirm"

    def _questions_remaining(self, sess: dict[str, Any], interp: dict[str, Any]) -> int:
        if sess["stage"] in ("confirming", "handoff", "choosing", "done"):
            return 0
        own, other = sess["station"], sess["other"]
        asked = set(sess["asked"])
        cov = self._coverage(sess, interp)
        attrs = interp["loaves"][own]["attributes"]
        n = 0
        if not sess["own_done"]:
            own_q = [t for t, miss in (("own_sourness", "sourness" not in attrs),
                                       ("own_crumb", "texture" not in attrs and "density" not in attrs),
                                       ("own_more", True)) if miss and t not in asked]
            n += min(len(own_q), MAX_OWN_FOLLOWUPS - sess["own_followups"])
        if sess["compare"] is None and "compare_offer" not in asked and not cov[other]:
            n += 1
        if sess["compare"] in (None, "yes"):
            n += int(not cov[other] and "other_open" not in asked)
            n += int(not (sess["preferred"] or sess["flags"]["tie"]) and "compare_pref" not in asked)
        n += int(not cov["wish"] and "wish" not in asked)
        return n

    def _advance(self, sess: dict[str, Any], text: str, ctx: dict[str, Any]) -> dict[str, Any]:
        stage = sess["stage"]
        if stage in ("handoff", "choosing") and sess.get("suggestions"):
            picked = parse_choice(text, sess["suggestions"]) if text else None
            if picked is None:
                sess["stage"] = "choosing"
                return {"kind": "rechoose"}
            sess["chosen_suggestion"] = {**picked, "station": sess["sous_chef"], "session_id": sess["session_id"],
                                         "cycle_id": (sess.get("handoff_result") or {}).get("cycle_id"),
                                         "trial_id": sess.get("trial_id"), "status": "proposed",
                                         "requires_human_confirmation": True}
            sess["stage"] = "done"
            return {"kind": "chosen"}
        if stage in ("handoff", "choosing", "done"):
            sess["stage"] = "done"
            return {"kind": "done"}
        if not text:
            if stage == "greeting":
                sess["last_topic"] = "own_open"
                if "own_open" not in sess["asked"]:
                    sess["asked"].append("own_open")
                return {"kind": "greet"}
            return {"kind": "reask", "topic": sess["last_topic"], "info": {}}
        affirm, negate = bool(AFFIRM_RE.search(text)), bool(NEGATE_RE.search(text))
        proceed = bool(PROCEED_RE.search(text))
        if stage == "confirming":
            if (affirm or proceed or SOFT_DONE_RE.search(text)) and not negate:
                return self._to_handoff(sess, "confirmed", {})
            if negate:
                info = self._absorb_correction(sess, text, ctx)
                if info.get("new_info"):
                    return self._to_confirm(sess, info, corrected=True)
                sess["stage"] = "collecting"
                sess["last_topic"] = "correction"
                return {"kind": "ask_correction", "info": info}
            info = self._absorb_correction(sess, text, ctx)
            if info.get("new_info"):
                return self._to_confirm(sess, info, added=True)
            return {"kind": "reconfirm", "info": info}
        if stage == "greeting" and "own_open" not in sess["asked"]:
            sess["asked"].append("own_open")
        sess["stage"] = "collecting"
        if sess["last_topic"] == "correction":
            info = self._absorb_correction(sess, text, ctx)
            if info.get("new_info"):
                return self._to_confirm(sess, info, corrected=True)
        if proceed:
            info = self._absorb(sess, text, ctx)
            return self._to_handoff(sess, "guest asked to proceed", info)
        info = self._absorb(sess, text, ctx)
        if info.get("off_topic"):
            sess["off_topic_streak"] += 1
            return {"kind": "redirect", "topic": sess["last_topic"], "info": info}
        sess["off_topic_streak"] = 0
        if info.get("soft_done") and sess["last_topic"].startswith("own_"):
            sess["own_done"] = True
        nxt = self._plan(sess, self._parse(sess, ctx))
        if nxt == "confirm":
            return self._to_confirm(sess, info)
        sess["asked"].append(nxt)
        if nxt.startswith("own_") and nxt != "own_open":
            sess["own_followups"] += 1
        sess["last_topic"] = nxt
        return {"kind": "ask", "topic": nxt, "info": info}

    def _absorb_correction(self, sess, text, ctx):
        """Corrections may concern either loaf; use the text's own loaf references (default: this station)."""
        body = re.sub(r"^\s*(?:no|nope|not quite|not really|actually|wrong|incorrect)\b[,.!]?\s*(?:actually\b[,]?\s*)?",
                      "", text, flags=re.IGNORECASE)
        if not body.strip():
            return {"new_info": False}
        line = self._rewrite(sess, body, "correction")
        if not _has_loaf_ref(line) and not DESIRE_RE.search(line.lower()):
            line = f"Loaf {sess['station']}: {line}"
        if self._changed(sess, line, ctx):
            sess["notes"].append(line)
            return {"new_info": True}
        return {"new_info": False}

    def _to_confirm(self, sess, info, corrected=False, added=False):
        sess["stage"] = "confirming"
        sess["last_topic"] = "confirm"
        return {"kind": "confirm", "info": info, "corrected": corrected, "added": added}

    def _to_handoff(self, sess, reason, info):
        sess["stage"] = "handoff"
        return {"kind": "handoff", "reason": reason, "info": info}

    # -- wording ---------------------------------------------------------------------
    def _preferred(self, sess: dict[str, Any], interp: dict[str, Any]) -> tuple[str | None, str | None]:
        if sess.get("preferred"):
            return sess["preferred"], sess.get("preference_basis") or "explicit comparison"
        if interp.get("preferred_loaf"):
            return interp["preferred_loaf"], "stated in the tasting notes"
        if sess["compare"] != "yes" and sess["flags"]["wish_no_change"]:
            return sess["station"], "inferred: guest would keep this loaf as it is"
        return None, ("single loaf tasted; preference expressed only through the wish" if sess["compare"] != "yes"
                      else ("no preference between the loaves" if sess["flags"]["tie"] else None))

    def _reflection(self, sess: dict[str, Any], interp: dict[str, Any]) -> str:
        f = sess["flags"]
        own, other = sess["station"], sess["other"]
        parts = []
        ph = _loaf_phrase(interp["loaves"][own]["attributes"], interp["loaves"][own]["aroma_tags"])
        parts.append(f"Loaf {own} here was {ph}" if ph else f"you weren't sure how to describe Loaf {own}")
        if sess["compare"] == "yes":
            oph = _loaf_phrase(interp["loaves"][other]["attributes"], interp["loaves"][other]["aroma_tags"])
            parts.append(f"Station {other}'s loaf was {oph}" if oph else f"you also tried Station {other}'s loaf")
        pref, _basis = self._preferred(sess, interp)
        if pref and sess["compare"] == "yes":
            why = sess.get("preference_reason")
            parts.append(f"you preferred Loaf {pref}" + (f" because {why}" if why else ""))
        elif f["tie"] and sess["compare"] == "yes":
            parts.append("you liked them about equally")
        prefs = [PREF_PHRASES.get(p, p.lower()) for p in interp.get("structured_preference") or []]
        if prefs:
            parts.append(f"you'd like the next one {_join(prefs)}")
        elif f["wish_no_change"]:
            parts.append("you'd keep it as it is")
        elif f["wish_unknown"]:
            parts.append("you're open on what to change")
        return _join(parts)

    def _question(self, sess: dict[str, Any], topic: str, interp: dict[str, Any]) -> str:
        sid, turn = sess["session_id"], sess["turn"]
        own, other = sess["station"], sess["other"]
        q = {
            "own_open": [f"You've just tasted Loaf {own}, our loaf here at Station {own}. How was it?",
                         f"What did you think of Loaf {own} — the one from this station?"],
            "own_sourness": [f"How sour was Loaf {own} — mild, gently tangy, or properly sour?",
                             "And the sourness — would you call it mild, tangy or really sour?"],
            "own_crumb": ["How was the crumb — light and airy, or on the dense side?",
                          f"And the texture of Loaf {own}: fluffy and open, or a bit tight and dense?"],
            "own_more": ["Anything about the aroma or the crust — or anything else that stood out?",
                         "Did the smell or the crust catch your attention? Anything else worth noting?"],
            "compare_offer": [f"Did you also taste another experiment — Station {other}'s loaf? Want to compare?",
                              f"Have you tried Station {other}'s loaf too? Happy to compare the two if you did."],
            "other_open": [f"How was Loaf {other} from Station {other} — sourness, crumb, anything that stood out?",
                           f"Tell me about Station {other}'s loaf: how sour, and how was the crumb?"],
            "compare_pref": ["Which did you prefer, and why?",
                             f"So between Loaf {own} and Loaf {other} — which did you prefer, and why?"],
            "wish": ["What would you want the next loaf to be?",
                     "If we bake again, what would you want the next loaf to be like?"],
            "correction": ["Sorry about that — what did I get wrong?"],
        }.get(topic) or [f"Tell me about Loaf {own} — how did it taste?"]
        return _pick(q, sid, turn, topic)

    def _ack(self, sess: dict[str, Any], action: dict[str, Any], interp: dict[str, Any]) -> str:
        info = action.get("info") or {}
        sid, turn = sess["session_id"], sess["turn"]
        if info.get("compare") == "no":
            return _pick([f"No problem — we'll stick with Loaf {sess['station']}.", "That's fine, one loaf is plenty."],
                         sid, turn, "cmpno")
        if info.get("compare") == "yes" and not info.get("new_info"):
            return "Great."
        if info.get("unknown"):
            return _pick(["No problem.", "That's fine — not every loaf speaks up.", "Fair enough."], sid, turn, "unk")
        if info.get("same"):
            return _pick(["Same as the other one — noted.", "Got it, no difference there."], sid, turn, "same")
        if info.get("no_change"):
            return "Keep it as it is — noted."
        if info.get("soft_done"):
            return "Okay."
        if info.get("new_info"):
            opts = ["Got it.", "Thanks, that helps.", "Noted.", "Lovely, that's useful."]
            choice = _pick(opts, sid, turn, "ack")
            if choice == sess.get("last_ack"):
                choice = opts[(opts.index(choice) + 1) % len(opts)]
            sess["last_ack"] = choice
            return choice
        return ""

    def _offline_reply(self, sess: dict[str, Any], action: dict[str, Any], interp: dict[str, Any]) -> str:
        sid, turn, x = sess["session_id"], sess["turn"], sess["station"]
        k = action["kind"]
        ack = self._ack(sess, action, interp)
        if k == "greet":
            hello = _pick([f"Hi! I'm Sous Chef {x}, and this is Station {x}.",
                           f"Welcome to Station {x} — I'm Sous Chef {x}."], sid, "greet")
            return f"{hello} {self._question(sess, 'own_open', interp)}"
        if k == "ask":
            return f"{ack} {self._question(sess, action['topic'], interp)}".strip()
        if k == "redirect":
            lead = _pick(["Ha — I'll stick to bread today.", "I'm only trusted with the dough, I'm afraid.",
                          "Let's keep it to the loaves for now."], sid, turn, "off")
            return f"{lead} {self._question(sess, sess['last_topic'], interp)}"
        if k == "reask":
            return self._question(sess, sess["last_topic"], interp)
        if k == "ask_correction":
            return self._question(sess, "correction", interp)
        if k == "confirm":
            refl = self._reflection(sess, interp)
            pre = ("Thanks for the correction. " if action.get("corrected") else
                   "Got it, adding that. " if action.get("added") else (ack + " " if ack else ""))
            return pre + _pick([f"So {refl} — right?", f"Let me check I've got it: {refl}. Is that right?",
                                f"Here's what I heard: {refl}. Did I get that right?"], sid, turn, "confirm")
        if k == "reconfirm":
            return f"Just to be sure — {self._reflection(sess, interp)}. Shall I take that to the Head Chef?"
        if k == "handoff":
            if action.get("reason") == "confirmed":
                return _pick(["Perfect — taking this to the Head Chef now.",
                              "Great, I'll hand this to the Head Chef."], sid, turn, "ho")
            return (f"{ack + ' ' if ack else ''}I have enough to go on: {self._reflection(sess, interp)}. "
                    f"Taking it to the Head Chef now.").strip()
        if k == "chosen":
            c = sess["chosen_suggestion"]
            return (f"Great — Station {sess['sous_chef']} will run '{c['title']}' next. The operator applies it. "
                    f"Nothing starts until they confirm.")
        if k == "rechoose":
            return (f"Sorry, I didn't catch which one. Which should we try? Reply 1, 2 or 3 "
                    f"({_menu_titles(sess['suggestions'])}), or say 'start over'.")
        if k == "done":
            c = sess.get("chosen_suggestion")
            if c:
                return (f"Station {sess['sous_chef']} is set to run '{c['title']}' next; the operator applies it. "
                        f"Say 'start over' if you'd like to taste again.")
            trial = sess.get("trial_id") or "the next trial"
            return (f"The Head Chef already has this tasting — the plan is {trial}. "
                    f"Say 'start over' if you'd like to taste again.")
        return self._question(sess, sess["last_topic"], interp)

    def _word(self, sess: dict[str, Any], action: dict[str, Any], ctx: dict[str, Any], mode: str,
              text: str) -> tuple[str, str]:
        interp = self._parse(sess, ctx)
        offline = self._offline_reply(sess, action, interp)
        if action["kind"] in ("chosen", "rechoose", "done"):  # deterministic; the LLM only words the chat part
            sess["chat_mode_label"] = sess.get("chat_mode_label") or "offline:templated"
            return offline, "Offline Policy"
        if (mode or "offline").lower() != "live":
            sess["chat_mode_label"] = "offline:templated"
            return offline, "Offline Policy"
        adapter = ctx.get("llm_adapter") or LLMAdapter(ctx.get("llm_mode"))
        ok, why = adapter.check_available()
        if not ok:
            sess["chat_mode_label"] = f"offline:templated (live unavailable: {why})"
            return offline, "Offline Policy"
        topic = action.get("topic") or sess["last_topic"]
        state = {
            "stage": sess["stage"], "next_action": action["kind"],
            "question_draft": self._question(sess, topic, interp) if action["kind"] in ("ask", "redirect", "reask",
                                                                                         "ask_correction", "greet") else None,
            "reflection_draft": self._reflection(sess, interp) if action["kind"] in ("confirm", "reconfirm", "handoff")
            else None,
            "template_reply": offline, "latest_guest_message": text,
            "understood_so_far": {"summary": interp.get("desired_summary"),
                                  "structured_preference": interp.get("structured_preference")},
            "recent_transcript": sess["transcript"][-6:],
        }
        try:
            out = adapter.chat(f"scientist_{sess['sous_chef']}", sess["sous_chef"], state)
            reply = out.reply.strip()
            if action["kind"] in ("ask", "redirect", "reask", "ask_correction", "greet", "confirm", "reconfirm") \
                    and "?" not in reply:
                raise ValueError("LLM reply must ask the planned question")
            sess["chat_mode_label"] = f"live:{adapter.mode}"
            sess.setdefault("llm_extracted", []).append(
                {"turn": sess["turn"], "loaf_a": out.extracted_loaf_a, "loaf_b": out.extracted_loaf_b,
                 "wish": out.extracted_wish, "used_for_decisions": False})
            return reply, "Online Agent"
        except Exception as exc:  # noqa: BLE001 - any LLM problem -> templated wording
            sess["chat_mode_label"] = f"offline:templated (live unavailable: {type(exc).__name__}: {str(exc)[:120]})"
            return offline, "Offline Policy"

    # -- handoff -----------------------------------------------------------------------
    def _handoff(self, sess: dict[str, Any], ctx: dict[str, Any], mode: str, now: str) -> dict[str, Any]:
        note = self._note(sess) or "The guest gave no specific tasting notes."
        cyc_ctx = {k: v for k, v in ctx.items() if k not in ("chat_sessions_path", "sous_chef")}
        cyc_ctx["station"] = sess["sous_chef"]
        cyc_ctx.setdefault("now_utc", now)
        result = run_taste_feedback_cycle(note, cyc_ctx, mode=mode)
        sess["handoff_result"] = result
        sess["trial_id"] = result["next_experiment"]["id"]
        sess["suggestions"] = list(result.get("suggestions") or [])
        sess["chosen_suggestion"] = None
        sess["stage"] = "handoff"
        if cyc_ctx.get("persist_findings", True):
            log = FindingsLog(cyc_ctx.get("findings_log_path") or "default")
            sous = f"scientist_{sess['sous_chef']}"
            peer = f"scientist_{'B' if sess['sous_chef'] == 'A' else 'A'}"
            interp = result["sensory_interpretation"]
            log.append(new_finding(
                sous, "sensory_feedback", f"[via chat] {note}",
                payload={"via": "chat", "tags": ["via chat"], "session_id": sess["session_id"],
                         "structured_preference": interp.get("structured_preference"),
                         "desired_summary": interp.get("desired_summary"),
                         "guest_messages": [t["text"] for t in sess["transcript"] if t["role"] == "guest"]},
                evidence_source="replay" if cyc_ctx.get("replay") else "judge_feedback",
                chamber_id=sess["sous_chef"], cycle_id=result["cycle_id"], timestamp_utc=now,
                recipients=["head_chef", peer], experiment_id=sess["trial_id"],
                evidence_refs=[f"chat:{sess['session_id']}"], claim=interp.get("desired_summary")))
        return result

    def _narrate(self, sess: dict[str, Any], r: dict[str, Any]) -> str:
        interp = r["sensory_interpretation"]
        prefs = [PREF_PHRASES.get(p, p.lower()) for p in interp.get("structured_preference") or []]
        goal = _join(prefs) if prefs else "no specific wish given, so explore the most informative conditions"
        h = r["hypothesis"]
        lead_ch, peer_ch = h["proposed_by"][-1], h["challenged_by"][-1]
        msgs = r["agent_messages"]
        lead_pick = msgs[0]["payload"]["own_decision"]["name"]
        crit = msgs[1]["payload"]
        concern = next((c for c in crit.get("concerns", []) if not c.startswith(("My chamber", "From my own"))),
                       crit.get("alternative_hypothesis", ""))
        concern = re.split(r"(?<=[.;])\s", concern.strip())[0].rstrip(";,: ")
        concern = concern if concern.endswith((".", "!", "?")) else concern + "."
        if len(concern) > 170:
            concern = concern[:167].rstrip() + "…"
        stance = "agreed, but flagged" if crit.get("agrees_with_hypothesis") else "pushed back"
        ne = r["next_experiment"]
        st = ne["schedules"]
        src = r["provenance"].get("prediction_source", "estimated")
        revised = msgs[2]["payload"].get("revised")
        trial = ne["id"].replace("_", " ").title()
        menu = ""
        if sess.get("suggestions"):
            items = "; ".join(f"{x['rank']}. {x['title']} — {x['target']} ({x['risk']})" for x in sess["suggestions"])
            menu = (f" Suggestions for Station {sess['sous_chef']} (my pick is 1): {items}. "
                    f"Which should we try? Reply 1, 2 or 3.")
        return (f"Head Chef: goal set — {goal}. I sent out {len(S.LIBRARY)} line cooks (candidate simulations, "
                f"{src}) to try every validated schedule. Sous Chef {lead_ch} proposed '{lead_pick}' for "
                f"Station {lead_ch}; Sous Chef {peer_ch} {stance}: {concern}"
                f"{' Sous Chef ' + lead_ch + ' revised its plan.' if revised else ''} "
                f"→ {trial}: Station A: '{st['A']['name']}', Station B: '{st['B']['name']}'. "
                f"({r['reasoning_mode_label']}; nothing starts until the operator confirms.){menu}")

    def _understood(self, sess: dict[str, Any]) -> dict[str, Any]:
        interp = parse_feedback(self._note(sess))
        cov = self._coverage(sess, interp)
        pref, basis = self._preferred(sess, interp)
        return {
            "loaves": {l: {d: {"label": v["label"], "score": v["score"], "confidence": v["confidence"]}
                           for d, v in interp["loaves"][l]["attributes"].items()} for l in ("A", "B")},
            "unattributed": {d: {"label": v["label"], "score": v["score"]}
                             for d, v in interp["unattributed"]["attributes"].items()},
            "desired": {d: {"direction": v["direction"], "strength": v["strength"], "confidence": v["confidence"]}
                        for d, v in interp["desired"].items()},
            "structured_preference": interp.get("structured_preference", []),
            "desired_summary": interp.get("desired_summary"),
            "station": sess["station"],
            "compared_other": sess["compare"] == "yes",
            "preferred_loaf": pref,
            "preference_basis": basis,
            "preference_reason": sess.get("preference_reason"),
            "coverage": cov,
            "flags": dict(sess["flags"]),
            "consolidated_note": self._note(sess),
        }


_ORDINALS = {"1": 0, "one": 0, "first": 0, "1st": 0, "2": 1, "two": 1, "second": 1, "2nd": 1,
             "3": 2, "three": 2, "third": 2, "3rd": 2, "last": 2}
_STOP = {"the", "a", "an", "one", "with", "and", "start", "ferment", "please", "try", "go", "let", "lets", "i", "to",
         "of", "it", "that", "this", "option", "suggestion", "number", "no", "pick", "choose", "take", "like", "want"}


def _menu_titles(suggestions: list[dict[str, Any]]) -> str:
    return "; ".join(f"{x['rank']}. {x['title']}" for x in suggestions)


def parse_choice(text: str, suggestions: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Deterministically map a reply ("2", "the second one", a title fragment) to one suggestion, else None."""
    words = re.findall(r"[a-z0-9]+(?:st|nd|rd)?", (text or "").lower())
    n = len(suggestions)
    hits = set()
    for w in words:
        if w in _ORDINALS and _ORDINALS[w] < n and (w != "one" or "number" in words or len(words) == 1):
            hits.add(_ORDINALS[w])
    if len(hits) == 1:
        return suggestions[hits.pop()]
    if len(hits) > 1:
        return None
    low = " ".join(words)
    full = [x for x in suggestions if x["title"].lower().rstrip(")").split(" (")[0] in low]
    if len(full) == 1:
        return full[0]
    toks = {w for w in words if len(w) >= 3 and w not in _STOP}
    if not toks:
        return None
    score = []
    for x in suggestions:
        tt = {w for w in re.findall(r"[a-z0-9]+", x["title"].lower()) if len(w) >= 3 and w not in _STOP}
        others = set().union(*(set(re.findall(r"[a-z0-9]+", y["title"].lower())) for y in suggestions if y is not x))
        score.append(len((tt - others) & toks))
    best = max(score)
    return suggestions[score.index(best)] if best >= 1 and score.count(best) == 1 else None


_CHATS: dict[str, SousChefChat] = {}


def _chat_for(context: dict | None) -> SousChefChat:
    key = str(_sessions_path(context))
    if key not in _CHATS:
        _CHATS[key] = SousChefChat(key)
    return _CHATS[key]


def chat_turn(session_id: str, text: str, context: dict | None = None, mode: str = "offline") -> dict:
    """One Guest message in, one Sous Chef message out (see module docstring)."""
    return _chat_for(context).turn(session_id, text, context, mode)


def chat_reset(session_id: str, context: dict | None = None) -> dict:
    return _chat_for(context).reset(session_id)
