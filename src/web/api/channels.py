from datetime import datetime
import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict

from src.web.database import get_db
from src.web.api.auth import get_current_user
from src.web.models import NotifyChannel, User
from src.core.notifier import NotifierManager, CHANNEL_TYPES

router = APIRouter()


def _user_channels_query(db: Session, user: User):
    """当前用户可见渠道: 自己的 + 全局共享(NULL)。"""
    return db.query(NotifyChannel).filter(
        or_(NotifyChannel.user_id == user.id, NotifyChannel.user_id.is_(None))
    )


def _channel_test_content() -> str:
    """Build a unique test message so providers do not reject duplicates."""
    sent_at = datetime.now().astimezone().isoformat(timespec="seconds")
    request_id = secrets.token_hex(3)
    return (
        "这是一条来自盯盘侠的测试通知，如果您收到此消息说明通知渠道配置正确。\n\n"
        f"测试时间：{sent_at}\n测试编号：{request_id}"
    )


def _validate_channel(channel_type: str, config: dict) -> None:
    if channel_type not in CHANNEL_TYPES:
        raise HTTPException(400, f"不支持的通知渠道: {channel_type}")
    try:
        NotifierManager().add_channel(channel_type, config or {})
    except ValueError as exc:
        raise HTTPException(400, f"渠道配置无效: {exc}") from exc


class ChannelCreate(BaseModel):
    name: str
    type: str = "telegram"
    config: dict = {}
    enabled: bool = True
    is_default: bool = False


class ChannelUpdate(BaseModel):
    name: str | None = None
    type: str | None = None
    config: dict | None = None
    enabled: bool | None = None
    is_default: bool | None = None


class ChannelResponse(BaseModel):
    id: int
    name: str
    type: str
    config: dict
    enabled: bool
    is_default: bool

    model_config = ConfigDict(from_attributes=True)


@router.get("", response_model=list[ChannelResponse])
def list_channels(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _user_channels_query(db, user).order_by(NotifyChannel.id).all()


@router.get("/types")
def list_channel_types():
    """返回支持的渠道类型及其字段"""
    return CHANNEL_TYPES


@router.post("", response_model=ChannelResponse)
def create_channel(body: ChannelCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _validate_channel(body.type, body.config)
    if body.is_default:
        _user_channels_query(db, user).update({"is_default": False})
    data = body.model_dump()
    data["user_id"] = user.id  # 渠道归属当前用户
    channel = NotifyChannel(**data)
    db.add(channel)
    db.commit()
    db.refresh(channel)
    return channel


@router.put("/{channel_id}", response_model=ChannelResponse)
def update_channel(channel_id: int, body: ChannelUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    channel = _user_channels_query(db, user).filter(NotifyChannel.id == channel_id).first()
    if not channel:
        raise HTTPException(404, "通知渠道不存在")

    data = body.model_dump(exclude_unset=True)
    next_type = data.get("type", channel.type)
    next_config = data.get("config", channel.config or {})
    _validate_channel(next_type, next_config)
    if data.get("is_default"):
        _user_channels_query(db, user).update({"is_default": False})

    for key, value in data.items():
        setattr(channel, key, value)

    db.commit()
    db.refresh(channel)
    return channel


@router.delete("/{channel_id}")
def delete_channel(channel_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    channel = _user_channels_query(db, user).filter(NotifyChannel.id == channel_id).first()
    if not channel:
        raise HTTPException(404, "通知渠道不存在")
    db.delete(channel)
    db.commit()
    return {"ok": True}


@router.post("/{channel_id}/test")
async def test_channel(channel_id: int, db: Session = Depends(get_db)):
    """发送测试通知"""
    channel = db.query(NotifyChannel).filter(NotifyChannel.id == channel_id).first()
    if not channel:
        raise HTTPException(404, "通知渠道不存在")

    notifier = NotifierManager()
    try:
        notifier.add_channel(channel.type, channel.config or {})
    except Exception as e:
        raise HTTPException(400, f"渠道配置无效: {e}")

    result = await notifier.notify_with_result(
        title="测试通知",
        content=_channel_test_content(),
        bypass_quiet_hours=True,
    )

    if result.get("success"):
        channel_result = next(
            (item for item in result.get("channels", []) if item.get("type") == channel.type),
            {},
        )
        receipt = channel_result.get("receipt") or {}
        if channel.type == "pushplus":
            return {
                "ok": True,
                "message": "PushPlus API 已接收测试消息",
                "message_id": receipt.get("message_id", ""),
            }
        return {"ok": True, "message": "测试通知发送成功"}
    else:
        raise HTTPException(500, f"通知发送失败: {result.get('error', '未知错误')}")
