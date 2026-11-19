"""
Run from the project root (venv active):   python scripts/diagnose_providers.py
Calls Gemini, NVIDIA and Ollama through the app's own provider code and prints the
exact error for each. If a cloud model is rejected (404 / 410 / retired), it lists the
model names YOUR key can actually use, so you can copy one into .env.
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import get_config  # noqa: E402


async def probe(name, factory):
    t0 = time.monotonic()
    try:
        provider = factory()
        text, usage = await provider.complete("Reply with the single word: OK", max_tokens=20, temperature=0.0)
        print(f"[PASS] {name:7} {time.monotonic()-t0:5.1f}s  model={usage.model}  reply={text.strip()[:40]!r}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] {name:7} {time.monotonic()-t0:5.1f}s  {type(e).__name__}: {str(e)[:400]}")
        return False


def list_gemini_models(cfg):
    try:
        from google import genai
        client = genai.Client(api_key=cfg.gemini_api_key)
        names = []
        for m in client.models.list():
            acts = getattr(m, "supported_actions", None) or []
            if "generateContent" in acts:
                names.append(m.name.replace("models/", ""))
        flash = [n for n in names if "flash" in n and "tts" not in n and "image" not in n]
        print("\n  Gemini models your key can use (set GEMINI_MODEL to one of these):")
        for n in (flash or names)[:20]:
            print("   -", n)
    except Exception as e:  # noqa: BLE001
        print(f"  (could not list Gemini models: {e})")


def list_nvidia_models(cfg):
    try:
        import httpx
        key = cfg.nvidia_api_key or cfg.xai_api_key
        r = httpx.get(f"{cfg.nvidia_base_url.rstrip('/')}/models",
                      headers={"Authorization": f"Bearer {key}"}, timeout=30)
        r.raise_for_status()
        ids = sorted(m["id"] for m in r.json().get("data", []))
        pref = [i for i in ids if any(k in i.lower() for k in ("llama", "qwen", "mistral", "deepseek", "nemotron", "gpt-oss"))
                and not any(k in i.lower() for k in ("embed", "vision", "guard", "reward", "rerank"))]
        print("\n  NVIDIA chat models your key can use (set NVIDIA_MODEL to one of these):")
        for n in (pref or ids)[:40]:
            print("   -", n)
    except Exception as e:  # noqa: BLE001
        print(f"  (could not list NVIDIA models: {e})")


async def main():
    cfg = get_config()
    print(f"Gemini key set: {cfg.gemini_available}   NVIDIA key set: {cfg.nvidia_available}")
    print(f"Models -> gemini={cfg.gemini_model}  nvidia={cfg.nvidia_model}  ollama={cfg.ollama_model}\n")

    if cfg.gemini_available:
        from backend.providers.gemini_provider import GeminiProvider
        if not await probe("gemini", lambda: GeminiProvider(cfg)):
            list_gemini_models(cfg)
    else:
        print("[SKIP] gemini  no GEMINI_API_KEY in .env")

    if cfg.nvidia_available:
        from backend.providers.nvidia_provider import NvidiaProvider
        if not await probe("nvidia", lambda: NvidiaProvider(cfg)):
            list_nvidia_models(cfg)
    else:
        print("[SKIP] nvidia  no NVIDIA_API_KEY in .env")

    from backend.providers.ollama_provider import OllamaProvider
    await probe("ollama", lambda: OllamaProvider(cfg))


asyncio.run(main())
