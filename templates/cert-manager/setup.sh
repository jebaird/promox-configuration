#!/bin/bash
# Certificate Manager Setup Script
# =================================
# This script is run inside the LXC container after creation.
# It installs certbot, configures Cloudflare DNS challenge,
# and sets up automatic certificate renewal and distribution.

set -e  # Exit on error

# Configuration (injected by deployment script)
DOMAIN="{{DOMAIN}}"
CLOUDFLARE_API_TOKEN="{{CLOUDFLARE_API_TOKEN}}"
CERT_EMAIL="{{CERT_EMAIL}}"
STAGING="{{STAGING}}"

echo "=== Certificate Manager Setup ==="
echo "Domain: $DOMAIN"
echo "Staging: $STAGING"
echo ""

# Update system
echo ">>> Updating system packages..."
apt-get update
apt-get upgrade -y

# Install required packages
echo ">>> Installing certbot and dependencies..."
apt-get install -y \
    certbot \
    python3-certbot-dns-cloudflare \
    openssh-client \
    rsync \
    curl \
    jq

# Create directories
echo ">>> Creating directories..."
mkdir -p /etc/letsencrypt
mkdir -p /etc/cert-manager
mkdir -p /root/.ssh

# Generate SSH keypair for cert distribution
if [ ! -f /root/.ssh/id_ed25519 ]; then
    echo ">>> Generating SSH keypair..."
    ssh-keygen -t ed25519 -f /root/.ssh/id_ed25519 -N "" -C "cert-manager@$(hostname)"
fi

# Configure Cloudflare credentials
echo ">>> Configuring Cloudflare credentials..."
cat > /etc/letsencrypt/cloudflare.ini << EOF
# Cloudflare API credentials for DNS challenge
dns_cloudflare_api_token = $CLOUDFLARE_API_TOKEN
EOF
chmod 600 /etc/letsencrypt/cloudflare.ini

# Copy deploy hook script
echo ">>> Installing deploy hook..."
cp /tmp/deploy-hook.sh /etc/letsencrypt/renewal-hooks/deploy/distribute-certs.sh
chmod +x /etc/letsencrypt/renewal-hooks/deploy/distribute-certs.sh

# Copy targets configuration
if [ -f /tmp/targets.yaml ]; then
    cp /tmp/targets.yaml /etc/cert-manager/targets.yaml
fi

# Build certbot command
CERTBOT_CMD="certbot certonly --dns-cloudflare"
CERTBOT_CMD="$CERTBOT_CMD --dns-cloudflare-credentials /etc/letsencrypt/cloudflare.ini"
CERTBOT_CMD="$CERTBOT_CMD --dns-cloudflare-propagation-seconds 30"
CERTBOT_CMD="$CERTBOT_CMD -d \"*.$DOMAIN\" -d \"$DOMAIN\""
CERTBOT_CMD="$CERTBOT_CMD --non-interactive --agree-tos"

if [ -n "$CERT_EMAIL" ]; then
    CERTBOT_CMD="$CERTBOT_CMD --email $CERT_EMAIL"
else
    CERTBOT_CMD="$CERTBOT_CMD --register-unsafely-without-email"
fi

if [ "$STAGING" = "true" ]; then
    CERTBOT_CMD="$CERTBOT_CMD --staging"
    echo ">>> Using Let's Encrypt STAGING environment (for testing)"
fi

# Request initial certificate
echo ">>> Requesting initial certificate..."
echo "Running: $CERTBOT_CMD"
eval $CERTBOT_CMD

# Set up systemd timer for renewal (certbot installs this by default on Debian)
echo ">>> Enabling certbot renewal timer..."
systemctl enable certbot.timer
systemctl start certbot.timer

# Show certificate info
echo ""
echo "=== Setup Complete ==="
echo ""
certbot certificates
echo ""
echo "=== SSH Public Key ==="
echo "Add this key to each target host's /root/.ssh/authorized_keys:"
echo ""
cat /root/.ssh/id_ed25519.pub
echo ""
echo "=== Next Steps ==="
echo "1. Add the SSH public key above to each target host"
echo "2. Test certificate distribution: /etc/letsencrypt/renewal-hooks/deploy/distribute-certs.sh"
echo "3. Certificates will auto-renew and distribute every 12 hours"
