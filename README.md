# Proxmox Configuration Tool

A Python CLI tool for configuring Proxmox VE via REST API. Designed for home lab infrastructure automation.

## Features

- **Declarative configuration** - Define VMs and networks in YAML files
- **Network management** - Create and manage network bridges
- **ISO management** - Download and upload installation ISOs (pfSense, etc.)
- **VM deployment** - Create VMs with full configuration
- **Idempotent operations** - Safe to run multiple times

## Quick Start

### Prerequisites

- Docker & Docker Compose (recommended), OR Python 3.10+
- Proxmox VE 7.x or 8.x
- Proxmox API token with appropriate permissions

### Installation (Local Python)

```bash
# Clone the repository
cd d:\repos\promox-configuration

# Create virtual environment
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/Mac

# Install dependencies
pip install -e .
```

### Docker (Recommended)

```bash
# Configure credentials
copy .env.example .env
# Edit .env with your token details

# Run commands using wrapper script (auto-builds image)
.\proxmox-config.ps1 test
.\proxmox-config.ps1 deploy pfsense --dry-run
.\proxmox-config.ps1 network list
.\proxmox-config.ps1 vm list

# Or run directly with docker compose
docker compose build
docker compose run --rm proxmox-config test
```

### Configuration

1. **Create API Token** in Proxmox:
   - Go to Datacenter → Permissions → API Tokens
   - Add token for your user (e.g., `root@pam`)
   - Note the Token ID and Secret

2. **Configure credentials**:
   ```bash
   copy .env.example .env
   # Edit .env with your token details
   ```

3. **Verify connection**:
   ```bash
   proxmox-config test
   # Or with Docker:
   docker compose run --rm proxmox-config test
   ```

## Usage

### Deploy pfSense Router

The quickest way to deploy pfSense:

```bash
# Dry run to see what will happen
proxmox-config deploy pfsense --dry-run

# Full deployment
proxmox-config deploy pfsense
```

This will:
1. Test Proxmox connection
2. Create LAN bridge (vmbr1) if needed
3. Download pfSense ISO
4. Create VM with configured resources

### Individual Commands

#### Network Management

```bash
# List network interfaces
proxmox-config network list

# Setup bridges from config
proxmox-config network setup --dry-run
proxmox-config network setup --apply

# Apply/revert pending changes
proxmox-config network apply
proxmox-config network revert
```

#### ISO Management

```bash
# List ISOs on storage
proxmox-config iso list

# Download pfSense ISO
proxmox-config iso download-pfsense --version 2.7.2
```

#### VM Management

```bash
# List VMs
proxmox-config vm list

# Create VM from config
proxmox-config vm create pfsense --dry-run
proxmox-config vm create pfsense

# Show VM details
proxmox-config vm info 100

# Delete VM
proxmox-config vm delete 100 --yes
```

## Configuration Files

### `config/proxmox.yaml`

Proxmox connection settings:

```yaml
host: "10.0.0.3"
port: 8006
node: "pve"
verify_ssl: false
```

### `config/network.yaml`

Network bridge definitions:

```yaml
bridges:
  wan:
    name: "vmbr0"
    exists: true
  lan:
    name: "vmbr1"
    exists: false
    physical_interface: "enp2s0"  # Optional: bind to physical NIC
```

### `config/vms/pfsense.yaml`

VM definition:

```yaml
vm:
  vmid: 100
  name: "pfsense"
  resources:
    cores: 2
    memory: 4096  # MB

storage:
  disk:
    storage: "local-lvm"
    size: "32G"

network:
  - interface: "net0"
    bridge: "vmbr0"
  - interface: "net1"
    bridge: "vmbr1"

iso:
  version: "2.7.2"
  storage: "local"
```

## Post-Deployment (pfSense)

After VM creation, complete setup via Proxmox console:

1. Start the VM
2. Boot from ISO and run installer
3. Accept defaults or customize partitioning
4. Reboot and remove ISO
5. Configure WAN interface (vtnet0 → DHCP or static)
6. Configure LAN interface (vtnet1 → e.g., 10.0.0.1/24)
7. Access web GUI at `https://<LAN_IP>` (default: admin/pfsense)

## Project Structure

```
promox-configuration/
├── config/
│   ├── proxmox.yaml      # Proxmox connection
│   ├── network.yaml      # Bridge definitions
│   └── vms/
│       └── pfsense.yaml  # pfSense VM config
├── src/
│   ├── __init__.py
│   ├── config.py         # Config loading
│   ├── proxmox_client.py # API client
│   ├── network.py        # Bridge management
│   ├── iso_manager.py    # ISO download/upload
│   ├── vm_creator.py     # VM creation
│   └── main.py           # CLI entry point
├── .env.example          # Credentials template
├── .gitignore
├── docker-compose.yaml   # Docker Compose config
├── Dockerfile            # Container image
├── proxmox-config.ps1    # PowerShell wrapper script
├── pyproject.toml        # Dependencies
└── README.md
```

## Adding New VMs

1. Create config file in `config/vms/<name>.yaml`
2. Define VM specs, storage, and network
3. Run `proxmox-config deploy <name>`

## Troubleshooting

### Connection refused
- Verify Proxmox host IP and port
- Check API token permissions
- Ensure Proxmox web interface is accessible

### SSL certificate errors
- Set `verify_ssl: false` in `config/proxmox.yaml`
- Or provide CA cert path via `PROXMOX_CA_CERT` env var

### Bridge creation fails
- Ensure physical interface name is correct (run `ip link` on Proxmox)
- Check if interface is already in use

### ISO download fails
- Verify internet connectivity
- Check pfSense version exists at mirror

## License

MIT
