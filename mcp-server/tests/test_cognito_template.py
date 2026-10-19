"""The deployed permission must belong to the access token's target API."""
import json
from pathlib import Path
import re

import pytest

from saarthi_mcp.auth import CognitoSettings


def _load_template():
    return json.loads((Path(__file__).resolve().parents[2] / "infra/cognito.json").read_text())


def test_family_mfa_stays_required_unless_explicitly_configured():
    template = _load_template()
    assert template['Parameters']['MfaPolicy']['Default'] == 'ON'
    assert template['Parameters']['MfaPolicy']['AllowedValues'] == ['ON', 'OPTIONAL']
    assert template['Resources']['HouseholdPool']['Properties']['MfaConfiguration'] == {'Ref': 'MfaPolicy'}


def _resolve(value, values):
    if isinstance(value, str):
        return value
    if "Fn::If" in value:
        condition, when_true, when_false = value["Fn::If"]
        chosen = when_true if values.get(condition) else when_false
        if isinstance(chosen, list):
            return [_resolve(item, values) for item in chosen]
        return _resolve(chosen, values)
    if "Fn::Sub" in value:
        return re.sub(r"\$\{([^}]+)\}", lambda match: values[match[1]], value["Fn::Sub"])
    raise AssertionError(f"unsupported intrinsic: {value!r}")


@pytest.mark.parametrize("origin", ["http://localhost:5173", "https://family.example"])
def test_cloud_scopes_match_backend_audience(origin):
    template = _load_template()
    resources = template["Resources"]
    values = {"DashboardOrigin": origin, "HasProductionOrigin": False}

    resource = _resolve(resources["NotebookScope"]["Properties"]["Identifier"], values)
    values["NotebookScope"] = resource
    audience = _resolve(template["Outputs"]["ResourceUrl"]["Value"], values)
    settings = CognitoSettings("us-east-1", "us-east-1_example", "client123", audience,
                               frozenset({"00000000-0000-0000-0000-000000000001"}))
    allowed = _resolve(resources["DashboardClient"]["Properties"]["AllowedOAuthScopes"], values)
    assert resource == audience
    assert allowed == ["openid", settings.scope]
    assert resources["NotebookScope"]["Properties"]["Scopes"][0]["ScopeName"] == "notebook"
    callbacks = _resolve(resources["DashboardClient"]["Properties"]["CallbackURLs"], values)
    assert callbacks == [f"{origin}/auth/callback"]


def test_production_origin_adds_urls_without_dropping_localhost():
    template = _load_template()
    resources = template["Resources"]
    local = "http://localhost:5173"
    prod = "https://main.d123.amplifyapp.com"
    values = {
        "DashboardOrigin": local,
        "ProductionDashboardOrigin": prod,
        "HasProductionOrigin": True,
        "NotebookScope": f"{local}/mcp",
        "ProductionNotebookScope": f"{prod}/mcp",
    }

    assert resources["ProductionNotebookScope"]["Condition"] == "HasProductionOrigin"
    callbacks = _resolve(resources["DashboardClient"]["Properties"]["CallbackURLs"], values)
    logouts = _resolve(resources["DashboardClient"]["Properties"]["LogoutURLs"], values)
    scopes = _resolve(resources["DashboardClient"]["Properties"]["AllowedOAuthScopes"], values)

    assert callbacks == [f"{local}/auth/callback", f"{prod}/auth/callback"]
    assert logouts == [f"{local}/", f"{prod}/"]
    assert scopes == ["openid", f"{local}/mcp/notebook", f"{prod}/mcp/notebook"]
    assert template["Resources"]["HouseholdPool"]["DeletionPolicy"] == "Retain"
