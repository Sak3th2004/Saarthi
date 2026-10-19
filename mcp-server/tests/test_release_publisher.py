"""Keep CI image publishing separate from runtime credentials and deployment."""
import json
from pathlib import Path


def test_publisher_is_bound_to_main_and_cannot_read_data_or_deploy():
    template = json.loads((Path(__file__).resolve().parents[2] / 'infra/github-release.json').read_text())
    role = template['Resources']['Publisher']['Properties']
    trust = role['AssumeRolePolicyDocument']['Statement']
    assert len(trust) == 1
    assert trust[0]['Action'] == 'sts:AssumeRoleWithWebIdentity'
    assert trust[0]['Condition']['StringEquals'] == {
        'token.actions.githubusercontent.com:aud': 'sts.amazonaws.com',
        'token.actions.githubusercontent.com:sub': 'repo:Sak3th2004/Saarthi:ref:refs/heads/main',
    }
    assert not role.get('ManagedPolicyArns')
    permissions = role['Policies'][0]['PolicyDocument']['Statement']
    assert len(role['Policies']) == 1 and len(permissions) == 2
    assert permissions[0] == {'Effect': 'Allow', 'Action': 'ecr:GetAuthorizationToken', 'Resource': '*'}
    assert set(permissions[1]['Action']) == {
        'ecr:BatchCheckLayerAvailability', 'ecr:InitiateLayerUpload', 'ecr:UploadLayerPart',
        'ecr:CompleteLayerUpload', 'ecr:PutImage',
    }
    assert permissions[1]['Resource']['Fn::Sub'].endswith(':repository/saarthi-notebook')
