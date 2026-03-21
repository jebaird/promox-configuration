"""Grafana Dashboard Deployment Orchestration.

Deploys and configures a Grafana LXC container with Prometheus datasource
and Proxmox dashboards pre-configured. Uses pct exec for configuration (no SSH keys needed).
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import time
import secrets
import string

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from .proxmox_client import ProxmoxClient
from .lxc_creator import LXCCreator, LXCConfig
from .config import get_lan_subnet, get_proxmox_ssh_config

console = Console()

# Template directory
TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "grafana"


def generate_password(length: int = 16) -> str:
    """Generate a secure random password."""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


@dataclass
class GrafanaConfig:
    """Configuration for Grafana deployment."""
    vmid: int = 111
    hostname: str = "grafana"
    
    # Resources
    cores: int = 1
    memory: int = 512
    swap: int = 256
    disk_size: str = "8G"
    
    # Network
    ip: str = "10.0.0.11"
    netmask: int = 24
    gateway: str = "10.0.0.2"
    bridge: str = "vmbr0"
    
    # Template
    template: str = "debian-12-standard"
    template_storage: str = "local"
    rootfs_storage: str = "local-lvm"
    
    # Grafana settings
    port: int = 3000
    admin_user: str = "admin"
    admin_password: str = ""  # Generated if empty
    
    # Datasource
    prometheus_url: str = "http://10.0.0.10:9090"
    
    @classmethod
    def from_yaml(cls, config_path: Path) -> "GrafanaConfig":
        """Load configuration from YAML file."""
        from .config import load_yaml_file
        data = load_yaml_file(config_path)
        
        config = cls()
        
        # Container settings
        container = data.get("container", {})
        if "vmid" in container:
            config.vmid = container["vmid"]
        if "hostname" in container:
            config.hostname = container["hostname"]
        
        # Resources
        resources = container.get("resources", {})
        if "cores" in resources:
            config.cores = resources["cores"]
        if "memory" in resources:
            config.memory = resources["memory"]
        if "swap" in resources:
            config.swap = resources["swap"]
        
        # Rootfs
        rootfs = container.get("rootfs", {})
        if "storage" in rootfs:
            config.rootfs_storage = rootfs["storage"]
        if "size" in rootfs:
            config.disk_size = rootfs["size"]
        
        # Template
        if "template" in container:
            config.template = container["template"]
        if "template_storage" in container:
            config.template_storage = container["template_storage"]
        
        # Network
        network = data.get("network", {})
        if "ip" in network:
            ip_str = network["ip"]
            if "/" in ip_str:
                config.ip, netmask = ip_str.split("/")
                config.netmask = int(netmask)
            else:
                config.ip = ip_str
        if "gateway" in network:
            config.gateway = network["gateway"]
        if "bridge" in network:
            config.bridge = network["bridge"]
        
        # Grafana settings
        grafana = data.get("grafana", {})
        if "port" in grafana:
            config.port = grafana["port"]
        if "admin_user" in grafana:
            config.admin_user = grafana["admin_user"]
        
        # Datasource
        datasources = data.get("datasources", {})
        prometheus = datasources.get("prometheus", {})
        if "url" in prometheus:
            config.prometheus_url = prometheus["url"]
        
        return config


class GrafanaDeployer:
    """Orchestrates Grafana LXC deployment."""
    
    def __init__(self, client: ProxmoxClient):
        """Initialize with Proxmox client."""
        self.client = client
        self.lxc = LXCCreator(client)
    
    def deploy(
        self,
        config: GrafanaConfig | None = None,
        dry_run: bool = False,
    ) -> bool:
        """Deploy the Grafana container.
        
        Args:
            config: Optional config override
            dry_run: If True, only show what would be done
            
        Returns:
            True if deployment succeeded
        """
        # Load config if not provided
        if config is None:
            config = self._load_default_config()
        
        # Generate admin password if not set
        if not config.admin_password:
            config.admin_password = generate_password()
        
        # Show deployment plan
        self._show_plan(config)
        
        if dry_run:
            console.print("\n[yellow]Dry run - no changes made[/yellow]")
            return True
        
        # Execute deployment
        return self._execute_deployment(config)
    
    def _load_default_config(self) -> GrafanaConfig:
        """Load config from default YAML file."""
        config_path = Path(__file__).parent.parent / "config" / "vms" / "grafana.yaml"
        
        if config_path.exists():
            config = GrafanaConfig.from_yaml(config_path)
        else:
            config = GrafanaConfig()
        
        # Apply subnet defaults from env
        subnet = get_lan_subnet()
        if config.ip == "10.0.0.11":
            config.ip = f"{subnet}.11"
        if config.gateway == "10.0.0.2":
            config.gateway = f"{subnet}.2"
        if config.prometheus_url == "http://10.0.0.10:9090":
            config.prometheus_url = f"http://{subnet}.10:9090"
        
        return config
    
    def _show_plan(self, config: GrafanaConfig) -> None:
        """Display deployment plan."""
        plan_text = f"""
[bold]Container:[/bold]
  VMID: {config.vmid}
  Hostname: {config.hostname}
  Template: {config.template}
  Resources: {config.cores} core, {config.memory}MB RAM, {config.disk_size} disk

[bold]Network:[/bold]
  IP: {config.ip}/{config.netmask}
  Gateway: {config.gateway}
  Bridge: {config.bridge}

[bold]Grafana:[/bold]
  Port: {config.port}
  Admin User: {config.admin_user}
  Admin Password: [hidden]

[bold]Datasource:[/bold]
  Prometheus: {config.prometheus_url}
        """
        
        panel = Panel(
            plan_text.strip(),
            title="📈 Grafana Deployment Plan",
            border_style="blue",
        )
        console.print(panel)
    
    def _execute_deployment(self, config: GrafanaConfig) -> bool:
        """Execute the deployment steps."""
        console.print("\n[bold]Starting deployment...[/bold]\n")
        
        # Require Proxmox host SSH for pct exec
        pve_ssh_config = get_proxmox_ssh_config()
        if not pve_ssh_config:
            console.print("[red]✗[/red] Proxmox host SSH not configured")
            console.print("[dim]Configure PROXMOX_SSH_USER and PROXMOX_SSH_PASSWORD in .env[/dim]")
            return False
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            
            # Step 1: Check if container already exists
            task = progress.add_task("Checking existing containers...", total=None)
            if self.lxc.container_exists(config.vmid):
                progress.update(task, description=f"[yellow]⚠[/yellow] Container {config.vmid} already exists")
                console.print(f"\n[yellow]Container {config.vmid} already exists. Delete it first or use a different VMID.[/yellow]")
                return False
            progress.update(task, description="[green]✓[/green] No conflicting container")
            
            # Step 2: Download template if needed
            progress.update(task, description="Checking container template...")
            try:
                ostemplate = self.lxc.download_template(
                    config.template_storage,
                    config.template,
                )
                progress.update(task, description="[green]✓[/green] Template ready")
            except Exception as e:
                progress.update(task, description="[red]✗[/red] Template download failed")
                console.print(f"\n[red]Error: {e}[/red]")
                return False
            
            # Step 3: Create container (no SSH keys needed - using pct exec)
            progress.update(task, description="Creating LXC container...")
            try:
                lxc_config = LXCConfig(
                    vmid=config.vmid,
                    hostname=config.hostname,
                    ostemplate=ostemplate,
                    cores=config.cores,
                    memory=config.memory,
                    swap=config.swap,
                    rootfs_storage=config.rootfs_storage,
                    rootfs_size=config.disk_size,
                    net0=f"name=eth0,bridge={config.bridge},ip={config.ip}/{config.netmask},gw={config.gateway}",
                    start=True,
                    onboot=True,
                    unprivileged=True,
                )
                
                upid = self.lxc.create_container(lxc_config)
                self.client.wait_for_task(upid, timeout=120)
                progress.update(task, description="[green]✓[/green] Container created")
            except Exception as e:
                progress.update(task, description="[red]✗[/red] Container creation failed")
                console.print(f"\n[red]Error: {e}[/red]")
                return False
            
            # Step 4: Wait for container to be ready
            progress.update(task, description="Waiting for container to start...")
            if not self.lxc.wait_for_container_ready(config.vmid, timeout=60):
                progress.update(task, description="[yellow]⚠[/yellow] Container may not be fully ready")
            else:
                progress.update(task, description="[green]✓[/green] Container running")
            
            # Step 5: Configure container via pct exec
            progress.update(task, description="Installing Grafana (this may take a few minutes)...")
            try:
                self._configure_container(config, pve_ssh_config)
                progress.update(task, description="[green]✓[/green] Grafana configured")
            except Exception as e:
                progress.update(task, description="[red]✗[/red] Configuration failed")
                console.print(f"\n[red]Error: {e}[/red]")
                return False
            
            # Step 6: Verify deployment
            progress.update(task, description="Verifying deployment...")
            verification = self._verify_deployment(config, pve_ssh_config)
            if verification["success"]:
                progress.update(task, description="[green]✓[/green] Grafana verified")
            else:
                progress.update(task, description="[yellow]⚠[/yellow] Verification failed")
                console.print(f"\n[yellow]Warning: {verification['message']}[/yellow]")
        
        # Save credentials
        data_dir = Path(__file__).parent.parent / "data"
        data_dir.mkdir(exist_ok=True)
        
        creds_file = data_dir / ".grafana-creds"
        creds_file.write_text(f"admin_user={config.admin_user}\nadmin_password={config.admin_password}\n")
        creds_file.chmod(0o600)
        
        # Show completion message
        self._show_completion(config, verification)
        return verification["success"]
    
    def _configure_container(self, config: GrafanaConfig, pve_ssh_config: dict) -> None:
        """Configure the container via pct exec."""
        vmid = config.vmid
        
        # Configure DNS
        console.print("[dim]  Configuring DNS...[/dim]")
        dns_config = "nameserver 8.8.8.8\nnameserver 8.8.4.4\n"
        self.lxc.pct_write_file(vmid, "/etc/resolv.conf", dns_config, pve_ssh_config)
        
        # Install dependencies
        console.print("[dim]  Installing dependencies...[/dim]")
        self.lxc.pct_exec(vmid, "apt-get update", pve_ssh_config, timeout=120, check=True)
        self.lxc.pct_exec(vmid, "apt-get install -y apt-transport-https software-properties-common wget gnupg2 curl", pve_ssh_config, timeout=120, check=True)
        
        # Add Grafana APT repository
        console.print("[dim]  Adding Grafana repository...[/dim]")
        self.lxc.pct_exec(vmid, "wget -q -O /usr/share/keyrings/grafana.key https://apt.grafana.com/gpg.key", pve_ssh_config, timeout=60, check=True)
        self.lxc.pct_exec(vmid, 'sh -c \'echo "deb [signed-by=/usr/share/keyrings/grafana.key] https://apt.grafana.com stable main" > /etc/apt/sources.list.d/grafana.list\'', pve_ssh_config, check=True)
        
        # Install Grafana
        console.print("[dim]  Installing Grafana...[/dim]")
        self.lxc.pct_exec(vmid, "apt-get update", pve_ssh_config, timeout=60, check=True)
        self.lxc.pct_exec(vmid, "apt-get install -y grafana", pve_ssh_config, timeout=180, check=True)
        
        # Configure Grafana
        console.print("[dim]  Configuring Grafana...[/dim]")
        grafana_ini = f"""[server]
http_port = {config.port}
root_url = http://{config.ip}:{config.port}/

[security]
admin_user = {config.admin_user}
admin_password = {config.admin_password}

[users]
allow_sign_up = false
"""
        self.lxc.pct_write_file(vmid, "/etc/grafana/grafana.ini", grafana_ini, pve_ssh_config, mode=0o640)
        self.lxc.pct_exec(vmid, "chown root:grafana /etc/grafana/grafana.ini", pve_ssh_config, check=True)
        
        # Setup datasource provisioning
        console.print("[dim]  Configuring Prometheus datasource...[/dim]")
        self.lxc.pct_exec(vmid, "mkdir -p /etc/grafana/provisioning/datasources", pve_ssh_config, check=True)
        
        datasource_config = f"""apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: {config.prometheus_url}
    isDefault: true
    editable: false
    jsonData:
      timeInterval: "15s"
      httpMethod: "POST"
"""
        self.lxc.pct_write_file(vmid, "/etc/grafana/provisioning/datasources/prometheus.yaml", datasource_config, pve_ssh_config)
        
        # Setup dashboard provisioning
        console.print("[dim]  Configuring dashboard provisioning...[/dim]")
        self.lxc.pct_exec(vmid, "mkdir -p /etc/grafana/provisioning/dashboards", pve_ssh_config, check=True)
        self.lxc.pct_exec(vmid, "mkdir -p /var/lib/grafana/dashboards", pve_ssh_config, check=True)
        
        dashboard_provision = """apiVersion: 1

providers:
  - name: 'Proxmox'
    orgId: 1
    folder: 'Proxmox'
    folderUid: 'proxmox'
    type: file
    disableDeletion: false
    updateIntervalSeconds: 60
    allowUiUpdates: true
    options:
      path: /var/lib/grafana/dashboards
"""
        self.lxc.pct_write_file(vmid, "/etc/grafana/provisioning/dashboards/proxmox.yaml", dashboard_provision, pve_ssh_config)
        
        # Upload dashboard JSON
        console.print("[dim]  Installing Proxmox dashboard...[/dim]")
        dashboard_path = TEMPLATES_DIR / "proxmox-dashboard.json"
        if dashboard_path.exists():
            dashboard_json = dashboard_path.read_text()
            self.lxc.pct_write_file(vmid, "/var/lib/grafana/dashboards/proxmox-overview.json", dashboard_json, pve_ssh_config)
        
        # Fix permissions
        self.lxc.pct_exec(vmid, "chown -R grafana:grafana /var/lib/grafana", pve_ssh_config, check=True)
        self.lxc.pct_exec(vmid, "chown -R root:grafana /etc/grafana/provisioning", pve_ssh_config, check=True)
        
        # Enable and start Grafana
        console.print("[dim]  Starting Grafana...[/dim]")
        self.lxc.pct_exec(vmid, "systemctl daemon-reload", pve_ssh_config, check=True)
        self.lxc.pct_exec(vmid, "systemctl enable grafana-server", pve_ssh_config, check=True)
        self.lxc.pct_exec(vmid, "systemctl start grafana-server", pve_ssh_config, check=True)
    
    def _verify_deployment(self, config: GrafanaConfig, pve_ssh_config: dict) -> dict:
        """Verify Grafana is running correctly."""
        try:
            time.sleep(5)
            vmid = config.vmid
            
            # Check Grafana status
            result = self.lxc.pct_exec(vmid, f"curl -s http://localhost:{config.port}/api/health", pve_ssh_config, timeout=10, check=False)
            if '"database": "ok"' not in result.stdout:
                return {
                    "success": False,
                    "message": "Grafana health check failed",
                }
            
            return {
                "success": True,
                "message": "Grafana running",
            }
            
        except Exception as e:
            return {
                "success": False,
                "message": f"Verification failed: {e}",
            }
    
    def _show_completion(self, config: GrafanaConfig, verification: dict) -> None:
        """Show completion message."""
        if verification["success"]:
            status = "[bold green]✓ Grafana deployment complete![/bold green]"
        else:
            status = "[bold yellow]⚠ Grafana deployed with warnings[/bold yellow]"
        
        text = f"""
{status}

[bold]Container Info:[/bold]
  VMID: {config.vmid}
  IP: {config.ip}

[bold]Grafana:[/bold]
  URL: http://{config.ip}:{config.port}
  Username: {config.admin_user}
  Password: {config.admin_password}
  
  [dim](Credentials saved to data/.grafana-creds)[/dim]

[bold]Datasource:[/bold]
  Prometheus: {config.prometheus_url} (auto-configured)

[bold]Dashboards:[/bold]
  Proxmox VE Overview (auto-installed)

[bold]Next Steps:[/bold]
  1. Open http://{config.ip}:{config.port}
  2. Login with admin / {config.admin_password}
  3. Navigate to Dashboards > Proxmox
"""
        
        panel = Panel(text.strip(), title="Grafana", border_style="green")
        console.print(panel)
