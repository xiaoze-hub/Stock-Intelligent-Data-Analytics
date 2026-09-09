from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict, field_validator

from src.web.database import get_db
from src.web.models import AIService, AIModel, AISceneBinding
from src.core.ai_client import AIClient

router = APIRouter()

# --- 场景绑定(统一 LLM 配置中心, 2026-08-12) ---

# 全量场景注册表: scene → 显示名 + 描述。新增使用点需在此登记。
SCENES = {
    "chat": {"name": "数智分析BOT", "desc": "日常对话 / 个股问答助手"},
    "trading_agents": {"name": "TradingAgents 深度分析", "desc": "多智能体深度分析报告"},
    "reports": {"name": "报告复盘 Agent", "desc": "盘前 / 盘后复盘报告生成"},
    "referee": {"name": "AI 裁判", "desc": "多模型结果裁决 / 交叉验证"},
    "selfcheck": {"name": "自检", "desc": "AI 自检 / 质量检查"},
    "insights": {"name": "机会评分", "desc": "投资机会评分 / 洞察"},
    "vision": {"name": "视觉代理(图片识别)", "desc": "图片内容理解(需支持视觉的多模态模型, 对话/微信发图时自动调用)"},
}


class SceneBindingResponse(BaseModel):
    scene: str
    display_name: str
    description: str
    model_id: int | None = None
    model_name: str | None = None
    service_id: int | None = None
    is_bound: bool = False


class SceneBindingUpdate(BaseModel):
    model_id: int | None = None  # None = 解绑, 回落默认模型


@router.get("/scene-bindings", response_model=list[SceneBindingResponse])
def list_scene_bindings(db: Session = Depends(get_db)):
    """返回全部场景的绑定状态(含未绑定场景), 前端据此渲染绑定 UI。"""
    bindings = {b.scene: b for b in db.query(AISceneBinding).all()}
    result = []
    for scene, meta in SCENES.items():
        binding = bindings.get(scene)
        model = None
        if binding and binding.model_id is not None:
            model = db.query(AIModel).filter(AIModel.id == binding.model_id).first()
        result.append({
            "scene": scene,
            "display_name": meta["name"],
            "description": meta["desc"],
            "model_id": model.id if model else None,
            "model_name": model.name if model else None,
            "service_id": model.service_id if model else None,
            "is_bound": model is not None,
        })
    return result


@router.put("/scene-bindings/{scene}", response_model=SceneBindingResponse)
def update_scene_binding(
    scene: str, body: SceneBindingUpdate, db: Session = Depends(get_db)
):
    """绑定场景到指定模型; model_id=None 解绑(使用点回落默认模型)。"""
    if scene not in SCENES:
        raise HTTPException(404, f"未知场景: {scene}")

    if body.model_id is not None:
        model = db.query(AIModel).filter(AIModel.id == body.model_id).first()
        if not model:
            raise HTTPException(400, "AI 模型不存在")

    binding = db.query(AISceneBinding).filter(AISceneBinding.scene == scene).first()
    if binding is None:
        binding = AISceneBinding(scene=scene, model_id=body.model_id)
        db.add(binding)
    else:
        binding.model_id = body.model_id
    db.commit()
    db.refresh(binding)

    model = None
    if binding.model_id is not None:
        model = db.query(AIModel).filter(AIModel.id == binding.model_id).first()
    return {
        "scene": scene,
        "display_name": SCENES[scene]["name"],
        "description": SCENES[scene]["desc"],
        "model_id": model.id if model else None,
        "model_name": model.name if model else None,
        "service_id": model.service_id if model else None,
        "is_bound": model is not None,
    }


# --- Service ---

class ServiceCreate(BaseModel):
    name: str
    base_url: str
    api_key: str = ""


class ServiceUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None


# 模型能力标签(2026-08-15): chat=对话, vision=视觉/图片理解, image=图像生成, video=视频生成, tools=工具调用
CAPABILITY_TAGS = ("chat", "vision", "image", "video", "tools")


def infer_capabilities(model_name: str, service_name: str = "") -> list[str]:
    """按模型名/服务商名自动推断能力(2026-08-15): 未手动配置 capabilities 的
    存量模型也能自动显示真实功能。保守规则, 命中关键词才标, 不猜。"""
    n = f"{model_name} {service_name}".lower()
    caps: set[str] = set()
    # 视频生成
    if any(k in n for k in ("video", "cinema", "animate", "mov", "video-gen")):
        caps.add("video")
    # 图像生成
    if any(k in n for k in ("image", "img-", "img_", "dall-e", "sdxl", "flux", "draw", "t2i", "paint")):
        caps.add("image")
    # 视觉理解(多模态); 注意不能用 "see"(deepseek/seed 会误命中)
    if any(k in n for k in ("vision", "vl", "4v", "omni", "multimodal", "visual", "caption", "vlm", "image-understanding")):
        caps.add("vision")
    # 已知多模态特例(实测): Agnes 2.5 Flash 支持 chat/vision/image/video/tools
    # 注意: 只匹配模型名(服务商名 "Agnes 2.5 Flash" 会误命中全部模型)
    mn = model_name.lower()
    if "agnes-2.5" in mn or "agnes 2.5" in mn:
        caps.update(("vision", "image", "video"))
    # 工具调用(主流对话模型默认支持)
    if any(k in n for k in ("deepseek", "glm", "doubao", "agnes", "sensenova", "qwen", "minimax", "gpt", "claude", "kimi", "moonshot", "yi", "ernie", "baichuan", "chat", "abab", "step", "hunyuan")):
        caps.add("tools")
    # chat 基础能力(对话/生成模型都有; 纯生成模型如 cinema-generate 不含)
    if any(k in n for k in ("chat", "flash", "lite", "fast", "mini", "max", "pro", "turbo", "deepseek", "glm", "agnes", "doubao", "sensenova", "qwen", "minimax", "gpt", "claude")):
        caps.add("chat")
    if not caps:
        caps.add("chat")  # 兜底: 未知模型至少可对话
    order = ["chat", "vision", "image", "video", "tools"]
    return [t for t in order if t in caps]


def _parse_capabilities(raw) -> list[str]:
    """从存储串(逗号分隔)解析能力标签; 空串/None = 默认 chat(兼容存量模型)。"""
    if isinstance(raw, list):
        return raw or ["chat"]
    if not raw or not str(raw).strip():
        return ["chat"]
    return [t.strip() for t in str(raw).split(",") if t.strip()]


class ModelResponse(BaseModel):
    id: int
    name: str
    service_id: int
    model: str
    is_default: bool
    capabilities: list[str] = []

    @field_validator("capabilities", mode="before")
    @classmethod
    def _validate_capabilities(cls, v):
        return _parse_capabilities(v)

    model_config = ConfigDict(from_attributes=True)


class ServiceResponse(BaseModel):
    id: int
    name: str
    base_url: str
    api_key: str
    models: list[ModelResponse] = []

    model_config = ConfigDict(from_attributes=True)


@router.get("/services", response_model=list[ServiceResponse])
def list_services(db: Session = Depends(get_db)):
    services = db.query(AIService).order_by(AIService.id).all()
    return [_service_to_response(s) for s in services]


def _service_to_response(service: AIService) -> dict:
    # 安全(2026-08-15): api_key 不回显明文, 已配置显示掩码占位(与 settings 的 SECRET_MASK 一致)
    api_key = service.api_key or ""
    if api_key:
        api_key = "********"
    return {
        "id": service.id,
        "name": service.name,
        "base_url": service.base_url,
        "api_key": api_key,
        "models": [
            {
                "id": m.id,
                "name": m.name,
                "service_id": m.service_id,
                "model": m.model,
                "is_default": m.is_default,
                "capabilities": _parse_capabilities(m.capabilities)
                if str(m.capabilities or "").strip()
                else infer_capabilities(m.model, service.name),
            }
            for m in service.models
        ],
    }


@router.post("/services", response_model=ServiceResponse)
def create_service(body: ServiceCreate, db: Session = Depends(get_db)):
    service = AIService(**body.model_dump())
    db.add(service)
    db.commit()
    db.refresh(service)
    return _service_to_response(service)


@router.put("/services/{service_id}", response_model=ServiceResponse)
def update_service(service_id: int, body: ServiceUpdate, db: Session = Depends(get_db)):
    service = db.query(AIService).filter(AIService.id == service_id).first()
    if not service:
        raise HTTPException(404, "AI 服务商不存在")

    for key, value in body.model_dump(exclude_unset=True).items():
        # 掩码占位不覆盖真 key(前端编辑时未修改会回传 "********")
        if key == "api_key" and value in ("********", "", None):
            continue
        setattr(service, key, value)

    db.commit()
    db.refresh(service)
    return _service_to_response(service)


@router.delete("/services/{service_id}")
def delete_service(service_id: int, db: Session = Depends(get_db)):
    service = db.query(AIService).filter(AIService.id == service_id).first()
    if not service:
        raise HTTPException(404, "AI 服务商不存在")
    db.delete(service)
    db.commit()
    return {"ok": True}


# --- Model ---

class ModelCreate(BaseModel):
    name: str = ""
    service_id: int
    model: str
    is_default: bool = False
    # None/缺省 = 不指定(存空串, 读回默认 chat), 兼容存量前端
    capabilities: list[str] | None = None


class ModelUpdate(BaseModel):
    name: str | None = None
    service_id: int | None = None
    model: str | None = None
    is_default: bool | None = None
    capabilities: list[str] | None = None  # None = 不变


class BatchModelItem(BaseModel):
    name: str = ""
    model: str
    is_default: bool = False


class BatchModelCreate(BaseModel):
    models: list[BatchModelItem] = []


@router.get("/models", response_model=list[ModelResponse])
def list_models(db: Session = Depends(get_db)):
    return db.query(AIModel).order_by(AIModel.id).all()


@router.post("/models", response_model=ModelResponse)
def create_model(body: ModelCreate, db: Session = Depends(get_db)):
    service = db.query(AIService).filter(AIService.id == body.service_id).first()
    if not service:
        raise HTTPException(400, "AI 服务商不存在")

    if body.is_default:
        db.query(AIModel).update({"is_default": False})

    data = body.model_dump()
    if not data["name"]:
        data["name"] = data["model"]
    # capabilities: list/None → 逗号分隔存储串(空=自动推断标注, 兼容存量)
    caps = data.get("capabilities")
    if caps:
        data["capabilities"] = ",".join(caps)
    else:
        data["capabilities"] = ",".join(infer_capabilities(data["model"], service.name))
    model = AIModel(**data)
    db.add(model)
    db.commit()
    db.refresh(model)
    return model


@router.put("/models/{model_id}", response_model=ModelResponse)
def update_model(model_id: int, body: ModelUpdate, db: Session = Depends(get_db)):
    model = db.query(AIModel).filter(AIModel.id == model_id).first()
    if not model:
        raise HTTPException(404, "AI 模型不存在")

    data = body.model_dump(exclude_unset=True)
    if data.get("is_default"):
        db.query(AIModel).update({"is_default": False})

    # capabilities: None = 不变(不覆盖); list = 覆盖为逗号分隔存储串
    caps = data.get("capabilities")
    if caps is None:
        data.pop("capabilities", None)
    else:
        data["capabilities"] = ",".join(caps)

    for key, value in data.items():
        setattr(model, key, value)

    db.commit()
    db.refresh(model)
    return model


@router.delete("/models/{model_id}")
def delete_model(model_id: int, db: Session = Depends(get_db)):
    model = db.query(AIModel).filter(AIModel.id == model_id).first()
    if not model:
        raise HTTPException(404, "AI 模型不存在")
    db.delete(model)
    db.commit()
    return {"ok": True}


@router.post("/models/{model_id}/test")
async def test_model(model_id: int, db: Session = Depends(get_db)):
    model = db.query(AIModel).filter(AIModel.id == model_id).first()
    if not model:
        raise HTTPException(404, "AI 模型不存在")

    service = db.query(AIService).filter(AIService.id == model.service_id).first()
    if not service:
        raise HTTPException(400, "关联的服务商不存在")

    try:
        client = AIClient(
            base_url=service.base_url,
            api_key=service.api_key,
            model=model.model,
        )
        # 测试连通性时不下发 temperature:部分模型(如 o1/claude-opus 等)不接受该参数,
        # 省略后对所有模型都安全,避免因 temperature 报错而误判模型不可用。
        reply = await client.chat(
            system_prompt="You are a helpful assistant.",
            user_content="Say 'OK' in one word.",
            temperature=None,
        )
        return {"ok": True, "reply": reply.strip()}
    except Exception as e:
        raise HTTPException(400, f"测试失败: {e}")


@router.post("/services/{service_id}/discover-models")
async def discover_models(service_id: int, db: Session = Depends(get_db)):
    service = db.query(AIService).filter(AIService.id == service_id).first()
    if not service:
        raise HTTPException(404, "AI 服务商不存在")
    try:
        client = AIClient(base_url=service.base_url, api_key=service.api_key)
        models = await client.list_models()
        return {"models": models}
    except Exception as e:
        raise HTTPException(400, f"嗅探失败: {e}")


@router.post("/services/{service_id}/models/batch")
def batch_add_models(service_id: int, body: BatchModelCreate, db: Session = Depends(get_db)):
    service = db.query(AIService).filter(AIService.id == service_id).first()
    if not service:
        raise HTTPException(404, "AI 服务商不存在")

    existing = {m.model for m in service.models}
    added = 0
    for item in body.models:
        if not item.model or item.model in existing:
            continue
        if item.is_default:
            db.query(AIModel).update({"is_default": False})
        db.add(AIModel(
            name=item.name or item.model,
            service_id=service_id,
            model=item.model,
            is_default=item.is_default,
        ))
        existing.add(item.model)
        added += 1
    db.commit()
    return {"added": added}
