"""The deployed permission must belong to the access token's target API."""
import json
from pathlib import Path
import re

import pytest

from saarthi_mcp.auth import CognitoSettings


@pytest.mark.parametrize("origin", ["http://localhost:5173", "https://family.example"])
def test_cloud_scopes_match_backend_audience(origin):
    template = json.loads((Path(__file__).resolve().parents[2] / "infra/cognito.json").read_text())
    resources = template["Resources"]
    values = {"DashboardOrigin": origin}

    def resolve(value):
        if isinstance(value, str):
            return value
        return re.sub(r"\$\{([^}]+)\}", lambda match: values[match[1]], value["Fn::Sub"])

    resource = resolve(resources["NotebookScope"]["Properties"]["Identifier"])
    values["NotebookScope"] = resource
    audience = resolve(template["Outputs"]["ResourceUrl"]["Value"])
    settings = CognitoSettings("us-east-1", "us-east-1_example", "client123", audience,
                               frozenset({"00000000-0000-0000-0000-000000000001"}))
    allowed = [resolve(scope) for scope in resources["DashboardClient"]["Properties"]["AllowedOAuthScopes"]]
    assert resource == audience
    assert allowed == ["openid", settings.scope]
    assert resources["NotebookScope"]["Properties"]["Scopes"][0]["ScopeName"] == "notebook"
