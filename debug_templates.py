#!/usr/bin/env python3
"""Debug script to check available templates."""
from src.proxmox_client import ProxmoxClient

client = ProxmoxClient()

# List available templates from Proxmox repos
templates = client.api.nodes(client.node).aplinfo.get()

print("=== Available Debian templates ===")
debian_templates = [t for t in templates if 'debian' in t.get('template', '').lower()]
for t in debian_templates[:10]:
    print(f"Template: {t.get('template')}")
    print(f"  Package: {t.get('package')}")
    print(f"  Section: {t.get('section')}")
    print()

# Check existing templates in storage
print("=== Templates in local storage ===")
existing = client.get_storage_content("local", content_type="vztmpl")
for t in existing:
    print(f"  {t.get('volid')}")
