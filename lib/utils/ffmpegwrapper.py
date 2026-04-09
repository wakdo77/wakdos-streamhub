import os
import signal
import subprocess
import threading
import sys
from typing import Iterable, Optional
import time

_IS_WINDOWS = sys.platform == "win32"

class FFmpegWrapper:
    def __init__(self, cmd: list[str], name: str = "FFmpeg", logger=None, timeout: int = 30):
        self.cmd = cmd
        self.name = name
        self.logger = logger or (lambda msg: print(msg, file=sys.stderr))
        self.proc: Optional[subprocess.Popen] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._last_read_at = time.time()
        self.timeout = timeout

    def start(self) -> None:
        with self._lock:
            if self.proc and self.proc.poll() is None:
                return

            popen_kwargs = dict(
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            # Process groups: start_new_session on Unix, CREATE_NEW_PROCESS_GROUP on Windows
            if _IS_WINDOWS:
                popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                popen_kwargs["start_new_session"] = True

            self.proc = subprocess.Popen(self.cmd, **popen_kwargs)

            if self.proc.stderr:
                self._stderr_thread = threading.Thread(
                    target=self._log_stderr,
                    name=f"{self.name}-stderr",
                    daemon=True,
                )
                self._stderr_thread.start()
                self.start_watchdog()

    def _log_stderr(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        for raw in self.proc.stderr:
            try:
                line = raw.decode(errors="replace").rstrip()
            except Exception:
                line = str(raw).rstrip()
            if line:
                self.logger(f"[{self.name}] {line}")

    def read_stdout(self, chunk_size: int = 8192) -> Iterable[bytes]:
        assert self.proc is not None and self.proc.stdout is not None
        try:
            while True:
                chunk = self.proc.stdout.read(chunk_size)
                if not chunk:
                    break
                self._last_read_at = time.time()
                yield chunk
        finally:
            self.stop()

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            if not self.proc:
                return
            proc = self.proc

            if proc.poll() is None:
                try:
                    if proc.stdin:
                        try:
                            proc.stdin.write(b"q")
                            proc.stdin.flush()
                        except Exception:
                            pass

                    if _IS_WINDOWS:
                        proc.terminate()
                    else:
                        os.killpg(proc.pid, signal.SIGTERM)
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    if _IS_WINDOWS:
                        proc.kill()
                    else:
                        os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                except (ProcessLookupError, OSError):
                    pass

            if proc.stderr:
                try:
                    proc.stderr.close()
                except Exception:
                    pass
            if proc.stdout:
                try:
                    proc.stdout.close()
                except Exception:
                    pass

            self.proc = None

    def start_watchdog(self):
        def _watch():
            while self.running:
                if time.time() - self._last_read_at > self.timeout:
                    self.logger(f"[{self.name}] idle timeout {self.timeout} reached, stopping")
                    self.stop()
                    break
                time.sleep(1)
        threading.Thread(target=_watch, daemon=True).start()

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None