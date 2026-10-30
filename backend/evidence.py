"""Evidence that a candidate is connected to the show's area: search, then verify.

Searching (Tavily) and verifying are separate steps. A search hit only becomes
evidence after three checks:

  1. Identity   is the page about this artist, not another person or thing with the same name?
  2. Relation   does the page say the artist is BASED in the area, or only that they PLAYED there?
  3. Scope      does the supporting sentence name a place inside the current search area?

Each kept row carries a status:
  verified    a sentence copied from the page supports the claim (checked in code)
  unverified  something points that way, but the support could not be confirmed
  rejected    the page is about someone else, is a ticket/listing page, or states no connection

Only verified "based_here" evidence counts as local proof (see rules.local_score).
"Played here" is a separate market signal: a touring act that played the city is not a local act.

Verification uses the LLM when available (it must quote the page, and the code checks the
quote is really in the text and names a place in the area). Without an LLM, a strict
sentence-pattern rule is used instead.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from typing import Callable, Optional
from urllib.parse import urlparse

import httpx

from . import cache, config
from .budget import Budget, BudgetExceeded
from .qloo_client import norm

VERIFIER_VERSION = "v3"  # part of the verdict cache key; bump when the verifier prompt or rules change

# Pages that list tickets, tour dates or streams for any city. They prove nothing about a connection.
LISTING_DOMAINS = ("seatgeek.com", "ticketmaster.com", "livenation.com", "stubhub.com", "vividseats.com",
                   "songkick.com", "bandsintown.com", "jambase.com", "concertarchives.org", "axs.com",
                   "eventbrite.com", "hypebot.com", "open.spotify.com", "music.apple.com", "shazam.com",
                   "last.fm", "allevents.in", "concerts50.com", "ticketsmarter.com", "gametime.co")
# Capitalised words that commonly follow an artist's name in titles without extending the name.
TITLE_WORDS = {"live", "concert", "concerts", "tickets", "ticket", "tour", "tours", "dates", "band", "music",
               "official", "shows", "show", "full", "performs", "performing", "interview", "review", "photos",
               "album", "new", "presents", "and", "with", "featuring", "feat", "at", "in", "on", "the", "is",
               "are", "was", "were", "has", "have", "tonight", "returns", "announces", "plays", "session",
               "sessions", "setlist", "wikipedia", "lyrics", "video", "videos", "home", "news", "event", "events"}
LEAD_WORDS = {"the", "a", "an", "by", "from", "with", "and", "featuring", "feat", "see", "watch", "meet",
              "photos", "review", "live", "tonight", "presents", "presenting", "introducing", "about", "why",
              "how", "when", "where", "who", "listen", "hear", "catch", "welcome", "support", "openers", "opener",
              "special", "guest", "guests", "plus", "also", "headliner", "headliners", "artist", "band", "singer",
              "since", "after", "before", "while", "although", "though", "because", "if", "as", "then", "but",
              "so", "yet", "now", "here", "there", "today", "recently", "even", "once", "until", "like", "unlike",
              "for", "in", "at", "on", "of", "to", "at", "this", "that", "these", "those", "both", "only", "just"}

# Where the act is NOW (counts most) vs where it COMES FROM (counts, shown separately).
CURRENT_PATTERNS = (r"{p}[\s-]based", r"based (?:out )?(?:of|in) (?:the )?{p}", r"(?:lives|living|resides|residing) in {p}",
                    r"(?:moved|relocated) to {p}", r"{p}'s own", r"calls? {p} home",
                    r"{p} (?:band|duo|trio|quartet|singer|songwriter|singer-songwriter|musician|artist|rapper|group|"
                    r"outfit|collective|act|producer|musicians|bands|artists)")
ORIGIN_PATTERNS = (r"(?:hails?|hailing) from {p}",
                   r"\b(?:is|are|was|were|band|duo|trio|quartet|group|singer|songwriter|singer-songwriter|musician|"
                   r"artist|rapper|producer|act|outfit|collective|natives?|musicians|originally|all)\s+(?:originally\s+)?"
                   r"(?:\w+\s+)?from {p}", r"formed in {p}", r"native of {p}", r"{p} native",
                   r"grew up in {p}", r"raised in {p}", r"born in {p}", r"hometown(?: of)?,? {p}")
BASED_PATTERNS = CURRENT_PATTERNS + ORIGIN_PATTERNS
# Words that turn a match into a negative, a past state or hearsay: "not based in", "formerly of",
# "spent time living in", "a blog claimed she was from".
HEDGE_RE = re.compile(r"\b(?:not|never|no longer|formerly|previously|once|used to|spent time|left|leaving|"
                      r"away from|claimed|claims|rumou?red|reportedly|allegedly|supposedly)\b[^.;]{0,40}$", re.I)
# "my mom lives in Atlanta": the place belongs to a relative, not the artist.
KIN_RE = re.compile(r"\b(?:my|his|her|their|our|whose)\s+(?:mom|mum|mother|dad|father|parents|family|wife|husband|"
                    r"partner|girlfriend|boyfriend|brother|sister|siblings|grand(?:ma|pa|mother|father|parents)|son|"
                    r"daughter|kids|friends?|manager|label)\b[^.;]{0,40}$", re.I)
# The artist is an object, not the subject: "playing with Cory Wong", "opened for X", "fans of X".
OBJECT_RE = re.compile(r"\b(?:with|for|alongside|featuring|feat\.?|ft\.?|supporting|behind|like|of|to|and|by|"
                       r"opening for|opened for|toured with|played with|plays with|playing with)\s+$", re.I)
PROPER_NAME_RE = re.compile(r"\b[A-Z][a-z'’.-]+(?:\s+[A-Z][a-z'’.-]+)+\b")
# "the band Bermuda Triangle, formed in Nashville": the place belongs to ANOTHER named group.
OTHER_GROUP_RE = re.compile(r"\b(?:band|group|project|side project|duo|trio|supergroup|collective)\s+"
                            r"(?:called\s+|named\s+)?[A-Z]")
# Whole words only: "tour" must not match "tournament", "show" must not match "showcase of tennis".
SHOW_RE = re.compile(r"\b(?:concerts?|live at|performed|performs|performing|played|plays|tour|touring|shows?|"
                     r"festival|gigs?|set at|headlin\w*|opened for|stage)\b", re.I)


# A "<place>-based ..." or "based in <place>" phrase about one of these is not about the artist:
# "signed to Nashville-based label New West", "austin, tx based indie music blog".
ORG_NOUNS = re.compile(r"\b(?:label|labels|records|recordings|imprint|blog|zine|magazine|publication|newspaper|"
                       r"outlet|radio|station|podcast|venue|venues|club|bar|theater|theatre|festival|promoter|"
                       r"promotions|agency|management|company|studio|studios|website|site|nonprofit|organi[sz]ation|"
                       r"store|shop|brand|distributor|collective of)\b", re.I)


class EvidenceError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _mentions(text_norm: str, term: str) -> bool:
    t = norm(term)
    return bool(t) and re.search(rf"(^| ){re.escape(t)}( |$)", text_norm) is not None


def _domain(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def is_listing_page(url: str) -> bool:
    d = _domain(url)
    return any(d == x or d.endswith("." + x) for x in LISTING_DOMAINS)


def clean_mentions(artist: str, text: str, place_terms: list[str]) -> int:
    """Count mentions of the artist that are not part of a longer proper name
    ('Whitney Houston', 'Sault Ste Marie', 'Sid Grimes')."""
    places = {norm(p) for p in place_terms}
    count = 0
    for m in re.finditer(rf"(?<![\w]){re.escape(artist)}(?![\w])", text, flags=re.I):
        if " " not in artist.strip() and artist[:1].isupper() and not m.group(0)[:1].isupper():
            continue  # one-word names: 'tennis tournament' is not the band Tennis
        before = text[max(0, m.start() - 40):m.start()]
        after = text[m.end():m.end() + 40]
        if _title_case(_sentence_around(text, m.start(), m.end())):
            count += 1  # in a Title Case Headline every word is capitalised, so capitals say nothing
            continue
        prev = re.search(r"([A-Z][\w'’.-]*) $", before)
        nxt = re.match(r" ([A-Z][\w'’.-]*)", after)
        def placeish(word: str) -> bool:
            w = norm(word.replace("’s", "").replace("'s", ""))
            return any(w and w in p.split() for p in places)
        extends_before = bool(prev) and prev.group(1).lower().strip(".") not in LEAD_WORDS \
            and not placeish(prev.group(1)) and not re.search(r"-[a-z]", prev.group(1))  # 'Nashville-based X'
        extends_after = bool(nxt) and nxt.group(1).lower().strip(".") not in TITLE_WORDS \
            and not placeish(nxt.group(1))
        if not extends_before and not extends_after:
            count += 1
    return count


def _sentence_around(text: str, start: int, end: int) -> str:
    left = max(text.rfind(ch, 0, start) for ch in ".!?\n|")
    rights = [i for i in (text.find(ch, end) for ch in ".!?\n|") if i != -1]
    return text[left + 1:min(rights) if rights else len(text)]


def _title_case(sentence: str) -> bool:
    words = [w for w in re.findall(r"[A-Za-z][\w'’-]*", sentence) if len(w) > 3]
    return len(words) >= 4 and sum(w[0].isupper() for w in words) / len(words) >= 0.8


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+|\s\|\s|\s•\s", text) if s.strip()]


FOLLOWED_BY_OTHER_GROUP_RE = re.compile(r"\s*(?:indie |rock |pop |punk |folk |country )?(?:band|group|duo|trio|quartet|"
                                        r"project|outfit|collective|label|act)\s+(?:called\s+|named\s+)?(?P<name>[A-Z][\w'’.&-]+(?:\s+[A-Z][\w'’.&-]+)*)")


def _describes_other_group(artist: str, after: str) -> bool:
    """'Philadelphia-based band Toy Soldiers': the phrase describes another named group."""
    m = FOLLOWED_BY_OTHER_GROUP_RE.match(after)
    return bool(m) and norm(m.group("name")) != norm(artist) and not norm(m.group("name")).startswith(norm(artist))


LEAD_SUBJECT_RE = re.compile(r"([A-Z][\w'’.-]+(?:\s+[A-Z][\w'’.-]+){1,4})\s*(?:\([^)]*\)\s*)?,?\s*"
                             r"(?:is|was|are|were|has|had)\b")
PARTICIPLE_BASE_RE = re.compile(r"^(?:(?:currently|now|presently)\s+)?based (?:out )?(?:in|of)\b", re.I)


def own_page_subject(artist: str, sentence: str, page_about_artist: bool, subject: Optional[str] = None) -> str:
    """On the artist's own page, 'Faye Connell Webster (born 1997) is ...' or 'The group is ...' are about
    the artist: rewrite the leading subject to the artist's name so the sentence rules can apply.
    `subject` is the person the page was last talking about (see page_sentences); a pronoun is only
    read as the artist when that person is the artist, so 'He moved to Nashville' in a bio of the
    artist's keyboardist stays about the keyboardist."""
    if not page_about_artist:
        return sentence
    if subject is not None and not _is_artist(artist, subject):
        return sentence
    tokens = norm(artist).split()
    m = re.match(r"([A-Z][\w'’.-]+(?:\s+[A-Z][\w'’.-]+){0,4})", sentence)
    if m and tokens and all(t in norm(m.group(1)).split() for t in tokens) and norm(m.group(1)) != norm(artist):
        return artist + sentence[m.end():]
    if PRONOUN_SUBJECT_RE.match(sentence):
        return f"{artist} " + PRONOUN_SUBJECT_RE.sub("", sentence, count=1)
    if PARTICIPLE_BASE_RE.match(sentence):  # Wikipedia: "Based in Los Angeles, California, her music ..."
        return f"{artist} is " + sentence[0].lower() + sentence[1:]
    return sentence


def _is_artist(artist: str, name: str) -> bool:
    tokens = norm(artist).split()
    return bool(tokens) and all(t in norm(name).split() for t in tokens)


def page_sentences(artist: str, text: str, page_about_artist: bool) -> list[str]:
    """Sentences of a page, with leading pronouns / 'Based in ...' resolved to the artist only while
    the page is talking about the artist (the last sentence that opened with a full name decides)."""
    out, subject = [], None
    for sent in _sentences(text):
        if norm(sent).startswith(norm(artist)):
            subject = artist
        else:
            m = LEAD_SUBJECT_RE.match(sent)
            if m:
                subject = m.group(1)
        out.append(own_page_subject(artist, sent, page_about_artist, subject))
    return out


def states_connection(artist: str, sentence: str, place_terms: list[str]) -> Optional[str]:
    """'based_here', 'formed_here' or None for ONE sentence. The sentence must name the artist and
    say that the artist itself (not a label, blog, venue or another named band near it) lives in /
    is based in (current) or comes from / formed in (origin) a place in the area. Negated, past or
    hearsay phrasing does not count."""
    if not clean_mentions(artist, sentence, place_terms):
        return None
    low = sentence.lower()
    names = [m.start() for m in re.finditer(re.escape(artist.lower()), low)]
    found = None
    for kind, patterns in (("based_here", CURRENT_PATTERNS), ("formed_here", ORIGIN_PATTERNS)):
        for p in place_terms:
            pe = re.escape(p.lower())
            for pat in patterns:
                for m in re.finditer(pat.format(p=pe), low):
                    after = low[m.end():m.end() + 30]
                    if "based" in m.group(0) and ORG_NOUNS.match(after.strip()):
                        continue  # "Nashville-based label ..."
                    if _describes_other_group(artist, sentence[m.end():m.end() + 60]):
                        continue  # "Atlanta-based band Other Name"
                    if re.match(r"\s*(?:indie |independent |music |record )?(?:label|blog|radio|venue|magazine)\b", after):
                        continue
                    if HEDGE_RE.search(sentence[max(0, m.start() - 60):m.start()]):
                        continue  # "not based in", "formerly", "spent time living in", "claimed"
                    if KIN_RE.search(sentence[max(0, m.start() - 60):m.start()]):
                        continue  # "my mom lives in Atlanta"
                    if not _artist_is_subject(artist, sentence, place_terms, m.start(), m.end()):
                        continue  # "Petar Janjic ... playing with Cory Wong ... in Nashville"
                    before = [n for n in names if n < m.start()]
                    if before:
                        g0, g1 = before[-1], m.start()
                    else:
                        g0, g1 = m.end(), min((n for n in names if n >= m.end()), default=m.end())
                    if ORG_NOUNS.search(low[g0:g1]) or OTHER_GROUP_RE.search(sentence[g0 + len(artist):g1]):
                        continue
                    return kind
    return found


def _artist_is_subject(artist: str, sentence: str, place_terms: list[str], p_start: int, p_end: int) -> bool:
    """Heuristic: the place phrase is about the artist, not another person named in the sentence.
    Applies only to normal sentences; in Title Case headlines capitals carry no information."""
    if _title_case(sentence):
        return True
    places = {norm(p) for p in place_terms}
    no_parens = lambda t: re.sub(r"\([^)]*\)", " ", t)
    for m in re.finditer(rf"(?<![\w]){re.escape(artist)}(?![\w])", sentence, flags=re.I):
        before = sentence[:m.start()]
        if OBJECT_RE.search(before[-25:]):
            continue  # artist is an object here
        if m.start() < p_start:
            # someone else named earlier in the sentence is the likelier subject
            others = [n for n in PROPER_NAME_RE.findall(no_parens(before))
                      if norm(n) not in places and norm(n) != norm(artist) and not any(norm(n) in p or p in norm(n) for p in places)]
            if others:
                continue
            return True
        # phrase before the artist ("Atlanta-based band Lunar Vacation"): nobody else named in between
        gap = no_parens(sentence[p_end:m.start()])
        others = [n for n in PROPER_NAME_RE.findall(gap) if norm(n) not in places]
        if not others:
            return True
    return False


def states_based(artist: str, sentence: str, place_terms: list[str]) -> bool:
    return states_connection(artist, sentence, place_terms) is not None


def states_played(artist: str, sentence: str, place_terms: list[str]) -> bool:
    return bool(clean_mentions(artist, sentence, place_terms)) and any(_mentions(norm(sentence), p) for p in place_terms) \
        and bool(SHOW_RE.search(sentence))


def rule_verdict(artist: str, text: str, place_terms: list[str]) -> dict:
    """Fallback without an LLM: one sentence must name the artist and state the connection."""
    for sent in _sentences(text):
        kind = states_connection(artist, sent, place_terms)
        if kind:
            return {"same_artist": "yes", "relation": kind, "quote": sent[:300], "method": "rule"}
    for sent in _sentences(text):
        if states_played(artist, sent, place_terms):
            return {"same_artist": "unclear", "relation": "played_here", "quote": sent[:300], "method": "rule"}
    return {"same_artist": "unclear", "relation": "none", "quote": "", "method": "rule"}


def _verdict_key(artist: str, url: str, place_terms: list[str]) -> str:
    return hashlib.sha1(f"{artist.lower()}|{url}|{'/'.join(place_terms).lower()}".encode()).hexdigest()[:16]


YEAR_IN_URL_RE = re.compile(r"(?:/|-|_)(20[0-3]\d)(?:/|-|_|\.)")


def source_year(hit: dict) -> Optional[int]:
    """Publication year when it can be read reliably (URL date path or the search API's date).
    Years mentioned inside the text are NOT used: 'formed in 2016' is not a publication date."""
    pd = hit.get("published_date") or hit.get("published") or ""
    m = re.match(r"(20[0-3]\d)", str(pd))
    if m:
        return int(m.group(1))
    m = YEAR_IN_URL_RE.search(str(hit.get("url") or ""))
    return int(m.group(1)) if m else None


def is_encyclopedia(url: Optional[str]) -> bool:
    return bool(url) and _domain(url).endswith("wikipedia.org")


def _row(artist, hit, scope_label, kind, status, reason, quote="", method="rule") -> dict:
    content = (hit.get("content") or "").strip()
    return {
        "kind": kind, "status": status, "reason": reason,
        "claim": {"based_here": f"{artist} is based in the area",
                  "formed_here": f"{artist} is from or formed in the area",
                  "played_here": f"{artist} has played in the area"}.get(kind, f"No connection shown for {artist}"),
        "quote": quote or None,
        "excerpt": content[:280] + ("…" if len(content) > 280 else "") or None,
        "source_title": (hit.get("title") or "")[:200], "source_url": hit.get("url"),
        "source_label": "public", "method": method, "scope": scope_label, "retrieved_at": _now(),
        "source_year": source_year(hit),
    }


def judge(artist: str, hit: dict, verdict: dict, place_terms: list[str], scope_label: str) -> dict:
    """Turn a verdict (LLM or rule) into an evidence row, checking the quote in code."""
    text = f"{hit.get('title') or ''}\n{hit.get('content') or ''}"
    method = verdict.get("method", "llm")
    if verdict.get("same_artist") == "no":
        return _row(artist, hit, scope_label, "none", "rejected",
                    f"About a different person or thing named '{artist}'", method=method)
    rel = verdict.get("relation")
    if rel not in ("based_here", "formed_here", "played_here"):
        return _row(artist, hit, scope_label, "none", "rejected",
                    "Mentions the artist and the area but does not say the artist is from or played there",
                    method=method)
    quote = (verdict.get("quote") or "").strip()
    squash = lambda x: re.sub(r"\s+", " ", x).strip().lower()
    if verdict.get("same_artist") != "yes":
        return _row(artist, hit, scope_label, rel, "unverified", "Not certain the page is about this artist",
                    quote, method)
    # The model's label is a hint, not a decision. The sentence itself decides both whether the claim is
    # supported and which claim it is: "lives in / based in" = based_here, "from / formed in" = formed_here.
    def check(sentence):
        if rel == "played_here":
            return "played_here" if states_played(artist, sentence, place_terms) else None
        return states_connection(artist, sentence, place_terms)
    if quote and squash(quote) in squash(text):
        kind = check(quote)
        if kind:
            return _row(artist, hit, scope_label, kind, "verified", "Supporting sentence quoted from the page",
                        quote, method)
    own = norm(hit.get("title") or "").startswith(norm(artist))
    for sent in page_sentences(artist, text, own):  # the model may have quoted the wrong sentence of a good page
        kind = check(sent)
        if kind:
            return _row(artist, hit, scope_label, kind, "verified", "Supporting sentence found on the page",
                        sent[:300], method)
    if not quote or squash(quote) not in squash(text):
        why = "The supporting sentence was not found in the page text"
    elif not any(_mentions(norm(quote), p) for p in place_terms):
        why = "The supporting sentence does not name a place in the search area"
    elif rel != "played_here":
        why = "No sentence on the page says the artist itself is from or based in the area now"
    else:
        why = "No sentence on the page names the artist performing in the area"
    return _row(artist, hit, scope_label, rel, "unverified", why, quote, method)


def qloo_birthplace_evidence(artist: str, place_of_birth: Optional[str], place_terms: list[str],
                             scope_label: str) -> list[dict]:
    """Qloo's own 'place_of_birth' property (individual artists only)."""
    if not place_of_birth:
        return []
    place = next((p for p in place_terms if _mentions(norm(place_of_birth), p)), None)
    if not place:
        return []
    return [{
        "kind": "formed_here", "status": "verified", "reason": "Qloo entity data",
        "claim": f"Qloo lists {artist}'s place of birth as {place_of_birth}",
        "quote": place_of_birth, "excerpt": place_of_birth, "source_title": "Qloo entity properties",
        "source_url": None, "source_label": "qloo", "method": "qloo", "scope": scope_label, "retrieved_at": _now(),
    }]


# ── where is the act NOW? (run for artists that look local) ──────────────────
_PLACE = r"(?P<p>[A-Z][\w.'’-]+(?:(?:,\s*|\s+)(?:[A-Z][\w.'’-]+|D\.C\.)){0,3})"
ELSEWHERE_PATTERNS = (
    ("based", re.compile(r"\b(?:now |currently |presently )?based (?:out )?(?:of|in) (?:the )?" + _PLACE)),
    ("based", re.compile(r"(?<![\w-])" + _PLACE.replace("{0,3}", "{0,1}") + r"-based\b")),
    ("based", re.compile(r"\b(?:lives|living|resides|residing|relocated to|moved to|calls?)\s+(?:in\s+)?" + _PLACE)),
    ("origin", re.compile(r"\b(?:formed|founded|started|began) in " + _PLACE)),
)
INACTIVE_RE = re.compile(r"\b(?:retired from (?:music|performing|the music (?:industry|business))|has retired|disbanded|"
                         r"broke up|split up|went on (?:an )?(?:indefinite )?hiatus|farewell (?:show|tour)|"
                         r"final (?:show|tour|performance)|ceased (?:performing|activity))\b", re.I)
PRONOUN_SUBJECT_RE = re.compile(r"^(?:the (?:group|band|duo|trio|quartet|project|act|singer|artist|musician)|"
                                r"she|he|they)\s+(?=(?:is|are|was|were|lives|live|resides|reside|moved|relocated|"
                                r"formed|grew up|hails|hail|calls|call|currently|now|later)\b)", re.I)
MUSIC_WORD_RE = re.compile(r"\b(?:band|musician|singer|songwriter|album|record|music|artist|duo|trio|EP|single|tour)\b", re.I)
NOT_PLACES = {"music", "indie", "the", "a", "an", "this", "their", "his", "her", "its", "my", "our"}


def _place_is_in(place: str, terms: list[str]) -> bool:
    pn = norm(place)
    return any(_mentions(pn, t) or norm(t) == pn for t in terms)


def location_rows(artist: str, hit: dict, place_terms: list[str], wide_terms: list[str], scope_label: str) -> list[dict]:
    """Statements in one page about where the artist is based or began, in ANY place, plus
    statements that the act has stopped. Places inside the wider area (metro/state) are neutral."""
    text = f"{hit.get('title') or ''}\n{hit.get('content') or ''}"
    if not clean_mentions(artist, text, place_terms) or not MUSIC_WORD_RE.search(text) or is_listing_page(hit["url"]):
        return []
    out = []
    page_about_artist = norm(hit.get("title") or "").startswith(norm(artist))
    for sent in page_sentences(artist, text, page_about_artist):
        if not clean_mentions(artist, sent, place_terms):
            continue
        if INACTIVE_RE.search(sent) and not HEDGE_RE.search(sent[:INACTIVE_RE.search(sent).start()]):
            m = INACTIVE_RE.search(sent)
            if _artist_is_subject(artist, sent, place_terms, m.start(), m.end()):
                r = _row(artist, hit, scope_label, "inactive", "verified", "A source says the act may have stopped",
                         sent[:300])
                r["claim"] = f"A source says {artist} may no longer be active"
                out.append(r)
        for kind, rx in ELSEWHERE_PATTERNS:
            for m in rx.finditer(sent):
                place = m.group("p").strip(" ,.")
                if norm(place) in NOT_PLACES or norm(place) == norm(artist) or len(place) < 3:
                    continue
                pre = sent[max(0, m.start() - 60):m.start()]
                if HEDGE_RE.search(pre) or KIN_RE.search(pre) or ORG_NOUNS.match(sent[m.end():m.end() + 25].strip()):
                    continue
                if _describes_other_group(artist, sent[m.end():m.end() + 60]):
                    continue  # "including Philadelphia-based band Toy Soldiers"
                if not _artist_is_subject(artist, sent, place_terms + [place], m.start(), m.end()):
                    continue
                if _place_is_in(place, wide_terms):
                    continue  # local or inside the wider area: handled by the normal evidence path
                k = "based_elsewhere" if kind == "based" else "formed_elsewhere"
                r = _row(artist, hit, scope_label, k, "verified", "A source places the act outside the area", sent[:300])
                r["claim"] = (f"A source says {artist} is based in {place}" if kind == "based"
                              else f"A source says {artist} began in {place}")
                r["place"] = place
                out.append(r)
    return out


# ── search + verify ─────────────────────────────────────────────────────────
Verifier = Callable[[str, dict, list[str], list[dict]], list[dict]]


class TavilyEvidence:
    """Live provider: two searches per artist, then verification of every hit."""

    name = "tavily"
    MAX_REJECTED = 4

    SEARCH_PARAMS = {"max_results": 5, "search_depth": "basic"}

    def __init__(self, transport: Optional[httpx.BaseTransport] = None, api_key: Optional[str] = None,
                 verifier: Optional[Verifier] = None, budget: Optional[Budget] = None):
        self.key = api_key if api_key is not None else config.TAVILY_API_KEY
        self.budget = budget or Budget(config.SEARCH_BUDGET_PER_RUN)
        self._http = httpx.Client(transport=transport, timeout=config.HTTP_TIMEOUT)
        self._memo: dict[str, list[dict]] = {}
        self._verifier = verifier
        self._verdicts: dict[str, dict] = {}
        self._vlock = threading.Lock()

    @property
    def available(self) -> bool:
        return bool(self.key)

    # search ------------------------------------------------------------------
    def _search(self, query: str) -> list[dict]:
        if query in self._memo:
            return self._memo[query]
        hit = cache.get(self.name, query, self.SEARCH_PARAMS)
        if hit is not None:
            self.budget.hit()
            self._memo[query] = hit[0]
            return hit[0]
        self.budget.spend(1)  # raises BudgetExceeded before a paid call that would go over the cap
        hits = self._search_uncached(query)
        cache.put(self.name, query, hits, self.SEARCH_PARAMS)
        self._memo[query] = hits
        return hits

    def _search_uncached(self, query: str) -> list[dict]:
        last = None
        for _ in range(2):
            try:
                r = self._http.post("https://api.tavily.com/search",
                                    json={"api_key": self.key, "query": query, "max_results": 5,
                                          "search_depth": "basic"})
                if r.status_code == 200:
                    data = r.json()
                    hits = data.get("results") if isinstance(data, dict) else None
                    hits = [h for h in (hits or []) if isinstance(h, dict)]
                    if config.RECORD_FIXTURES:
                        _record("searches", query, [{k: h.get(k) for k in ("title", "url", "content")} for h in hits])
                    return hits
                last = f"HTTP {r.status_code}"
                if r.status_code < 500 and r.status_code != 429:
                    break
            except (httpx.HTTPError, ValueError) as e:
                last = f"{type(e).__name__}"
        raise EvidenceError(f"Evidence search failed ({last})")

    @staticmethod
    def queries(artist: str, main_place: str) -> list[str]:
        # Kept identical to earlier versions so recorded searches still replay.
        return [f'"{artist}" band OR musician based in OR hometown {main_place}',
                f'"{artist}" {main_place} live concert']

    # verify ------------------------------------------------------------------
    def _verdicts_for(self, artist: str, context: dict, place_terms: list[str], hits: list[dict]) -> list[dict]:
        keys = [_verdict_key(artist, h["url"], place_terms) for h in hits]
        if self.name == "tavily":  # live mode: reuse verdicts from earlier runs for the same page and area
            vparams = {"model": config.OPENAI_MODEL, "version": VERIFIER_VERSION}
            for k in keys:
                if k not in self._verdicts:
                    got = cache.get("verdict", k, vparams)
                    if got is not None:
                        self._verdicts[k] = got[0]
        todo = [(k, h) for k, h in zip(keys, hits) if k not in self._verdicts]
        if todo:
            got = self._run_verifier(artist, context, place_terms, [h for _, h in todo])
            with self._vlock:
                for (k, h), v in zip(todo, got):
                    self._verdicts[k] = v
                    if v.get("method") == "llm":
                        if self.name == "tavily":
                            cache.put("verdict", k, v, {"model": config.OPENAI_MODEL, "version": VERIFIER_VERSION})
                        if config.RECORD_FIXTURES:
                            _record("verdicts", k, v)
        return [self._verdicts[k] for k in keys]

    def _run_verifier(self, artist, context, place_terms, hits) -> list[dict]:
        verifier = self._verifier
        if verifier is None:
            from . import llm  # lazy: tests and demo mode without a key never import openai
            verifier = llm.verify_sources if config.OPENAI_API_KEY else None
        if verifier is not None:
            try:
                out = verifier(artist, context, place_terms, hits)
                if len(out) == len(hits):
                    return [{**v, "method": "llm"} for v in out]
            except Exception:  # LLM down or malformed: fall back to the strict rule
                pass
        return [rule_verdict(artist, f"{h.get('title') or ''}\n{h.get('content') or ''}", place_terms) for h in hits]

    @staticmethod
    def base_query(artist: str) -> str:
        return f'"{artist}" musician OR band based in'

    def current_base(self, artist: str, place_terms: list[str], wide_terms: list[str], scope_label: str) -> list[dict]:
        """One neutral search ('where is X based') for an artist that looks local: finds sources that place
        the act elsewhere now, or say it has stopped. Costs one search per artist."""
        rows, seen = [], set()
        for h in self._search(self.base_query(artist)):
            url = h.get("url") if isinstance(h, dict) else None
            if not isinstance(url, str) or not url.startswith(("http://", "https://")) or url in seen:
                continue
            seen.add(url)
            rows += location_rows(artist, h, place_terms, wide_terms or place_terms, scope_label)
            own = norm(h.get("title") or "").startswith(norm(artist))
            for sent in page_sentences(artist, f"{h.get('title') or ''}\n{h.get('content') or ''}", own):
                kind = states_connection(artist, sent, place_terms)  # dated local statements count too
                if kind:
                    rows.append(_row(artist, h, scope_label, kind, "verified", "Found by the current-base check",
                                     sent[:300]))
                    break
        dedup, keys = [], set()
        for r in rows:
            k = (r["kind"], r["source_url"], r.get("place"))
            if k not in keys:
                keys.add(k)
                dedup.append(r)
        return dedup[:6]

    def check(self, artist: str, place_terms: list[str], scope_label: str, context: Optional[dict] = None,
              wide_terms: Optional[list[str]] = None) -> list[dict]:
        """search() plus, for artists with any local lead (verified or not: a MusicBrainz area, an
        unconfirmed 'based here' line, a local origin), the current-base check."""
        from . import rules
        rows = self.search(artist, place_terms, scope_label, context)
        extra = [r for r in (context or {}).get("prior", []) if r]
        leads = [r for r in rows + extra if r.get("kind") in ("based_here", "formed_here") and r.get("status") != "rejected"]
        if leads:
            wide = wide_terms or place_terms
            rows += self.current_base(artist, place_terms, wide, scope_label)
            # the evidence-search pages can also say the act is based elsewhere or has stopped (no extra cost)
            have = {(r["kind"], r["source_url"]) for r in rows}
            for q in self.queries(artist, place_terms[0]):
                for h in self._memo.get(q, []):
                    url = h.get("url") if isinstance(h, dict) else None
                    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                        continue
                    for r in location_rows(artist, h, place_terms, wide, scope_label):
                        if (r["kind"], r["source_url"]) not in have:
                            have.add((r["kind"], r["source_url"]))
                            rows.append(r)
        return rows

    def search(self, artist: str, place_terms: list[str], scope_label: str, context: Optional[dict] = None) -> list[dict]:
        """Verified, unverified and rejected evidence rows for one artist in one scope."""
        context = context or {}
        place_terms = [p for p in (place_terms or []) if isinstance(p, str) and p.strip()]
        if not isinstance(artist, str) or not artist.strip() or not place_terms:
            return []  # nothing to search for: no artist or no area
        hits, seen = [], set()
        for q in self.queries(artist, place_terms[0]):
            for h in self._search(q):
                url = h.get("url") if isinstance(h, dict) else None
                if isinstance(url, str) and url.startswith(("http://", "https://")) and url not in seen:
                    seen.add(url)
                    hits.append(h)
        rows, candidates = [], []
        for h in hits:
            text = f"{h.get('title') or ''}\n{h.get('content') or ''}"
            tn = norm(text)
            if not _mentions(tn, artist) or not any(_mentions(tn, p) for p in place_terms):
                continue  # says nothing about this artist in this area
            if is_listing_page(h["url"]):
                rows.append(_row(artist, h, scope_label, "none", "rejected",
                                 "Ticket, tour-date or streaming listing page; it does not state a connection"))
                continue
            if not clean_mentions(artist, text, place_terms):
                rows.append(_row(artist, h, scope_label, "none", "rejected",
                                 f"'{artist}' only appears as part of a longer name"))
                continue
            candidates.append(h)
        if candidates:
            for h, v in zip(candidates, self._verdicts_for(artist, context, place_terms, candidates[:8])):
                rows.append(judge(artist, h, v, place_terms, scope_label))
        order = {"verified": 0, "unverified": 1, "rejected": 2}
        rank = {"based_here": 0, "formed_here": 1, "played_here": 2}
        rows.sort(key=lambda r: (order[r["status"]], rank.get(r["kind"], 3)))
        kept = [r for r in rows if r["status"] == "verified"][:4]
        kept += [r for r in rows if r["status"] == "unverified"][:2]
        kept += [r for r in rows if r["status"] == "rejected"][:self.MAX_REJECTED]
        return kept


_rec_lock = threading.Lock()


def _replay_path():
    return config.FIXTURE_DIR / "evidence_replay.json"


def _load_replay() -> dict:
    try:
        return json.loads(_replay_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"searches": {}, "verdicts": {}}


def _record(section: str, key: str, value) -> None:
    with _rec_lock:
        fx = _load_replay()
        fx.setdefault(section, {})[key] = value
        _replay_path().write_text(json.dumps(fx, indent=1), encoding="utf-8")


class ReplayEvidence(TavilyEvidence):
    """Demo provider: replays recorded searches and recorded LLM verdicts. Never invents results.
    A hit without a recorded verdict is judged by the strict sentence rule."""

    name = "replay"

    def __init__(self, fixtures: Optional[dict] = None):
        super().__init__(api_key="", verifier=lambda *a: [], budget=Budget(None))
        self.fx = fixtures if fixtures is not None else _load_replay()
        self._verdicts = dict(self.fx.get("verdicts", {}))

    @property
    def available(self) -> bool:
        return True

    def _search(self, query: str) -> list[dict]:
        return list(self.fx.get("searches", {}).get(query, []))


def get_provider(demo_mode: bool, budget: Optional[Budget] = None):
    return ReplayEvidence() if demo_mode else TavilyEvidence(budget=budget)
