#!/usr/bin/env python3
"""AI YouTube Shorts Generator - Admin Overview Web Server.

Provides a web dashboard (index.html) and REST API for:
- Video preview & link parsing
- Initiating shorts generation (API & Local mode)
- Realtime job tracking & streaming logs
- Video player preview with range-request streaming
- Clips gallery, history, and .env configuration
"""
import argparse
import glob
import json
import mimetypes
import os
import re
import shutil
import sys
import threading
import time
import traceback
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

# Ensure stdout/stderr use UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
JOBS_DIR = OUTPUT_DIR / "jobs"
ENV_FILE = BASE_DIR / ".env"
ENV_EXAMPLE = BASE_DIR / ".env.example"

# Ensure directories exist
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DIR.mkdir(parents=True, exist_ok=True)

# In-memory jobs tracking
jobs_lock = threading.Lock()
active_jobs: Dict[str, Dict[str, Any]] = {}


def load_env_vars() -> Dict[str, str]:
    """Parse .env file into key-value pairs."""
    env_data: Dict[str, str] = {}
    target_file = ENV_FILE if ENV_FILE.exists() else ENV_EXAMPLE
    if target_file.exists():
        with open(target_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.split("#")[0].strip().strip('"').strip("'")
                    env_data[k] = v
    # Also overlay current os.environ
    for k in [
        "MUAPI_API_KEY",
        "LLM_PROVIDER",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "LOCAL_WHISPER_MODEL",
        "LOCAL_WHISPER_DEVICE",
        "LOCAL_OUTPUT_DIR",
    ]:
        if k in os.environ and os.environ[k]:
            env_data[k] = os.environ[k]
    return env_data


ORIG_STDOUT = sys.__stdout__ or sys.stdout
ORIG_STDERR = sys.__stderr__ or sys.stderr


def save_env_vars(updates: Dict[str, str]) -> None:
    """Save updated keys to .env and refresh os.environ."""
    current_lines: List[str] = []
    if ENV_FILE.exists():
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            current_lines = f.readlines()
    elif ENV_EXAMPLE.exists():
        with open(ENV_EXAMPLE, "r", encoding="utf-8") as f:
            current_lines = f.readlines()

    keys_written = set()
    new_lines: List[str] = []

    for line in current_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in updates:
                new_lines.append(f"{k}={updates[k]}\n")
                os.environ[k] = updates[k]
                keys_written.add(k)
                continue
        new_lines.append(line)

    for k, v in updates.items():
        if k not in keys_written:
            new_lines.append(f"{k}={v}\n")
            os.environ[k] = v

    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


class JobLogger:
    """Thread-safe multi-writer capturing stdout for a specific job."""

    def __init__(self, job_id: str, job_dict: Dict[str, Any]):
        self.job_id = job_id
        self.job_dict = job_dict

    def write(self, message: str) -> None:
        ORIG_STDOUT.write(message)
        if not message:
            return
        lines = message.splitlines()
        now_str = datetime.now().strftime("%H:%M:%S")
        with jobs_lock:
            for line in lines:
                clean_line = line.strip()
                if not clean_line:
                    continue
                # Exclude HTTP request logs from job execution terminal
                if re.search(r'"(GET|POST|HEAD|OPTIONS|DELETE)\s+', clean_line) or "HTTP/1." in clean_line:
                    continue
                log_entry = f"[{now_str}] {clean_line}"
                self.job_dict["logs"].append(log_entry)
                # Automatically update progress stage based on log output
                lower = clean_line.lower()
                if "download" in lower:
                    self.job_dict["stage"] = "downloading"
                    self.job_dict["progress"] = max(self.job_dict["progress"], 20)
                elif "whisper" in lower or "transcrib" in lower:
                    self.job_dict["stage"] = "transcribing"
                    self.job_dict["progress"] = max(self.job_dict["progress"], 45)
                elif "highlight" in lower or "llm" in lower:
                    self.job_dict["stage"] = "analyzing_virality"
                    self.job_dict["progress"] = max(self.job_dict["progress"], 70)
                elif "crop" in lower or "clip" in lower:
                    self.job_dict["stage"] = "rendering_shorts"
                    self.job_dict["progress"] = max(self.job_dict["progress"], 85)

    def flush(self) -> None:
        ORIG_STDOUT.flush()


def run_job_worker(job_id: str) -> None:
    """Background worker executing generate_shorts."""
    with jobs_lock:
        job = active_jobs.get(job_id)
    if not job:
        return

    job["status"] = "running"
    job["stage"] = "initializing"
    job["progress"] = 5
    job["started_at"] = time.time()

    params = job["params"]
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    old_stdout = sys.stdout
    old_stderr = sys.stderr
    logger = JobLogger(job_id, job)
    sys.stdout = logger
    sys.stderr = logger

    try:
        from shorts_generator.pipeline import generate_shorts

        print(f"Starting job {job_id} for URL: {params['url']}")
        print(f"Mode: {params['mode']} | Clips: {params['num_clips']} | Aspect Ratio: {params['aspect_ratio']}")

        # Apply any request-time environment overrides
        if params.get("llm_provider"):
            os.environ["LLM_PROVIDER"] = params["llm_provider"]
        if params.get("openai_api_key"):
            os.environ["OPENAI_API_KEY"] = params["openai_api_key"]
        if params.get("gemini_api_key"):
            os.environ["GEMINI_API_KEY"] = params["gemini_api_key"]
        if params.get("muapi_api_key"):
            os.environ["MUAPI_API_KEY"] = params["muapi_api_key"]

        result = generate_shorts(
            youtube_url=params["url"],
            num_clips=params["num_clips"],
            aspect_ratio=params["aspect_ratio"],
            download_format=params["format"],
            language=params["language"],
            mode=params["mode"],
            out_dir=str(job_dir),
        )

        # Normalize clip URLs so frontend can stream them
        shorts = result.get("shorts", [])
        for i, s in enumerate(shorts):
            clip_path = s.get("clip_url")
            if clip_path and os.path.exists(clip_path):
                # Ensure relative web path /output/...
                rel_path = os.path.relpath(clip_path, str(OUTPUT_DIR)).replace("\\", "/")
                s["web_url"] = f"/output/{rel_path}"
                s["filename"] = os.path.basename(clip_path)
            elif clip_path and (clip_path.startswith("http://") or clip_path.startswith("https://")):
                s["web_url"] = clip_path
                s["filename"] = f"short_{i+1:02d}.mp4"

        with jobs_lock:
            job["status"] = "completed"
            job["stage"] = "completed"
            job["progress"] = 100
            job["result"] = result
            job["finished_at"] = time.time()
            job["duration"] = round(job["finished_at"] - job["started_at"], 1)

        print(f"Job {job_id} completed successfully in {job['duration']}s! Generated {len(shorts)} clips.")

        # Save metadata JSON file
        meta_file = JOBS_DIR / f"{job_id}.json"
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(job, f, indent=2, ensure_ascii=False)

    except Exception as e:
        err_msg = f"{type(e).__name__}: {str(e)}"
        print(f"Job {job_id} failed: {err_msg}")
        traceback.print_exc(file=logger)
        with jobs_lock:
            job["status"] = "failed"
            job["stage"] = "failed"
            job["error"] = err_msg
            job["finished_at"] = time.time()

        # Save failure info
        meta_file = JOBS_DIR / f"{job_id}.json"
        try:
            with open(meta_file, "w", encoding="utf-8") as f:
                json.dump(job, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr


def get_all_history() -> List[Dict[str, Any]]:
    """Scan jobs directory and output directory for historical clips."""
    history = []
    # 1. From saved job json files
    job_files = sorted(JOBS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for jf in job_files:
        try:
            with open(jf, "r", encoding="utf-8") as f:
                job_data = json.load(f)
                history.append(job_data)
        except Exception:
            continue
    return history


class StudioRequestHandler(BaseHTTPRequestHandler):
    """Custom HTTP handler serving Admin UI and API."""

    def log_message(self, format: str, *args: Any) -> None:
        try:
            msg = format % args
            if "/api/jobs/" in msg or "/api/status" in msg or "/api/history" in msg:
                return
            ORIG_STDERR.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
        except Exception:
            pass

    def do_HEAD(self) -> None:
        self.do_GET()

    def send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_error_json(self, message: str, status: int = 400) -> None:
        self.send_json({"error": message, "status": "error"}, status=status)

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Range")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # Serve Admin Overview UI (index.html)
        if path in ("/", "/index.html"):
            index_path = BASE_DIR / "index.html"
            if not index_path.exists():
                index_path = BASE_DIR / "templates" / "index.html"
            if index_path.exists():
                with open(index_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(content)
                return
            else:
                self.send_error_json("index.html not found", status=404)
                return

        # Serve static output video clips with HTTP Range support for scrubbing
        if path.startswith("/output/"):
            rel_path = unquote(path[len("/output/") :]).lstrip("/")
            file_path = (OUTPUT_DIR / rel_path).resolve()

            # Security check: ensure path is within OUTPUT_DIR
            try:
                file_path.relative_to(OUTPUT_DIR.resolve())
            except ValueError:
                self.send_error_json("Access denied", status=403)
                return

            if not file_path.exists() or not file_path.is_file():
                self.send_error_json("File not found", status=404)
                return

            self.serve_file_with_range(file_path)
            return

        # API: Status / System Info
        if path == "/api/status":
            env = load_env_vars()
            self.send_json(
                {
                    "status": "online",
                    "time": time.time(),
                    "active_jobs_count": len([j for j in active_jobs.values() if j.get("status") == "running"]),
                    "output_dir": str(OUTPUT_DIR),
                    "ffmpeg_available": shutil.which("ffmpeg") is not None,
                    "default_mode": "local",
                    "llm_provider": env.get("LLM_PROVIDER", "openai"),
                    "has_openai_key": bool(env.get("OPENAI_API_KEY")),
                    "has_gemini_key": bool(env.get("GEMINI_API_KEY")),
                    "has_muapi_key": bool(env.get("MUAPI_API_KEY")),
                }
            )
            return

        # API: Config
        if path == "/api/config":
            env = load_env_vars()
            safe_config = {
                "LLM_PROVIDER": env.get("LLM_PROVIDER", "openai"),
                "OPENAI_MODEL": env.get("OPENAI_MODEL", "gpt-4o-mini"),
                "GEMINI_MODEL": env.get("GEMINI_MODEL", "gemini-3.8-flash"),
                "LOCAL_WHISPER_MODEL": env.get("LOCAL_WHISPER_MODEL", "base"),
                "LOCAL_WHISPER_DEVICE": env.get("LOCAL_WHISPER_DEVICE", "auto"),
                "has_openai_key": bool(env.get("OPENAI_API_KEY")),
                "has_gemini_key": bool(env.get("GEMINI_API_KEY")),
                "has_muapi_key": bool(env.get("MUAPI_API_KEY")),
                "openai_key_preview": (env.get("OPENAI_API_KEY", "")[:7] + "..." + env.get("OPENAI_API_KEY", "")[-4:])
                if env.get("OPENAI_API_KEY")
                else "",
                "gemini_key_preview": (env.get("GEMINI_API_KEY", "")[:7] + "..." + env.get("GEMINI_API_KEY", "")[-4:])
                if env.get("GEMINI_API_KEY")
                else "",
                "muapi_key_preview": (env.get("MUAPI_API_KEY", "")[:7] + "..." + env.get("MUAPI_API_KEY", "")[-4:])
                if env.get("MUAPI_API_KEY")
                else "",
            }
            self.send_json(safe_config)
            return

        # API: List all jobs
        if path == "/api/jobs":
            with jobs_lock:
                jobs_list = sorted(list(active_jobs.values()), key=lambda j: j.get("created_at", 0), reverse=True)
            self.send_json({"jobs": jobs_list})
            return

        # API: Single job status
        if path.startswith("/api/jobs/"):
            job_id = path[len("/api/jobs/") :].strip()
            with jobs_lock:
                job = active_jobs.get(job_id)
            if not job:
                # Check on disk
                disk_job_file = JOBS_DIR / f"{job_id}.json"
                if disk_job_file.exists():
                    try:
                        with open(disk_job_file, "r", encoding="utf-8") as f:
                            job = json.load(f)
                    except Exception:
                        pass

            if job:
                self.send_json(job)
            else:
                self.send_error_json("Job not found", status=404)
            return

        # API: History
        if path == "/api/history":
            history = get_all_history()
            self.send_json({"history": history})
            return

        self.send_error_json("Not found", status=404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        # Read JSON body
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            body = json.loads(post_data.decode("utf-8")) if post_data else {}
        except Exception:
            self.send_error_json("Invalid JSON body", status=400)
            return

        # API: Cancel Job
        if path.startswith("/api/jobs/") and path.endswith("/cancel"):
            job_id = path[len("/api/jobs/") : -len("/cancel")].strip()
            with jobs_lock:
                job = active_jobs.get(job_id)
                if job:
                    job["status"] = "cancelled"
                    job["stage"] = "cancelled"
                    job["error"] = "Job cancelled by user"
                    job["finished_at"] = time.time()
                    job["logs"].append(f"[{datetime.now().strftime('%H:%M:%S')}] Job cancelled by user.")
                    self.send_json({"status": "cancelled", "job_id": job_id})
                    return
            self.send_error_json("Job not found", status=404)
            return

        # API: Update Config
        if path == "/api/config":
            updates = {}
            for k in [
                "LLM_PROVIDER",
                "OPENAI_API_KEY",
                "OPENAI_MODEL",
                "GEMINI_API_KEY",
                "GEMINI_MODEL",
                "MUAPI_API_KEY",
                "LOCAL_WHISPER_MODEL",
                "LOCAL_WHISPER_DEVICE",
            ]:
                if k in body and body[k] is not None and str(body[k]).strip() != "":
                    updates[k] = str(body[k]).strip()
            if updates:
                save_env_vars(updates)
            self.send_json({"status": "saved", "updated": list(updates.keys())})
            return

        # API: Generate Shorts
        if path == "/api/generate":
            url = body.get("url", "").strip()
            if not url:
                self.send_error_json("Video URL is required", status=400)
                return

            mode = body.get("mode", "local").strip().lower()
            num_clips = int(body.get("num_clips", 3))
            aspect_ratio = body.get("aspect_ratio", "9:16").strip()
            fmt = str(body.get("format", "720")).strip()
            language = body.get("language") or None

            # Generate unique job ID
            timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            job_id = f"job_{timestamp_str}_{os.urandom(2).hex()}"

            job_record: Dict[str, Any] = {
                "id": job_id,
                "status": "queued",
                "stage": "queued",
                "progress": 0,
                "created_at": time.time(),
                "started_at": None,
                "finished_at": None,
                "duration": None,
                "logs": [f"[{datetime.now().strftime('%H:%M:%S')}] Job queued."],
                "params": {
                    "url": url,
                    "mode": mode,
                    "num_clips": num_clips,
                    "aspect_ratio": aspect_ratio,
                    "format": fmt,
                    "language": language,
                    "llm_provider": body.get("llm_provider"),
                    "openai_api_key": body.get("openai_api_key"),
                    "gemini_api_key": body.get("gemini_api_key"),
                    "muapi_api_key": body.get("muapi_api_key"),
                },
                "result": None,
                "error": None,
            }

            with jobs_lock:
                active_jobs[job_id] = job_record

            # Launch background worker
            thread = threading.Thread(target=run_job_worker, args=(job_id,), daemon=True)
            thread.start()

            self.send_json({"status": "queued", "job_id": job_id, "job": job_record}, status=202)
            return

        self.send_error_json("Not found", status=404)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/clips/"):
            rel_path = unquote(path[len("/api/clips/") :]).lstrip("/")
            file_path = (OUTPUT_DIR / rel_path).resolve()
            try:
                file_path.relative_to(OUTPUT_DIR.resolve())
            except ValueError:
                self.send_error_json("Access denied", status=403)
                return

            if file_path.exists() and file_path.is_file():
                try:
                    file_path.unlink()
                    self.send_json({"status": "deleted", "file": rel_path})
                    return
                except Exception as e:
                    self.send_error_json(f"Failed to delete: {e}", status=500)
                    return
            self.send_error_json("File not found", status=404)
            return

        self.send_error_json("Not found", status=404)

    def serve_file_with_range(self, file_path: Path) -> None:
        """Stream video files supporting HTTP Range header for scrubbed playback."""
        file_size = file_path.stat().st_size
        mime_type, _ = mimetypes.guess_type(str(file_path))
        if not mime_type:
            mime_type = "video/mp4"

        range_header = self.headers.get("Range")
        if not range_header or not range_header.startswith("bytes="):
            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(file_size))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            with open(file_path, "rb") as f:
                shutil.copyfileobj(f, self.wfile)
            return

        # Parse range request (e.g. bytes=0- or bytes=100-200)
        try:
            raw_range = range_header.replace("bytes=", "").strip()
            parts = raw_range.split("-")
            start = int(parts[0]) if parts[0] else 0
            end = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1
            if end >= file_size:
                end = file_size - 1
            if start > end or start >= file_size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{file_size}")
                self.end_headers()
                return

            chunk_len = end - start + 1
            self.send_response(206)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.send_header("Content-Length", str(chunk_len))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            with open(file_path, "rb") as f:
                f.seek(start)
                remaining = chunk_len
                buffer_size = 64 * 1024
                while remaining > 0:
                    read_size = min(buffer_size, remaining)
                    data = f.read(read_size)
                    if not data:
                        break
                    self.wfile.write(data)
                    remaining -= len(data)
        except (ConnectionResetError, BrokenPipeError):
            pass


def find_free_port(start_port: int = 5000, max_attempts: int = 20) -> int:
    """Find an available port starting from start_port."""
    import socket

    for port in range(start_port, start_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start_port


def main() -> int:
    parser = argparse.ArgumentParser(description="AI YouTube Shorts Generator - Admin Overview Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host interface (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5000, help="Port to listen on (default: 5000)")
    args = parser.parse_args()

    # Preload previous history into active_jobs for immediate display
    for hist in get_all_history():
        if "id" in hist and hist["id"] not in active_jobs:
            active_jobs[hist["id"]] = hist

    port = args.port
    # If default port is busy, pick a free one
    try:
        server = ThreadingHTTPServer((args.host, port), StudioRequestHandler)
    except OSError:
        alt_port = find_free_port(start_port=port + 1)
        print(f"[!] Port {port} is busy. Using port {alt_port} instead.")
        port = alt_port
        server = ThreadingHTTPServer((args.host, port), StudioRequestHandler)

    url_local = f"http://localhost:{port}"
    print("=" * 68)
    print("  🚀 AI YouTube Shorts Generator - Admin Overview")
    print(f"  🌐 Running locally at: {url_local}")
    if args.host == "0.0.0.0":
        print(f"  🌐 Network access at: http://<your-ip>:{port}")
    print("  📁 Output clips dir:   " + str(OUTPUT_DIR))
    print("  Press Ctrl+C to stop the server")
    print("=" * 68)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        server.server_close()
        print("Server stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
