"""SSH Executor for remote command execution.

Provides SSH-based command execution for configuring LXC containers
when the Proxmox API exec endpoint is not available.
"""

import io
import socket
import time
from dataclasses import dataclass
from typing import Callable

import paramiko
from rich.console import Console

console = Console()


@dataclass
class SSHResult:
    """Result of an SSH command execution."""
    exit_code: int
    stdout: str
    stderr: str
    
    @property
    def success(self) -> bool:
        """Check if command succeeded."""
        return self.exit_code == 0
    
    @property
    def output(self) -> str:
        """Combined stdout and stderr."""
        return self.stdout + self.stderr


class SSHExecutor:
    """Executes commands over SSH on a remote host."""
    
    def __init__(
        self,
        host: str,
        username: str = "root",
        password: str | None = None,
        key_filename: str | None = None,
        key_string: str | None = None,
        port: int = 22,
        timeout: float = 30.0,
    ):
        """Initialize SSH executor.
        
        Args:
            host: Target hostname or IP
            username: SSH username (default: root)
            password: SSH password (optional)
            key_filename: Path to private key file (optional)
            key_string: Private key as PEM string (optional)
            port: SSH port (default: 22)
            timeout: Connection timeout in seconds
        """
        self.host = host
        self.username = username
        self.password = password
        self.key_filename = key_filename
        self.key_string = key_string
        self.port = port
        self.timeout = timeout
        self._client: paramiko.SSHClient | None = None
    
    def connect(self) -> None:
        """Establish SSH connection."""
        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        connect_kwargs: dict = {
            "hostname": self.host,
            "port": self.port,
            "username": self.username,
            "timeout": self.timeout,
            "allow_agent": False,
            "look_for_keys": False,
        }
        
        if self.password:
            connect_kwargs["password"] = self.password
        if self.key_filename:
            connect_kwargs["key_filename"] = self.key_filename
        if self.key_string:
            # Load key from string
            key_file = io.StringIO(self.key_string)
            pkey = paramiko.Ed25519Key.from_private_key(key_file)
            connect_kwargs["pkey"] = pkey
        
        self._client.connect(**connect_kwargs)
    
    def disconnect(self) -> None:
        """Close SSH connection."""
        if self._client:
            self._client.close()
            self._client = None
    
    def execute(
        self,
        command: str,
        timeout: float | None = None,
        check: bool = False,
    ) -> SSHResult:
        """Execute a command over SSH.
        
        Args:
            command: Command to execute
            timeout: Command timeout in seconds (None = no timeout)
            check: If True, raise exception on non-zero exit code
            
        Returns:
            SSHResult with exit code and output
            
        Raises:
            RuntimeError: If check=True and command fails
            Exception: If not connected
        """
        if not self._client:
            raise RuntimeError("Not connected. Call connect() first.")
        
        stdin, stdout, stderr = self._client.exec_command(
            command,
            timeout=timeout,
        )
        
        exit_code = stdout.channel.recv_exit_status()
        stdout_text = stdout.read().decode("utf-8", errors="replace")
        stderr_text = stderr.read().decode("utf-8", errors="replace")
        
        result = SSHResult(
            exit_code=exit_code,
            stdout=stdout_text,
            stderr=stderr_text,
        )
        
        if check and not result.success:
            raise RuntimeError(
                f"Command failed with exit code {exit_code}: {command}\n"
                f"stderr: {stderr_text}"
            )
        
        return result
    
    def upload_file(
        self,
        local_path: str,
        remote_path: str,
        mode: int | None = None,
    ) -> None:
        """Upload a file to the remote host.
        
        Args:
            local_path: Path to local file
            remote_path: Destination path on remote host
            mode: Optional file permissions (e.g., 0o600)
        """
        if not self._client:
            raise RuntimeError("Not connected. Call connect() first.")
        
        sftp = self._client.open_sftp()
        try:
            sftp.put(local_path, remote_path)
            if mode is not None:
                sftp.chmod(remote_path, mode)
        finally:
            sftp.close()
    
    def write_file(
        self,
        remote_path: str,
        content: str,
        mode: int | None = None,
    ) -> None:
        """Write content to a file on the remote host.
        
        Args:
            remote_path: Destination path on remote host
            content: File content to write
            mode: Optional file permissions (e.g., 0o600)
        """
        if not self._client:
            raise RuntimeError("Not connected. Call connect() first.")
        
        sftp = self._client.open_sftp()
        try:
            with sftp.file(remote_path, "w") as f:
                f.write(content)
            if mode is not None:
                sftp.chmod(remote_path, mode)
        finally:
            sftp.close()
    
    def read_file(self, remote_path: str) -> str:
        """Read content from a file on the remote host.
        
        Args:
            remote_path: Path to file on remote host
            
        Returns:
            File content as string
        """
        if not self._client:
            raise RuntimeError("Not connected. Call connect() first.")
        
        sftp = self._client.open_sftp()
        try:
            with sftp.file(remote_path, "r") as f:
                return f.read().decode("utf-8", errors="replace")
        finally:
            sftp.close()
    
    def __enter__(self) -> "SSHExecutor":
        """Context manager entry."""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.disconnect()


def wait_for_ssh(
    host: str,
    port: int = 22,
    timeout: float = 120.0,
    interval: float = 5.0,
    progress_callback: Callable[[float], None] | None = None,
) -> bool:
    """Wait for SSH to become available on a host.
    
    Args:
        host: Target hostname or IP
        port: SSH port (default: 22)
        timeout: Maximum time to wait in seconds
        interval: Time between connection attempts
        progress_callback: Optional callback with elapsed time
        
    Returns:
        True if SSH is available, False if timeout reached
    """
    start_time = time.time()
    
    while True:
        elapsed = time.time() - start_time
        
        if elapsed >= timeout:
            return False
        
        if progress_callback:
            progress_callback(elapsed)
        
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(min(interval, timeout - elapsed))
            result = sock.connect_ex((host, port))
            sock.close()
            
            if result == 0:
                # Port is open - give sshd a moment to fully initialize
                time.sleep(1)
                return True
                
        except (socket.error, socket.timeout):
            pass
        
        # Wait before next attempt
        remaining = timeout - elapsed
        wait_time = min(interval, remaining)
        if wait_time > 0:
            time.sleep(wait_time)
    
    return False
