# Saarthi agents

Bounded Strands agents for the family notebook. See the [project README](../README.md) for installation, the live application and judge instructions.

## Runtime behavior

The orchestrator routes validated operations to MedGuardian, Appointments, FamilyLoop and Watch. The application binds arguments before model execution and records outcomes in household memory. Specialist tools handle the domain operation; the model cannot change the person or arguments.

Conversation uses a separate bounded read/draft flow. Saved excerpts carry supporting evidence; follow-ups re-read sources. Calendar and email drafts require explicit review and confirmation in the application. No agent prescribes medication, diagnoses, purchases items or autonomously contacts the family.

## Configuration

Install both local packages from the repository root, using the root README's pinned installation commands. Set:

```dotenv
SAARTHI_AGENTS=bedrock
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=us.amazon.nova-pro-v1:0
```

Provide AWS credentials through the SDK credential chain. Model access must work in your account; inference incurs charges. The deployed release uses Nova Pro. AgentCore Runtime/Memory, SMS and background reminders are not part of this release.

`SAARTHI_AGENTS=off` uses direct repository handlers for local development and offline checks. Failures are reported explicitly; the app never fabricates a successful external action.

## Verification

From the root, run `python scripts/check.py` using the configured virtual environment. Add `--neo4j` to exercise the dedicated disposable database and persistence checks. Paid model tests are opt-in.

The [conversation implementation](src/saarthi_agents/conversation.py) and [orchestrator](src/saarthi_agents/orchestrator.py) include the tool boundaries and guardrails; tests live in [tests](tests).
