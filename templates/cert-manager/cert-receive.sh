#!/bin/bash
# Certificate Receiver Script
# ===========================
# Restricted script for cert-manager SSH key access.
# This script is the ONLY thing the cert-manager key can execute.
#
# Install location: /usr/local/bin/cert-receive.sh
# Used with authorized_keys command= restriction.
#
# Usage (from cert-manager):
#   ssh target "receive cert /path/to/cert.pem" < cert_content
#   ssh target "receive key /path/to/key.pem" < key_content
#   ssh target "reload"
#   ssh target "test"

set -e

# Configuration - these are set during deployment by setup-cert-targets
ALLOWED_CERT_PATHS="{{ALLOWED_CERT_PATHS}}"
ALLOWED_KEY_PATHS="{{ALLOWED_KEY_PATHS}}"
RELOAD_CMD="{{RELOAD_CMD}}"

LOG_TAG="cert-receive"

log() {
    logger -t "$LOG_TAG" "$1"
    echo "$1" >&2
}

log_error() {
    logger -t "$LOG_TAG" -p user.err "ERROR: $1"
    echo "ERROR: $1" >&2
}

# Validate path is in allowed list
path_allowed() {
    local path="$1"
    local allowed_paths="$2"
    
    for allowed in $allowed_paths; do
        if [ "$path" = "$allowed" ]; then
            return 0
        fi
    done
    return 1
}

# Validate PEM format (basic check)
validate_pem() {
    local content="$1"
    if echo "$content" | grep -q "^-----BEGIN"; then
        return 0
    fi
    return 1
}

# Handle receive command
do_receive() {
    local type="$1"
    local dest_path="$2"
    
    if [ -z "$dest_path" ]; then
        log_error "No destination path specified"
        exit 1
    fi
    
    # Validate path based on type
    case "$type" in
        cert|fullchain)
            if ! path_allowed "$dest_path" "$ALLOWED_CERT_PATHS"; then
                log_error "Path not allowed for cert: $dest_path"
                exit 1
            fi
            ;;
        key)
            if ! path_allowed "$dest_path" "$ALLOWED_KEY_PATHS"; then
                log_error "Path not allowed for key: $dest_path"
                exit 1
            fi
            ;;
        *)
            log_error "Unknown type: $type"
            exit 1
            ;;
    esac
    
    # Read content from stdin
    local content
    content=$(cat)
    
    # Validate PEM format
    if ! validate_pem "$content"; then
        log_error "Invalid PEM format for $type"
        exit 1
    fi
    
    # Create parent directory if needed
    local parent_dir
    parent_dir=$(dirname "$dest_path")
    if [ ! -d "$parent_dir" ]; then
        mkdir -p "$parent_dir"
        log "Created directory: $parent_dir"
    fi
    
    # Write file with appropriate permissions
    if [ "$type" = "key" ]; then
        # Private key - restrictive permissions
        umask 077
        echo "$content" > "$dest_path"
        chmod 600 "$dest_path"
    else
        # Cert/fullchain - readable
        echo "$content" > "$dest_path"
        chmod 644 "$dest_path"
    fi
    
    log "Received $type -> $dest_path ($(echo "$content" | wc -c) bytes)"
    echo "OK: $type written to $dest_path"
}

# Handle reload command
do_reload() {
    if [ -z "$RELOAD_CMD" ]; then
        log "No reload command configured"
        echo "OK: No reload command configured"
        return 0
    fi
    
    log "Running reload command: $RELOAD_CMD"
    if eval "$RELOAD_CMD" 2>&1; then
        log "Reload successful"
        echo "OK: Reload completed"
    else
        log_error "Reload failed"
        echo "ERROR: Reload failed"
        exit 1
    fi
}

# Handle test command (for verification)
do_test() {
    echo "OK: cert-receive.sh is working"
    echo "Allowed cert paths: $ALLOWED_CERT_PATHS"
    echo "Allowed key paths: $ALLOWED_KEY_PATHS"
    echo "Reload command: $RELOAD_CMD"
    log "Test command executed"
}

# Main - parse SSH_ORIGINAL_COMMAND or arguments
if [ -n "$SSH_ORIGINAL_COMMAND" ]; then
    # Running via SSH with command= restriction
    set -- $SSH_ORIGINAL_COMMAND
fi

cmd="$1"
shift || true

case "$cmd" in
    receive)
        do_receive "$@"
        ;;
    reload)
        do_reload
        ;;
    test)
        do_test
        ;;
    *)
        log_error "Unknown command: $cmd"
        echo "Usage: receive {cert|key|fullchain} <path>"
        echo "       reload"
        echo "       test"
        exit 1
        ;;
esac
