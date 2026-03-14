"""Configuration loading utilities."""

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


def get_config_dir() -> Path:
    """Get the configuration directory path."""
    return Path(__file__).parent.parent / "config"


def load_yaml(filename: str) -> dict[str, Any]:
    """Load a YAML configuration file.
    
    Args:
        filename: Name of the YAML file (with or without .yaml extension)
        
    Returns:
        Parsed YAML content as dictionary
        
    Raises:
        FileNotFoundError: If config file doesn't exist
        yaml.YAMLError: If YAML parsing fails
    """
    config_dir = get_config_dir()
    
    # Add .yaml extension if not present
    if not filename.endswith((".yaml", ".yml")):
        filename = f"{filename}.yaml"
    
    config_path = config_dir / filename
    
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


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
    # Load .env file if it exists
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    
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
    # Load .env file if it exists
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    
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
