"""Threading regressions for bash-agent OpenAI calls."""

from __future__ import annotations

from types import SimpleNamespace
import threading

from saas_bench.agents.bash_agent.agent import BashAgent, Message


class FakeResponses:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            usage=None,
            output=[
                SimpleNamespace(
                    type="function_call",
                    name="bash",
                    arguments='{"command":"true"}',
                    call_id="call_1",
                )
            ],
            model_dump=lambda: {"ok": True},
        )


class FakeOpenAIClient:
    base_url = "https://api.openai.com/v1"

    def __init__(self):
        self.responses = FakeResponses()


def test_openai_responses_call_uses_request_timeout_in_worker_thread():
    client = FakeOpenAIClient()
    agent = BashAgent(
        tool_descriptions=[
            {
                "name": "bash",
                "description": "Run a shell command",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            }
        ],
        client=client,
        model="gpt-5.5",
        reasoning_effort="none",
    )
    agent.conversation.append(Message(role="user", content="Take an action."))

    result_holder = {}

    def call_agent():
        result_holder["action"] = agent._call_openai_responses()

    thread = threading.Thread(target=call_agent)
    thread.start()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert result_holder["action"].tool == "bash"
    assert result_holder["action"].arguments == {"command": "true"}
    assert client.responses.kwargs["timeout"] == 600
