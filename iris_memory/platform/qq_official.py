"""AstrBot QQ Official Bot WebSocket platform adapter."""

from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from iris_memory.core import get_logger
from iris_memory.platform.base import PlatformAdapter, ReplyInfo

if TYPE_CHECKING:
    from iris_memory.image.models import ImageInfo

logger = get_logger("platform.qq_official")


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _component_type(component: Any) -> str:
    component_type = _value(component, "type", "")
    return str(getattr(component_type, "value", component_type)).lower()


def _normalize_message_fields(fields: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(fields)
    if "id" in normalized and "message_id" not in normalized:
        normalized["message_id"] = normalized["id"]
    return normalized


class QQOfficialAdapter(PlatformAdapter):
    """Adapter for AstrBot's QQ Official Bot WebSocket events.

    AstrBot normalizes QQ Official WebSocket payloads into ``AstrBotMessage``:
    sender OpenIDs are in ``sender.user_id``, group OpenIDs in ``group_id``,
    and the original qq-botpy message is available as ``raw_message``.
    """

    @staticmethod
    def _message_obj(event: Any) -> Any:
        message_obj = getattr(event, "message_obj", None)
        if message_obj is None:
            raise AttributeError("event.message_obj 不存在")
        return message_obj

    def get_user_id(self, event: Any) -> str:
        try:
            user_id = self._message_obj(event).sender.user_id
        except AttributeError:
            logger.error(
                "无法获取 QQ Official 用户 OpenID：message_obj.sender.user_id 不存在"
            )
            raise
        if not user_id:
            raise ValueError("QQ Official 消息缺少发送者 OpenID")
        return str(user_id)

    def get_user_name(self, event: Any) -> str:
        return self.get_user_nickname(event)

    def get_user_nickname(self, event: Any) -> str:
        try:
            nickname = self._message_obj(event).sender.nickname
        except AttributeError:
            logger.error(
                "无法获取 QQ Official 用户名称：message_obj.sender.nickname 不存在"
            )
            raise
        return str(nickname) if nickname else ""

    def get_group_id(self, event: Any) -> str:
        message_obj = self._message_obj(event)
        group_id = getattr(message_obj, "group_id", "")
        if not group_id:
            group = getattr(message_obj, "group", None)
            group_id = getattr(group, "group_id", "")
        return str(group_id) if group_id else ""

    def get_group_name(self, event: Any) -> str:
        message_obj = self._message_obj(event)
        group = getattr(message_obj, "group", None)
        group_name = getattr(group, "group_name", "")
        return str(group_name) if group_name else ""

    def get_user_role(self, event: Any) -> str:
        return "member" if self.is_group_message(event) else "private"

    def get_raw_message(self, event: Any) -> dict[str, Any]:
        try:
            source = getattr(self._message_obj(event), "raw_message", None)
            if isinstance(source, dict):
                return _normalize_message_fields(source)

            raw_data = getattr(source, "raw_data", None)
            if isinstance(raw_data, dict):
                return _normalize_message_fields(raw_data)

            if source is not None:
                try:
                    source_fields = dict(vars(source))
                except TypeError:
                    source_fields = {}

                for message_type in type(source).__mro__:
                    slots = getattr(message_type, "__slots__", ())
                    if isinstance(slots, str):
                        slots = (slots,)
                    for field in slots:
                        if field.startswith("_") or field in source_fields:
                            continue
                        try:
                            source_fields[field] = getattr(source, field)
                        except AttributeError:
                            continue

                return _normalize_message_fields(source_fields)
        except AttributeError:
            logger.debug("QQ Official 事件缺少原始消息")
        return {}

    def is_group_message(self, event: Any) -> bool:
        return bool(self.get_group_id(event))

    def get_images(self, event: Any) -> list["ImageInfo"]:
        from iris_memory.image.models import ImageInfo

        message_obj = self._message_obj(event)
        message_id = str(getattr(message_obj, "message_id", "") or "")
        components = getattr(message_obj, "message", None)
        images: list[ImageInfo] = []

        if isinstance(components, (list, tuple)):
            for component in components:
                if _component_type(component) == "image":
                    image = self._make_image_info(component, message_id)
                    if image is not None:
                        images.append(image)
                elif _component_type(component) == "reply":
                    reply_chain = _value(component, "chain", None)
                    if isinstance(reply_chain, (list, tuple)):
                        for reply_component in reply_chain:
                            if _component_type(reply_component) == "image":
                                image = self._make_image_info(
                                    reply_component, message_id, source="forward"
                                )
                                if image is not None:
                                    images.append(image)

        if images:
            return images

        raw_message = getattr(message_obj, "raw_message", None)
        attachments = getattr(raw_message, "attachments", None)
        if not attachments:
            raw_data = self.get_raw_message(event)
            attachments = raw_data.get("attachments", [])

        if isinstance(attachments, list):
            for attachment in attachments:
                content_type = str(_value(attachment, "content_type", "") or "").lower()
                if content_type.startswith("image"):
                    image = self._make_image_info(attachment, message_id)
                    if image is not None:
                        images.append(image)
        return images

    @staticmethod
    def _make_image_info(
        attachment: Any, message_id: str, source: str = "user"
    ) -> "ImageInfo | None":
        from iris_memory.image.models import ImageInfo

        url = str(_value(attachment, "url", "") or "")
        file_value = str(_value(attachment, "file", "") or "")
        path = str(_value(attachment, "path", "") or "")
        remote_url = url if url.startswith(("http://", "https://")) else ""
        if not remote_url and file_value.startswith(("http://", "https://")):
            remote_url = file_value

        file_path = path or ""
        if not file_path and not remote_url:
            candidate = file_value or url
            if candidate and not candidate.startswith(("base64://", "data:")):
                file_path = candidate
        if not remote_url and not file_path:
            return None

        suffix = PurePosixPath(urlsplit(remote_url or file_path).path).suffix
        return ImageInfo(
            url=remote_url or None,
            file_path=file_path or None,
            format=suffix.lstrip(".").lower(),
            source=source,
            message_id=message_id,
        )

    def get_reply_info(self, event: Any) -> ReplyInfo:
        message_obj = self._message_obj(event)
        components = getattr(message_obj, "message", None)
        if isinstance(components, (list, tuple)):
            for component in components:
                if _component_type(component) != "reply":
                    continue
                message_id = _value(component, "id", "")
                if message_id:
                    return ReplyInfo(
                        message_id=str(message_id),
                        user_id=str(_value(component, "sender_id", "") or ""),
                        user_name=str(_value(component, "sender_nickname", "") or ""),
                        content=str(
                            _value(component, "message_str", "")
                            or _value(component, "text", "")
                            or ""
                        ),
                    )

        raw_message = getattr(message_obj, "raw_message", None)
        reference = _value(raw_message, "message_reference")
        if reference is None:
            reference = self.get_raw_message(event).get("message_reference")
        if reference is not None:
            message_id = _value(reference, "message_id") or _value(reference, "id")
            if message_id:
                return ReplyInfo(message_id=str(message_id))

        raw_data = self.get_raw_message(event)
        msg_elements = raw_data.get("msg_elements", [])
        if isinstance(msg_elements, list) and msg_elements:
            quoted = msg_elements[0]
            message_id = _value(quoted, "id") or _value(quoted, "message_id")
            if message_id:
                return ReplyInfo(
                    message_id=str(message_id),
                    content=str(_value(quoted, "content", "") or ""),
                )
        return ReplyInfo()

    def get_mentioned_users(self, event: Any) -> list[tuple[str, str]]:
        message_obj = self._message_obj(event)
        raw_message = getattr(message_obj, "raw_message", None)
        mentions = getattr(raw_message, "mentions", None)
        if mentions is None:
            mentions = self.get_raw_message(event).get("mentions", [])

        mentioned: list[tuple[str, str]] = []
        seen: set[str] = set()
        self_id = str(getattr(message_obj, "self_id", "") or "")
        if isinstance(mentions, list):
            for mention in mentions:
                if _value(mention, "is_you", False) is True:
                    continue
                user_id = (
                    _value(mention, "member_openid")
                    or _value(mention, "user_openid")
                    or _value(mention, "id")
                )
                if not user_id:
                    continue
                user_id = str(user_id)
                if user_id == self_id or user_id in {"qq_official", "unknown_selfid"}:
                    continue
                name = str(_value(mention, "username", "") or "")
                if user_id not in seen:
                    mentioned.append((user_id, name))
                    seen.add(user_id)
        return mentioned
