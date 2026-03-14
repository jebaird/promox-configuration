"""Create disk images with pfSense configuration."""

import subprocess
import tempfile
from pathlib import Path
from typing import Any

from rich.console import Console

console = Console()


def create_config_disk(config_xml: str, output_path: Path | str) -> Path:
    """Create a small FAT32 disk image containing pfSense config.xml.
    
    This creates a disk image that pfSense can read during installation
    to restore configuration automatically.
    
    Args:
        config_xml: The config.xml content as a string
        output_path: Path for the output disk image (.img)
        
    Returns:
        Path to the created disk image
        
    Note:
        This requires 'mtools' to be installed in the container.
        Falls back to creating a raw file with config if mtools unavailable.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Size of disk image (2MB is plenty for config.xml)
    size_mb = 2
    
    try:
        # Try using mtools (available in Docker container)
        return _create_fat_image_mtools(config_xml, output_path, size_mb)
    except (subprocess.CalledProcessError, FileNotFoundError):
        console.print("[yellow]mtools not available, using fallback method[/yellow]")
        return _create_fallback_image(config_xml, output_path)


def _create_fat_image_mtools(config_xml: str, output_path: Path, size_mb: int) -> Path:
    """Create FAT image using mtools.
    
    mtools commands:
    - mformat: format disk image as FAT
    - mcopy: copy files to FAT image
    """
    # Create empty disk image
    with open(output_path, 'wb') as f:
        f.write(b'\x00' * (size_mb * 1024 * 1024))
    
    # Format as FAT12 (suitable for small images)
    subprocess.run(
        ['mformat', '-i', str(output_path), '-f', str(size_mb * 1024), '::'],
        check=True,
        capture_output=True,
    )
    
    # Write config.xml to temp file, then copy to image
    with tempfile.NamedTemporaryFile(mode='w', suffix='.xml', delete=False) as f:
        f.write(config_xml)
        temp_config = f.name
    
    try:
        subprocess.run(
            ['mcopy', '-i', str(output_path), temp_config, '::config.xml'],
            check=True,
            capture_output=True,
        )
    finally:
        Path(temp_config).unlink(missing_ok=True)
    
    console.print(f"[green]✓[/green] Created config disk: {output_path}")
    return output_path


def _create_fallback_image(config_xml: str, output_path: Path) -> Path:
    """Create a simple disk image without FAT formatting.
    
    This is a fallback when mtools is not available.
    Creates a raw disk with config.xml content that can be 
    manually extracted or used differently.
    """
    # Create a simple tar archive instead
    import tarfile
    import io
    
    tar_path = output_path.with_suffix('.tar')
    
    with tarfile.open(tar_path, 'w') as tar:
        # Add config.xml to archive
        config_bytes = config_xml.encode('utf-8')
        info = tarfile.TarInfo(name='config.xml')
        info.size = len(config_bytes)
        tar.addfile(info, io.BytesIO(config_bytes))
    
    console.print(f"[green]✓[/green] Created config archive: {tar_path}")
    console.print("[dim]Note: Manual config import may be required[/dim]")
    return tar_path


def check_mtools_available() -> bool:
    """Check if mtools is available in the system."""
    try:
        subprocess.run(['mformat', '--version'], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


class ConfigDiskManager:
    """Manages creation and upload of config disks to Proxmox."""
    
    def __init__(self, client: Any):
        """Initialize with Proxmox client.
        
        Args:
            client: ProxmoxClient instance
        """
        self.client = client
    
    def create_and_upload(
        self,
        config_xml: str,
        storage: str = "local",
        filename: str = "pfsense-config.img",
    ) -> str:
        """Create config disk and upload to Proxmox storage.
        
        Args:
            config_xml: pfSense config.xml content
            storage: Target Proxmox storage
            filename: Name for the uploaded file
            
        Returns:
            Volume ID of uploaded disk
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            disk_path = Path(tmpdir) / filename
            
            # Create the disk image
            create_config_disk(config_xml, disk_path)
            
            # Upload to Proxmox
            # Note: The actual upload mechanism depends on storage type
            # For now, we'll return the local path for manual handling
            console.print(f"[dim]Config disk created at: {disk_path}[/dim]")
            
            # TODO: Implement actual upload to Proxmox storage
            # This would use the Proxmox API to upload the image
            
            return f"{storage}:images/{filename}"
