"""Hosts configuration loader and validator.

Loads hosts.yaml and converts entries to DNS/DHCP configuration objects.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import re

import yaml

from .pfsense_config import DnsHost, DhcpReservation, DomainOverride, UPSTREAM_DNS


@dataclass
class HostEntry:
    """A static host entry with optional DHCP reservation."""
    hostname: str
    ip: str
    mac: Optional[str] = None
    description: Optional[str] = None


@dataclass
class HostsConfig:
    """Parsed hosts configuration."""
    domain: str = "local"
    hosts: list[HostEntry] = field(default_factory=list)
    reservations: list[DhcpReservation] = field(default_factory=list)
    domain_overrides: list[DomainOverride] = field(default_factory=list)
    upstream_dns: str = "cloudflare"
    dns_over_tls: bool = False
    
    def get_all_dns_hosts(self) -> list[DnsHost]:
        """Get all static DNS host entries."""
        return [
            DnsHost(
                host=h.hostname,
                domain=self.domain,
                ip=h.ip,
                description=h.description
            )
            for h in self.hosts
        ]
    
    def get_all_dhcp_reservations(self) -> list[DhcpReservation]:
        """Get DHCP reservations from hosts with MACs plus explicit reservations."""
        reservations = []
        
        # Hosts with MAC addresses get DHCP reservations
        for h in self.hosts:
            if h.mac:
                reservations.append(DhcpReservation(
                    mac=h.mac,
                    ip=h.ip,
                    hostname=h.hostname,
                    description=h.description
                ))
        
        # Add explicit reservations
        reservations.extend(self.reservations)
        
        return reservations
    
    def get_upstream_dns_servers(self) -> list[str]:
        """Get upstream DNS server IPs."""
        if self.upstream_dns in UPSTREAM_DNS:
            return UPSTREAM_DNS[self.upstream_dns]
        # Assume it's a comma-separated list of IPs
        return [ip.strip() for ip in self.upstream_dns.split(",")]


def validate_ip(ip: str) -> bool:
    """Validate IPv4 address format."""
    pattern = r"^(\d{1,3}\.){3}\d{1,3}$"
    if not re.match(pattern, ip):
        return False
    octets = ip.split(".")
    return all(0 <= int(o) <= 255 for o in octets)


def validate_mac(mac: str) -> bool:
    """Validate MAC address format."""
    pattern = r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$"
    return bool(re.match(pattern, mac))


def normalize_mac(mac: str) -> str:
    """Normalize MAC address to colon-separated lowercase."""
    # Replace dashes with colons and lowercase
    return mac.replace("-", ":").lower()


def load_hosts_config(path: Path) -> HostsConfig:
    """Load hosts configuration from YAML file.
    
    Args:
        path: Path to hosts.yaml file
        
    Returns:
        Parsed and validated HostsConfig
        
    Raises:
        FileNotFoundError: If file doesn't exist
        ValueError: If configuration is invalid
    """
    if not path.exists():
        raise FileNotFoundError(f"Hosts config not found: {path}")
    
    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}
    
    return parse_hosts_config(data)


def parse_hosts_config(data: dict) -> HostsConfig:
    """Parse hosts configuration from dictionary.
    
    Args:
        data: Dictionary from YAML or other source
        
    Returns:
        Parsed and validated HostsConfig
        
    Raises:
        ValueError: If configuration is invalid
    """
    config = HostsConfig()
    
    # Domain
    if "domain" in data:
        config.domain = data["domain"]
    
    # Upstream DNS
    if "upstream_dns" in data:
        config.upstream_dns = str(data["upstream_dns"])
    
    # DNS over TLS
    if "dns_over_tls" in data:
        config.dns_over_tls = bool(data["dns_over_tls"])
    
    # Static hosts
    hosts_data = data.get("hosts", {})
    if isinstance(hosts_data, dict):
        for hostname, host_info in hosts_data.items():
            if not isinstance(host_info, dict):
                continue
            
            ip = host_info.get("ip")
            if not ip:
                raise ValueError(f"Host '{hostname}' missing required 'ip' field")
            if not validate_ip(ip):
                raise ValueError(f"Host '{hostname}' has invalid IP: {ip}")
            
            mac = host_info.get("mac")
            if mac:
                if not validate_mac(mac):
                    raise ValueError(f"Host '{hostname}' has invalid MAC: {mac}")
                mac = normalize_mac(mac)
            
            config.hosts.append(HostEntry(
                hostname=hostname,
                ip=ip,
                mac=mac,
                description=host_info.get("description")
            ))
    
    # Explicit DHCP reservations
    reservations_data = data.get("reservations", [])
    if isinstance(reservations_data, list):
        for res in reservations_data:
            if not isinstance(res, dict):
                continue
            
            mac = res.get("mac")
            ip = res.get("ip")
            hostname = res.get("hostname")
            
            if not mac:
                raise ValueError("DHCP reservation missing required 'mac' field")
            if not ip:
                raise ValueError(f"DHCP reservation for {mac} missing required 'ip' field")
            if not validate_mac(mac):
                raise ValueError(f"DHCP reservation has invalid MAC: {mac}")
            if not validate_ip(ip):
                raise ValueError(f"DHCP reservation for {mac} has invalid IP: {ip}")
            
            config.reservations.append(DhcpReservation(
                mac=normalize_mac(mac),
                ip=ip,
                hostname=hostname,
                description=res.get("description")
            ))
    
    # Domain overrides (split DNS)
    overrides_data = data.get("domain_overrides", [])
    if isinstance(overrides_data, list):
        for override in overrides_data:
            if not isinstance(override, dict):
                continue
            
            domain = override.get("domain")
            ip = override.get("ip")
            
            if not domain:
                raise ValueError("Domain override missing required 'domain' field")
            if not ip:
                raise ValueError(f"Domain override for {domain} missing required 'ip' field")
            if not validate_ip(ip):
                raise ValueError(f"Domain override for {domain} has invalid IP: {ip}")
            
            config.domain_overrides.append(DomainOverride(
                domain=domain,
                ip=ip,
                description=override.get("description")
            ))
    
    return config


def merge_hosts_configs(*configs: HostsConfig) -> HostsConfig:
    """Merge multiple hosts configs, with later configs taking precedence.
    
    Args:
        *configs: HostsConfig objects to merge
        
    Returns:
        Merged HostsConfig
    """
    if not configs:
        return HostsConfig()
    
    result = HostsConfig()
    
    # Track seen entries by key for deduplication
    seen_hosts: dict[str, HostEntry] = {}
    seen_reservations: dict[str, DhcpReservation] = {}
    seen_overrides: dict[str, DomainOverride] = {}
    
    for config in configs:
        # Last domain wins
        result.domain = config.domain
        result.upstream_dns = config.upstream_dns
        result.dns_over_tls = config.dns_over_tls
        
        # Merge hosts by hostname
        for host in config.hosts:
            seen_hosts[host.hostname] = host
        
        # Merge reservations by MAC
        for res in config.reservations:
            seen_reservations[res.mac] = res
        
        # Merge overrides by domain
        for override in config.domain_overrides:
            seen_overrides[override.domain] = override
    
    result.hosts = list(seen_hosts.values())
    result.reservations = list(seen_reservations.values())
    result.domain_overrides = list(seen_overrides.values())
    
    return result
