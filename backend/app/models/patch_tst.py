"""
PatchTST - Patch Time Series Transformer

Modern time-series transformer for temporal embeddings.
Used ONLY to produce embeddings, NOT for direct predictions.

Reference: "A Time Series is Worth 64 Words: Long-term Forecasting with Transformers"
"""
import logging
from dataclasses import dataclass
from typing import Optional, Tuple
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class PatchTSTConfig:
    """Configuration for PatchTST model."""
    seq_len: int = 168          # Input sequence length (1 week of hourly)
    patch_len: int = 16         # Patch length
    stride: int = 8             # Stride for patches
    d_model: int = 64           # Model dimension
    n_heads: int = 4            # Number of attention heads
    n_layers: int = 2           # Number of transformer layers
    d_ff: int = 256             # Feed-forward dimension
    dropout: float = 0.1        # Dropout rate
    n_features: int = 5         # OHLCV = 5 features
    
    @property
    def n_patches(self) -> int:
        """Calculate number of patches."""
        return (self.seq_len - self.patch_len) // self.stride + 1
    
    @property
    def embedding_dim(self) -> int:
        """Output embedding dimension."""
        return self.d_model


class PatchEmbedding(nn.Module):
    """Convert input patches to embeddings."""
    
    def __init__(self, config: PatchTSTConfig):
        super().__init__()
        self.patch_len = config.patch_len
        self.stride = config.stride
        self.n_features = config.n_features
        
        # Linear projection of flattened patch
        self.projection = nn.Linear(
            config.patch_len * config.n_features,
            config.d_model
        )
        
        # Positional encoding
        self.pos_embedding = nn.Parameter(
            torch.randn(1, config.n_patches, config.d_model)
        )
    
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: (batch, seq_len, n_features)
        Returns:
            (batch, n_patches, d_model)
        """
        batch_size = x.shape[0]
        
        # Create patches
        patches = []
        for i in range(0, x.shape[1] - self.patch_len + 1, self.stride):
            patch = x[:, i:i + self.patch_len, :]  # (batch, patch_len, features)
            patch = patch.reshape(batch_size, -1)   # (batch, patch_len * features)
            patches.append(patch)
        
        patches = torch.stack(patches, dim=1)  # (batch, n_patches, patch_len * features)
        
        # Project and add positional encoding
        embeddings = self.projection(patches)
        embeddings = embeddings + self.pos_embedding[:, :embeddings.shape[1], :]
        
        return embeddings


class TransformerEncoderLayer(nn.Module):
    """Single transformer encoder layer."""
    
    def __init__(self, config: PatchTSTConfig):
        super().__init__()
        
        self.attention = nn.MultiheadAttention(
            config.d_model,
            config.n_heads,
            dropout=config.dropout,
            batch_first=True
        )
        
        self.ff = nn.Sequential(
            nn.Linear(config.d_model, config.d_ff),
            nn.GELU(),
            nn.Dropout(config.dropout),
            nn.Linear(config.d_ff, config.d_model),
            nn.Dropout(config.dropout)
        )
        
        self.norm1 = nn.LayerNorm(config.d_model)
        self.norm2 = nn.LayerNorm(config.d_model)
        self.dropout = nn.Dropout(config.dropout)
    
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # Self-attention with residual
        attn_out, _ = self.attention(x, x, x)
        x = self.norm1(x + self.dropout(attn_out))
        
        # Feed-forward with residual
        ff_out = self.ff(x)
        x = self.norm2(x + ff_out)
        
        return x


class PatchTST(nn.Module):
    """
    PatchTST for temporal embedding extraction.
    
    This model is used ONLY to produce embeddings.
    The embeddings are then fed to XGBoost for decision making.
    
    Architecture:
    1. Patch embedding (convert sequence to patches)
    2. Transformer encoder (learn temporal patterns)
    3. Pooling (aggregate to fixed-size embedding)
    """
    
    def __init__(self, config: Optional[PatchTSTConfig] = None):
        super().__init__()
        
        if config is None:
            settings = get_settings()
            config = PatchTSTConfig(
                seq_len=settings.model.seq_len,
                patch_len=settings.model.patch_len,
                stride=settings.model.stride,
                d_model=settings.model.d_model,
                n_heads=settings.model.n_heads,
                n_layers=settings.model.n_layers
            )
        
        self.config = config
        
        # Patch embedding
        self.patch_embed = PatchEmbedding(config)
        
        # Transformer encoder
        self.encoder_layers = nn.ModuleList([
            TransformerEncoderLayer(config)
            for _ in range(config.n_layers)
        ])
        
        # Final normalization
        self.norm = nn.LayerNorm(config.d_model)
        
        # Pooling head for embedding extraction
        self.pool = nn.Sequential(
            nn.Linear(config.d_model, config.d_model),
            nn.GELU()
        )
    
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Forward pass to extract embeddings.
        
        Args:
            x: (batch, seq_len, n_features) - OHLCV sequence
            
        Returns:
            (batch, d_model) - Temporal embeddings
        """
        # Patch embedding
        x = self.patch_embed(x)
        
        # Transformer encoding
        for layer in self.encoder_layers:
            x = layer(x)
        
        x = self.norm(x)
        
        # Global average pooling over patches
        x = x.mean(dim=1)  # (batch, d_model)
        
        # Final projection
        embeddings = self.pool(x)
        
        return embeddings
    
    def get_embedding_dim(self) -> int:
        """Get output embedding dimension."""
        return self.config.embedding_dim
    
    @torch.no_grad()
    def extract_embeddings(
        self,
        sequences: np.ndarray,
        batch_size: int = 32
    ) -> np.ndarray:
        """
        Extract embeddings for numpy array input.
        
        Args:
            sequences: (n_samples, seq_len, n_features)
            batch_size: Batch size for processing
            
        Returns:
            (n_samples, d_model) embeddings
        """
        self.eval()
        device = next(self.parameters()).device
        
        n_samples = len(sequences)
        
        # Handle empty input
        if n_samples == 0:
            raise ValueError(
                f"Cannot extract embeddings from empty sequences array. "
                f"Need at least {self.config.seq_len} data points to create sequences."
            )
        
        all_embeddings = []
        
        for i in range(0, n_samples, batch_size):
            batch = sequences[i:i + batch_size]
            batch_tensor = torch.FloatTensor(batch).to(device)
            
            embeddings = self.forward(batch_tensor)
            all_embeddings.append(embeddings.cpu().numpy())
        
        return np.concatenate(all_embeddings, axis=0)
    
    def save(self, path: str) -> None:
        """Save model weights."""
        torch.save({
            'config': self.config,
            'state_dict': self.state_dict()
        }, path)
        logger.info(f"PatchTST saved to {path}")
    
    @classmethod
    def load(cls, path: str, device: str = 'cpu') -> 'PatchTST':
        """Load model from file."""
        checkpoint = torch.load(path, map_location=device)
        model = cls(checkpoint['config'])
        model.load_state_dict(checkpoint['state_dict'])
        model.to(device)
        logger.info(f"PatchTST loaded from {path}")
        return model
