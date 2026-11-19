# Changes in this build

## Live "which model is answering" indicator
- `backend/activity.py`: every provider call (Gemini / NVIDIA / Ollama) reports `thinking -> generating -> done/failed`.
  `get_active_model()` returns the model working right now (or None); also `GET /api/active-model`.
- WebSocket forwards `model_activity` events to the UI while the answer is produced.
- UI: a chip in the status banner shows the live chain, e.g. `NVIDIA ✗ -> Gemini ● writing…` (so you can see fallbacks happen).
  The sidebar row of the working provider pulses "Working…". Frontend helper: `useActiveModel()` / `getActiveModel()`.
- Sidebar label "Grok 4.7" corrected to "NVIDIA Llama 70B" (that slot is NVIDIA NIM).

## Smart prompt routing (AUTO mode)
Normal questions used to be treated as projects: with a workspace open, *every* message went to
plan -> subtasks -> code changes. Now `backend/routing/intent.py` classifies each prompt first:

| Intent | Example | What happens |
|---|---|---|
| chat | "what is gravity?" | direct answer (NVIDIA -> Gemini -> local), no plan, no repo scan |
| code_question | "what does orchestrator.py do?" | direct answer using repo context |
| snippet | "write a python function to reverse a string" | local Qwen writes it in chat, no file edits |
| task | "fix the bug in escalation.py" | full plan -> code -> test -> review workflow |

Ambiguous prompts get a quick one-word model tie-break (6s timeout, falls back to the rules).
Force a path with a prefix: `/ask`, `/explain`, `/code`, `/plan`.
Also fixed: `plan_task()` returned one list but the orchestrator unpacked two values
(`too many values to unpack (expected 2)`).

## Frontend
- globals.css: removed the `* {margin:0;padding:0}` reset that wiped out Tailwind v4 spacing; added `@plugin` typography + `@theme` cyan colours.
- AsciiBackground: looping scene (fade out / respawn every 25s), real-size canvas, brighter, calmer speed, vignette for readability.
- ChatArea / ChatMessage: centred layout, scene switcher + pause button in header, nicer hero + message bubbles.
- useWebSocket: connects ONCE (it used to reconnect when the conversation changed and dropped the first message); resets spinner on disconnect; errors now appear in the chat.
- api.ts: if the socket is not open, show an error instead of hanging on "Planning...".
- useChat: streaming/stop-button state now follows the agent status.
- store: default asciiSpeed 5 -> 1, default scene is now Lagoon.
- Scenes: removed City and Servers; added the Lagoon family (Day, Night, Dolphins, Manta Rays, Sea Turtles, Seahorses, Hammerheads, Tang) and Koi Pond. Old saved settings fall back to Lagoon.
- Sidebar: live VRAM (polls /api/gpu every 2s) with a colour-coded bar, GPU utilisation, temperature and the model Ollama has loaded.

## Backend
- Provider order is now NVIDIA first, Gemini as backup, local Qwen last (expert_agent, orchestrator, reviewer, model_router).
- GET /api/gpu: lightweight live GPU stats; nvidia-smi now runs in a worker thread so it never blocks the server.
- escalation.py: accepts `hint_chain` (orchestrator was passing it but it was ignored).

## Scripts / config
- start.ps1: launches `npm.cmd` (fixes the Windows Start-Process error).
- requirements.txt added (was missing).
- .env.example: added GEMINI_/NVIDIA_/OLLAMA_ timeout + retry variables the code really reads.
- The real .env is NOT included. Copy .env.example to .env and add your own keys.
