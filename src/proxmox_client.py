"""Proxmox VE API client wrapper."""

import urllib3
from typing import Any

from proxmoxer import ProxmoxAPI
from rich.console import Console

from .config import load_proxmox_config, load_credentials, get_ca_cert_path

# Suppress SSL warnings when verify_ssl is disabled
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

console = Console()


class ProxmoxClient:
    """Wrapper around Proxmox API with convenience methods."""
    
    def __init__(self, config: dict[str, Any] | None = None):
        """Initialize Proxmox API connection.
        
        Args:
            config: Optional config dict. If None, loads from config/proxmox.yaml
        """
        self.config = config or load_proxmox_config()
        self.node = self.config["node"]
        self._api: ProxmoxAPI | None = None
    
    @property
    def api(self) -> ProxmoxAPI:
        """Lazy-load API connection."""
        if self._api is None:
            self._api = self._connect()
        return self._api
    
    def _connect(self) -> ProxmoxAPI:
        """Establish connection to Proxmox API."""
        token_id, token_secret = load_credentials()
        
        # Determine SSL verification
        verify_ssl = self.config.get("verify_ssl", False)
        ca_cert = get_ca_cert_path()
        
        if ca_cert:
            verify_ssl = ca_cert
        
        console.print(f"[dim]Connecting to Proxmox at {self.config['host']}...[/dim]")
        
        api = ProxmoxAPI(
            host=self.config["host"],
            port=self.config.get("port", 8006),
            user=token_id.split("!")[0],  # Extract user from token_id
            token_name=token_id.split("!")[1],  # Extract token name
            token_value=token_secret,
            verify_ssl=verify_ssl,
            timeout=self.config.get("connection_timeout", 30),
        )
        
        return api
    
    def test_connection(self) -> bool:
        """Test API connection by fetching version info.
        
        Returns:
            True if connection successful
            
        Raises:
            Exception: If connection fails
        """
        version = self.api.version.get()
        console.print(f"[green]✓[/green] Connected to Proxmox VE {version['version']}")
        return True
    
    def get_nodes(self) -> list[dict]:
        """Get list of cluster nodes."""
        return self.api.nodes.get()
    
    def get_node_status(self) -> dict:
        """Get status of configured node."""
        return self.api.nodes(self.node).status.get()
    
    # -------------------------------------------------------------------------
    # Storage operations
    # -------------------------------------------------------------------------
    
    def get_storage_list(self) -> list[dict]:
        """Get list of storage pools on node."""
        return self.api.nodes(self.node).storage.get()
    
    def get_storage_content(self, storage: str, content_type: str | None = None) -> list[dict]:
        """Get contents of a storage pool.
        
        Args:
            storage: Storage pool name (e.g., 'local', 'local-lvm')
            content_type: Optional filter by content type ('iso', 'images', etc.)
        """
        params = {}
        if content_type:
            params["content"] = content_type
        return self.api.nodes(self.node).storage(storage).content.get(**params)
    
    def upload_iso(self, storage: str, filepath: str, filename: str) -> dict:
        """Upload an ISO file to storage.
        
        Args:
            storage: Target storage pool (must support 'iso' content)
            filepath: Local path to ISO file
            filename: Filename to use on Proxmox
            
        Returns:
            Upload task info
        """
        with open(filepath, "rb") as f:
            return self.api.nodes(self.node).storage(storage).upload.post(
                content="iso",
                filename=filename,
                file=f,
            )
    
    def iso_exists(self, storage: str, filename: str) -> bool:
        """Check if an ISO file exists in storage.
        
        Args:
            storage: Storage pool name
            filename: ISO filename to check
        """
        try:
            content = self.get_storage_content(storage, "iso")
            return any(item["volid"].endswith(filename) for item in content)
        except Exception:
            return False
    
    # -------------------------------------------------------------------------
    # Network operations
    # -------------------------------------------------------------------------
    
    def get_network_interfaces(self) -> list[dict]:
        """Get list of network interfaces on node."""
        return self.api.nodes(self.node).network.get()
    
    def get_network_interface(self, iface: str) -> dict | None:
        """Get specific network interface configuration.
        
        Args:
            iface: Interface name (e.g., 'vmbr0')
            
        Returns:
            Interface config or None if not found
        """
        try:
            return self.api.nodes(self.node).network(iface).get()
        except Exception:
            return None
    
    def create_bridge(
        self,
        name: str,
        ports: str | None = None,
        address: str | None = None,
        gateway: str | None = None,
        comments: str | None = None,
        autostart: bool = True,
    ) -> None:
        """Create a network bridge.
        
        Args:
            name: Bridge name (e.g., 'vmbr1')
            ports: Physical interface to bridge (e.g., 'enp2s0')
            address: IP address in CIDR notation (e.g., '10.0.0.1/24')
            gateway: Default gateway IP
            comments: Description
            autostart: Start bridge on boot
        """
        params = {
            "iface": name,
            "type": "bridge",
            "autostart": 1 if autostart else 0,
        }
        
        if ports:
            params["bridge_ports"] = ports
        if address:
            params["cidr"] = address
        if gateway:
            params["gateway"] = gateway
        if comments:
            params["comments"] = comments
        
        self.api.nodes(self.node).network.post(**params)
    
    def apply_network_config(self) -> None:
        """Apply pending network configuration changes."""
        self.api.nodes(self.node).network.put()
    
    def revert_network_config(self) -> None:
        """Revert pending network configuration changes."""
        self.api.nodes(self.node).network.delete()
    
    # -------------------------------------------------------------------------
    # VM operations
    # -------------------------------------------------------------------------
    
    def get_vms(self) -> list[dict]:
        """Get list of all VMs on node."""
        return self.api.nodes(self.node).qemu.get()
    
    def vm_exists(self, vmid: int) -> bool:
        """Check if a VM with given ID exists."""
        try:
            self.api.nodes(self.node).qemu(vmid).status.current.get()
            return True
        except Exception:
            return False
    
    def get_vm_config(self, vmid: int) -> dict:
        """Get VM configuration."""
        return self.api.nodes(self.node).qemu(vmid).config.get()
    
    def get_vm_status(self, vmid: int) -> dict:
        """Get VM runtime status."""
        return self.api.nodes(self.node).qemu(vmid).status.current.get()
    
    def create_vm(self, vmid: int, **kwargs) -> str:
        """Create a new VM.
        
        Args:
            vmid: VM identifier
            **kwargs: VM configuration parameters
            
        Returns:
            Task UPID
        """
        return self.api.nodes(self.node).qemu.post(vmid=vmid, **kwargs)
    
    def update_vm_config(self, vmid: int, **kwargs) -> None:
        """Update VM configuration.
        
        Args:
            vmid: VM identifier
            **kwargs: Configuration parameters to update
        """
        self.api.nodes(self.node).qemu(vmid).config.put(**kwargs)
    
    def delete_vm(self, vmid: int, purge: bool = True) -> str:
        """Delete a VM.
        
        Args:
            vmid: VM identifier
            purge: Also remove from backup jobs and HA
            
        Returns:
            Task UPID
        """
        params = {}
        if purge:
            params["purge"] = 1
            params["destroy-unreferenced-disks"] = 1
        return self.api.nodes(self.node).qemu(vmid).delete(**params)
    
    def start_vm(self, vmid: int) -> str:
        """Start a VM.
        
        Returns:
            Task UPID
        """
        return self.api.nodes(self.node).qemu(vmid).status.start.post()
    
    def stop_vm(self, vmid: int) -> str:
        """Stop a VM.
        
        Returns:
            Task UPID
        """
        return self.api.nodes(self.node).qemu(vmid).status.stop.post()
    
    # -------------------------------------------------------------------------
    # Task operations
    # -------------------------------------------------------------------------
    
    def get_task_status(self, upid: str) -> dict:
        """Get status of a task.
        
        Args:
            upid: Task UPID
        """
        return self.api.nodes(self.node).tasks(upid).status.get()
    
    def wait_for_task(self, upid: str, timeout: int = 300) -> dict:
        """Wait for a task to complete.
        
        Args:
            upid: Task UPID
            timeout: Maximum wait time in seconds
            
        Returns:
            Final task status
            
        Raises:
            TimeoutError: If task doesn't complete within timeout
        """
        import time
        
        start = time.time()
        while time.time() - start < timeout:
            status = self.get_task_status(upid)
            if status.get("status") == "stopped":
                if status.get("exitstatus") != "OK":
                    raise Exception(f"Task failed: {status.get('exitstatus')}")
                return status
            time.sleep(2)
        
        raise TimeoutError(f"Task {upid} did not complete within {timeout}s")
    
    def get_next_vmid(self) -> int:
        """Get next available VM ID."""
        return self.api.cluster.nextid.get()
