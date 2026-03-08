"""
Data and Feature Versioning

Implements versioning for:
- Raw datasets
- Processed datasets
- Feature sets
- Training snapshots
"""
import logging
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class DatasetVersion:
    """Version information for a dataset."""
    version: str
    symbol: str
    timeframe: str
    start_date: datetime
    end_date: datetime
    row_count: int
    checksum: str
    created_at: datetime = field(default_factory=datetime.utcnow)
    metadata: Dict = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        return {
            "version": self.version,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "row_count": self.row_count,
            "checksum": self.checksum,
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata
        }


@dataclass
class FeatureVersion:
    """Version information for a feature set."""
    version: str
    feature_names: List[str]
    feature_count: int
    source_dataset_version: str
    created_at: datetime = field(default_factory=datetime.utcnow)
    
    def to_dict(self) -> Dict:
        return {
            "version": self.version,
            "feature_names": self.feature_names,
            "feature_count": self.feature_count,
            "source_dataset": self.source_dataset_version,
            "created_at": self.created_at.isoformat()
        }


class DatasetVersioning:
    """
    Dataset versioning system.
    
    Ensures reproducibility by tracking:
    - Exact data used for training
    - Data transformations applied
    - Checksums for integrity
    """
    
    def __init__(self, storage_path: str = "data"):
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        
        self._versions: Dict[str, DatasetVersion] = {}
        self._load_versions()
    
    def create_version(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
        metadata: Optional[Dict] = None
    ) -> DatasetVersion:
        """
        Create a new dataset version.
        
        Args:
            df: Dataset DataFrame
            symbol: Trading symbol
            timeframe: Data timeframe
            metadata: Optional metadata
            
        Returns:
            DatasetVersion with version info
        """
        # Generate version string
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        version = f"{symbol}_{timeframe}_v{timestamp}"
        
        # Calculate checksum
        checksum = self._calculate_checksum(df)
        
        # Get date range
        start_date = df.index.min()
        end_date = df.index.max()
        
        # Create version
        dataset_version = DatasetVersion(
            version=version,
            symbol=symbol,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            row_count=len(df),
            checksum=checksum,
            metadata=metadata or {}
        )
        
        # Save data
        data_path = self.storage_path / "raw" / version
        data_path.mkdir(parents=True, exist_ok=True)
        df.to_parquet(data_path / "data.parquet")
        
        # Save version info
        with open(data_path / "version.json", 'w') as f:
            json.dump(dataset_version.to_dict(), f, indent=2)
        
        self._versions[version] = dataset_version
        
        logger.info(f"Created dataset version: {version}")
        return dataset_version
    
    def get_version(self, version: str) -> Optional[DatasetVersion]:
        """Get version info."""
        return self._versions.get(version)
    
    def load_data(self, version: str) -> Optional[pd.DataFrame]:
        """Load data for a specific version."""
        data_path = self.storage_path / "raw" / version / "data.parquet"
        
        if not data_path.exists():
            logger.error(f"Data not found for version: {version}")
            return None
        
        return pd.read_parquet(data_path)
    
    def list_versions(self, symbol: Optional[str] = None) -> List[DatasetVersion]:
        """List all versions, optionally filtered by symbol."""
        versions = list(self._versions.values())
        
        if symbol:
            versions = [v for v in versions if v.symbol == symbol]
        
        return sorted(versions, key=lambda v: v.created_at, reverse=True)
    
    def verify_checksum(self, version: str, df: pd.DataFrame) -> bool:
        """Verify data integrity using checksum."""
        version_info = self.get_version(version)
        if not version_info:
            return False
        
        current_checksum = self._calculate_checksum(df)
        return current_checksum == version_info.checksum
    
    def _calculate_checksum(self, df: pd.DataFrame) -> str:
        """Calculate SHA256 checksum of DataFrame."""
        data_bytes = df.to_json().encode()
        return hashlib.sha256(data_bytes).hexdigest()[:16]
    
    def _load_versions(self) -> None:
        """Load existing versions from storage."""
        raw_path = self.storage_path / "raw"
        
        if not raw_path.exists():
            return
        
        for version_dir in raw_path.iterdir():
            if not version_dir.is_dir():
                continue
            
            version_file = version_dir / "version.json"
            if not version_file.exists():
                continue
            
            try:
                with open(version_file, 'r') as f:
                    data = json.load(f)
                
                self._versions[data["version"]] = DatasetVersion(
                    version=data["version"],
                    symbol=data["symbol"],
                    timeframe=data["timeframe"],
                    start_date=datetime.fromisoformat(data["start_date"]),
                    end_date=datetime.fromisoformat(data["end_date"]),
                    row_count=data["row_count"],
                    checksum=data["checksum"],
                    created_at=datetime.fromisoformat(data["created_at"]),
                    metadata=data.get("metadata", {})
                )
            except Exception as e:
                logger.warning(f"Failed to load version from {version_dir}: {e}")


class FeatureVersioning:
    """
    Feature set versioning.
    
    Tracks which features were used for model training.
    """
    
    def __init__(self, storage_path: str = "data"):
        self.storage_path = Path(storage_path) / "features"
        self.storage_path.mkdir(parents=True, exist_ok=True)
        
        self._versions: Dict[str, FeatureVersion] = {}
    
    def create_version(
        self,
        feature_names: List[str],
        source_dataset_version: str
    ) -> FeatureVersion:
        """Create a new feature version."""
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        version = f"features_v{timestamp}"
        
        feature_version = FeatureVersion(
            version=version,
            feature_names=feature_names,
            feature_count=len(feature_names),
            source_dataset_version=source_dataset_version
        )
        
        # Save version info
        version_path = self.storage_path / f"{version}.json"
        with open(version_path, 'w') as f:
            json.dump(feature_version.to_dict(), f, indent=2)
        
        self._versions[version] = feature_version
        
        return feature_version
    
    def get_version(self, version: str) -> Optional[FeatureVersion]:
        """Get feature version info."""
        return self._versions.get(version)
