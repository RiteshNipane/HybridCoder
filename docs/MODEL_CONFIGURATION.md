# Model Configuration & Optimization Guide

This document details how each of the three AI models in the hybrid architecture is configured, optimized for the Lenovo LOQ (RTX 3050 6GB VRAM, 16GB RAM), and operated.

---

## 1. Model A: Google Gemini API (Primary Expert)

- **Provider**: Google AI Studio / Google GenAI SDK (`google-genai >= 3.4.0`)
- **Default Model**: `gemini-3.5-flash-lite`
- **Configurable Models**:
  - `gemini-3.5-flash-lite`: Lower-demand model for high-volume agentic tasks and ordinary planning
  - `gemini-3.8-flash`: Higher-capability model for complex planning
- **Role**:
  - Task decomposition and architectural planning
  - Multi-file dependency analysis
  - Second-opinion test failure diagnosis
  - Final security and quality review of git diffs

### Configuration (`.env`)
```bash
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-3.5-flash-lite
GEMINI_TIMEOUT=180
GEMINI_MAX_RETRIES=1
```

---

## 2. Model B: xAI Grok API (Secondary Expert & Reviewer)

- **Provider**: xAI Console
- **Default Model**: `grok-4.7`
- **Base URL**: `https://api.x.ai/v1`
- **Role**:
  - Independent architectural critique
  - Challenging questionable design decisions
  - High-tier debugging escalation when tests fail repeatedly
  - Fallback expert when Gemini is unavailable

### Configuration (`.env`)
```bash
XAI_API_KEY=your_xai_api_key_here
XAI_BASE_URL=https://api.x.ai/v1
XAI_MODEL=grok-4.7
XAI_TIMEOUT=120
XAI_MAX_RETRIES=3
```

---

## 3. Model C: Local Qwen2.5-Coder 7B (Offline Worker)

- **Runtime**: Ollama for Windows (v0.40.16)
- **Model Tag**: `qwen2.5-coder:7b`
- **Quantization**: 4-bit GGUF (`Q4_K_M`)
- **VRAM Allocation (Measured)**:
  - Total VRAM: 6144 MiB (RTX 3050 Laptop GPU)
  - VRAM used by model & context: ~5039 MiB
  - Headroom remaining: ~960-1100 MiB (prevents CUDA out-of-memory errors alongside Windows desktop apps)
- **Concurrency**: Restricted to 1 to prevent VRAM spikes
- **Context Window**: 4096 tokens (configured via Ollama options)
- **Role**:
  - Code generation and file edits
  - Unit test writing
  - Subtask execution planned by Gemini/Grok
  - Routine debugging and refactoring

### Configuration (`.env`)
```bash
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5-coder:7b
OLLAMA_TIMEOUT=180
```

---

## 4. Intelligent Routing Modes

| Mode | Planning | Implementation | Review / Escalation | Cost Profile |
|------|----------|----------------|---------------------|--------------|
| **AUTO** | Gemini 3.8 Flash | Local Qwen 7B | Gemini / Grok (on test failure) | Ultra-low (mostly local execution) |
| **GEMINI** | Gemini 3.8 Flash | Gemini 3.8 Flash | Gemini 3.8 Flash | Standard cloud API |
| **GROK** | Grok 4.7 | Grok 4.7 | Grok 4.7 | Premium cloud API |
| **LOCAL** | Local Qwen 7B | Local Qwen 7B | Local Qwen 7B | $0.00 (100% offline) |
| **GEMINI+LOCAL** | Gemini 3.8 Flash | Local Qwen 7B | Gemini 3.8 Flash | Minimal |
| **GROK+LOCAL** | Grok 4.7 | Local Qwen 7B | Grok 4.7 | Minimal |
| **ALL** | Gemini 3.8 Flash | Local Qwen 7B | Grok 4.7 + Gemini | Comprehensive audit |

---

## 5. Hardware Safety Guarantees

1. **VRAM Safety**: Concurrency is limited to 1 execution at a time. The local model's KV cache is bound to 4096 tokens to ensure GPU memory never exceeds 5.2GB.
2. **Reduced Motion / Background Throttle**: The ASCII Things canvas pauses automatically when the browser tab is hidden (`document.hidden`), releasing GPU cycles back to the system.
3. **Workspace Boundary**: Path traversal attacks (`../`) are strictly blocked at the security guard layer before filesystem tools touch the disk.
4. **Git Safety Checkpoints**: The agent stashes uncommitted user work before running complex multi-file edits, guaranteeing safe rollbacks.

