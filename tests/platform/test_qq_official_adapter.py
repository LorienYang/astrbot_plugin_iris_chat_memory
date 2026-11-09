"""QQ Official Bot WebSocket adapter tests."""

import sys
from dataclasses import dataclass
from types import ModuleType, SimpleNamespace

import pytest

from iris_memory.platform.base import ReplyInfo
from iris_memory.platform.qq_official import QQOfficialAdapter


@pytest.fixture
def image_info_model(monkeypatch):
    @dataclass
    class ImageInfo:
        url: str | None = None
        file_path: str | None = None
        format: str = ""
        size_kb: int = 0
        source: str = "user"
        message_id: str = ""

    image_package = ModuleType("iris_memory.image")
    image_package.__path__ = []
    image_models = ModuleType("iris_memory.image.models")
    image_models.ImageInfo = ImageInfo
    monkeypatch.setitem(sys.modules, "iris_memory.image", image_package)
    monkeypatch.setitem(sys.modules, "iris_memory.image.models", image_models)
    return ImageInfo


def _event(
    *,
    user_id: str = "user-openid",
    nickname: str = "测试用户",
    group_id: str = "",
    group_name: str = "",
    message: list | None = None,
    raw_message=None,
):
    group = (
        SimpleNamespace(group_id=group_id, group_name=group_name) if group_id else None
    )
    message_obj = SimpleNamespace(
        sender=SimpleNamespace(user_id=user_id, nickname=nickname),
        group=group,
        group_id=group_id,
        message=message or [],
        message_id="message-1",
        self_id="bot-openid",
        raw_message=raw_message,
    )
    return SimpleNamespace(message_obj=message_obj)


class TestQQOfficialAdapter:
    def setup_method(self):
        self.adapter = QQOfficialAdapter()

    def test_reads_c2c_user_and_private_session(self):
        event = _event()

        assert self.adapter.get_user_id(event) == "user-openid"
        assert self.adapter.get_user_name(event) == "测试用户"
        assert self.adapter.get_user_nickname(event) == "测试用户"
        assert self.adapter.get_group_id(event) == ""
        assert self.adapter.get_session_id(event) == "private:user-openid"
        assert self.adapter.get_user_role(event) == "private"
        assert self.adapter.is_group_message(event) is False

    def test_reads_group_openid_and_metadata(self):
        event = _event(group_id="group-openid", group_name="测试群")

        assert self.adapter.get_group_id(event) == "group-openid"
        assert self.adapter.get_group_name(event) == "测试群"
        assert self.adapter.get_session_id(event) == "group-openid"
        assert self.adapter.get_user_role(event) == "member"
        assert self.adapter.is_group_message(event) is True

    def test_get_raw_message_returns_qq_botpy_payload(self):
        payload = {
            "id": "message-1",
            "group_openid": "group-openid",
            "attachments": [],
        }
        event = _event(raw_message=SimpleNamespace(raw_data=payload))

        assert self.adapter.get_raw_message(event) == payload

    def test_get_raw_message_supports_botpy_slots_and_normalizes_message_id(self):
        class BotpyMessage:
            __slots__ = ("attachments", "content", "id", "message_reference")

            def __init__(self):
                self.id = "botpy-message-1"
                self.content = "hello"
                self.message_reference = SimpleNamespace(message_id="quoted-message-1")
                self.attachments = []

        event = _event(raw_message=BotpyMessage())

        raw_message = self.adapter.get_raw_message(event)

        assert raw_message["id"] == "botpy-message-1"
        assert raw_message["message_id"] == "botpy-message-1"
        assert raw_message["content"] == "hello"
        assert self.adapter.get_reply_info(event).message_id == "quoted-message-1"

    def test_extracts_astrbot_image_component(self, image_info_model):
        event = _event(
            message=[
                SimpleNamespace(
                    type="Image",
                    url="https://example.com/image.png",
                    file="https://example.com/image.png",
                    path="",
                )
            ]
        )

        images = self.adapter.get_images(event)

        assert len(images) == 1
        assert isinstance(images[0], image_info_model)
        assert images[0].url == "https://example.com/image.png"
        assert images[0].format == "png"
        assert images[0].message_id == "message-1"

    def test_extracts_image_attachment_when_message_chain_is_empty(
        self, image_info_model
    ):
        event = _event(
            raw_message=SimpleNamespace(
                attachments=[
                    SimpleNamespace(
                        content_type="image/jpeg",
                        filename="photo.jpg",
                        url="https://example.com/photo.jpg",
                    )
                ]
            )
        )

        images = self.adapter.get_images(event)

        assert len(images) == 1
        assert images[0].url == "https://example.com/photo.jpg"
        assert images[0].format == "jpg"

    def test_extracts_image_from_reply_chain(self, image_info_model):
        event = _event(
            message=[
                SimpleNamespace(
                    type="Reply",
                    id="quoted-message",
                    chain=[
                        SimpleNamespace(
                            type="Image",
                            url="https://example.com/quoted.png",
                            file="https://example.com/quoted.png",
                        )
                    ],
                )
            ]
        )

        images = self.adapter.get_images(event)

        assert len(images) == 1
        assert isinstance(images[0], image_info_model)
        assert images[0].url == "https://example.com/quoted.png"
        assert images[0].source == "forward"

    def test_extracts_reply_component(self):
        event = _event(
            message=[
                SimpleNamespace(
                    type="Reply",
                    id="quoted-message",
                    sender_id="quoted-user",
                    sender_nickname="被引用用户",
                    message_str="引用内容",
                    text="",
                )
            ]
        )

        assert self.adapter.get_reply_info(event) == ReplyInfo(
            message_id="quoted-message",
            user_id="quoted-user",
            user_name="被引用用户",
            content="引用内容",
        )

    def test_extracts_reply_reference_from_raw_payload(self):
        event = _event(
            raw_message=SimpleNamespace(
                raw_data={"message_reference": {"message_id": "quoted-message"}}
            )
        )

        assert self.adapter.get_reply_info(event).message_id == "quoted-message"

    def test_extracts_non_bot_mentions_only(self):
        event = _event(
            raw_message=SimpleNamespace(
                mentions=[
                    SimpleNamespace(id="bot-openid", username="机器人", is_you=True),
                    SimpleNamespace(
                        member_openid="mentioned-user",
                        username="被提及用户",
                        is_you=False,
                    ),
                ]
            )
        )

        assert self.adapter.get_mentioned_users(event) == [
            ("mentioned-user", "被提及用户")
        ]
