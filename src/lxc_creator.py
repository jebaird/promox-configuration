"""LXC Container creator for Proxmox.

Handles creation and management of LXC containers via Proxmox API.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import time

from rich.console import Console

from .proxmox_client import ProxmoxClient

console = Console()


@dataclass
class LXCConfig:
    """LXC container configuration."""
    vmid: int
    hostname: str
    ostemplate: str  # e.g., "local:vztmpl/debian-12-standard_12.2-1_amd64.tar.zst"
    
    # Resources
    cores: int = 1
    memory: int = 256  # MB
    swap: int = 256    # MB
    
    # Storage
    rootfs_storage: str = "local-lvm"
    rootfs_size: str = "2G"
    
    # Network
    net0: str = ""  # e.g., "name=eth0,bridge=vmbr1,ip=10.0.0.5/24,gw=10.0.0.1"
    
    # Other
    start: bool = True
    onboot: bool = True
    unprivileged: bool = True
    password: str = ""  # Root password (optional)
    ssh_public_keys: str = ""  # SSH authorized keys


class LXCCreator:
    """Creates and manages LXC containers on Proxmox."""
    
    # Known Debian templates on Proxmox (base names without version)
    TEMPLATE_PATTERNS = {
        "debian-12-standard": "debian-12-standard",
        "debian-11-standard": "debian-11-standard",
        "debian-13-standard": "debian-13-standard",
        "ubuntu-24.04-standard": "ubuntu-24.04-standard",
        "ubuntu-22.04-standard": "ubuntu-22.04-standard",
        "alpine": "alpine",
    }
    
    def __init__(self, client: ProxmoxClient):
        """Initialize with Proxmox client."""
        self.client = client
    
    def get_available_templates(self, storage: str = "local") -> list[dict]:
        """Get list of available container templates.
        
        Args:
            storage: Storage to check for templates
            
        Returns:
            List of template info dicts
        """
        try:
            content = self.client.get_storage_content(storage, content_type="vztmpl")
            return content
        except Exception:
            return []
    
    def find_template(self, storage: str, template_pattern: str) -> str | None:
        """Find a template matching the given pattern.
        
        Args:
            storage: Storage name
            template_pattern: Base template name (e.g., 'debian-12-standard')
            
        Returns:
            Full volid if found, None otherwise
        """
        templates = self.get_available_templates(storage)
        pattern = self.TEMPLATE_PATTERNS.get(template_pattern, template_pattern)
        
        for t in templates:
            volid = t.get("volid", "")
            # Check if the template matches the pattern
            # volid format: "local:vztmpl/debian-12-standard_12.12-1_amd64.tar.zst"
            if pattern in volid:
                return volid
        
        return None
    
    def template_exists(self, storage: str, template_name: str) -> bool:
        """Check if a template exists in storage.
        
        Args:
            storage: Storage name
            template_name: Template filename or pattern
            
        Returns:
            True if template exists
        """
        return self.find_template(storage, template_name) is not None
    
    def download_template(
        self,
        storage: str,
        template: str,
        timeout: int = 300, 
    ) -> str:
        """Get a container template, downloading if not present.
        
        Args:
            storage: Storage to check/download to
            template: Template identifier (e.g., "debian-12-standard")
            timeout: Download timeout in seconds
            
        Returns:
            Template volid
        """
        # Check if template already exists
        existing = self.find_template(storage, template)
        if existing:
            console.print(f"[dim]Template found: {existing}[/dim]")
            return existing
        
        console.print(f"[dim]Downloading template: {template}...[/dim]")
        
        # Query available templates from Proxmox repos
        available = self.client.api.nodes(self.client.node).aplinfo.get()
        
        # Find matching template in available list
        pattern = self.TEMPLATE_PATTERNS.get(template, template)
        matching = [t for t in available if pattern in t.get('template', '')]
        
        if not matching:
            raise ValueError(f"Template '{template}' not found in Proxmox repositories")
        
        # Use the first match (usually the standard/system section)
        system_match = [t for t in matching if t.get('section') == 'system']
        target = system_match[0] if system_match else matching[0]
        
        template_name = target['template']
        console.print(f"[dim]Found: {template_name}[/dim]")
        
        # Download using Proxmox API
        upid = self.client.api.nodes(self.client.node).aplinfo.post(
            storage=storage,
            template=template_name,
        )
        
        # Wait for download
        self.client.wait_for_task(upid, timeout=timeout)
        
        return f"{storage}:vztmpl/{template_name}"
    
    def container_exists(self, vmid: int) -> bool:
        """Check if a container with given ID exists."""
        try:
            self.client.api.nodes(self.client.node).lxc(vmid).status.current.get()
            return True
        except Exception:
            return False
    
    def get_containers(self) -> list[dict]:
        """Get list of all LXC containers on node."""
        return self.client.api.nodes(self.client.node).lxc.get()
    
    def get_container_status(self, vmid: int) -> dict:
        """Get container runtime status."""
        return self.client.api.nodes(self.client.node).lxc(vmid).status.current.get()
    
    def get_container_config(self, vmid: int) -> dict:
        """Get container configuration."""
        return self.client.api.nodes(self.client.node).lxc(vmid).config.get()
    
    def create_container(self, config: LXCConfig) -> str:
        """Create a new LXC container.
        
        Args:
            config: LXCConfig with container settings
            
        Returns:
            Task UPID
        """
        # Parse rootfs size - Proxmox expects size in GB as number
        rootfs_size = config.rootfs_size
        if rootfs_size.upper().endswith('G'):
            rootfs_size = rootfs_size[:-1]  # Remove 'G' suffix
        elif rootfs_size.upper().endswith('GB'):
            rootfs_size = rootfs_size[:-2]  # Remove 'GB' suffix
        
        # Build API parameters
        params = {
            "vmid": config.vmid,
            "hostname": config.hostname,
            "ostemplate": config.ostemplate,
            "cores": config.cores,
            "memory": config.memory,
            "swap": config.swap,
            "rootfs": f"{config.rootfs_storage}:{rootfs_size}",
            "start": 1 if config.start else 0,
            "onboot": 1 if config.onboot else 0,
            "unprivileged": 1 if config.unprivileged else 0,
        }
        
        # Network
        if config.net0:
            params["net0"] = config.net0
        
        # Password
        if config.password:
            params["password"] = config.password
        
        # SSH keys
        if config.ssh_public_keys:
            params["ssh-public-keys"] = config.ssh_public_keys
        
        return self.client.api.nodes(self.client.node).lxc.post(**params)
    
    def start_container(self, vmid: int) -> str:
        """Start a container.
        
        Returns:
            Task UPID
        """
        return self.client.api.nodes(self.client.node).lxc(vmid).status.start.post()
    
    def stop_container(self, vmid: int) -> str:
        """Stop a container.
        
        Returns:
            Task UPID
        """
        return self.client.api.nodes(self.client.node).lxc(vmid).status.stop.post()
    
    def delete_container(self, vmid: int, purge: bool = True) -> str:
        """Delete a container.
        
        Args:
            vmid: Container ID
            purge: Also remove from backup jobs
            
        Returns:
            Task UPID
        """
        params = {}
        if purge:
            params["purge"] = 1
            params["destroy-unreferenced-disks"] = 1
        return self.client.api.nodes(self.client.node).lxc(vmid).delete(**params)
    
    def exec_command(self, vmid: int, command: str, timeout: int = 60) -> dict:
        """Execute a command inside a running container.
        
        Note: Requires container to be running and have exec enabled.
        
        Args:
            vmid: Container ID
            command: Command to execute
            timeout: Execution timeout
            
        Returns:
            Task result
        """
        # Proxmox exec API (requires REST API 7.0+)
        return self.client.api.nodes(self.client.node).lxc(vmid).exec.post(
            command=command,
        )
    
    def upload_file_to_container(
        self,
        vmid: int,
        local_path: Path | str,
        remote_path: str,
    ) -> None:
        """Upload a file to a container.
        
        Uses Proxmox's storage and temporary mount.
        For simpler approach, use SSH after container starts.
        
        Args:
            vmid: Container ID
            local_path: Local file path
            remote_path: Destination path in container
        """
        # Note: Direct file upload to LXC is complex via API
        # The cert_manager_deploy will use SSH instead after container starts
        raise NotImplementedError(
            "Use SSH to upload files after container starts. "
            "See cert_manager_deploy.py for implementation."
        )
    
    def wait_for_container_ready(
        self,
        vmid: int,
        timeout: int = 120,
        check_interval: int = 5,
    ) -> bool:
        """Wait for container to be running and network ready.
        
        Args:
            vmid: Container ID
            timeout: Maximum wait time
            check_interval: Seconds between checks
            
        Returns:
            True if container is ready
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                status = self.get_container_status(vmid)
                if status.get("status") == "running":
                    # Container is running, give network a moment
                    time.sleep(3)
                    return True
            except Exception:
                pass
            
            time.sleep(check_interval)
        
        return False
