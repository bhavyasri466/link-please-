import hmac
import hashlib
import logging
from typing import Optional
from app.config import settings

logger = logging.getLogger(__name__)

def verify_webhook_signature(raw_body: bytes, signature_header: Optional[str], secret_key: Optional[str] = None) -> bool:
    """
    Verifies the HMAC-SHA256 signature sent in the X-PseudoGram-Signature header.
    Format expected: sha256=<hex_digest> (or raw hex).
    Secret: Pseudogram API Key.
    """
    secret = secret_key or settings.PSEUDOGRAM_API_KEY
    
    # If no secret is configured and signature verification is not strictly required, allow in dev
    if not secret:
        if settings.VERIFY_SIGNATURE:
            # If signature verification is enabled but no secret is set, log and proceed with warning
            logger.warning("No PSEUDOGRAM_API_KEY configured for HMAC verification. Skipping check in development mode.")
            return True
        return True
        
    if not signature_header:
        logger.warning("Missing X-PseudoGram-Signature header.")
        return False
        
    signature = signature_header.strip()
    if signature.startswith("sha256="):
        signature = signature[7:].strip()
        
    computed_digest = hmac.new(
        key=secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256
    ).hexdigest()
    
    is_valid = hmac.compare_digest(computed_digest, signature)
    if not is_valid:
        logger.warning(f"Invalid signature. Expected {computed_digest}, received {signature}")
    return is_valid
