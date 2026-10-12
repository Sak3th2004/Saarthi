"""Amazon SES email delivery — mockable, no secrets in code."""

from __future__ import annotations

from dataclasses import dataclass
import os
import re


class SesDeliveryError(RuntimeError):
    """Safe failure that never includes message bodies or credentials."""


class SesDeliveryUncertain(SesDeliveryError):
    """The provider may have accepted the request; never retry automatically."""


@dataclass(frozen=True)
class SesSettings:
    sender: str
    region: str
    approved_live_recipient: str | None

    @classmethod
    def from_env(cls) -> SesSettings | None:
        sender = os.getenv("SES_SENDER_EMAIL", "").strip()
        if not sender:
            return None
        region = os.getenv("AWS_REGION", "us-east-1").strip() or "us-east-1"
        approved = os.getenv("SES_APPROVED_RECIPIENT", "").strip() or None
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", sender):
            raise ValueError("SES_SENDER_EMAIL must be a valid email address.")
        if approved and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", approved):
            raise ValueError("SES_APPROVED_RECIPIENT must be a valid email address when set.")
        return cls(sender=sender, region=region, approved_live_recipient=approved)


class SesMailer:
    """Thin SES wrapper; inject a client in tests."""

    def __init__(self, settings: SesSettings, *, client=None):
        self.settings = settings
        self._client = client

    def _client_or_create(self):
        if self._client is not None:
            return self._client
        try:
            import boto3
        except ImportError as exc:
            raise SesDeliveryError("Install boto3 to send email through SES.") from exc
        from botocore.config import Config

        return boto3.client("ses", region_name=self.settings.region,
                            config=Config(retries={"total_max_attempts": 1, "mode": "standard"},
                                          connect_timeout=10, read_timeout=30))

    def send(
        self,
        *,
        to_addresses: list[str],
        subject: str,
        body_text: str,
    ) -> str:
        if not to_addresses:
            raise SesDeliveryError("No verified household recipients to send to.")
        try:
            client = self._client_or_create()
        except Exception as exc:
            raise SesDeliveryError("SES client could not be initialized.") from exc
        try:
            response = client.send_email(
                Source=self.settings.sender,
                Destination={"ToAddresses": to_addresses},
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {"Text": {"Data": body_text, "Charset": "UTF-8"}},
                },
            )
        except Exception as exc:
            from botocore.exceptions import ClientError, NoCredentialsError, PartialCredentialsError, ParamValidationError

            if isinstance(exc, (NoCredentialsError, PartialCredentialsError, ParamValidationError)):
                raise SesDeliveryError("SES request could not be submitted.") from exc
            if isinstance(exc, ClientError) and exc.response.get("Error", {}).get("Code") in {
                "MessageRejected", "MailFromDomainNotVerifiedException", "ConfigurationSetDoesNotExistException",
                "AccountSendingPausedException", "ConfigurationSetSendingPausedException",
                "AccessDenied", "AccessDeniedException", "InvalidClientTokenId", "SignatureDoesNotMatch",
                "InvalidParameterValue", "MissingParameter", "Throttling", "ThrottlingException",
            }:
                raise SesDeliveryError("SES rejected the family message.") from exc
            raise SesDeliveryUncertain("SES acceptance could not be confirmed. Do not resend automatically.") from exc
        message_id = response.get("MessageId")
        if not isinstance(message_id, str) or not message_id:
            raise SesDeliveryUncertain("SES did not return a message identifier.")
        return message_id
