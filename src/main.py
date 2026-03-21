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
            # Handle both list and dict network configs
            network_config = vm_config.get("network", [])
            if isinstance(network_config, dict):
                bridges = [network_config.get("bridge")] if network_config.get("bridge") else []
            else:
                bridges = [n["bridge"] for n in network_config if isinstance(n, dict)]
            
            for name in bridges:
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


# ---------------------------------------------------------------------------
# Cert-Manager Deployment
# ---------------------------------------------------------------------------

@app.command("deploy-cert-manager")
def deploy_cert_manager(
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show what would be done"),
    staging: bool = typer.Option(False, "--staging", help="Use Let's Encrypt staging environment"),
):
    """
    Deploy cert-manager LXC container for automated SSL certificates.
    
    Creates a Debian LXC container that:
    - Requests wildcard certificates from Let's Encrypt
    - Uses Cloudflare DNS challenge (requires API token)
    - Auto-renews certificates before expiry
    - Can distribute certs to pfSense, Proxmox, and other services
    
    Requirements:
    - CLOUDFLARE_API_TOKEN in .env (with Zone:DNS:Edit permission)
    - CLOUDFLARE_ZONE in .env (e.g., example.com)
    - PFSENSE_DOMAIN in .env (e.g., lab.example.com)
    """
    from .cert_manager_deploy import CertManagerDeployer, CertManagerConfig
    
    # Test connection first
    try:
        client = ProxmoxClient()
        client.test_connection()
    except Exception as e:
        console.print(f"[red]✗[/red] Connection failed: {e}")
        console.print("\nMake sure your .env file has valid Proxmox credentials.")
        raise typer.Exit(1)
    
    # Deploy
    deployer = CertManagerDeployer(client)
    
    # Override staging if specified
    config = None
    if staging:
        from .cert_manager_deploy import CertManagerConfig
        config = CertManagerConfig()
        config.staging = True
    
    success = deployer.deploy(config=config, dry_run=dry_run)
    
    if not success:
        raise typer.Exit(1)


@app.command("deploy-stack")
def deploy_stack(
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show what would be done"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompts"),
    skip_network: bool = typer.Option(False, "--skip-network", help="Skip network bridge setup"),
    skip_prometheus: bool = typer.Option(False, "--skip-prometheus", help="Skip Prometheus deployment"),
    skip_grafana: bool = typer.Option(False, "--skip-grafana", help="Skip Grafana deployment"),
    skip_cert_manager: bool = typer.Option(False, "--skip-cert-manager", help="Skip cert-manager deployment"),
    skip_cert_targets: bool = typer.Option(False, "--skip-cert-targets", help="Skip cert target setup"),
):
    """
    Deploy the full monitoring and certificate stack.
    
    This is the recommended one-command deployment for the home lab:
    
    1. Network: Sets up vmbr0, vmbr1, vmbr2 bridges (if needed)
    2. Prometheus: Metrics collection (VMID 110)
    3. Grafana: Dashboards (VMID 111)
    4. Cert-Manager: SSL certificates (VMID 105)
    5. Cert Targets: SSH key distribution for certificate automation
    
    Uses configuration from config/vms/*.yaml and environment variables.
    Test environment automatically uses test subnet (172.30.0.x).
    
    Examples:
        # Deploy everything
        proxmox-config deploy-stack --yes
        
        # Deploy only monitoring (skip certs)
        proxmox-config deploy-stack --skip-cert-manager --skip-cert-targets
        
        # Dry run to see what would happen
        proxmox-config deploy-stack --dry-run
    """
    import os
    from .config import load_vm_config, _load_env_files
    
    # Ensure env is loaded to get password
    _load_env_files()
    root_password = os.getenv("PROXMOX_ROOT_PASSWORD", "")
    
    if not yes:
        console.print("[bold]Deploy Stack Plan[/bold]")
        console.print("This will deploy the following components:")
        if not skip_network:
            console.print("  • Network bridges (vmbr0, vmbr1, vmbr2)")
        if not skip_prometheus:
            console.print("  • Prometheus (VMID 110)")
        if not skip_grafana:
            console.print("  • Grafana (VMID 111)")
        if not skip_cert_manager:
            console.print("  • Cert-Manager (VMID 105)")
        if not skip_cert_targets:
            console.print("  • Certificate distribution setup")
        console.print()
        
        if dry_run:
            console.print("[yellow]DRY RUN - no changes will be made[/yellow]\n")
        else:
            response = console.input("Proceed? [y/N]: ")
            if response.lower() not in ("y", "yes"):
                console.print("[dim]Aborted[/dim]")
                raise typer.Exit(0)
    
    client = ProxmoxClient()
    
    # Step 1: Network bridges
    if not skip_network:
        console.print("\n[bold cyan]Step 1: Network Bridges[/bold cyan]")
        try:
            from .network import NetworkManager
            manager = NetworkManager(client)
            results = manager.setup_bridges_from_config(apply=not dry_run)
            created = [name for name, was_created in results.items() if was_created]
            if created:
                console.print(f"[green]✓[/green] Created bridges: {', '.join(created)}")
            else:
                console.print("[green]✓[/green] All bridges already exist")
        except Exception as e:
            console.print(f"[red]✗[/red] Network setup failed: {e}")
            if not yes:
                raise typer.Exit(1)
    
    # Step 2: Prometheus
    if not skip_prometheus:
        console.print("\n[bold cyan]Step 2: Prometheus[/bold cyan]")
        try:
            from .prometheus_deploy import PrometheusDeployer
            deployer = PrometheusDeployer(client)
            success = deployer.deploy(dry_run=dry_run)
            if success:
                console.print("[green]✓[/green] Prometheus deployed")
            else:
                console.print("[yellow]⚠[/yellow] Prometheus deployment had issues")
        except Exception as e:
            console.print(f"[red]✗[/red] Prometheus deployment failed: {e}")
            if not yes:
                raise typer.Exit(1)
    
    # Step 3: Grafana
    if not skip_grafana:
        console.print("\n[bold cyan]Step 3: Grafana[/bold cyan]")
        try:
            from .grafana_deploy import GrafanaDeployer
            deployer = GrafanaDeployer(client)
            success = deployer.deploy(dry_run=dry_run)
            if success:
                console.print("[green]✓[/green] Grafana deployed")
            else:
                console.print("[yellow]⚠[/yellow] Grafana deployment had issues")
        except Exception as e:
            console.print(f"[red]✗[/red] Grafana deployment failed: {e}")
            if not yes:
                raise typer.Exit(1)
    
    # Step 4: Cert-Manager
    if not skip_cert_manager:
        console.print("\n[bold cyan]Step 4: Cert-Manager[/bold cyan]")
        try:
            from .cert_manager_deploy import CertManagerDeployer
            deployer = CertManagerDeployer(client)
            success = deployer.deploy(dry_run=dry_run)
            if success:
                console.print("[green]✓[/green] Cert-Manager deployed")
            else:
                console.print("[yellow]⚠[/yellow] Cert-Manager deployment had issues")
        except Exception as e:
            console.print(f"[red]✗[/red] Cert-Manager deployment failed: {e}")
            if not yes:
                raise typer.Exit(1)
    
    # Step 5: Setup cert targets
    if not skip_cert_targets and not skip_cert_manager:
        console.print("\n[bold cyan]Step 5: Certificate Distribution Setup[/bold cyan]")
        key_file = Path(__file__).parent.parent / "data" / ".cert-manager.key"
        
        if not key_file.exists():
            console.print("[yellow]⚠[/yellow] Skipping cert targets (no management key yet)")
        elif not root_password:
            console.print("[yellow]⚠[/yellow] Skipping cert targets (no PROXMOX_ROOT_PASSWORD)")
        elif dry_run:
            console.print("[yellow]DRY RUN[/yellow] Would setup cert targets")
        else:
            try:
                from .cert_key_deploy import CertKeyDeployer
                
                # Get cert-manager IP
                cm_config = load_vm_config("cert-manager")
                network = cm_config.get("network", {})
                ip_str = network.get("ip", "")
                cert_manager_ip = ip_str.split("/")[0] if ip_str else ""
                
                if cert_manager_ip:
                    private_key = key_file.read_text()
                    deployer = CertKeyDeployer(
                        cert_manager_ip=cert_manager_ip,
                        cert_manager_key=private_key,
                    )
                    result = deployer.deploy_all_targets(
                        auto_confirm=True,
                        default_password=root_password,
                    )
                    if result.get("success"):
                        console.print("[green]✓[/green] Cert targets configured")
                    else:
                        console.print("[yellow]⚠[/yellow] Some cert targets failed")
            except Exception as e:
                console.print(f"[red]✗[/red] Cert target setup failed: {e}")
    
    # Summary
    console.print("\n[bold green]Stack deployment complete![/bold green]")
    
    # Show access info
    from .config import load_vm_config
    try:
        prom_ip = load_vm_config("prometheus").get("network", {}).get("ip", "").split("/")[0]
        graf_ip = load_vm_config("grafana").get("network", {}).get("ip", "").split("/")[0]
        
        console.print("\nAccess your services:")
        if not skip_prometheus and prom_ip:
            console.print(f"  • Prometheus: http://{prom_ip}:9090")
        if not skip_grafana and graf_ip:
            console.print(f"  • Grafana: http://{graf_ip}:3000  (admin/admin)")
        if not skip_cert_manager:
            cm_ip = load_vm_config("cert-manager").get("network", {}).get("ip", "").split("/")[0]
            console.print(f"  • Cert-Manager: ssh root@{cm_ip}")
    except Exception:
        pass


@app.command("setup-cert-targets")
def setup_cert_targets(
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show what would be done"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompts"),
    password: str = typer.Option("", "--password", "-p", help="Root password for targets (or use PROXMOX_ROOT_PASSWORD env)", envvar="PROXMOX_ROOT_PASSWORD"),
):
    """
    Deploy SSH keys and receiver scripts to certificate targets.
    
    This sets up secure, restricted SSH access from the cert-manager
    container to each target defined in config/cert-targets.yaml.
    
    The SSH keys are restricted to:
    - Only connect from the LAN IP range
    - Only execute the cert-receive.sh script (no shell access)
    - No port forwarding, X11, or agent forwarding
    
    You will be prompted for the root password of each target unless
    --password or PROXMOX_ROOT_PASSWORD is set.
    
    Prerequisites:
    - cert-manager container must be deployed (deploy-cert-manager)
    - .cert-manager.key file must exist (created during deployment)
    - Targets must have SSH enabled
    """
    from .cert_key_deploy import CertKeyDeployer
    from .config import load_yaml
    
    # Check for management key
    key_file = Path(__file__).parent.parent / "data" / ".cert-manager.key"
    if not key_file.exists():
        console.print("[red]✗[/red] Management key not found: data/.cert-manager.key")
        console.print("Run [bold]deploy-cert-manager[/bold] first to create the cert-manager container.")
        raise typer.Exit(1)
    
    # Load cert-manager config with env var expansion
    from .config import load_vm_config
    try:
        cm_config = load_vm_config("cert-manager")
        network = cm_config.get("network", {})
        ip_str = network.get("ip", "")
        cert_manager_ip = ip_str.split("/")[0] if ip_str else ""
        if not cert_manager_ip:
            raise ValueError("No cert-manager IP configured")
    except Exception as e:
        console.print(f"[red]✗[/red] Failed to load cert-manager config: {e}")
        raise typer.Exit(1)
    
    # Read management key
    private_key = key_file.read_text()
    
    console.print(f"[bold]Setting up certificate distribution targets[/bold]")
    console.print(f"Cert-manager: {cert_manager_ip}\n")
    
    # Deploy to targets
    deployer = CertKeyDeployer(
        cert_manager_ip=cert_manager_ip,
        cert_manager_key=private_key,
    )
    
    result = deployer.deploy_all_targets(
        dry_run=dry_run,
        auto_confirm=yes,
        default_password=password or None,
    )
    
    if not result.get("success", False):
        raise typer.Exit(1)


@app.command("verify-cert-targets")
def verify_cert_targets():
    """
    Verify cert-manager can reach all configured targets.
    
    Tests SSH connectivity from the cert-manager container to each
    target defined in config/cert-targets.yaml.
    
    Prerequisites:
    - cert-manager container must be deployed
    - setup-cert-targets must have been run
    """
    from .cert_key_deploy import CertKeyDeployer
    
    # Check for management key
    key_file = Path(__file__).parent.parent / "data" / ".cert-manager.key"
    if not key_file.exists():
        console.print("[red]✗[/red] Management key not found: data/.cert-manager.key")
        console.print("Run [bold]deploy-cert-manager[/bold] first.")
        raise typer.Exit(1)
    
    # Load cert-manager config with env var expansion
    from .config import load_vm_config
    try:
        cm_config = load_vm_config("cert-manager")
        network = cm_config.get("network", {})
        ip_str = network.get("ip", "")
        cert_manager_ip = ip_str.split("/")[0] if ip_str else ""
        if not cert_manager_ip:
            raise ValueError("No cert-manager IP configured")
    except Exception as e:
        console.print(f"[red]✗[/red] Failed to load cert-manager config: {e}")
        raise typer.Exit(1)
    
    # Read management key
    private_key = key_file.read_text()
    
    # Verify targets
    deployer = CertKeyDeployer(
        cert_manager_ip=cert_manager_ip,
        cert_manager_key=private_key,
    )
    
    results = deployer.verify_targets()
    
    # Check if all passed
    if not all(r.get("success", False) for r in results.values()):
        raise typer.Exit(1)


@app.command("delete-lxc")
def delete_lxc(
    vmid: int = typer.Argument(..., help="LXC container ID"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Delete an LXC container."""
    from .lxc_creator import LXCCreator
    
    client = ProxmoxClient()
    lxc = LXCCreator(client)
    
    if not lxc.container_exists(vmid):
        console.print(f"[red]✗[/red] Container {vmid} not found")
        raise typer.Exit(1)
    
    # Get container info
    try:
        config = lxc.get_container_config(vmid)
        hostname = config.get("hostname", "unknown")
    except Exception:
        hostname = "unknown"
    
    if not yes:
        console.print(f"[yellow]Warning:[/yellow] This will delete container {vmid} ({hostname})")
        response = console.input("Type 'yes' to confirm: ")
        if response.lower() != "yes":
            console.print("[dim]Aborted[/dim]")
            raise typer.Exit(0)
    
    console.print(f"[dim]Stopping container {vmid}...[/dim]")
    try:
        upid = lxc.stop_container(vmid)
        client.wait_for_task(upid, timeout=30)
    except Exception:
        pass  # Container may already be stopped
    
    console.print(f"[dim]Deleting container {vmid}...[/dim]")
    upid = lxc.delete_container(vmid)
    client.wait_for_task(upid, timeout=60)
    
    console.print(f"[green]✓[/green] Deleted container {vmid} ({hostname})")


@app.command("topology")
def topology():
    """Show network topology (bridges and interfaces)."""
    client = ProxmoxClient()
    manager = NetworkManager(client)
    manager.print_topology_table()


# ---------------------------------------------------------------------------
# Test Environment Commands
# ---------------------------------------------------------------------------

test_env_app = typer.Typer(help="Isolated test environment for safe experimentation")
app.add_typer(test_env_app, name="test-env")


@test_env_app.command("status")
def test_env_status():
    """Show test environment status."""
    from .test_env import TestEnvironment
    
    client = ProxmoxClient()
    env = TestEnvironment(client)
    env.print_status()


@test_env_app.command("create")
def test_env_create(
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show what would be done"),
    skip_client: bool = typer.Option(False, "--skip-client", help="Skip Alpine test client"),
    skip_iso: bool = typer.Option(False, "--skip-iso", help="Skip ISO download (upload manually later)"),
):
    """
    Create isolated test environment.
    
    Sets up a completely isolated network for testing pfSense configuration:
    
    - Creates vmbr2 bridge (no physical interface = isolated)
    - Creates pfSense test VM (VMID 101) on 192.168.99.1
    - Creates Alpine Linux test client (VMID 199) for DHCP/DNS testing
    
    The test network uses subnet 192.168.99.0/24, completely separate from
    your production 10.0.0.x network. Your internet stays untouched.
    
    After creation, complete pfSense installation via Proxmox console.
    """
    from .test_env import TestEnvironment
    
    try:
        client = ProxmoxClient()
        client.test_connection()
    except Exception as e:
        console.print(f"[red]✗[/red] Connection failed: {e}")
        raise typer.Exit(1)
    
    env = TestEnvironment(client)
    success = env.create(skip_client=skip_client, skip_iso=skip_iso, dry_run=dry_run)
    
    if not success:
        raise typer.Exit(1)


@test_env_app.command("destroy")
def test_env_destroy(
    force: bool = typer.Option(False, "--force", "-f", help="Stop running VMs before deleting"),
    keep_bridge: bool = typer.Option(False, "--keep-bridge", help="Keep the vmbr2 bridge"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """
    Destroy test environment.
    
    Removes the test pfSense VM, test client, and optionally the test bridge.
    """
    from .test_env import TestEnvironment
    
    try:
        client = ProxmoxClient()
        client.test_connection()
    except Exception as e:
        console.print(f"[red]✗[/red] Connection failed: {e}")
        raise typer.Exit(1)
    
    if not yes:
        console.print("[yellow]Warning:[/yellow] This will delete the test environment VMs")
        response = console.input("Type 'yes' to confirm: ")
        if response.lower() != "yes":
            console.print("[dim]Aborted[/dim]")
            raise typer.Exit(0)
    
    env = TestEnvironment(client)
    success = env.destroy(keep_bridge=keep_bridge, force=force)
    
    if not success:
        raise typer.Exit(1)


@test_env_app.command("start")
def test_env_start():
    """Start test environment VMs."""
    from .test_env import TestEnvironment
    
    try:
        client = ProxmoxClient()
        env = TestEnvironment(client)
        env.start()
    except Exception as e:
        console.print(f"[red]✗[/red] Error: {e}")
        raise typer.Exit(1)


@test_env_app.command("stop")
def test_env_stop():
    """Stop test environment VMs."""
    from .test_env import TestEnvironment
    
    try:
        client = ProxmoxClient()
        env = TestEnvironment(client)
        env.stop()
    except Exception as e:
        console.print(f"[red]✗[/red] Error: {e}")
        raise typer.Exit(1)


def main():
    """Entry point."""
    app()


if __name__ == "__main__":
    main()
