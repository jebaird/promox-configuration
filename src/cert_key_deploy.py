"""Certificate Key Deployment Module.

Handles automated deployment of SSH keys and restricted receiver scripts
to certificate distribution targets (Proxmox, pfSense, etc.).
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import getpass

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt, Confirm

from .ssh_executor import SSHExecutor, wait_for_ssh
from .config import get_lan_subnet, load_yaml

console = Console()

# Template directory
TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "cert-manager"


@dataclass
class TargetConfig:
    """Configuration for a certificate distribution target."""
    name: str
    host: str
    user: str = "root"
    cert_path: str = ""
    key_path: str = ""
    fullchain_path: str = ""
    reload_cmd: str = ""
    enabled: bool = True
    
    @property
    def allowed_cert_paths(self) -> str:
        """Get space-separated list of allowed cert paths."""
        paths = []
        if self.cert_path:
            paths.append(self.cert_path)
        if self.fullchain_path:
            paths.append(self.fullchain_path)
        return " ".join(paths)
    
    @property
    def allowed_key_paths(self) -> str:
        """Get space-separated list of allowed key paths."""
        if self.key_path:
            return self.key_path
        return ""


class CertKeyDeployer:
    """Deploys SSH keys and receiver scripts to certificate targets."""
    
    def __init__(self, cert_manager_ip: str, cert_manager_key: str):
        """Initialize with cert-manager connection info.
        
        Args:
            cert_manager_ip: IP address of cert-manager container
            cert_manager_key: SSH private key (PEM) for accessing cert-manager
        """
        self.cert_manager_ip = cert_manager_ip
        self.cert_manager_key = cert_manager_key
        self._cert_manager_pubkey: str | None = None
    
    def get_cert_manager_pubkey(self) -> str:
        """Get the public key from cert-manager container.
        
        Returns:
            OpenSSH public key string
        """
        if self._cert_manager_pubkey:
            return self._cert_manager_pubkey
        
        ssh = SSHExecutor(
            host=self.cert_manager_ip,
            username="root",
            key_string=self.cert_manager_key,
        )
        
        with ssh:
            result = ssh.execute("cat /root/.ssh/id_ed25519.pub")
            if not result.success:
                raise RuntimeError(f"Failed to get public key: {result.stderr}")
            self._cert_manager_pubkey = result.stdout.strip()
        
        return self._cert_manager_pubkey
    
    def load_targets(self) -> list[TargetConfig]:
        """Load certificate distribution targets from config.
        
        Returns:
            List of enabled TargetConfig objects
        """
        from .config import load_yaml_file
        config_path = Path(__file__).parent.parent / "config" / "cert-targets.yaml"
        
        if not config_path.exists():
            console.print("[yellow]Warning: cert-targets.yaml not found[/yellow]")
            return []
        
        data = load_yaml_file(config_path) or {}
        
        targets = []
        for name, target_data in data.get("targets", {}).items():
            if not target_data.get("enabled", True):
                continue
            
            target = TargetConfig(
                name=name,
                host=target_data.get("host", ""),
                user=target_data.get("user", "root"),
                cert_path=target_data.get("cert_path", ""),
                key_path=target_data.get("key_path", ""),
                fullchain_path=target_data.get("fullchain_path", ""),
                reload_cmd=target_data.get("reload_cmd", ""),
                enabled=target_data.get("enabled", True),
            )
            
            if target.host:
                targets.append(target)
        
        return targets
    
    def generate_receiver_script(self, target: TargetConfig) -> str:
        """Generate the cert-receive.sh script for a target.
        
        Args:
            target: Target configuration
            
        Returns:
            Script content with paths filled in
        """
        template_path = TEMPLATES_DIR / "cert-receive.sh"
        
        with open(template_path) as f:
            script = f.read()
        
        # Replace placeholders
        script = script.replace("{{ALLOWED_CERT_PATHS}}", target.allowed_cert_paths)
        script = script.replace("{{ALLOWED_KEY_PATHS}}", target.allowed_key_paths)
        script = script.replace("{{RELOAD_CMD}}", target.reload_cmd)
        
        return script
    
    def generate_authorized_keys_entry(self, pubkey: str) -> str:
        """Generate restricted authorized_keys entry.
        
        Args:
            pubkey: OpenSSH public key
            
        Returns:
            Full authorized_keys line with restrictions
        """
        # Get LAN subnet for IP restriction
        lan_subnet = get_lan_subnet()
        # Convert "10.0.0" to "10.0.0.0/24"
        ip_range = f"{lan_subnet}.0/24"
        
        # Build restrictions
        restrictions = [
            f'from="{ip_range}"',
            'command="/usr/local/bin/cert-receive.sh"',
            'no-port-forwarding',
            'no-X11-forwarding',
            'no-agent-forwarding',
            'no-pty',
        ]
        
        return f"{','.join(restrictions)} {pubkey}"
    
    def deploy_to_target(
        self,
        target: TargetConfig,
        password: str,
        pubkey: str,
    ) -> dict[str, Any]:
        """Deploy receiver script and SSH key to a single target.
        
        Args:
            target: Target configuration
            password: Root password for initial SSH access
            pubkey: cert-manager public key
            
        Returns:
            Dict with 'success', 'message', and optional 'details'
        """
        result = {
            "success": False,
            "message": "",
            "details": {},
        }
        
        # Check SSH connectivity
        console.print(f"[dim]  Checking SSH connectivity to {target.host}...[/dim]")
        if not wait_for_ssh(target.host, timeout=10):
            result["message"] = f"Cannot connect to {target.host}:22. Is SSH enabled?"
            if target.name == "pfsense":
                result["message"] += "\nFor pfSense: System > Advanced > Admin Access > Enable SSH"
            return result
        
        try:
            ssh = SSHExecutor(
                host=target.host,
                username=target.user,
                password=password,
            )
            
            with ssh:
                # Step 1: Backup existing authorized_keys
                console.print("[dim]  Backing up authorized_keys...[/dim]")
                backup_result = ssh.execute(
                    "cp /root/.ssh/authorized_keys /root/.ssh/authorized_keys.bak 2>/dev/null || true"
                )
                
                # Step 2: Ensure .ssh directory exists
                ssh.execute("mkdir -p /root/.ssh && chmod 700 /root/.ssh", check=True)
                
                # Step 3: Deploy receiver script
                console.print("[dim]  Deploying cert-receive.sh...[/dim]")
                script_content = self.generate_receiver_script(target)
                ssh.write_file("/usr/local/bin/cert-receive.sh", script_content, mode=0o755)
                result["details"]["script_deployed"] = True
                
                # Step 4: Generate and add authorized_keys entry
                console.print("[dim]  Adding restricted SSH key...[/dim]")
                auth_entry = self.generate_authorized_keys_entry(pubkey)
                
                # Check if key already exists (avoid duplicates)
                existing = ssh.execute("cat /root/.ssh/authorized_keys 2>/dev/null || true")
                if "cert-manager" in existing.stdout:
                    # Remove old cert-manager entries
                    ssh.execute(
                        "grep -v 'cert-manager' /root/.ssh/authorized_keys > /tmp/ak_new 2>/dev/null; "
                        "mv /tmp/ak_new /root/.ssh/authorized_keys 2>/dev/null || true"
                    )
                
                # Append new entry
                ssh.execute(
                    f"echo '{auth_entry}' >> /root/.ssh/authorized_keys && "
                    "chmod 600 /root/.ssh/authorized_keys",
                    check=True,
                )
                result["details"]["key_added"] = True
                
                # Step 5: Test the script
                console.print("[dim]  Testing receiver script...[/dim]")
                test_result = ssh.execute("/usr/local/bin/cert-receive.sh test")
                if "OK: cert-receive.sh is working" in test_result.stdout:
                    result["details"]["script_tested"] = True
                else:
                    result["message"] = f"Script test failed: {test_result.stdout}"
                    return result
                
                result["success"] = True
                result["message"] = "Successfully deployed"
                
        except Exception as e:
            result["message"] = f"Deployment failed: {e}"
        
        return result
    
    def deploy_all_targets(
        self,
        dry_run: bool = False,
        auto_confirm: bool = False,
        default_password: str | None = None,
    ) -> dict[str, Any]:
        """Deploy receiver scripts and SSH keys to all enabled targets.
        
        Args:
            dry_run: If True, only show what would be done
            auto_confirm: If True, skip confirmation prompts
            default_password: Password to use for all targets (skips prompts)
            
        Returns:
            Summary of deployment results
        """
        targets = self.load_targets()
        
        if not targets:
            console.print("[yellow]No enabled targets found in cert-targets.yaml[/yellow]")
            return {"success": False, "message": "No targets configured"}
        
        # Get cert-manager public key
        console.print("[bold]Retrieving cert-manager public key...[/bold]")
        try:
            pubkey = self.get_cert_manager_pubkey()
            console.print(f"[green]✓[/green] Got public key")
        except Exception as e:
            console.print(f"[red]✗[/red] Failed to get public key: {e}")
            return {"success": False, "message": str(e)}
        
        # Show deployment plan
        self._show_targets_table(targets, pubkey)
        
        if dry_run:
            console.print("\n[yellow]Dry run - no changes made[/yellow]")
            return {"success": True, "message": "Dry run"}
        
        # Confirm unless auto_confirm is set
        if not auto_confirm:
            if not Confirm.ask("\nProceed with deployment?"):
                return {"success": False, "message": "Cancelled by user"}
        
        # Deploy to each target
        results = {}
        for target in targets:
            console.print(f"\n[bold]Deploying to {target.name} ({target.host})...[/bold]")
            
            # Use default password if provided, otherwise prompt
            if default_password:
                password = default_password
            else:
                password = Prompt.ask(
                    f"Root password for {target.host}",
                    password=True,
                )
            
            if not password:
                console.print("[yellow]Skipped (no password)[/yellow]")
                results[target.name] = {"success": False, "message": "Skipped"}
                continue
            
            result = self.deploy_to_target(target, password, pubkey)
            results[target.name] = result
            
            if result["success"]:
                console.print(f"[green]✓[/green] {target.name}: {result['message']}")
            else:
                console.print(f"[red]✗[/red] {target.name}: {result['message']}")
        
        # Show summary
        self._show_results_summary(results)
        
        return {
            "success": all(r["success"] for r in results.values()),
            "results": results,
        }
    
    def verify_targets(self) -> dict[str, Any]:
        """Verify connectivity from cert-manager to all targets.
        
        Returns:
            Verification results per target
        """
        targets = self.load_targets()
        results = {}
        
        console.print("[bold]Verifying cert-manager can reach targets...[/bold]\n")
        
        ssh = SSHExecutor(
            host=self.cert_manager_ip,
            username="root",
            key_string=self.cert_manager_key,
        )
        
        with ssh:
            for target in targets:
                console.print(f"Testing {target.name} ({target.host})... ", end="")
                
                try:
                    # Test SSH from cert-manager to target
                    test_cmd = f'ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=no {target.user}@{target.host} "test"'
                    result = ssh.execute(test_cmd, timeout=30)
                    
                    if "OK: cert-receive.sh is working" in result.stdout:
                        console.print("[green]✓ Working[/green]")
                        results[target.name] = {"success": True, "message": "OK"}
                    elif result.exit_code == 0:
                        console.print("[yellow]⚠ Connected but unexpected response[/yellow]")
                        results[target.name] = {"success": False, "message": result.stdout}
                    else:
                        console.print(f"[red]✗ Failed[/red]")
                        results[target.name] = {"success": False, "message": result.stderr}
                except Exception as e:
                    console.print(f"[red]✗ Error: {e}[/red]")
                    results[target.name] = {"success": False, "message": str(e)}
        
        return results
    
    def _show_targets_table(self, targets: list[TargetConfig], pubkey: str) -> None:
        """Display targets table."""
        table = Table(title="Certificate Distribution Targets")
        table.add_column("Name", style="cyan")
        table.add_column("Host", style="green")
        table.add_column("Cert/Fullchain Path")
        table.add_column("Key Path")
        table.add_column("Reload Command")
        
        for target in targets:
            # Show cert_path or fullchain_path (whichever is configured)
            cert_display = target.cert_path or target.fullchain_path or "-"
            if target.fullchain_path and not target.cert_path:
                cert_display = f"{target.fullchain_path} (fullchain)"
            
            table.add_row(
                target.name,
                target.host,
                cert_display,
                target.key_path or "-",
                target.reload_cmd[:30] + "..." if len(target.reload_cmd) > 30 else target.reload_cmd or "-",
            )
        
        console.print(table)
        
        # Show restricted key info
        lan_subnet = get_lan_subnet()
        console.print(f"\n[bold]SSH Key Restrictions:[/bold]")
        console.print(f"  • IP range: {lan_subnet}.0/24")
        console.print(f"  • Command: /usr/local/bin/cert-receive.sh only")
        console.print(f"  • No port forwarding, X11, agent forwarding, or PTY")
    
    def _show_results_summary(self, results: dict[str, Any]) -> None:
        """Display deployment results summary."""
        console.print("\n[bold]Deployment Summary:[/bold]")
        
        success_count = sum(1 for r in results.values() if r["success"])
        total_count = len(results)
        
        for name, result in results.items():
            status = "[green]✓[/green]" if result["success"] else "[red]✗[/red]"
            console.print(f"  {status} {name}: {result['message']}")
        
        console.print(f"\n{success_count}/{total_count} targets configured successfully")
        
        if success_count < total_count:
            console.print("\n[yellow]Some targets failed. You can re-run setup-cert-targets to retry.[/yellow]")
        else:
            console.print("\n[green]All targets configured! Run 'verify-cert-targets' to test.[/green]")
