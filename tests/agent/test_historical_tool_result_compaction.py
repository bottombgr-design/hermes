from agent import agent_runtime_helpers


def test_completed_tool_results_are_ten_words_or_less_but_current_turn_stays_full():
    messages = [
        {"role": "user", "content": "old request"},
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "historical private reasoning must not be replayed",
            "tool_calls": [{
                "id": "old-call",
                "type": "function",
                "function": {"name": "terminal", "arguments": "{\"command\":\"huge old command\"}"},
            }],
        },
        {
            "role": "tool",
            "tool_call_id": "old-call",
            "content": "very long historical output " * 40,
        },
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "new request"},
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "current reasoning remains available",
            "tool_calls": [{
                "id": "new-call",
                "type": "function",
                "function": {"name": "read_file", "arguments": "{\"path\":\"current\"}"},
            }],
        },
        {
            "role": "tool",
            "tool_call_id": "new-call",
            "content": "current turn output must remain complete",
        },
    ]

    compacted = agent_runtime_helpers.compact_historical_tool_results(
        messages,
        current_turn_user_idx=4,
        max_words=10,
    )

    assert len(compacted[2]["content"].split()) <= 10
    assert compacted[2]["tool_call_id"] == "old-call"
    assert compacted[1]["tool_calls"][0]["function"]["arguments"] == "{}"
    assert "reasoning_content" not in compacted[1]
    assert compacted[5]["tool_calls"][0]["function"]["arguments"] == "{\"path\":\"current\"}"
    assert compacted[5]["reasoning_content"] == "current reasoning remains available"
    assert compacted[6]["content"] == "current turn output must remain complete"
    assert messages[2]["content"].startswith("very long historical output")
