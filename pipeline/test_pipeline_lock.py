"""Tests for PipelineLock."""

import os
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from lock import LockInfo, PipelineLock, PipelineLockError


class MockConfig:
    """Mock Config for testing."""
    def __init__(self, mad_dir: Path):
        self._mad_dir = mad_dir
    
    @property
    def mad_dir(self) -> Path:
        return self._mad_dir


class TestPipelineLock(unittest.TestCase):
    """Test cases for PipelineLock."""
    
    def setUp(self):
        """Create a temp directory for each test."""
        self.temp_dir = Path("/tmp/test-pipeline-lock")
        self.temp_dir.mkdir(exist_ok=True)
        self.config = MockConfig(self.temp_dir)
        self.lock = PipelineLock(self.config)
    
    def tearDown(self):
        """Clean up temp files."""
        import shutil
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)
    
    def test_acquire_release(self):
        """Test basic acquire/release cycle."""
        self.assertTrue(self.lock.acquire('plan'))
        lock_path = self.temp_dir / ".pipeline-lock.plan"
        self.assertTrue(lock_path.exists())
        
        self.lock.release('plan')
        self.assertFalse(lock_path.exists())
    
    def test_lock_file_format(self):
        """Test lock file format is correct."""
        self.lock.acquire('plan')
        lock_path = self.temp_dir / ".pipeline-lock.plan"
        content = lock_path.read_text()
        # Format: <pid>:<hostname>:<timestamp>:<username>
        # Split on first 2 colons only (timestamp contains colons)
        parts = content.strip().split(":", 2)
        
        self.assertEqual(len(parts), 3)
        self.assertEqual(int(parts[0]), os.getpid())
        self.assertEqual(parts[1], self.lock._current_hostname)
        # rest is timestamp:username
        rest = parts[2]
        last_colon = rest.rfind(':')
        timestamp = rest[:last_colon]
        username = rest[last_colon + 1:]
        # Verify timestamp is ISO format
        datetime.fromisoformat(timestamp)
        self.assertEqual(username, self.lock._current_username)
    
    def test_double_acquire_same_process(self):
        """Test same process can re-acquire."""
        self.assertTrue(self.lock.acquire('plan'))
        self.assertTrue(self.lock.acquire('plan'))
        self.lock.release('plan')
        # After release, should be able to acquire again
        self.assertTrue(self.lock.acquire('plan'))
        self.lock.release('plan')
    
    def test_acquire_blocks_other_pid(self):
        """Test different PID is blocked."""
        # Write a lock file with a fake PID and RECENT timestamp (within 30 min)
        lock_path = self.temp_dir / ".pipeline-lock.plan"
        fake_pid = 999999
        recent_timestamp = datetime.now().isoformat()
        lock_path.write_text(f"{fake_pid}:hostname:{recent_timestamp}:user")
        
        # Try to acquire - should fail (not force, PID is "alive", not stale)
        with patch('lock.os.kill', return_value=None):
            self.assertFalse(self.lock.acquire('plan'))
            
            # check() should return the info while inside the patch
            info = self.lock.check('plan')
            self.assertIsNotNone(info)
            self.assertEqual(info.pid, fake_pid)
    
    def test_stale_pid_cleanup(self):
        """Test dead PID is cleaned up."""
        lock_path = self.temp_dir / ".pipeline-lock.plan"
        fake_pid = 999998
        recent_timestamp = datetime.now().isoformat()
        lock_path.write_text(f"{fake_pid}:hostname:{recent_timestamp}:user")
        
        # ProcessLookupError means dead PID - should be treated as stale
        with patch('lock.os.kill', side_effect=ProcessLookupError()):
            result = self.lock.acquire('plan')
            self.assertTrue(result)
        
        # Lock should now be ours
        info = self.lock.check('plan')
        self.assertIsNotNone(info)
        self.assertEqual(info.pid, os.getpid())
        self.lock.release('plan')
    
    def test_stale_timeout_cleanup(self):
        """Test old timestamp is cleaned up."""
        lock_path = self.temp_dir / ".pipeline-lock.plan"
        old_timestamp = (datetime.now() - timedelta(minutes=31)).isoformat()
        lock_path.write_text(f"{os.getpid()}:hostname:{old_timestamp}:user")
        
        # Make os.kill think the PID is alive but timestamp is old
        with patch('lock.os.kill', return_value=None):
            self.assertTrue(self.lock.is_stale('plan'))
            result = self.lock.acquire('plan')
            self.assertTrue(result)
        
        self.lock.release('plan')
    
    def test_force_override(self):
        """Test force flag overrides active lock."""
        lock_path = self.temp_dir / ".pipeline-lock.plan"
        fake_pid = 999997
        recent_timestamp = datetime.now().isoformat()
        lock_path.write_text(f"{fake_pid}:hostname:{recent_timestamp}:user")
        
        # With force=True, should succeed even with different alive PID
        with patch('lock.os.kill', return_value=None):
            result = self.lock.acquire('plan', force=True)
            self.assertTrue(result)
        
        # Lock should now be ours
        info = self.lock.check('plan')
        self.assertIsNotNone(info)
        self.assertEqual(info.pid, os.getpid())
        self.lock.release('plan')
    
    def test_context_manager(self):
        """Test lock_phase context manager."""
        with self.lock.lock_phase('impl'):
            lock_path = self.temp_dir / ".pipeline-lock.impl"
            self.assertTrue(lock_path.exists())
        
        # Should be released after exit
        self.assertFalse(lock_path.exists())
    
    def test_context_manager_error(self):
        """Test lock is released on exception."""
        with self.assertRaises(RuntimeError):
            with self.lock.lock_phase('impl'):
                lock_path = self.temp_dir / ".pipeline-lock.impl"
                self.assertTrue(lock_path.exists())
                raise RuntimeError("test error")
        
        # Should be released after exception
        self.assertFalse(lock_path.exists())
    
    def test_context_manager_failure_raises_error(self):
        """Test context manager raises PipelineLockError on failure."""
        # Pre-lock the file with a recent timestamp
        lock_path = self.temp_dir / ".pipeline-lock.plan"
        fake_pid = 999996
        recent_timestamp = datetime.now().isoformat()
        lock_path.write_text(f"{fake_pid}:hostname:{recent_timestamp}:user")
        
        with patch('lock.os.kill', return_value=None):
            with self.assertRaises(PipelineLockError) as ctx:
                with self.lock.lock_phase('plan'):
                    pass
        
            self.assertEqual(ctx.exception.phase, 'plan')
            self.assertIsNotNone(ctx.exception.info)
            self.assertEqual(ctx.exception.info.pid, fake_pid)
    
    def test_release_wrong_pid(self):
        """Test release is no-op if not owner."""
        lock_path = self.temp_dir / ".pipeline-lock.plan"
        fake_pid = 999995
        lock_path.write_text(f"{fake_pid}:hostname:2026-01-01T00:00:00:user")
        
        # Our release should be no-op
        self.lock.release('plan')
        
        # File should still exist
        self.assertTrue(lock_path.exists())
    
    def test_check_returns_none_for_missing(self):
        """Test check returns None when no lock."""
        info = self.lock.check('plan')
        self.assertIsNone(info)
    
    def test_is_stale_no_lock(self):
        """Test is_stale returns False when no lock."""
        result = self.lock.is_stale('plan')
        self.assertFalse(result)
    
    def test_independent_plan_impl_locks(self):
        """Test plan and impl locks are independent."""
        self.assertTrue(self.lock.acquire('plan'))
        self.assertTrue(self.lock.acquire('impl'))
        
        plan_path = self.temp_dir / ".pipeline-lock.plan"
        impl_path = self.temp_dir / ".pipeline-lock.impl"
        
        self.assertTrue(plan_path.exists())
        self.assertTrue(impl_path.exists())
        
        self.lock.release('plan')
        self.assertFalse(plan_path.exists())
        self.assertTrue(impl_path.exists())
    
    def test_empty_lock_file(self):
        """Test empty lock file is treated as no lock."""
        lock_path = self.temp_dir / ".pipeline-lock.plan"
        lock_path.write_text("")
        
        info = self.lock.check('plan')
        self.assertIsNone(info)
        
        # Should be able to acquire
        result = self.lock.acquire('plan')
        self.assertTrue(result)
        self.lock.release('plan')
    
    def test_malformed_lock_file(self):
        """Test malformed lock file is handled gracefully."""
        lock_path = self.temp_dir / ".pipeline-lock.plan"
        lock_path.write_text("not-valid")
        
        info = self.lock.check('plan')
        self.assertIsNone(info)
        
        # Should be able to acquire
        result = self.lock.acquire('plan')
        self.assertTrue(result)
        self.lock.release('plan')

    def test_parse_lock_with_colons_in_timestamp(self):
        """ISO timestamp contains colons, parsed correctly."""
        lock_path = self.temp_dir / ".pipeline-lock.plan"
        timestamp = "2026-01-15T10:30:45"
        lock_path.write_text(f"{os.getpid()}:hostname:{timestamp}:user")
        
        info = self.lock._parse_lock(lock_path)
        
        self.assertIsNotNone(info)
        self.assertEqual(info.timestamp, timestamp)

    def test_permission_error_means_alive(self):
        """PermissionError from os.kill → not stale."""
        lock_path = self.temp_dir / ".pipeline-lock.plan"
        fake_pid = 999994
        recent_timestamp = datetime.now().isoformat()
        lock_path.write_text(f"{fake_pid}:hostname:{recent_timestamp}:user")
        
        # PermissionError means the process exists but we can't signal it - still considered alive
        with patch('lock.os.kill', side_effect=PermissionError()):
            result = self.lock.is_stale('plan')
            self.assertFalse(result)
        
        # Should not be able to acquire
        with patch('lock.os.kill', side_effect=PermissionError()):
            result = self.lock.acquire('plan')
            self.assertFalse(result)
        
        # Clean up
        lock_path.unlink()

    def test_invalid_timestamp_format(self):
        """Non-ISO timestamp → treated as stale."""
        lock_path = self.temp_dir / ".pipeline-lock.plan"
        lock_path.write_text(f"{os.getpid()}:hostname:not-a-valid-timestamp:user")
        
        result = self.lock.is_stale('plan')
        self.assertTrue(result)
        
        # Should be able to acquire (auto-cleanup of stale lock)
        result = self.lock.acquire('plan')
        self.assertTrue(result)
        self.lock.release('plan')

    def test_lock_file_with_extra_colons(self):
        """Lock parser has limited support for colons in username.
        
        The parser splits on last colon, so only the last segment after
        the final colon becomes the username. This is a known limitation
        due to ISO timestamps also containing colons.
        """
        lock_path = self.temp_dir / ".pipeline-lock.plan"
        timestamp = datetime.now().isoformat()
        lock_path.write_text(f"{os.getpid()}:hostname:{timestamp}:user:name:with:colons")
        
        info = self.lock._parse_lock(lock_path)
        
        self.assertIsNotNone(info)
        # Parser limitation: only gets the last colon-separated segment
        self.assertEqual(info.username, "colons")

    def test_concurrent_acquire_release(self):
        """Rapid acquire/release cycles don't corrupt."""
        for _ in range(10):
            result = self.lock.acquire('plan')
            self.assertTrue(result)
            self.lock.release('plan')
        
        # Final state should be clean
        lock_path = self.temp_dir / ".pipeline-lock.plan"
        self.assertFalse(lock_path.exists())

    def test_release_missing_file(self):
        """No crash when lock file already deleted."""
        # Should not raise
        self.lock.release('plan')
        
        # Should also work when we own the lock but file was deleted
        self.lock.acquire('plan')
        lock_path = self.temp_dir / ".pipeline-lock.plan"
        lock_path.unlink()
        
        # Should not raise
        self.lock.release('plan')

    def test_acquire_creates_mad_dir(self):
        """mad_dir created if missing."""
        # Delete the temp dir to test creation
        import shutil
        shutil.rmtree(self.temp_dir)
        
        result = self.lock.acquire('plan')
        
        self.assertTrue(result)
        self.assertTrue(self.temp_dir.exists())


if __name__ == '__main__':
    unittest.main()
