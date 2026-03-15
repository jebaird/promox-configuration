#!/usr/bin/env python3
"""Verify cert on Proxmox."""
import sys
sys.path.insert(0, '/app/src')

from ssh_executor import SSHExecutor

# Connect to cert-manager
key = open('/app/data/.cert-manager.key').read()
ssh = SSHExecutor(host='10.0.0.5', username='root', key_string=key)
ssh.connect()

# Check cert on Proxmox via restricted SSH
cmd = "echo 'test' | ssh -o StrictHostKeyChecking=no root@10.0.0.3 'cat > /dev/null; openssl x509 -in /etc/pve/local/pveproxy-ssl.pem -noout -subject -dates -issuer'"
result = ssh.execute(cmd)
print("Certificate info on Proxmox:")
print(result.stdout)
if result.stderr:
    print("Errors:", result.stderr)
