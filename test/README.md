# Proxmox VE 9.1 Test Environment

This directory contains the configuration needed to run a local Proxmox VE 9.1
instance inside Docker for integration testing of the `proxmox-config` CLI tool.

## How It Works

The test environment uses [qemux/qemu](https://github.com/qemus/qemu-docker)
to boot the official Proxmox VE 9.1 ISO inside a QEMU virtual machine running in
a Docker container.  This gives you a **real** Proxmox API endpoint on
`https://localhost:8006` that the CLI tool can interact with.

## Prerequisites

| Requirement | Minimum |
|---|---|
| Docker & Docker Compose | v2.20+ |
| KVM support | `/dev/kvm` available on the host |
| Free RAM | 16 GB (test VM uses ~16 GB) |
| Free disk | 130 GB (128 GB virtual disk + ISO cache) |

> **Tip:** On Linux, verify KVM is available with `ls -l /dev/kvm`.  On WSL 2,
> KVM is supported in recent kernels — check with `kvm-ok` or
> `cat /sys/module/kvm/parameters/nested`.

## Quick Start

### First Time Setup (Manual Installation)

```powershell
# 1. Start the Proxmox VE container (downloads ISO on first run)
.\proxmox-test.ps1 start

# 2. Open the noVNC web viewer and complete installation
#    http://localhost:8006
#    - Set root password (remember it!)
#    - Accept defaults for network, disk, etc.
#    - Wait for installation to complete and reboot

# 3. Wait for container to become healthy
.\proxmox-test.ps1 status

# 4. Run the setup script to create API token
.\proxmox-test.ps1 setup
#    Enter the root password you set during installation

# 5. Verify the CLI can connect
.\proxmox-test.ps1 test

# 6. (Optional) Save a snapshot for fast restore later
.\proxmox-test.ps1 save-snapshot
```

### Subsequent Runs (Fast Restore)

If you've saved a snapshot, skip installation entirely:

```powershell
# Restore from snapshot (instant, no installation needed)
.\proxmox-test.ps1 restore-snapshot

# Start the instance
.\proxmox-test.ps1 start

# Wait for healthy, then test
.\proxmox-test.ps1 status
.\proxmox-test.ps1 test
```

## Commands Reference

| Command | Description |
|---------|-------------|
| `start` | Start the Proxmox VE container |
| `stop` | Stop container (keeps data) |
| `destroy` | Stop and remove all data |
| `status` | Show container health status |
| `logs` | Follow Proxmox boot/runtime logs |
| `setup` | Create API token (run after installation) |
| `save-snapshot` | Export current state to `test/proxmox-snapshot.tar.gz` |
| `restore-snapshot` | Restore from saved snapshot |
| `test` | Test API connection |
| `ports` | List all configured port forwards |
| `forward` | Set up iptables port forwarding to LXC containers |
| `<any>` | Pass through to proxmox-config CLI |

## Accessing Services

Services running inside Proxmox LXC containers (Grafana, Prometheus, etc.) are not
directly accessible from Windows because they're on an internal Docker network.

### Port Forwarding with iptables

The `forward` command sets up iptables rules inside Proxmox to forward ports:

```powershell
# View configured ports
.\proxmox-test.ps1 ports

# Set up port forwarding (one-time, persists until Proxmox reboots)
.\proxmox-test.ps1 forward
```

Once configured, access services at:
- **Grafana**: http://localhost:3000
- **Prometheus**: http://localhost:9090

### Configuring Ports

Edit `test/ports.yaml` to add services:

```yaml
my_service:
  ip: "172.30.0.20"      # IP inside Proxmox
  port: 8080             # Service port
  local_port: 8080       # Port on Windows (optional, defaults to port)
  description: "My app"
```

### Storing Root Password

To avoid entering the password each time, add to `test/.env`:

```
PROXMOX_ROOT_PASSWORD=your_password
```

## Configuration

| File | Purpose |
|---|---|
| `proxmox-test.ps1` | PowerShell wrapper script |
| `docker-compose.test.yaml` | Compose file with QEMU container and proxmox-config service |
| `test/config/proxmox.yaml` | Connection settings (PROXMOX_HOST overrides in env) |
| `test/.env` | Test-specific overrides (Proxmox host/tokens) |
| `.env` | Base credentials (Cloudflare, domain settings) |
| `test/proxmox-snapshot.tar.gz` | Saved state for fast restore (not committed) |

### Environment Variable Layering

The test environment uses layered env files:

1. **`.env`** (base) — Cloudflare credentials, domain settings, etc.
2. **`test/.env`** (override) — Proxmox host, API tokens for test instance

This mirrors production while allowing test-specific connection settings.

### Customizing Resources

Edit the `proxmox` service environment variables in `docker-compose.test.yaml`:

```yaml
environment:
  RAM_SIZE: "16G"    # Simulates Dell Optiplex with 32GB
  CPU_CORES: "4"     # 4 cores - typical i5/i7 from 2015
  DISK_SIZE: "128G"  # Room for full VM stack
```

## Tearing Down

```powershell
# Stop containers but keep persistent data
.\proxmox-test.ps1 stop

# Stop and remove all data (next start will re-install from ISO)
.\proxmox-test.ps1 destroy
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Container fails to start | Verify KVM: `ls -l /dev/kvm` |
| Web UI not loading | Wait 3-5 min for boot; check `.\proxmox-test.ps1 logs` |
| API connection fails | Run `.\proxmox-test.ps1 setup` to create token |
| Health check failing | Container needs ~2 min after boot to become healthy |
| Slow performance | Increase `RAM_SIZE`/`CPU_CORES` in docker-compose.test.yaml |
# Point the CLI at the local container
export PROXMOX_HOST=localhost
export PROXMOX_TOKEN_ID=root@pam!test
export PROXMOX_TOKEN_SECRET=<your-secret>

proxmox-config test
```

## Troubleshooting

### `/dev/kvm` not found
KVM is required for acceptable performance.  Ensure your host kernel supports
hardware virtualisation (Intel VT-x / AMD-V) and that the `kvm` modules are
loaded (`modprobe kvm_intel` or `modprobe kvm_amd`).

### Proxmox UI not reachable after start
The ISO download and initial QEMU boot can take several minutes.  Watch the logs
with `docker compose -f docker-compose.test.yaml logs -f proxmox` and wait for
the installer screen to appear.

### Health check keeps failing
The `start_period` is set to 5 minutes to allow time for the ISO download and
boot.  If your connection is slow, increase `start_period` in
`docker-compose.test.yaml`.

### Connection refused from proxmox-config container
Ensure the `proxmox-config` service uses the hostname `proxmox` (the Docker
Compose service name) as the host — this is already configured in
`test/config/proxmox.yaml`.
