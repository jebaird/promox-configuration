"""Test environment management for safe pfSense experimentation."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from .config import load_vm_config
from .proxmox_client import ProxmoxClient
from .network import NetworkManager
from .iso_manager import ISOManager
from .vm_creator import VMCreator
from .pfsense_config import PfSenseConfigBuilder

console = Console()

# Test environment constants
TEST_BRIDGE = "vmbr2"
TEST_SUBNET = "192.168.99"
TEST_DOMAIN = "test.local"
PFSENSE_TEST_VMID = 101
TEST_CLIENT_VMID = 199


@dataclass
class TestEnvStatus:
    """Status of test environment components."""
    bridge_exists: bool = False
    pfsense_exists: bool = False
    pfsense_running: bool = False
    client_exists: bool = False
    client_running: bool = False
    pfsense_iso_ready: bool = False
    alpine_iso_ready: bool = False


class TestEnvironment:
    """Manages isolated test environment for pfSense experimentation."""
    
    def __init__(self, client: ProxmoxClient):
        """Initialize test environment manager.
        
        Args:
            client: Proxmox API client
        """
        self.client = client
        self.network = NetworkManager(client)
        self.iso_manager = ISOManager(client)
        self.vm_creator = VMCreator(client)
    
    def get_status(self) -> TestEnvStatus:
        """Get current status of test environment components."""
        status = TestEnvStatus()
        
        # Check bridge
        status.bridge_exists = self.network.bridge_exists(TEST_BRIDGE)
        
        # Check pfSense test VM
        status.pfsense_exists = self.client.vm_exists(PFSENSE_TEST_VMID)
        if status.pfsense_exists:
            vm_status = self.client.get_vm_status(PFSENSE_TEST_VMID)
            status.pfsense_running = vm_status.get("status") == "running"
        
        # Check test client VM
        status.client_exists = self.client.vm_exists(TEST_CLIENT_VMID)
        if status.client_exists:
            vm_status = self.client.get_vm_status(TEST_CLIENT_VMID)
            status.client_running = vm_status.get("status") == "running"
        
        # Check ISOs
        status.pfsense_iso_ready = self.iso_manager.iso_exists_on_proxmox(
            "local", 
            self.iso_manager.get_pfsense_iso_filename("2.7.2")
        )
        status.alpine_iso_ready = self.iso_manager.iso_exists_on_proxmox(
            "local",
            "alpine-virt-3.19.1-x86_64.iso"
        )
        
        return status
    
    def print_status(self) -> None:
        """Print formatted status of test environment."""
        status = self.get_status()
        
        table = Table(title="Test Environment Status")
        table.add_column("Component", style="cyan")
        table.add_column("Status", style="green")
        table.add_column("Details")
        
        # Bridge
        bridge_status = "[green]✓ exists[/green]" if status.bridge_exists else "[dim]not created[/dim]"
        table.add_row(f"Bridge {TEST_BRIDGE}", bridge_status, "Isolated test network")
        
        # pfSense
        if status.pfsense_exists:
            pf_status = "[green]✓ running[/green]" if status.pfsense_running else "[yellow]stopped[/yellow]"
        else:
            pf_status = "[dim]not created[/dim]"
        table.add_row(f"pfSense (VMID {PFSENSE_TEST_VMID})", pf_status, f"{TEST_SUBNET}.1")
        
        # Test client
        if status.client_exists:
            client_status = "[green]✓ running[/green]" if status.client_running else "[yellow]stopped[/yellow]"
        else:
            client_status = "[dim]not created[/dim]"
        table.add_row(f"Alpine Client (VMID {TEST_CLIENT_VMID})", client_status, "DHCP client")
        
        # ISOs
        pf_iso = "[green]✓ ready[/green]" if status.pfsense_iso_ready else "[yellow]needs download[/yellow]"
        table.add_row("pfSense ISO", pf_iso, "2.7.2")
        
        alpine_iso = "[green]✓ ready[/green]" if status.alpine_iso_ready else "[yellow]needs download[/yellow]"
        table.add_row("Alpine ISO", alpine_iso, "3.19.1")
        
        console.print(table)
    
    def create(self, skip_client: bool = False, skip_iso: bool = False, dry_run: bool = False) -> bool:
        """Create the complete test environment.
        
        Args:
            skip_client: If True, don't create the Alpine test client
            skip_iso: If True, skip ISO download (create VMs without ISO attached)
            dry_run: If True, only show what would be done
            
        Returns:
            True if successful
        """
        console.print("\n[bold]Creating isolated test environment[/bold]\n")
        console.print(f"  Subnet: {TEST_SUBNET}.0/24")
        console.print(f"  Bridge: {TEST_BRIDGE} (no physical interface)")
        console.print(f"  Domain: {TEST_DOMAIN}")
        console.print()
        
        if dry_run:
            console.print("[yellow]DRY RUN[/yellow] - showing what would be done:\n")
        
        iso_ready = False
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            
            # Step 1: Create bridge
            task = progress.add_task("Creating test bridge...", total=None)
            if not self._create_bridge(dry_run):
                progress.update(task, description="[red]✗[/red] Bridge creation failed")
                return False
            progress.update(task, description=f"[green]✓[/green] Bridge {TEST_BRIDGE} ready")
            
            # Step 2: Prepare pfSense ISO (optional)
            if skip_iso:
                progress.update(task, description="[dim]Skipping ISO download[/dim]")
                iso_ready = False
            else:
                progress.update(task, description="Preparing pfSense ISO...")
                iso_ready = self._prepare_pfsense_iso(dry_run)
                if not iso_ready:
                    progress.update(task, description="[yellow]⚠[/yellow] pfSense ISO not ready - VM will be created without ISO")
                else:
                    progress.update(task, description="[green]✓[/green] pfSense ISO ready")
            
            # Step 3: Create pfSense test VM
            progress.update(task, description="Creating pfSense test VM...")
            if not self._create_pfsense_vm(dry_run, with_iso=iso_ready):
                progress.update(task, description="[red]✗[/red] pfSense VM creation failed")
                return False
            progress.update(task, description=f"[green]✓[/green] pfSense VM {PFSENSE_TEST_VMID} created")
            
            # Step 4: Create test client (optional)
            if not skip_client:
                progress.update(task, description="Creating Alpine test client...")
                if not self._create_client_vm(dry_run):
                    progress.update(task, description="[yellow]⚠[/yellow] Test client creation failed")
                else:
                    progress.update(task, description=f"[green]✓[/green] Test client {TEST_CLIENT_VMID} created")
        
        # Print next steps
        self._print_next_steps()
        return True
    
    def _create_bridge(self, dry_run: bool) -> bool:
        """Create isolated test bridge."""
        if self.network.bridge_exists(TEST_BRIDGE):
            console.print(f"  [dim]Bridge {TEST_BRIDGE} already exists[/dim]")
            return True
        
        if dry_run:
            console.print(f"  [blue]→[/blue] Would create bridge {TEST_BRIDGE}")
            return True
        
        try:
            self.client.create_bridge(
                name=TEST_BRIDGE,
                ports=None,  # No physical interface = isolated
                comments="TEST - Isolated test network (no external connectivity)",
            )
            self.network.apply_changes()
            return True
        except Exception as e:
            console.print(f"  [red]Error:[/red] {e}")
            return False
    
    def _prepare_pfsense_iso(self, dry_run: bool) -> bool:
        """Ensure pfSense ISO is available."""
        iso_filename = self.iso_manager.get_pfsense_iso_filename("2.7.2")
        
        if self.iso_manager.iso_exists_on_proxmox("local", iso_filename):
            console.print(f"  [dim]pfSense ISO already uploaded[/dim]")
            return True
        
        if dry_run:
            console.print(f"  [blue]→[/blue] Would download pfSense 2.7.2 ISO")
            return True
        
        try:
            self.iso_manager.download_and_upload_pfsense("2.7.2", "local")
            return True
        except Exception as e:
            console.print(f"  [yellow]Warning:[/yellow] Could not download ISO: {e}")
            console.print(f"  [dim]You can upload the ISO manually to Proxmox[/dim]")
            return False
    
    def _create_pfsense_vm(self, dry_run: bool, with_iso: bool = True) -> bool:
        """Create pfSense test VM.
        
        Args:
            dry_run: If True, only show what would be done
            with_iso: If True, attach the pfSense ISO
        """
        if self.client.vm_exists(PFSENSE_TEST_VMID):
            console.print(f"  [dim]pfSense test VM {PFSENSE_TEST_VMID} already exists[/dim]")
            return True
        
        if dry_run:
            console.print(f"  [blue]→[/blue] Would create pfSense VM {PFSENSE_TEST_VMID}")
            return True
        
        try:
            config = load_vm_config("pfsense-test")
            
            iso_volid = None
            if with_iso:
                iso_filename = self.iso_manager.get_pfsense_iso_filename("2.7.2")
                iso_volid = f"local:iso/{iso_filename}"
            
            self.vm_creator.create_vm(config, iso_volid=iso_volid)
            return True
        except Exception as e:
            console.print(f"  [red]Error:[/red] {e}")
            return False
    
    def _create_client_vm(self, dry_run: bool) -> bool:
        """Create Alpine test client VM."""
        if self.client.vm_exists(TEST_CLIENT_VMID):
            console.print(f"  [dim]Test client {TEST_CLIENT_VMID} already exists[/dim]")
            return True
        
        if dry_run:
            console.print(f"  [blue]→[/blue] Would create Alpine client VM {TEST_CLIENT_VMID}")
            return True
        
        try:
            config = load_vm_config("test-client")
            
            # Check if Alpine ISO exists
            alpine_iso = "alpine-virt-3.19.1-x86_64.iso"
            if not self.iso_manager.iso_exists_on_proxmox("local", alpine_iso):
                console.print(f"  [yellow]Warning:[/yellow] Alpine ISO not found")
                console.print(f"  [dim]Download from: https://alpinelinux.org/downloads/[/dim]")
                console.print(f"  [dim]Upload '{alpine_iso}' to Proxmox 'local' storage[/dim]")
                # Create VM anyway, user can attach ISO later
                iso_volid = None
            else:
                iso_volid = f"local:iso/{alpine_iso}"
            
            self.vm_creator.create_vm(config, iso_volid=iso_volid)
            return True
        except Exception as e:
            console.print(f"  [red]Error:[/red] {e}")
            return False
    
    def _print_next_steps(self) -> None:
        """Print next steps after environment creation."""
        host = self.client.config["host"]
        node = self.client.node
        
        text = f"""
[bold]Test environment created![/bold]

[bold]Next steps:[/bold]

1. Start pfSense test VM and complete installation:
   [cyan]https://{host}:8006/?console=kvm&vmid={PFSENSE_TEST_VMID}&node={node}[/cyan]

2. In pfSense installer:
   • Assign vtnet0 as WAN (can leave unconfigured for testing)
   • Assign vtnet1 as LAN
   • Set LAN IP: [cyan]{TEST_SUBNET}.1/24[/cyan]
   • Enable DHCP server: [cyan]{TEST_SUBNET}.100 - {TEST_SUBNET}.254[/cyan]

3. Start Alpine test client:
   [cyan]https://{host}:8006/?console=kvm&vmid={TEST_CLIENT_VMID}&node={node}[/cyan]

4. In Alpine, verify DHCP:
   [cyan]# setup-interfaces  (select dhcp)[/cyan]
   [cyan]# ip addr            (should show {TEST_SUBNET}.x)[/cyan]
   [cyan]# ping {TEST_SUBNET}.1[/cyan]

5. Test DNS resolution:
   [cyan]# nslookup pfsense-test.{TEST_DOMAIN} {TEST_SUBNET}.1[/cyan]

[bold]To clean up:[/bold]
   [cyan]proxmox-config test-env destroy[/cyan]
"""
        panel = Panel(text.strip(), title="Test Environment", border_style="green")
        console.print(panel)
    
    def destroy(self, keep_bridge: bool = False, force: bool = False) -> bool:
        """Destroy test environment.
        
        Args:
            keep_bridge: If True, don't remove the bridge
            force: If True, stop running VMs before deleting
            
        Returns:
            True if successful
        """
        console.print("\n[bold]Destroying test environment[/bold]\n")
        
        errors = []
        
        # Step 1: Delete test client
        if self.client.vm_exists(TEST_CLIENT_VMID):
            console.print(f"  Deleting test client (VMID {TEST_CLIENT_VMID})...")
            try:
                status = self.client.get_vm_status(TEST_CLIENT_VMID)
                if status.get("status") == "running":
                    if force:
                        console.print("    Stopping VM...")
                        upid = self.client.stop_vm(TEST_CLIENT_VMID)
                        self.client.wait_for_task(upid, timeout=30)
                    else:
                        errors.append(f"VM {TEST_CLIENT_VMID} is running. Use --force to stop it.")
                        console.print(f"  [yellow]⚠[/yellow] VM running, skipping (use --force)")
                
                if not errors or force:
                    upid = self.client.delete_vm(TEST_CLIENT_VMID)
                    self.client.wait_for_task(upid)
                    console.print(f"  [green]✓[/green] Deleted test client")
            except Exception as e:
                errors.append(f"Failed to delete test client: {e}")
                console.print(f"  [red]✗[/red] Error: {e}")
        else:
            console.print(f"  [dim]Test client not found[/dim]")
        
        # Step 2: Delete pfSense test VM
        if self.client.vm_exists(PFSENSE_TEST_VMID):
            console.print(f"  Deleting pfSense test (VMID {PFSENSE_TEST_VMID})...")
            try:
                status = self.client.get_vm_status(PFSENSE_TEST_VMID)
                if status.get("status") == "running":
                    if force:
                        console.print("    Stopping VM...")
                        upid = self.client.stop_vm(PFSENSE_TEST_VMID)
                        self.client.wait_for_task(upid, timeout=30)
                    else:
                        errors.append(f"VM {PFSENSE_TEST_VMID} is running. Use --force to stop it.")
                        console.print(f"  [yellow]⚠[/yellow] VM running, skipping (use --force)")
                
                if not errors or force:
                    upid = self.client.delete_vm(PFSENSE_TEST_VMID)
                    self.client.wait_for_task(upid)
                    console.print(f"  [green]✓[/green] Deleted pfSense test VM")
            except Exception as e:
                errors.append(f"Failed to delete pfSense VM: {e}")
                console.print(f"  [red]✗[/red] Error: {e}")
        else:
            console.print(f"  [dim]pfSense test VM not found[/dim]")
        
        # Step 3: Delete bridge (optional)
        if not keep_bridge and self.network.bridge_exists(TEST_BRIDGE):
            console.print(f"  Deleting bridge {TEST_BRIDGE}...")
            try:
                self.client.delete_network_interface(TEST_BRIDGE)
                self.network.apply_changes()
                console.print(f"  [green]✓[/green] Deleted bridge")
            except Exception as e:
                errors.append(f"Failed to delete bridge: {e}")
                console.print(f"  [red]✗[/red] Error: {e}")
        elif keep_bridge:
            console.print(f"  [dim]Keeping bridge {TEST_BRIDGE}[/dim]")
        
        if errors:
            console.print(f"\n[yellow]Completed with {len(errors)} error(s)[/yellow]")
            return False
        
        console.print("\n[green]✓[/green] Test environment destroyed")
        return True
    
    def start(self) -> bool:
        """Start test environment VMs."""
        status = self.get_status()
        
        if not status.pfsense_exists:
            console.print("[red]✗[/red] pfSense test VM not found. Run 'test-env create' first.")
            return False
        
        # Start pfSense first (it's the gateway)
        if not status.pfsense_running:
            console.print(f"  Starting pfSense (VMID {PFSENSE_TEST_VMID})...")
            try:
                upid = self.client.start_vm(PFSENSE_TEST_VMID)
                self.client.wait_for_task(upid, timeout=60)
                console.print(f"  [green]✓[/green] pfSense started")
            except Exception as e:
                console.print(f"  [red]✗[/red] Error: {e}")
                return False
        else:
            console.print(f"  [dim]pfSense already running[/dim]")
        
        # Start test client
        if status.client_exists and not status.client_running:
            console.print(f"  Starting test client (VMID {TEST_CLIENT_VMID})...")
            try:
                upid = self.client.start_vm(TEST_CLIENT_VMID)
                self.client.wait_for_task(upid, timeout=60)
                console.print(f"  [green]✓[/green] Test client started")
            except Exception as e:
                console.print(f"  [yellow]⚠[/yellow] Could not start test client: {e}")
        
        host = self.client.config["host"]
        node = self.client.node
        console.print(f"\n[bold]Console URLs:[/bold]")
        console.print(f"  pfSense:  https://{host}:8006/?console=kvm&vmid={PFSENSE_TEST_VMID}&node={node}")
        if status.client_exists:
            console.print(f"  Client:   https://{host}:8006/?console=kvm&vmid={TEST_CLIENT_VMID}&node={node}")
        
        return True
    
    def stop(self) -> bool:
        """Stop test environment VMs."""
        status = self.get_status()
        
        # Stop client first
        if status.client_running:
            console.print(f"  Stopping test client (VMID {TEST_CLIENT_VMID})...")
            try:
                upid = self.client.stop_vm(TEST_CLIENT_VMID)
                self.client.wait_for_task(upid, timeout=30)
                console.print(f"  [green]✓[/green] Test client stopped")
            except Exception as e:
                console.print(f"  [yellow]⚠[/yellow] Error: {e}")
        
        # Stop pfSense
        if status.pfsense_running:
            console.print(f"  Stopping pfSense (VMID {PFSENSE_TEST_VMID})...")
            try:
                upid = self.client.stop_vm(PFSENSE_TEST_VMID)
                self.client.wait_for_task(upid, timeout=30)
                console.print(f"  [green]✓[/green] pfSense stopped")
            except Exception as e:
                console.print(f"  [red]✗[/red] Error: {e}")
                return False
        
        console.print("\n[green]✓[/green] Test environment stopped")
        return True
