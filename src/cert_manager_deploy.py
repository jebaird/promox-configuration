"""Certificate Manager Deployment Orchestration.

Deploys and configures the cert-manager LXC container for automated
SSL certificate management using Let's Encrypt and Cloudflare DNS.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import tempfile
import time

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from .proxmox_client import ProxmoxClient
from .lxc_creator import LXCCreator, LXCConfig
from .ssh_executor import SSHExecutor, wait_for_ssh
from .config import (
    get_cloudflare_credentials,
    get_default_domain,
    get_lan_subnet,
    load_yaml,
)

console = Console()

# Template directory
TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "cert-manager"


@dataclass
class CertManagerConfig:
    """Configuration for cert-manager deployment."""
    vmid: int = 105
    hostname: str = "cert-manager"
    
    # Resources
    cores: int = 1
    memory: int = 256
    swap: int = 256
    disk_size: str = "2G"
    
    # Network
    ip: str = "10.0.0.5"
    netmask: int = 24
    gateway: str = "10.0.0.1"
    bridge: str = "vmbr0"  # Default to main bridge
    
    # Template
    template: str = "debian-12-standard"
    template_storage: str = "local"
    rootfs_storage: str = "local-lvm"
    
    # Certificate settings
    domain: str = ""
    cloudflare_api_token: str = ""
    cloudflare_zone: str = ""
    cert_email: str = ""
    staging: bool = False
    
    @classmethod
    def from_yaml(cls, config_path: Path) -> "CertManagerConfig":
        """Load configuration from YAML file.
        
        Args:
            config_path: Path to cert-manager.yaml
            
        Returns:
            CertManagerConfig instance
        """
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
            # Parse IP/netmask like "10.0.0.5/24"
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
        
        # Certificate settings
        certs = data.get("certificates", {})
        if "domain" in certs:
            config.domain = certs["domain"]
        
        letsencrypt = certs.get("letsencrypt", {})
        if "staging" in letsencrypt:
            config.staging = letsencrypt["staging"]
        if "email" in letsencrypt:
            config.cert_email = letsencrypt["email"]
        
        return config


class CertManagerDeployer:
    """Orchestrates cert-manager LXC deployment."""
    
    def __init__(self, client: ProxmoxClient):
        """Initialize with Proxmox client."""
        self.client = client
        self.lxc = LXCCreator(client)
    
    def deploy(
        self,
        config: CertManagerConfig | None = None,
        dry_run: bool = False,
    ) -> bool:
        """Deploy the cert-manager container.
        
        Args:
            config: Optional config override
            dry_run: If True, only show what would be done
            
        Returns:
            True if deployment succeeded
        """
        # Load config if not provided
        if config is None:
            config = self._load_default_config()
        
        # Load Cloudflare credentials
        try:
            cf_token, cf_zone = get_cloudflare_credentials()
            config.cloudflare_api_token = cf_token
            config.cloudflare_zone = cf_zone
        except ValueError as e:
            console.print(f"[red]✗[/red] {e}")
            return False
        
        # Use domain from config or env
        if not config.domain:
            config.domain = get_default_domain()
            if config.domain == "local":
                console.print(
                    "[red]✗[/red] Domain not configured. "
                    "Set PFSENSE_DOMAIN in .env (e.g., PFSENSE_DOMAIN=lab.example.com)"
                )
                return False
        
        # Show deployment plan
        self._show_plan(config)
        
        if dry_run:
            console.print("\n[yellow]Dry run - no changes made[/yellow]")
            return True
        
        # Execute deployment
        return self._execute_deployment(config)
    
    def _load_default_config(self) -> CertManagerConfig:
        """Load config from default YAML file."""
        config_path = Path(__file__).parent.parent / "config" / "vms" / "cert-manager.yaml"
        
        if config_path.exists():
            config = CertManagerConfig.from_yaml(config_path)
        else:
            config = CertManagerConfig()
        
        # Apply subnet defaults from env
        subnet = get_lan_subnet()
        if config.ip == "10.0.0.5":  # Default not changed
            config.ip = f"{subnet}.5"
        if config.gateway == "10.0.0.1":  # Default not changed
            config.gateway = f"{subnet}.1"
        
        return config
    
    def _show_plan(self, config: CertManagerConfig) -> None:
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

[bold]Certificates:[/bold]
  Domain: *.{config.domain}, {config.domain}
  Cloudflare Zone: {config.cloudflare_zone}
  Let's Encrypt: {'STAGING' if config.staging else 'Production'}
        """
        
        panel = Panel(
            plan_text.strip(),
            title="🔐 Cert-Manager Deployment Plan",
            border_style="blue",
        )
        console.print(panel)
    
    def _execute_deployment(self, config: CertManagerConfig) -> bool:
        """Execute the deployment steps."""
        console.print("\n[bold]Starting deployment...[/bold]\n")
        
        # Generate temporary SSH keypair for initial configuration
        private_key = ed25519.Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        
        # Serialize keys
        private_key_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.OpenSSH,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")
        
        public_key_openssh = public_key.public_bytes(
            encoding=serialization.Encoding.OpenSSH,
            format=serialization.PublicFormat.OpenSSH,
        ).decode("utf-8") + " proxmox-config-setup"
        
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
                progress.update(task, description=f"[red]✗[/red] Template download failed")
                console.print(f"\n[red]Error: {e}[/red]")
                return False
            
            # Step 3: Create container with SSH key (for key-based auth)
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
                    ssh_public_keys=public_key_openssh,
                )
                
                upid = self.lxc.create_container(lxc_config)
                self.client.wait_for_task(upid, timeout=120)
                progress.update(task, description="[green]✓[/green] Container created")
            except Exception as e:
                progress.update(task, description=f"[red]✗[/red] Container creation failed")
                console.print(f"\n[red]Error: {e}[/red]")
                return False
            
            # Step 4: Wait for container to be ready
            progress.update(task, description="Waiting for container to start...")
            if not self.lxc.wait_for_container_ready(config.vmid, timeout=60):
                progress.update(task, description="[yellow]⚠[/yellow] Container may not be fully ready")
            else:
                progress.update(task, description="[green]✓[/green] Container running")
            
            # Step 5: Configure container via SSH
            progress.update(task, description="Waiting for SSH...")
            if not wait_for_ssh(config.ip, timeout=90):
                progress.update(task, description="[red]✗[/red] SSH not available")
                console.print(f"\n[red]Error: Could not connect to {config.ip}:22[/red]")
                return False
            progress.update(task, description="[green]✓[/green] SSH available")
            
            progress.update(task, description="Configuring cert-manager (this may take a few minutes)...")
            try:
                ssh_public_key = self._configure_container(config, private_key_pem)
                progress.update(task, description="[green]✓[/green] Cert-manager configured")
            except Exception as e:
                progress.update(task, description=f"[red]✗[/red] Configuration failed")
                console.print(f"\n[red]Error: {e}[/red]")
                return False
            
            # Step 6: Verify certificate was obtained
            progress.update(task, description="Verifying certificate...")
            verification_result = self._verify_deployment(config, private_key_pem)
            if verification_result["success"]:
                progress.update(task, description="[green]✓[/green] Certificate verified")
            else:
                progress.update(task, description="[yellow]⚠[/yellow] Certificate verification failed")
                console.print(f"\n[yellow]Warning: {verification_result['message']}[/yellow]")
        
        # Save management key for future use (setup-cert-targets)
        data_dir = Path(__file__).parent.parent / "data"
        data_dir.mkdir(exist_ok=True)
        key_file = data_dir / ".cert-manager.key"
        key_file.write_text(private_key_pem)
        key_file.chmod(0o600)
        
        # Show completion message
        self._show_completion(config, ssh_public_key, verification_result)
        return verification_result["success"]
    
    def _configure_container(self, config: CertManagerConfig, private_key_pem: str) -> str:
        """Configure the container after creation using SSH.
        
        Args:
            config: Container configuration
            private_key_pem: PEM-encoded private key for SSH auth
            
        Returns:
            SSH public key generated in container
        """
        ssh = SSHExecutor(
            host=config.ip,
            username="root",
            key_string=private_key_pem,
        )
        
        with ssh:
            # Configure DNS first (required for apt to work)
            console.print("[dim]  Configuring DNS...[/dim]")
            dns_config = "nameserver 8.8.8.8\nnameserver 8.8.4.4\n"
            ssh.write_file("/etc/resolv.conf", dns_config)
            
            # Install required packages
            console.print("[dim]  Installing packages...[/dim]")
            ssh.execute("apt-get update", timeout=120, check=True)
            ssh.execute(
                "apt-get install -y certbot python3-certbot-dns-cloudflare openssh-client",
                timeout=300,
                check=True,
            )
            
            # Create directories
            ssh.execute("mkdir -p /etc/letsencrypt /etc/cert-manager /root/.ssh", check=True)
            
            # Generate SSH keypair for cert distribution
            console.print("[dim]  Generating SSH keypair...[/dim]")
            ssh.execute(
                'ssh-keygen -t ed25519 -f /root/.ssh/id_ed25519 -N "" -C "cert-manager"',
                check=True,
            )
            
            # Read the public key
            ssh_public_key = ssh.read_file("/root/.ssh/id_ed25519.pub").strip()
            
            # Write Cloudflare credentials
            console.print("[dim]  Configuring Cloudflare...[/dim]")
            cf_ini = f"dns_cloudflare_api_token = {config.cloudflare_api_token}"
            ssh.write_file("/etc/letsencrypt/cloudflare.ini", cf_ini, mode=0o600)
            
            # Request certificate
            console.print("[dim]  Requesting certificate from Let's Encrypt...[/dim]")
            certbot_cmd = [
                "certbot", "certonly",
                "--dns-cloudflare",
                "--dns-cloudflare-credentials", "/etc/letsencrypt/cloudflare.ini",
                "--dns-cloudflare-propagation-seconds", "30",
                "-d", f"*.{config.domain}",
                "-d", config.domain,
                "--non-interactive",
                "--agree-tos",
            ]
            
            if config.cert_email:
                certbot_cmd.extend(["--email", config.cert_email])
            else:
                certbot_cmd.append("--register-unsafely-without-email")
            
            if config.staging:
                certbot_cmd.append("--staging")
            
            result = ssh.execute(" ".join(certbot_cmd), timeout=180)
            if not result.success:
                raise RuntimeError(f"Certbot failed: {result.stderr}")
            
            # Enable renewal timer
            console.print("[dim]  Enabling auto-renewal...[/dim]")
            ssh.execute("systemctl enable certbot.timer", check=True)
            ssh.execute("systemctl start certbot.timer", check=True)
            
            # Install deploy hook for certificate distribution
            console.print("[dim]  Installing deploy hook...[/dim]")
            deploy_hook_path = TEMPLATES_DIR / "deploy-hook.sh"
            if deploy_hook_path.exists():
                deploy_hook_content = deploy_hook_path.read_text()
                ssh.execute("mkdir -p /etc/letsencrypt/renewal-hooks/deploy", check=True)
                ssh.write_file(
                    "/etc/letsencrypt/renewal-hooks/deploy/distribute-certs.sh",
                    deploy_hook_content,
                    mode=0o755,
                )
            
            # Install cert-targets.yaml for distribution configuration
            targets_path = Path(__file__).parent.parent / "config" / "cert-targets.yaml"
            if targets_path.exists():
                targets_content = targets_path.read_text()
                ssh.write_file("/etc/cert-manager/targets.yaml", targets_content)
            
            # Install pyyaml for deploy hook
            ssh.execute("apt-get install -y python3-yaml", timeout=60)
            
            # Disable password authentication (key-only for future)
            console.print("[dim]  Hardening SSH config...[/dim]")
            ssh.execute(
                "sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config",
            )
            ssh.execute("systemctl restart sshd")
            
            return ssh_public_key
    
    def _verify_deployment(self, config: CertManagerConfig, private_key_pem: str) -> dict:
        """Verify that certificate was obtained successfully.
        
        Args:
            config: Container configuration
            private_key_pem: PEM-encoded private key for SSH auth
            
        Returns:
            Dict with 'success' boolean and 'message' or 'certificates' info
        """
        try:
            # Give sshd a moment to restart
            time.sleep(2)
            
            ssh = SSHExecutor(
                host=config.ip,
                username="root",
                key_string=private_key_pem,
            )
            
            with ssh:
                # Check certbot certificates
                result = ssh.execute("certbot certificates 2>/dev/null", timeout=30)
                
                if "No certificates found" in result.stdout:
                    return {
                        "success": False,
                        "message": "No certificates found. Certbot may have failed.",
                    }
                
                # Parse certificate info
                if f"*.{config.domain}" in result.stdout or config.domain in result.stdout:
                    # Extract expiry date
                    expiry = ""
                    for line in result.stdout.split("\n"):
                        if "Expiry Date:" in line:
                            expiry = line.split("Expiry Date:")[1].strip()
                            break
                    
                    return {
                        "success": True,
                        "message": "Certificate obtained successfully",
                        "domain": config.domain,
                        "expiry": expiry,
                        "cert_path": f"/etc/letsencrypt/live/{config.domain}/",
                    }
                
                return {
                    "success": False,
                    "message": f"Certificate for {config.domain} not found in output",
                }
                
        except Exception as e:
            return {
                "success": False,
                "message": f"Verification failed: {e}",
            }
    
    def _show_completion(
        self,
        config: CertManagerConfig,
        ssh_public_key: str,
        verification: dict,
    ) -> None:
        """Show completion message with next steps."""
        # Build status indicator
        if verification["success"]:
            status = "[bold green]✓ Cert-manager deployment complete![/bold green]"
            cert_info = f"""
[bold]Certificate:[/bold]
  Domain: *.{config.domain}, {config.domain}
  Location: {verification.get('cert_path', f'/etc/letsencrypt/live/{config.domain}/')}
  Expiry: {verification.get('expiry', 'Unknown')}
  Auto-renewal: Enabled (checks every 12 hours)
"""
        else:
            status = "[bold yellow]⚠ Cert-manager deployed with warnings[/bold yellow]"
            cert_info = f"""
[bold yellow]Certificate Status:[/bold yellow]
  {verification.get('message', 'Unknown error')}
  Check manually: ssh root@{config.ip} certbot certificates
"""
        
        completion_text = f"""
{status}

[bold]Container Info:[/bold]
  VMID: {config.vmid}
  IP: {config.ip}
  SSH: ssh root@{config.ip} (key auth only)
  Management key: data/.cert-manager.key (saved for setup-cert-targets)
{cert_info}
[bold]Certificate Distribution Key:[/bold]
[cyan]{ssh_public_key}[/cyan]

[bold]Next Steps:[/bold]
1. Run [bold]setup-cert-targets[/bold] to automatically deploy SSH keys to targets
2. Or manually add the key above to target hosts' /root/.ssh/authorized_keys
3. Test: ssh root@{config.ip} certbot certificates

[bold]Manual cert distribution:[/bold]
  scp root@{config.ip}:/etc/letsencrypt/live/{config.domain}/fullchain.pem /destination/
  scp root@{config.ip}:/etc/letsencrypt/live/{config.domain}/privkey.pem /destination/
        """
        
        panel = Panel(
            completion_text.strip(),
            title="🎉 Deployment Complete" if verification["success"] else "⚠ Deployment Finished",
            border_style="green" if verification["success"] else "yellow",
        )
        console.print(panel)


def load_cert_targets() -> dict[str, Any]:
    """Load certificate distribution targets from config."""
    config_path = Path(__file__).parent.parent / "config" / "cert-targets.yaml"
    
    if not config_path.exists():
        return {"targets": {}}
    
    with open(config_path) as f:
        return yaml.safe_load(f) or {"targets": {}}
