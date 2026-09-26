"""Route validated requests without giving a model authority over their arguments."""

from collections.abc import Callable
from threading import Lock
from time import monotonic
from typing import Any

from botocore.config import Config
from strands import Agent, tool
from strands.hooks import BeforeModelCallEvent, HookRegistry
from strands.models import BedrockModel, Model


_SPECIALISTS = {
    "get_medication_schedule": "MedGuardian",
    "log_dose": "MedGuardian",
    "query_memory": "MedGuardian",
    "book_appointment": "Appointments",
    "list_appointments": "Appointments",
}
_GUARDRAILS = (
    "Never diagnose, recommend treatment, calculate doses, or give medical advice. "
    "Only the saved facts returned by the application are authoritative. "
    "Health questions require 'talk to your doctor'. Never contact people or purchase anything. "
    "Do not invent facts, change a person, change arguments, or repeat a request. "
)


class AgentExecutionError(RuntimeError):
    """A safe failure, including whether a write might already have happened.

    Callers must not automatically retry when action_attempted is True. If an audit
    write fails after the action, completed_result preserves its confirmed result.
    """

    def __init__(
        self, reason: str, *, action_attempted: bool = False,
        completed_result: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"Agent execution failed: {reason}.")
        self.reason = reason
        self.action_attempted = action_attempted
        self.completed_result = completed_result


class _CallBudget:
    """Stop a misbehaving model after three calls per agent invocation."""

    def __init__(self) -> None:
        self.calls = 0

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(BeforeModelCallEvent, self.before_model)

    def before_model(self, event: BeforeModelCallEvent) -> None:
        if self.calls >= 3:
            raise AgentExecutionError("model_call_budget_exceeded")
        self.calls += 1


def _usage_snapshot(agent: Agent | None, budget: _CallBudget, role: str, finished: bool) -> dict[str, Any]:
    """Keep provider-reported counts without making missing telemetry look like free inference."""
    counts = {"inputTokens": None, "outputTokens": None, "totalTokens": None}
    try:
        usage = agent.event_loop_metrics.accumulated_usage if agent is not None else {}
        values = {key: usage.get(key) for key in counts}
        if all(type(value) is int and value >= 0 for value in values.values()) and values["totalTokens"] > 0:
            counts = values
    except Exception:
        # Observability must not lose or repeat an application action.
        pass
    return {
        "role": role,
        "model_calls_attempted": budget.calls,
        "invocation_completed": finished,
        "reported_tokens": counts,
    }


class CareOrchestrator:
    """Fresh router and specialist agents per request, with a single bound action.

    model_factory(role) optionally supplies a Strands Model for offline tests;
    role is 'Orchestrator', 'MedGuardian', or 'Appointments'.
    """

    def __init__(
        self, model_id: str, region: str,
        model_factory: Callable[[str], Model] | None = None,
    ) -> None:
        if not model_id.strip() or not region.strip():
            raise ValueError("model_id and region must be nonempty")
        self.model_id = model_id
        self.region = region
        self._model_factory = model_factory

    def _model(self, role: str) -> Model:
        if self._model_factory is not None:
            return self._model_factory(role)
        return BedrockModel(
            model_id=self.model_id,
            region_name=self.region,
            max_tokens=512,
            temperature=0,
            boto_client_config=Config(
                connect_timeout=5, read_timeout=30,
                retries={"mode": "standard", "total_max_attempts": 2},
            ),
        )

    def execute(
        self, operation: str, arguments: dict[str, Any],
        action: Callable[[], dict[str, Any]],
        record: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        """Execute a validated callback, then record a metadata-only outcome.

        Arguments remain application-owned: neither model receives them, and both
        tool schemas take no arguments. The caller must bind validated values in
        action, and bind the correct person in record. The exact action result is
        returned; generated narration never becomes a care fact.
        """
        specialist_name = _SPECIALISTS.get(operation)
        attempted = False
        completed: dict[str, Any] | None = None
        action_failed = False
        model_failed = False
        action_lock, delegate_lock = Lock(), Lock()
        delegated = False
        stages: list[str] = []
        usage: list[dict[str, Any]] = []
        started = monotonic()

        @tool
        def execute_request() -> dict[str, str]:
            """Execute the already validated request once, without changing its arguments."""
            nonlocal attempted, completed, action_failed
            with action_lock:
                if not attempted:
                    attempted = True
                    stages.append("execute_request")
                    try:
                        result = action()
                        if not isinstance(result, dict):
                            raise TypeError("Action must return a dict")
                        completed = result
                    except Exception:
                        # An exception may follow a committed write. Never retry it,
                        # and never leak its potentially sensitive text to the model.
                        action_failed = True
                return {"status": "failed" if action_failed else "completed"}

        @tool
        def delegate_request() -> dict[str, str]:
            """Route the already validated request to its authorized specialist once."""
            nonlocal delegated, model_failed
            with delegate_lock:
                if not delegated:
                    delegated = True
                    stages.append(specialist_name)
                    specialist = None
                    specialist_budget = _CallBudget()
                    specialist_finished = False
                    try:
                        specialist = Agent(
                            name=specialist_name,
                            model=self._model(specialist_name),
                            tools=[execute_request],
                            system_prompt=(
                                f"You are Saarthi's {specialist_name}. " + _GUARDRAILS
                                + "Call execute_request exactly once to process the validated operation. "
                                "Arguments are already bound by the application. After the tool, stop."
                            ),
                            callback_handler=None,
                            hooks=[specialist_budget],
                            retry_strategy=None,
                        )
                        specialist(f"Process the validated operation: {operation}.")
                        specialist_finished = True
                    except Exception:
                        model_failed = True
                    finally:
                        usage.append(_usage_snapshot(
                            specialist, specialist_budget, specialist_name, specialist_finished,
                        ))
                return {"status": "completed" if completed is not None else "failed"}

        reason = "unsupported_operation"
        if specialist_name is not None:
            router = None
            router_budget = _CallBudget()
            router_finished = False
            try:
                stages.append("Orchestrator")
                router = Agent(
                    name="Orchestrator",
                    model=self._model("Orchestrator"),
                    tools=[delegate_request],
                    system_prompt=(
                        "You are Saarthi's router. You never do domain work. " + _GUARDRAILS
                        + f"The operation is assigned to {specialist_name}. "
                        "Call delegate_request exactly once, then stop."
                    ),
                    callback_handler=None,
                    hooks=[router_budget],
                    retry_strategy=None,
                )
                router(f"Route validated operation {operation} to {specialist_name}.")
                router_finished = True
            except Exception:
                model_failed = True
            finally:
                usage.insert(0, _usage_snapshot(router, router_budget, "Orchestrator", router_finished))
            if action_failed:
                reason = "action_failed_outcome_uncertain"
            elif completed is not None:
                reason = "model_response_failed" if model_failed else "validated_action_completed"
            else:
                reason = "model_failed" if model_failed else "action_not_executed"

        outcome = {
            "operation": operation if specialist_name else "unsupported",
            "specialist": specialist_name or "none",
            "status": "completed" if completed is not None else "failed",
            "reason": reason,
            "stages": stages,
            "model_id": self.model_id,
            "model_usage": usage,
            "elapsed_ms": round((monotonic() - started) * 1000),
        }
        try:
            record(outcome)
        except Exception:
            raise AgentExecutionError(
                "audit_failed", action_attempted=attempted, completed_result=completed,
            ) from None
        if completed is not None:
            return completed
        raise AgentExecutionError(reason, action_attempted=attempted) from None
