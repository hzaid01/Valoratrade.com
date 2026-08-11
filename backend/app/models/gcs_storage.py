"""
Google Cloud Storage helper for model persistence.
"""
import os
import logging
from google.cloud import storage
from google.api_core.exceptions import NotFound

logger = logging.getLogger(__name__)

BUCKET_NAME = "crypto-models-bucket"

def get_storage_client() -> storage.Client:
    """Gets an authenticated GCS client."""
    return storage.Client()

def ensure_bucket_exists():
    """Ensures the models bucket exists."""
    client = get_storage_client()
    try:
        bucket = client.get_bucket(BUCKET_NAME)
        return bucket
    except NotFound:
        logger.info(f"Bucket {BUCKET_NAME} not found. Creating it...")
        # Note: bucket names are globally unique.
        try:
            # Set location to the same as the project (e.g. asia-south1)
            bucket = client.create_bucket(BUCKET_NAME, location="asia-south1")
            return bucket
        except Exception as e:
            logger.error(f"Failed to create bucket: {e}")
            raise

def upload_model(local_path: str, remote_path: str) -> None:
    """Uploads a model file to GCS."""
    bucket = ensure_bucket_exists()
    blob = bucket.blob(remote_path)
    blob.upload_from_filename(local_path)
    logger.info(f"Uploaded {local_path} to gs://{BUCKET_NAME}/{remote_path}")

def download_model(remote_path: str, local_path: str) -> None:
    """Downloads a model file from GCS."""
    bucket = ensure_bucket_exists()
    blob = bucket.blob(remote_path)
    
    # Ensure local directory exists
    os.makedirs(os.path.dirname(local_path) if os.path.dirname(local_path) else ".", exist_ok=True)
    
    blob.download_to_filename(local_path)
    logger.info(f"Downloaded gs://{BUCKET_NAME}/{remote_path} to {local_path}")

def model_exists_in_gcs(remote_path: str) -> bool:
    """Checks if a model file exists in GCS."""
    try:
        bucket = get_storage_client().get_bucket(BUCKET_NAME)
        blob = bucket.blob(remote_path)
        return blob.exists()
    except NotFound:
        return False
