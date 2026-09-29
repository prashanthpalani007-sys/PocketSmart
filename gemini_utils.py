"""Gemini prompts, response cleanup, product links and fallback recommendations."""
import json
import os
import re
import time
from urllib.parse import quote_plus

MODELS = [m.strip() for m in os.getenv("GEMINI_MODEL", "gemini-3.8-flash").split(",") if m.strip()]

HOME_PLATFORMS = ["IKEA", "Amazon", "Flipkart"]
PARTY_PLATFORMS = ["Zomato", "Swiggy", "OYO", "Amazon"]
JEWELRY_PLATFORMS = ["Amazon", "Flipkart"]

SEARCH_URLS = {
    "amazon": "https://www.amazon.in/s?k={q}",
    "flipkart": "https://www.flipkart.com/search?q={q}",
    "ikea": "https://www.ikea.com/in/en/search/?q={q}",
}

RULES = """You are PocketSmart AI, a budget planning assistant for shoppers in India.
Rules:
- All prices are in Indian Rupees (INR) as plain numbers.
- "price" is the TOTAL cost of that line (unit price x quantity).
- The sum of every item's price MUST NOT exceed the budget. Aim to use 85-100% of it.
- "platform" must be one of: {platforms}.
- Suggest real, commonly available product types or services. Do not invent URLs.
- Return ONLY valid JSON, no markdown, in exactly this shape:
{{"summary": "one or two sentences",
 "sections": [{{"title": "category name", "allocated": 0,
   "items": [{{"name": "", "platform": "", "quantity": 1, "price": 0, "reason": "short reason"}}]}}],
 "tips": ["short money-saving tip"]}}
"""


# ---------------------------------------------------------------- Gemini call
def _is_busy(exc: Exception) -> bool:
    return any(t in str(exc) for t in ("503", "UNAVAILABLE", "429", "500", "overloaded", "RESOURCE_EXHAUSTED"))


def _other_flash_models(client, tried: list[str]) -> list[str]:
    """Ask Google which Flash models this key can use, minus the ones already tried."""
    try:
        names = [m.name.replace("models/", "") for m in client.models.list()
                 if "generateContent" in (m.supported_actions or [])]
    except Exception:
        return []
    return [n for n in names if "flash" in n and n not in tried and not any(x in n for x in ("image", "tts", "live", "audio"))][:4]


def _call_gemini(prompt: str, image_bytes: bytes | None = None, mime: str | None = None) -> str:
    from google import genai
    from google.genai import types

    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    client = genai.Client(api_key=key)
    parts = [prompt]
    if image_bytes:
        parts.insert(0, types.Part.from_bytes(data=image_bytes, mime_type=mime or "image/jpeg"))
    config = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.6)

    tried, last_error = [], None
    queue = list(MODELS)
    discovered = False
    while queue:
        model = queue.pop(0)
        tried.append(model)
        for attempt in range(2):  # two tries per model
            try:
                return client.models.generate_content(model=model, contents=parts, config=config).text
            except Exception as exc:
                last_error = exc
                if not _is_busy(exc):
                    if "404" in str(exc) or "NOT_FOUND" in str(exc):
                        print(f"[gemini_utils] {model} is not available for this key, skipping")
                        break  # move on to the next model
                    raise
                print(f"[gemini_utils] {model} busy (try {attempt + 1}/2)")
                time.sleep(2)
        if not queue and not discovered:  # every configured model failed: look for others
            discovered = True
            queue = _other_flash_models(client, tried)
            if queue:
                print(f"[gemini_utils] trying other models: {', '.join(queue)}")
    raise last_error or RuntimeError("No Gemini model could be used")


def _parse_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    return json.loads(text)


# ------------------------------------------------------------- normalisation
def _num(value, default=0.0) -> float:
    try:
        return float(str(value).replace(",", "").replace("₹", "").strip())
    except (TypeError, ValueError):
        return default


def make_link(name: str, platform: str) -> str:
    q = quote_plus(name)
    template = SEARCH_URLS.get(platform.lower())
    if template:
        return template.format(q=q)
    return "https://www.google.com/search?q=" + quote_plus(f"{name} {platform}")


def normalise(data: dict, budget: float, platforms: list[str]) -> dict:
    """Validate the AI output, coerce numbers, attach links, compute totals."""
    sections = []
    for sec in data.get("sections", []):
        items = []
        for it in sec.get("items", []):
            name = str(it.get("name", "")).strip()
            if not name:
                continue
            platform = str(it.get("platform", "")).strip()
            if platform.lower() not in [p.lower() for p in platforms]:
                platform = platforms[0]
            items.append({
                "name": name,
                "platform": platform,
                "quantity": max(1, int(_num(it.get("quantity"), 1))),
                "price": round(_num(it.get("price")), 2),
                "reason": str(it.get("reason", "")).strip(),
                "link": make_link(name, platform),
            })
        if items:
            spent = round(sum(i["price"] for i in items), 2)
            sections.append({"title": str(sec.get("title", "Items")), "allocated": round(_num(sec.get("allocated"), spent), 2),
                             "spent": spent, "items": items})
    if not sections:
        raise ValueError("AI returned no usable sections")
    total = round(sum(s["spent"] for s in sections), 2)
    return {
        "summary": str(data.get("summary", "")).strip(),
        "sections": sections,
        "tips": [str(t) for t in data.get("tips", []) if str(t).strip()][:5],
        "budget": budget,
        "total": total,
        "over_budget": total > budget * 1.02,
        "remaining": round(budget - total, 2),
    }


# ----------------------------------------------------------------- fallbacks
def _section(title, share, budget, items):
    allocated = round(budget * share, 2)
    each = round(allocated / len(items), 2)
    return {"title": title, "allocated": allocated,
            "items": [{"name": n, "platform": p, "quantity": 1, "price": each, "reason": r} for n, p, r in items]}


def fallback(category: str, budget: float, inputs: dict) -> dict:
    if category == "home":
        secs = [
            _section("Lighting", 0.20, budget, [("LED ceiling light", "Amazon", "Energy efficient"), ("Table lamp", "IKEA", "Warm accent light")]),
            _section("Fans and cooling", 0.20, budget, [("BLDC ceiling fan", "Flipkart", "Low power use")]),
            _section("Furniture", 0.45, budget, [("Dining table set", "IKEA", "Good value"), ("Storage cabinet", "Amazon", "Saves space")]),
            _section("Decor", 0.15, budget, [("Wall art set", "Amazon", "Affordable style")]),
        ]
        platforms = HOME_PLATFORMS
    elif category == "party":
        secs = [
            _section("Catering", 0.45, budget, [("Party food platters", "Zomato", "Order for your guest count")]),
            _section("Venue or stay", 0.25, budget, [("Small hall or guest rooms", "OYO", "Book early for better rates")]),
            _section("Decoration", 0.18, budget, [("Balloon and banner kit", "Amazon", "Ready to use")]),
            _section("Entertainment", 0.12, budget, [("Speaker and party lights", "Amazon", "Reusable")]),
        ]
        platforms = PARTY_PLATFORMS
    else:
        secs = [
            _section("Main piece", 0.55, budget, [("Statement necklace set", "Amazon", "Suits most outfits")]),
            _section("Earrings", 0.25, budget, [("Matching earrings", "Flipkart", "Completes the look")]),
            _section("Accessories", 0.20, budget, [("Bangles or bracelet", "Amazon", "Easy to pair")]),
        ]
        platforms = JEWELRY_PLATFORMS
    data = {"summary": "Showing a default plan because the AI could not respond. Prices are estimates.",
            "sections": secs, "tips": ["Compare prices on two platforms before buying."]}
    result = normalise(data, budget, platforms)
    result["source"] = "fallback"
    return result


# -------------------------------------------------------------------- planners
def _run(category, budget, platforms, prompt, inputs, image_bytes=None, mime=None):
    try:
        data = _parse_json(_call_gemini(RULES.format(platforms=", ".join(platforms)) + "\n" + prompt, image_bytes, mime))
        result = normalise(data, budget, platforms)
        result["source"] = "ai"
        if result["over_budget"]:  # one retry with a stricter instruction
            data = _parse_json(_call_gemini(
                RULES.format(platforms=", ".join(platforms)) + "\n" + prompt +
                f"\nYour last answer cost {result['total']}. The budget is {budget}. Reduce prices so the total is below {budget}.",
                image_bytes, mime))
            retry = normalise(data, budget, platforms)
            retry["source"] = "ai"
            result = retry
        return result
    except Exception as exc:  # missing key, network, bad JSON, quota...
        print(f"[gemini_utils] falling back: {exc}")
        return fallback(category, budget, inputs)


def home_recommendations(budget: float, inputs: dict) -> dict:
    prompt = (f"Plan a home interior purchase.\nBudget: INR {budget}\nRooms: {', '.join(inputs['rooms']) or 'general'}\n"
              f"Style: {inputs['style']}\nQuantities needed: {json.dumps(inputs['quantities'])}\n"
              "Group items into sections by category (lighting, fans, furniture, decor). Balance function, style and price.")
    return _run("home", budget, HOME_PLATFORMS, prompt, inputs)


def party_recommendations(budget: float, inputs: dict) -> dict:
    prompt = (f"Plan a party.\nTotal budget: INR {budget}\nGuests: {inputs['guests']}\nEvent type: {inputs['event_type']}\n"
              f"Venue details: {inputs['venue'] or 'not decided'}\nCity: {inputs['city'] or 'not given'}\n"
              f"Include these areas: {', '.join(inputs['services'])}.\n"
              "Split the budget across the areas in sensible proportions for this event type. Consider cost per guest.")
    return _run("party", budget, PARTY_PLATFORMS, prompt, inputs)


def jewelry_recommendations(budget: float, inputs: dict, image_bytes: bytes | None = None, mime: str | None = None) -> dict:
    prompt = (f"Recommend jewelry.\nBudget: INR {budget}\nOccasion: {inputs['occasion']}\nStyle: {inputs['style']}\n"
              f"Metal preference: {inputs['metal'] or 'any'}\n")
    if image_bytes:
        prompt += "An outfit photo is attached. Note its colours and neckline, and pick jewelry that matches. Mention this in the summary.\n"
    prompt += "Group items into sections such as necklace, earrings, bangles, rings."
    return _run("jewelry", budget, JEWELRY_PLATFORMS, prompt, inputs, image_bytes, mime)
