"""
backend/main.py
FastAPI entry point for the Hybrid AI Coding Agent.
Provides WebSocket for streaming agent interactions, and REST endpoints for
workspace management, system health, and usage tracking.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend import activity
from backend.config import Config, RoutingMode, get_config
from backend.orchestrator import CodingOrchestrator
from backend.schemas.task import ChatRequest, ChatResponse, ConversationMessage, MessageRole, WorkspaceSetRequest
from backend.storage.usage_tracker import UsageTracker
from backend.tools.filesystem import delete_file, list_files, read_file, write_file
from backend.tools.terminal import run_command

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global Instances & Lifespan
# ---------------------------------------------------------------------------
cfg = get_config()
usage_tracker = UsageTracker(cfg)
orchestrator = CodingOrchestrator(config=cfg, usage_tracker=usage_tracker)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup and shutdown lifecycle."""
    logger.info("Initializing Hybrid Coding Agent backend...")
    await usage_tracker.initialise()
    # Check default workspace
    if cfg.workspace_root.exists():
        try:
            await orchestrator.set_workspace(cfg.workspace_root)
        except Exception as e:
            logger.warning("Could not initialize default workspace: %s", e)
    yield
    logger.info("Shutting down Hybrid Coding Agent backend...")


app = FastAPI(
    title="Hybrid Autonomous AI Coding Agent",
    version="1.0.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS Middleware
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow browser UI from localhost:3000, 5173, etc.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# WebSocket Endpoint (Streaming Chat & Execution)
# ---------------------------------------------------------------------------
def _json_safe(obj):
    """Make any event JSON-serialisable (datetimes, enums, exceptions...) so a send can never crash the socket."""
    import json as _json
    return _json.loads(_json.dumps(obj, default=str))


async def _safe_send(websocket: WebSocket, payload: dict) -> None:
    try:
        await websocket.send_json(_json_safe(payload))
    except WebSocketDisconnect:
        raise
    except Exception as exc:  # serialisation problem: tell the UI instead of silently dying
        logger.exception("Failed to send WS event %s: %s", payload.get("type"), exc)
        await websocket.send_json({"type": "error", "error": f"Internal error sending '{payload.get('type')}': {exc}",
                                   "conversationId": payload.get("conversationId")})


@app.websocket("/ws")
async def websocket_chat_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    logger.info("WebSocket client connected")
    receive_task: Optional[asyncio.Task[str]] = asyncio.create_task(websocket.receive_text())
    try:
        while True:
            assert receive_task is not None
            data = await receive_task
            receive_task = asyncio.create_task(websocket.receive_text())
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "error": "Invalid JSON payload"})
                continue

            msg_type = payload.get("type", "chat")

            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            if msg_type == "stop":
                conv_id = payload.get("conversationId") or payload.get("conversation_id")
                await _safe_send(websocket, {
                    "type": "cancelled",
                    "conversationId": conv_id,
                    "was_running": False,
                })
                continue

            if msg_type in ("chat", "send_message"):
                prompt = payload.get("content") or payload.get("message", "")
                mode_str = payload.get("model_mode") or payload.get("modelMode", "AUTO")
                ws_path = payload.get("workspace_path") or payload.get("workspacePath")
                conv_id = payload.get("conversationId") or payload.get("conversation_id", "default")
                raw_history = payload.get("history") or payload.get("conversation_history") or []
                try:
                    # Only accept plain conversational turns. Tool events and system
                    # messages are UI metadata, not model conversation history.
                    history = [
                        ConversationMessage(role=MessageRole(item["role"]), content=str(item["content"]))
                        for item in raw_history[-40:]
                        if isinstance(item, dict)
                        and item.get("role") in (MessageRole.USER.value, MessageRole.ASSISTANT.value)
                        and item.get("content")
                    ]
                except (TypeError, ValueError, KeyError) as exc:
                    await websocket.send_json({"type": "error", "error": f"Invalid conversation history: {exc}", "conversationId": conv_id})
                    continue

                if ws_path and (not orchestrator.workspace_root or str(orchestrator.workspace_root) != ws_path):
                    try:
                        await orchestrator.set_workspace(ws_path)
                    except Exception as e:
                        await websocket.send_json({"type": "error", "error": f"Failed setting workspace: {e}", "conversationId": conv_id})

                try:
                    mode = RoutingMode(mode_str)
                except ValueError:
                    mode = RoutingMode.AUTO

                # Run the orchestrator in a task and merge two streams into the socket:
                #  - normal chunks (status / text / done / error)
                #  - live "model_activity" events (which model is working right now)
                queue: asyncio.Queue = asyncio.Queue()
                token = activity.set_listener(lambda ev: queue.put_nowait(("model", ev)))

                async def _pump() -> None:
                    try:
                        async for chunk in orchestrator.handle_user_message_stream(
                            prompt=prompt,
                            conversation_history=history,
                            mode_override=mode,
                        ):
                            await queue.put(("chunk", chunk))
                    except Exception as exc:  # reported to the UI below
                        logger.exception("Error during streaming workflow: %s", exc)
                        await queue.put(("error", exc))
                    finally:
                        await queue.put(("end", None))

                pump_task = asyncio.create_task(_pump())  # inherits the listener context
                activity.reset_listener(token)
                queue_task: Optional[asyncio.Task] = asyncio.create_task(queue.get())
                cancelled = False
                try:
                    while True:
                        assert queue_task is not None and receive_task is not None
                        completed, _ = await asyncio.wait(
                            {queue_task, receive_task},
                            return_when=asyncio.FIRST_COMPLETED,
                        )

                        # Drain generated events first if both the stream and
                        # socket receive completed in the same event-loop turn.
                        if queue_task in completed:
                            kind, chunk = queue_task.result()
                            if kind == "end":
                                break
                            if kind == "model":
                                await _safe_send(websocket, {"type": "model_activity", "conversationId": conv_id, **chunk})
                            elif kind == "chunk":
                                chunk["conversationId"] = conv_id
                                await _safe_send(websocket, chunk)
                            elif kind == "error":
                                await _safe_send(websocket, {"type": "error", "error": str(chunk), "conversationId": conv_id})
                            queue_task = asyncio.create_task(queue.get())

                        if receive_task in completed:
                            incoming = receive_task.result()
                            receive_task = asyncio.create_task(websocket.receive_text())
                            try:
                                control = json.loads(incoming)
                            except json.JSONDecodeError:
                                await _safe_send(websocket, {"type": "error", "error": "Invalid JSON payload", "conversationId": conv_id})
                                continue

                            control_type = control.get("type", "chat")
                            if control_type == "ping":
                                await _safe_send(websocket, {"type": "pong"})
                            elif control_type == "stop":
                                requested_conv = control.get("conversationId") or control.get("conversation_id")
                                if not requested_conv or requested_conv == conv_id:
                                    pump_task.cancel()
                                    await asyncio.gather(pump_task, return_exceptions=True)
                                    cancelled = True
                                    await _safe_send(websocket, {
                                        "type": "cancelled",
                                        "conversationId": conv_id,
                                        "was_running": True,
                                    })
                                    break
                            elif control_type in ("chat", "send_message"):
                                waiting_conv = control.get("conversationId") or control.get("conversation_id", "default")
                                await _safe_send(websocket, {
                                    "type": "busy",
                                    "conversationId": waiting_conv,
                                    "message": "A request is already running. Stop it or wait for it to finish before sending another.",
                                })
                finally:
                    if queue_task is not None and not queue_task.done():
                        queue_task.cancel()
                        await asyncio.gather(queue_task, return_exceptions=True)
                    if not pump_task.done():
                        pump_task.cancel()
                        await asyncio.gather(pump_task, return_exceptions=True)

                if cancelled:
                    logger.info("Cancelled active chat generation for conversation %s", conv_id)

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error("Unexpected WebSocket error: %s", e)
    finally:
        if receive_task is not None and not receive_task.done():
            receive_task.cancel()
            await asyncio.gather(receive_task, return_exceptions=True)


# ---------------------------------------------------------------------------
# REST Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
@app.get("/api/health")
async def health_check() -> Dict[str, Any]:
    return {"status": "ok", "timestamp": str(asyncio.get_event_loop().time())}


def _read_gpu_sync() -> Dict[str, Any]:
    """Query nvidia-smi once (blocking). Runs in a worker thread, never on the event loop."""
    info: Dict[str, Any] = {
        "available": False, "name": "N/A",
        "vram_total_mb": 0, "vram_used_mb": 0,
        "utilization_pct": None, "temperature_c": None,
    }
    smi = shutil.which("nvidia-smi")
    if not smi:
        return info
    try:
        res = subprocess.run(
            [smi, "--query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
        )
        if res.returncode == 0 and res.stdout.strip():
            first = res.stdout.strip().splitlines()[0]  # first GPU only
            p = [x.strip() for x in first.split(",")]
            def _num(v: str):
                try:
                    return int(float(v))
                except ValueError:
                    return None
            if len(p) >= 3:
                info.update({
                    "available": True, "name": p[0],
                    "vram_total_mb": _num(p[1]) or 0,
                    "vram_used_mb": _num(p[2]) or 0,
                    "utilization_pct": _num(p[3]) if len(p) > 3 else None,
                    "temperature_c": _num(p[4]) if len(p) > 4 else None,
                })
    except Exception:
        pass
    return info


async def _read_ollama_loaded() -> List[Dict[str, Any]]:
    """Models currently loaded in Ollama and how much VRAM each is using."""
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            r = await client.get(f"{cfg.ollama_base_url.rstrip('/')}/api/ps")
            if r.status_code == 200:
                return [
                    {
                        "name": m.get("name"),
                        "size_mb": int((m.get("size") or 0) / 1_048_576),
                        "vram_mb": int((m.get("size_vram") or 0) / 1_048_576),
                    }
                    for m in r.json().get("models", [])
                ]
    except Exception:
        pass
    return []


@app.get("/api/active-model")
async def get_active_model_endpoint() -> Dict[str, Any]:
    """Which model is working on an answer right now (null when idle)."""
    return {"active": activity.get_active_model()}


@app.get("/api/gpu")
async def get_gpu_status() -> Dict[str, Any]:
    """Lightweight live GPU stats for the sidebar (polled every ~2s)."""
    gpu = await asyncio.to_thread(_read_gpu_sync)
    gpu["ollama_loaded"] = await _read_ollama_loaded()
    return gpu


@app.get("/api/status")
async def get_system_status() -> Dict[str, Any]:
    """Inspect all 3 models, Ollama connectivity, and GPU VRAM usage."""
    # Check Ollama
    ollama_ok = False
    ollama_models = []
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{cfg.ollama_base_url.rstrip('/')}/api/tags")
            if resp.status_code == 200:
                ollama_ok = True
                ollama_models = [m.get("name") for m in resp.json().get("models", [])]
    except Exception:
        ollama_ok = False

    gpu_info = await asyncio.to_thread(_read_gpu_sync)

    return {
        "providers": {
            "gemini": {
                "configured": cfg.gemini_available,
                "model": cfg.gemini_model,
            },
            "nvidia": {
                "configured": cfg.nvidia_available,
                "model": cfg.nvidia_model,
            },
            "grok": {
                "configured": cfg.nvidia_available,
                "model": cfg.nvidia_model,
            },
            "ollama": {
                "running": ollama_ok,
                "model": cfg.ollama_model,
                "available_models": ollama_models,
            },
        },
        "hardware": {
            "gpu": gpu_info,
        },
        "active_workspace": str(orchestrator.workspace_root) if orchestrator.workspace_root else None,
    }


@app.post("/api/workspace/set")
async def set_workspace_endpoint(body: WorkspaceSetRequest) -> Dict[str, Any]:
    try:
        repo_ctx = await orchestrator.set_workspace(body.path)
        return {
            "status": "success",
            "root": repo_ctx.root,
            "languages": repo_ctx.languages,
            "frameworks": repo_ctx.frameworks,
            "file_count": len(repo_ctx.files),
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


def _choose_workspace_directory(initial_dir: str) -> str:
    """Open the host's native folder picker (the backend runs on the user's PC)."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        root.update()
        return filedialog.askdirectory(
            title="Select workspace folder",
            initialdir=initial_dir,
            mustexist=True,
            parent=root,
        )
    finally:
        root.destroy()


@app.get("/api/workspace/select")
async def select_workspace_endpoint() -> Dict[str, Any]:
    """Show a native folder picker and make the chosen directory the workspace."""
    initial = str(orchestrator.workspace_root or cfg.workspace_root)
    try:
        selected = await asyncio.to_thread(_choose_workspace_directory, initial)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not open the folder picker: {exc}")
    if not selected:
        return {"cancelled": True}
    try:
        repo_ctx = await orchestrator.set_workspace(selected)
        return {
            "cancelled": False,
            "root": repo_ctx.root,
            "languages": repo_ctx.languages,
            "frameworks": repo_ctx.frameworks,
            "file_count": len(repo_ctx.files),
            "files": [f.path for f in repo_ctx.files[:200]],
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/workspace/scan")
async def scan_workspace_endpoint() -> Dict[str, Any]:
    if not orchestrator.workspace_root:
        raise HTTPException(status_code=400, detail="No workspace set")
    repo_ctx = await orchestrator.scan_workspace()
    if not repo_ctx:
        raise HTTPException(status_code=500, detail="Failed to scan workspace")
    return {
        "root": repo_ctx.root,
        "languages": repo_ctx.languages,
        "frameworks": repo_ctx.frameworks,
        "entry_points": repo_ctx.entry_points,
        "file_count": len(repo_ctx.files),
        "files": [f.path for f in repo_ctx.files[:200]],  # cap list for payload
    }


@app.get("/api/usage")
async def get_usage_endpoint() -> Dict[str, Any]:
    summary = await usage_tracker.get_summary()
    return summary.model_dump()


# ---------------------------------------------------------------------------
# Direct File Management & Terminal Endpoints (Claude Code style)
# ---------------------------------------------------------------------------

class FileWriteRequest(BaseModel):
    path: str
    content: str


class FileDeleteRequest(BaseModel):
    path: str


class TerminalRunRequest(BaseModel):
    command: str
    cwd: Optional[str] = None
    timeout: Optional[int] = 30


@app.get("/api/files/tree")
async def get_files_tree_endpoint() -> Dict[str, Any]:
    ws = orchestrator.workspace_root or cfg.workspace_root
    try:
        entries = await list_files(ws)
        return {"root": str(ws), "files": entries}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/files/read")
async def read_file_endpoint(path: str) -> Dict[str, Any]:
    ws = orchestrator.workspace_root or cfg.workspace_root
    try:
        content = await read_file(path, ws)
        return {"path": path, "content": content}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/files/write")
async def write_file_endpoint(body: FileWriteRequest) -> Dict[str, Any]:
    ws = orchestrator.workspace_root or cfg.workspace_root
    try:
        result = await write_file(body.path, body.content, ws)
        return {"status": "success", **result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/files/delete")
async def delete_file_endpoint(body: FileDeleteRequest) -> Dict[str, Any]:
    ws = orchestrator.workspace_root or cfg.workspace_root
    try:
        result = await delete_file(body.path, ws, require_approval=False)
        return {"status": "success", **result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/terminal/run")
async def run_terminal_endpoint(body: TerminalRunRequest) -> Dict[str, Any]:
    ws = body.cwd or orchestrator.workspace_root or cfg.workspace_root
    try:
        result = await run_command(body.command, ws, timeout=body.timeout or 30, approved=True)
        return result
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class SettingsUpdateRequest(BaseModel):
    gemini_api_key: Optional[str] = None
    nvidia_api_key: Optional[str] = None
    xai_api_key: Optional[str] = None
    gemini_model: Optional[str] = None
    nvidia_model: Optional[str] = None
    xai_model: Optional[str] = None
    ollama_model: Optional[str] = None
    routing_mode: Optional[str] = None
    escalation_enabled: Optional[bool] = None


@app.post("/api/settings")
async def update_settings_endpoint(settings: SettingsUpdateRequest) -> Dict[str, Any]:
    if settings.gemini_api_key:
        os.environ["GEMINI_API_KEY"] = settings.gemini_api_key
    if settings.nvidia_api_key:
        os.environ["NVIDIA_API_KEY"] = settings.nvidia_api_key
        os.environ["XAI_API_KEY"] = settings.nvidia_api_key
    elif settings.xai_api_key:
        os.environ["NVIDIA_API_KEY"] = settings.xai_api_key
        os.environ["XAI_API_KEY"] = settings.xai_api_key
    if settings.gemini_model:
        os.environ["GEMINI_MODEL"] = settings.gemini_model
    if settings.nvidia_model:
        os.environ["NVIDIA_MODEL"] = settings.nvidia_model
        os.environ["XAI_MODEL"] = settings.nvidia_model
    elif settings.xai_model:
        os.environ["NVIDIA_MODEL"] = settings.xai_model
        os.environ["XAI_MODEL"] = settings.xai_model
    if settings.ollama_model:
        os.environ["OLLAMA_MODEL"] = settings.ollama_model
    if settings.routing_mode:
        os.environ["ROUTING_MODE"] = settings.routing_mode

    # Clear config cache
    get_config.cache_clear()
    new_cfg = get_config()

    return {
        "status": "updated",
        "gemini_configured": new_cfg.gemini_available,
        "nvidia_configured": new_cfg.nvidia_available,
        "grok_configured": new_cfg.nvidia_available,
        "routing_mode": new_cfg.routing_mode.value,
    }


# ---------------------------------------------------------------------------
# Serve Frontend Static Build if present
# ---------------------------------------------------------------------------
frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if frontend_dist.exists() and (frontend_dist / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
else:
    @app.get("/")
    async def index_placeholder() -> Dict[str, Any]:
        return {
            "message": "Hybrid AI Coding Agent Backend API is running.",
            "frontend_url": "http://localhost:3000",
            "api_docs": "http://localhost:8000/docs",
        }
