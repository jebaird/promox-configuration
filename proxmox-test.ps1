<#
.SYNOPSIS
    Wrapper script for running proxmox-config commands against the test Proxmox VE 9.1 instance.

.DESCRIPTION
    This script simplifies running proxmox-config CLI commands against the local
    Proxmox VE 9.1 test container (QEMU-in-Docker).
    Use the 'start' and 'stop' subcommands to manage the test instance, or pass
    any other arguments through to the proxmox-config CLI.

.EXAMPLE
    .\proxmox-test.ps1 start
    Starts the Proxmox VE 9.1 test container.

.EXAMPLE
    .\proxmox-test.ps1 stop
    Stops the test container but keeps persistent data.

.EXAMPLE
    .\proxmox-test.ps1 destroy
    Stops the test container and removes all persistent data.

.EXAMPLE
    .\proxmox-test.ps1 setup
    Creates API token after Proxmox installation (requires root password).

.EXAMPLE
    .\proxmox-test.ps1 save-snapshot
    Saves the current Proxmox state to a snapshot for fast restore.

.EXAMPLE
    .\proxmox-test.ps1 restore-snapshot
    Restores Proxmox from a saved snapshot (skips installation).

.EXAMPLE
    .\proxmox-test.ps1 forward
    Sets up iptables port forwarding to LXC containers (Grafana, Prometheus, etc.).

.EXAMPLE
    .\proxmox-test.ps1 ports
    Lists all configured port forwards and their status.

.EXAMPLE
    .\proxmox-test.ps1 logs
    Follows the Proxmox container logs.

.EXAMPLE
    .\proxmox-test.ps1 status
    Shows the status of test containers.

.EXAMPLE
    .\proxmox-test.ps1 test
    Tests connection to the test Proxmox API.

.EXAMPLE
    .\proxmox-test.ps1 network list
    Lists network interfaces on the test Proxmox instance.

.EXAMPLE
    .\proxmox-test.ps1 vm list
    Lists all VMs on the test Proxmox instance.

.EXAMPLE
    .\proxmox-test.ps1 --help
    Shows available proxmox-config commands.
#>

param(
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ErrorActionPreference = "Stop"
$ComposeFile = "docker-compose.test.yaml"
$SnapshotFile = "test/proxmox-snapshot.tar.gz"
$VolumeName = "proxmox-test-storage"

# Change to script directory
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $ScriptDir

try {
    # Ensure test/.env exists
    if (-not (Test-Path "test/.env")) {
        if (Test-Path "test/.env.example") {
            Write-Host "Creating test/.env from test/.env.example..." -ForegroundColor Cyan
            Copy-Item "test/.env.example" "test/.env"
            Write-Host "Edit test/.env with your API token after completing the Proxmox installer." -ForegroundColor Yellow
            Write-Host ""
        }
        else {
            Write-Host "Warning: test/.env not found. Copy test/.env.example to test/.env and configure credentials." -ForegroundColor Yellow
            Write-Host ""
        }
    }

    # Handle subcommands for managing the test environment
    $Subcommand = if ($Arguments.Count -gt 0) { $Arguments[0] } else { "" }

    switch ($Subcommand) {
        "start" {
            Write-Host "Starting Proxmox VE 9.1 test instance..." -ForegroundColor Cyan

            # Build the proxmox-config image if needed
            $ImageExists = docker images -q proxmox-config:latest 2>$null
            if (-not $ImageExists) {
                Write-Host "Building proxmox-config image..." -ForegroundColor Cyan
                docker compose -f $ComposeFile build
                if ($LASTEXITCODE -ne 0) { throw "Failed to build Docker image" }
            }

            docker compose -f $ComposeFile up -d proxmox
            if ($LASTEXITCODE -ne 0) { throw "Failed to start Proxmox container" }

            Write-Host ""
            Write-Host "Proxmox VE 9.1 is starting. This may take several minutes." -ForegroundColor Green
            Write-Host "  Web UI:  https://localhost:8006" -ForegroundColor Cyan
            Write-Host "  Logs:    .\proxmox-test.ps1 logs" -ForegroundColor Cyan
            Write-Host "  Status:  .\proxmox-test.ps1 status" -ForegroundColor Cyan
            Write-Host ""
            Write-Host "After installation completes, run: .\proxmox-test.ps1 setup" -ForegroundColor Yellow
        }

        "stop" {
            Write-Host "Stopping test instance (keeping data)..." -ForegroundColor Cyan
            docker compose -f $ComposeFile down
            exit $LASTEXITCODE
        }

        "destroy" {
            Write-Host "Stopping test instance and removing all data..." -ForegroundColor Yellow
            docker compose -f $ComposeFile down -v
            exit $LASTEXITCODE
        }

        "logs" {
            docker compose -f $ComposeFile logs -f proxmox
            exit $LASTEXITCODE
        }

        "status" {
            docker compose -f $ComposeFile ps
            exit $LASTEXITCODE
        }

        "setup" {
            # Create API token after Proxmox installation
            Write-Host "Setting up API token for test instance..." -ForegroundColor Cyan
            Write-Host ""
            
            # Check if container is running and healthy
            $Health = docker inspect proxmox-test --format '{{.State.Health.Status}}' 2>$null
            if ($Health -ne "healthy") {
                Write-Host "Error: Proxmox container is not healthy (status: $Health)" -ForegroundColor Red
                Write-Host "Wait for installation to complete and container to become healthy." -ForegroundColor Yellow
                Write-Host "Check status with: .\proxmox-test.ps1 status" -ForegroundColor Yellow
                exit 1
            }

            # Prompt for root password
            $RootPassword = Read-Host "Enter the root password you set during Proxmox installation"
            if (-not $RootPassword) {
                Write-Host "Error: Password required" -ForegroundColor Red
                exit 1
            }

            Write-Host "Authenticating with Proxmox..." -ForegroundColor Cyan
            
            # Get authentication ticket
            $AuthResponse = docker exec proxmox-test curl -sk -d "username=root@pam&password=$RootPassword" "https://172.30.0.2:8006/api2/json/access/ticket" 2>$null
            $Auth = $AuthResponse | ConvertFrom-Json
            
            if (-not $Auth.data.ticket) {
                Write-Host "Error: Authentication failed. Check your password." -ForegroundColor Red
                exit 1
            }

            $Ticket = $Auth.data.ticket
            $CSRFToken = $Auth.data.CSRFPreventionToken

            Write-Host "Creating API token..." -ForegroundColor Cyan
            
            # Create API token
            $TokenResponse = docker exec proxmox-test curl -sk -X POST `
                -H "Cookie: PVEAuthCookie=$Ticket" `
                -H "CSRFPreventionToken: $CSRFToken" `
                -d "privsep=0" `
                "https://172.30.0.2:8006/api2/json/access/users/root@pam/token/test" 2>$null
            
            $Token = $TokenResponse | ConvertFrom-Json
            
            if (-not $Token.data.value) {
                # Token might already exist, try to show existing
                Write-Host "Note: Token may already exist. Checking..." -ForegroundColor Yellow
                Write-Host $TokenResponse -ForegroundColor Gray
            }
            else {
                $TokenSecret = $Token.data.value
                Write-Host ""
                Write-Host "API Token created successfully!" -ForegroundColor Green
                Write-Host "  Token ID:     root@pam!test" -ForegroundColor Cyan
                Write-Host "  Token Secret: $TokenSecret" -ForegroundColor Cyan
                Write-Host ""

                # Update test/.env with the new token
                $EnvContent = Get-Content "test/.env" -Raw
                $EnvContent = $EnvContent -replace 'PROXMOX_TOKEN_SECRET=.*', "PROXMOX_TOKEN_SECRET=$TokenSecret"
                Set-Content "test/.env" $EnvContent -NoNewline
                
                Write-Host "Updated test/.env with new token." -ForegroundColor Green
                Write-Host ""
                Write-Host "Test the connection with: .\proxmox-test.ps1 test" -ForegroundColor Yellow
            }
        }

        "save-snapshot" {
            Write-Host "Saving Proxmox snapshot..." -ForegroundColor Cyan
            
            # Stop container first for consistent snapshot
            $Running = docker ps -q -f name=proxmox-test 2>$null
            if ($Running) {
                Write-Host "Stopping container for consistent snapshot..." -ForegroundColor Yellow
                docker compose -f $ComposeFile down
            }

            # Check if volume exists
            $VolumeExists = docker volume ls -q -f name=$VolumeName 2>$null
            if (-not $VolumeExists) {
                Write-Host "Error: Volume $VolumeName not found. Run installation first." -ForegroundColor Red
                exit 1
            }

            Write-Host "Exporting volume to $SnapshotFile..." -ForegroundColor Cyan
            docker run --rm -v ${VolumeName}:/data -v ${PWD}/test:/backup alpine tar -czf /backup/proxmox-snapshot.tar.gz -C /data .
            
            if ($LASTEXITCODE -eq 0) {
                $Size = (Get-Item $SnapshotFile).Length / 1MB
                Write-Host ""
                Write-Host "Snapshot saved successfully!" -ForegroundColor Green
                Write-Host "  File: $SnapshotFile" -ForegroundColor Cyan
                Write-Host "  Size: $([math]::Round($Size, 1)) MB" -ForegroundColor Cyan
                Write-Host ""
                Write-Host "Restore with: .\proxmox-test.ps1 restore-snapshot" -ForegroundColor Yellow
            }
            else {
                Write-Host "Error: Failed to create snapshot" -ForegroundColor Red
                exit 1
            }
        }

        "restore-snapshot" {
            Write-Host "Restoring Proxmox from snapshot..." -ForegroundColor Cyan
            
            # Check if snapshot exists
            if (-not (Test-Path $SnapshotFile)) {
                Write-Host "Error: Snapshot not found at $SnapshotFile" -ForegroundColor Red
                Write-Host "Run installation first, then: .\proxmox-test.ps1 save-snapshot" -ForegroundColor Yellow
                exit 1
            }

            # Stop and remove existing container/volume
            Write-Host "Removing existing instance..." -ForegroundColor Yellow
            docker compose -f $ComposeFile down -v 2>$null

            # Create fresh volume
            Write-Host "Creating volume..." -ForegroundColor Cyan
            docker volume create $VolumeName
            
            # Restore from snapshot
            Write-Host "Restoring from snapshot..." -ForegroundColor Cyan
            docker run --rm -v ${VolumeName}:/data -v ${PWD}/test:/backup alpine tar -xzf /backup/proxmox-snapshot.tar.gz -C /data
            
            if ($LASTEXITCODE -eq 0) {
                Write-Host ""
                Write-Host "Snapshot restored successfully!" -ForegroundColor Green
                Write-Host ""
                Write-Host "Start with: .\proxmox-test.ps1 start" -ForegroundColor Yellow
            }
            else {
                Write-Host "Error: Failed to restore snapshot" -ForegroundColor Red
                exit 1
            }
        }

        "ports" {
            # List all configured port forwards
            Write-Host ""
            Write-Host "Configured Port Forwards" -ForegroundColor Cyan
            Write-Host "========================" -ForegroundColor Cyan
            Write-Host ""
            
            $PortsFile = "test/ports.yaml"
            if (-not (Test-Path $PortsFile)) {
                Write-Host "No ports.yaml found. Create test/ports.yaml to configure port forwards." -ForegroundColor Yellow
                exit 0
            }
            
            # Parse YAML (simple parser for our format)
            $Content = Get-Content $PortsFile -Raw
            $Lines = $Content -split "`n"
            $CurrentService = $null
            $Services = @{}
            
            foreach ($Line in $Lines) {
                if ($Line -match "^(\w+):$") {
                    $CurrentService = $Matches[1]
                    $Services[$CurrentService] = @{}
                }
                elseif ($CurrentService -and $Line -match "^\s+(\w+):\s*(.+)$") {
                    $Key = $Matches[1]
                    $Value = $Matches[2].Trim().Trim('"')
                    $Services[$CurrentService][$Key] = $Value
                }
            }
            
            # Display table
            Write-Host ("{0,-20} {1,-18} {2,-12} {3}" -f "Service", "Target", "Local Port", "Description")
            Write-Host ("{0,-20} {1,-18} {2,-12} {3}" -f "-------", "------", "----------", "-----------")
            
            foreach ($Name in $Services.Keys) {
                $Svc = $Services[$Name]
                $IP = $Svc["ip"]
                $Port = $Svc["port"]
                $LocalPort = if ($Svc["local_port"]) { $Svc["local_port"] } else { $Port }
                $Desc = if ($Svc["description"]) { $Svc["description"] } else { "" }
                
                Write-Host ("{0,-20} {1,-18} {2,-12} {3}" -f $Name, "${IP}:${Port}", $LocalPort, $Desc)
            }
            
            Write-Host ""
            Write-Host "Access URLs (after running 'forward'):" -ForegroundColor Green
            foreach ($Name in $Services.Keys) {
                $Svc = $Services[$Name]
                $LocalPort = if ($Svc["local_port"]) { $Svc["local_port"] } else { $Svc["port"] }
                Write-Host "  $Name : http://localhost:$LocalPort" -ForegroundColor Cyan
            }
            Write-Host ""
            Write-Host "Set up forwarding with: .\proxmox-test.ps1 forward" -ForegroundColor Yellow
        }

        "tunnel" {
            Write-Host ""
            Write-Host "Note: SSH tunnels don't work from Docker. Use 'forward' instead:" -ForegroundColor Yellow
            Write-Host "  .\proxmox-test.ps1 forward" -ForegroundColor Cyan
            Write-Host ""
            Write-Host "This sets up iptables inside Proxmox to forward ports to LXC containers." -ForegroundColor Gray
            exit 0
        }

        "forward" {
            # Set up iptables port forwarding inside Proxmox
            Write-Host ""
            Write-Host "Setting up port forwarding to test services..." -ForegroundColor Cyan
            Write-Host ""
            
            # Check if container is running
            $Running = docker ps -q -f name=proxmox-test 2>$null
            if (-not $Running) {
                Write-Host "Error: Proxmox container is not running" -ForegroundColor Red
                Write-Host "Start with: .\proxmox-test.ps1 start" -ForegroundColor Yellow
                exit 1
            }
            
            # Make sure sshpass is installed in container
            $SshpassExists = docker exec proxmox-test which sshpass 2>$null
            if (-not $SshpassExists) {
                Write-Host "Installing SSH tools in container..." -ForegroundColor Cyan
                docker exec proxmox-test apt-get update -qq 2>$null
                docker exec proxmox-test apt-get install -y -qq openssh-client sshpass 2>$null
            }
            
            # Get root password from env or prompt
            $EnvContent = Get-Content "test/.env" -Raw -ErrorAction SilentlyContinue
            if ($EnvContent -match 'PROXMOX_ROOT_PASSWORD=(.+)') {
                $RootPassword = $Matches[1].Trim()
            }
            else {
                $RootPassword = Read-Host "Enter Proxmox root password"
            }
            
            # Parse ports.yaml
            $PortsFile = "test/ports.yaml"
            if (-not (Test-Path $PortsFile)) {
                Write-Host "Error: $PortsFile not found" -ForegroundColor Red
                exit 1
            }
            
            $Content = Get-Content $PortsFile -Raw
            $Lines = $Content -split "`n"
            $CurrentService = $null
            $Services = @{}
            
            foreach ($Line in $Lines) {
                if ($Line -match "^(\w+):$") {
                    $CurrentService = $Matches[1]
                    $Services[$CurrentService] = @{}
                }
                elseif ($CurrentService -and $Line -match "^\s+(\w+):\s*(.+)$") {
                    $Key = $Matches[1]
                    $Value = $Matches[2].Trim().Trim('"')
                    $Services[$CurrentService][$Key] = $Value
                }
            }
            
            if ($Services.Count -eq 0) {
                Write-Host "No services configured in $PortsFile" -ForegroundColor Yellow
                exit 0
            }
            
            # Build iptables commands
            $IptablesCmd = "echo 1 > /proc/sys/net/ipv4/ip_forward"
            
            # Clear any existing DNAT rules first (flush PREROUTING)
            $IptablesCmd += " && iptables -t nat -F PREROUTING"
            
            foreach ($Name in $Services.Keys) {
                $Svc = $Services[$Name]
                $IP = $Svc["ip"]
                $Port = $Svc["port"]
                $LocalPort = if ($Svc["local_port"]) { $Svc["local_port"] } else { $Port }
                
                # Add DNAT rule for this service
                $IptablesCmd += " && iptables -t nat -A PREROUTING -p tcp --dport $LocalPort -j DNAT --to-destination ${IP}:${Port}"
                Write-Host "  $Name : localhost:$LocalPort -> ${IP}:${Port}" -ForegroundColor Green
            }
            
            # Add masquerade for return traffic
            $IptablesCmd += " && iptables -t nat -C POSTROUTING -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -j MASQUERADE"
            $IptablesCmd += " && echo 'OK'"
            
            Write-Host ""
            Write-Host "Configuring iptables in Proxmox..." -ForegroundColor Cyan
            
            $Result = docker exec proxmox-test sshpass -p "$RootPassword" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR root@172.30.0.2 "$IptablesCmd" 2>$null
            
            if ($Result -match "OK") {
                Write-Host ""
                Write-Host "Port forwarding configured!" -ForegroundColor Green
                Write-Host ""
                Write-Host "Access your services:" -ForegroundColor Cyan
                foreach ($Name in $Services.Keys) {
                    $Svc = $Services[$Name]
                    $LocalPort = if ($Svc["local_port"]) { $Svc["local_port"] } else { $Svc["port"] }
                    $Desc = if ($Svc["description"]) { " - " + $Svc["description"] } else { "" }
                    Write-Host "  http://localhost:$LocalPort$Desc" -ForegroundColor White
                }
                Write-Host ""
                Write-Host "Note: Forwarding persists until Proxmox reboots." -ForegroundColor Gray
            }
            else {
                Write-Host "Error configuring iptables:" -ForegroundColor Red
                Write-Host $Result -ForegroundColor Gray
                exit 1
            }
        }

        default {
            # Build image if it doesn't exist
            $ImageExists = docker images -q proxmox-config:latest 2>$null
            if (-not $ImageExists) {
                Write-Host "Building Docker image..." -ForegroundColor Cyan
                docker compose -f $ComposeFile build
                if ($LASTEXITCODE -ne 0) { throw "Failed to build Docker image" }
                Write-Host ""
            }

            # Pass all arguments through to proxmox-config CLI
            if ($Arguments.Count -eq 0) {
                docker compose -f $ComposeFile run --rm proxmox-config --help
            }
            else {
                docker compose -f $ComposeFile run --rm proxmox-config @Arguments
            }

            exit $LASTEXITCODE
        }
    }
}
finally {
    Pop-Location
}
