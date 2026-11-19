# 📦 Installation Guide — Windows 11

Complete step-by-step installation for the Hybrid AI Coding Agent on Windows 11.

---

## Prerequisites

| Tool | Minimum Version | Download |
|---|---|---|
| Python | 3.11+ | https://python.org/downloads |
| Node.js | 18 LTS+ | https://nodejs.org |
| Git | 2.40+ | https://git-scm.com |
| Ollama | 0.1.30+ | https://ollama.com |
| NVIDIA GPU | RTX 3050+ (optional) | — |

---

## Step 1 — Check Prerequisites

Run the automated environment checker:

```powershell
# After cloning, from the project root:
.\scripts\check_env.ps1
```

Or check manually:

```powershell
python --version          # Must be 3.11+
node --version            # Must be 18+
npm --version
git --version
ollama --version
```

If Python is missing or old:
```powershell
winget install Python.Python.3.12
```

If Node.js is missing:
```powershell
winget install OpenJS.NodeJS.LTS
```

---

## Step 2 — Clone / Download

**Option A — Git clone (recommended):**
```powershell
cd $env:USERPROFILE\Projects   # or any folder you prefer
git clone https://github.com/yourname/hybrid-coding-agent.git
cd hybrid-coding-agent
```

**Option B — Download ZIP:**
1. Download from GitHub → "Code" → "Download ZIP"
2. Extract to a folder (avoid paths with spaces)
3. Open PowerShell in that folder

---

## Step 3 — Python Virtual Environment

```powershell
# From the project root:
python -m venv .venv
.venv\Scripts\Activate.ps1
```

If script execution is blocked:
```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

Verify activation (prompt should show `(.venv)`):
```powershell
where python   # Should point to .venv\Scripts\python.exe
```

---

## Step 4 — Install Backend Dependencies

```powershell
# With .venv active:
pip install --upgrade pip
pip install -r requirements.txt
```

> **requirements.txt** includes:
> `fastapi uvicorn[standard] websockets python-dotenv pydantic google-genai openai httpx aiofiles aiosqlite python-multipart pytest pytest-asyncio`

If a package fails, install individually:
```powershell
pip install fastapi uvicorn websockets python-dotenv pydantic
pip install google-genai openai httpx aiofiles aiosqlite
pip install python-multipart pytest pytest-asyncio
```

---

## Step 5 — Install Frontend Dependencies

```powershell
cd frontend
npm install
cd ..
```

If npm is slow or fails:
```powershell
npm install --prefer-offline
# or use pnpm:
npm install -g pnpm
pnpm install
```

---

## Step 6 — Ollama Setup & Model Pull

Ollama must be running before starting the agent.

**Start Ollama (if not already running as a service):**
```powershell
Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden
```

**Pull the default coding model:**
```powershell
ollama pull qwen2.5-coder:7b
```

This downloads ~4.4 GB. Progress is shown in the terminal.

**Verify:**
```powershell
ollama list   # Should show qwen2.5-coder:7b
```

**GPU acceleration** (NVIDIA): Ollama auto-detects CUDA. Set layers in `.env`:
```
OLLAMA_NUM_GPU_LAYERS=28   # For 6 GB VRAM (RTX 3050)
```

---

## Step 7 — Configure `.env`

```powershell
Copy-Item .env.example .env
```

Open `.env` in your editor:
```powershell
notepad .env
# or: code .env
```

**Minimum required settings:**
```env
GEMINI_API_KEY=AIza...        # From aistudio.google.com
```

**Optional (for Grok escalation):**
```env
XAI_API_KEY=xai-...           # From console.x.ai
```

**Adjust for your hardware:**
```env
OLLAMA_NUM_GPU_LAYERS=28      # Reduce if VRAM < 6 GB
OLLAMA_CONTEXT_SIZE=4096      # Reduce to 2048 if RAM is tight
```

See [`docs/API_SETUP.md`](docs/API_SETUP.md) for detailed API key instructions.

---

## Step 8 — Start the Application

**Recommended — use the start script:**
```powershell
.\scripts\start.ps1
```

This will:
1. Activate `.venv` if present
2. Start the FastAPI backend on `http://127.0.0.1:8000`
3. Start the Vite frontend dev server on `http://localhost:3000`
4. Open your browser automatically

**Manual start (if the script fails):**

Terminal 1 — Backend:
```powershell
.venv\Scripts\Activate.ps1
uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

Terminal 2 — Frontend:
```powershell
cd frontend
npm run dev
```

---

## Step 9 — Verify Everything Works

1. **Backend health check:**
   ```powershell
   Invoke-RestMethod http://127.0.0.1:8000/health
   # Expected: {"status":"ok","models":{"ollama":true,"gemini":true,"grok":false}}
   ```

2. **Ollama connectivity:**
   ```powershell
   Invoke-RestMethod http://localhost:11434/api/tags
   ```

3. **Frontend:** Open `http://localhost:3000` — you should see the chat UI

4. **Run tests:**
   ```powershell
   .venv\Scripts\Activate.ps1
   pytest backend/tests/ -v
   ```

---

## Troubleshooting

See [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) for common issues.

Quick fixes:
- **Port 8000 in use:** Set `APP_PORT=8001` in `.env`
- **Ollama not found:** Ensure Ollama is installed and `ollama serve` is running
- **CUDA not detected:** Install NVIDIA CUDA Toolkit 12+
- **WebSocket errors:** Disable Windows Firewall for localhost or add an exception
