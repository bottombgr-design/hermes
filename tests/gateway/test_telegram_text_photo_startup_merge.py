import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionSource, build_session_key
from plugins.platforms.telegram.adapter import TelegramAdapter


def _source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="273403055",
        chat_type="dm",
        user_id="273403055",
    )


def _text_event(source: SessionSource) -> MessageEvent:
    return MessageEvent(
        text="Is this a real study?",
        message_type=MessageType.TEXT,
        source=source,
    )


def _photo_event(source: SessionSource, path: str = "/tmp/late-photo.jpg") -> MessageEvent:
    return MessageEvent(
        text="",
        message_type=MessageType.PHOTO,
        source=source,
        media_urls=[path],
        media_types=["image/jpeg"],
    )


def _make_adapter() -> TelegramAdapter:
    adapter = TelegramAdapter(
        PlatformConfig(enabled=True, token="test-token")
    )
    adapter._apply_topic_recovery = lambda _event: None
    return adapter


def _make_runner(adapter: TelegramAdapter) -> GatewayRunner:
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(enabled=True, token="test-token")
        }
    )
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._profile_adapters = {}
    runner._adapter_for_source = lambda _source: adapter
    return runner


@pytest.mark.asyncio
async def test_startup_text_yields_for_photo_registration_then_merges_downloaded_photo():
    """A photo handler may register one loop turn after text startup checks pending."""
    source = _source()
    session_key = build_session_key(source)
    adapter = _make_adapter()
    runner = _make_runner(adapter)

    async def register_and_finish_photo() -> None:
        await asyncio.sleep(0)
        adapter._media_downloads_in_progress_by_session[session_key] = 1
        await asyncio.sleep(0.02)
        batch_key = f"{session_key}:photo-burst"
        adapter._pending_photo_batches[batch_key] = _photo_event(source)
        adapter._media_downloads_in_progress_by_session.pop(session_key, None)

    producer = asyncio.create_task(register_and_finish_photo())
    event = await runner._merge_telegram_startup_image_followups(
        _text_event(source),
        source,
        session_key,
    )
    await producer

    assert event.message_type == MessageType.PHOTO
    assert event.text == "Is this a real study?"
    assert event.media_urls == ["/tmp/late-photo.jpg"]
    assert adapter._pending_photo_batches == {}


@pytest.mark.asyncio
async def test_startup_text_without_photo_uses_bounded_registration_grace():
    source = _source()
    session_key = build_session_key(source)
    adapter = _make_adapter()
    runner = _make_runner(adapter)
    loop = asyncio.get_running_loop()

    started = loop.time()
    event = await runner._merge_telegram_startup_image_followups(
        _text_event(source), source, session_key
    )
    elapsed = loop.time() - started

    assert event.text == "Is this a real study?"
    assert event.media_urls == []
    assert 0.008 <= elapsed < 0.05


@pytest.mark.asyncio
async def test_startup_text_waits_for_photo_registration_after_real_timer_gap():
    source = _source()
    session_key = build_session_key(source)
    adapter = _make_adapter()
    runner = _make_runner(adapter)

    async def register_after_timer_gap() -> None:
        await asyncio.sleep(0.005)
        adapter._media_downloads_in_progress_by_session[session_key] = 1
        await asyncio.sleep(0.01)
        adapter._pending_photo_batches[f"{session_key}:photo-burst"] = _photo_event(
            source, "/tmp/timer-gap.jpg"
        )
        adapter._media_downloads_in_progress_by_session.pop(session_key, None)

    producer = asyncio.create_task(register_after_timer_gap())
    event = await runner._merge_telegram_startup_image_followups(
        _text_event(source), source, session_key
    )
    await producer

    assert event.media_urls == ["/tmp/timer-gap.jpg"]
    assert adapter._pending_photo_batches == {}


@pytest.mark.asyncio
async def test_startup_merge_does_not_consume_adapter_generic_fifo():
    source = _source()
    session_key = build_session_key(source)
    adapter = _make_adapter()
    runner = _make_runner(adapter)
    unrelated = _photo_event(source, "/tmp/queued-followup.jpg")
    adapter._pending_messages[session_key] = unrelated
    event = await runner._merge_telegram_startup_image_followups(_text_event(source), source, session_key)
    assert event.media_urls == []
    assert adapter._pending_messages[session_key] is unrelated


def test_startup_merge_does_not_consume_existing_media_group_buffer():
    source = _source()
    session_key = build_session_key(source)
    adapter = _make_adapter()
    album_event = _photo_event(source, "/tmp/album-photo.jpg")
    adapter._media_group_events["album-1"] = album_event
    assert adapter.has_startup_image_pending(session_key) is False
    assert adapter.pop_startup_image_event(session_key) is None
    assert adapter._media_group_events["album-1"] is album_event


def test_media_session_key_stamps_secondary_profile_before_download_tracking():
    source = _source()
    source.profile = None
    source.thread_id = "raw-topic"
    adapter = _make_adapter()
    adapter._gateway_profile_name = "coder"
    adapter._apply_topic_recovery = lambda event: setattr(
        event.source, "thread_id", "recovered-topic"
    )
    event = _photo_event(source)

    session_key = adapter._track_media_download_start(event)

    assert session_key == "agent:coder:telegram:dm:273403055:recovered-topic"
    assert event.source.profile == "coder"
    assert event.source.thread_id == "recovered-topic"
    adapter._track_media_download_done(session_key)
    assert adapter._media_downloads_in_progress_by_session == {}


def test_configure_profile_adapter_exposes_profile_to_pre_dispatch_media_tracking():
    adapter = _make_adapter()
    runner = object.__new__(GatewayRunner)
    runner.session_store = object()
    runner._busy_text_mode = "queue"
    runner._handle_active_session_busy_message = None
    runner._handle_reaction_event = None
    runner._recover_telegram_topic_thread_id = lambda _source: None
    runner._make_adapter_auth_check = lambda *_args, **_kwargs: None
    runner._make_profile_fatal_error_handler = lambda *_args, **_kwargs: None
    runner._make_profile_message_handler = lambda _profile: None

    runner._configure_profile_adapter(adapter, "coder", Platform.TELEGRAM)

    assert adapter._gateway_profile_name == "coder"
    assert adapter._session_key_profile == "coder"


@pytest.mark.asyncio
async def test_secondary_profile_adapter_guard_uses_profile_session_key():
    source = _source()
    source.profile = None
    source.thread_id = "raw-topic"
    adapter = _make_adapter()
    adapter._session_key_profile = "coder"
    adapter._apply_topic_recovery = lambda event: setattr(event.source, "thread_id", "recovered-topic")
    captured = {}
    async def busy(_event, session_key):
        captured["key"] = session_key
        return True
    adapter.set_message_handler(AsyncMock())
    adapter.set_busy_session_handler(busy)
    expected = "agent:coder:telegram:dm:273403055:recovered-topic"
    adapter._active_sessions[expected] = asyncio.Event()
    event = _photo_event(source)
    await adapter.handle_message(event)
    assert captured["key"] == expected
    assert event.source.thread_id == "recovered-topic"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["voice", "audio", "video", "document"])
async def test_oversized_media_rejection_does_not_register_startup_download(kind):
    adapter = _make_adapter()
    adapter._is_callback_user_authorized = lambda _user_id, **_kwargs: True
    adapter.handle_message = AsyncMock()
    adapter._max_doc_bytes = 1024
    payload = MagicMock()
    payload.file_size = 2048
    payload.file_name = "large.bin" if kind == "document" else None
    payload.mime_type = "application/octet-stream" if kind == "document" else None
    payload.get_file = AsyncMock(side_effect=AssertionError("oversized media downloaded"))

    msg = MagicMock()
    msg.message_id = 42
    msg.text = ""
    msg.caption = None
    msg.date = None
    msg.photo = None
    msg.voice = payload if kind == "voice" else None
    msg.audio = payload if kind == "audio" else None
    msg.video = payload if kind == "video" else None
    msg.document = payload if kind == "document" else None
    msg.sticker = None
    msg.media_group_id = None
    msg.message_thread_id = None
    msg.chat = SimpleNamespace(
        id=273403055,
        type="private",
        title=None,
        full_name="Test User",
    )
    msg.from_user = SimpleNamespace(id=273403055, full_name="Test User")
    update = SimpleNamespace(message=msg, update_id=123)

    await adapter._handle_media_message(update, None)

    payload.get_file.assert_not_awaited()
    assert adapter._media_downloads_in_progress_by_session == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["photo", "image_document"])
async def test_image_download_tracking_wraps_real_media_handler(kind, monkeypatch):
    adapter = _make_adapter()
    adapter._is_callback_user_authorized = lambda _user_id, **_kwargs: True
    adapter.handle_message = AsyncMock()
    adapter._max_doc_bytes = 1024 * 1024
    file_obj = SimpleNamespace(
        file_path="images/test.jpg",
        download_as_bytearray=AsyncMock(return_value=bytearray(b"image-bytes")),
    )
    payload = MagicMock()
    payload.file_size = 1024
    payload.file_name = "test.jpg"
    payload.mime_type = "image/jpeg"
    payload.get_file = AsyncMock(return_value=file_obj)

    msg = MagicMock()
    msg.message_id = 42
    msg.text = ""
    msg.caption = None
    msg.date = None
    msg.photo = [payload] if kind == "photo" else None
    msg.voice = None
    msg.audio = None
    msg.video = None
    msg.document = payload if kind == "image_document" else None
    msg.sticker = None
    msg.media_group_id = None
    msg.message_thread_id = None
    msg.chat = SimpleNamespace(
        id=273403055,
        type="private",
        title=None,
        full_name="Test User",
    )
    msg.from_user = SimpleNamespace(id=273403055, full_name="Test User")
    update = SimpleNamespace(message=msg, update_id=123)
    observed_counts: list[int] = []

    def fake_cache(_data: bytes, *, ext: str) -> str:
        observed_counts.append(sum(adapter._media_downloads_in_progress_by_session.values()))
        return f"/tmp/cached{ext}"

    monkeypatch.setattr(
        "plugins.platforms.telegram.adapter.cache_image_from_bytes",
        fake_cache,
    )

    await adapter._handle_media_message(update, None)

    assert observed_counts == [1]
    assert adapter._media_downloads_in_progress_by_session == {}
    assert len(adapter._pending_photo_batches) == 1
    for task in adapter._pending_photo_batch_tasks.values():
        task.cancel()
