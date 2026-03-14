"""VM creation and management for Proxmox."""

from typing import Any

from rich.console import Console
from rich.table import Table

from .config import load_vm_config
from .proxmox_client import ProxmoxClient
from .network import NetworkManager

console = Console()


class VMCreator:
    """Creates and configures VMs on Proxmox."""
    
    def __init__(self, client: ProxmoxClient):
        """Initialize VM creator.
        
        Args:
            client: Proxmox API client
        """
        self.client = client
        self.network_manager = NetworkManager(client)
    
    def load_vm_config(self, vm_name: str) -> dict[str, Any]:
        """Load VM configuration from YAML file.
        
        Args:
            vm_name: Name of VM config file (e.g., 'pfsense')
        """
        return load_vm_config(vm_name)
    
    def build_vm_params(self, config: dict[str, Any], iso_volid: str | None = None) -> dict:
        """Build Proxmox API parameters from VM config.
        
        Args:
            config: VM configuration dict
            iso_volid: ISO volume ID to attach (e.g., 'local:iso/pfSense.iso')
            
        Returns:
            Dict of parameters for Proxmox VM creation API
        """
        vm = config["vm"]
        storage_config = config["storage"]
        network_config = config["network"]
        
        params = {
            # Basic settings
            "name": vm["name"],
            "description": vm.get("description", ""),
            "bios": vm.get("bios", "seabios"),
            "boot": vm.get("boot_order", "cdn"),
            "onboot": 1 if vm.get("onboot", False) else 0,
            
            # CPU
            "cores": vm["resources"]["cores"],
            "sockets": vm["resources"].get("sockets", 1),
            "cpu": vm.get("cpu", {}).get("type", "host"),
            
            # Memory
            "memory": vm["resources"]["memory"],
            "balloon": vm["resources"].get("balloon", 0),
            
            # OS type (for optimizations)
            "ostype": "other",  # pfSense is FreeBSD-based
        }
        
        # Storage - build disk specification
        disk = storage_config["disk"]
        disk_spec = f"{disk['storage']}:{disk['size']}"
        disk_opts = []
        
        if disk.get("format"):
            disk_opts.append(f"format={disk['format']}")
        if disk.get("cache"):
            disk_opts.append(f"cache={disk['cache']}")
        if disk.get("discard"):
            disk_opts.append("discard=on")
        if disk.get("ssd"):
            disk_opts.append("ssd=1")
        
        if disk_opts:
            disk_spec += f",{','.join(disk_opts)}"
        
        params[disk.get("interface", "virtio0")] = disk_spec
        
        # Network interfaces
        for net in network_config:
            iface = net["interface"]
            net_spec = f"model={net.get('model', 'virtio')},bridge={net['bridge']}"
            
            if net.get("firewall"):
                net_spec += ",firewall=1"
            if net.get("tag"):
                net_spec += f",tag={net['tag']}"
            
            params[iface] = net_spec
        
        # CD-ROM with ISO
        if iso_volid:
            params["ide2"] = f"{iso_volid},media=cdrom"
        
        return params
    
    def create_vm(
        self,
        config: dict[str, Any],
        iso_volid: str | None = None,
        dry_run: bool = False,
    ) -> int:
        """Create a VM from configuration.
        
        Args:
            config: VM configuration dict
            iso_volid: ISO volume ID to attach
            dry_run: If True, only print what would be done
            
        Returns:
            VM ID
        """
        vmid = config["vm"]["vmid"]
        vm_name = config["vm"]["name"]
        
        # Check if VM already exists
        if self.client.vm_exists(vmid):
            console.print(f"[yellow]⚠[/yellow] VM {vmid} ({vm_name}) already exists")
            return vmid
        
        # Verify required network bridges exist
        missing_bridges = self.network_manager.verify_vm_networks(config["network"])
        if missing_bridges:
            raise ValueError(
                f"Required network bridge(s) not found: {', '.join(missing_bridges)}. "
                f"Create them first or update network config."
            )
        
        # Build API parameters
        params = self.build_vm_params(config, iso_volid)
        
        if dry_run:
            console.print(f"[yellow]DRY RUN:[/yellow] Would create VM {vmid} ({vm_name})")
            self._print_vm_params(params)
            return vmid
        
        console.print(f"[dim]Creating VM {vmid} ({vm_name})...[/dim]")
        
        # Create VM
        upid = self.client.create_vm(vmid, **params)
        
        # Wait for task to complete
        console.print("[dim]Waiting for VM creation to complete...[/dim]")
        self.client.wait_for_task(upid)
        
        console.print(f"[green]✓[/green] Created VM {vmid} ({vm_name})")
        return vmid
    
    def _print_vm_params(self, params: dict) -> None:
        """Print VM parameters in a readable format."""
        table = Table(title="VM Parameters")
        table.add_column("Parameter", style="cyan")
        table.add_column("Value", style="yellow")
        
        for key, value in sorted(params.items()):
            table.add_row(key, str(value))
        
        console.print(table)
    
    def print_vm_info(self, vmid: int) -> None:
        """Print detailed info about a VM."""
        if not self.client.vm_exists(vmid):
            console.print(f"[red]✗[/red] VM {vmid} not found")
            return
        
        config = self.client.get_vm_config(vmid)
        status = self.client.get_vm_status(vmid)
        
        table = Table(title=f"VM {vmid} Information")
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="yellow")
        
        # Basic info
        table.add_row("Name", config.get("name", ""))
        table.add_row("Status", status.get("status", "unknown"))
        table.add_row("Cores", str(config.get("cores", "")))
        table.add_row("Memory", f"{config.get('memory', 0)} MB")
        
        # Network interfaces
        for i in range(10):
            key = f"net{i}"
            if key in config:
                table.add_row(f"Network {i}", config[key])
        
        # Disks
        for key in ["virtio0", "scsi0", "ide0", "ide2"]:
            if key in config:
                table.add_row(key.upper(), config[key])
        
        console.print(table)
        
        # Print console access URL
        host = self.client.config["host"]
        node = self.client.node
        console.print(
            f"\n[dim]Console URL: https://{host}:8006/?console=kvm&vmid={vmid}&node={node}[/dim]"
        )
    
    def list_vms(self) -> None:
        """Print list of all VMs."""
        vms = self.client.get_vms()
        
        table = Table(title="Virtual Machines")
        table.add_column("VMID", style="cyan")
        table.add_column("Name", style="yellow")
        table.add_column("Status", style="green")
        table.add_column("Memory", style="blue")
        table.add_column("CPUs")
        
        for vm in sorted(vms, key=lambda x: x.get("vmid", 0)):
            mem_mb = vm.get("maxmem", 0) / (1024 * 1024)
            table.add_row(
                str(vm.get("vmid", "")),
                vm.get("name", ""),
                vm.get("status", ""),
                f"{mem_mb:.0f} MB",
                str(vm.get("cpus", "")),
            )
        
        console.print(table)
    
    def delete_vm(self, vmid: int, confirm: bool = False) -> bool:
        """Delete a VM.
        
        Args:
            vmid: VM ID to delete
            confirm: Skip confirmation prompt
            
        Returns:
            True if deleted
        """
        if not self.client.vm_exists(vmid):
            console.print(f"[red]✗[/red] VM {vmid} not found")
            return False
        
        config = self.client.get_vm_config(vmid)
        vm_name = config.get("name", "unknown")
        
        if not confirm:
            console.print(f"[yellow]Warning:[/yellow] This will delete VM {vmid} ({vm_name})")
            response = console.input("Type 'yes' to confirm: ")
            if response.lower() != "yes":
                console.print("[dim]Aborted[/dim]")
                return False
        
        console.print(f"[dim]Deleting VM {vmid}...[/dim]")
        upid = self.client.delete_vm(vmid)
        self.client.wait_for_task(upid)
        
        console.print(f"[green]✓[/green] Deleted VM {vmid} ({vm_name})")
        return True
