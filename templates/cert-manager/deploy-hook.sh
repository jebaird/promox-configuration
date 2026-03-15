#!/bin/bash
# Certificate Distribution Hook
# ==============================
# This script runs after certbot renews certificates.
# It distributes the new certificates to all configured targets.
#
# Works with restricted SSH access via cert-receive.sh on targets.

set -e

# Configuration
TARGETS_FILE="/etc/cert-manager/targets.yaml"
CERT_DIR="/etc/letsencrypt/live"
LOG_FILE="/var/log/cert-distribution.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Find the domain directory (first one in live/)
DOMAIN_DIR=$(ls -1 "$CERT_DIR" 2>/dev/null | head -1)
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

# Distribute to a single target using restricted SSH
distribute_to_target() {
    local name="$1"
    local host="$2"
    local user="$3"
    local cert_dest="$4"
    local key_dest="$5"
    local fullchain_dest="$6"
    
    log "--- Distributing to $name ($host) ---"
    
    local ssh_opts="-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=no"
    local success=true
    
    # Test connection using restricted 'test' command
    if ! ssh $ssh_opts "$user@$host" "test" 2>/dev/null | grep -q "OK"; then
        log "ERROR: Cannot connect to $user@$host (restricted SSH) - skipping"
        return 1
    fi
    
    # Send certificate
    if [ -n "$cert_dest" ] && [ -f "$CERT_PATH" ]; then
        log "Sending cert to $cert_dest"
        if ssh $ssh_opts "$user@$host" "receive cert $cert_dest" < "$CERT_PATH" 2>&1 | tee -a "$LOG_FILE" | grep -q "^OK:"; then
            log "Cert delivered successfully"
        else
            log "ERROR: Failed to send cert"
            success=false
        fi
    fi
    
    # Send private key
    if [ -n "$key_dest" ] && [ -f "$KEY_PATH" ]; then
        log "Sending key to $key_dest"
        if ssh $ssh_opts "$user@$host" "receive key $key_dest" < "$KEY_PATH" 2>&1 | tee -a "$LOG_FILE" | grep -q "^OK:"; then
            log "Key delivered successfully"
        else
            log "ERROR: Failed to send key"
            success=false
        fi
    fi
    
    # Send fullchain
    if [ -n "$fullchain_dest" ] && [ -f "$FULLCHAIN_PATH" ]; then
        log "Sending fullchain to $fullchain_dest"
        if ssh $ssh_opts "$user@$host" "receive fullchain $fullchain_dest" < "$FULLCHAIN_PATH" 2>&1 | tee -a "$LOG_FILE" | grep -q "^OK:"; then
            log "Fullchain delivered successfully"
        else
            log "ERROR: Failed to send fullchain"
            success=false
        fi
    fi
    
    # Trigger reload on target
    log "Triggering reload on $name"
    if ssh $ssh_opts "$user@$host" "reload" 2>&1 | tee -a "$LOG_FILE" | grep -q "^OK:"; then
        log "Reload successful"
    else
        log "WARNING: Reload may have failed (check target logs)"
    fi
    
    if $success; then
        log "Completed: $name"
        return 0
    else
        log "Completed with errors: $name"
        return 1
    fi
}

# Parse targets.yaml and distribute
if command -v python3 &> /dev/null; then
    # Parse targets.yaml with Python
    python3 << 'PYEOF'
import yaml
import subprocess
import os

with open('/etc/cert-manager/targets.yaml', 'r') as f:
    config = yaml.safe_load(f)

targets = config.get('targets', {})
failed = []

for name, target in targets.items():
    if not target.get('enabled', True):
        print(f"Skipping {name}: disabled")
        continue
        
    host = target.get('host', '')
    user = target.get('user', 'root')
    cert_path = target.get('cert_path', '')
    key_path = target.get('key_path', '')
    fullchain_path = target.get('fullchain_path', '')
    
    if not host:
        print(f"Skipping {name}: no host configured")
        continue
    
    print(f"Distributing to {name}...")
    
    # Build and run the distribute command
    cmd = f'distribute_to_target "{name}" "{host}" "{user}" "{cert_path}" "{key_path}" "{fullchain_path}"'
    
    # Source this script and run the function
    script_path = os.environ.get('BASH_SOURCE', '/etc/letsencrypt/renewal-hooks/deploy/distribute-certs.sh')
    result = subprocess.run(
        ['bash', '-c', f'source "{script_path}" 2>/dev/null; {cmd}'],
        capture_output=False
    )
    
    if result.returncode != 0:
        failed.append(name)

if failed:
    print(f"WARNING: Distribution failed for: {', '.join(failed)}")
else:
    print("All targets updated successfully")
PYEOF
else
    log "ERROR: Python3 not available for YAML parsing"
    log "Install python3 or edit this script with hardcoded targets"
    exit 1
fi

log "=== Certificate distribution complete ==="
