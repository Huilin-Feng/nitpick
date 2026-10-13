"""OpenAIClient.chat with a fake SDK client: no network, no API key."""

from types import SimpleNamespace

from nitpick.llm import ChatTurn, OpenAIClient, ToolCall


class FakeCompletions:
    def __init__(self, message, usage=(100, 20)):
        self.message = message
        self.usage = SimpleNamespace(prompt_tokens=usage[0], completion_tokens=usage[1])
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return SimpleNamespace(choices=[SimpleNamespace(message=self.message)], usage=self.usage)


def make_client(message):
    completions = FakeCompletions(message)
    fake_sdk = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return OpenAIClient(api_key="unused", client=fake_sdk), completions


def raw_call(call_id, name, arguments):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


def test_chat_parses_tool_calls_and_requires_a_tool():
    message = SimpleNamespace(content=None, tool_calls=[raw_call("c1", "read_file", '{"path": "app/api.py"}')])
    client, completions = make_client(message)

    turn = client.chat([{"role": "user", "content": "hi"}], tools=[{"type": "function"}])

    assert turn.tool_calls == [ToolCall("c1", "read_file", {"path": "app/api.py"}, '{"path": "app/api.py"}')]
    assert completions.last_kwargs["tool_choice"] == "required"
    assert completions.last_kwargs["tools"] == [{"type": "function"}]


def test_chat_records_usage():
    client, _ = make_client(SimpleNamespace(content="ok", tool_calls=None))
    client.chat([], tools=[])
    client.chat([], tools=[])
    assert client.usage == {"calls": 2, "prompt_tokens": 200, "completion_tokens": 40}
    assert client.total_tokens == 240


def test_invalid_or_non_object_arguments_become_none():
    message = SimpleNamespace(content=None, tool_calls=[
        raw_call("c1", "read_file", "{not json"),
        raw_call("c2", "read_file", "[1, 2]"),
        raw_call("c3", "submit_review", ""),
    ])
    client, _ = make_client(message)
    turn = client.chat([], tools=[])
    assert [c.arguments for c in turn.tool_calls] == [None, None, {}]
    assert turn.tool_calls[0].raw_arguments == "{not json"


def test_to_message_keeps_raw_arguments_for_history():
    turn = ChatTurn(content=None, tool_calls=[ToolCall("c1", "search_code", {"query": "x"}, '{"query": "x"}')])
    assert turn.to_message() == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "search_code", "arguments": '{"query": "x"}'}}
        ],
    }


def test_to_message_without_tool_calls():
    assert ChatTurn(content="done").to_message() == {"role": "assistant", "content": "done"}
