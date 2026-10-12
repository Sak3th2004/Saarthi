"""Optional live SES send — runs only with explicit owner-approved env."""

from __future__ import annotations

import os

import pytest

from saarthi_mcp.ses_delivery import SesMailer, SesSettings


@pytest.mark.skipif(os.getenv("SAARTHI_RUN_SES_TESTS") != "1", reason="Live email requires explicit SAARTHI_RUN_SES_TESTS=1 approval.")
@pytest.mark.skipif(not os.getenv("SES_SENDER_EMAIL"), reason="Set SES_SENDER_EMAIL for live SES test.")
@pytest.mark.skipif(not os.getenv("SES_APPROVED_RECIPIENT"), reason="Set SES_APPROVED_RECIPIENT for live SES test.")
def test_live_ses_to_approved_recipient():
    settings = SesSettings.from_env()
    assert settings is not None
    assert settings.approved_live_recipient
    mailer = SesMailer(settings)
    message_id = mailer.send(
        to_addresses=[settings.approved_live_recipient],
        subject="Saarthi SES smoke test",
        body_text="Harmless connectivity check from pytest. No household data included.",
    )
    assert message_id
