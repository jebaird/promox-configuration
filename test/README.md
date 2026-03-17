# Proxmox VE 9.1 Test Environment

This directory contains the configuration needed to run a local Proxmox VE 9.1
instance inside Docker for integration testing of the `proxmox-config` CLI tool.

## How It Works

The test environment uses [qemus/qemu-docker](https://github.com/qemus/qemu-docker)
to boot the official Proxmox VE 9.1 ISO inside a QEMU virtual machine running in
a Docker container.  This gives you a **real** Proxmox API endpoint on
`https://localhost:8006` that the CLI tool can interact with.

## Prerequisites

| Requirement | Minimum |
|---|---|
| Docker & Docker Compose | v2.20+ |
| KVM support | `/dev/kvm` available on the host |
| Free RAM | 6 GB (4 GB for QEMU + headroom) |
| Free disk | 40 GB (32 GB virtual disk + ISO cache) |

> **Tip:** On Linux, verify KVM is available with `ls -l /dev/kvm`.  On WSL 2,
> KVM is supported in recent kernels — check with `kvm-ok` or
> `cat /sys/module/kvm/parameters/nested`.

## Quick Start

A wrapper script (`proxmox-test.ps1`) is provided to simplify all test-instance
operations.  It follows the same pattern as `proxmox-config.ps1`.

```bash
# 1. Start the Proxmox VE container (creates test/.env and downloads the ISO on first run)
.\proxmox-test.ps1 start

# 2. Follow the boot progress
.\proxmox-test.ps1 logs

# 3. Open the Proxmox web UI and complete the installer
#    https://localhost:8006

# 4. After installation, create an API token in the web UI:
#    Datacenter → Permissions → API Tokens → Add
#    - User: root@pam
#    - Token ID: test
#    - Privilege Separation: unchecked
#    Copy the secret and paste it into test/.env

# 5. Verify the CLI can connect
.\proxmox-test.ps1 test

# 6. Run any command against the test instance
.\proxmox-test.ps1 network list
.\proxmox-test.ps1 vm list

# 7. Check container status
.\proxmox-test.ps1 status
```

You can also use `docker compose` directly:

```bash
docker compose -f docker-compose.test.yaml up -d proxmox
docker compose -f docker-compose.test.yaml run --rm proxmox-config test
```

## Tearing Down

```bash
# Stop containers but keep persistent data (Proxmox installation on disk)
.\proxmox-test.ps1 stop

# Stop and remove all data (next start will re-install from ISO)
.\proxmox-test.ps1 destroy
```

## Configuration

| File | Purpose |
|---|---|
| `proxmox-test.ps1` | PowerShell wrapper script (start/stop/logs/status + CLI pass-through) |
| `docker-compose.test.yaml` | Compose file defining the QEMU container and proxmox-config service |
| `test/config/proxmox.yaml` | Connection settings pointing to the local container |
| `test/.env` | API token credentials (not committed — see `.env.example`) |

### Customizing Resources

Edit the `proxmox` service environment variables in `docker-compose.test.yaml`:

```yaml
environment:
  RAM_SIZE: "4G"     # Increase for heavier workloads
  CPU_CORES: "2"     # Match your available cores
  DISK_SIZE: "32G"   # Virtual disk size
```

### Running proxmox-config Locally (Outside Docker)

If you prefer running the CLI on your host:

```bash
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
