"""Pipeline lock module for preventing concurrent pipeline runs."""

import getpass
import logging
import os
import socket
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("pipeline")


@dataclass
class LockInfo:
    """Information about a lock holder."""
    pid: int
    hostname: str
    timestamp: str
    username: str


class PipelineLockError(Exception):
    """Raised when a pipeline lock cannot be acquired."""
    def __init__(self, phase: str, info: Optional[LockInfo]):
        self.phase = phase
        self.info = info
        if info:
            super().__init__(
                f"Pipeline locked by PID {info.pid} on {info.hostname} "
                f"since {info.timestamp} (user: {info.username})"
            )
        else:
            super().__init__(f"Pipeline phase '{phase}' is locked")


class PipelineLock:
    """Manages pipeline phase locks to prevent concurrent execution."""
    
    LOCK_TIMEOUT_SECONDS = 1800  # 30 minutes
    
    def __init__(self, config):
        """Initialize with config to get the mad_dir."""
        self._config = config
        self._current_pid = os.getpid()
        self._current_hostname = socket.gethostname()
        self._current_username = getpass.getuser()
    
    def _lock_path(self, phase: str) -> Path:
        """Get the lock file path for a phase."""
        return self._config.mad_dir / f".pipeline-lock.{phase}"
    
    def _parse_lock(self, path: Path) -> Optional[LockInfo]:
        """Parse a lock file and return LockInfo, or None if invalid."""
        try:
            if not path.exists():
                return None
            content = path.read_text().strip()
            if not content:
                return None
            # Split on first 2 colons only (hostname, then timestamp+username)
            # Format: <pid>:<hostname>:<timestamp>:<username>
            # Timestamp contains colons, so we need to split carefully
            parts = content.split(":", 2)
            if len(parts) != 3:
                return None
            pid = int(parts[0])
            hostname = parts[1]
            rest = parts[2]
            # Split on last colon to separate timestamp and username
            last_colon = rest.rfind(":")
            if last_colon == -1:
                return None
            timestamp = rest[:last_colon]
            username = rest[last_colon + 1:]
            return LockInfo(
                pid=pid,
                hostname=hostname,
                timestamp=timestamp,
                username=username
            )
        except (ValueError, IndexError, OSError) as e:
            logger.debug(f"Failed to parse lock file {path}: {e}")
            return None
    
    def is_stale(self, phase: str) -> bool:
        """Check if a lock is stale (dead PID or >30 min old)."""
        path = self._lock_path(phase)
        info = self._parse_lock(path)
        if info is None:
            return False
        
        # Check if PID is alive
        try:
            os.kill(info.pid, 0)
            # PID is alive, check timestamp
            try:
                lock_time = datetime.fromisoformat(info.timestamp)
                age = datetime.now() - lock_time
                return age.total_seconds() > self.LOCK_TIMEOUT_SECONDS
            except ValueError:
                # Invalid timestamp format, treat as stale
                return True
        except ProcessLookupError:
            # PID doesn't exist
            return True
        except PermissionError:
            # PID exists but we can't signal it - it's alive
            return False
    
    def check(self, phase: str) -> Optional[LockInfo]:
        """Check if a phase is locked. Returns LockInfo or None."""
        path = self._lock_path(phase)
        info = self._parse_lock(path)
        if info is None:
            return None
        if self.is_stale(phase):
            return None
        return info
    
    def acquire(self, phase: str, force: bool = False) -> bool:
        """Acquire a lock for a phase. Returns True on success."""
        path = self._lock_path(phase)
        
        # Ensure mad_dir exists
        path.parent.mkdir(parents=True, exist_ok=True)
        
        # Check existing lock
        info = self._parse_lock(path)
        if info is not None:
            if force:
                # Force override - clean up and proceed
                logger.debug(f"Force acquiring lock for {phase}")
            elif info.pid == self._current_pid:
                # Same process - allow re-acquire (reentrant)
                logger.debug(f"Re-acquiring lock for {phase} (same PID)")
            elif not self.is_stale(phase):
                # Different PID and not stale - blocked
                return False
            # Otherwise, stale lock will be cleaned below
        
        # Clean stale lock if exists
        if path.exists() and self.is_stale(phase):
            try:
                path.unlink()
                logger.debug(f"Cleaned stale lock for {phase}")
            except OSError as e:
                logger.warning(f"Failed to clean stale lock {path}: {e}")
        
        # Write new lock
        timestamp = datetime.now().isoformat()
        content = f"{self._current_pid}:{self._current_hostname}:{timestamp}:{self._current_username}"
        try:
            path.write_text(content)
            logger.debug(f"Acquired lock for {phase}: {content}")
            return True
        except OSError as e:
            logger.error(f"Failed to acquire lock {path}: {e}")
            return False
    
    def release(self, phase: str) -> None:
        """Release a lock if owned by current process."""
        path = self._lock_path(phase)
        try:
            if not path.exists():
                return
            info = self._parse_lock(path)
            if info is not None and info.pid == self._current_pid:
                path.unlink()
                logger.debug(f"Released lock for {phase}")
        except OSError as e:
            logger.error(f"Failed to release lock {path}: {e}")
    
    @contextmanager
    def lock_phase(self, phase: str, force: bool = False):
        """Context manager to acquire and release a phase lock."""
        if not self.acquire(phase, force=force):
            info = self.check(phase)
            raise PipelineLockError(phase, info)
        try:
            yield
        finally:
            self.release(phase)
