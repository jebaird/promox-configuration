#!/bin/bash
# Certificate Distribution Hook
# ==============================
# This script runs after certbot renews certificates.
# It distributes the new certificates to all configured targets.

set -e

# Load configuration
TARGETS_FILE="/etc/cert-manager/targets.yaml"
CERT_DIR="/etc/letsencrypt/live"
LOG_FILE="/var/log/cert-distribution.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Find the domain directory (first one in live/)
DOMAIN_DIR=$(ls -1 "$CERT_DIR" | head -1)
if [ -z "$DOMAIN_DIR" ]; then
    log "ERROR: No certificate directory found in $CERT_DIR"
    exit 1
fi

CERT_PATH="$CERT_DIR/$DOMAIN_DIR/cert.pem"
KEY_PATH="$CERT_DIR/$DOMAIN_DIR/privkey.pem"
CHAIN_PATH="$CERT_DIR/$DOMAIN_DIR/chain.pem"
FULLCHAIN_PATH="$CERT_DIR/$DOMAIN_DIR/fullchain.pem"

log "=== Starting certificate distribution ==="
log "Domain: $DOMAIN_DIR"
log "Cert: $CERT_PATH"
log "Key: $KEY_PATH"
log "Fullchain: $FULLCHAIN_PATH"

# Check if targets file exists
if [ ! -f "$TARGETS_FILE" ]; then
    log "WARNING: No targets file found at $TARGETS_FILE"
    log "Skipping distribution"
    exit 0
fi

# Parse YAML and distribute to each target
# Using a simple approach since we can't guarantee yq is installed

distribute_to_target() {
    local name="$1"
    local host="$2"
    local user="$3"
    local cert_dest="$4"
    local key_dest="$5"
    local fullchain_dest="$6"
    local reload_cmd="$7"
    
    log "--- Distributing to $name ($host) ---"
    
    # Test SSH connection first
    if ! ssh -o BatchMode=yes -o ConnectTimeout=5 "$user@$host" "echo ok" > /dev/null 2>&1; then
        log "ERROR: Cannot connect to $user@$host - skipping"
        return 1
    fi
    
    # Copy certificate
    if [ -n "$cert_dest" ]; then
        log "Copying cert to $cert_dest"
        scp -q "$CERT_PATH" "$user@$host:$cert_dest"
    fi
    
    # Copy private key
    if [ -n "$key_dest" ]; then
        log "Copying key to $key_dest"
        scp -q "$KEY_PATH" "$user@$host:$key_dest"
    fi
    
    # Copy fullchain
    if [ -n "$fullchain_dest" ]; then
        log "Copying fullchain to $fullchain_dest"
        scp -q "$FULLCHAIN_PATH" "$user@$host:$fullchain_dest"
    fi
    
    # Reload service
    if [ -n "$reload_cmd" ]; then
        log "Running reload command: $reload_cmd"
        ssh "$user@$host" "$reload_cmd" 2>&1 | tee -a "$LOG_FILE" || true
    fi
    
    log "Completed: $name"
    return 0
}

# Distribution targets (edit these or use targets.yaml)
# Format: distribute_to_target "name" "host" "user" "cert_path" "key_path" "fullchain_path" "reload_cmd"

# Check for Python/yq to parse YAML, otherwise use hardcoded defaults
if command -v python3 &> /dev/null; then
    # Parse targets.yaml with Python
    python3 << 'PYEOF'
import yaml
import subprocess
import sys

with open('/etc/cert-manager/targets.yaml', 'r') as f:
    config = yaml.safe_load(f)

targets = config.get('targets', {})
for name, target in targets.items():
    if not target.get('enabled', True):
        continue
        
    host = target.get('host', '')
    user = target.get('user', 'root')
    cert_path = target.get('cert_path', '')
    key_path = target.get('key_path', '')
    fullchain_path = target.get('fullchain_path', '')
    reload_cmd = target.get('reload_cmd', '')
    
    if not host:
        print(f"Skipping {name}: no host configured")
        continue
    
    # Call the distribute function via bash
    cmd = f'distribute_to_target "{name}" "{host}" "{user}" "{cert_path}" "{key_path}" "{fullchain_path}" "{reload_cmd}"'
    print(f"Distributing to {name}...")
    
    # Export function and call it
    result = subprocess.run(['bash', '-c', f'source /etc/letsencrypt/renewal-hooks/deploy/distribute-certs.sh && {cmd}'], 
                          capture_output=False)
PYEOF
else
    # Fallback: Use hardcoded targets if YAML parsing not available
    log "Python not available, using hardcoded targets"
    
    # Example targets - edit these if not using targets.yaml
    # distribute_to_target "pfsense" "10.0.0.1" "root" "/etc/ssl/cert.pem" "/etc/ssl/key.pem" "/etc/ssl/fullchain.pem" "/etc/rc.restart_webgui"
    # distribute_to_target "proxmox" "10.0.0.3" "root" "/etc/pve/local/pveproxy-ssl.pem" "/etc/pve/local/pveproxy-ssl.key" "" "systemctl restart pveproxy"
fi

log "=== Certificate distribution complete ==="
