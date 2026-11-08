# 平台适配器开发指南

本文介绍 Iris Chat Memory 的平台适配器架构，以及如何为新的消息平台或特殊事件类型实现适配器。

## 适配器的作用

AstrBot 的不同平台事件可能采用不同的字段和消息段格式。适配器负责把这些差异转换成统一接口，供记忆、画像、消息钩子和 LLM 工具使用。业务代码应通过 `get_adapter(event)` 访问平台信息，而不是在业务模块中直接判断平台名称或解析平台原始消息。

适配器代码位于 `iris_memory/platform/`：

- `base.py`：抽象接口、`ReplyInfo` 和 `ForwardMessage` 数据类，以及通用默认行为。
- `factory.py`：平台类型识别、适配器注册表和实例缓存。
- `generic.py`：基于 AstrBot 标准事件字段的降级实现。
- `qq.py`：OneBot11 / QQ 的平台专用实现。
- `cron.py`：AstrBot 定时任务事件的专用实现。

## 适配器选择流程

`get_adapter(event)` 按以下顺序工作：

1. 优先读取 `event.get_platform_name()` 获取平台协议类型。
2. 如果不可用，回退到 `event.platform_meta.name`。
3. 将平台类型转为小写，并在工厂注册表中查找适配器类。
4. 返回该平台缓存的适配器实例；缓存创建由锁保护。
5. 未知平台或已登记但尚无实现的平台会使用 `GenericAdapter`。

当前注册表中，`aiocqhttp` 和 `qq` 使用 `OneBot11Adapter`，`cron` 使用 `CronAdapter`，`qq_official` 使用 `QQOfficialAdapter`。QQ 官方机器人适配范围仅包括 WebSocket 平台 `qq_official`；`qqofficial` 旧名称和 `qq_official_webhook` 均不注册为该适配器，会走通用降级。`gewechat` 也暂无专用实现。

`QQOfficialAdapter` 面向 AstrBot 已归一化的 `AstrBotMessage`，不直接依赖 qq-botpy 类型：发送者 ID 取 `message_obj.sender.user_id`（QQ 群成员 OpenID 或 C2C 用户 OpenID），群/频道标识取 `message_obj.group_id`，原始 qq-botpy 事件取 `message_obj.raw_message`。图片和回复优先从 AstrBot 消息链读取。

如果无法从事件中得到平台类型，工厂会记录错误并抛出 `ValueError`。工厂函数文档中提及的 `UnsupportedPlatformError` 并不是当前未知平台路径的实际行为。

## 接口约定

继承 `PlatformAdapter` 的类必须实现以下抽象方法：

| 方法 | 返回值 | 约定 |
|---|---|---|
| `get_user_id(event)` | `str` | 当前消息发送者的稳定平台 ID |
| `get_user_name(event)` | `str` | 用于展示的名称；群聊通常优先群名片 |
| `get_user_nickname(event)` | `str` | 用户原始昵称，不使用群名片 |
| `get_group_id(event)` | `str` | 群 ID；私聊返回空字符串 |
| `get_group_name(event)` | `str` | 群名称；不可获得时返回空字符串 |
| `get_user_role(event)` | `str` | `owner`、`admin`、`member` 或私聊时的 `private` |
| `get_raw_message(event)` | `dict` | 平台原始消息的字典形式；不可用时返回空字典 |
| `is_group_message(event)` | `bool` | 判断当前事件是否属于群聊 |
| `get_images(event)` | `list[ImageInfo]` | 提取当前消息中的图片；不支持时返回空列表 |
| `get_reply_info(event)` | `ReplyInfo` | 提取引用消息；不是回复或无法解析时返回空 `ReplyInfo` |

基类还提供了可选扩展点：

- `get_session_id(event)`：默认返回群 ID；私聊使用 `private:{user_id}`，避免不同私聊用户共享同一会话键。事件的会话结构特殊时应覆盖。
- `get_mentioned_users(event)`：默认返回空列表；有平台专用 @ 提及格式时可覆盖，返回 `(user_id, user_name)` 元组列表。
- `get_msg_by_id(event, message_id)`：异步方法，默认返回空 `ReplyInfo`；平台支持历史消息查询时可覆盖。
- `get_forward_messages(event)`：异步方法，默认返回空列表；平台支持合并转发时可覆盖并返回 `ForwardMessage` 列表。

对于平台不支持或当前消息中不可用的信息，优先使用接口约定的空值表示能力缺失。读取必需字段但发现事件结构不符合预期时，应保留明确的错误行为，不要把错误伪装成看似有效的用户或群 ID。

## 实现步骤

### 1. 先确认 AstrBot 事件结构

查阅目标平台在 AstrBot 中生成的事件对象及平台 API，确认用户 ID、群/私聊标识、昵称、角色、原始消息、图片和回复消息的实际位置。不要仅凭平台协议示例假定 AstrBot 暴露了相同字段。

若事件类型与普通聊天事件结构不同，应优先识别其特殊字段和会话语义。`CronAdapter` 就是这类实现：它从 `event.session.message_type` 和 `event.session.session_id` 恢复会话目标，避免把群号误作发送者 ID。

### 2. 创建适配器类

在 `iris_memory/platform/` 中添加模块，并继承 `PlatformAdapter`。实现所有抽象方法，平台不支持的可选能力保留基类默认实现。实现应保持无状态：不要保存某次事件对象或会话数据在适配器实例上，因为同一实例会被同平台事件共享。

对平台消息段的解析应校验数据类型和必需字段；遇到未知消息段时安全跳过。图片、回复、提及等能力应在对应接口中处理，不要让业务调用方解析平台原始格式。

### 3. 注册平台类型

若适配器属于本仓库的内置实现，在 `iris_memory/platform/factory.py` 的 `_ADAPTER_REGISTRY` 中将平台协议名映射到适配器类，并在 `iris_memory/platform/__init__.py` 中按需导出。

外部扩展可以使用公共 API 注册，不需要直接修改注册表：

```python
from iris_memory.platform import PlatformAdapter, register_adapter

# FeishuAdapter 需要先完整实现 PlatformAdapter 的抽象方法。
register_adapter("feishu", FeishuAdapter)
```

注册应在该平台第一次调用 `get_adapter(event)` 之前完成。平台类型以 AstrBot 的协议类型为准，不要使用用户在 AstrBot 中设置的实例显示名称。`register_adapter()` 会将名称转为小写；若覆盖已有映射，也会清除其缓存实例。

### 4. 添加测试

在 `tests/platform/` 为新适配器添加测试，使用 mock 事件描述真实字段结构。建议覆盖：

- 群聊和私聊的用户 ID、群 ID、会话 ID 及角色。
- 昵称、群名片或其他展示名称的优先级。
- 缺失字段、空值和非预期消息段的处理。
- 图片、回复、@提及等已实现的平台能力。
- 异步历史消息或合并转发 API 的成功、不可用和异常结果。
- 工厂能识别对应平台类型，并返回预期适配器。

可参考 `tests/platform/test_cron_adapter.py`（特殊会话结构）和 `tests/platform/test_platform.py`（适配器及工厂行为）。

运行适配器测试：

```bash
pytest tests/platform/ -v
```

## 现有实现参考

### OneBot11 / QQ

`OneBot11Adapter` 展示了基于平台原始消息段实现完整能力的方式，包括群名片、群角色、CQ 码/消息段解析、图片、回复、@提及、按 ID 获取消息和合并转发。开发相似聊天平台适配器时，可参考其解析和测试组织方式，但应以目标平台及 AstrBot 实际事件结构为准。

### Cron 定时任务

`CronAdapter` 展示了如何为特殊事件类型修正通用字段的语义：群聊定时任务从 session 恢复群 ID，并将机器人自身 ID 作为发送者；私聊定时任务将 session ID 作为目标用户 ID。图片和回复等聊天平台能力则按不支持处理。

### 通用降级适配器

`GenericAdapter` 适用于未实现专用适配器的平台，尝试使用 `message_obj.sender` 和 `message_obj.group_id` 等标准字段读取基本信息。平台专属能力（如图片、回复）会返回空值。因此通用适配器只能作为兼容性兜底，不能代替对目标平台行为的验证。

## 维护注意事项

- 适配器被多个事件共享，保持无状态，不在实例中缓存事件级数据。
- 群 ID 为空通常代表私聊；若平台事件结构例外，必须覆盖 `is_group_message()` 和相关 ID / session 方法，确保记忆隔离正确。
- 通用降级可能导致专属功能缺失；新增正式支持的平台时，应增加映射和覆盖相关能力的测试。
- 修改公共接口时，检查 `iris_memory/core/`、`iris_memory/tools/` 等调用方及测试，确保同步兼容。
- 接口源码以 `iris_memory/platform/base.py` 为准；新增抽象方法会要求所有具体适配器实现该方法。
- QQ 官方机器人只针对 AstrBot 的 WebSocket 适配器 `qq_official` 开发和测试；不要将该实现注册给 `qq_official_webhook`。
