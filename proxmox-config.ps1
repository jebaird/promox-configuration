<#
.SYNOPSIS
    Wrapper script for running proxmox-config commands via Docker Compose.

.DESCRIPTION
    This script simplifies running proxmox-config CLI commands in Docker.
    All arguments are passed through to the proxmox-config CLI.

.EXAMPLE
    .\proxmox-config.ps1 test
    Tests connection to Proxmox API.

.EXAMPLE
    .\proxmox-config.ps1 deploy pfsense --dry-run
    Shows what the pfSense deployment would do.

.EXAMPLE
    .\proxmox-config.ps1 network list
    Lists network interfaces on Proxmox.

.EXAMPLE
    .\proxmox-config.ps1 vm list
    Lists all VMs on Proxmox.

.EXAMPLE
    .\proxmox-config.ps1 --help
    Shows available commands.
#>

param(
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ErrorActionPreference = "Stop"

# Change to script directory
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $ScriptDir

try {
    # Check if .env file exists
    if (-not (Test-Path ".env")) {
        Write-Host "Warning: .env file not found. Copy .env.example to .env and configure credentials." -ForegroundColor Yellow
        Write-Host ""
    }

    # Build image if it doesn't exist
    $ImageExists = docker images -q proxmox-config:latest 2>$null
    if (-not $ImageExists) {
        Write-Host "Building Docker image..." -ForegroundColor Cyan
        docker compose build
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to build Docker image"
        }
        Write-Host ""
    }

    # Run the command
    if ($Arguments.Count -eq 0) {
        # No arguments - show help
        docker compose run --rm proxmox-config --help
    }
    else {
        docker compose run --rm proxmox-config @Arguments
    }

    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
