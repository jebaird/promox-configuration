"""ISO download and upload management for Proxmox."""

import hashlib
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, DownloadColumn

from .proxmox_client import ProxmoxClient

console = Console()

# pfSense download URLs and patterns
PFSENSE_MIRROR_BASE = "https://atxfiles.netgate.com/mirror/downloads/"
PFSENSE_FILENAME_PATTERN = "pfSense-CE-{version}-RELEASE-{arch}.iso.gz"


class ISOManager:
    """Manages ISO downloads and uploads to Proxmox storage."""
    
    def __init__(self, client: ProxmoxClient):
        """Initialize ISO manager.
        
        Args:
            client: Proxmox API client
        """
        self.client = client
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "proxmox-config/0.1.0"
        })
    
    def get_pfsense_download_url(self, version: str, arch: str = "amd64") -> str:
        """Get pfSense ISO download URL.
        
        Args:
            version: pfSense version (e.g., '2.7.2')
            arch: Architecture ('amd64' or 'i386')
            
        Returns:
            Full download URL
        """
        filename = PFSENSE_FILENAME_PATTERN.format(version=version, arch=arch)
        return urljoin(PFSENSE_MIRROR_BASE, filename)
    
    def get_pfsense_iso_filename(self, version: str, arch: str = "amd64") -> str:
        """Get the uncompressed ISO filename for pfSense.
        
        Args:
            version: pfSense version
            arch: Architecture
            
        Returns:
            ISO filename (without .gz)
        """
        return f"pfSense-CE-{version}-RELEASE-{arch}.iso"
    
    def iso_exists_on_proxmox(self, storage: str, filename: str) -> bool:
        """Check if ISO already exists on Proxmox storage.
        
        Args:
            storage: Storage pool name (e.g., 'local')
            filename: ISO filename
        """
        return self.client.iso_exists(storage, filename)
    
    def download_file(
        self,
        url: str,
        dest_path: Path,
        show_progress: bool = True,
    ) -> Path:
        """Download a file from URL.
        
        Args:
            url: Source URL
            dest_path: Destination file path
            show_progress: Show download progress bar
            
        Returns:
            Path to downloaded file
        """
        console.print(f"[dim]Downloading from {url}...[/dim]")
        
        response = self.session.get(url, stream=True)
        response.raise_for_status()
        
        total_size = int(response.headers.get("content-length", 0))
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            console=console,
            disable=not show_progress,
        ) as progress:
            task = progress.add_task("Downloading", total=total_size)
            
            with open(dest_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    progress.update(task, advance=len(chunk))
        
        console.print(f"[green]✓[/green] Downloaded to {dest_path}")
        return dest_path
    
    def decompress_gzip(self, gz_path: Path, dest_path: Path | None = None) -> Path:
        """Decompress a gzip file.
        
        Args:
            gz_path: Path to .gz file
            dest_path: Destination path (default: same name without .gz)
            
        Returns:
            Path to decompressed file
        """
        import gzip
        
        if dest_path is None:
            dest_path = gz_path.with_suffix("")
        
        console.print(f"[dim]Decompressing {gz_path.name}...[/dim]")
        
        with gzip.open(gz_path, "rb") as f_in:
            with open(dest_path, "wb") as f_out:
                # Copy in chunks for large files
                while chunk := f_in.read(8192 * 1024):  # 8MB chunks
                    f_out.write(chunk)
        
        console.print(f"[green]✓[/green] Decompressed to {dest_path.name}")
        return dest_path
    
    def upload_iso_to_proxmox(
        self,
        local_path: Path,
        storage: str,
        filename: str | None = None,
    ) -> None:
        """Upload an ISO file to Proxmox storage.
        
        Args:
            local_path: Local path to ISO file
            storage: Target storage pool
            filename: Filename on Proxmox (default: same as local)
        """
        if filename is None:
            filename = local_path.name
        
        console.print(f"[dim]Uploading {filename} to Proxmox storage '{storage}'...[/dim]")
        
        # Check file size for progress estimation
        file_size = local_path.stat().st_size
        console.print(f"[dim]File size: {file_size / (1024*1024*1024):.2f} GB[/dim]")
        
        # Upload via API
        # Note: proxmoxer handles the multipart upload
        self.client.upload_iso(storage, str(local_path), filename)
        
        console.print(f"[green]✓[/green] Uploaded {filename} to {storage}")
    
    def download_and_upload_pfsense(
        self,
        version: str,
        storage: str = "local",
        arch: str = "amd64",
        keep_local: bool = False,
    ) -> str:
        """Download pfSense ISO and upload to Proxmox.
        
        Args:
            version: pfSense version (e.g., '2.7.2')
            storage: Target Proxmox storage
            arch: Architecture
            keep_local: Keep downloaded files after upload
            
        Returns:
            Volume ID of uploaded ISO
        """
        iso_filename = self.get_pfsense_iso_filename(version, arch)
        
        # Check if already uploaded
        if self.iso_exists_on_proxmox(storage, iso_filename):
            console.print(f"[green]✓[/green] ISO {iso_filename} already exists on Proxmox")
            return f"{storage}:iso/{iso_filename}"
        
        # Create temp directory for download
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            
            # Download compressed ISO
            gz_url = self.get_pfsense_download_url(version, arch)
            gz_filename = f"{iso_filename}.gz"
            gz_path = tmpdir_path / gz_filename
            
            try:
                self.download_file(gz_url, gz_path)
            except requests.HTTPError as e:
                if e.response.status_code == 404:
                    raise ValueError(
                        f"pfSense version {version} not found. "
                        f"Check available versions at {PFSENSE_MIRROR_BASE}"
                    ) from e
                raise
            
            # Decompress
            iso_path = self.decompress_gzip(gz_path, tmpdir_path / iso_filename)
            
            # Remove compressed file to save space
            gz_path.unlink()
            
            # Upload to Proxmox
            self.upload_iso_to_proxmox(iso_path, storage, iso_filename)
            
            # Optionally copy to current directory
            if keep_local:
                local_copy = Path.cwd() / iso_filename
                import shutil
                shutil.copy2(iso_path, local_copy)
                console.print(f"[dim]Kept local copy at {local_copy}[/dim]")
        
        return f"{storage}:iso/{iso_filename}"
    
    def list_isos(self, storage: str = "local") -> list[dict]:
        """List ISO files on Proxmox storage.
        
        Args:
            storage: Storage pool name
            
        Returns:
            List of ISO file info dicts
        """
        return self.client.get_storage_content(storage, "iso")
    
    def print_isos_table(self, storage: str = "local") -> None:
        """Print formatted table of ISOs on storage."""
        from rich.table import Table
        
        isos = self.list_isos(storage)
        
        table = Table(title=f"ISO Images on '{storage}'")
        table.add_column("Filename", style="cyan")
        table.add_column("Size", style="yellow")
        table.add_column("Volume ID")
        
        for iso in isos:
            size_mb = iso.get("size", 0) / (1024 * 1024)
            table.add_row(
                iso.get("volid", "").split("/")[-1],
                f"{size_mb:.1f} MB",
                iso.get("volid", ""),
            )
        
        console.print(table)
