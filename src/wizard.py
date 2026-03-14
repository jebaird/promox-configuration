"""Interactive deployment wizard for pfSense and other VMs."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from InquirerPy import inquirer
from InquirerPy.base.control import Choice
from InquirerPy.separator import Separator
from InquirerPy.validator import EmptyInputValidator
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .proxmox_client import ProxmoxClient
from .network import NetworkManager
from .pfsense_config import DnsHost, DhcpReservation, DomainOverride, UPSTREAM_DNS
from .hosts_config import HostsConfig, load_hosts_config, parse_hosts_config
from .config import get_default_domain, get_lan_subnet

console = Console()


@dataclass
class WizardConfig:
    """Configuration collected from wizard prompts."""
    
    # Network settings
    wan_bridge: str = "vmbr0"
    lan_bridge: str = "vmbr1"
    lan_physical_interface: str | None = None
    create_lan_bridge: bool = False
    
    # pfSense network settings
    lan_ip: str = "10.0.0.1"
    lan_netmask: int = 24
    dhcp_start: str = "10.0.0.100"
    dhcp_end: str = "10.0.0.254"
    
    # DNS settings
    domain: str = "local"
    upstream_dns: str = "cloudflare"
    dns_over_tls: bool = False
    register_dhcp_hostnames: bool = True
    
    # Static hosts and reservations
    dns_hosts: list[DnsHost] = field(default_factory=list)
    dhcp_reservations: list[DhcpReservation] = field(default_factory=list)
    domain_overrides: list[DomainOverride] = field(default_factory=list)
    
    # Credentials
    admin_password: str = ""
    
    # VM resources
    vmid: int = 100
    vm_name: str = "pfsense"
    cores: int = 2
    memory: int = 4096
    disk_size: str = "32G"
    
    # pfSense ISO
    pfsense_version: str = "2.7.2"
    
    # Options
    enable_ssh: bool = True
    enable_api: bool = True


class DeploymentWizard:
    """Interactive wizard for deploying pfSense."""
    
    def __init__(self, client: ProxmoxClient, hosts_path: Path | None = None):
        """Initialize wizard with Proxmox client.
        
        Args:
            client: ProxmoxClient instance
            hosts_path: Optional path to hosts.yaml for pre-loading static hosts
        """
        self.client = client
        self.network_manager = NetworkManager(client)
        self.config = WizardConfig()
        self.hosts_config: HostsConfig | None = None
        
        # Apply environment variable defaults
        subnet = get_lan_subnet()
        self.config.domain = get_default_domain()
        self.config.lan_ip = f"{subnet}.1"
        self.config.dhcp_start = f"{subnet}.100"
        self.config.dhcp_end = f"{subnet}.254"
        
        # Load hosts file if provided (overrides env defaults)
        if hosts_path and hosts_path.exists():
            try:
                self.hosts_config = load_hosts_config(hosts_path)
                # Pre-populate config from hosts file
                self.config.domain = self.hosts_config.domain
                self.config.upstream_dns = self.hosts_config.upstream_dns
                self.config.dns_over_tls = self.hosts_config.dns_over_tls
                self.config.dns_hosts = self.hosts_config.get_all_dns_hosts()
                self.config.dhcp_reservations = self.hosts_config.get_all_dhcp_reservations()
                self.config.domain_overrides = self.hosts_config.domain_overrides
            except Exception as e:
                console.print(f"[yellow]Warning: Could not load hosts file: {e}[/yellow]")
    
    def run(self) -> WizardConfig | None:
        """Run the full wizard flow.
        
        Returns:
            WizardConfig if user completes wizard, None if cancelled
        """
        try:
            self._show_welcome()
            
            # Step 1: Network Discovery
            if not self._step_network_discovery():
                return None
            
            # Step 2: Bridge Setup
            if not self._step_bridge_setup():
                return None
            
            # Step 3: pfSense Settings
            if not self._step_pfsense_settings():
                return None
            
            # Step 4: DNS Settings
            if not self._step_dns_settings():
                return None
            
            # Step 5: Static Hosts
            if not self._step_static_hosts():
                return None
            
            # Step 6: VM Resources
            if not self._step_vm_resources():
                return None
            
            # Step 7: Confirmation
            if not self._step_confirmation():
                return None
            
            return self.config
            
        except KeyboardInterrupt:
            console.print("\n[yellow]Wizard cancelled[/yellow]")
            return None
    
    def _show_welcome(self) -> None:
        """Display welcome banner."""
        welcome_text = """
This wizard will deploy [bold]pfSense[/bold] as your home router.

We'll help you:
  • Set up network bridges for WAN and LAN
  • Configure your LAN network (IP, DHCP)
  • Set up DNS with automatic hostname registration
  • Create a pre-configured pfSense VM

The deployment will be ready to use after installation completes.
        """
        panel = Panel(
            welcome_text.strip(),
            title="🔥 pfSense Deployment Wizard",
            border_style="blue",
        )
        console.print(panel)
        console.print()
    
    def _step_network_discovery(self) -> bool:
        """Step 1: Discover and display network topology."""
        console.print("[bold]Step 1/7: Network Discovery[/bold]")
        console.print("─" * 40)
        console.print("[dim]Scanning Proxmox network interfaces...[/dim]\n")
        
        topology = self.network_manager.get_network_topology()
        
        # Display bridges table
        table = Table(title="Current Network Topology")
        table.add_column("Bridge", style="cyan")
        table.add_column("Ports", style="blue")
        table.add_column("IP Address", style="yellow")
        table.add_column("Role", style="green")
        
        wan_bridge = topology.get("wan_bridge")
        has_vmbr1 = False
        
        for bridge in topology.get("bridges", []):
            role = ""
            if bridge["name"] == wan_bridge:
                role = "✓ WAN"
            if bridge["name"] == "vmbr1":
                has_vmbr1 = True
                if not role:
                    role = "LAN"
            
            table.add_row(
                bridge["name"],
                bridge["ports"] or "-",
                bridge["address"] or "-",
                role,
            )
        
        # Add missing vmbr1 row if not present
        if not has_vmbr1:
            table.add_row("vmbr1", "-", "-", "[red]✗ Missing[/red]")
        
        console.print(table)
        console.print()
        
        # Store WAN bridge
        self.config.wan_bridge = wan_bridge or "vmbr0"
        
        # Check if LAN bridge needs to be created
        if not has_vmbr1:
            self.config.create_lan_bridge = True
            console.print("[yellow]Note:[/yellow] LAN bridge (vmbr1) will need to be created.\n")
        
        # Show available physical interfaces
        available = topology.get("available_for_lan", [])
        if available and self.config.create_lan_bridge:
            console.print("[bold]Available physical interfaces for LAN:[/bold]")
            for iface in available:
                status = "active" if iface["active"] else "inactive"
                console.print(f"  • {iface['name']} ({status})")
            console.print()
        
        proceed = inquirer.confirm(
            message="Continue with network setup?",
            default=True,
        ).execute()
        
        console.print()
        return proceed
    
    def _step_bridge_setup(self) -> bool:
        """Step 2: Configure LAN bridge if needed."""
        console.print("[bold]Step 2/7: Bridge Setup[/bold]")
        console.print("─" * 40)
        
        if not self.config.create_lan_bridge:
            console.print("[green]✓[/green] LAN bridge (vmbr1) already exists\n")
            return True
        
        topology = self.network_manager.get_network_topology()
        available = topology.get("available_for_lan", [])
        
        if not available:
            # No physical interfaces - create bridge without port (VM-only network)
            console.print(
                "[yellow]No unused physical interfaces found.[/yellow]\n"
                "A virtual bridge will be created for VM-to-VM communication.\n"
                "You can add a physical interface later via Proxmox UI.\n"
            )
            
            proceed = inquirer.confirm(
                message="Create virtual LAN bridge (vmbr1)?",
                default=True,
            ).execute()
            
            if proceed:
                self.config.lan_physical_interface = None
            console.print()
            return proceed
        
        # Let user select physical interface
        choices = [
            Choice(value=iface["name"], name=f"{iface['name']} ({'active' if iface['active'] else 'inactive'})")
            for iface in available
        ]
        choices.append(Separator())
        choices.append(Choice(value=None, name="None (virtual bridge only)"))
        
        selected = inquirer.select(
            message="Select physical interface for LAN bridge:",
            choices=choices,
        ).execute()
        
        self.config.lan_physical_interface = selected
        
        if selected:
            console.print(f"\n[green]✓[/green] Will bind {selected} to vmbr1\n")
        else:
            console.print("\n[green]✓[/green] Will create virtual bridge (no physical port)\n")
        
        return True
    
    def _step_pfsense_settings(self) -> bool:
        """Step 3: Configure pfSense network settings."""
        console.print("[bold]Step 3/7: pfSense Network Settings[/bold]")
        console.print("─" * 40)
        console.print()
        
        # LAN IP
        lan_ip = inquirer.text(
            message="LAN IP address:",
            default=self.config.lan_ip,
            validate=lambda x: self._validate_ip(x) or "Invalid IP address",
        ).execute()
        self.config.lan_ip = lan_ip
        
        # LAN netmask
        netmask = inquirer.text(
            message="LAN subnet mask (CIDR notation):",
            default=str(self.config.lan_netmask),
            validate=lambda x: x.isdigit() and 1 <= int(x) <= 32 or "Must be 1-32",
        ).execute()
        self.config.lan_netmask = int(netmask)
        
        # Calculate sensible DHCP defaults based on LAN IP
        base_ip = ".".join(lan_ip.split(".")[:-1])
        default_start = f"{base_ip}.100"
        default_end = f"{base_ip}.254"
        
        # DHCP start
        dhcp_start = inquirer.text(
            message="DHCP range start:",
            default=default_start,
            validate=lambda x: self._validate_ip(x) or "Invalid IP address",
        ).execute()
        self.config.dhcp_start = dhcp_start
        
        # DHCP end
        dhcp_end = inquirer.text(
            message="DHCP range end:",
            default=default_end,
            validate=lambda x: self._validate_ip(x) or "Invalid IP address",
        ).execute()
        self.config.dhcp_end = dhcp_end
        
        # Admin password
        console.print()
        admin_password = inquirer.secret(
            message="pfSense admin password:",
            validate=EmptyInputValidator("Password cannot be empty"),
        ).execute()
        self.config.admin_password = admin_password
        
        # Confirm password
        confirm_password = inquirer.secret(
            message="Confirm password:",
        ).execute()
        
        if admin_password != confirm_password:
            console.print("[red]Passwords do not match. Please try again.[/red]\n")
            return self._step_pfsense_settings()
        
        console.print()
        return True
    
    def _step_dns_settings(self) -> bool:
        """Step 4: Configure DNS settings."""
        console.print("[bold]Step 4/7: DNS Settings[/bold]")
        console.print("─" * 40)
        console.print()
        
        # Domain
        domain = inquirer.text(
            message="Local domain name:",
            default=self.config.domain,
        ).execute()
        self.config.domain = domain
        
        # Upstream DNS
        dns_choices = [
            Choice(value="cloudflare", name="Cloudflare (1.1.1.1, 1.0.0.1)"),
            Choice(value="google", name="Google (8.8.8.8, 8.8.4.4)"),
            Choice(value="quad9", name="Quad9 (9.9.9.9, 149.112.112.112)"),
            Choice(value="opendns", name="OpenDNS (208.67.222.222, 208.67.220.220)"),
            Separator(),
            Choice(value="custom", name="Custom DNS servers"),
        ]
        
        upstream = inquirer.select(
            message="Upstream DNS servers:",
            choices=dns_choices,
            default=self.config.upstream_dns if self.config.upstream_dns in UPSTREAM_DNS else "cloudflare",
        ).execute()
        
        if upstream == "custom":
            custom_dns = inquirer.text(
                message="Enter DNS servers (comma-separated IPs):",
                default=self.config.upstream_dns if self.config.upstream_dns not in UPSTREAM_DNS else "",
                validate=lambda x: len(x.strip()) > 0 or "Enter at least one DNS server",
            ).execute()
            self.config.upstream_dns = custom_dns
        else:
            self.config.upstream_dns = upstream
        
        # DNS over TLS
        dns_tls = inquirer.confirm(
            message="Enable DNS-over-TLS for upstream queries?",
            default=self.config.dns_over_tls,
        ).execute()
        self.config.dns_over_tls = dns_tls
        
        # DHCP hostname registration
        register_dhcp = inquirer.confirm(
            message="Register DHCP hostnames in DNS automatically?",
            default=self.config.register_dhcp_hostnames,
        ).execute()
        self.config.register_dhcp_hostnames = register_dhcp
        
        if register_dhcp:
            console.print(
                f"\n[dim]Devices will be accessible as hostname.{domain}[/dim]"
            )
        
        console.print()
        return True
    
    def _step_static_hosts(self) -> bool:
        """Step 5: Configure static hosts and reservations."""
        console.print("[bold]Step 5/7: Static Hosts & Reservations[/bold]")
        console.print("─" * 40)
        console.print()
        
        # Show loaded hosts if any
        if self.config.dns_hosts or self.config.dhcp_reservations:
            console.print("[green]✓[/green] Loaded from hosts.yaml:")
            console.print(f"  • {len(self.config.dns_hosts)} DNS host entries")
            console.print(f"  • {len(self.config.dhcp_reservations)} DHCP reservations")
            console.print(f"  • {len(self.config.domain_overrides)} domain overrides")
            console.print()
            
            # Show details table
            if self.config.dns_hosts:
                table = Table(title="Static DNS Hosts")
                table.add_column("Hostname", style="cyan")
                table.add_column("IP", style="yellow")
                table.add_column("Description", style="dim")
                
                for host in self.config.dns_hosts:
                    table.add_row(
                        f"{host.host}.{host.domain}",
                        host.ip,
                        host.description or "-"
                    )
                console.print(table)
                console.print()
            
            if self.config.domain_overrides:
                table = Table(title="Split DNS Overrides")
                table.add_column("Domain", style="cyan")
                table.add_column("IP", style="yellow")
                table.add_column("Description", style="dim")
                
                for override in self.config.domain_overrides:
                    table.add_row(
                        override.domain,
                        override.ip,
                        override.description or "-"
                    )
                console.print(table)
                console.print()
            
            modify = inquirer.confirm(
                message="Modify these entries?",
                default=False,
            ).execute()
            
            if not modify:
                return True
        
        # Interactive host entry
        add_more = inquirer.confirm(
            message="Add a static DNS host entry?",
            default=False,
        ).execute()
        
        while add_more:
            hostname = inquirer.text(
                message="Hostname (without domain):",
                validate=lambda x: len(x.strip()) > 0 or "Hostname required",
            ).execute()
            
            ip = inquirer.text(
                message="IP address:",
                validate=lambda x: self._validate_ip(x) or "Invalid IP address",
            ).execute()
            
            mac = inquirer.text(
                message="MAC address (optional, for DHCP reservation):",
                default="",
            ).execute()
            
            description = inquirer.text(
                message="Description (optional):",
                default="",
            ).execute()
            
            # Add DNS host
            self.config.dns_hosts.append(DnsHost(
                host=hostname,
                domain=self.config.domain,
                ip=ip,
                description=description if description else None
            ))
            
            # Add DHCP reservation if MAC provided
            if mac:
                self.config.dhcp_reservations.append(DhcpReservation(
                    mac=mac.lower().replace("-", ":"),
                    ip=ip,
                    hostname=hostname,
                    description=description if description else None
                ))
            
            console.print(f"[green]✓[/green] Added {hostname}.{self.config.domain} → {ip}")
            
            add_more = inquirer.confirm(
                message="Add another host?",
                default=False,
            ).execute()
        
        console.print()
        return True
    
    def _step_vm_resources(self) -> bool:
        """Step 4: Configure VM resources."""
        console.print("[bold]Step 6/7: VM Resources[/bold]")
        console.print("─" * 40)
        console.print()
        
        # Check for existing VM
        existing_vms = self.client.get_vms()
        existing_ids = {vm.get("vmid") for vm in existing_vms}
        
        # VM ID
        default_vmid = self.config.vmid
        while default_vmid in existing_ids:
            default_vmid += 1
        
        vmid = inquirer.text(
            message="VM ID:",
            default=str(default_vmid),
            validate=lambda x: (x.isdigit() and int(x) >= 100) or "Must be >= 100",
        ).execute()
        self.config.vmid = int(vmid)
        
        if self.config.vmid in existing_ids:
            console.print(f"[yellow]Warning: VM {self.config.vmid} already exists and will be skipped[/yellow]")
        
        # VM name
        vm_name = inquirer.text(
            message="VM name:",
            default=self.config.vm_name,
        ).execute()
        self.config.vm_name = vm_name
        
        # CPU cores
        cores = inquirer.text(
            message="CPU cores:",
            default=str(self.config.cores),
            validate=lambda x: x.isdigit() and int(x) >= 1 or "Must be >= 1",
        ).execute()
        self.config.cores = int(cores)
        
        # Memory
        memory = inquirer.text(
            message="Memory (MB):",
            default=str(self.config.memory),
            validate=lambda x: x.isdigit() and int(x) >= 512 or "Must be >= 512",
        ).execute()
        self.config.memory = int(memory)
        
        # Disk size
        disk_size = inquirer.text(
            message="Disk size:",
            default=self.config.disk_size,
        ).execute()
        self.config.disk_size = disk_size
        
        console.print()
        return True
    
    def _step_confirmation(self) -> bool:
        """Step 5: Show summary and get confirmation."""
        console.print("[bold]Step 7/7: Confirmation[/bold]")
        console.print("─" * 40)
        console.print()
        
        # Summary table
        table = Table(title="Deployment Summary", show_header=False)
        table.add_column("Setting", style="cyan")
        table.add_column("Value", style="yellow")
        
        table.add_row("VM Name", self.config.vm_name)
        table.add_row("VM ID", str(self.config.vmid))
        table.add_row("Resources", f"{self.config.cores} cores, {self.config.memory}MB RAM, {self.config.disk_size} disk")
        table.add_row("", "")
        table.add_row("WAN Interface", f"{self.config.wan_bridge} (DHCP)")
        table.add_row("LAN Interface", f"{self.config.lan_bridge} → {self.config.lan_ip}/{self.config.lan_netmask}")
        
        if self.config.create_lan_bridge:
            if self.config.lan_physical_interface:
                table.add_row("LAN Bridge", f"Create vmbr1 with {self.config.lan_physical_interface}")
            else:
                table.add_row("LAN Bridge", "Create vmbr1 (virtual)")
        
        table.add_row("", "")
        table.add_row("DHCP Range", f"{self.config.dhcp_start} - {self.config.dhcp_end}")
        table.add_row("", "")
        table.add_row("Domain", self.config.domain)
        table.add_row("Upstream DNS", self.config.upstream_dns)
        table.add_row("DNS-over-TLS", "Enabled" if self.config.dns_over_tls else "Disabled")
        table.add_row("DHCP→DNS", "Enabled" if self.config.register_dhcp_hostnames else "Disabled")
        table.add_row("Static Hosts", str(len(self.config.dns_hosts)))
        table.add_row("DHCP Reservations", str(len(self.config.dhcp_reservations)))
        table.add_row("Domain Overrides", str(len(self.config.domain_overrides)))
        table.add_row("", "")
        table.add_row("SSH", "Enabled" if self.config.enable_ssh else "Disabled")
        table.add_row("API", "Enabled" if self.config.enable_api else "Disabled")
        
        console.print(table)
        console.print()
        
        proceed = inquirer.confirm(
            message="Proceed with deployment?",
            default=True,
        ).execute()
        
        console.print()
        return proceed
    
    def _validate_ip(self, ip: str) -> bool:
        """Validate an IPv4 address."""
        parts = ip.split(".")
        if len(parts) != 4:
            return False
        try:
            return all(0 <= int(part) <= 255 for part in parts)
        except ValueError:
            return False
