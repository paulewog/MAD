"""Systemd user service management for MAD TUI instances."""

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path


def service_name_for_dir(project_dir: Path) -> str:
    """Derive a filesystem-safe systemd service name from a project directory."""
    abs_path = str(project_dir.resolve())
    hash_value = hashlib.sha256(abs_path.encode()).hexdigest()[:12]
    return f"mad-tui-{hash_value}"


def tmux_session_name(project_dir: Path) -> str:
    """Derive a human-readable tmux session name from the project directory."""
    dir_name = project_dir.name
    sanitized = "".join(c if c.isalnum() else "-" for c in dir_name).lower()
    sanitized = sanitized[:32 - len('mad-') - 5]
    sanitized = sanitized.rstrip("-")
    abs_path = str(project_dir.resolve())
    hash_value = hashlib.sha256(abs_path.encode()).hexdigest()[:4]
    return f"mad-{sanitized}-{hash_value}"


def generate_unit_file(project_dir: Path, pipeline_executable) -> str:
    """Generate a systemd unit file content for the TUI service.
    
    Args:
        project_dir: Path to the project directory
        pipeline_executable: Either a Path (when pipeline is on PATH) or str (when using fallback)
    """
    session = tmux_session_name(project_dir)
    if isinstance(pipeline_executable, str):
        exec_path = pipeline_executable
    else:
        exec_path = str(pipeline_executable.resolve())
    work_dir = str(project_dir.resolve())
    home = str(Path.home())
    
    return f"""[Unit]
Description=MAD TUI - {project_dir.name}
After=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory={work_dir}
ExecStartPre=/bin/bash -c '/usr/bin/tmux kill-session -t {session} 2>/dev/null; true'
ExecStart=/usr/bin/tmux new-session -d -s {session} -x 200 -y 50 {exec_path} tui
ExecStop=/usr/bin/tmux kill-session -t {session}
Environment=HOME={home}
Environment=TERM=xterm-256color

[Install]
WantedBy=default.target
"""


def _find_pipeline_executable():
    """Find the pipeline executable, either on PATH or as a fallback to pipeline.py.
    
    Returns:
        Path: When pipeline is found on PATH
        str: When using fallback (python interpreter + script path)
        None: When neither is available
    """
    pipeline_path = shutil.which("pipeline")
    if pipeline_path:
        return Path(pipeline_path)
    
    fallback = Path(__file__).parent / "pipeline.py"
    if fallback.exists():
        return f"{sys.executable} {fallback}"
    
    return None


def install_service(project_dir: Path) -> None:
    """Install and start a systemd user service for this project's TUI."""
    if not shutil.which("tmux"):
        print("Error: tmux is not installed. Please install tmux to use service management.")
        sys.exit(1)
    
    if not shutil.which("systemctl"):
        print("Error: systemctl is not available. Systemd user services require systemd.")
        sys.exit(1)
    
    pipeline_executable = _find_pipeline_executable()
    if not pipeline_executable:
        print("Error: Could not find 'pipeline' executable. Ensure it is on PATH or pipeline.py exists.")
        sys.exit(1)
    
    service_name = service_name_for_dir(project_dir)
    session_name = tmux_session_name(project_dir)
    
    unit_file_content = generate_unit_file(project_dir, pipeline_executable)
    
    systemd_dir = Path.home() / ".config" / "systemd" / "user"
    systemd_dir.mkdir(parents=True, exist_ok=True)
    
    unit_file_path = systemd_dir / f"{service_name}.service"
    unit_file_path.write_text(unit_file_content)
    
    try:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", service_name], check=True)
        subprocess.run(["systemctl", "--user", "start", service_name], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error: Failed to manage service: {e}")
        print(f"Unit file was written to {unit_file_path} but may not be properly enabled/started.")
        print("You may need to run the following commands manually:")
        print(f"  systemctl --user daemon-reload")
        print(f"  systemctl --user enable {service_name}")
        print(f"  systemctl --user start {service_name}")
        sys.exit(1)
    
    print(f"Service installed: {service_name}")
    print(f"tmux session: {session_name}")
    print(f"To attach: tmux attach -t {session_name}")
    print(f"To view logs: journalctl --user -u {service_name} -f")


def uninstall_service(project_dir: Path) -> None:
    """Stop and remove the systemd user service for this project."""
    service_name = service_name_for_dir(project_dir)
    session_name = tmux_session_name(project_dir)
    
    subprocess.run(["systemctl", "--user", "stop", service_name], capture_output=True)
    subprocess.run(["systemctl", "--user", "disable", service_name], capture_output=True)
    
    unit_file_path = Path.home() / ".config" / "systemd" / "user" / f"{service_name}.service"
    if unit_file_path.exists():
        unit_file_path.unlink()
    
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    
    subprocess.run(["tmux", "kill-session", "-t", session_name], capture_output=True)
    
    print(f"Service uninstalled: {service_name}")


def stop_service(project_dir: Path) -> None:
    """Stop the systemd user service for this project without removing it."""
    service_name = service_name_for_dir(project_dir)
    session_name = tmux_session_name(project_dir)
    
    subprocess.run(["systemctl", "--user", "stop", service_name], capture_output=True)
    subprocess.run(["tmux", "kill-session", "-t", session_name], capture_output=True)
    
    print(f"Service stopped: {service_name}")


def start_service(project_dir: Path) -> None:
    """Start the systemd user service for this project."""
    service_name = service_name_for_dir(project_dir)
    
    unit_file_path = Path.home() / ".config" / "systemd" / "user" / f"{service_name}.service"
    if not unit_file_path.exists():
        print(f"Service not installed. Run: pipeline service install")
        return
    
    try:
        subprocess.run(["systemctl", "--user", "start", service_name], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error: Failed to start service: {e}")
        print(f"You can try manually: systemctl --user start {service_name}")
        sys.exit(1)
    
    result = subprocess.run(
        ["systemctl", "--user", "status", service_name],
        capture_output=True,
        text=True
    )
    
    print(f"Service started: {service_name}")
    print(result.stdout)


def restart_service(project_dir: Path) -> None:
    """Restart the systemd user service for this project."""
    service_name = service_name_for_dir(project_dir)
    
    unit_file_path = Path.home() / ".config" / "systemd" / "user" / f"{service_name}.service"
    if not unit_file_path.exists():
        print(f"Service not installed. Run: pipeline service install")
        return
    
    try:
        subprocess.run(["systemctl", "--user", "restart", service_name], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error: Failed to restart service: {e}")
        print(f"You can try manually: systemctl --user restart {service_name}")
        sys.exit(1)
    
    print(f"Service restarted: {service_name}")


def service_status(project_dir: Path) -> None:
    """Show the systemd service status for this project."""
    service_name = service_name_for_dir(project_dir)
    session_name = tmux_session_name(project_dir)
    
    unit_file_path = Path.home() / ".config" / "systemd" / "user" / f"{service_name}.service"
    
    if not unit_file_path.exists():
        print(f"Service not installed. Run: pipeline service install")
        return
    
    result = subprocess.run(
        ["systemctl", "--user", "status", service_name],
        capture_output=True,
        text=True
    )
    
    print(f"Service: {service_name}")
    print(f"tmux session: {session_name}")
    print()
    print(result.stdout)
    
    session_result = subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        capture_output=True
    )
    
    if session_result.returncode == 0:
        print("tmux session: running")
    else:
        print("tmux session: not running")
