"""Mention rendering: the agent must be able to tell who was tagged.

WhatsApp puts mentions in the body as opaque numeric ids and carries the
identities out-of-band. The old behaviour stripped only the bot's own mention
and left every other id as a bare number, which inverted the signal the agent
reads: a message addressed to the bot lost its marker, while a message
addressed to a human kept an ambiguous "@<digits>".

Observed in production before this fix: the agent replied to 7/7 group
messages that tagged other people and stayed silent on the one message that
actually tagged it.

These are behaviour contracts about the *relationship* between the mention
metadata and the rendered text, not snapshots of exact wording.
"""

from unittest.mock import AsyncMock

from gateway.config import Platform, PlatformConfig


BOT_LID = "142348218536170"
JESSICA_LID = "144492044791824"
KEVIN_LID = "55869572161642"


def _make_adapter():
    from plugins.platforms.whatsapp.adapter import WhatsAppAdapter

    adapter = object.__new__(WhatsAppAdapter)
    adapter.platform = Platform.WHATSAPP
    adapter.config = PlatformConfig(enabled=True, extra={})
    adapter._message_handler = AsyncMock()
    adapter._mention_patterns = []
    return adapter


def _msg(body, mentioned_ids=None, mentioned_names=None, **overrides):
    data = {
        "isGroup": True,
        "body": body,
        "chatId": "120363400236163214@g.us",
        "senderId": f"{KEVIN_LID}@lid",
        "senderName": "Kevin",
        "mentionedIds": list(mentioned_ids or []),
        "mentionedNames": dict(mentioned_names or {}),
        "botIds": [f"{BOT_LID}@lid", f"{BOT_LID}@s.whatsapp.net"],
        "quotedParticipant": "",
    }
    data.update(overrides)
    return data


def test_tagging_another_person_is_not_rendered_as_self():
    """The regression: Kevin tags Jessica, agent must not read it as its own."""
    adapter = _make_adapter()
    data = _msg(
        f"@{JESSICA_LID} cuando Aldo te explicaba lo que es carrear",
        mentioned_ids=[f"{JESSICA_LID}@lid"],
        mentioned_names={JESSICA_LID: "Jessica"},
    )

    rendered = adapter._clean_bot_mention_text(data["body"], data)

    assert "Jessica" in rendered
    # The raw id must not survive — that is what the agent misread as "me".
    assert JESSICA_LID not in rendered
    assert adapter._MENTION_SELF_LABEL not in rendered


def test_tagging_the_bot_is_rendered_as_self():
    adapter = _make_adapter()
    data = _msg(f"@{BOT_LID} cuantos niveles tiene el juego?",
                mentioned_ids=[f"{BOT_LID}@lid"])

    rendered = adapter._clean_bot_mention_text(data["body"], data)

    assert adapter._MENTION_SELF_LABEL in rendered
    assert BOT_LID not in rendered


def test_bot_and_human_tagged_together_keeps_both_distinguishable():
    """Multi-tag: being tagged alongside someone must not erase either party."""
    adapter = _make_adapter()
    data = _msg(
        f"@{BOT_LID} @{JESSICA_LID} a que hora caen?",
        mentioned_ids=[f"{BOT_LID}@lid", f"{JESSICA_LID}@lid"],
        mentioned_names={JESSICA_LID: "Jessica"},
    )

    rendered = adapter._clean_bot_mention_text(data["body"], data)

    assert adapter._MENTION_SELF_LABEL in rendered
    assert "Jessica" in rendered
    assert JESSICA_LID not in rendered
    assert BOT_LID not in rendered


def test_unresolved_mention_is_marked_as_another_person():
    """Name lookup can fail; it must degrade to 'not me', never to silence."""
    adapter = _make_adapter()
    unknown = "99988877766655"
    data = _msg(f"@{unknown} vienes?", mentioned_ids=[f"{unknown}@lid"])

    rendered = adapter._clean_bot_mention_text(data["body"], data)

    assert adapter._MENTION_OTHER_SUFFIX.strip() in rendered
    assert adapter._MENTION_SELF_LABEL not in rendered


def test_message_without_mentions_is_unchanged():
    adapter = _make_adapter()
    data = _msg("ya no estara el chino que ayuda a mancos")

    assert adapter._clean_bot_mention_text(data["body"], data) == data["body"]


def test_rendering_never_empties_a_mention_only_message():
    """A body that is only a mention must stay non-empty.

    Empty bodies are dropped upstream as 'no content', so a bare '@bot' ping
    would vanish instead of waking the agent.
    """
    adapter = _make_adapter()
    data = _msg(f"@{BOT_LID}", mentioned_ids=[f"{BOT_LID}@lid"])

    rendered = adapter._clean_bot_mention_text(data["body"], data)

    assert rendered.strip()


def test_mention_detection_still_gates_on_bot_identity():
    """The existing ingestion gate must keep working after the rewrite."""
    adapter = _make_adapter()

    tagged_bot = _msg("hola", mentioned_ids=[f"{BOT_LID}@lid"])
    tagged_human = _msg("hola", mentioned_ids=[f"{JESSICA_LID}@lid"])

    assert adapter._message_mentions_bot(tagged_bot) is True
    assert adapter._message_mentions_bot(tagged_human) is False


def test_sender_name_resolves_a_self_referential_tag():
    """Fallback path: no group metadata, but the sender tagged themselves."""
    adapter = _make_adapter()
    data = _msg(
        f"@{KEVIN_LID} yo mismo",
        mentioned_ids=[f"{KEVIN_LID}@lid"],
        mentioned_names={},
    )

    rendered = adapter._clean_bot_mention_text(data["body"], data)

    assert "Kevin" in rendered
    assert KEVIN_LID not in rendered
