"""Natural-language understanding for restaurant requests.

Two interchangeable parsers produce the same ``Preferences`` object:

* ``RuleBasedParser`` (default) - deterministic slot filling with gazetteers and
  regular expressions. No network, no API key, fully reproducible, and every
  extracted slot is logged with the text span that justified it.
* ``LLMParser`` (optional, ``--nlp llm``) - asks Claude for a structured JSON
  parse and falls back to the rule-based parser on any failure.

The query the agent is built around:

    "Find a Turkish restaurant in Downtown Baltimore, MD for two people to have
     dinner under $65 on Thursday night at 7:30 pm with a table for two near a
     window with a view of the garden or the street."
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import List, Optional, Tuple

from .eventlog import EventLog
from .geo import AREA_ALIASES, canonical_area, pretty_area
from .models import WEEKDAYS, Preferences

CUISINES = [
    "turkish", "mediterranean", "greek", "lebanese", "italian", "french", "american",
    "steakhouse", "seafood", "japanese", "sushi", "ramen", "thai", "mexican", "indian",
    "chinese", "barbecue", "bbq", "vegan", "pizza", "diner",
]
CUISINE_CANON = {"bbq": "Barbecue", "sushi": "Japanese", "ramen": "Japanese"}

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "a couple": 2, "couple": 2,
}
DAY_ALIASES = {
    "monday": "Mon", "mon": "Mon", "tuesday": "Tue", "tue": "Tue", "tues": "Tue",
    "wednesday": "Wed", "wed": "Wed", "thursday": "Thu", "thu": "Thu", "thurs": "Thu",
    "friday": "Fri", "fri": "Fri", "saturday": "Sat", "sat": "Sat", "sunday": "Sun", "sun": "Sun",
}
MEAL_DEFAULT_TIME = {"breakfast": "08:30", "brunch": "11:00", "lunch": "12:30", "dinner": "19:00"}
VIEW_WORDS = ["garden", "street", "harbor", "harbour", "water", "waterfront", "skyline", "park", "courtyard"]
VIEW_CANON = {"harbour": "harbor", "waterfront": "water", "courtyard": "garden"}


class RuleBasedParser:
    name = "rule-based"

    def parse(self, text: str, log: Optional[EventLog] = None) -> Preferences:
        p = Preferences(raw_query=text)
        t = " " + re.sub(r"\s+", " ", text.strip()) + " "
        low = t.lower()
        trace: List[Tuple[str, str, str]] = []

        def found(field: str, value, span: str):
            trace.append((field, str(value), span.strip()))
            if log:
                log.info("NLP", f"slot {field} = {value!r}", evidence=span.strip(), method="rule")

        # -- cuisine ----------------------------------------------------------
        for c in CUISINES:
            m = re.search(rf"\b{c}\b", low)
            if m:
                p.cuisine = CUISINE_CANON.get(c, c.capitalize())
                found("cuisine", p.cuisine, m.group(0))
                break

        # -- location: "in <Area> <City>, <ST>" ------------------------------
        loc = re.search(
            r"\bin\s+(?P<loc>[A-Za-z][A-Za-z.' ]{2,40}?)(?:,\s*(?P<st>[A-Z]{2}))?"
            r"(?=\s+(?:for|to|under|on|at|with|near|around|tonight|this|next|by)\b|[,.]|$)",
            t,
        )
        if loc:
            phrase = loc.group("loc").strip()
            p.state = loc.group("st")
            words = phrase.split()
            # Peel the city off the end if present ("Downtown Baltimore").
            if words and words[-1].lower() in ("baltimore",):
                p.city = words[-1].capitalize()
                words = words[:-1]
            area_text = " ".join(words).lower() if words else phrase.lower()
            area = canonical_area(area_text)
            if not area:
                for alias, key in AREA_ALIASES.items():
                    if re.search(rf"\b{re.escape(alias)}\b", low):
                        area = key
                        break
            if area:
                p.area = pretty_area(area)
            found("location", f"{p.area or phrase} / {p.city} / {p.state}", loc.group(0))
        if not p.city and re.search(r"\bbaltimore\b", low):
            p.city = "Baltimore"
            if not p.area:
                for alias, key in AREA_ALIASES.items():
                    if re.search(rf"\b{re.escape(alias)}\b", low):
                        p.area = pretty_area(key)
                        break
            found("location", f"{p.area} / {p.city}", "baltimore")

        # -- party size -------------------------------------------------------
        num = r"(?P<n>\d+|" + "|".join(NUMBER_WORDS) + r")"
        m = (re.search(rf"\b(?:party of|table for|for)\s+{num}\b(?:\s+(?:people|persons|guests|diners|adults))?", low)
             or re.search(rf"\b{num}\s+(?:people|persons|guests|diners|adults)\b", low))
        if m:
            n = m.group("n")
            p.party_size = int(n) if n.isdigit() else NUMBER_WORDS[n]
            found("party_size", p.party_size, m.group(0))

        # -- budget -----------------------------------------------------------
        m = (re.search(r"\b(?:under|below|less than|no more than|up to|max(?:imum)?|within|around|about)\s*\$\s?(?P<amt>\d+(?:\.\d+)?)", low)
             or re.search(r"\$\s?(?P<amt>\d+(?:\.\d+)?)\s*(?:budget|or less|max|total|per couple|for two)", low)
             or re.search(r"\b(?:under|below|less than)\s*(?P<amt>\d+)\s*(?:dollars|bucks|usd)", low)
             or re.search(r"\$\s?(?P<amt>\d+(?:\.\d+)?)", low))
        if m:
            p.budget_total = float(m.group("amt"))
            per_person = re.search(r"(?:per person|each|a head|pp)\b", low)
            if per_person and p.party_size:
                p.budget_total *= p.party_size
                found("budget_total", p.budget_total, m.group(0) + " " + per_person.group(0))
            else:
                found("budget_total", p.budget_total, m.group(0))

        # -- day --------------------------------------------------------------
        m = re.search(r"\b(" + "|".join(sorted(DAY_ALIASES, key=len, reverse=True)) + r")\b", low)
        if m:
            p.day = DAY_ALIASES[m.group(1)]
            found("day", p.day, m.group(0))
        elif re.search(r"\btonight\b|\btoday\b", low):
            p.day = WEEKDAYS[_dt.date.today().weekday()]
            found("day", p.day, "tonight/today")
        elif re.search(r"\btomorrow\b", low):
            p.day = WEEKDAYS[(_dt.date.today().weekday() + 1) % 7]
            found("day", p.day, "tomorrow")

        # -- meal + time ------------------------------------------------------
        m = re.search(r"\b(breakfast|brunch|lunch|dinner|supper)\b", low)
        if m:
            p.meal = "dinner" if m.group(1) == "supper" else m.group(1)
            found("meal", p.meal, m.group(0))
        m = re.search(r"\b(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>a\.?m\.?|p\.?m\.?)", low)
        if m:
            h = int(m.group("h")) % 12
            if m.group("ap").startswith("p"):
                h += 12
            p.time = f"{h:02d}:{int(m.group('m') or 0):02d}"
            found("time", p.time, m.group(0))
        else:
            m = re.search(r"\b(?P<h>[01]?\d|2[0-3]):(?P<m>[0-5]\d)\b", low)
            if m:
                p.time = f"{int(m.group('h')):02d}:{m.group('m')}"
                found("time", p.time, m.group(0))
            elif p.meal in MEAL_DEFAULT_TIME:
                p.time = MEAL_DEFAULT_TIME[p.meal]
                found("time", p.time, f"default for {p.meal}")

        # -- seating & view ---------------------------------------------------
        m = re.search(r"\b(?:near|by|at|beside|next to)\s+(?:a|the)\s+window\b|\bwindow\s+(?:seat|table|side)\b|\bwindow\b", low)
        if m:
            p.window_seat = True
            found("window_seat", True, m.group(0))
        for kw in ("booth", "patio", "outdoor", "bar seating", "quiet", "private room"):
            if re.search(rf"\b{kw}\b", low):
                p.seating_notes.append(kw)
                found("seating_note", kw, kw)

        m = re.search(r"\b(?:view|views|overlooking|facing)\s+(?:of\s+)?(?P<v>(?:the\s+)?[a-z]+(?:\s*(?:,|or|and|/)\s*(?:the\s+)?[a-z]+)*)", low)
        if m:
            for w in re.findall(r"[a-z]+", m.group("v")):
                if w in VIEW_WORDS:
                    p.views.add(VIEW_CANON.get(w, w))
            if p.views:
                found("views", sorted(p.views), m.group(0))
        # "garden view", "street-facing", "garden or street view"
        m = re.search(r"(?P<v>[a-z]+(?:\s*(?:,|or|and|/)\s*(?:the\s+)?[a-z]+)*)[- ](?:view|views|facing)\b", low)
        if m:
            before = len(p.views)
            for w in re.findall(r"[a-z]+", m.group("v")):
                if w in VIEW_WORDS:
                    p.views.add(VIEW_CANON.get(w, w))
            if len(p.views) > before:
                found("views", sorted(p.views), m.group(0))

        p.extraction_trace = trace
        return p


class LLMParser:
    """Optional parser that asks Claude for a structured parse.

    Requires the ``anthropic`` package and ``ANTHROPIC_API_KEY`` (or an
    ``ant auth login`` profile). Falls back to ``RuleBasedParser`` on any error.
    """

    name = "llm"

    def __init__(self, model: str = "claude-opus-5-5"):
        self.model = model
        self.fallback = RuleBasedParser()

    def parse(self, text: str, log: Optional[EventLog] = None) -> Preferences:
        try:
            import anthropic
            from pydantic import BaseModel
        except ImportError as e:  # pragma: no cover - depends on environment
            if log:
                log.warn("NLP", f"LLM parser unavailable ({e}); using rule-based parser")
            return self.fallback.parse(text, log)

        class ParsedQuery(BaseModel):
            cuisine: Optional[str] = None
            area: Optional[str] = None
            city: Optional[str] = None
            state: Optional[str] = None
            party_size: int = 2
            budget_total_usd: Optional[float] = None
            day_of_week: Optional[str] = None      # Mon..Sun
            time_24h: Optional[str] = None         # HH:MM
            meal: Optional[str] = None
            window_seat: bool = False
            views: List[str] = []

        try:
            client = anthropic.Anthropic()
            response = client.messages.parse(
                model=self.model,
                max_tokens=1024,
                output_config={"effort": "low"},
                system=(
                    "Extract restaurant-search preferences from the user's request. "
                    "Use 3-letter weekday abbreviations (Mon..Sun), 24h HH:MM times, and lowercase "
                    "view words such as garden, street, harbor, water. budget_total_usd is the total "
                    "for the whole party."
                ),
                messages=[{"role": "user", "content": text}],
                output_format=ParsedQuery,
            )
            if response.stop_reason == "refusal":
                raise RuntimeError("model declined the request")
            parsed = response.parsed_output
        except Exception as e:  # network, auth, schema - all fall back deterministically
            if log:
                log.warn("NLP", f"LLM parse failed ({type(e).__name__}: {e}); using rule-based parser")
            return self.fallback.parse(text, log)

        p = Preferences(raw_query=text)
        p.cuisine = parsed.cuisine.capitalize() if parsed.cuisine else None
        area = canonical_area(parsed.area) if parsed.area else None
        p.area = pretty_area(area) if area else parsed.area
        p.city, p.state = parsed.city, parsed.state
        p.party_size = parsed.party_size or 2
        p.budget_total = parsed.budget_total_usd
        p.day = DAY_ALIASES.get((parsed.day_of_week or "").lower(), parsed.day_of_week)
        p.time = parsed.time_24h
        p.meal = parsed.meal
        p.window_seat = parsed.window_seat
        p.views = {VIEW_CANON.get(v.lower(), v.lower()) for v in parsed.views}
        p.extraction_trace = [(k, str(v), "llm") for k, v in parsed.model_dump().items() if v not in (None, [], False)]
        if log:
            for f, v, ev in p.extraction_trace:
                log.info("NLP", f"slot {f} = {v!r}", evidence=ev, method="llm")
        return p


def get_parser(kind: str = "rules"):
    return LLMParser() if kind == "llm" else RuleBasedParser()
