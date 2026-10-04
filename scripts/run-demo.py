"""Install/build and supervise the credential-free judging demo (macOS/Linux)."""

import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    children: list[subprocess.Popen[bytes]] = []

    def stop(signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        # Check before installation, and never terminate another running application.
        ports = [
            int(os.environ.get(key, default))
            for key, default in (
                ("ABE_DEMO_BACKEND_PORT", "8000"),
                ("ABE_DEMO_PORT", "3000"),
                ("ABE_DEMO_FRONTEND_PORT", "5173"),
            )
        ]
        if len(set(ports)) != 3 or any(not 1024 <= port <= 65535 for port in ports):
            raise RuntimeError(
                "Demo ports must be distinct numbers from 1024 to 65535."
            )
        backend_port, gateway_port, frontend_port = ports
        for port in ports:
            with socket.socket() as probe:
                try:
                    probe.bind(("127.0.0.1", port))
                except OSError:
                    print(
                        f"Port {port} is in use. Stop that service, then retry.",
                        file=sys.stderr,
                    )
                    return 1
        python = ROOT / "backend/.venv/bin/python"
        if not python.exists():
            subprocess.run(
                [sys.executable, "-m", "venv", "backend/.venv"], cwd=ROOT, check=True
            )
        subprocess.run(
            [str(python), "-c", "import sys; sys.exit(sys.version_info < (3, 12))"],
            check=True,
        )
        subprocess.run(
            [str(python), "-m", "pip", "install", "-r", "backend/requirements-dev.txt"],
            cwd=ROOT,
            check=True,
        )
        for package in ("voice-gateway", "frontend"):
            for action in (["ci"], ["run", "build"]):
                subprocess.run(["npm", *action], cwd=ROOT / package, check=True)

        # No .env is loaded. Only ordinary OS/runtime settings cross into demo processes.
        env = {
            key: os.environ[key]
            for key in ("PATH", "HOME", "TMPDIR", "LANG", "SYSTEMROOT")
            if key in os.environ
        }
        env.update(
            ABE_INTERNAL_TOKEN=secrets.token_urlsafe(32),
            ABE_DEMO_PORT=str(gateway_port),
            ABE_DEMO_BACKEND_PORT=str(backend_port),
            ABE_DEMO_FRONTEND_PORT=str(frontend_port),
            MEMBER_REPOSITORY="synthetic",
            CONVERSATION_REPOSITORY="synthetic",
            PLAN_RULES_SOURCE="local",
            PROVIDER_REPOSITORY="synthetic",
            HISTORY_REPOSITORY="synthetic",
            RAG_PROVIDER="local",
            AWS_EC2_METADATA_DISABLED="true",
        )
        commands = [
            (
                "backend",
                [
                    str(python),
                    "-m",
                    "uvicorn",
                    "app.main:create_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(backend_port),
                    "--no-access-log",
                ],
                "/health",
                backend_port,
            ),
            ("voice-gateway", ["node", "dist/demo.js"], "/health", gateway_port),
            (
                "frontend",
                [
                    "node",
                    "node_modules/vite/bin/vite.js",
                    "preview",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(frontend_port),
                    "--strictPort",
                ],
                "/",
                frontend_port,
            ),
        ]
        for directory, command, path, port in commands:
            children.append(
                subprocess.Popen(
                    command, cwd=ROOT / directory, env=env, start_new_session=True
                )
            )
            deadline = time.monotonic() + 30
            while True:
                if any(child.poll() is not None for child in children):
                    raise RuntimeError("A demo component exited during startup.")
                try:
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}{path}", timeout=1
                    ) as response:
                        if response.status == 200:
                            break
                except (urllib.error.URLError, TimeoutError):
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"{directory} did not become healthy within 30 seconds."
                    )
                time.sleep(0.2)
        print(f"\nABE DEMO READY — http://127.0.0.1:{gateway_port}", flush=True)
        print(
            f"Companion app: http://127.0.0.1:{frontend_port} | Backend health: http://127.0.0.1:{backend_port}/health",
            flush=True,
        )
        print(
            "Synthetic, credential-free demo. No Nova, AWS or phone calls. Ctrl+C stops all three.",
            flush=True,
        )
        while all(child.poll() is None for child in children):
            time.sleep(0.5)
        raise RuntimeError(
            "A demo component stopped; shutting down the other components."
        )
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError, RuntimeError) as error:
        print(f"Demo failed: {error}", file=sys.stderr)
        return 1
    finally:
        for child in children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
