"""
Secret Resolver Service

Resolves secret references to actual values from configured providers.

Railway-first implementation - future providers (AWS, GCP, Vault) are interface stubs.

Reference format: "provider:identifier"
Examples:
  - "railway:KAISCOUT_DB_URL"
  - "aws:prod/kaiscout/db-url" (future)
  - "gcp:projects/123/secrets/db-url/versions/latest" (future)
"""

import os
import logging
from typing import Optional
from enum import Enum

logger = logging.getLogger(__name__)


class SecretsProvider(str, Enum):
    """Supported secret providers."""
    RAILWAY = "railway"
    AWS_SECRETS_MANAGER = "aws_secrets_manager"
    GCP_SECRET_MANAGER = "gcp_secret_manager"
    VAULT = "vault"
    NONE = "none"


class SecretResolutionError(Exception):
    """Raised when secret resolution fails."""
    pass


class SecretResolver:
    """
    Pluggable secret resolution interface.

    Currently implements Railway-only. Future providers will extend this.

    Security:
    - Never logs resolved secrets
    - Validates reference format before resolution
    - Fails closed (raises on missing secrets)
    """

    def __init__(self):
        """Initialize resolver (Railway requires no configuration)."""
        pass

    def resolve(self, provider: SecretsProvider, reference: str) -> str:
        """
        Resolve a secret reference to its actual value.

        Args:
            provider: Secret provider type
            reference: Provider-specific reference string

        Returns:
            Resolved secret value

        Raises:
            SecretResolutionError: If resolution fails or secret not found

        Example:
            resolver = SecretResolver()
            db_url = resolver.resolve(SecretsProvider.RAILWAY, "railway:KAISCOUT_DB_URL")
        """
        if provider == SecretsProvider.RAILWAY:
            return self._resolve_railway(reference)
        elif provider == SecretsProvider.AWS_SECRETS_MANAGER:
            return self._resolve_aws(reference)
        elif provider == SecretsProvider.GCP_SECRET_MANAGER:
            return self._resolve_gcp(reference)
        elif provider == SecretsProvider.VAULT:
            return self._resolve_vault(reference)
        elif provider == SecretsProvider.NONE:
            raise SecretResolutionError("Provider set to 'none' - no secret resolution available")
        else:
            raise SecretResolutionError(f"Unknown provider: {provider}")

    def _resolve_railway(self, reference: str) -> str:
        """
        Resolve Railway environment variable.

        Format: "railway:VAR_NAME"
        Resolves to: os.getenv("VAR_NAME")

        Args:
            reference: Railway reference string

        Returns:
            Environment variable value

        Raises:
            SecretResolutionError: If format invalid or variable not found
        """
        if not reference.startswith("railway:"):
            raise SecretResolutionError(
                f"Invalid Railway reference format: {reference}. "
                f"Expected: railway:VAR_NAME"
            )

        var_name = reference.split(":", 1)[1]

        if not var_name:
            raise SecretResolutionError("Railway variable name cannot be empty")

        value = os.getenv(var_name)

        if value is None:
            raise SecretResolutionError(
                f"Railway environment variable not found: {var_name}. "
                f"Ensure the variable is set in Railway service configuration."
            )

        if not value.strip():
            raise SecretResolutionError(
                f"Railway environment variable is empty: {var_name}"
            )

        logger.info(f"Resolved Railway secret reference: {var_name}")
        return value

    def _resolve_aws(self, reference: str) -> str:
        """
        Resolve AWS Secrets Manager secret (FUTURE - not implemented).

        Format: "aws:secret-name" or "aws:arn:aws:secretsmanager:..."

        Implementation will require:
        - boto3 (AWS SDK)
        - AWS credentials configured (IAM role or access keys)
        - secretsmanager:GetSecretValue permission
        """
        raise SecretResolutionError(
            "AWS Secrets Manager not yet implemented. "
            "Use Railway provider for now or implement AWS support in secret_resolver.py"
        )

        # Future implementation:
        # import boto3
        # client = boto3.client('secretsmanager')
        # response = client.get_secret_value(SecretId=reference.split(":", 1)[1])
        # return response['SecretString']

    def _resolve_gcp(self, reference: str) -> str:
        """
        Resolve GCP Secret Manager secret (FUTURE - not implemented).

        Format: "gcp:projects/PROJECT_ID/secrets/SECRET_NAME/versions/VERSION"

        Implementation will require:
        - google-cloud-secret-manager (GCP SDK)
        - GCP credentials configured (service account)
        - secretmanager.versions.access permission
        """
        raise SecretResolutionError(
            "GCP Secret Manager not yet implemented. "
            "Use Railway provider for now or implement GCP support in secret_resolver.py"
        )

        # Future implementation:
        # from google.cloud import secretmanager
        # client = secretmanager.SecretManagerServiceClient()
        # response = client.access_secret_version(name=reference.split(":", 1)[1])
        # return response.payload.data.decode('UTF-8')

    def _resolve_vault(self, reference: str) -> str:
        """
        Resolve HashiCorp Vault secret (FUTURE - not implemented).

        Format: "vault:secret/data/path/to/secret"

        Implementation will require:
        - hvac (Vault SDK)
        - VAULT_ADDR and VAULT_TOKEN environment variables
        - Read permission on secret path
        """
        raise SecretResolutionError(
            "HashiCorp Vault not yet implemented. "
            "Use Railway provider for now or implement Vault support in secret_resolver.py"
        )

        # Future implementation:
        # import hvac
        # client = hvac.Client(url=os.getenv('VAULT_ADDR'), token=os.getenv('VAULT_TOKEN'))
        # response = client.secrets.kv.v2.read_secret_version(path=reference.split(":", 1)[1])
        # return response['data']['data']['value']

    def resolve_db_url(self, provider: SecretsProvider, reference: Optional[str]) -> Optional[str]:
        """
        Convenience method for resolving database URLs.

        Args:
            provider: Secret provider type
            reference: Provider-specific reference (can be None for none provider)

        Returns:
            Resolved database URL or None if reference is None

        Raises:
            SecretResolutionError: If resolution fails
        """
        if reference is None:
            return None

        if provider == SecretsProvider.NONE:
            return None

        return self.resolve(provider, reference)


# Global resolver instance
_resolver: Optional[SecretResolver] = None


def get_resolver() -> SecretResolver:
    """
    Get the global SecretResolver instance (singleton pattern).

    Returns:
        SecretResolver instance
    """
    global _resolver
    if _resolver is None:
        _resolver = SecretResolver()
    return _resolver
