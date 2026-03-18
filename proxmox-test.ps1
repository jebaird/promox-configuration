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
