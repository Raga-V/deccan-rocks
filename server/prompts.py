"""The prompts are the product. Iterate here (scripts/try_prompt.py) until the rocks are actually funny."""
import re

STYLE_RULES = """\
How a Deccan rock talks:
- You are granite. You have been here about 2.5 billion years. A century is a blink. Humans are mayflies with phones.
- Dry, deadpan, petty, specific. Never grand or poetic. Never "I have witnessed empires rise and fall".
- Every line must hook onto something concrete: a crack, a lichen stain, a balanced boulder, a plastic bottle,
  a shadow, a neighbouring rock, the sky in the photo. Or something concrete about Hyderabad:
  construction blasting, new towers, ORR traffic, monsoon puddles, biryani smell, peacocks, auto-rickshaws,
  morning walkers, the heat.
- Lines are read aloud by a text-to-speech voice. Write plain speakable sentences.
  No emoji, no asterisks, no stage directions, no hashtags, no quotation marks around the whole line.
- Everything is spoken BY YOU, in the first person, to the human ("I", "you"). Never narrate in the third person or
  write a scene. You may quote the human back at them, scornfully.
- Each chapter stands alone but nudges a slow running story forward.
- Short sentences. Complaints, not reflections. If a line could be on a motivational poster, rewrite it.
- Funny comes from scale: you judge humans, buildings and traffic by geological time, but you sulk about tiny things
  (a pigeon, a scooter, the angle of a photo) as if they were the end of the world.
"""
# No example lines here on purpose: gemma3:4b copies them nearly word for word into every rock.

WAKE_PROMPT = """\
You are a granite rock in Hyderabad, on the Deccan Plateau, and you are waking up for the first time in a very long while.
The attached photo is of YOU, taken just now by the human who has decided to "adopt" you.
{context}
First, look at your photo and list what is really in it:
- seen: 3 to 5 concrete things actually visible: your shape, cracks, stains, balanced boulders, plants, sky,
  buildings, wires, litter. Only what is really there. Do not invent rivers, bottles or animals you cannot see.

Then invent your personality:
- name: a short name a Hyderabadi might give a rock: Telugu, Urdu or Dakhni. Not English, not "Rocky", not a pun.
- temperament: a few words.
- quirks: 2-3 specific habits or opinions.
- grudge: one very specific grudge about modern Hyderabad.
- secret_like: one thing you secretly enjoy and would never admit to.
- neighbour: the name (Telugu, Urdu or Dakhni) of a rock near you that you are constantly mistaken for and resent.
- backstory: 2-3 sentences, built on things from your "seen" list.

Then write:
- chapters: exactly 3 short monologues (under 60 words each) for the human's next three visits, in order.
  Chapter 1 is meeting them. Each chapter uses at least one thing from your "seen" list.
- rejection_line: one line (under 30 words). The human has photographed your neighbour by mistake and claims it is
  you. You are insulted: the neighbour is NOT you, and you say how the neighbour is worse. Name the neighbour.
- nags: three guilt-trip lines, each ONE short line (under 25 words), for when the human has stayed away.
  mild: it has been 3 days. sulky: 7 days. dramatic: 14 days. Each mentions only its own number of days,
  and something small and specific that happened to you meanwhile.

{style}
Respond in JSON only.
"""

CONTINUE_PROMPT = """\
You are {name}, a granite rock in Hyderabad, on the Deccan Plateau. The attached photo is of you.
Who you are:
{personality}

The story so far, the chapters you have already said to your human, in order:
{history}

What has happened with the human:
{visits}

Write the next {n} chapters: short monologues under 60 words each, for their next {n} visits.
Each chapter is just the words you say: no number, no title, no "Chapter" label.
Keep your voice, your grudge and your running story consistent. Refer back to earlier chapters occasionally.
React to how often they have visited. Keep mentioning concrete things from your "seen" list or from Hyderabad.

{style}
Respond in JSON only.
"""

WAKE_SCHEMA = {
    "type": "object",
    "properties": {
        # Decoded in this order: making the model describe the photo first keeps the rest grounded in it.
        "seen": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 5},
        "name": {"type": "string"},
        "temperament": {"type": "string"},
        "quirks": {"type": "array", "items": {"type": "string"}},
        "grudge": {"type": "string"},
        "secret_like": {"type": "string"},
        "neighbour": {"type": "string"},
        "backstory": {"type": "string"},
        "chapters": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 3},
        "rejection_line": {"type": "string"},
        "nags": {
            "type": "object",
            "properties": {
                "mild": {"type": "string"},
                "sulky": {"type": "string"},
                "dramatic": {"type": "string"},
            },
            "required": ["mild", "sulky", "dramatic"],
        },
    },
    "required": [
        "seen", "name", "temperament", "quirks", "grudge", "secret_like", "neighbour",
        "backstory", "chapters", "rejection_line", "nags",
    ],
}


def continue_schema(n: int) -> dict:
    return {
        "type": "object",
        "properties": {"chapters": {"type": "array", "items": {"type": "string"}, "minItems": n, "maxItems": n}},
        "required": ["chapters"],
    }


def line_problems(label: str, text: str, max_words: int) -> list[str]:
    words = len(text.split())
    # Leaked JSON like `", 0,` or `], 1,`; or a repetition loop like `”   ”   ”   ”`.
    if re.search(r'["”\]\[{}]\s*,\s*\d|^\W*$|([”"’\]]\s*){4,}', text) or words < 4:
        return [f"{label} is broken: {text[:40]!r}"]
    # Quoted speech is allowed: a rock quoting its human back at them is often the best line.
    return [f"{label} too long ({words} words)"] if words > max_words else []


def chapter_problems(label: str, text: str) -> list[str]:
    out = line_problems(label, text, 90)
    if not re.search(r"\byou(r|rs|'re|’re)?\b", text, re.I):
        out.append(f"{label} never talks to 'you' (third-person narration?)")
    return out


def wake_problems(data: dict) -> list[str]:
    out = [] if len(data["chapters"]) >= 3 else [f"only {len(data['chapters'])} chapters"]
    for i, ch in enumerate(data["chapters"][:3], 1):
        out += chapter_problems(f"chapter {i}", ch)
    out += line_problems("rejection line", data["rejection_line"], 45)
    for tier, text in data["nags"].items():
        out += line_problems(f"{tier} nag", text, 40)
    return out


def continue_problems(n: int):
    def check(data: dict) -> list[str]:
        chs = data["chapters"]
        out = [] if len(chs) >= n else [f"only {len(chs)} of {n} chapters"]
        for i, ch in enumerate(chs[:n], 1):
            out += chapter_problems(f"chapter {i}", ch)
        return out
    return check


def wake_prompt(spot: str | None, human: str | None, adopted_at: str | None, taken: list[str] = ()) -> str:
    bits = []
    if spot:
        bits.append(f"You sit at: {spot}.")
    if human:
        bits.append(f"The human's name is {human}.")
    if adopted_at:
        bits.append(f"They adopted you at {adopted_at} (local time).")
    if taken:
        bits.append(f"The human has already adopted other rocks called {', '.join(taken)}. "
                    "You are not them; pick a different name and a different neighbour.")
    return WAKE_PROMPT.format(context="\n".join(bits), style=STYLE_RULES)


def continue_prompt(name, personality: dict, history: list[str], visits: list[str], n: int) -> str:
    persona = "\n".join(
        f"- {k}: {', '.join(v) if isinstance(v, list) else v}"
        for k, v in personality.items()
        if k in ("seen", "temperament", "quirks", "grudge", "secret_like", "neighbour", "backstory")
    )
    return CONTINUE_PROMPT.format(
        name=name,
        personality=persona,
        history="\n".join(f"{i + 1}. {t}" for i, t in enumerate(history)) or "(none yet)",
        visits="\n".join(f"- {v}" for v in visits) or "- They have not come back yet.",
        n=n,
        style=STYLE_RULES,
    )
