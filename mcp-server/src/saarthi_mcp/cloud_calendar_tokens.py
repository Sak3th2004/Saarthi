"""Per-caregiver Calendar credentials in AWS Secrets Manager, never local plaintext."""

import hashlib
import json
import re

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from saarthi_mcp.calendar_tokens import TokenStoreError


class SecretsManagerTokenStore:
    def __init__(self, prefix: str, region: str, *, client=None):
        if not re.fullmatch(r"saarthi/[A-Za-z0-9_-]+/calendar", prefix):
            raise ValueError("Use a dedicated saarthi/<deployment>/calendar secret prefix.")
        self.prefix = prefix
        self.client = client or boto3.client("secretsmanager", region_name=region, config=Config(
            connect_timeout=5, read_timeout=10, retries={"mode": "standard", "total_max_attempts": 2},
        ))

    def _name(self, subject):
        if not isinstance(subject, str) or not subject or len(subject) > 256:
            raise TokenStoreError("Calendar connection requires a signed-in caregiver.")
        return self.prefix + "/" + hashlib.sha256(subject.encode()).hexdigest()

    def load(self, *, subject=None):
        name = self._name(subject)
        try:
            data = self.client.get_secret_value(SecretId=name)["SecretString"]
            if len(data.encode()) > 65536:
                raise ValueError("Oversize payload")
            payload = json.loads(data)
            if not isinstance(payload, dict):
                raise ValueError("Invalid payload")
            return payload
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
                return None
            raise TokenStoreError("Calendar credentials could not be read securely.") from None
        except Exception:
            raise TokenStoreError("Calendar credentials could not be read securely.") from None

    def save(self, payload, *, subject=None):
        name = self._name(subject)
        try:
            if not isinstance(payload, dict):
                raise ValueError("Invalid payload")
            data = json.dumps(payload, allow_nan=False, separators=(",", ":"))
            if len(data.encode()) > 65536:
                raise ValueError("Oversize payload")
            # Content hash makes a retry of an identical write use the same version.
            version = hashlib.sha256(data.encode()).hexdigest()
            try:
                self.client.put_secret_value(SecretId=name, SecretString=data, ClientRequestToken=version)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
                    raise
                try:
                    self.client.create_secret(Name=name, SecretString=data, ClientRequestToken=version,
                                              Tags=[{"Key": "project", "Value": "saarthi"}])
                except ClientError as create_error:
                    if create_error.response.get("Error", {}).get("Code") != "ResourceExistsException":
                        raise
                    self.client.put_secret_value(SecretId=name, SecretString=data, ClientRequestToken=version)
        except Exception:
            raise TokenStoreError("Calendar credentials could not be saved securely.") from None
