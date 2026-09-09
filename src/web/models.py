from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Float,
    Boolean,
    DateTime,
    Date,
    JSON,
    ForeignKey,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from src.web.database import Base


class User(Base):
    """多用户账号(2026-08-10 阶段1)。

    - UUID 主键(后续独立账号系统/多服务合并无痛)
    - role: owner(管理员) / member(普通)
    - provider/external_id 预留: 后续对接 SSO/企业微信组织
    - permissions 预留: 权限点字符串数组, 后续 RBAC 扩展
    """
    __tablename__ = "users"

    id = Column(String(36), primary_key=True)  # UUID
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    role = Column(String(16), default="member", nullable=False)  # owner | member
    permissions = Column(JSON, default=list)  # 预留: ["user:manage", ...]
    provider = Column(String(16), default="local")  # 预留: local | wecom | ldap
    external_id = Column(String(128), nullable=True)  # 预留: 第三方用户ID
    is_active = Column(Boolean, default=True, nullable=False)
    token_version = Column(Integer, default=1, nullable=False)
    # 影子账户交易画像(交割单分析落库, profile.to_dict()); NULL=未上传过交割单
    shadow_profile_json = Column(JSON, nullable=True)
    # 个人中心(2026-08-15): 昵称/头像
    nickname = Column(String(64), nullable=True)
    avatar = Column(String(255), nullable=True)  # 头像(base64 data URL 或路径)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AuditLog(Base):
    """操作审计日志(2026-08-15): 记录关键写操作(登录/配置修改/用户管理/导出等)。

    owner 视角审计: 谁在什么时候改了什么。不记录读操作(避免噪音)。
    """

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_created", "created_at"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(36), nullable=True)  # 操作者(系统任务为 NULL)
    username = Column(String(64), default="")
    action = Column(String(64), nullable=False)  # login / logout / update_profile / update_password / manage_user / update_settings / update_datasource / export / register
    detail = Column(String(255), default="")
    ip = Column(String(64), default="")
    created_at = Column(DateTime, server_default=func.now())


class ReportSubscription(Base):
    """定时报告订阅(2026-08-10 阶段4): 每个用户可配置收哪些定时报告。

    report_type: premarket(盘前) / intraday(盘中) / review(盘后复盘) / prediction(预测)
    """
    __tablename__ = "report_subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    report_type = Column(String(32), nullable=False)  # premarket | intraday | review | prediction
    enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "report_type", name="uq_user_report_type"),
    )


class AIService(Base):
    """AI 服务商（base_url + api_key）"""

    __tablename__ = "ai_services"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)  # "OpenAI", "智谱", "DeepSeek"
    base_url = Column(String, nullable=False)
    api_key = Column(String, default="")
    created_at = Column(DateTime, server_default=func.now())

    models = relationship(
        "AIModel", back_populates="service", cascade="all, delete-orphan"
    )


class UserAIService(Base):
    """用户 BYOK AI 服务(2026-08-15 SIDA 账号权限控制): 用户自配模型服务。

    用户级 LLM 解析(get_model_for_scene ①)查此表: models_json 为 JSON 数组
    [{"name","model","is_default","scene","capabilities"}], scene 匹配或
    is_default 的模型优先于平台模型使用。
    """

    __tablename__ = "user_ai_services"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(36), index=True, nullable=False)  # users.id(UUID)
    name = Column(String, nullable=False)  # 服务名, 如 "我的DeepSeek"
    base_url = Column(String, nullable=False)
    api_key = Column(String, default="")
    # 模型列表 JSON 字符串: [{"name","model","is_default","scene","capabilities"}]
    models_json = Column(Text, nullable=False, default="[]")
    created_at = Column(DateTime, server_default=func.now())


class AIModel(Base):
    """AI 模型（属于某个服务商）"""

    __tablename__ = "ai_models"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)  # 显示名，如 "GLM-4-Flash"
    service_id = Column(
        Integer, ForeignKey("ai_services.id", ondelete="CASCADE"), nullable=False
    )
    model = Column(String, nullable=False)  # 实际模型标识，如 "glm-4-flash"
    is_default = Column(Boolean, default=False)
    # 功能选型(2026-08-15): 逗号分隔能力标签 chat,vision,image,video,tools; 空串=默认 chat(兼容存量模型)
    capabilities = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, server_default=func.now())

    service = relationship("AIService", back_populates="models")


class AISceneBinding(Base):
    """AI 使用场景绑定(2026-08-12 统一 LLM 配置中心)。

    每个使用场景可绑定模型池(ai_models)中的任意模型,模型源(服务商)统一在
    ai_services 管理。model_id 为 NULL = 未绑定,使用点回落默认模型
    (is_default)或模型池第一个模型。

    场景取值(6 个):
      - chat: 对话助手(日常问答/个股咨询)
      - trading_agents: TradingAgents 深度分析
      - reports: 报告复盘 Agent(盘前/盘后复盘)
      - referee: AI 裁判(多模型结果裁决/交叉验证)
      - selfcheck: 自检(AI 质量自检)
      - insights: 机会评分(投资机会评分/洞察)
    """

    __tablename__ = "ai_scene_bindings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    scene = Column(String(64), unique=True, nullable=False)
    model_id = Column(
        Integer, ForeignKey("ai_models.id", ondelete="SET NULL"), nullable=True
    )
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class NotifyChannel(Base):
    __tablename__ = "notify_channels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)  # NULL=全局共享渠道
    name = Column(String, nullable=False)
    type = Column(String, nullable=False)  # "telegram" | "wecom" | "weixin" | "webhook"
    config = Column(JSON, default={})  # {"bot_token": "...", "chat_id": "..."}
    enabled = Column(Boolean, default=True)
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())


class Account(Base):
    """交易账户"""

    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)  # NULL=旧数据/全局
    name = Column(String, nullable=False)  # 账户名称，如 "招商证券"、"华泰证券"
    available_funds = Column(Float, default=0)  # 可用资金
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    positions = relationship(
        "Position", back_populates="account", cascade="all, delete-orphan"
    )


class Stock(Base):
    __tablename__ = "stocks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)  # 自选归属用户
    symbol = Column(String, nullable=False)
    name = Column(String, nullable=False)
    market = Column(String, nullable=False)  # CN / HK / US
    # 以下字段已废弃，持仓信息移至 Position 表
    cost_price = Column(Float, nullable=True)
    quantity = Column(Integer, nullable=True)
    invested_amount = Column(Float, nullable=True)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    agents = relationship(
        "StockAgent", back_populates="stock", cascade="all, delete-orphan"
    )
    positions = relationship(
        "Position", back_populates="stock", cascade="all, delete-orphan"
    )


class Position(Base):
    """持仓记录（多账户多股票）"""

    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint("account_id", "stock_id", name="uq_account_stock"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)  # 持仓归属用户
    account_id = Column(
        Integer, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    stock_id = Column(
        Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False
    )
    cost_price = Column(Float, nullable=False)  # 成本价
    quantity = Column(Integer, nullable=False)  # 持仓数量
    invested_amount = Column(Float, nullable=True)  # 投入资金（用于盘中监控）
    sort_order = Column(Integer, default=0)
    trading_style = Column(
        String, default="swing"
    )  # short: 短线, swing: 波段, long: 长线
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    account = relationship("Account", back_populates="positions")
    stock = relationship("Stock", back_populates="positions")


class StockAgent(Base):
    """多对多: 每只股票可被多个 Agent 监控"""

    __tablename__ = "stock_agents"
    __table_args__ = (
        UniqueConstraint("stock_id", "agent_name", name="uq_stock_agent"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_id = Column(
        Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False
    )
    agent_name = Column(String, nullable=False)
    schedule = Column(String, default="")
    ai_model_id = Column(
        Integer, ForeignKey("ai_models.id", ondelete="SET NULL"), nullable=True
    )
    notify_channel_ids = Column(JSON, default=[])
    created_at = Column(DateTime, server_default=func.now())

    stock = relationship("Stock", back_populates="agents")


class AgentConfig(Base):
    __tablename__ = "agent_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, unique=True, nullable=False)
    display_name = Column(String, nullable=False)
    description = Column(String, default="")
    kind = Column(String, default="workflow")  # workflow / capability
    visible = Column(Boolean, default=True)
    lifecycle_status = Column(String, default="active")  # active / deprecated
    replaced_by = Column(String, default="")
    display_order = Column(Integer, default=0)
    enabled = Column(Boolean, default=True)
    schedule = Column(String, default="")
    # 执行模式: batch=批量(多只股票一起分析发送) / single=单只(逐只分析发送，实时性高)
    execution_mode = Column(String, default="batch")
    ai_model_id = Column(
        Integer, ForeignKey("ai_models.id", ondelete="SET NULL"), nullable=True
    )
    notify_channel_ids = Column(JSON, default=[])
    config = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)  # 触发用户(NULL=系统定时)
    agent_name = Column(String, nullable=False)
    status = Column(String, nullable=False)  # success / failed
    trace_id = Column(String, default="")
    trigger_source = Column(String, default="")  # schedule / manual / api
    notify_attempted = Column(Boolean, default=False)
    notify_sent = Column(Boolean, default=False)
    context_chars = Column(Integer, default=0)
    model_label = Column(String, default="")
    result = Column(String, default="")
    error = Column(String, default="")
    duration_ms = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())


class LogEntry(Base):
    __tablename__ = "log_entries"
    __table_args__ = (
        Index("ix_log_entries_time_id", "timestamp", "id"),
        Index("ix_log_entries_trace", "trace_id"),
        Index("ix_log_entries_agent_event", "agent_name", "event"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False)
    level = Column(String, nullable=False)
    logger_name = Column(String, default="")
    message = Column(String, default="")
    trace_id = Column(String, default="")
    run_id = Column(String, default="")
    agent_name = Column(String, default="")
    event = Column(String, default="")
    tags = Column(JSON, default={})
    notify_status = Column(String, default="")
    notify_reason = Column(String, default="")
    created_at = Column(DateTime, server_default=func.now())


class AppSettings(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String, unique=True, nullable=False)
    value = Column(String, default="")
    description = Column(String, default="")


class DataSource(Base):
    """数据源配置（新闻、K线图、行情）"""

    __tablename__ = "data_sources"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)  # "雪球资讯"
    type = Column(
        String, nullable=False
    )  # "news" / "quote" / "kline" / "capital_flow"
    provider = Column(String, nullable=False)  # "xueqiu" / "eastmoney" / "tencent"
    config = Column(JSON, default={})  # 配置参数
    enabled = Column(Boolean, default=True)
    priority = Column(Integer, default=0)  # 越小优先级越高
    supports_batch = Column(Boolean, default=False)  # 是否支持批量查询
    test_symbols = Column(JSON, default=[])  # 测试用股票代码列表
    created_at = Column(DateTime, server_default=func.now())


class CorporateAction(Base):
    """除权除息事件（M1）。东财分红口径入库，回测前复权与 K 线除权标记共用。

    dividend_per_share: 每股派息(元)；bonus_ratio/transfer_ratio: 每10股送/转股数。
    """

    __tablename__ = "corporate_actions"
    __table_args__ = (
        UniqueConstraint(
            "symbol", "market", "ex_date", "source",
            name="uq_corp_action_symbol_ex",
        ),
        Index("ix_corp_action_ex_date", "ex_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False)
    market = Column(String, nullable=False, default="CN")
    ex_date = Column(String, nullable=False)  # YYYY-MM-DD(除权除息日)
    dividend_per_share = Column(Float, nullable=True)
    bonus_ratio = Column(Float, nullable=True)
    transfer_ratio = Column(Float, nullable=True)
    source = Column(String, nullable=False, default="eastmoney")
    created_at = Column(DateTime, server_default=func.now())


class NewsCache(Base):
    """新闻缓存（用于去重）"""

    __tablename__ = "news_cache"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_news_source_external"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String, nullable=False)  # "cls" / "eastmoney"
    external_id = Column(String, nullable=False)  # 来源侧 ID
    title = Column(String, nullable=False)
    content = Column(String, default="")
    publish_time = Column(DateTime, nullable=False)
    symbols = Column(JSON, default=[])  # 关联股票代码列表
    importance = Column(Integer, default=0)  # 0-3 重要性
    created_at = Column(DateTime, server_default=func.now())


class NotifyThrottle(Base):
    """通知节流记录（防止同一股票短时间内重复通知）"""

    __tablename__ = "notify_throttle"
    __table_args__ = (
        UniqueConstraint("agent_name", "stock_symbol", name="uq_agent_stock_throttle"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_name = Column(String, nullable=False)
    stock_symbol = Column(String, nullable=False)
    last_notify_at = Column(DateTime, nullable=False)
    notify_count = Column(Integer, default=1)  # 当日通知次数


class AnalysisHistory(Base):
    """分析历史记录（盘后分析、盘前分析等）

    UNIQUE 约束加 user_id 维度(S1/M3, 2026-08-23): 多账号下同一 (agent, stock, date)
    各自可独立存储一行; user_id=NULL 的旧数据/系统报告继续保留 NULL 共享语义
    (SQLite/PG 的 UNIQUE 都不比较 NULL 与 NULL, 允许多个 NULL 共存)。
    """

    __tablename__ = "analysis_history"
    __table_args__ = (
        UniqueConstraint(
            "agent_name",
            "stock_symbol",
            "analysis_date",
            "user_id",
            name="uq_agent_stock_date_user",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)  # 报告归属(NULL=全局共享)
    agent_name = Column(String, nullable=False)  # "daily_report" / "premarket_outlook"
    stock_symbol = Column(String, nullable=False)  # 股票代码，"*" 表示全部
    analysis_date = Column(String, nullable=False)  # 分析日期 "YYYY-MM-DD"
    title = Column(String, default="")  # 分析标题
    content = Column(String, nullable=False)  # AI 分析结果
    raw_data = Column(JSON, default={})  # 原始数据快照
    agent_kind_snapshot = Column(String, default="workflow")
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class StockContextSnapshot(Base):
    """按股票/日期保存结构化上下文快照（用于跨天记忆）"""

    __tablename__ = "stock_context_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "market",
            "snapshot_date",
            "context_type",
            name="uq_stock_context_snapshot",
        ),
        Index(
            "ix_stock_context_symbol_date",
            "symbol",
            "market",
            "snapshot_date",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False)
    market = Column(String, nullable=False)  # CN/HK/US
    snapshot_date = Column(String, nullable=False)  # YYYY-MM-DD
    context_type = Column(String, nullable=False)  # premarket_outlook/daily_report/...
    payload = Column(JSON, default={})
    quality = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())


class NewsTopicSnapshot(Base):
    """新闻主题快照（按日期和窗口聚合）"""

    __tablename__ = "news_topic_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_date",
            "window_days",
            name="uq_news_topic_snapshot_date_window",
        ),
        Index("ix_news_topic_snapshot_date", "snapshot_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(String, nullable=False)  # YYYY-MM-DD
    window_days = Column(Integer, nullable=False, default=7)
    symbols = Column(JSON, default=[])
    summary = Column(String, default="")
    topics = Column(JSON, default=[])
    sentiment = Column(String, default="neutral")
    coverage = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())


class AgentContextRun(Base):
    """每次 Agent 执行时使用的上下文摘要"""

    __tablename__ = "agent_context_runs"
    __table_args__ = (
        Index("ix_agent_context_agent_date", "agent_name", "analysis_date"),
        Index("ix_agent_context_stock_date", "stock_symbol", "analysis_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_name = Column(String, nullable=False)
    stock_symbol = Column(String, nullable=False, default="*")
    analysis_date = Column(String, nullable=False)  # YYYY-MM-DD
    context_payload = Column(JSON, default={})
    quality = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())


class AgentPredictionOutcome(Base):
    """建议后验评估记录（用于回放与效果统计）"""

    __tablename__ = "agent_prediction_outcomes"
    __table_args__ = (
        Index(
            "ix_prediction_agent_stock_date",
            "agent_name",
            "stock_symbol",
            "prediction_date",
        ),
        Index("ix_prediction_status_horizon", "outcome_status", "horizon_days"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_name = Column(String, nullable=False)
    stock_symbol = Column(String, nullable=False)
    stock_market = Column(String, nullable=False, default="CN")
    prediction_date = Column(String, nullable=False)  # YYYY-MM-DD
    horizon_days = Column(Integer, nullable=False, default=1)  # 1/5/10...
    action = Column(String, nullable=False, default="watch")
    action_label = Column(String, nullable=False, default="观望")
    confidence = Column(Float, nullable=True)
    trigger_price = Column(Float, nullable=True)
    outcome_price = Column(Float, nullable=True)
    outcome_return_pct = Column(Float, nullable=True)
    outcome_status = Column(String, nullable=False, default="pending")
    meta = Column(JSON, default={})
    evaluated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class StockSuggestion(Base):
    """股票建议池 - 汇总各 Agent 建议

    user_id(M2): 建议归属用户, NULL=旧数据; dedupe key 加 user_id 维度, 不同账号可同时存同标的建议。
    """

    __tablename__ = "stock_suggestions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)  # 建议归属(NULL=旧数据)
    stock_symbol = Column(String, nullable=False, index=True)
    stock_market = Column(String, nullable=False, default="CN", index=True)
    stock_name = Column(String, default="")

    # 建议内容
    action = Column(
        String, nullable=False
    )  # buy/add/reduce/sell/hold/watch/alert/avoid
    action_label = Column(
        String, nullable=False
    )  # 中文标签：建仓/加仓/减仓/清仓/持有/观望
    signal = Column(String, default="")  # 信号描述
    reason = Column(String, default="")  # 建议理由

    # 来源追踪
    agent_name = Column(
        String, nullable=False
    )  # intraday_monitor/daily_report/premarket_outlook
    agent_label = Column(String, default="")  # 盘中监测/盘后日报/盘前分析

    # 上下文信息
    prompt_context = Column(String, default="")  # Prompt 上下文摘要
    ai_response = Column(String, default="")  # AI 原始响应

    # 元数据（输入快照/触发原因等）
    meta = Column(JSON, default={})

    # 时间信息
    created_at = Column(DateTime, server_default=func.now())
    expires_at = Column(DateTime, nullable=True)  # 建议过期时间

    # 索引：按市场+股票+时间快速查询
    __table_args__ = (
        Index(
            "ix_suggestion_market_symbol_time",
            "stock_market",
            "stock_symbol",
            "created_at",
        ),
        Index("ix_suggestion_market_expires", "stock_market", "expires_at"),
    )


class EntryCandidate(Base):
    """入场候选榜快照（按天去重，可追溯来源建议与证据）。"""

    __tablename__ = "entry_candidates"
    __table_args__ = (
        UniqueConstraint(
            "stock_symbol",
            "stock_market",
            "snapshot_date",
            name="uq_entry_candidate_stock_date",
        ),
        Index("ix_entry_candidate_score_date", "snapshot_date", "score"),
        Index("ix_entry_candidate_status_updated", "status", "updated_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_symbol = Column(String, nullable=False)
    stock_market = Column(String, nullable=False, default="CN")
    stock_name = Column(String, default="")
    snapshot_date = Column(String, nullable=False)  # YYYY-MM-DD
    status = Column(String, default="active")  # active / inactive / invalidated
    score = Column(Float, nullable=False, default=0)
    confidence = Column(Float, nullable=True)
    action = Column(String, nullable=False, default="watch")
    action_label = Column(String, nullable=False, default="观望")
    signal = Column(String, default="")
    reason = Column(String, default="")
    candidate_source = Column(String, nullable=False, default="watchlist")  # watchlist / market_scan
    strategy_tags = Column(JSON, default=[])
    is_holding_snapshot = Column(Boolean, default=False)
    plan_quality = Column(Integer, default=0)  # 0-100
    entry_low = Column(Float, nullable=True)
    entry_high = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=True)
    target_price = Column(Float, nullable=True)
    invalidation = Column(String, default="")
    source_agent = Column(String, default="")
    source_suggestion_id = Column(Integer, nullable=True)
    source_trace_id = Column(String, default="")
    evidence = Column(JSON, default=[])
    plan = Column(JSON, default={})
    meta = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class MarketScanSnapshot(Base):
    """市场池候选快照（用于多源回退与覆盖诊断）。"""

    __tablename__ = "market_scan_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_date",
            "stock_symbol",
            "stock_market",
            name="uq_market_scan_snapshot_symbol",
        ),
        Index("ix_market_scan_snapshot_day_market", "snapshot_date", "stock_market"),
        Index("ix_market_scan_snapshot_source", "snapshot_date", "source"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(String, nullable=False)  # YYYY-MM-DD
    stock_symbol = Column(String, nullable=False)
    stock_market = Column(String, nullable=False, default="CN")
    stock_name = Column(String, default="")
    source = Column(String, nullable=False, default="market_scan")
    score_seed = Column(Float, nullable=False, default=0.0)
    quote = Column(JSON, default={})
    meta = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class EntryCandidateFeedback(Base):
    """入场候选反馈（用于策略迭代与质量评估）。"""

    __tablename__ = "entry_candidate_feedback"
    __table_args__ = (
        Index("ix_entry_feedback_time", "created_at"),
        Index("ix_entry_feedback_symbol_day", "stock_market", "stock_symbol", "snapshot_date"),
        Index("ix_entry_feedback_source", "candidate_source"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(String, nullable=False, default="")  # YYYY-MM-DD
    stock_symbol = Column(String, nullable=False)
    stock_market = Column(String, nullable=False, default="CN")
    candidate_source = Column(String, nullable=False, default="watchlist")
    strategy_tags = Column(JSON, default=[])
    useful = Column(Boolean, default=True)
    reason = Column(String, default="")
    created_at = Column(DateTime, server_default=func.now(), index=True)


class ManualQueryCandidate(Base):
    """用户主动查询结果入池记录(2026-08-21 机会页整合 P1)。

    - 问小达(tdx)/问财(wencai) 每次自然语言查询返回的标的落一行,
      供 refresh_entry_candidates 当日入池(多源共振计分)。
    - 只记当日, 历史由 entry_candidates 主表承接。
    """

    __tablename__ = "manual_query_candidates"
    __table_args__ = (
        Index("ix_manual_query_day_kind", "snapshot_date", "kind"),
        Index("ix_manual_query_symbol", "stock_market", "stock_symbol"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(String, nullable=False, default="")  # YYYY-MM-DD
    kind = Column(String(16), nullable=False, default="tdx")  # tdx | wencai
    query_text = Column(String, default="")  # 用户原始查询语句
    stock_symbol = Column(String(16), nullable=False)
    stock_market = Column(String(8), nullable=False, default="CN")
    stock_name = Column(String(64), default="")
    rank_in_result = Column(Integer, nullable=True)  # 在查询结果中的名次
    created_at = Column(DateTime, server_default=func.now())


class EntryCandidateOutcome(Base):
    """入场候选后验结果（自动评估）。"""

    __tablename__ = "entry_candidate_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "candidate_id",
            "horizon_days",
            name="uq_entry_outcome_candidate_horizon",
        ),
        Index("ix_entry_outcome_status_horizon", "outcome_status", "horizon_days"),
        Index("ix_entry_outcome_symbol_day", "stock_market", "stock_symbol", "snapshot_date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    candidate_id = Column(Integer, ForeignKey("entry_candidates.id", ondelete="CASCADE"), nullable=False)
    snapshot_date = Column(String, nullable=False, default="")
    stock_symbol = Column(String, nullable=False)
    stock_market = Column(String, nullable=False, default="CN")
    candidate_source = Column(String, nullable=False, default="watchlist")
    strategy_tags = Column(JSON, default=[])
    horizon_days = Column(Integer, nullable=False, default=1)
    target_date = Column(String, nullable=False, default="")  # YYYY-MM-DD
    base_price = Column(Float, nullable=True)
    outcome_price = Column(Float, nullable=True)
    outcome_return_pct = Column(Float, nullable=True)
    hit_target = Column(Boolean, nullable=True)
    hit_stop = Column(Boolean, nullable=True)
    outcome_status = Column(String, nullable=False, default="pending")
    meta = Column(JSON, default={})
    evaluated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class StrategyCatalog(Base):
    """策略目录（可版本化、可启停、可调权重）。"""

    __tablename__ = "strategy_catalog"
    __table_args__ = (
        UniqueConstraint("code", name="uq_strategy_catalog_code"),
        Index("ix_strategy_catalog_enabled", "enabled"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String, nullable=False)
    name = Column(String, nullable=False)
    description = Column(String, default="")
    version = Column(String, nullable=False, default="v1")
    enabled = Column(Boolean, default=True)
    market_scope = Column(String, default="ALL")  # ALL/CN/HK/US
    risk_level = Column(String, default="medium")  # low/medium/high
    params = Column(JSON, default={})
    default_weight = Column(Float, nullable=False, default=1.0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class StrategySignalRun(Base):
    """策略信号执行快照（按日/股票/策略去重）。"""

    __tablename__ = "strategy_signal_runs"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_date",
            "stock_symbol",
            "stock_market",
            "strategy_code",
            "source_candidate_id",
            name="uq_strategy_signal_daily_unique",
        ),
        Index("ix_strategy_signal_snapshot_rank", "snapshot_date", "rank_score"),
        Index("ix_strategy_signal_strategy_market", "strategy_code", "stock_market"),
        Index("ix_strategy_signal_status", "status", "updated_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(String, nullable=False)  # YYYY-MM-DD
    stock_symbol = Column(String, nullable=False)
    stock_market = Column(String, nullable=False, default="CN")
    stock_name = Column(String, default="")

    strategy_code = Column(String, nullable=False)
    strategy_name = Column(String, default="")
    strategy_version = Column(String, default="v1")
    risk_level = Column(String, default="medium")
    source_pool = Column(String, default="watchlist")  # watchlist/market_scan

    score = Column(Float, nullable=False, default=0)
    rank_score = Column(Float, nullable=False, default=0)
    confidence = Column(Float, nullable=True)
    status = Column(String, default="active")  # active/inactive/invalidated
    action = Column(String, default="watch")
    action_label = Column(String, default="观望")
    signal = Column(String, default="")
    reason = Column(String, default="")
    evidence = Column(JSON, default=[])
    holding_days = Column(Integer, default=3)

    entry_low = Column(Float, nullable=True)
    entry_high = Column(Float, nullable=True)
    stop_loss = Column(Float, nullable=True)
    target_price = Column(Float, nullable=True)
    invalidation = Column(String, default="")
    plan_quality = Column(Integer, default=0)

    source_agent = Column(String, default="")
    source_suggestion_id = Column(Integer, nullable=True)
    source_candidate_id = Column(Integer, nullable=True)
    trace_id = Column(String, default="")
    is_holding_snapshot = Column(Boolean, default=False)
    context_quality_score = Column(Float, nullable=True)
    payload = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class StrategyOutcome(Base):
    """策略后验结果。"""

    __tablename__ = "strategy_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "signal_run_id",
            "horizon_days",
            name="uq_strategy_outcome_signal_horizon",
        ),
        Index("ix_strategy_outcome_strategy_horizon", "strategy_code", "horizon_days"),
        Index("ix_strategy_outcome_market_date", "stock_market", "target_date"),
        Index("ix_strategy_outcome_status", "outcome_status", "evaluated_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_run_id = Column(
        Integer, ForeignKey("strategy_signal_runs.id", ondelete="CASCADE"), nullable=False
    )
    strategy_code = Column(String, nullable=False)
    snapshot_date = Column(String, nullable=False, default="")
    stock_symbol = Column(String, nullable=False)
    stock_market = Column(String, nullable=False, default="CN")
    source_pool = Column(String, default="watchlist")
    horizon_days = Column(Integer, nullable=False, default=1)
    target_date = Column(String, nullable=False, default="")  # YYYY-MM-DD
    base_price = Column(Float, nullable=True)
    outcome_price = Column(Float, nullable=True)
    outcome_return_pct = Column(Float, nullable=True)
    hit_target = Column(Boolean, nullable=True)
    hit_stop = Column(Boolean, nullable=True)
    outcome_status = Column(String, nullable=False, default="pending")
    meta = Column(JSON, default={})
    evaluated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class StrategyWeight(Base):
    """策略权重（当前生效值）。"""

    __tablename__ = "strategy_weights"
    __table_args__ = (
        UniqueConstraint(
            "strategy_code",
            "market",
            "regime",
            name="uq_strategy_weight_key",
        ),
        Index("ix_strategy_weight_effective", "effective_from"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_code = Column(String, nullable=False)
    market = Column(String, nullable=False, default="ALL")
    regime = Column(String, nullable=False, default="default")
    weight = Column(Float, nullable=False, default=1.0)
    reason = Column(String, default="")
    meta = Column(JSON, default={})
    effective_from = Column(DateTime, server_default=func.now())
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class StrategyWeightHistory(Base):
    """策略调权历史。"""

    __tablename__ = "strategy_weight_history"
    __table_args__ = (
        Index("ix_strategy_weight_history_time", "created_at"),
        Index("ix_strategy_weight_history_strategy_market", "strategy_code", "market"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_code = Column(String, nullable=False)
    market = Column(String, nullable=False, default="ALL")
    regime = Column(String, nullable=False, default="default")
    old_weight = Column(Float, nullable=False, default=1.0)
    new_weight = Column(Float, nullable=False, default=1.0)
    reason = Column(String, default="")
    window_days = Column(Integer, default=45)
    sample_size = Column(Integer, default=0)
    meta = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())


class FactorWeight(Base):
    """因子权重（当前生效值）——每因子 × 市场,由 IC/IR 自动标定 + 可手动覆盖。

    镜像 StrategyWeight,但作用于因子级(alpha/catalyst/quality/risk/crowd),
    让信号合成从「隐式权重=1 的黑盒」变成「外置可标定」。
    """

    __tablename__ = "factor_weights"
    __table_args__ = (
        UniqueConstraint("factor_code", "market", name="uq_factor_weight_key"),
        Index("ix_factor_weight_effective", "effective_from"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    factor_code = Column(String, nullable=False)
    market = Column(String, nullable=False, default="CN")  # CN/HK/US
    weight = Column(Float, nullable=False, default=1.0)
    is_pinned = Column(Boolean, nullable=False, default=False)  # 手动锁定,标定跳过
    auto_calibrate = Column(Boolean, nullable=False, default=True)  # 关掉则标定跳过
    reason = Column(String, default="")
    meta = Column(JSON, default={})
    effective_from = Column(DateTime, server_default=func.now())
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class FactorWeightHistory(Base):
    """因子调权历史(审计)。"""

    __tablename__ = "factor_weight_history"
    __table_args__ = (
        Index("ix_factor_weight_history_time", "created_at"),
        Index("ix_factor_weight_history_factor_market", "factor_code", "market"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    factor_code = Column(String, nullable=False)
    market = Column(String, nullable=False, default="CN")
    old_weight = Column(Float, nullable=False, default=1.0)
    new_weight = Column(Float, nullable=False, default=1.0)
    ic = Column(Float, nullable=True)
    ir = Column(Float, nullable=True)
    sample_size = Column(Integer, default=0)
    reason = Column(String, default="")  # auto/manual/pinned_skip/auto_off/insufficient_samples
    meta = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())


class MarketRegimeSnapshot(Base):
    """市场状态快照（用于按市场动态调权与解释）。"""

    __tablename__ = "market_regime_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_date",
            "market",
            name="uq_market_regime_day_market",
        ),
        Index("ix_market_regime_snapshot", "snapshot_date", "market"),
        Index("ix_market_regime_type", "regime"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(String, nullable=False)  # YYYY-MM-DD
    market = Column(String, nullable=False, default="CN")  # CN/HK/US
    regime = Column(String, nullable=False, default="neutral")  # bullish/neutral/bearish
    regime_score = Column(Float, nullable=False, default=0.0)  # [-1, 1]
    confidence = Column(Float, nullable=False, default=0.0)  # [0, 1]
    breadth_up_pct = Column(Float, nullable=True)
    avg_change_pct = Column(Float, nullable=True)
    volatility_pct = Column(Float, nullable=True)
    active_ratio = Column(Float, nullable=True)
    sample_size = Column(Integer, default=0)
    meta = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class StrategyFactorSnapshot(Base):
    """每条策略信号的因子分解快照。"""

    __tablename__ = "strategy_factor_snapshots"
    __table_args__ = (
        UniqueConstraint("signal_run_id", name="uq_strategy_factor_signal"),
        Index("ix_strategy_factor_snapshot_score", "snapshot_date", "final_score"),
        Index("ix_strategy_factor_strategy_market", "strategy_code", "stock_market"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_run_id = Column(
        Integer, ForeignKey("strategy_signal_runs.id", ondelete="CASCADE"), nullable=False
    )
    snapshot_date = Column(String, nullable=False)  # YYYY-MM-DD
    stock_symbol = Column(String, nullable=False)
    stock_market = Column(String, nullable=False, default="CN")
    strategy_code = Column(String, nullable=False)
    alpha_score = Column(Float, default=0.0)
    catalyst_score = Column(Float, default=0.0)
    quality_score = Column(Float, default=0.0)
    risk_penalty = Column(Float, default=0.0)
    crowd_penalty = Column(Float, default=0.0)
    source_bonus = Column(Float, default=0.0)
    regime_multiplier = Column(Float, default=1.0)
    final_score = Column(Float, nullable=False, default=0.0)
    factor_payload = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class PortfolioRiskSnapshot(Base):
    """按快照/市场聚合的组合风险画像。"""

    __tablename__ = "portfolio_risk_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_date",
            "market",
            name="uq_portfolio_risk_day_market",
        ),
        Index("ix_portfolio_risk_snapshot", "snapshot_date", "market"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_date = Column(String, nullable=False)  # YYYY-MM-DD
    market = Column(String, nullable=False, default="CN")
    total_signals = Column(Integer, default=0)
    active_signals = Column(Integer, default=0)
    held_signals = Column(Integer, default=0)
    unheld_signals = Column(Integer, default=0)
    high_risk_ratio = Column(Float, nullable=True)
    concentration_top5 = Column(Float, nullable=True)
    avg_rank_score = Column(Float, nullable=True)
    risk_level = Column(String, default="medium")  # low/medium/high
    meta = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class SuggestionFeedback(Base):
    """建议反馈（匿名、轻量）"""

    __tablename__ = "suggestion_feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    suggestion_id = Column(
        Integer,
        ForeignKey("stock_suggestions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    useful = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)


class PriceAlertRule(Base):
    """价格提醒规则

    user_id(S3): 规则归属用户, NULL=旧数据; 端点按 user 过滤, 缺失则返回 404。
    """

    __tablename__ = "price_alert_rules"
    __table_args__ = (
        Index("ix_price_alert_enabled", "enabled"),
        Index("ix_price_alert_stock_enabled", "stock_id", "enabled"),
        Index("ix_price_alert_user", "user_id", "updated_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)  # 规则归属(NULL=旧数据)
    stock_id = Column(
        Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False
    )
    name = Column(String, nullable=False, default="")
    enabled = Column(Boolean, default=True)
    condition_group = Column(JSON, default={})
    market_hours_mode = Column(String, default="trading_only")  # always/trading_only
    cooldown_minutes = Column(Integer, default=30)
    max_triggers_per_day = Column(Integer, default=3)
    repeat_mode = Column(String, default="repeat")  # once/repeat
    expire_at = Column(DateTime, nullable=True)
    notify_channel_ids = Column(JSON, default=[])
    last_trigger_at = Column(DateTime, nullable=True)
    last_trigger_price = Column(Float, nullable=True)
    trigger_count_today = Column(Integer, default=0)
    trigger_date = Column(String, default="")  # YYYY-MM-DD
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    stock = relationship("Stock")


class PriceAlertHit(Base):
    """价格提醒命中记录"""

    __tablename__ = "price_alert_hits"
    __table_args__ = (
        Index("ix_price_alert_hits_rule_time", "rule_id", "trigger_time"),
        UniqueConstraint(
            "rule_id",
            "trigger_bucket",
            name="uq_price_alert_rule_bucket",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    rule_id = Column(
        Integer, ForeignKey("price_alert_rules.id", ondelete="CASCADE"), nullable=False
    )
    stock_id = Column(
        Integer, ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False
    )
    trigger_time = Column(DateTime, server_default=func.now(), nullable=False)
    trigger_bucket = Column(String, nullable=False, default="")  # YYYYMMDDHHMM
    trigger_snapshot = Column(JSON, default={})
    notify_success = Column(Boolean, default=False)
    notify_error = Column(String, default="")
    created_at = Column(DateTime, server_default=func.now())

    rule = relationship("PriceAlertRule")
    stock = relationship("Stock")


class PaperTradingAccount(Base):
    """模拟盘账户（单例）"""

    __tablename__ = "paper_trading_account"

    id = Column(Integer, primary_key=True, autoincrement=True)
    initial_capital = Column(Float, nullable=False, default=1000000.0)
    current_capital = Column(Float, nullable=False, default=1000000.0)
    total_pnl = Column(Float, nullable=False, default=0.0)
    total_trades = Column(Integer, nullable=False, default=0)
    winning_trades = Column(Integer, nullable=False, default=0)
    max_drawdown_pct = Column(Float, nullable=False, default=0.0)
    peak_capital = Column(Float, nullable=False, default=1000000.0)
    enabled = Column(Boolean, default=True)
    excluded_markets = Column(JSON, default=[])  # 排除的市场，如 ["US"]（兼容旧字段，由 market_allocations 派生）
    # 各市场投资比例 {"CN":0.5,"HK":0.3,"US":0.2}，比例 0~1、合计 ≤ 1；比例 0 表示不投入该市场
    market_allocations = Column(JSON, default={})
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class PaperTradingPosition(Base):
    """模拟盘持仓"""

    __tablename__ = "paper_trading_positions"
    __table_args__ = (
        Index("ix_paper_pos_status", "status"),
        Index("ix_paper_pos_symbol_market", "stock_symbol", "stock_market"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_symbol = Column(String, nullable=False)
    stock_market = Column(String, nullable=False, default="CN")
    stock_name = Column(String, default="")
    quantity = Column(Integer, nullable=False, default=100)
    entry_price = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=True)
    target_price = Column(Float, nullable=True)
    current_price = Column(Float, nullable=True)
    highest_price = Column(Float, nullable=True)  # 持仓期最高价(移动止损用)
    unrealized_pnl = Column(Float, nullable=False, default=0.0)
    status = Column(String, nullable=False, default="open")  # open/closed
    signal_run_id = Column(Integer, nullable=True)
    signal_snapshot_date = Column(String, default="")
    signal_action = Column(String, default="")
    strategy_code = Column(String, default="")
    opened_at = Column(DateTime, server_default=func.now())
    closed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class PaperTradingTrade(Base):
    """模拟盘已平仓记录"""

    __tablename__ = "paper_trading_trades"
    __table_args__ = (
        Index("ix_paper_trade_closed", "closed_at"),
        Index("ix_paper_trade_symbol", "stock_symbol", "stock_market"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    stock_symbol = Column(String, nullable=False)
    stock_market = Column(String, nullable=False, default="CN")
    stock_name = Column(String, default="")
    quantity = Column(Integer, nullable=False, default=100)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=False)
    pnl = Column(Float, nullable=False, default=0.0)
    pnl_pct = Column(Float, nullable=False, default=0.0)
    exit_reason = Column(String, nullable=False, default="")  # stop_loss/target_price/signal_reversal/manual
    signal_run_id = Column(Integer, nullable=True)
    signal_snapshot_date = Column(String, default="")
    strategy_code = Column(String, default="")
    holding_days = Column(Integer, default=0)
    opened_at = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, server_default=func.now())
    meta = Column(JSON, default={})


class ChatConversation(Base):
    """AI 对话会话

    user_id(S2): 会话归属用户, NULL=旧数据/系统; 多账号下各自只看到自己的会话。
    端点(list/get/delete/send_message)按 user 过滤, 缺失则返回 404 防账号探测。
    """

    __tablename__ = "chat_conversations"
    __table_args__ = (
        Index("ix_chat_conv_updated", "updated_at"),
        Index("ix_chat_conv_stock", "stock_symbol", "stock_market"),
        Index("ix_chat_conv_user", "user_id", "updated_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)  # 会话归属(NULL=旧数据/系统)
    title = Column(String, default="")
    stock_symbol = Column(String, nullable=True)
    stock_market = Column(String, nullable=True)
    ai_model_id = Column(Integer, nullable=True)
    initial_context = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ChatMessage(Base):
    """AI 对话消息"""

    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_msg_conv", "conversation_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(Integer, nullable=False)
    role = Column(String, nullable=False, default="user")  # user/assistant/system
    content = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, server_default=func.now())


class Notification(Base):
    """站内消息中心。

    后台任务(报告/Agent/策略刷新)完成或失败时写一条, 前端铃铛轮询未读数。
    与外发推送(notify_channels)互补: 站内保证"关掉页面也不失联", 外发保证"人不在也能收到"。

    user_id(S4): 通知归属用户, NULL=旧数据/全局系统通知; 多账号下端点按 user 过滤。
    """

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_unread", "read_at", "created_at"),
        Index("ix_notifications_trace", "trace_id"),
        Index("ix_notifications_user", "user_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)  # 通知归属(NULL=旧数据/全局系统通知)
    # 分类: agent_run / report / strategy / price_alert / system
    category = Column(String, nullable=False, default="system")
    # 级别: info / success / warning / error
    level = Column(String, nullable=False, default="info")
    title = Column(String, nullable=False, default="")
    body = Column(Text, default="")
    # 点击跳转的前端路由, 如 /agents 或 /reports?name=xxx
    link = Column(String, default="")
    # 关联溯源
    source = Column(String, default="")       # agent_name / job 名
    trace_id = Column(String, default="")
    # 外发结果: pending / sent / failed / skipped(无渠道)
    push_status = Column(String, default="")
    push_error = Column(String, default="")
    # 当次实际尝试的渠道回执（仅 ID/名称/类型/状态/错误，不保存密钥）
    push_channels = Column(JSON, default=list)
    read_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class LLMUsage(Base):
    """LLM 调用日志(2026-08-15): 每次 AI 调用的 token 用量/耗时/场景, 用于成本统计。

    轻量设计: 不存 prompt/回复原文, 只存计数。一个月几千行, 无性能压力。
    记录失败静默(不影响主流程)。
    """

    __tablename__ = "llm_usage"
    __table_args__ = (Index("ix_llm_usage_created", "created_at"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 场景: chat(对话助手) / premarket(盘前报告) / postmarket(盘后报告) / referee(AI裁判) / selfcheck / insights / other
    scene = Column(String, nullable=False, default="other", index=True)
    model_name = Column(String, default="")
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    latency_ms = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())


class Board(Base):
    """thsdk 板块/概念注册表(阶段2.1, v0.3.0)。

    - block_code: thsdk 板块代码, URFI 前缀(如 URFI883404), 唯一
    - board_type: "industry"(行业) / "concept"(概念)
    - 由工作日 08:30 cron 从 fetch_ths_industry / fetch_ths_concept 同步
    """

    __tablename__ = "boards"

    id = Column(Integer, primary_key=True, autoincrement=True)
    block_code = Column(String, unique=True, nullable=False, index=True)  # URFI883404
    name = Column(String, nullable=True)
    board_type = Column(String, nullable=True)  # "industry" / "concept"
    last_synced_at = Column(DateTime, nullable=True)


class BoardDaily(Base):
    """板块日线(阶段2.1, v0.3.0): 每板块每日涨跌幅/资金净流入/量能。

    - date: 交易日 (YYYY-MM-DD)
    - change_pct: 当日涨跌幅(%)
    - fund_net: 当日资金净流入(单位与 thsdk 一致, 元/万元视源而定)
    - volume: 当日量能(成交额 元 或 成交量, 视源而定)
    同一 (block_code, date) 唯一, 重复同步时更新不重复插入。
    """

    __tablename__ = "board_daily"
    __table_args__ = (
        UniqueConstraint("block_code", "date", name="uq_board_daily_code_date"),
        Index("ix_board_daily_code_date", "block_code", "date"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    block_code = Column(String, nullable=False)
    date = Column(Date, nullable=False)  # YYYY-MM-DD
    change_pct = Column(Float, nullable=True)
    fund_net = Column(Float, nullable=True)  # 资金净流入
    volume = Column(Float, nullable=True)


class AuctionAnomalyRecord(Base):
    """竞价异动池落库(阶段1.2, v0.3.0): 工作日 09:25 竞价异动股快照。

    - 由 cron(register_cron, 挂 report_scheduler)+ POST /api/auction/sync 写入
    - 每交易日开盘前把当日竞价异动股按行追加, created_at 打时间戳, 供
      get_anomaly_history 做多日历史追踪(某股连日竞价异动 = 强关注信号)。
    - 不做唯一约束: 同日允许重复快照(手动 sync + cron 双写), 历史查询去向重/取最新。
    """

    __tablename__ = "auction_anomaly_records"
    __table_args__ = (
        Index("ix_auction_anomaly_sym_created", "symbol", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(16), nullable=False, index=True)  # 6 位 A 股代码
    name = Column(String(64), default="")
    gap_pct = Column(Float, nullable=True)        # 高开幅度 / 涨跌幅 %
    withdraw_rate = Column(Float, nullable=True)  # 撤单率 (0~1 或 %, 视源而定)
    volume_ratio = Column(Float, nullable=True)   # 量比
    note = Column(String(255), default="")
    created_at = Column(DateTime, server_default=func.now(), index=True)


class MarketPhaseDaily(Base):
    """市场情绪周期每日指标 + 阶段标签(2026-08-24, 6 阶段体系)。

    - 原始指标由 src/core/market_phase.compute_daily_metrics 从
      MarketSentimentCollector().get_limit_up_pool() 派生:
        first_board, ge2/3/5_count, max_height, promo_rate, seal_rate
    - 上证当日涨跌幅 sh_index_pct 用于弱档否决(正向阶段 → repair)
    - phase 字段在 sync 时由 classify_phase_series 重算(EMA + 2 日确认),
      取值见 src.core.market_phase.PHASE_* 常量
    - 由 POST /api/market/phase/sync 写入, create_all 自动建表
    """
    __tablename__ = "market_phase_daily"

    date = Column(Date, primary_key=True)  # YYYY-MM-DD
    first_board = Column(Integer, default=0, nullable=False)
    ge2_count = Column(Integer, default=0, nullable=False)
    ge3_count = Column(Integer, default=0, nullable=False)
    ge5_count = Column(Integer, default=0, nullable=False)
    max_height = Column(Integer, default=0, nullable=False)
    promo_rate = Column(Float, nullable=True)        # 晋级率, 池不足时 None
    seal_rate = Column(Float, nullable=True)         # 封板率, 数据源不可得时 None
    sh_index_pct = Column(Float, nullable=True)      # 上证当日涨跌幅 %
    phase = Column(String(32), default="", nullable=False)  # ice/ignite/rally/climax/ebb/repair/accumulating
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
