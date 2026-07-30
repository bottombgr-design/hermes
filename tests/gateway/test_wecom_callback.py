"""Tests for the WeCom callback-mode adapter."""

import asyncio
from xml.etree import ElementTree as ET

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.wecom.callback_adapter import WecomCallbackAdapter
from plugins.platforms.wecom.wecom_crypto import WXBizMsgCrypt


def _app(name="test-app", corp_id="ww1234567890", agent_id="1000002"):
    return {
        "name": name,
        "corp_id": corp_id,
        "corp_secret": "test-secret",
        "agent_id": agent_id,
        "token": "test-callback-token",
        "encoding_aes_key": "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
    }


def _config(apps=None):
    return PlatformConfig(
        enabled=True,
        extra={"mode": "callback", "host": "127.0.0.1", "port": 0, "apps": apps or [_app()]},
    )


class TestWecomCrypto:
    def test_roundtrip_encrypt_decrypt(self):
        app = _app()
        crypt = WXBizMsgCrypt(app["token"], app["encoding_aes_key"], app["corp_id"])
        encrypted_xml = crypt.encrypt(
            "<xml><Content>hello</Content></xml>", nonce="nonce123", timestamp="123456",
        )
        root = ET.fromstring(encrypted_xml)
        decrypted = crypt.decrypt(
            root.findtext("MsgSignature", default=""),
            root.findtext("TimeStamp", default=""),
            root.findtext("Nonce", default=""),
            root.findtext("Encrypt", default=""),
        )
        assert b"<Content>hello</Content>" in decrypted


class TestWecomCallbackEventConstruction:
    def test_build_event_extracts_text_message(self):
        adapter = WecomCallbackAdapter(_config())
        xml_text = """
        <xml>
          <ToUserName>ww1234567890</ToUserName>
          <FromUserName>zhangsan</FromUserName>
          <CreateTime>1710000000</CreateTime>
          <MsgType>text</MsgType>
          <Content>\u4f60\u597d</Content>
          <MsgId>123456789</MsgId>
        </xml>
        """
        event = adapter._build_event(_app(), xml_text)
        assert event is not None
        assert event.source is not None
        assert event.source.user_id == "zhangsan"
        assert event.source.chat_id == "ww1234567890:zhangsan"
        assert event.message_id == "123456789"
        assert event.text == "\u4f60\u597d"


class TestWecomCallbackRouting:

    @pytest.mark.asyncio
    async def test_send_selects_correct_app_for_scoped_chat_id(self):
        apps = [
            _app(name="corp-a", corp_id="corpA", agent_id="1001"),
            _app(name="corp-b", corp_id="corpB", agent_id="2002"),
        ]
        adapter = WecomCallbackAdapter(_config(apps=apps))
        adapter._user_app_map["corpB:alice"] = "corp-b"
        adapter._access_tokens["corp-b"] = {"token": "tok-b", "expires_at": 9999999999}

        calls = {}

        class FakeResponse:
            def json(self):
                return {"errcode": 0, "msgid": "ok1"}

        class FakeClient:
            async def post(self, url, json):
                calls["url"] = url
                calls["json"] = json
                return FakeResponse()

        adapter._http_client = FakeClient()
        result = await adapter.send("corpB:alice", "hello")

        assert result.success is True
        assert calls["json"]["touser"] == "alice"
        assert calls["json"]["agentid"] == 2002
        assert "tok-b" in calls["url"]


class TestWecomCallbackSendTokenRefresh:
    @pytest.mark.asyncio
    async def test_send_retries_with_fresh_token_on_errcode_40001(self):
        """errcode=40001 must evict the cached token, refresh, and retry once."""
        adapter = WecomCallbackAdapter(_config())
        adapter._access_tokens["test-app"] = {"token": "stale", "expires_at": 9999999999}
        adapter._user_app_map["ww1234567890:alice"] = "test-app"

        responses = [
            {"errcode": 40001, "errmsg": "invalid credential"},
            {"errcode": 0, "msgid": "msg-ok"},
        ]
        post_calls = []

        class FakeClient:
            async def post(self, url, json=None, **kw):
                post_calls.append(url)

                class R:
                    def json(inner):
                        return responses[len(post_calls) - 1]
                return R()

            async def get(self, url, params=None, **kw):
                class R:
                    def json(inner):
                        return {"errcode": 0, "access_token": "fresh", "expires_in": 7200}
                return R()

        adapter._http_client = FakeClient()
        result = await adapter.send("ww1234567890:alice", "hello")

        assert result.success is True
        assert result.message_id == "msg-ok"
        assert len(post_calls) == 2
        assert "fresh" in post_calls[1]
        assert adapter._access_tokens["test-app"]["token"] == "fresh"


class TestWecomCallbackPollLoop:
    @pytest.mark.asyncio
    async def test_poll_loop_dispatches_handle_message(self, monkeypatch):
        adapter = WecomCallbackAdapter(_config())
        calls = []

        async def fake_handle_message(event):
            calls.append(event.text)

        monkeypatch.setattr(adapter, "handle_message", fake_handle_message)
        event = adapter._build_event(
            _app(),
            """
            <xml>
              <ToUserName>ww1234567890</ToUserName>
              <FromUserName>lisi</FromUserName>
              <CreateTime>1710000000</CreateTime>
              <MsgType>text</MsgType>
              <Content>test</Content>
              <MsgId>m2</MsgId>
            </xml>
            """,
        )
        task = asyncio.create_task(adapter._poll_loop())
        await adapter._message_queue.put(event)
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert calls == ["test"]


class TestWecomCallbackBodySizeLimit:
    """Pre-auth oversized-body rejection (DoS hardening, PR #10192)."""

    def _request(self, body_bytes):
        from unittest.mock import Mock

        from aiohttp import StreamReader
        from aiohttp.test_utils import make_mocked_request

        protocol = Mock(_reading_paused=False)
        reader = StreamReader(protocol=protocol, limit=2 ** 20)
        reader.feed_data(body_bytes)
        reader.feed_eof()
        return make_mocked_request(
            "POST", "/wecom/callback?msg_signature=s&timestamp=1&nonce=n",
            payload=reader,
        )

    @pytest.mark.asyncio
    async def test_oversized_body_rejected_with_413(self):
        from plugins.platforms.wecom.callback_adapter import _MAX_BODY

        adapter = WecomCallbackAdapter(_config())
        oversized = b"<xml>" + b"A" * (_MAX_BODY + 1) + b"</xml>"
        response = await adapter._handle_callback(self._request(oversized))
        assert response.status == 413




class TestWecomCallbackStandaloneSend:
    """standalone_send must deliver without a live runner or port bind."""

    @pytest.mark.asyncio
    async def test_delivers_without_binding_callback_port(self, monkeypatch):
        import plugins.platforms.wecom.callback_adapter as ca

        calls = {"post": [], "closed": False}

        class FakeResponse:
            def __init__(self, data):
                self._data = data

            def json(self):
                return self._data

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def get(self, url, params=None):
                return FakeResponse(
                    {"errcode": 0, "access_token": "tok-standalone", "expires_in": 7200}
                )

            async def post(self, url, json=None):
                calls["post"].append((url, json))
                return FakeResponse({"errcode": 0, "msgid": "m-1"})

            async def aclose(self):
                calls["closed"] = True

        monkeypatch.setattr(ca.httpx, "AsyncClient", FakeClient)

        async def _no_connect(self, **kwargs):
            raise AssertionError("standalone_send must not call connect()")

        monkeypatch.setattr(ca.WecomCallbackAdapter, "connect", _no_connect)

        result = await ca.standalone_send(_config(), "ww1234567890:alice", "hello")

        assert result == {
            "success": True,
            "platform": "wecom_callback",
            "chat_id": "ww1234567890:alice",
            "message_id": "m-1",
        }
        url, payload = calls["post"][0]
        assert payload["touser"] == "alice"
        assert "tok-standalone" in url
        assert calls["closed"] is True

    @pytest.mark.asyncio
    async def test_error_when_no_apps_configured(self):
        from plugins.platforms.wecom.callback_adapter import standalone_send

        result = await standalone_send(
            PlatformConfig(enabled=True, extra={}), "alice", "hi"
        )

        assert "no callback apps configured" in result["error"]

    @pytest.mark.asyncio
    async def test_send_failure_surfaces_as_error_dict(self, monkeypatch):
        import plugins.platforms.wecom.callback_adapter as ca

        class FakeResponse:
            def __init__(self, data):
                self._data = data

            def json(self):
                return self._data

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def get(self, url, params=None):
                return FakeResponse(
                    {"errcode": 0, "access_token": "tok", "expires_in": 7200}
                )

            async def post(self, url, json=None):
                return FakeResponse({"errcode": 60011, "errmsg": "no privilege"})

            async def aclose(self):
                pass

        monkeypatch.setattr(ca.httpx, "AsyncClient", FakeClient)

        result = await ca.standalone_send(_config(), "alice", "hello")

        assert result["error"].startswith("WeCom Callback send failed")
        assert "60011" in result["error"]

    def test_register_supplies_standalone_sender_fn(self):
        from plugins.platforms.wecom import adapter as wecom_adapter
        from plugins.platforms.wecom.callback_adapter import standalone_send

        registered = {}

        class Ctx:
            def register_platform(self, **kwargs):
                registered[kwargs["name"]] = kwargs

        wecom_adapter.register(Ctx())

        assert registered["wecom_callback"]["standalone_sender_fn"] is standalone_send
