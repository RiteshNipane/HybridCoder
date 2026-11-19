# 🔑 API Setup Guide

This guide explains how to obtain and configure API keys for Gemini and xAI Grok, plus how to set up the local Ollama model.

---

## 1. Google Gemini API Key

### Get Your Key

1. Go to **[Google AI Studio](https://aistudio.google.com)**
2. Sign in with your Google account
3. Click **"Get API key"** in the top-left sidebar
4. Click **"Create API key in new project"** (or select an existing project)
5. Copy the key — it starts with `AIza...`

### Free Tier Limits (as of 2025)
- **gemini-3.8-flash**: 1,500 requests/day, 1M tokens/min
- **gemini-3.5-flash-lite**: 1,500 requests/day
- Paid tier removes limits (pay-per-token)

### Configure
Add to your `.env`:
```env
GEMINI_API_KEY=AIzaSy...your-key-here...
GEMINI_MODEL=gemini-3.5-flash-lite
```

Available models:
- `gemini-4-argon` — Most capable, higher cost
- `gemini-3.5-flash-lite` — Default, lower-demand model for high-volume agentic tasks
- `gemini-3.8-flash` — Higher-capability option for complex planning

> **Note**: The Gemini API key is **required**. The app will warn on startup if missing but will still work for local-only tasks.

---

## 2. xAI / Grok API Key

> **Optional** — Only needed for the escalation tier. The app works without it.

### Get Your Key

1. Go to **[xAI API Console](https://console.x.ai)**
2. Sign in or create an account (requires phone verification)
3. Navigate to **API Keys** in the left menu
4. Click **"Create new API key"**
5. Give it a name (e.g., `hybrid-agent-local`)
6. Copy the key — it starts with `xai-...`

### Pricing
- Grok 4.7: Check https://console.x.ai for current pricing
- Credits are available on new accounts

### Configure
Add to your `.env`:
```env
XAI_API_KEY=xai-...your-key-here...
XAI_BASE_URL=https://api.x.ai/v1
XAI_MODEL=grok-4.7
```

---

## 3. Ollama Local Model Setup

Ollama runs models locally — no API key needed.

### Install Ollama

```powershell
# Windows: Download installer from https://ollama.com
# Or via winget:
winget install Ollama.Ollama
```

After installation, Ollama runs as a background service automatically.

### Pull the Recommended Model

```powershell
ollama pull qwen2.5-coder:7b
```

| Model | VRAM Required | Quality |
|---|---|---|
| `qwen2.5-coder:1.5b` | ~1.5 GB | Basic |
| `qwen2.5-coder:7b` | ~5.5 GB | **Recommended** ✅ |
| `qwen2.5-coder:14b` | ~10 GB | High |
| `codellama:7b` | ~5.5 GB | Alternative |

### GPU Configuration

The `.env` controls GPU layer offloading:
```env
OLLAMA_NUM_GPU_LAYERS=28    # RTX 3050 6GB — good for 7B model
OLLAMA_CONTEXT_SIZE=4096    # Context window (reduce if OOM)
```

**Layer guide for 6 GB VRAM:**
- `28` layers → ~5.5 GB VRAM, fast
- `20` layers → ~4 GB VRAM, slightly slower
- `0` → CPU only, ~2 tokens/sec

### Verify Ollama is Working

```powershell
# Check running models:
ollama list

# Test inference directly:
ollama run qwen2.5-coder:7b "Write a Python hello world"

# Check API endpoint:
Invoke-RestMethod http://localhost:11434/api/tags
```

---

## Security Reminders

- Never share or commit your API keys
- `.env` is in `.gitignore` by default
- Use environment variables, never hardcode keys in source files
- Rotate keys immediately if accidentally exposed
- Set `APP_DEBUG=false` in production
