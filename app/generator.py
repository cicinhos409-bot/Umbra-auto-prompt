import base64
import json
import os
import random
import re

from .db import IMG_DIR

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
MOCK = os.environ.get("MOCK_LLM") == "1"

ANGLES = [
    "unboxing reveal with hands opening the package",
    "macro close-up of fine details (paint, wheels, grille, headlights)",
    "hands rolling / pushing the product across a table (friction motion)",
    "lights and sound demo in a dim room, glowing headlights",
    "collector shelf display with slow orbit camera",
    "kid's room playtime scene, warm natural light",
    "gift reveal, birthday or Father's Day mood",
    "dramatic studio shot on a rotating turntable, rim lighting",
    "doors, hood or cabin opening close-up",
    "tiny-scale road scene with the miniature on a realistic surface",
    "side-by-side with a coin or hand to show real size",
    "fast-cut energetic hook with a punch-in zoom",
]

SYSTEM = """You write prompts for AI video generation tools (text-to-video / image-to-video) used to make short vertical ads (9:16) for e-commerce products.

Rules:
- Describe the product ONLY as it appears in the product data and reference images. Never invent features, brands, logos, text on screen or claims.
- Each prompt must be concrete and visual: subject, action, camera movement, lighting, environment, mood, pacing. No hashtags, no commentary.
- Every prompt must work on its own when pasted into the tool (restate the product briefly).
- Do not repeat ideas listed under PREVIOUS PROMPTS.
- Return ONLY valid JSON, exactly: {"prompts": ["...", "..."]} with the requested number of items."""


def _images_content(product: dict, limit: int = 3):
    blocks = []
    for name in json.loads(product.get("images") or "[]")[:limit]:
        path = os.path.join(IMG_DIR, os.path.basename(name))
        if not os.path.isfile(path):
            continue
        ext = path.rsplit(".", 1)[-1].lower()
        media = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(ext)
        if not media:
            continue
        with open(path, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode()
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": media, "data": data}})
    return blocks


def build_user_text(product: dict, count: int, seconds: int, mode: str, language: str,
                    target_tool: str, style: str, extra: str, history: list[str]) -> str:
    lang = {"en": "English", "pt": "Brazilian Portuguese", "pt-br": "Brazilian Portuguese"}.get(
        (language or "en").lower(), language or "English")
    lines = [
        "PRODUCT",
        f"Name: {product['name']}",
        f"Description: {product.get('description') or '-'}",
        f"Benefits / selling points: {product.get('benefits') or '-'}",
        f"Price: {product.get('price') or '-'}",
        f"Target audience: {product.get('audience') or '-'}",
        f"Notes: {product.get('notes') or '-'}",
        "",
        "TASK",
        f"Write {count} prompt(s), each for a clip of exactly {seconds} seconds. Language: {lang}.",
    ]
    if mode == "sequence":
        lines.append(
            f"The {count} prompts are consecutive shots of ONE ad ({count * seconds}s total). "
            "Shot 1 is a strong hook; the last shot is a closing shot that makes people want the product. "
            "Keep product, colors, lighting and setting consistent across shots; shots after the first start "
            "from where the previous one ended."
        )
    else:
        angles = random.sample(ANGLES, k=min(count, len(ANGLES)))
        lines.append(
            f"The {count} prompts are INDEPENDENT clips with clearly different concepts. Suggested angles "
            "(use them or better ones): " + "; ".join(angles) + "."
        )
    if target_tool:
        lines.append(f"Target tool: {target_tool} (match its prompt style).")
    if style:
        lines.append(f"Visual style: {style}")
    if extra:
        lines.append(f"Extra instructions: {extra}")
    if history:
        lines += ["", "PREVIOUS PROMPTS (do not repeat these ideas):"] + [f"- {h}" for h in history]
    return "\n".join(lines)


def parse_prompts(raw: str, count: int) -> list[str]:
    start, end = raw.find("{"), raw.rfind("}")
    items = None
    if start != -1 and end > start:
        try:
            items = json.loads(raw[start:end + 1]).get("prompts")
        except Exception:
            items = None
    if items is None:
        m = re.search(r"\[.*\]", raw, re.S)
        if m:
            try:
                items = json.loads(m.group(0))
            except Exception:
                items = None
    if not isinstance(items, list):
        raise ValueError("A IA não devolveu JSON válido: " + raw[:200])
    out = [str(i).strip() for i in items if str(i).strip()]
    if not out:
        raise ValueError("A IA devolveu lista vazia")
    return out[:count]


def generate_prompts(product: dict, count: int, seconds: int, mode: str = "separate",
                     language: str = "en", target_tool: str = "", style: str = "",
                     extra: str = "", history: list[str] | None = None) -> list[str]:
    history = history or []
    if MOCK:
        return [f"[MOCK] {product['name']} — clip {i + 1}/{count}, {seconds}s, mode={mode}" for i in range(count)]

    import anthropic
    client = anthropic.Anthropic()
    content = _images_content(product)
    content.append({"type": "text", "text": build_user_text(
        product, count, seconds, mode, language, target_tool, style, extra, history)})
    msg = client.messages.create(
        model=MODEL, max_tokens=4000, system=SYSTEM,
        messages=[{"role": "user", "content": content}],
    )
    raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    return parse_prompts(raw, count)
