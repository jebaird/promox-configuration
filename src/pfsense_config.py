"""pfSense config.xml generator."""

import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

from passlib.hash import bcrypt

from .config import get_default_domain, get_lan_subnet

if TYPE_CHECKING:
    from .wizard import WizardConfig

# Path to config template
TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "pfsense_config.xml"


@dataclass
class DnsHost:
    """Static DNS host override."""
    hostname: str
    ip: str
    domain: str = ""
    description: str = ""


@dataclass
class DhcpReservation:
    """Static DHCP reservation."""
    mac: str
    ip: str
    hostname: str
    description: str = ""


@dataclass
class DomainOverride:
    """DNS domain override for split-horizon DNS."""
    domain: str
    ip: str
    description: str = ""


# Common upstream DNS servers
UPSTREAM_DNS = {
    "cloudflare": ["1.1.1.1", "1.0.0.1"],
    "cloudflare_tls": ["1.1.1.1", "1.0.0.1"],  # With DNS-over-TLS
    "google": ["8.8.8.8", "8.8.4.4"],
    "quad9": ["9.9.9.9", "149.112.112.112"],
    "opendns": ["208.67.222.222", "208.67.220.220"],
}


class PfSenseConfigBuilder:
    """Builder for pfSense config.xml files."""
    
    def __init__(self):
        """Initialize builder with default values from environment."""
        subnet = get_lan_subnet()
        
        self._hostname = "pfsense"
        self._domain = get_default_domain()
        self._lan_ip = f"{subnet}.1"
        self._lan_subnet = 24
        self._dhcp_start = f"{subnet}.100"
        self._dhcp_end = f"{subnet}.254"
        self._admin_password = ""
        self._ssh_enabled = True
        self._timestamp = int(time.time())
        
        # DNS settings
        self._dns_servers: list[str] = []
        self._dns_forwarding = False
        self._dns_tls = False
        self._register_dhcp = True
        
        # Static entries
        self._dns_hosts: list[DnsHost] = []
        self._dhcp_reservations: list[DhcpReservation] = []
        self._domain_overrides: list[DomainOverride] = []
    
    def set_hostname(self, hostname: str) -> "PfSenseConfigBuilder":
        """Set pfSense hostname."""
        self._hostname = hostname
        return self
    
    def set_domain(self, domain: str) -> "PfSenseConfigBuilder":
        """Set local domain for DNS.
        
        Args:
            domain: Domain name (e.g., 'local', 'home.lan', 'lab.example.com')
        """
        self._domain = domain
        return self
    
    def set_lan(self, ip: str, subnet: int) -> "PfSenseConfigBuilder":
        """Set LAN interface configuration.
        
        Args:
            ip: LAN IP address (e.g., '10.0.0.1')
            subnet: Subnet mask in CIDR notation (e.g., 24)
        """
        self._lan_ip = ip
        self._lan_subnet = subnet
        return self
    
    def set_dhcp_range(self, start: str, end: str) -> "PfSenseConfigBuilder":
        """Set DHCP server range.
        
        Args:
            start: First IP in range (e.g., '10.0.0.100')
            end: Last IP in range (e.g., '10.0.0.254')
        """
        self._dhcp_start = start
        self._dhcp_end = end
        return self
    
    def set_admin_password(self, password: str) -> "PfSenseConfigBuilder":
        """Set admin password (will be hashed).
        
        Args:
            password: Plain text password
        """
        self._admin_password = password
        return self
    
    def enable_ssh(self, enabled: bool = True) -> "PfSenseConfigBuilder":
        """Enable or disable SSH access."""
        self._ssh_enabled = enabled
        return self
    
    def set_upstream_dns(
        self, 
        servers: list[str] | str,
        use_tls: bool = False,
    ) -> "PfSenseConfigBuilder":
        """Set upstream DNS servers.
        
        Args:
            servers: List of DNS server IPs, or preset name ('cloudflare', 'google', etc.)
            use_tls: Enable DNS-over-TLS (requires compatible servers)
        """
        if isinstance(servers, str):
            if servers in UPSTREAM_DNS:
                self._dns_servers = UPSTREAM_DNS[servers].copy()
            else:
                self._dns_servers = [servers]
        else:
            self._dns_servers = servers.copy()
        
        self._dns_forwarding = True
        self._dns_tls = use_tls
        return self
    
    def enable_dhcp_dns_registration(self, enabled: bool = True) -> "PfSenseConfigBuilder":
        """Enable/disable DHCP hostname registration in DNS.
        
        When enabled, DHCP clients that send a hostname will automatically
        get a DNS entry: <hostname>.<domain>
        """
        self._register_dhcp = enabled
        return self
    
    def add_dns_host(
        self,
        hostname: str,
        ip: str,
        domain: str = "",
        description: str = "",
    ) -> "PfSenseConfigBuilder":
        """Add a static DNS host override.
        
        Args:
            hostname: Host name (e.g., 'nas')
            ip: IP address (e.g., '10.0.0.10')
            domain: Domain (uses default if empty)
            description: Optional description
        """
        self._dns_hosts.append(DnsHost(
            hostname=hostname,
            ip=ip,
            domain=domain or self._domain,
            description=description,
        ))
        return self
    
    def add_dhcp_reservation(
        self,
        mac: str,
        ip: str,
        hostname: str,
        description: str = "",
    ) -> "PfSenseConfigBuilder":
        """Add a static DHCP reservation.
        
        Args:
            mac: MAC address (e.g., 'aa:bb:cc:dd:ee:ff')
            ip: Reserved IP address
            hostname: Hostname for DNS registration
            description: Optional description
        """
        # Normalize MAC address format
        mac = mac.lower().replace("-", ":")
        
        self._dhcp_reservations.append(DhcpReservation(
            mac=mac,
            ip=ip,
            hostname=hostname,
            description=description,
        ))
        return self
    
    def add_domain_override(
        self,
        domain: str,
        ip: str,
        description: str = "",
    ) -> "PfSenseConfigBuilder":
        """Add a DNS domain override for split-horizon DNS.
        
        Use this to resolve external domains to internal IPs.
        
        Args:
            domain: Full domain name (e.g., 'plex.example.com')
            ip: Internal IP address
            description: Optional description
        """
        self._domain_overrides.append(DomainOverride(
            domain=domain,
            ip=ip,
            description=description,
        ))
        return self
    
    @classmethod
    def from_wizard_config(cls, config: "WizardConfig") -> "PfSenseConfigBuilder":
        """Create builder from wizard configuration.
        
        Args:
            config: WizardConfig object from wizard
            
        Returns:
            Configured PfSenseConfigBuilder
        """
        builder = cls()
        builder.set_hostname(config.vm_name)
        builder.set_domain(config.domain)
        builder.set_lan(config.lan_ip, config.lan_netmask)
        builder.set_dhcp_range(config.dhcp_start, config.dhcp_end)
        builder.set_admin_password(config.admin_password)
        builder.enable_ssh(config.enable_ssh)
        builder.enable_dhcp_dns_registration(config.register_dhcp_hostnames)
        
        # Set upstream DNS
        if config.upstream_dns:
            builder.set_upstream_dns(config.upstream_dns, config.dns_over_tls)
        
        # Add static DNS hosts
        for host in config.dns_hosts:
            builder.add_dns_host(
                hostname=host.host,
                ip=host.ip,
                description=host.description or "",
            )
        
        # Add DHCP reservations
        for reservation in config.dhcp_reservations:
            builder.add_dhcp_reservation(
                mac=reservation.mac,
                ip=reservation.ip,
                hostname=reservation.hostname,
                description=reservation.description or "",
            )
        
        # Add domain overrides (split DNS)
        for override in config.domain_overrides:
            builder.add_domain_override(
                domain=override.domain,
                ip=override.ip,
                description=override.description or "",
            )
        
        return builder
    
    def _hash_password(self, password: str) -> str:
        """Hash password using bcrypt (pfSense format).
        
        pfSense uses bcrypt with $2b$ prefix.
        """
        if not password:
            # Default password hash for 'pfsense'
            return "$2b$10$v0wU0xQ0xQ0xQ0xQ0xQ0xedefaulthashdontuse"
        
        # Generate bcrypt hash
        hashed = bcrypt.using(rounds=10).hash(password)
        return hashed
    
    def _generate_tracker(self) -> str:
        """Generate a random rule tracker ID."""
        return str(random.randint(1000000000, 9999999999))
    
    def _build_dns_host_overrides(self) -> str:
        """Build XML for DNS host overrides."""
        if not self._dns_hosts:
            return ""
        
        lines = []
        for host in self._dns_hosts:
            lines.append("        <hosts>")
            lines.append(f"            <host>{host.hostname}</host>")
            lines.append(f"            <domain>{host.domain}</domain>")
            lines.append(f"            <ip>{host.ip}</ip>")
            lines.append(f"            <descr>{host.description}</descr>")
            lines.append("        </hosts>")
        
        return "\n".join(lines)
    
    def _build_domain_overrides(self) -> str:
        """Build XML for DNS domain overrides (split DNS)."""
        if not self._domain_overrides:
            return ""
        
        lines = []
        for override in self._domain_overrides:
            lines.append("        <domainoverrides>")
            lines.append(f"            <domain>{override.domain}</domain>")
            lines.append(f"            <ip>{override.ip}</ip>")
            lines.append(f"            <descr>{override.description}</descr>")
            lines.append("        </domainoverrides>")
        
        return "\n".join(lines)
    
    def _build_dhcp_static_mappings(self) -> str:
        """Build XML for DHCP static mappings."""
        if not self._dhcp_reservations:
            return ""
        
        lines = []
        for res in self._dhcp_reservations:
            lines.append("            <staticmap>")
            lines.append(f"                <mac>{res.mac}</mac>")
            lines.append(f"                <ipaddr>{res.ip}</ipaddr>")
            lines.append(f"                <hostname>{res.hostname}</hostname>")
            lines.append(f"                <descr>{res.description}</descr>")
            lines.append("            </staticmap>")
        
        return "\n".join(lines)
    
    def build(self) -> str:
        """Build the config.xml content.
        
        Returns:
            Complete config.xml as string
        """
        # Load template
        if not TEMPLATE_PATH.exists():
            raise FileNotFoundError(f"Template not found: {TEMPLATE_PATH}")
        
        template = TEMPLATE_PATH.read_text(encoding="utf-8")
        
        # Build DNS servers string
        dns_servers_xml = ""
        for server in self._dns_servers:
            dns_servers_xml += f"<dnsserver>{server}</dnsserver>"
        
        # Replace placeholders
        replacements = {
            "{{TIMESTAMP}}": str(self._timestamp),
            "{{HOSTNAME}}": self._hostname,
            "{{DOMAIN}}": self._domain,
            "{{LAN_IP}}": self._lan_ip,
            "{{LAN_SUBNET}}": str(self._lan_subnet),
            "{{DHCP_START}}": self._dhcp_start,
            "{{DHCP_END}}": self._dhcp_end,
            "{{ADMIN_PASSWORD_HASH}}": self._hash_password(self._admin_password),
            "{{SSH_ENABLED}}": "enabled" if self._ssh_enabled else "",
            "{{RULE_TRACKER_1}}": self._generate_tracker(),
            "{{RULE_TRACKER_2}}": self._generate_tracker(),
            # DNS settings
            "{{DNS_SERVERS}}": dns_servers_xml,
            "{{DNS_FORWARDING_ENABLED}}": "1" if self._dns_forwarding else "",
            "{{DNS_FORWARD_TLS}}": "1" if self._dns_tls else "",
            # Static entries
            "{{DNS_HOST_OVERRIDES}}": self._build_dns_host_overrides(),
            "{{DNS_DOMAIN_OVERRIDES}}": self._build_domain_overrides(),
            "{{DHCP_STATIC_MAPPINGS}}": self._build_dhcp_static_mappings(),
        }
        
        config = template
        for placeholder, value in replacements.items():
            config = config.replace(placeholder, value)
        
        return config
    
    def save(self, path: Path | str) -> Path:
        """Build and save config.xml to file.
        
        Args:
            path: Destination file path
            
        Returns:
            Path to saved file
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        config = self.build()
        path.write_text(config, encoding="utf-8")
        
        return path


def generate_pfsense_config(config: "WizardConfig", output_path: Path | str | None = None) -> str:
    """Generate pfSense config.xml from wizard configuration.
    
    Args:
        config: WizardConfig from wizard
        output_path: Optional path to save config file
        
    Returns:
        Config XML as string
    """
    builder = PfSenseConfigBuilder.from_wizard_config(config)
    xml_content = builder.build()
    
    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(xml_content, encoding="utf-8")
    
    return xml_content
