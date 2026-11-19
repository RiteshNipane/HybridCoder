# 🔧 Troubleshooting Guide

---

## Ollama Not Running

**Symptom**: `Connection refused` to `http://localhost:11434`

**Fix**:
```powershell
# Start Ollama manually:
ollama serve

# Or restart the Ollama service:
Restart-Service -Name "Ollama" -ErrorAction SilentlyContinue

# Check if it's running:
Invoke-RestMethod http://localhost:11434/api/tags
```

If Ollama isn't installed:
```powershell
winget install Ollama.Ollama
# Then restart PowerShell
```

---

## GPU / VRAM Issues

**Symptom**: `CUDA out of memory` / Model loads slowly / Fallback to CPU

**Diagnosis**:
```powershell
# Check GPU VRAM usage:
nvidia-smi

# Check which layers are on GPU:
# (Look at Ollama logs after running a model)
ollama run qwen2.5-coder:7b "test"
```

**Fix — Reduce GPU layers in `.env`**:
```env
# 6 GB VRAM → 28 layers
OLLAMA_NUM_GPU_LAYERS=28

# 4 GB VRAM → 18 layers
OLLAMA_NUM_GPU_LAYERS=18

# 2 GB VRAM → 8 layers
OLLAMA_NUM_GPU_LAYERS=8

# CPU only:
OLLAMA_NUM_GPU_LAYERS=0
```

**Fix — Reduce context size**:
```env
OLLAMA_CONTEXT_SIZE=2048   # down from 4096
```

**Fix — Use a smaller model**:
```powershell
ollama pull qwen2.5-coder:1.5b
```
Then update `.env`: `OLLAMA_MODEL=qwen2.5-coder:1.5b`

---

## API Key Errors

**Symptom**: `401 Unauthorized` / `Invalid API key`

**Gemini**:
```powershell
# Verify key is set:
Get-Content .env | Select-String "GEMINI_API_KEY"

# Test key directly:
$key = (Get-Content .env | Select-String "GEMINI_API_KEY").ToString().Split("=")[1]
Invoke-RestMethod -Uri "https://generativelanguage.googleapis.com/v1/models?key=$key"
```

Common causes:
- Key has trailing spaces → edit `.env` carefully
- Key was rotated → generate a new one at aistudio.google.com
- IP restrictions set on the key → remove them in Google Cloud Console

**Grok / xAI**:
```powershell
# Verify:
Get-Content .env | Select-String "XAI_API_KEY"
```

If `XAI_API_KEY` is empty, the system will skip Grok escalation and fall back to Gemini — this is normal if you don't have a Grok key.

---

## WebSocket Connection Failures

**Symptom**: Chat UI shows "Disconnected" / spinner never stops / no response

**Step 1 — Check backend is running**:
```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

**Step 2 — Check WebSocket endpoint**:
```powershell
# Using wscat (npm i -g wscat):
wscat -c ws://127.0.0.1:8000/ws/test-session
```

**Step 3 — Check browser console** (F12 → Console):
- Look for `WebSocket connection to 'ws://...' failed`
- If you see CORS errors, check `APP_HOST` in `.env`

**Step 4 — Firewall**:
```powershell
# Add firewall exception for backend port:
New-NetFirewallRule -DisplayName "Hybrid Agent Backend" `
  -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
```

**Step 5 — Restart both servers** via `.\scripts\stop.ps1` then `.\scripts\start.ps1`

---

## Port Conflicts

**Symptom**: `Address already in use` / `EADDRINUSE`

**Check what's using the port**:
```powershell
# Check port 8000 (backend):
netstat -ano | Select-String ":8000"

# Check port 3000 (frontend):
netstat -ano | Select-String ":3000"

# Kill process by PID (replace 1234):
Stop-Process -Id 1234 -Force
```

**Change ports** in `.env` and `frontend/vite.config.ts`:
```env
APP_PORT=8001
```
```typescript
// vite.config.ts
server: { port: 3001 }
```

---

## Windows Firewall Issues

**Symptom**: Backend unreachable / frontend can't connect to WebSocket

```powershell
# Allow Python through firewall:
New-NetFirewallRule -DisplayName "Python (Hybrid Agent)" `
  -Direction Inbound -Program "$env:USERPROFILE\.venv\Scripts\python.exe" `
  -Action Allow

# Or temporarily disable firewall for testing (re-enable after!):
Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled False
# ... test ...
Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled True
```

---

## Model Download Stuck / Slow

**Symptom**: `ollama pull` stalls or is very slow

```powershell
# Cancel and retry:
# Ctrl+C, then:
ollama pull qwen2.5-coder:7b

# Check Ollama service logs:
Get-EventLog -LogName Application -Source "*ollama*" -Newest 20

# Try a smaller model first to test connectivity:
ollama pull qwen2.5-coder:1.5b
```

---

## Frontend Build Errors

**Symptom**: `npm run build` or `npm run dev` fails

```powershell
# Clear cache and reinstall:
cd frontend
Remove-Item -Recurse -Force node_modules, package-lock.json
npm install

# TypeScript errors — check output carefully for the file/line
npm run build 2>&1 | Select-String "error TS"
```

---

## Python Import Errors

**Symptom**: `ModuleNotFoundError` when starting backend

```powershell
# Ensure venv is active:
.venv\Scripts\Activate.ps1

# Reinstall requirements:
pip install -r requirements.txt --force-reinstall

# Check specific package:
python -c "import fastapi; print(fastapi.__version__)"
python -c "from google import genai; print('genai ok')"
```

---

## Logs

Backend logs are written to `logs/app.log`. To view in real-time:
```powershell
Get-Content logs\app.log -Wait -Tail 50
```

Set `LOG_LEVEL=DEBUG` in `.env` for verbose output.
