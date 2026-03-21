"""Configuration loading utilities."""

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


# Load .env files on module import (base first, then test override if in test mode)
_env_loaded = False


def _load_env_files() -> None:
    """Load environment files with proper layering.
    
    Loads in order:
    1. .env (base/production values)
    2. test/.env (test overrides, if PROXMOX_HOST matches test pattern)
    """
    global _env_loaded
    if _env_loaded:
        return
    
    root = Path(__file__).parent.parent
    
    # Load base .env first
    base_env = root / ".env"
    if base_env.exists():
        load_dotenv(base_env)
    
    # Auto-detect test mode and load test/.env overrides
    # Test mode if PROXMOX_HOST is already set to 172.30.x.x (Docker network)
    test_env = root / "test" / ".env"
    if test_env.exists():
        # Check if we should load test overrides
        # This happens when running via docker-compose.test.yaml which sets PROXMOX_HOST
        host = os.getenv("PROXMOX_HOST", "")
        if host.startswith("172.30.") or host == "localhost":
            load_dotenv(test_env, override=True)
    
    _env_loaded = True


def expand_env_vars(text: str) -> str:
    """Expand environment variables in text.
    
    Supports:
    - ${VAR} - Replace with env var value (empty string if not set)
    - ${VAR:-default} - Replace with env var or default if not set
    - ${VAR:?error} - Replace with env var or raise error if not set
    
    Args:
        text: Text containing ${VAR} patterns
        
    Returns:
        Text with environment variables expanded
        
    Raises:
        ValueError: If ${VAR:?error} pattern is used and VAR is not set
    """
    # Ensure env files are loaded
    _load_env_files()
    
    def replace_var(match: re.Match) -> str:
        full_match = match.group(0)
        var_name = match.group(1)
        modifier = match.group(2)  # Either None, ":-default", or ":?error"
        
        value = os.getenv(var_name)
        
        if value is not None:
            return value
        elif modifier is None:
            # ${VAR} with no default
            return ""
        elif modifier.startswith(":-"):
            # ${VAR:-default}
            return modifier[2:]
        elif modifier.startswith(":?"):
            # ${VAR:?error}
            raise ValueError(f"Required environment variable {var_name} is not set: {modifier[2:]}")
        else:
            return ""
    
    # Pattern matches ${VAR}, ${VAR:-default}, or ${VAR:?error}
    # Group 1: variable name
    # Group 2: optional modifier (:-default or :?error)
    pattern = r'\$\{([A-Za-z_][A-Za-z0-9_]*)(:-[^}]*|:\?[^}]*)?\}'
    
    return re.sub(pattern, replace_var, text)


def get_config_dir() -> Path:
    """Get the configuration directory path."""
    return Path(__file__).parent.parent / "config"


def load_yaml(filename: str, expand_vars: bool = True) -> dict[str, Any]:
    """Load a YAML configuration file.
    
    Args:
        filename: Name of the YAML file (with or without .yaml extension)
        expand_vars: If True, expand ${VAR} patterns in the file
        
    Returns:
        Parsed YAML content as dictionary
        
    Raises:
        FileNotFoundError: If config file doesn't exist
        yaml.YAMLError: If YAML parsing fails
    """
    # Ensure env files are loaded
    _load_env_files()
    
    config_dir = get_config_dir()
    
    # Add .yaml extension if not present
    if not filename.endswith((".yaml", ".yml")):
        filename = f"{filename}.yaml"
    
    config_path = config_dir / filename
    
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    
    with open(config_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # Expand environment variables before parsing
    if expand_vars:
        content = expand_env_vars(content)
    
    return yaml.safe_load(content)


def load_yaml_file(file_path: Path, expand_vars: bool = True) -> dict[str, Any]:
    """Load a YAML file from any path with env var expansion.
    
    This is useful for loading config files that aren't in the config directory.
    
    Args:
        file_path: Full path to the YAML file
        expand_vars: If True, expand ${VAR} patterns in the file
        
    Returns:
        Parsed YAML content as dictionary
    """
    # Ensure env files are loaded
    _load_env_files()
    
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    if expand_vars:
        content = expand_env_vars(content)
    
    return yaml.safe_load(content)


def load_vm_config(vm_name: str) -> dict[str, Any]:
    """Load a VM configuration file.
    
    Args:
        vm_name: Name of the VM config file (e.g., 'pfsense')
        
    Returns:
        Parsed VM configuration
    """
    return load_yaml(f"vms/{vm_name}")


def load_proxmox_config() -> dict[str, Any]:
    """Load the main Proxmox connection configuration.
    
    Environment variables override YAML config:
    - PROXMOX_HOST: Proxmox server IP/hostname
    - PROXMOX_PORT: API port (default: 8006)
    - PROXMOX_NODE: Node name (default: pve)
    """
    _load_env_files()
    
    config = load_yaml("proxmox")
    
    # Override with environment variables if set
    if host := os.getenv("PROXMOX_HOST"):
        config["host"] = host
    if port := os.getenv("PROXMOX_PORT"):
        config["port"] = int(port)
    if node := os.getenv("PROXMOX_NODE"):
        config["node"] = node
    
    return config


def load_network_config() -> dict[str, Any]:
    """Load network bridge configuration."""
    return load_yaml("network")


def load_credentials() -> tuple[str, str]:
    """Load Proxmox API credentials from environment.
    
    Returns:
        Tuple of (token_id, token_secret)
        
    Raises:
        ValueError: If credentials are not configured
    """
    _load_env_files()
    
    token_id = os.getenv("PROXMOX_TOKEN_ID")
    token_secret = os.getenv("PROXMOX_TOKEN_SECRET")
    
    if not token_id or not token_secret:
        raise ValueError(
            "Proxmox credentials not configured. "
            "Set PROXMOX_TOKEN_ID and PROXMOX_TOKEN_SECRET in .env file. "
            "See .env.example for format."
        )
    
    return token_id, token_secret


def get_ca_cert_path() -> str | None:
    """Get custom CA certificate path from environment."""
    return os.getenv("PROXMOX_CA_CERT")


def get_default_domain() -> str:
    """Get default domain name from environment.
    
    Returns:
        Domain from PFSENSE_DOMAIN env var, or 'local' if not set
    """
    _load_env_files()
    return os.getenv("PFSENSE_DOMAIN", "local")


def get_lan_subnet() -> str:
    """Get default LAN subnet from environment.
    
    Returns:
        LAN subnet from PFSENSE_LAN_SUBNET env var, or '10.0.0' if not set.
        Format: '10.0.0' (without trailing dot)
    """
    _load_env_files()
    return os.getenv("PFSENSE_LAN_SUBNET", "10.0.0")


def get_cloudflare_credentials() -> tuple[str, str]:
    """Get Cloudflare API credentials from environment.
    
    Returns:
        Tuple of (api_token, zone)
        
    Raises:
        ValueError: If credentials are not configured
    """
    _load_env_files()
    
    api_token = os.getenv("CLOUDFLARE_API_TOKEN")
    zone = os.getenv("CLOUDFLARE_ZONE")
    
    if not api_token:
        raise ValueError(
            "Cloudflare API token not configured. "
            "Set CLOUDFLARE_API_TOKEN in .env file. "
            "Create token at: https://dash.cloudflare.com/profile/api-tokens"
        )
    
    if not zone:
        raise ValueError(
            "Cloudflare zone not configured. "
            "Set CLOUDFLARE_ZONE in .env file (e.g., CLOUDFLARE_ZONE=example.com)"
        )
    
    return api_token, zone
