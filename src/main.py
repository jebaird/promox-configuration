"""CLI entry point for Proxmox configuration tool."""

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from .proxmox_client import ProxmoxClient
from .network import NetworkManager
from .iso_manager import ISOManager
from .vm_creator import VMCreator
from .config import load_vm_config

app = typer.Typer(
    name="proxmox-config",
    help="Proxmox VE configuration tool for home lab infrastructure",
    no_args_is_help=True,
)
console = Console()


# ---------------------------------------------------------------------------
# Connection commands
# ---------------------------------------------------------------------------

@app.command("test")
def test_connection():
    """Test connection to Proxmox API."""
    try:
        client = ProxmoxClient()
        client.test_connection()
        
        # Show node info
        status = client.get_node_status()
        console.print(f"  Node: {client.node}")
        console.print(f"  Uptime: {status.get('uptime', 0) // 3600} hours")
    except Exception as e:
        console.print(f"[red]✗[/red] Connection failed: {e}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Network commands
# ---------------------------------------------------------------------------

network_app = typer.Typer(help="Network management commands")
app.add_typer(network_app, name="network")


@network_app.command("list")
def network_list():
    """List all network interfaces."""
    client = ProxmoxClient()
    manager = NetworkManager(client)
    manager.print_interfaces_table()


@network_app.command("setup")
def network_setup(
    apply: bool = typer.Option(False, "--apply", "-a", help="Apply changes immediately"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show what would be done"),
):
    """Setup network bridges from config."""
    client = ProxmoxClient()
    manager = NetworkManager(client)
    
    if dry_run:
        console.print("[yellow]DRY RUN:[/yellow] Checking network configuration...")
        
        for bridge_id, bridge_def in manager.config.get("bridges", {}).items():
            name = bridge_def.get("name")
            exists = manager.bridge_exists(name)
            expected = bridge_def.get("exists", False)
            
            if exists:
                console.print(f"  [green]✓[/green] {name} exists")
            elif expected:
                console.print(f"  [yellow]⚠[/yellow] {name} expected but missing")
            else:
                console.print(f"  [blue]→[/blue] {name} would be created")
        return
    
    results = manager.setup_bridges_from_config(apply=apply)
    
    created = [name for name, was_created in results.items() if was_created]
    if created and not apply:
        console.print(
            "\n[yellow]Note:[/yellow] Network changes are pending. "
            "Use --apply to apply immediately, or apply via Proxmox UI."
        )


@network_app.command("apply")
def network_apply():
    """Apply pending network changes."""
    client = ProxmoxClient()
    manager = NetworkManager(client)
    manager.apply_changes()


@network_app.command("revert")
def network_revert():
    """Revert pending network changes."""
    client = ProxmoxClient()
    manager = NetworkManager(client)
    manager.revert_changes()


# ---------------------------------------------------------------------------
# ISO commands
# ---------------------------------------------------------------------------

iso_app = typer.Typer(help="ISO management commands")
app.add_typer(iso_app, name="iso")


@iso_app.command("list")
def iso_list(
    storage: str = typer.Option("local", "--storage", "-s", help="Storage pool name"),
):
    """List ISO images on Proxmox storage."""
    client = ProxmoxClient()
    manager = ISOManager(client)
    manager.print_isos_table(storage)


@iso_app.command("download-pfsense")
def iso_download_pfsense(
    version: str = typer.Option("2.7.2", "--version", "-v", help="pfSense version"),
    storage: str = typer.Option("local", "--storage", "-s", help="Target storage pool"),
    keep_local: bool = typer.Option(False, "--keep", "-k", help="Keep local copy of ISO"),
):
    """Download and upload pfSense ISO to Proxmox."""
    client = ProxmoxClient()
    manager = ISOManager(client)
    
    try:
        volid = manager.download_and_upload_pfsense(
            version=version,
            storage=storage,
            keep_local=keep_local,
        )
        console.print(f"\n[green]✓[/green] ISO available as: {volid}")
    except ValueError as e:
        console.print(f"[red]✗[/red] {e}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# VM commands
# ---------------------------------------------------------------------------

vm_app = typer.Typer(help="VM management commands")
app.add_typer(vm_app, name="vm")


@vm_app.command("list")
def vm_list():
    """List all VMs."""
    client = ProxmoxClient()
    creator = VMCreator(client)
    creator.list_vms()


@vm_app.command("info")
def vm_info(vmid: int = typer.Argument(..., help="VM ID")):
    """Show VM details."""
    client = ProxmoxClient()
    creator = VMCreator(client)
    creator.print_vm_info(vmid)


@vm_app.command("create")
def vm_create(
    config_name: str = typer.Argument(..., help="VM config name (e.g., 'pfsense')"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show what would be done"),
    skip_iso: bool = typer.Option(False, "--skip-iso", help="Don't attach ISO"),
):
    """Create VM from configuration file."""
    client = ProxmoxClient()
    creator = VMCreator(client)
    iso_manager = ISOManager(client)
    
    # Load config
    try:
        config = creator.load_vm_config(config_name)
    except FileNotFoundError:
        console.print(f"[red]✗[/red] VM config '{config_name}' not found in config/vms/")
        raise typer.Exit(1)
    
    # Handle ISO
    iso_volid = None
    if not skip_iso and "iso" in config:
        iso_config = config["iso"]
        version = iso_config.get("version", "2.7.2")
        storage = iso_config.get("storage", "local")
        arch = iso_config.get("architecture", "amd64")
        
        iso_filename = iso_manager.get_pfsense_iso_filename(version, arch)
        iso_volid = f"{storage}:iso/{iso_filename}"
        
        if not iso_manager.iso_exists_on_proxmox(storage, iso_filename):
            if dry_run:
                console.print(f"[yellow]DRY RUN:[/yellow] Would download {iso_filename}")
            else:
                console.print(f"[dim]ISO not found, downloading...[/dim]")
                iso_volid = iso_manager.download_and_upload_pfsense(version, storage, arch)
    
    # Create VM
    creator.create_vm(config, iso_volid=iso_volid, dry_run=dry_run)


@vm_app.command("delete")
def vm_delete(
    vmid: int = typer.Argument(..., help="VM ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Delete a VM."""
    client = ProxmoxClient()
    creator = VMCreator(client)
    creator.delete_vm(vmid, confirm=yes)


# ---------------------------------------------------------------------------
# Deploy command (orchestrated workflow)
# ---------------------------------------------------------------------------

@app.command("deploy")
def deploy(
    vm_name: str = typer.Argument("pfsense", help="VM config name to deploy"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show what would be done"),
    skip_network: bool = typer.Option(False, "--skip-network", help="Skip network setup"),
    skip_iso: bool = typer.Option(False, "--skip-iso", help="Skip ISO download"),
    apply_network: bool = typer.Option(True, "--apply-network/--no-apply-network", 
                                       help="Apply network changes immediately"),
):
    """
    Deploy a VM with full setup.
    
    This orchestrates the complete deployment workflow:
    1. Test connection to Proxmox
    2. Setup required network bridges
    3. Download and upload ISO (if needed)
    4. Create the VM
    """
    client = ProxmoxClient()
    
    console.print(f"\n[bold]Deploying {vm_name}[/bold]\n")
    
    # Step 1: Test connection
    console.print("[bold]1. Testing Proxmox connection...[/bold]")
    try:
        client.test_connection()
    except Exception as e:
        console.print(f"[red]✗[/red] Connection failed: {e}")
        raise typer.Exit(1)
    
    # Load VM config
    try:
        vm_config = load_vm_config(vm_name)
    except FileNotFoundError:
        console.print(f"[red]✗[/red] VM config '{vm_name}' not found")
        raise typer.Exit(1)
    
    # Step 2: Network setup
    if not skip_network:
        console.print("\n[bold]2. Setting up network bridges...[/bold]")
        network_manager = NetworkManager(client)
        
        if dry_run:
            for name in [n["bridge"] for n in vm_config.get("network", [])]:
                exists = network_manager.bridge_exists(name)
                status = "[green]exists[/green]" if exists else "[blue]will create[/blue]"
                console.print(f"  Bridge {name}: {status}")
        else:
            network_manager.setup_bridges_from_config(apply=apply_network)
    else:
        console.print("\n[bold]2. Skipping network setup[/bold]")
    
    # Step 3: ISO management
    iso_volid = None
    if not skip_iso and "iso" in vm_config:
        console.print("\n[bold]3. Preparing ISO...[/bold]")
        iso_manager = ISOManager(client)
        iso_config = vm_config["iso"]
        
        version = iso_config.get("version", "2.7.2")
        storage = iso_config.get("storage", "local")
        arch = iso_config.get("architecture", "amd64")
        
        iso_filename = iso_manager.get_pfsense_iso_filename(version, arch)
        iso_volid = f"{storage}:iso/{iso_filename}"
        
        if iso_manager.iso_exists_on_proxmox(storage, iso_filename):
            console.print(f"  [green]✓[/green] ISO already uploaded: {iso_filename}")
        elif dry_run:
            console.print(f"  [blue]→[/blue] Would download: {iso_filename}")
        else:
            iso_volid = iso_manager.download_and_upload_pfsense(version, storage, arch)
    else:
        console.print("\n[bold]3. Skipping ISO setup[/bold]")
    
    # Step 4: Create VM
    console.print("\n[bold]4. Creating VM...[/bold]")
    vm_creator = VMCreator(client)
    
    vmid = vm_config["vm"]["vmid"]
    
    if client.vm_exists(vmid):
        console.print(f"  [yellow]⚠[/yellow] VM {vmid} already exists")
    elif dry_run:
        console.print(f"  [blue]→[/blue] Would create VM {vmid}")
        vm_creator._print_vm_params(vm_creator.build_vm_params(vm_config, iso_volid))
    else:
        vm_creator.create_vm(vm_config, iso_volid=iso_volid)
    
    # Summary
    console.print("\n[bold green]Deployment complete![/bold green]")
    
    if not dry_run and client.vm_exists(vmid):
        host = client.config["host"]
        node = client.node
        console.print(f"\n[bold]Next steps:[/bold]")
        console.print(f"  1. Open Proxmox console: https://{host}:8006/?console=kvm&vmid={vmid}&node={node}")
        console.print(f"  2. Start the VM and complete pfSense installation")
        console.print(f"  3. Configure WAN/LAN interfaces in pfSense")
        console.print(f"  4. Access pfSense web UI at the LAN IP (default: admin/pfsense)")


# ---------------------------------------------------------------------------
# Interactive Wizard
# ---------------------------------------------------------------------------

@app.command("wizard")
def wizard(
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show what would be done"),
):
    """
    Interactive wizard for pfSense deployment.
    
    Guides you through:
    - Network discovery and bridge setup
    - pfSense network configuration (LAN IP, DHCP)
    - DNS/DHCP settings (domain, hostname registration)
    - Static hosts and reservations (from hosts.yaml)
    - VM resource allocation
    - Automated deployment
    """
    from .wizard import DeploymentWizard
    from .deploy import PfSenseDeployer, print_deployment_result
    
    # Test connection first
    try:
        client = ProxmoxClient()
        client.test_connection()
    except Exception as e:
        console.print(f"[red]✗[/red] Connection failed: {e}")
        console.print("\nMake sure your .env file has valid Proxmox credentials.")
        raise typer.Exit(1)
    
    # Look for hosts.yaml config
    hosts_path = Path("config/hosts.yaml")
    if hosts_path.exists():
        console.print(f"[dim]Loading hosts from {hosts_path}...[/dim]\n")
    else:
        hosts_path = None
    
    # Run the wizard
    wiz = DeploymentWizard(client, hosts_path=hosts_path)
    config = wiz.run()
    
    if config is None:
        console.print("[dim]Wizard cancelled[/dim]")
        raise typer.Exit(0)
    
    # Deploy
    deployer = PfSenseDeployer(client)
    result = deployer.deploy(config, dry_run=dry_run)
    
    # Show result
    print_deployment_result(result, config)
    
    if not result.success:
        raise typer.Exit(1)


@app.command("topology")
def topology():
    """Show network topology (bridges and interfaces)."""
    client = ProxmoxClient()
    manager = NetworkManager(client)
    manager.print_topology_table()


def main():
    """Entry point."""
    app()


if __name__ == "__main__":
    main()
