from dataclasses import dataclass, field


@dataclass
class BenchesDatasetConfig:
    reservation: str = "10G"
    quota: str = "50G"
    data_dir: str = "/home/bench"


@dataclass
class MariaDBDatasetConfig:
    reservation: str = "5G"
    quota: str = "20G"
    data_dir: str = "/var/lib/mysql"


@dataclass
class SnapshotConfig:
    enabled: bool = False


@dataclass
class ImageConfig:
    size: str = ""
    path: str = ""


@dataclass
class VolumeConfig:
    enabled: bool = False
    pool: str = ""
    backing: str = "device"  # "device" (dedicated block device) | "image" (file on the root filesystem)
    device: str = ""
    image: ImageConfig = field(default_factory=ImageConfig)
    benches: BenchesDatasetConfig = field(default_factory=BenchesDatasetConfig)
    mariadb: MariaDBDatasetConfig = field(default_factory=MariaDBDatasetConfig)
    snapshots: SnapshotConfig = field(default_factory=SnapshotConfig)

    @property
    def benches_dataset(self) -> str:
        return f"{self.pool}/benches"

    @property
    def mariadb_dataset(self) -> str:
        return f"{self.pool}/mariadb"

    @property
    def image_path(self) -> str:
        return self.image.path or f"/var/lib/bench-zfs/{self.pool}.img"
