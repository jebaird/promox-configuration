"""Deployment orchestration for pfSense and other VMs."""

from pathlib import Path
import tempfile
from typing import Any

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from .proxmox_client import ProxmoxClient
from .network import NetworkManager
from .iso_manager import ISOManager
from .vm_creator import VMCreator
from .wizard import WizardConfig
from .pfsense_config import generate_pfsense_config

console = Console()


class DeploymentResult:
    """Result of a deployment operation."""
    
    def __init__(self):
        self.success = False
        self.vmid: int | None = None
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.console_url: str = ""
        self.lan_ip: str = ""
    
    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
    
    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


class PfSenseDeployer:
    """Orchestrates pfSense VM deployment."""
    
    def __init__(self, client: ProxmoxClient):
        """Initialize deployer with Proxmox client."""
        self.client = client
        self.network_manager = NetworkManager(client)
        self.iso_manager = ISOManager(client)
        self.vm_creator = VMCreator(client)
    
    def deploy(self, config: WizardConfig, dry_run: bool = False) -> DeploymentResult:
        """Execute full pfSense deployment.
        
        Args:
            config: Configuration from wizard
            dry_run: If True, only show what would be done
            
        Returns:
            DeploymentResult with status and details
        """
        result = DeploymentResult()
        result.vmid = config.vmid
        result.lan_ip = config.lan_ip
        
        console.print("\n[bold]Deploying pfSense...[/bold]\n")
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            console=console,
        ) as progress:
            
            # Step 1: Setup network bridges
            task = progress.add_task("Setting up network bridges...", total=None)
            try:
                self._setup_network(config, dry_run)
                progress.update(task, description="[green]✓[/green] Network bridges ready")
            except Exception as e:
                result.add_error(f"Network setup failed: {e}")
                progress.update(task, description=f"[red]✗[/red] Network setup failed")
                return result
            
            # Step 2: Download ISO
            progress.update(task, description="Preparing pfSense ISO...")
            try:
                iso_volid = self._prepare_iso(config, dry_run)
                progress.update(task, description="[green]✓[/green] pfSense ISO ready")
            except Exception as e:
                result.add_error(f"ISO preparation failed: {e}")
                progress.update(task, description=f"[red]✗[/red] ISO preparation failed")
                return result
            
            # Step 3: Generate pfSense config
            progress.update(task, description="Generating pfSense configuration...")
            try:
                config_xml = self._generate_config(config)
                progress.update(task, description="[green]✓[/green] Configuration generated")
            except Exception as e:
                result.add_warning(f"Config generation failed: {e}")
                config_xml = None
                progress.update(task, description="[yellow]⚠[/yellow] Config generation skipped")
            
            # Step 4: Create VM
            progress.update(task, description="Creating VM...")
            try:
                self._create_vm(config, iso_volid, dry_run)
                progress.update(task, description="[green]✓[/green] VM created")
            except Exception as e:
                if "already exists" in str(e).lower():
                    result.add_warning(f"VM {config.vmid} already exists")
                    progress.update(task, description="[yellow]⚠[/yellow] VM already exists")
                else:
                    result.add_error(f"VM creation failed: {e}")
                    progress.update(task, description=f"[red]✗[/red] VM creation failed")
                    return result
            
            # Step 5: Start VM
            if not dry_run:
                progress.update(task, description="Starting VM...")
                try:
                    self._start_vm(config.vmid)
                    progress.update(task, description="[green]✓[/green] VM started")
                except Exception as e:
                    result.add_warning(f"Could not start VM: {e}")
                    progress.update(task, description="[yellow]⚠[/yellow] VM not started")
        
        # Build result
        result.success = len(result.errors) == 0
        result.console_url = (
            f"https://{self.client.config['host']}:8006/"
            f"?console=kvm&vmid={config.vmid}&node={self.client.node}"
        )
        
        return result
    
    def _setup_network(self, config: WizardConfig, dry_run: bool) -> None:
        """Setup network bridges if needed."""
        if not config.create_lan_bridge:
            console.print("[dim]  LAN bridge already exists[/dim]")
            return
        
        if dry_run:
            console.print(f"[dim]  Would create bridge {config.lan_bridge}[/dim]")
            return
        
        self.network_manager.create_bridge(
            name=config.lan_bridge,
            physical_interface=config.lan_physical_interface,
            comment="LAN - Internal network (created by wizard)",
        )
        
        # Apply network changes
        self.network_manager.apply_changes()
    
    def _prepare_iso(self, config: WizardConfig, dry_run: bool) -> str:
        """Download and upload pfSense ISO if needed."""
        version = config.pfsense_version
        storage = "local"
        
        iso_filename = self.iso_manager.get_pfsense_iso_filename(version)
        
        if self.iso_manager.iso_exists_on_proxmox(storage, iso_filename):
            console.print(f"[dim]  ISO already uploaded: {iso_filename}[/dim]")
            return f"{storage}:iso/{iso_filename}"
        
        if dry_run:
            console.print(f"[dim]  Would download pfSense {version}[/dim]")
            return f"{storage}:iso/{iso_filename}"
        
        return self.iso_manager.download_and_upload_pfsense(version, storage)
    
    def _generate_config(self, config: WizardConfig) -> str:
        """Generate pfSense config.xml."""
        return generate_pfsense_config(config)
    
    def _create_vm(self, config: WizardConfig, iso_volid: str, dry_run: bool) -> None:
        """Create the pfSense VM."""
        if self.client.vm_exists(config.vmid):
            raise Exception(f"VM {config.vmid} already exists")
        
        if dry_run:
            console.print(f"[dim]  Would create VM {config.vmid} ({config.vm_name})[/dim]")
            return
        
        # Build VM configuration
        vm_config = self._build_vm_config(config, iso_volid)
        
        # Create VM
        upid = self.client.create_vm(config.vmid, **vm_config)
        self.client.wait_for_task(upid)
    
    def _build_vm_config(self, config: WizardConfig, iso_volid: str) -> dict:
        """Build Proxmox VM configuration parameters."""
        return {
            "name": config.vm_name,
            "description": f"pfSense router - LAN: {config.lan_ip}/{config.lan_netmask}",
            "bios": "seabios",
            "boot": "cdn",
            "onboot": 1,
            "cores": config.cores,
            "sockets": 1,
            "cpu": "host",
            "memory": config.memory,
            "balloon": 0,
            "ostype": "other",
            # Storage
            "virtio0": f"local-lvm:{config.disk_size},cache=writeback,discard=on,ssd=1",
            # Network - WAN
            "net0": f"model=virtio,bridge={config.wan_bridge},firewall=0",
            # Network - LAN
            "net1": f"model=virtio,bridge={config.lan_bridge},firewall=0",
            # ISO
            "ide2": f"{iso_volid},media=cdrom",
        }
    
    def _start_vm(self, vmid: int) -> None:
        """Start the VM."""
        if self.client.vm_exists(vmid):
            status = self.client.get_vm_status(vmid)
            if status.get("status") != "running":
                upid = self.client.start_vm(vmid)
                self.client.wait_for_task(upid, timeout=60)


def print_deployment_result(result: DeploymentResult, config: WizardConfig) -> None:
    """Print deployment result summary."""
    from rich.panel import Panel
    
    if result.success:
        success_text = f"""
[bold green]pfSense deployment complete![/bold green]

[bold]Next steps:[/bold]

1. Open Proxmox console to complete installation:
   [cyan]{result.console_url}[/cyan]

2. In the installer:
   • Accept the copyright notice
   • Choose "Install pfSense"
   • Select Auto (ZFS) or Auto (UFS) partitioning
   • Wait for installation to complete
   • Remove the ISO and reboot

3. After reboot, pfSense will be available at:
   [cyan]Web GUI: https://{result.lan_ip}[/cyan]
   [cyan]SSH:     ssh admin@{result.lan_ip}[/cyan]

[bold]Credentials:[/bold]
   Username: admin
   Password: <the password you set>
        """
        
        panel = Panel(
            success_text.strip(),
            title="✓ Success",
            border_style="green",
        )
    else:
        error_text = "\n".join(f"• {e}" for e in result.errors)
        panel = Panel(
            f"[red]Deployment failed:[/red]\n\n{error_text}",
            title="✗ Failed",
            border_style="red",
        )
    
    console.print(panel)
    
    # Print warnings if any
    if result.warnings:
        console.print("\n[yellow]Warnings:[/yellow]")
        for warning in result.warnings:
            console.print(f"  • {warning}")
