from dataclasses import dataclass


@dataclass
class ProductionConfig:
    nginx: bool = False
    lightweight: bool = False
