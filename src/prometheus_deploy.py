"""Prometheus Monitoring Deployment Orchestration.

Deploys and configures a Prometheus LXC container with pve-exporter
for Proxmox VE monitoring. Uses pct exec for configuration (no SSH keys needed).
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import time

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from .proxmox_client import ProxmoxClient
from .lxc_creator import LXCCreator, LXCConfig
from .config import get_lan_subnet, load_proxmox_config, get_proxmox_ssh_config

console = Console()

# Template directory
TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "prometheus"


@dataclass
class PrometheusConfig:
    """Configuration for Prometheus deployment."""
    vmid: int = 110
    hostname: str = "prometheus"
    
    # Resources
    cores: int = 2
    memory: int = 1024
    swap: int = 512
    disk_size: str = "20G"
    
    # Network
    ip: str = "10.0.0.10"
    netmask: int = 24
    gateway: str = "10.0.0.2"
    bridge: str = "vmbr0"
    
    # Template
    template: str = "debian-12-standard"
    template_storage: str = "local"
    rootfs_storage: str = "local-lvm"
    
    # Prometheus settings
    retention_time: str = "15d"
    retention_size: str = "15GB"
    scrape_interval: str = "15s"
    port: int = 9090
    
    # PVE Exporter settings
    pve_exporter_port: int = 9221
    pve_host: str = ""
    pve_user: str = ""
    pve_token_name: str = ""
    pve_token_value: str = ""
    
    @classmethod
    def from_yaml(cls, config_path: Path) -> "PrometheusConfig":
        """Load configuration from YAML file."""
        with open(config_path) as f:
            data = yaml.safe_load(f)
        
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
        
        # Prometheus settings
        prometheus = data.get("prometheus", {})
        if "port" in prometheus:
            config.port = prometheus["port"]
        if "scrape_interval" in prometheus:
            config.scrape_interval = prometheus["scrape_interval"]
        
        retention = prometheus.get("retention", {})
        if "time" in retention:
            config.retention_time = retention["time"]
        if "size" in retention:
            config.retention_size = retention["size"]
        
        # PVE Exporter settings
        pve_exporter = data.get("pve_exporter", {})
        if "port" in pve_exporter:
            config.pve_exporter_port = pve_exporter["port"]
        
        return config


class PrometheusDeployer:
    """Orchestrates Prometheus LXC deployment."""
    
    def __init__(self, client: ProxmoxClient):
        """Initialize with Proxmox client."""
        self.client = client
        self.lxc = LXCCreator(client)
    
    def deploy(
        self,
        config: PrometheusConfig | None = None,
        dry_run: bool = False,
    ) -> bool:
        """Deploy the Prometheus container.
        
        Args:
            config: Optional config override
            dry_run: If True, only show what would be done
            
        Returns:
            True if deployment succeeded
        """
        # Load config if not provided
        if config is None:
            config = self._load_default_config()
        
        # Load Proxmox credentials for pve-exporter
        try:
            pve_config = load_proxmox_config()
            config.pve_host = pve_config["host"]
            
            # Parse token credentials from environment
            import os
            token_id = os.environ.get("PROXMOX_TOKEN_ID", "")
            token_secret = os.environ.get("PROXMOX_TOKEN_SECRET", "")
            
            if "@" in token_id and "!" in token_id:
                # Format: user@realm!tokenname
                user_realm, token_name = token_id.split("!")
                config.pve_user = user_realm
                config.pve_token_name = token_name
                config.pve_token_value = token_secret
            else:
                raise ValueError("Invalid PROXMOX_TOKEN_ID format")
                
        except Exception as e:
            console.print(f"[red]✗[/red] Failed to load Proxmox credentials: {e}")
            return False
        
        # Show deployment plan
        self._show_plan(config)
        
        if dry_run:
            console.print("\n[yellow]Dry run - no changes made[/yellow]")
            return True
        
        # Execute deployment
        return self._execute_deployment(config)
    
    def _load_default_config(self) -> PrometheusConfig:
        """Load config from default YAML file."""
        config_path = Path(__file__).parent.parent / "config" / "vms" / "prometheus.yaml"
        
        if config_path.exists():
            config = PrometheusConfig.from_yaml(config_path)
        else:
            config = PrometheusConfig()
        
        # Apply subnet defaults from env
        subnet = get_lan_subnet()
        if config.ip == "10.0.0.10":
            config.ip = f"{subnet}.10"
        if config.gateway == "10.0.0.2":
            config.gateway = f"{subnet}.2"
        
        return config
    
    def _show_plan(self, config: PrometheusConfig) -> None:
        """Display deployment plan."""
        plan_text = f"""
[bold]Container:[/bold]
  VMID: {config.vmid}
  Hostname: {config.hostname}
  Template: {config.template}
  Resources: {config.cores} cores, {config.memory}MB RAM, {config.disk_size} disk

[bold]Network:[/bold]
  IP: {config.ip}/{config.netmask}
  Gateway: {config.gateway}
  Bridge: {config.bridge}

[bold]Prometheus:[/bold]
  Port: {config.port}
  Retention: {config.retention_time} / {config.retention_size}
  Scrape Interval: {config.scrape_interval}

[bold]PVE Exporter:[/bold]
  Port: {config.pve_exporter_port}
  Target: {config.pve_host}
        """
        
        panel = Panel(
            plan_text.strip(),
            title="📊 Prometheus Deployment Plan",
            border_style="blue",
        )
        console.print(panel)
    
    def _execute_deployment(self, config: PrometheusConfig) -> bool:
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
            progress.update(task, description="Installing Prometheus (this may take a few minutes)...")
            try:
                self._configure_container(config, pve_ssh_config)
                progress.update(task, description="[green]✓[/green] Prometheus configured")
            except Exception as e:
                progress.update(task, description="[red]✗[/red] Configuration failed")
                console.print(f"\n[red]Error: {e}[/red]")
                return False
            
            # Step 6: Verify deployment
            progress.update(task, description="Verifying deployment...")
            verification = self._verify_deployment(config, pve_ssh_config)
            if verification["success"]:
                progress.update(task, description="[green]✓[/green] Prometheus verified")
            else:
                progress.update(task, description="[yellow]⚠[/yellow] Verification failed")
                console.print(f"\n[yellow]Warning: {verification['message']}[/yellow]")
        
        # Show completion message
        self._show_completion(config, verification)
        return verification["success"]
    
    def _configure_container(self, config: PrometheusConfig, pve_ssh_config: dict) -> None:
        """Configure the container via pct exec."""
        vmid = config.vmid
        
        # Configure DNS
        console.print("[dim]  Configuring DNS...[/dim]")
        dns_config = "nameserver 8.8.8.8\nnameserver 8.8.4.4\n"
        self.lxc.pct_write_file(vmid, "/etc/resolv.conf", dns_config, pve_ssh_config)
        
        # Install Prometheus
        console.print("[dim]  Installing Prometheus...[/dim]")
        self.lxc.pct_exec(vmid, "apt-get update", pve_ssh_config, timeout=120, check=True)
        self.lxc.pct_exec(vmid, "apt-get install -y prometheus python3-pip curl", pve_ssh_config, timeout=300, check=True)
        
        # Install pve-exporter
        console.print("[dim]  Installing pve-exporter...[/dim]")
        self.lxc.pct_exec(vmid, "pip3 install prometheus-pve-exporter --break-system-packages", pve_ssh_config, timeout=120, check=True)
        
        # Create config directories
        self.lxc.pct_exec(vmid, "mkdir -p /etc/prometheus", pve_ssh_config, check=True)
        
        # Write pve-exporter config
        console.print("[dim]  Configuring pve-exporter...[/dim]")
        pve_config = f"""default:
  user: {config.pve_user}
  token_name: {config.pve_token_name}
  token_value: {config.pve_token_value}
  verify_ssl: false
"""
        self.lxc.pct_write_file(vmid, "/etc/prometheus/pve.yml", pve_config, pve_ssh_config, mode=0o600)
        
        # Write Prometheus config
        console.print("[dim]  Configuring Prometheus...[/dim]")
        prometheus_config = f"""global:
  scrape_interval: {config.scrape_interval}
  evaluation_interval: {config.scrape_interval}

scrape_configs:
  - job_name: 'prometheus'
    static_configs:
      - targets: ['localhost:9090']

  - job_name: 'pve'
    static_configs:
      - targets: ['localhost:{config.pve_exporter_port}']
    metrics_path: /pve
    params:
      module: [default]
      target: [{config.pve_host}]
"""
        self.lxc.pct_write_file(vmid, "/etc/prometheus/prometheus.yml", prometheus_config, pve_ssh_config)
        
        # Create pve-exporter systemd service
        console.print("[dim]  Creating pve-exporter service...[/dim]")
        pve_exporter_service = f"""[Unit]
Description=Prometheus PVE Exporter
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/pve_exporter --config.file /etc/prometheus/pve.yml --web.listen-address=:{config.pve_exporter_port}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
        self.lxc.pct_write_file(vmid, "/etc/systemd/system/pve-exporter.service", pve_exporter_service, pve_ssh_config)
        
        # Update Prometheus service to use retention settings
        console.print("[dim]  Configuring Prometheus retention...[/dim]")
        self.lxc.pct_exec(vmid, "mkdir -p /etc/systemd/system/prometheus.service.d", pve_ssh_config, check=True)
        override_config = f"""[Service]
ExecStart=
ExecStart=/usr/bin/prometheus --config.file=/etc/prometheus/prometheus.yml --storage.tsdb.path=/var/lib/prometheus --storage.tsdb.retention.time={config.retention_time} --storage.tsdb.retention.size={config.retention_size} --web.listen-address=:{config.port}
"""
        self.lxc.pct_write_file(vmid, "/etc/systemd/system/prometheus.service.d/override.conf", override_config, pve_ssh_config)
        
        # Reload and start services
        console.print("[dim]  Starting services...[/dim]")
        self.lxc.pct_exec(vmid, "systemctl daemon-reload", pve_ssh_config, check=True)
        self.lxc.pct_exec(vmid, "systemctl enable pve-exporter", pve_ssh_config, check=True)
        self.lxc.pct_exec(vmid, "systemctl start pve-exporter", pve_ssh_config, check=True)
        self.lxc.pct_exec(vmid, "systemctl restart prometheus", pve_ssh_config, check=True)
    
    def _verify_deployment(self, config: PrometheusConfig, pve_ssh_config: dict) -> dict:
        """Verify Prometheus is running correctly."""
        try:
            time.sleep(5)  # Give services time to start
            vmid = config.vmid
            
            # Check Prometheus status
            result = self.lxc.pct_exec(vmid, f"curl -s http://localhost:{config.port}/-/healthy", pve_ssh_config, timeout=10, check=False)
            if "Prometheus Server is Healthy" not in result.stdout:
                return {
                    "success": False,
                    "message": "Prometheus health check failed",
                }
            
            # Check pve-exporter
            result = self.lxc.pct_exec(vmid, f"curl -s http://localhost:{config.pve_exporter_port}/pve?target={config.pve_host}", pve_ssh_config, timeout=15, check=False)
            if "pve_up" not in result.stdout:
                return {
                    "success": False,
                    "message": "pve-exporter not returning metrics",
                }
            
            return {
                "success": True,
                "message": "All services running",
            }
            
        except Exception as e:
            return {
                "success": False,
                "message": f"Verification failed: {e}",
            }
    
    def _show_completion(self, config: PrometheusConfig, verification: dict) -> None:
        """Show completion message."""
        if verification["success"]:
            status = "[bold green]✓ Prometheus deployment complete![/bold green]"
        else:
            status = "[bold yellow]⚠ Prometheus deployed with warnings[/bold yellow]"
        
        text = f"""
{status}

[bold]Container Info:[/bold]
  VMID: {config.vmid}
  IP: {config.ip}

[bold]Services:[/bold]
  Prometheus: http://{config.ip}:{config.port}
  PVE Exporter: http://{config.ip}:{config.pve_exporter_port}

[bold]Verify:[/bold]
  curl http://{config.ip}:{config.port}/-/healthy
  curl http://{config.ip}:{config.port}/api/v1/targets

[bold]Next:[/bold]
  Deploy Grafana: proxmox-config deploy-grafana
"""
        
        panel = Panel(text.strip(), title="Prometheus", border_style="green")
        console.print(panel)
