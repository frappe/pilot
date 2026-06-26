from dataclasses import dataclass, field


@dataclass
class DatasetConfig:
    reservation: str = "5G"
    quota: str = "50G"


@dataclass
class ImageConfig:
    size: str = ""
    path: str = ""


@dataclass
class VolumeConfig:
    """ZFS storage for the bench. Set enabled = false to skip ZFS entirely.

    When enabled on Linux, the bench lives on a single dataset inside a shared
    pool (one dataset per bench, named after the bench). Both the bench files
    and the MariaDB data live on that one dataset — exposed at their
    conventional paths via bind mounts — so a snapshot/rollback is atomic across
    both. The pool is backed by a dedicated disk, a preallocated image file on
    the root filesystem, or auto-resolved at init time. Skipped on macOS (dev
    only)."""

    enabled: bool = False
    pool: str = "bench-pool"
    name: str = ""  # dataset leaf, defaults to the bench name
    backing: str = "auto"  # "device" | "image" | "auto" (resolved during bench init)
    device: str = ""
    image: ImageConfig = field(default_factory=ImageConfig)
    dataset: DatasetConfig = field(default_factory=DatasetConfig)

    @property
    def dataset_path(self) -> str:
        return f"{self.pool}/{self.name or 'bench'}"

    @property
    def image_path(self) -> str:
        return self.image.path or f"/var/lib/bench-zfs/{self.pool}.img"
