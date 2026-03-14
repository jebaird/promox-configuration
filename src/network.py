"""Network bridge management for Proxmox."""

from typing import Any

from rich.console import Console
from rich.table import Table

from .config import load_network_config
from .proxmox_client import ProxmoxClient

console = Console()


class NetworkManager:
    """Manages Proxmox network bridge configuration."""
    
    def __init__(self, client: ProxmoxClient, config: dict[str, Any] | None = None):
        """Initialize network manager.
        
        Args:
            client: Proxmox API client
            config: Optional network config. If None, loads from config/network.yaml
        """
        self.client = client
        self.config = config or load_network_config()
    
    def list_interfaces(self) -> list[dict]:
        """Get all network interfaces with their current state."""
        return self.client.get_network_interfaces()
    
    def list_physical_interfaces(self) -> list[dict]:
        """Get only physical network interfaces (potential bridge ports)."""
        interfaces = self.list_interfaces()
        physical = []
        
        for iface in interfaces:
            iface_type = iface.get("type", "")
            iface_name = iface.get("iface", "")
            
            # Filter to physical interfaces (eth*, enp*, eno*, ens*)
            if iface_type == "eth" or (
                iface_type == "" and 
                any(iface_name.startswith(p) for p in ["eth", "enp", "eno", "ens"])
            ):
                physical.append(iface)
        
        return physical
    
    def list_bridges(self) -> list[dict]:
        """Get only bridge interfaces."""
        interfaces = self.list_interfaces()
        return [i for i in interfaces if i.get("type") == "bridge"]
    
    def bridge_exists(self, name: str) -> bool:
        """Check if a bridge exists.
        
        Args:
            name: Bridge name (e.g., 'vmbr1')
        """
        return self.client.get_network_interface(name) is not None
    
    def print_interfaces_table(self) -> None:
        """Print a formatted table of all network interfaces."""
        interfaces = self.list_interfaces()
        
        table = Table(title="Proxmox Network Interfaces")
        table.add_column("Interface", style="cyan")
        table.add_column("Type", style="magenta")
        table.add_column("Active", style="green")
        table.add_column("Address", style="yellow")
        table.add_column("Bridge Ports", style="blue")
        table.add_column("Comments")
        
        for iface in interfaces:
            table.add_row(
                iface.get("iface", ""),
                iface.get("type", ""),
                "✓" if iface.get("active") else "",
                iface.get("cidr", iface.get("address", "")),
                iface.get("bridge_ports", ""),
                iface.get("comments", ""),
            )
        
        console.print(table)
    
    def create_bridge(
        self,
        name: str,
        physical_interface: str | None = None,
        address: str | None = None,
        comment: str | None = None,
    ) -> bool:
        """Create a network bridge if it doesn't exist.
        
        Args:
            name: Bridge name (e.g., 'vmbr1')
            physical_interface: Physical interface to bind (optional)
            address: IP address in CIDR notation (optional)
            comment: Description for the bridge
            
        Returns:
            True if bridge was created, False if it already exists
        """
        if self.bridge_exists(name):
            console.print(f"[yellow]⚠[/yellow] Bridge {name} already exists")
            return False
        
        console.print(f"[dim]Creating bridge {name}...[/dim]")
        
        self.client.create_bridge(
            name=name,
            ports=physical_interface if physical_interface else None,
            address=address,
            comments=comment,
        )
        
        console.print(f"[green]✓[/green] Created bridge {name}")
        return True
    
    def setup_bridges_from_config(self, apply: bool = False) -> dict[str, bool]:
        """Create all bridges defined in network config.
        
        Args:
            apply: If True, apply network changes immediately
            
        Returns:
            Dict mapping bridge names to whether they were created
        """
        results = {}
        bridges_config = self.config.get("bridges", {})
        
        for bridge_id, bridge_def in bridges_config.items():
            name = bridge_def.get("name")
            
            if not name:
                console.print(f"[red]✗[/red] Bridge '{bridge_id}' has no name defined")
                continue
            
            # Skip bridges marked as already existing
            if bridge_def.get("exists", False):
                if self.bridge_exists(name):
                    console.print(f"[dim]Bridge {name} exists (as expected)[/dim]")
                    results[name] = False
                else:
                    console.print(
                        f"[yellow]⚠[/yellow] Bridge {name} marked as 'exists: true' "
                        f"but not found on Proxmox"
                    )
                    results[name] = False
                continue
            
            # Create bridge
            created = self.create_bridge(
                name=name,
                physical_interface=bridge_def.get("physical_interface"),
                address=bridge_def.get("address"),
                comment=bridge_def.get("comment"),
            )
            results[name] = created
        
        # Apply changes if requested and any bridges were created
        if apply and any(results.values()):
            self.apply_changes()
        
        return results
    
    def apply_changes(self) -> None:
        """Apply pending network configuration changes."""
        console.print("[dim]Applying network configuration...[/dim]")
        self.client.apply_network_config()
        console.print("[green]✓[/green] Network configuration applied")
    
    def revert_changes(self) -> None:
        """Revert pending network configuration changes."""
        console.print("[dim]Reverting network configuration...[/dim]")
        self.client.revert_network_config()
        console.print("[yellow]⚠[/yellow] Network configuration reverted")
    
    def verify_vm_networks(self, network_config: list[dict]) -> list[str]:
        """Verify that all bridges required by a VM exist.
        
        Args:
            network_config: VM network configuration (list of interface defs)
            
        Returns:
            List of missing bridge names
        """
        missing = []
        
        for net in network_config:
            bridge = net.get("bridge")
            if bridge and not self.bridge_exists(bridge):
                missing.append(bridge)
        
        return missing

    # -------------------------------------------------------------------------
    # Discovery methods for interactive wizard
    # -------------------------------------------------------------------------
    
    def get_network_topology(self) -> dict[str, Any]:
        """Get complete network topology for wizard display.
        
        Returns:
            Dict with 'bridges', 'physical', 'wan_bridge', 'available_for_lan'
        """
        interfaces = self.list_interfaces()
        bridges = []
        physical = []
        bridged_ports = set()
        
        # First pass: collect bridges and their ports
        for iface in interfaces:
            if iface.get("type") == "bridge":
                bridge_info = {
                    "name": iface.get("iface", ""),
                    "ports": iface.get("bridge_ports", ""),
                    "address": iface.get("cidr", iface.get("address", "")),
                    "gateway": iface.get("gateway", ""),
                    "active": iface.get("active", False),
                    "comments": iface.get("comments", ""),
                }
                bridges.append(bridge_info)
                # Track which physical interfaces are already bridged
                if bridge_info["ports"]:
                    for port in bridge_info["ports"].split():
                        bridged_ports.add(port)
        
        # Second pass: collect physical interfaces
        for iface in interfaces:
            iface_name = iface.get("iface", "")
            iface_type = iface.get("type", "")
            
            # Identify physical interfaces
            is_physical = iface_type == "eth" or (
                iface_type == "" and 
                any(iface_name.startswith(p) for p in ["eth", "enp", "eno", "ens"])
            )
            
            if is_physical:
                physical.append({
                    "name": iface_name,
                    "active": iface.get("active", False),
                    "address": iface.get("cidr", iface.get("address", "")),
                    "in_bridge": iface_name in bridged_ports,
                })
        
        # Identify WAN bridge (has gateway)
        wan_bridge = None
        for bridge in bridges:
            if bridge.get("gateway"):
                wan_bridge = bridge["name"]
                break
        
        # If no gateway found, assume vmbr0 is WAN
        if not wan_bridge:
            for bridge in bridges:
                if bridge["name"] == "vmbr0":
                    wan_bridge = "vmbr0"
                    break
        
        # Find physical interfaces available for LAN bridge
        available_for_lan = [p for p in physical if not p["in_bridge"]]
        
        return {
            "bridges": bridges,
            "physical": physical,
            "wan_bridge": wan_bridge,
            "available_for_lan": available_for_lan,
        }
    
    def suggest_wan_bridge(self) -> str | None:
        """Find the bridge that appears to be the WAN interface.
        
        Returns:
            Bridge name (e.g., 'vmbr0') or None if not found
        """
        topology = self.get_network_topology()
        return topology.get("wan_bridge")
    
    def suggest_lan_candidates(self) -> list[dict]:
        """Find physical interfaces that could be used for a LAN bridge.
        
        Returns:
            List of available physical interface info dicts
        """
        topology = self.get_network_topology()
        return topology.get("available_for_lan", [])
    
    def get_bridge_info(self, name: str) -> dict | None:
        """Get detailed info about a specific bridge.
        
        Args:
            name: Bridge name
            
        Returns:
            Bridge info dict or None if not found
        """
        topology = self.get_network_topology()
        for bridge in topology.get("bridges", []):
            if bridge["name"] == name:
                return bridge
        return None
    
    def print_topology_table(self) -> None:
        """Print network topology in a wizard-friendly format."""
        topology = self.get_network_topology()
        
        # Bridges table
        table = Table(title="Network Bridges")
        table.add_column("Bridge", style="cyan")
        table.add_column("Ports", style="blue")
        table.add_column("IP Address", style="yellow")
        table.add_column("Role", style="green")
        
        wan_bridge = topology.get("wan_bridge")
        
        for bridge in topology.get("bridges", []):
            role = ""
            if bridge["name"] == wan_bridge:
                role = "✓ WAN (has gateway)" if bridge.get("gateway") else "✓ WAN (assumed)"
            
            table.add_row(
                bridge["name"],
                bridge["ports"] or "-",
                bridge["address"] or "-",
                role,
            )
        
        console.print(table)
        
        # Available interfaces for LAN
        available = topology.get("available_for_lan", [])
        if available:
            console.print("\n[bold]Available physical interfaces for LAN bridge:[/bold]")
            for iface in available:
                status = "[green]active[/green]" if iface["active"] else "[dim]inactive[/dim]"
                console.print(f"  • {iface['name']} ({status})")
        else:
            console.print("\n[yellow]No unused physical interfaces available for LAN bridge[/yellow]")
