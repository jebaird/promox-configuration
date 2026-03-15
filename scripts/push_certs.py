"""Push certificates from cert-manager to Proxmox."""
from src.ssh_executor import SSHExecutor

key = open('/app/data/.cert-manager.key').read()
ssh = SSHExecutor(host='10.0.0.5', username='root', key_string=key)
ssh.connect()

print('=== Pushing certificates to Proxmox ===')

# Get domain directory
result = ssh.execute('ls -d /etc/letsencrypt/live/*/ | head -1')
domain_path = result.stdout.strip().rstrip('/')
domain = domain_path.split('/')[-1]
print(f'Domain: {domain}')

# Read certs
result = ssh.execute(f'cat /etc/letsencrypt/live/{domain}/fullchain.pem')
fullchain = result.stdout
print(f'Fullchain: {len(fullchain)} bytes')

result = ssh.execute(f'cat /etc/letsencrypt/live/{domain}/privkey.pem')
privkey = result.stdout
print(f'Private key: {len(privkey)} bytes')

# Push via SCP approach - write to temp file then send
print('Pushing fullchain to Proxmox...')
ssh.execute(f'cat /etc/letsencrypt/live/{domain}/fullchain.pem | ssh -o StrictHostKeyChecking=no root@10.0.0.3 "receive fullchain /etc/pve/local/pveproxy-ssl.pem"')

print('Pushing privkey to Proxmox...')
ssh.execute(f'cat /etc/letsencrypt/live/{domain}/privkey.pem | ssh -o StrictHostKeyChecking=no root@10.0.0.3 "receive key /etc/pve/local/pveproxy-ssl.key"')

print('Triggering reload...')
result = ssh.execute('ssh -o StrictHostKeyChecking=no root@10.0.0.3 reload')
print(result.stdout)

print('Done!')
