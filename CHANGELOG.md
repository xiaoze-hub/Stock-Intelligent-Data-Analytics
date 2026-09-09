# Changelog

## 2026-09-10

### fix

- S1止损跌停顺延验收: 确认锁死日`continue`在止损检查之前(止损同样顺延), 已有`test_engine_down_lock_exit_deferred`覆盖, 无需改代码
- S2 outcome可执行基准: `evaluate_strategy_outcomes`基准改次日开盘(`_pick_open_after`, 与回测入场一致), 旧降级链保留并记`meta.base_kind`(next_open/signal_px)
- S3滑点分档: `CostModel.slippage_bps_for(市值)`大3/中5/小10/微15bps, `fill()`/`round_trip_pnl()`接受覆盖, 不传零改动
- S4 chat包快照测试: `tests/test_chat_package.py`锁死7路由+12导出+子模块归属(防拆分类/mock打点事故)
- S5a组合熔断(记录阶段): `assess_portfolio_risk`纯函数(日亏3%/回撤8%/集中40%) + `daily_risk_check`每轮扫描聚合+触发写站内通知(同日去重)+结果进scan返回；本阶段不阻断开仓，S5b再接`_check_entries`冻结
- S5b熔断阻断: `_scan_sync`先算risk，frozen则跳过`_check_entries`(开仓0)，`_check_exits`永远放行；mock编排测试锁死两种状态

## 2026-09-09

### doc

- README中英同步v0.4.34(徽标+ACR tag+来源标注/TQ默认关闭说明)；Release v0.4.34补亮点+国内ACR拉取地址；About已OK(描述/主页/topics满20个，无需动)

### update

- 图表库npm化(第6项b): `lightweight-charts@5.2.1` pin进biz-ui依赖，删index.html双CDN；`InteractiveKline`/`MinuteLwcChart`改typed v5直调（去getLW/any垫片、去v4兼容分支、markers/crosshair/time全类型化，`mode:1`→`CrosshairMode.Magnet`同值）；删死文件`MinuteEChart.tsx`（ECharts分时早被LWC替换后的残留）。tsc+vite build双过，lib打进InteractiveKline chunk（240KB含运行时），dist无CDN引用
- 发版门禁脚本重写(第6项a): `scripts/pre_release_check.sh` = tsc + ruff + CI同款pytest + marketdata + 联网集尝试(非阻断记录)。附带发现: CI排除表含不存在的 `test_main_flow_hengsheng.py`(已从脚本剔除)；联网集本地158过/8挂经基线worktree验证为历史问题(thsdk router注册断言+批量污染)，与本次改动无关
- 股票页拆分(第2项前端): `Stocks.tsx:3421` → 类型抽 `stocks/types.ts`(230行) + 纯函数抽 `stocks/portfolio.ts`(165行, round2/mergePortfolioQuotes等), 页面剩3030行。tsc+vite build双过；组件内112个hook的域拆分需配合可视化验收，留后续
- 对话API拆包(第2项后端): `chat.py:2980` → `chat/` 包(prompts140/context763/tools1100/engine545/routes478, 无逻辑变更, AST切分)。`__init__` 重导出外部依赖面(router/_get_ai_client/_execute_tool等12个), 旧import路径零改动；`test_chat_stream` mock打点改按定义/引用模块(engine+routes/tools)；`test_ai_layer_data_sources`源码扫描路径跟随tools.py。全量门禁1038+1与拆前一致

### fix

- 行情来源标注透传(原第5项TQ熔断的剩余缺口; 熔断/timeout/key轮换/metrics引擎早已有): `Quote.source` + 引擎命中回填(显式源不覆盖) + `_quote_to_row` + `/quotes` 响应 `source` 字段。前端可据此显式标注来源/无数据
- DB日志降级: `LOG_DB_LEVEL` 默认 WARNING(原 DEBUG 全量收录), `LOG_DB_LEVEL=DEBUG` 可恢复排查；控制台 `LOG_LEVEL` 不变
- Pydantic V2迁移: 9文件12处 `class Config` → `model_config = ConfigDict(...)`，测试零 `PydanticDeprecatedSince20` 告警
- 回测涨跌停约束建模(原TODO): `limit_pct_for`(主板10/创科20/北交所30/ST 经 overrides 传5%) + 入场触涨停整笔跳过(计 skipped) + 一字跌停日不可卖出顺延 + 止盈 high 按涨停封顶。两个旧测试按真实约束修正(10→11精确涨停不再视为可买/12.5超涨停 high 不再触发止盈), 新增4例
- 通达信客户端接口默认关闭: `PANWATCH_ENABLE_TQ` 总开关(默认0, `_rpc` 快失败不发请求) + 种子 `通达信TQ行情/K线 enabled=False` + `_m126` 存量库 tq 行置 disabled。开启需 env=1 且数据源页手动启用。关闭后行情/K线走腾讯/东财降级链, more-info 回404“无数据”, 决策先锋 L2 卡片 `available=False` 置灰, formula/ZLJC 返回空

## 2026-09-01

### fix

- **CI test 门禁修复**(ghcr 一直无法自动发版的原因, 2026-09-01)。根因 4 处: ①`src/web/migrations.py` 的 `_m111_strategy_layer` 是 PG-only 方言(SERIAL/`::text`/ON CONFLICT), 缺 `_dialect_is_pg` 保护, 测试 sqlite 环境跑它报 `unrecognized token ":"`(对齐其它 PG-only 迁移模式加保护); ②`tests/conftest.py` 的 `_init_test_db` 只调 `init_db()` 没先 `import src.web.models`, create_all 建空库 → 后续 `no such table: users`(先 import models 注册全部表); ③CI `build-and-push-image.yml` test job 只设 `AUTH_ALLOW_DEFAULT_ADMIN` 没设 `AUTH_PASSWORD`, 测试硬编码 `admin/xz.170530` 登录 → 401(补 env); ④S5 归属校验(2026-08-26)后 `get_run_progress` 查询序列/`export_tradingagents_analysis_pdf`/`chat` 工具执行加了 `user` 参数, 三个测试文件未同步 → `Depends object has no attribute 'id'` / mock 签名不匹配(补 user 参数 + 重写 run_progress mock 顺序 + tdx 异常类型改 NotImplementedError)。修复后 CI 命令全量 `1033 passed, 1 skipped` + marketdata `190 passed`。

## 2026-08-31

### feature

- 通达信 TQ formula 引擎接入 + `_TQ_URL` 修复: `vendors/tq.py` 新增 `formula_mul`(formula_process_mul_zb 批量指标公式) + `formula_zb_single`(单只, 依赖客户端打开数据); `core/marketdata_client.py` 新增 `md_formula_mul` + `md_main_flow_zljc`(ZLJC 主力进出三档 jcl/jcm/jcs)。周期参数须 stock_period+periodstr 同传(缺 periodstr 报 periodstr error)。内置公式: MACD/ZLJC/ZJL。L2_AMO 是公式函数非独立公式, 需客户端公式管理器自定义后按名调。同时修复 `_TQ_URL` 硬编码旧 frps 地址 `172.18.0.1:5100`(已不通) → 改读 `TDX_QUANT_URL` 环境变量(生产容器注入 `172.27.16.1:17709` 直连), 兜底旧地址
- 通达信 .tck 逐笔解析器落地 + 暗盘双数据源框架: 新增 `src/core/tdx_tick_parser.py`(36字节委托号级解析, 官方方向 2B主买/2S主卖 + 主动侧委托号 a28/a32 + 撤单 0C) + `src/core/dark_l2.py`(fetch_l2_ticks 接入层)。dark_flow 通过 `PANWATCH_DARK_SOURCE=tdx_tck` 切换: .tck 文件存在用官方方向(盘后精确), 找不到自动回退腾讯逐笔(盘中实时兜底)。官方方向比腾讯自解析方向准(交易所级标记), 修正暗盘方向误差。局限: .tck 仅主动侧(被动 maker 未落盘), 盘后数据(超盘回放落盘 zst_cache)

### fix

- 拆单识别重写(暗盘对齐同花顺, 两次修复): ①`_classify_split` 改位置主判据(获利区卖=主力派发、套牢区买=主力抄底), 补"散户追涨/散户割肉"两类 + `TestClassifySplit` 6 例。②`_detect_split_orders` 重写为"时间间隔聚类(gap10s+window90s)+全簇累计暗盘流入/流出", 修复方向反+漏检90%(金健米业 600127 同花顺暗盘流入8亿 vs 我们净流出358万 → 修复后暗盘流入10.2亿/净流入+4.5亿, 方向对齐量级125倍)。根因: 同花顺暗盘=所有拆单簇(买簇=流入/卖簇=流出), 不分主力/散户; 原逻辑"连续同方向5-100万"在涨停股成交密集夹杂反向/超范围单时 seq 频繁被打断漏检90%+, 且把获利区买入误判散户排除在暗盘外。contrarian/reason 降级为意图展示属性(不再决定是否计入暗盘)

## 2026-08-30

### feature

- 决策先锋三指标(GS策略 + AI机构活跃度 + L2主力净流入, 盘中实时): src/core/decision_pioneer.py 新增 AI机构活跃度(7因子MAX×1.2零调参, 阈值1.56/3/6, 连强天数+5日均值) + GS策略(BB0慢线/A0快线交叉G买S卖) + L2主力净流入(TQ get_more_info Zjl_HB, 明盘口径=同花顺主力净额); 后端 /api/decision-pioneer/{symbol} 端点 + /api/dark-flow 增 l2 字段; 前端 DecisionPioneerCard 卡片挂分时图下方; AI助手 get_decision_pioneer 工具; 盘中监测推送增"决策先锋三指标"段
- 决策先锋选股池(三指标共振扫描, 盘中实时): src/web/api/stock_pool.py 新增 POST /api/stock-pool/screen(批量算GS+机构活跃度+L2净流入, 按共振强度排序); 前端机会页新增"选股池"Tab
- K线缓存表迁移(_m125): klines 表自动建表(优先 TimescaleDB hypertable, 无扩展降级普通表), 恢复 PG 直读支撑机会页全盘扫描; 生产 PG 换 timescale/timescaledb:latest-pg16 镜像
- 暗盘资金前端展示: 拆单识别结果(主力伪装的中小单, 逆势+位置确认)从 compute_dark_flow 暴露到 /api/dark-flow 响应(新增 dark_order 字段), 前端 DarkFlowCards 新增「暗盘资金(拆单识别)」卡片(暗盘净额+疑似主力买/卖+散户顺势/解套+拆单组明细top5)

### fix

- 决策先锋 L2 主力净流入单位修复: 通达信 get_more_info 的 Zjl_HB 单位为万元(与成交额 Amount 同量纲), 此前 _l2_summary 误当元返回致前端/推送显示小 1 万倍; 现统一转元返回
- 修正三指标口径: L2 Zjl_HB 是「主力净流入」(明盘口径, 同花顺主力净额), 非「暗盘资金」; 暗盘=拆单识别走 dark_flow(腾讯逐笔+.tck)。修正 decision_pioneer.py/intraday_monitor.py/darkflow.py/DecisionPioneerCard.tsx 中"对齐同花顺暗盘"的错误注释

### update

- 决策先锋三指标 GS 定位调整: GS 从"买卖触发"降级为"趋势过滤"(知乎第三方实测 GS 买卖点滞后一天, 日线均线交叉天然右侧滞后); 共振判定改为 GS 只做 S区过滤 + 活跃度/L2资金打分; 前端 GS 卡片改"趋势过滤"标注

### fix

- 日K线不显示修复: klines 表缺失走 fallback 联网源时 date 返回 '20260828'(8位无横杠), 前端 parseBusinessDay 正则要求 'YYYY-MM-DD' → 全部过滤空白; 后端 _fmt_date 统一格式 + 前端正则兼容 8 位

## 2026-08-29

### feature

- 通达信 L2 暗盘资金接入口(逐笔还原+十档盘口+自建分档, 盘后 ZCode TQ4 采集): types.py 新增 DarkFlowTq 强类型 + marketdata_client.md_dark_flow_tq 读取 DATA_DIR/darkflow/*.json + /quotes/{symbol}/dark-flow-tq 端点 + 前端暗盘资金卡片(超大/大/中/小单净额+拆单委托+撤单比+托盘/压盘/锁盘), 盘后数据无文件时静默降级

## 2026-08-27

### fix

- 盘中监测等 Agent 推送渠道失效: `notify_task_done` 无 user_id 上下文时兜底推 owner(role=owner)。多用户改造后 notify_channels 全归属 user_id、无全局渠道，此前 agent 后台任务收尾通知调 `push_notification` 未传 user_id → `_build_notifier(None)` 查不到渠道 → 站内通知永远 skipped("未配置通知渠道")、不外发，用户收不到盘中监测等 Agent 的信号/完成通知

## 2026-08-26

### feature

- 通达信 TQ 数据源回归开源仓库(撤销"移出私有维护"): tq.py 重新入 git, server.py/registry 重新注册 TQ 为 quote/kline 主源(priority 0), 隧道断开自动降级腾讯/东财
- MoreInfo 扩展指标透传(104字段): types.py 新增 MoreInfo 强类型 + TqMoreInfoVendor + /quotes/{symbol}/more-info 端点 + 前端洞察卡片(封成比/封单额/撤单量/逐笔笔数等)
- L2 主力净额字段: MoreInfo 新增 zjl(主买净额)/zjl_hb(主力净流入), 对齐同花顺口径, 前端卡片展示
- 通达信本地数据解析器 src/collectors/tdx_local_parser.py: 北向分券商持仓(signals_sys_*.dat) + 概念归属(extern_sys.txt), 含 11 单测
- L2AMO 分档资金 scripts/l2_amo_formula.py: 系统内置 ZJBY 公式输出超/大/中/小 4 档净额, 与 get_more_info Zjl_HB 精确对齐(误差<0.01)

### fix

- 多用户隔离修复(S5, P0×5+P1×2, 4 账号并存下跨账号读/删数据):
  - agents.py 深度分析三端点(latest/analysis/pdf)与 run 历史/进度补 user_id 过滤(NULL 兼容), trace_id 归属校验防枚举他人运行详情
  - 建议池读端全链路隔离(suggestion_pool get_suggestions_for_stock/get_latest_suggestions 子查询按用户圈定; suggestions.py 三端点注入 user), 非本人建议不下发 prompt_context/ai_response, cleanup 限本人范围
  - chat.py 四个上下文 helper(自选/个股/持仓/推荐问题)与 tool 调用链(_execute_tool/_run_tool_loop/_run_tool_loop_stream)全传 user, 只注入本人持仓/建议/报告/通知/自选
  - accounts.py delete_account 补归属校验(唯一漏网的写操作, 越权返回 404 防账号探测); dashboard.py overview insights/get_brief 按 user 过滤报告
  - 写端补 user_id 归属: tradingagents agent save_analysis/save_suggestion 从 context.user 提取, intraday scan save_suggestion 同步修复(防止建议写到 NULL 共享池被他人读到 Prompt/AI 原文)
  - 顺手修 strategies.py apply 的 amount 字段恒为 None(行情对象字段名是 turnover, 改 getf('amount') or getf('turnover'))
- 修复 migration boolean 类型 bug: agent_configs.visible/enabled 与 users.is_active 用 0/1 导致 PG 严格类型启动崩溃(SQLite 宽松不报错), 改 false/true
- 修复 migration SQLite→PG 系统性兼容(生产临时库实测 101-124 全跑通): AUTOINCREMENT→SERIAL PRIMARY KEY(19处)、DATETIME→TIMESTAMP(31处)、INSERT OR IGNORE→INSERT...ON CONFLICT DO NOTHING(5处)、boolean 列 INTEGER→BOOLEAN+seed 0/1→True/False、json 列 strategy_tags LIKE 加 ::text 转型(10处)+TEXT→JSON(3处)、_m117 try/except ALTER→_add_column_if_missing(PG 失败会 abort transaction)、_m124 PG 分支加 DROP CONSTRAINT IF EXISTS 幂等
- 修复 migration 数据回填缺列+OR REPLACE 语法(有数据临时库实测回填通过): entry_candidates 回填补 candidate_source(NOT NULL 违反)、建表 SQL 补 candidate_source/strategy_tags/is_holding_snapshot/plan_quality 列+evidence/plan/meta TEXT→JSON、INSERT OR REPLACE→INSERT...ON CONFLICT DO UPDATE(2处)
- 技术性修复(5+1 评审 B 轨, 4 项):
  - migrations.py run_versioned_migrations 失败记录事务毒化: PG 下 runner 抛异常后同事务写 success=0 必报 current transaction is aborted, 失败记录写不进/原始错误被掩盖/启动持续失败 → 失败记录改用新连接新事务 INSERT...ON CONFLICT 写入后 re-raise 原始异常(幂等语义不变)
  - database.py 四处 last_insert_rowid()(SQLite 专属, PG 下报 function does not exist 启动即崩) → 新增 _insert_returning_id 方言无关助手: PG 走 INSERT...RETURNING id, SQLite 保留 last_insert_rowid
  - kline_backfill_scheduler.py schedule_one_off: APScheduler 兜底块原错误缩进嵌在 except 内(只在 Stream publish 抛异常时触发, 实际是死代码)→ 移到正常流程无条件调度; 删除死字符串字面量; Stream 发布保留作未来 worker 预留
  - quote_stream.py WebSocket 订阅泄漏: 删除重复的 accept+subscribe 块(复制粘贴残留, 二次 accept 必抛且首次订阅队列永不退订, 每次断连泄漏一个队列); send 失败区分 WebSocketDisconnect 与发送异常; _ensure_aggregator check-then-set 加 threading.Lock 防并发首连起双聚合器线程

### update

- TQ 数据源(vendors/tq.py + registry/seed 注册)移出开源仓库,转私有维护(依赖个人小主机通达信网关,他人无法复现)。生产环境保留:文件仍在生产容器与本地工作区,后续 TQ 迭代只在私有副本进行,不再进 git。仓库内 quote/kline 路由回到 腾讯→ths→新浪/智兔 主链。

## 2026-08-25

### feature

- 交易策略新增3(情绪周期自适应/事件预期差/暗盘资金跟随): `panwatch_strategies.yaml` + `strategy_catalog.py` 注册，11个孤岛模块首次接入策略引擎
- TimesFM(Google)接入替代Lag-Llama: `forecast_models.py` 新增 `timesfm_predict` + 5模型投票(`model_weights.py` 0.35/0.25/0.20/0.15/0.05)，`forecast_server.py` 全链路打通，`forecast_requirements.txt` +timesfm
- DarkFlowCard前端: `DarkFlowCard.tsx` 主力意图+内外盘占比+七口诀标签，背离时⚠️提示+「咨询AI助手」按钮

### fix

- 暗盘七口诀全修(`dark_flow.py`): ⑥永不触发→缩量+震荡，⑦恒真→失衡+不动+放量，③④追加量能确认，抽公共常量防漂移
- AI全局熔断(`ai_client.py`): 新增 `GlobalLLMCircuitBreaker` 按service分桶(10/min+600s冷却)，三方法入口限流+429优雅降级，指数退避
- K线/资金流TTL收紧: `kline_collector.py` 180→60s(集合竞价15s)，`capital_flow_collector.py` 600→120s，区分竞价/连续竞价
- 方向预测摘除: `chat.py` get_forecast加31.7%不可靠警告+去suggested_questions引流，`daily_report.txt` 明日关注→证伪清单
- ECharts主题注册: `echarts-core.ts` 注册SIDA双态主题，`echarts-theme.ts` CSS变量双态，`useECharts` hook+ResizeObserver，4图表白屏修复


## 2026-08-25

### update

- 首页 UI 重构(v0.4.11): 信息优先级重排——「今日要紧事+组合体检+机会精选」工作台上提到
  大盘指标带之后(盯盘系统先看自己的票, 大盘级情绪/主线/资金流/板块榜下沉); 涨跌红绿三套色值
  统一到 stock.up/down token(#E53935红涨/#43A047绿跌, 清理 red-600/emerald/rose 硬编码);
  KPI带跌停无数据源显式标注「暂无」; 机会精选满格评分进度条改为详情箭头; 组合体检无持仓空态
  紧凑化+去添加持仓引导

## 2026-08-25

### feature

- K线盘前预缓存(v0.4.10): 工作日 09:20 主动增量入库「自选+候选池当日」全部标的
  (kline_precache.py, 复用 ingest_symbol 幂等入库), 开盘后自选页/机会页/预测/AI
  直接命中 PG 缓存, 对外请求数砍约80%, 从源头降低触发数据源风控的概率


## 2026-08-25

### fix

- /api/klines PG 优先路径加最小条数校验(<30根视为无效, 继续走联网+新浪兜底) —
  修复新加股回填失败时 PG 只有2根也直接命中返回的问题(v0.4.9.1 兜底因此没触发)


## 2026-08-25

### fix

- 个股K线新浪直拉兜底(v0.4.9.1): 新自选股(有研新材600206等5只)PG 仅6行且
  800天补数因腾讯501+东财断连全失败 → K线只显示两天。KlineCollector 在
  PG兜底之后追加新浪 CN_MarketData 日K直拉(容器内实测可达且含当日数据),
  /api/klines 与 summary 链路立即恢复完整历史


## 2026-08-25

### fix

- AI 反证层 429 风暴根治(v0.4.9, 五连修):
  ① 全局令牌桶限速 ≤10 次/分钟 + 撞 429 后 10 分钟全局面板冷却(期间反证直接静默降级)
  ② AIClient 关闭 SDK 自动重试(max_retries=0), 消灭 retry 放大
  ③ 反证结果按 (股票,日期) 写 biz_cache(TTL 6h), 同股一天只评一次
  ④ _run_coro 改进程级复用后台事件循环(run_coroutine_threadsafe),
     修 "no running event loop"/"Event loop is closed"(旧实现每次新建+close,
     AsyncOpenAI 客户端持有旧 loop 引用)
  ⑤ /api/quotes/ws 加入限流豁免列表 — WebSocket 行情轮询被自家限流挡(429 重连风暴)


## 2026-08-25

### fix

- 自选页技术指标全部显示"观望"修复: summary 接口冷启动(主力意图逐笔翻页)20-30s,
  前端默认 20s 超时导致首轮大面积 abort → kline=null → 徽章回落"观望",
  点进弹窗单只重拉时后端已有缓存才有真数据。双修:
  ① 前端自选页 K线摘要拉取显式 timeoutMs=45s
  ② 后端 summary 进程内缓存 TTL 30s→5min(冷启动重算太贵, 指标分钟级刷新足够)


## 2026-08-25

### update

- ECharts 按需加载(v0.4.8): 新增 lib/echarts-core.ts 统一按需注册
  (Bar/Gauge/Line/Candlestick/Heatmap + 常用组件 + CanvasRenderer),
  全部图表组件切换至该入口, Dashboard chunk 减重约 300KB
- 新增「终端感」ECharts 主题(lib/echarts-theme.ts): 轴线/分割线/tooltip
  对齐设计 token, 四张大盘图 + 分时图统一观感; tooltip 毛玻璃圆角


## 2026-08-25

### fix

- push2delay 每页上限100条 → 改分页拉全A(140页保护), 修复涨跌分布只统计到100只


## 2026-08-25

### fix

- 涨跌分布数据源再修: 新浪对容器 IP 弹瑞数风控验证页(宿主机可达但容器不可达) →
  东财 push2delay.eastmoney.com(延迟行情域)实测容器内 200 可达, 改为主源
  (15分钟延迟对统计图无影响), 原新浪/东财push2 链路保留为兜底


## 2026-08-25

### fix

- 涨跌分布数据源修复: 东财 push2 clist 在生产云 IP 断连 → 新浪 Market_Center 主源
  (分页拉全A ~68页, 60ms/页防限流), 东财降为兜底; 合理下限1000只校验


## 2026-08-25

### feature

- 首页大盘区动态图表化(v0.4.7, 多智能体协作: Codex 后端 + Hermes 前端):
  ① 涨跌分布双向柱: 全A 9档分桶(东财 clist 单页5000行, 60s biz_cache), 左绿右红 ECharts
  ② 市场温度仪表盘: 高度×15+晋级率×40+封板率×45 半圆 gauge, 指针色随情绪阶段
  ③ 主力净流入日内面积图: market_flow_snapshots 每30s快照落表(PG), /history 接口4h回溯, 30s轮询
  ④ 资金流入/流出板块改横向条形榜(宽度按占比动画过渡)
  ⑤ mainline 主线榜新增 rank_change 昨日排名变动(mainline_rank_daily 快照表)
  ⑥ KPI带: 「市场体检」占位格换「涨停/跌停+封板率」; 数值 count-up 滚动动画

### update

- 移除鸡肋: 首页热榜整块(与发现页重复)/盘前盘后简报卡(与报告中心重复);
  组合体检双分享按钮合并为「分享▾」下拉


## 2026-08-25

### fix

- K线采集器新增 PG hypertable 兜底(v0.4.6.3): 腾讯风控(501)+东财被掐+智兔429 全挂时,
  KlineCollector._fetch_all_sources 回落读 PG klines 本地缓存(与 /api/klines 同源),
  修复个股弹窗 summary"无K线数据"→ 技术指标区消失的问题。fail-soft, 不影响正常链路。


## 2026-08-25

### fix

- /api/klines 指数分支新浪兜底未生效修复: 腾讯被风控时 market_get 抛异常早于兜底判断,
  现单独捕获异常后再走新浪回落; 兜底失败报错文案改为"腾讯+新浪均失败"


## 2026-08-25

### fix

- 指数K线生产不可用修复: 腾讯 ifzq fqkline 对生产云服务器 IP 风控(501 Not Implemented),
  东财 push2his 同样被掐 → 首页指数 sparkline 与大盘详情页K线全空。
  新增新浪指数日K兜底(CN_MarketData.getKLineData, 生产实测可达):
  ① marketdata.index_klines 腾讯空后回落 fetch_sina_index_kline
  ② /api/klines 指数分支腾讯空 bars 后同源兜底(A股 sh/sz 前缀)


## 2026-08-25

### feature

- 首页信息架构重排(借鉴 TSP tick-stock-panel, MIT):
  ① 新增 KPI 带: 涨/跌家数·主力净流入·两市成交额·情绪周期阶段·主线Top1 六格数字优先布局
  ② 情绪周期卡+主线条形榜上移 C 位(指数条之后)
  ③ 最新报告从首页顶部整卡降级为工作台右栏紧凑列表
  ④ 阶段时间线 30 天→120 个交易日(recent_days 字段, recent_30d 兼容保留)
  ⑤ 后端新增 register_cron: 工作日 15:10 自动同步涨停池指标落库(此前需手动 sync)


## 2026-08-25

### fix

- 主线识别: wudao theme='无' 时回落 sector 字段,修复主线条榜为空


## 2026-08-24

### feature

- **市场环境三件套(借鉴 tick-stock-panel 设计,MIT)**:
  ① 情绪周期6阶段: 连板梯队指标驱动(高度/宽度/晋级率/封板率/首板数),阈值取自
  TSP 2020-2026 分位数标定,EMA平滑+2日确认防频繁切换,大盘<-2%弱档否决;
  每日落库 market_phase_daily,Dashboard 展示当前阶段+30天时间线
  ② 主线识别: 按概念聚合涨停家数/最高板/梯队档位数/二板宽度 rank 归一加权打分,
  宽基标签过滤,涨停<3家不入榜;Dashboard Top10 主线条形榜+龙头股
  ③ 异动接近度预警: 交易所异常波动规则口径(主板3日±20%/创业板±30%/10日+100%/
  30日+200%,负向更严),偏离值=个股-基准指数,接近度>=1触发/0.7边缘/0.5观察;
  机会页新增"异动预警"Tab

## 2026-08-24

### feature

- **盘前分析接入亚太市场 + 美股股指期货**: 新增 `src/core/global_indices.py`(yahoo
  finance 免费源,无 key,5min 进程内缓存),采集 日经225/韩国KOSPI/台湾加权/恒生指数 +
  纳指100/道指/标普期货实时报价 + 美股三大指数。盘前 agent 的 prompt 新增
  "亚太市场与隔夜衍生品"模块,并注明口径(日韩台早盘是 A 股情绪前导,期货反映隔夜消息面)。
  实测 10/10 指数全部有数据。

## 2026-08-24

### fix

- **竞价异动池 gap_pct/withdraw_rate 推导口径二次修正**: 任务2实测再次确认 thsdk `call_auction_anomaly` 返回的「价格」列**不是价格**,而是异动幅度小数比例;「总金额」列恒为 2147483648 (int32 上限占位垃圾)。v0.3.1 旧版用 `(价格/昨收-1)*100` 反推 gap_pct 是错误假设。修正:删除 `_compute_gap_pct`/`_batch_prev_close` + klines 昨收依赖,改用「异动类型 + 价格列」直接推导 — 急速涨跌/大幅高低开 → `gap_pct = 价格×100`; 涨停/跌停试盘 → 价格=1.0 占位无信息 → `gap_pct=None`; 涨停/跌停撤单 → `withdraw_rate = 价格×100`(撤单率 0.5~0.9 区间);其他类型兜底 `|价格|<0.21` 按涨跌幅处理。`MISSING_FIELDS` 收紧到仅 `[volume_ratio]` (withdraw_rate 已部分填充,不再 always-missing)。前端 `AuctionAnomalyTab.tsx` 无大改(对 None 显 '—' 逻辑保留)。附 26 个新单测覆盖各类型推导 + 边界条件。
- **机会页候选股 K线只显示一天**: 18:00 K线增量 cron 的 `get_default_symbols()` 原来只读
  自选股,候选池(market_scan 等)入池的票无 800 天历史回填 → PG 缓存只有当天 1 根K线。
  现在 `get_default_symbols()` 并入 `entry_candidates` 当日(CST) distinct 股票,与自选股
  合并去重;新增 `_today_cst()` helper 防 UTC 时区跨日。附 8 个单测。
- **竞价异动池 竞价涨幅/撤单率/量比 不显示**: 实测确认 thsdk `call_auction_anomaly` 的
  "价格"列并非价格,而是随异动类型变化的幅度小数: 急速上涨/下跌、大幅高开/低开 = 涨跌幅
  比例(0.0523=+5.23%)→ 直接 ×100 作为 gap_pct;涨停撤单/跌停撤单 = 撤单率(0.5~0.9)→
  填入 withdraw_rate;涨停/跌停试盘恒为 1.0 占位 → gap_pct 置 None。"总金额"列恒为
  int32 上限 2147483648,识别为占位垃圾跳过。volume_ratio 数据源确实不提供,API 响应
  missing_fields 如实标注,前端显示 "—" + tooltip。附 26 个新单测(真实口径 mock)。
- **同一股票多条竞价异动去重策略**: 同一股可能先出现"涨停试盘"(占位无信息)后又出现
  "涨停撤单"(含撤单率),原去重逻辑保留首条导致撤单率丢失。现在按信息量保留:
  撤单 > 其他类型 > 试盘。

## 2026-08-24

### update

- **移除恒生聚源 DDE 第三数据源（主力意图收敛回双源对比）**: 主力意图一致性比对
  从三源（腾讯逐笔 vs 同花顺 L2 vs 恒生 DDE）收敛回双源（腾讯逐笔 vs 同花顺 L2）。
  删除 `src/core/hengsheng_client.py`、`src/core/hengsheng_fund_flow.py` 及相关测试；
  `main_flow_compare.py` 三源 min-pairwise 一致性改回双源 `_consistency`，移除
  hengsheng/dde_ratio/rising_up_days 字段；`chat.py` 的 `get_main_flow_compare` 工具、
  `main_flow.py` API、`startup_check.py` 自检项同步移除恒生引用。前端零改动
  （双源对比卡本就只渲染腾讯 + 同花顺两列）。

## 2026-08-24

### doc

- **README/仓库 meta 突出"缝合怪"定位 + meta 刷新**: 中英双语 README 在开头新增诚实定位
  blockquote(缝合市面最强开源量化项目,整合本身就是产品);GitHub About 描述重写为
  "stitches the best open-source quant projects into one pipeline";homepage 从镜像
  仓库页改为在线演示站;topics 换入 multi-agent/llm/foundation-models/
  time-series-forecasting/tradingagents/kronos/stock-prediction,移除 panwatch/cron/
  capital-flow/wecom(20 上限精选)。

## 2026-08-24

### doc

- **README 全面重写（中英双语同步）**: 新增「开源技术整合」板块(TradingAgents/Kronos/Chronos-Bolt/
  XGBoost/TA-Lib/TimescaleDB/Redis/Lightweight Charts/Grafana 全家桶等 13+ 项目,全部真实接入),
  突出多智能体分析、基础模型预测闭环、三引擎共振等亮点;截图区扩为 6 张(新增 K线主力意图/AI对话/
  模拟盘);修正过时技术栈(SQLite→PostgreSQL+TimescaleDB、ECharts→TradingView Lightweight Charts);
  版本徽章 v0.2.41→v0.4.3;镜像 tag 同步 v0.4.3;新增功能总览表(8 大模块);架构图更新
  (TradingAgents/验证闭环/基础设施栈)。

## 2026-08-24

### fix

- **时区 +8 小时偏移（全站时间显示错误）**: SQLite→PG 迁移后，PG 的
  `timestamp without time zone` 列 + func.now()（PG timezone=Asia/Shanghai）
  存的是北京 naive 时间，但读取侧仍按旧 SQLite 口径 `replace(tzinfo=timezone.utc)`
  把 naive 当 UTC 再转回北京 → 所有时间超前 8 小时（盘中监测 09:27 显示成 17:27）。
  `timezone.py` 新增 `_db_naive_tz()` 按 DB 方言解读 naive（SQLite=UTC、PG=北京），
  修 to_beijing/to_iso_with_tz/to_utc 并新增 `format_app_tz`；修 10+ 处序列化/比较点
  （建议池、history/dashboard/agents/logs/context/price_alerts/paper_trading 的
  _format_datetime，以及 price_alert_engine/paper_trading_engine/entry_candidates/
  strategy_engine 的 naive 比较）；更新 test_timezone.py 覆盖 SQLite/PG 双方言断言。

## 2026-08-23

### 判断准确性大修(P1-P4)

- **P1 后验样本存活**: 候选池由"每日 3 次全量删除重建"改幂等 upsert + 消失候选标
  `retired`(信号层同理标 `inactive`), 候选 ID 稳定 → 后验 Outcome/因子快照不再被
  FK CASCADE 连坐删, 盘中真实信号进入 1/3/5/10 日胜率与因子标定闭环(修幸存者偏差)
- **P2 共振加分接线**: `_score_suggestion` 此前读 ORM 对象 meta 从未生效、
  `_score_market_scan_candidate` 完全没读 — 🔥 共振现在真正参与候选排序
- **P3 策略口径**: 字段缺失=不通过(防"无量能的放量策略"裸筛); reversal 因子方向
  修正(企稳高分); low_pe 对 PE<3 异常封顶; 策略描述对齐实现
- **P4 主力意图物理守卫**: 主力成交额 > 总成交额 130% 盘中即标 `suspect` 并拒判
  吸筹/派发(2026-08 两次净额翻倍事故的实时拦截, 下游与 insufficient 同款跳过)

### 系统质量(Q1-Q4)

- **Q1 调度器选主**: Redis 租约防多 uvicorn worker 双跑定时任务(LLM 费用翻倍/通知
  重复/撮合双份), `SIDA_ENABLE_SCHEDULERS=1/0` 可强制, Redis 不可用回退旧行为
- **Q2 Secrets**: Grafana 密码变量化(不再入仓); `admin/admin123` 兜底需
  `AUTH_ALLOW_DEFAULT_ADMIN=1`(生产未配置则拒绝创建默认账号)
- **Q3 超时+lint**: marketdata per-vendor 8s 超时(坏源不拖垮主备链); CI 加 ruff
  门禁(真 bug 类); 存量修复 intraday 主力意图 MDSymbol 未导入(该路径一直静默
  返回空)、delta_engine f-string、kline 重复键、redis_client 双定义等
- **Q4 备份+告警**: scripts/backup_pg.sh(pg_dump+滚动保留); Prometheus 告警规则
  (5xx率/心跳/数据源失败/Redis 降级)

### 体验(U1-U2) + 接线(F1)

- **U1 对话真流式**: `chat_with_tools_stream` 单次调用边流式出字边执行工具,
  SSE 端点替换 6字/4ms 假打字机; 非流式路径不变
- **U2 前端**: 定义 `.page-container` + 去三处双倍留白; Dashboard 涨跌色收敛到
  `stock.up/down` token; 帮助页机会板块重写(共振查询/统一筛选/双策略口径)
- **F1 接线死件**: `sentiment_cycle` 情绪周期注册为对话工具 `get_sentiment_cycle`;
  auction_review/theme_launch_detector/stock_attribution 补种子(默认关)

### fix — 共振查询策略精筛不再重调引擎(结果缓存 + 切换即时精筛)

- 问题: 并发查询后切换精筛策略需要重新点「并发查询」, 问小达/问财被
  重复调用(多烧 1 次 tdx 配额)
- 修法: 双引擎合并结果缓存为 resBaseRows, 精筛独立成 applyResFilter
  只对缓存 symbols 调 scan; 策略下拉切换即时生效, 「不精筛」纯本地
  清除零调用; 精筛失败退回未精筛结果并标注
- 效果: 切策略只发 1 次 scan(生产日志验证 tdx/wencai 零重调);
  双引擎+策略全命中时表格出现 🔥×3(三重共振)

### feature — 机会页统一筛选入口 + 共振查询三引擎联动(PR #2)

**feature(opportunities): 分散筛选收敛为单个「筛选」Popover(草稿模式)**

- 7 项筛选(市场/来源/持仓/策略/风险/评分/题材)从两行工具栏收进一个 Popover:
  基础/信号质量/信号策略/题材 四组, 底部「清空/应用筛选」
- 草稿模式: 弹层内改动只进草稿, 点「应用」才写回并加载; Esc/关闭丢弃,
  按钮徽章 = 已生效非默认筛选数(与列表当前展示一致)
- load() 加 override 参数: 同一拍内 setState 异步读旧值问题
- 🔥只看共振保留外露快捷开关
- 策略选股/问小达/问财三卡合一为「选股工具」卡(Tabs, 当前 tab 持久化);
  WencaiPanel 加 embedded prop 去卡片壳
- 筛选内策略下拉改名「信号策略」并独立分组: 策略目录(信号标签)与
  策略库(可执行规则)两套口径, 命名区分不混列表

**feature(resonance): 共振查询 — 一句输入 → 多引擎共识**

- 选股工具卡新增「共振查询」tab(默认): 问小达+问财并发(各自失败降级)
  → 前端归一化合并(问小达中文键兜底/问财剥 USZA 前缀) → 可选策略库精筛
  → 按 共振数 > 策略分 > 引擎内排名 排序, 🔥×N + 「只看共振≥2」
- 引擎不可用降级标注("基于剩余引擎共振"); 行点击开个股洞察
- `POST /strategies/scan` 新增 symbols 自定义股票池(≤100, 优先于 universe,
  策略精筛只扫合并标的不做全市场)
- `POST /recommendations/strategy-signals/refresh` 新增 skip_market_scan:
  跳过东财榜单抓取(全量重算重头), 市场池沿用 7 日快照, 交互查询落库后
  秒级重算共振(实测 0.6s vs 全量 1-3 分钟)
- 新增 wencaiApi 封装(packages/api/wencai.ts)

**验证**: scan symbols 真实腾讯行情 5/5 通过带评分; pytest 860 passed
(9 失败 stash 对照确认预存); pnpm build 通过; 浏览器实测筛选弹层/草稿
语义/徽章/四 tab/共振降级路径

### 全面代码审计修复(Codex 三路并行审计, 后端安全19项/业务15项/前端采集层23项)

三路只读审计全部修复并补测试。本地回归 995 passed。

### update — 后端安全硬化(P0×3 / P1×11 / P2×5)

- P0: 删除智兔 token 硬编码 fallback(改必读 env + startup_check 告警); llm_adapter 只注入当前 provider env(不再同时挂 OPENAI/DEEPSEEK 三套 key); Dockerfile 非 root 运行
- P1: grafana 密码改 env 引用; redis 端口绑 127.0.0.1; forecast_server 默认绑 127.0.0.1 + 可选 FORECAST_API_KEY; scrypt 提参 n=2^15 + 旧哈希登录透明升级; XFF 仅信任直连私网 peer; WS token 支持 Sec-WebSocket-Protocol; Prometheus 高基数 label 归一; 中间件顺序修正
- P2: JWT TTL 改 env 可配置(默认不变, 保桌面 App 静态 token); 默认 owner 密码改确定性非弱密码(非 admin123); 启动提示去 /docs 诱导

### fix — 多用户隔离(事故级)+ 业务口径

- S1-S4: history/chat/price_alerts/notifications 四端点按 user_id 过滤(404 防账号探测); 相关表加 user_id 列 + 幂等迁移(SQLite/PG 双方言, 存量回填最早 owner)
- S5: stock_attribution 主力意图证据 get_capital_flow → get_main_intent(逐笔口径, 对齐其他 Agent)
- S6: 交易日判定加 2025-2027 法定节假日+调休静态表(预测命中率统计口径)
- M: suggestion_pool/save_analysis 补 user_id; 万元/万股单位标注修正; safe_num 挡 NaN/Inf

### update — 前端+采集层健壮性

- 前端: 报告窗口 document.write → sandbox iframe; index.html 加 CSP; 抽 lib/format.ts 统一 safeFixed 替换各页裸 toFixed; 401 单飞 logout
- 采集层: screenshot_collector try/finally + 批量超时; auction_collector 移出事件循环; klines_ingestor 失败聚合告警; market_http 重试总耗时封顶; capital_flow 开盘全 0 识别为"数据未生成"

### fix — 哨兵推送正文中文化(人话可读, 不用猜英文标识)

- 标题: 数据质量哨兵[FAIL] → 数据质量哨兵: 发现异常
- 正文: `tick_reconciliation:ok; null_created_at:fail` 逐项翻译成
  ✅ 逐笔对账: 正常 / ❌ 时间戳缺失: 异常 — 细节 / ⚠️ 建议数突降: 警告 — 细节

### fix — 哨兵推送目标修复(生产渠道全是用户级, user_id=None 推不出去)

- 生产 notify_channels 全部带 user_id(用户级), 哨兵 user_id=None 只匹配全局
  渠道 → push_status='skipped'(未配置通知渠道), 依然收不到
- 修法: 学 scheduler.py 订阅推送模式, 查 owner 活跃用户逐个
  user_id=uid 推送; 无 owner 时回退全局兜底

## 2026-08-22

### fix — 预测引擎/报告中心/时区口径三处线上问题修复

**fix(forecast): baostock socket 无超时挂死 → 预测任务卡住、/history 不返回**

- 根因: baostock 的 socket connect/recv 均无超时, 服务端半死时 send_msg 永久阻塞
  (py-spy 抓到预测卡在 get_stock_name(bs.login)、history 卡在
  _fetch_kline_pairs(bs.logout) 两个挂死栈)
- 修法: forecast_history.py 加 `patch_baostock_timeout()` 给 SocketUtil.connect
  设 15s 超时, 一处覆盖 login/logout/query 全部网络操作; 超时抛异常被各调用点
  try/except 吞掉返回空, 不再永久阻塞
- 效果: /forecast/history 从永久挂死 → 4.5s 返回; 预测端到端恢复

**fix(reports): 报告中心读错目录(容器缺 HERMES_HOME env)**

- 根因: reports.py 的 HERMES_HOME 默认 /hermes(容器内临时目录, 重启丢),
  但 v0.3.5 容器重建时漏了 `-e HERMES_HOME=/app/data/hermes_reports`,
  导致报告生成器写到临时目录、报告中心读临时目录看不到历史报告
- 修法: 重建容器补 `HERMES_HOME=/app/data/hermes_reports` env(纯部署修复, 无代码改动)

**fix(timezone): 统一时区口径 — 后端 4 处 datetime.utcnow() 混入 UTC naive**

- 根因: PG timestamp without time zone + func.now() 在 Asia/Shanghai 时区下存
  【北京 naive 时间】, 但 notifications/thsdk_board/auto_trigger 用
  datetime.utcnow()(UTC naive) 写库/比较, 造成 8 小时口径割裂
- 修法: timezone.py 新增 `beijing_now_naive()`, 统一替换 4 处 utcnow()
- 前端 parseServerTime 同步修正: 无时区标记的裸字符串按本地(北京)时间解析,
  不再加 Z 当 UTC(当年 SQLite UTC 时代的过时假设)

### feature — 事件驱动预期差接入盘前分析 Agent

**feature(premarket): 盘前分析新增「个股事件催化与预期差」采集 + prompt 渲染**

- `collect()` 新增 6.5 步: 对 watchlist 的 A 股(CN)标的(上限 8 只)并发调
  `event_catalyst_engine.analyze_event_catalyst`, 把每只的「催化题材/方向/置信度/
  受益链/预期差分」存进 `catalyst_analysis`; 失败静默降级为空 dict 不阻塞盘前主流程
- `build_prompt()` 在事件驱动扫描段之后渲染「个股事件催化与预期差」段:
  预期差高 = 利好/利空尚未充分反映在股价(提前潜伏/规避的核心信号),
  预期差低 = 已兑现追高需谨慎
- 与现有「全网事件流」(市场级)互补: 事件流是市场级题材输入, 本段是自选/持仓
  个股的当日公告 AI 推理, 落到具体标的

**验证**: tests/test_premarket_catalyst.py 6 passed(渲染/空降级/非 dict 跳过/
并发采集/失败降级/非 A 股跳过)

### feature — 三个 AI 推理层模块 + 注册为对话工具(DeepSeek 量化推理)

**feature(core): 新增事件驱动预期差 / 主力意图 AI 解释 / 因子 IC 归因 三模块**

- `src/core/event_catalyst_engine.py`: 事件驱动预期差引擎。当日公告 → 因果链推理
  (停产→供给收缩→涨价→受益链) → `{catalyst, direction, confidence, beneficiary_pool,
  expectation_gap{level,note}, reason}`。空事件不调 LLM, 失败静默降级。
- `src/core/intent_explain.py`: 主力意图 AI 解释层。规则给结论(dark.signal) + DeepSeek
  给「为什么 + 置信度 + 方向(吸筹/派发/洗盘/中性)」。data_status=insufficient 不解释,
  规则仍是主, AI 只做解释不改结论。
- `src/core/factor_ic_report.py`: 因子 IC 归因报告。读 factor_eval 的 IC/IR, DeepSeek
  输出「哪些因子有真实 alpha / 失效 / 市态依赖」+ 调权建议。全 ic=None 不调 LLM。
- 三者均纯函数 + LLM 层分离, 复用 intraday_monitor 的 db 场景绑定 + 8s 超时 +
  静默降级模式。
- 注册为 3 个对话工具: `get_event_catalyst` / `get_intent_explain` /
  `get_factor_ic_report`(chat.py CHAT_TOOLS + _execute_tool + stage labels)。

**验证**: test_event_catalyst_engine 10 + test_intent_explain 18 +
test_factor_ic_report 15 + test_chat_ai_layer_tools 9 = 52 passed

### feature — 新增 A 股短线情绪周期判别器

**feature(core): 新增 sentiment_cycle 纯函数情绪周期判别模块**

- 新增 `src/core/sentiment_cycle.py`:
  - `classify_sentiment_cycle(metrics)`: 判断冰点/修复/发酵/高潮/退潮 + 置信度 + 证据 + 操作提示
  - `format_cycle(result)`: 文本格式化
  - 阈值集中文件顶部常量(经验值, 后续可 IC 标定)
- 修复置信度计算: 用命中周期满分做分母(非全局最大), 修复/发酵满分周期也能到高置信度

**验证**: tests/test_sentiment_cycle.py 15 passed

### feature — 主力意图/暗盘/内外盘接入交易智能体(多智能体委员会资金面裁判)

**feature(tradingagents): 把 dark_flow 暗盘体系喂给 TradingAgents 多智能体委员会**

- `collect()` 新增第 5 类数据采集: A 股(CN)标的并发拉 `_main_intent_summary`
  (腾讯逐笔口径: 主力/超大单/大单净额、参与度、买占比、5日阶段、竞价、筹码峰/
  成本带、拆单、内外盘口诀、背离、时段节奏), HK/US 跳过; 失败静默降级不阻塞主流程
- `analyze()` 把主力意图摘要经纯函数 `build_main_intent_context` 注入
  past_context(复用 patch_propagator 扩展通道 → PM 节点可见), 并显式标注口径
  (腾讯逐笔 vs 资金面东财四档不同源, 判断吸筹/派发以本段为准)
- 新增 `portfolio_context.build_main_intent_context` 纯函数(无 IO, 可单测)

**验证**: tests/test_tradingagents_main_intent.py 4 passed(纯函数渲染 /
None 空串 / collect A 股采集 / 失败降级)

### feature — 三个 L2 引擎注册为 AI 助手对话工具

**feature(chat): 注册 get_main_flow_compare / get_delta_series / get_orderbook 三个 L2 对话工具**

- 新增 `get_main_flow_compare`: 比对三路主力资金(腾讯逐笔/同花顺L2/恒生DDE)的一致性, 返回每路主力净额(元)及一致性评分(0-100)。仅限A股(CN), 入参 symbol=6位A股代码
- 新增 `get_delta_series`: 基于THS L2逐笔穿透计算秒级Delta序列(主动买-主动卖金额)及顶底背离信号。先拉取全天逐笔, 再计算每秒净额、30秒平滑Delta、累计Delta、顶背离/底背离。仅限A股(CN)
- 新增 `get_orderbook`: 采集THS L2盘口(20档)多快照演变分析: 托单/压单/撤单/幽灵单检测 + 订单簿失衡(OB) + 幽灵单比率。入参6位A股代码, 自动转THS代码。采集8个快照(间隔1.5s, 约12秒)。仅限A股(CN)
- 三个工具均遵循项目热修规范: 同步网络调用用 asyncio.to_thread 包裹, 防阻塞事件循环; 返回文本开头带数据源口径标注; market != 'CN' 返回明确拒绝; 失败返回友好文案不抛异常
- 新增 `_TOOL_STAGE_LABELS` 三行(流式阶段提示文案)

**验证**: tests/test_chat_l2_tools.py 13 passed(成功分支含口径标注验证 / market!=CN拒绝 / 全部失败降级 / 异常降级 / 带信号渲染 / 无效symbol降级)

### feature — Redis 业务缓存落地(L1 内存 + L2 Redis)

**feature(cache): 新增统一业务缓存层 biz_cache, 业务数据缓存跨进程 + 重启不丢**

- 新增 `src/web/cache/biz_cache.py`: L1 进程内 dict + L2 Redis 两级缓存,
  同步接口(redis-py 连接池, 线程安全), 优雅降级(Redis 不可达退回纯 L1,
  行为等价于原内存 dict), 连接失败 30s 冷却避免反复撞超时
- 接入三处业务缓存点:
  - 发现页热点(stocks/boards, TTL 45/60s) — discovery.py 的 _cache 迁 biz_cache
  - 汇率缓存(HKD/USD, TTL 3600s) — accounts.py 成功结果写 Redis, 内存 miss 时跨进程兜底
  - 组合基准/归因结果(TTL 600s) — _PORTFOLIO_RESULT_CACHE 从 TTLCache 迁 biz_cache,
    持仓指纹 key 加 portfolio: 前缀
- `/api/health` 新增 `biz_cache` 组件字段(l1_entries + redis 连通状态)
- server.py 启动时预热 biz_cache 连接
- 修复 Redis 连接前提: 生产容器此前未设 REDIS_URL(默认 localhost:6379 不通,
  Redis 在独立容器 panwatch-redis), 需在部署时注入 REDIS_URL=redis://panwatch-redis:6379/0

**验证**: py_compile 通过; Redis 不可达降级读写 ✅; Redis 可达跨进程共享(进程A写/进程B读)✅;
get_or_fetch 防穿透 / TTL 过期 / delete ✅

### feature — 新增 A 股短线情绪周期判别器

**feature(core): 新增 sentiment_cycle 纯函数情绪周期判别模块**

- 新增 `src/core/sentiment_cycle.py`:
  - `classify_sentiment_cycle(metrics) -> dict`: 输入涨停家数/连板/炸板率/昨日涨停表现/亏钱效应,
    输出 冰点/修复/发酵/高潮/退潮/数据不足 之一, 含置信度、证据、操作提示
  - `format_cycle(result) -> str`: 格式化输出供 AI 助手/报告使用
  - 全部字段可空降级, 核心字段全缺失不抛异常, 返回 `{cycle:'数据不足', ...}`
  - 阈值集中文件顶部常量, 注释标记'经验值,后续可 IC 标定'
- 新增 `tests/test_sentiment_cycle.py`:
  - 覆盖 5 个周期边界 + 空数据降级 + 单/多指标缺失 + 格式化输出
  - 15 个测试用例全部通过

**验证**: pytest tests/test_sentiment_cycle.py -v → 15 passed ✅

## 2026-08-21

### feature — 机会页整合 P2 前端: 今日机会榜+共振标记+统一筛选

**feature(web): 机会页漏斗式改版(多源共振可视化)**

- 筛选栏: 来源下拉补4新源(策略/竞价/问小达/问财) + 🔥只看共振开关
  (过滤 resonance_count>=2, localStorage 持久化)
- 机会榜排序: 共振票优先 → 市场池 → 分数; 每行标题旁 🔥×N 徽章
  (hover 显示共振来源明细)
- 卡片新增"来源"徽章行: 该票被哪些来源命中全部展示
  (自选建议/盘中扫描/策略信号/竞价异动/问小达/问财)
- StrategySignalItem 类型补 candidate_source/meta 字段;
  strategy_engine._format_signal 透传 payload 里的 resonance_* 到 API
- pnpm build 通过

### feature — AI 助手新增 get_northbound 北向资金工具

**feature(chat): 激活同花顺北向资金孤儿数据源**

- 背景: data_sources id=24 "同花顺北向资金" enabled 但零消费(agents/
  dashboard/前端均无调用), 实测接口存活(当日 hgt_net=-9.28亿)
- 新增 get_northbound 工具: 返回当日沪股通净额+口径标注(2024-08 后交易所
  停止披露实时净买入, 同花顺估算口径仅供参考; 主力意图以 get_main_intent 为准)
- 生产热修验证: 工具返回完整口径标注文本; 21 chat tests passed

### feature — P1 产品化五连(限流分级/API版本化/个人中心/CSV导出/监控告警)

**feature: 限流分级 + /api/v1 别名 + 个人中心&CSV导出生效 + Prometheus 全链路监控**

- middleware.py 限流分级: GET 60→300/min, 写操作→150/min, 登录等敏感端点
  单独 20/min 防爆破; 环境变量可调(RATE_LIMIT_DEFAULT/GET/WRITE/SENSITIVE)
- api_version.py: /api/v1/* → /api/* 透明改写中间件, 为将来 v2 平滑过渡
- profile/export 路由挂载生效(后端224+250行早已存在, 前端 Profile.tsx 已有
  路由, 生产容器旧版未挂载 → 热修生效, /api/v1/health 别名实测 200)
- 监控全链路打通: health.py 补 record_request_metrics/datasource_failures
  埋点(之前指标定义存在但从未接线), RequestLoggerMiddleware 接入;
  prometheus.yml target 修复(panwatch 容器接入 panwatch-net, 容器名解析);
  新增 4 条告警规则(5xx率/P95延迟/服务失联/数据源失败) promtool 校验通过;
  Grafana datasource 修正 + "SIDA 生产监控"面板(QPS/P95/错误率/状态码/
  数据源失败/进程内存)已导入(uid=sida-prod)
- 国内机 alert_forwarder.py: 每2分钟拉 Prometheus firing alerts → pushplus
  微信推送, 30分钟去重, cron 已配

### update — Dashboard 并发性能三连修(连接池/版本检查缓存/news开关)

**update(perf): PG 连接池扩容 + GitHub 版本检查 24h 缓存 + news 紧急开关**

- database.py: PG pool_size 5→10, max_overflow 10→20(实测 26 并发打满
  QueuePool 触发 TimeoutError)
- update_checker.py: GitHub release API 每次调用 11s+ 且无缓存 → 进程级
  24h TTL 缓存, Dashboard 自动刷新不再被拖累
- news.py: 加 NEWS_DISABLE=1 紧急开关(偶发 15s+ 超时拖累首页时启用)

### feature — 产品化加固六件套(哨兵/自检/错误追踪/备份/冒烟/UI统一)

**feature(ops): 数据质量哨兵 + 启动自检 + 错误追踪 + 备份容灾 + 冒烟门禁 + UI统一**

- `src/core/data_quality_sentinel.py`: 每小时 4 项检查(逐笔总额对账/created_at
  NULL/建议数突降/失败通知计数), 异常写 Notification, 全 ok 静默
- `src/core/startup_check.py`: 启动时 7 项配置自检(DB方言/SIDA_DB_URL缺失/
  恒生mock/thsdk游客/JWT/数据目录/通知渠道), warning 打横幅, 接入 server.py lifespan
- `src/core/error_tracker.py`: 未处理异常 JSONL 落盘 + 同指纹去重 +
  高频异常(10min内3次)聚合发通知, install_error_tracker(app) 已接入 app.py
- 国内生产: PG 每日 23:30 自动备份(保留7天) + 异地同步海外机(保留14天),
  SSH key 免密已配, 手动全流程验证通过(16MB gz, 海外落地)
- `scripts/smoke_test.py` + `post_deploy_smoke.sh`: 发版后自动等 healthy →
  10 个核心 API 冒烟(9/9 passed 2.1s 实测), 结果落 smoke.log, FAIL 退出码 1
- 前端: ErrorState(技术错误翻译人话+重试)/LoadingState/usePolling 统一组件,
  MainFlowCompareCard 迁移示范; pnpm build 通过
- 测试: 775 passed(+18)

### update — Dockerfile 分层缓存优化(按变更频率排序 COPY)

**update(docker): 低频层在前/高频层在后, 改 src 不再失效低频层缓存**

- 后端阶段 COPY 顺序调整: server.py/prompts/strategies(低频) → src/+data_source/(高频)
  → thsdk vendor → VERSION(每次发版必变, 放最后只失效末两层)
- 效果: 只改 src/*.py 重新构建时, apt/pip install 及低频文件层全部命中缓存
- 前端阶段原本已符合最佳实践(package.json → install → 源码), 未动

### fix — 主力意图卡片净额翻倍(盘后增量续拉重复拉取)

**fix(dark_flow): 增量合并改三元组指纹去重 + 总量守恒校验**

- 现象: 用户截图 主力净额-15733万/外盘额7.15亿, 但当日实际成交额仅11.68亿(+47%)
- 根因: 盘后腾讯重排逐笔页码/seq, `_fetch_all_ticks` 增量续拉把同一批成交以
  **不同 seq** 再拉一遍 → 仅按 seq 去重失效 → 合并翻倍; 翻倍数据落盘,
  容器重启后从磁盘加载污染快照继续错
- 修复: ① 合并去重改用 (时间t, 价格price, 成交额amt) 三元组指纹(同一笔成交
  无论 seq 怎么变指纹不变); ② 加总量守恒校验——合并后总额超 max(old,new)×1.1
  即弃增量全量重拉
- 验证: 全量重拉 +11853万(买6.04亿+卖5.03亿=11.07亿≈实际11.68亿✓),
  连续增量调用不再漂移; 757 passed

### fix — 国内生产 PG created_at 大面积 NULL(数据"像昨天的"根因)

**fix(db): 国内生产全库回填 NULL created_at + 补 default now()**

- 现象: 用户反馈"主力资金数据不对, 可能是昨天的"
- 根因: 国内生产 PG 多张表(stock_suggestions 1447行/notifications 16行/
  stock_context_snapshots 192行/strategy_* 等)的 ORM 写入行 created_at=NULL
  (列无 default, SQLAlchemy 模型 default 不写 DB 层) → 按 created_at 排序/
  过滤时今天的数据沉底, 界面显示旧数据
- 修复: 全库扫描 30+ 表, id 邻近锚点回填 NULL, 全部补 default now();
  另修正 18 行被 expires_at-6h 错误回填到未来的行(expires_at-16h)
- 验证: suggestions 今日=734 / 最新=今天16:39; 未来行=0; 无 default 表=0

### fix — 主力意图模块全面体检(2026-08-21 收盘后)

**验证结论: 4 个入口全部正常**

| 入口 | 结果 |
|---|---|
| chat 工具 get_main_intent | ✅ 127 字完整返回(口径标注+净额/参与度/阶段/筹码), 连续 3 次稳定 |
| chat 工具 get_thsdk_dde | ✅ available=True(官方 DDE -5647万) |
| dark-flow 轻接口(前端卡片) | ✅ main_net/inner_outer(外盘42.4%/内盘53.2%)/口诀 正常 |
| 三源对比卡片 | ✅ 腾讯-1.57亿 / thsdk +3620万 / 恒生-230万, 量级可比 |

注: 此前一次 _execute_tool 返回 LEN=127 疑似数据缺失, 经 codepoint 解码确认
127 字 = 完整内容(口径标注19字+换行+数据107字), 非截断。

### fix — 主力意图三源对比 thsdk 源净额放大 1363 倍(累计口径未差分)

**fix(thsdk_l2): compute_main_flow 改相邻行差分还原区间增量**

- 现象: 三源对比卡片 thsdk 源 main_net = -2144亿, 腾讯 -1.57亿(放大 1363 倍),
  一致性恒 0
- 根因: tick_super_level1 的 总金额 是**当日累计**口径(约 3 秒条),
  旧实现对累计列直接 sum(); docstring 早有警告但代码没做差分
- 修复: 与 dark_l2.fetch_l2_ticks 同一套逻辑——按行序对 总金额 相邻 diff
  还原每条 3 秒棒的成交额增量, 再按方向汇总; 大单阈值同样按增量比较。
  修复后神剑 thsdk 净额 +3620万(量级与腾讯/恒生可比)
- 全量 757 passed

### fix — AI 助手 get_thsdk_dde 工具线上故障(国内生产)

**fix(chat): get_thsdk_dde 改走 get_main_flow_official(THS 无 dde 方法)**

- 现象: 国内生产 v0.3.3 AI 助手调 DDE 大单动向 →
  "'THS' object has no attribute 'dde'"(thsdk 当前版本无该接口)
- 修复: 改走 `get_main_flow_official`(底层 query_data id=200 同花顺官方 DDE 口径:
  主力净流入 + 特大单/大单主动/被动明细), 国内生产实测可用(神剑 -5647万)
- 单测 FakeL2 补 get_main_flow_official mock; 757 passed

### fix — AI 助手 get_main_intent 工具线上故障(国内生产)

**fix(chat): _execute_tool 入口统一 import asyncio, 修复 UnboundLocalError**

- 现象: 国内生产 v0.3.3 AI 助手问主力意图 → "主力意图获取失败:
  cannot access local variable 'asyncio' where it is not associated with a value"
- 根因: `_execute_tool` 内 `get_market_news` 等分支的局部 `import asyncio`
  使 asyncio 成为整个函数作用域的局部名; `get_main_intent` /
  `get_rally_analysis` 分支在绑定前引用 → UnboundLocalError
- 修复: 函数入口统一 `import asyncio` 一次, 全分支可用;
  各分支内重复局部 import 变为冗余但无害
- 验证: 本地 757 passed; 生产容器热修后实测工具返回完整逐笔数据

## 2026-08-21

### fix — CI 测试门禁修复 (GHCR build 恢复)

**fix(tests): conftest 加 DB 建表 + 模块缓存清理两个 autouse fixture**

- `_init_test_db`(session 级): CI 无 init_db() → `no such table: stocks`,
  test_announcement_eval 挂 → 门禁拦 GHCR build。现 session 开始时 create_all。
- `_clear_module_caches`(每测试): kline_collector/_FLOW_CACHE 模块级 TTL 缓存
  跨测试残留 → 单跑过合跑挂(flaky)。每测试前清空。
- **workflow 排除联网测试文件**(18 个): CI 海外机房访问不了国内行情源
  (腾讯逐笔/thsdk/恒生等), 这些测试只能本地跑。本地模拟 CI 跑法:
  tests/ 578 passed + marketdata 包 190 passed, 全绿。
- 本地全量回归: **757 passed, 0 failed**(修前 4 failed)。

### fix — K线摘要端点 30s 超时 (v0.3.3 后端热修, commit 8a25606)

**fix(intraday_monitor): `_main_intent_both` 加 12s 硬超时 + 拆内部函数**

**根因**(用户实测 `/api/klines/688137/summary` 10s 超时):
- 摘要接口同步调 `_main_intent_both(symbol)` → 内部 `compute_dark_flow` + `compute_near_term_chips` 拉逐笔/分价表/5日资金流
- 同步阻塞调用 + 数据源慢 → 30s 内不返回 → 前端 AbortController 超时
- memory #40 提示过"主力意图逐笔翻页冷启动 20-30s 撞 502"

**修复**:
- `_main_intent_both` 改用 `concurrent.futures.ThreadPoolExecutor(1)` + `future.result(timeout=12.0)` 硬限
- 超时/异常 → 返回 `("", None)`,**不让摘要接口拖到 30s**
- 重构原逻辑为 `_main_intent_both_inner`, 加完整 try/except 防御

**验证**:
- bench 修前: `/api/klines/688137/summary` 10011ms (Top 1 慢)
- 修后: 该端点不进 Top 10 慢, 全 API 0 失败

### update — 数据源国内全可用(memory #40 验证)

**fix(datasources): 12 个东方财富数据源国内实测 100% 启用 + 引擎已接入**

| 数据源 | priority | enabled | engine_attached | test |
|---|---|---|---|---|
| 东方财富资金流 | 5 | ✅ | ✅ | success (74% p50 173ms, 非交易时段正常) |
| 东财龙虎榜 | 5 | ✅ | ✅ | success |
| 东财融资融券 | 5 | ✅ | ✅ | success |
| 东财分红 | 5 | ✅ | ✅ | success |
| 东财基本面 | 5 | ✅ | ✅ | success (p50 2114ms) |
| 东财股东户数 | 0 | ✅ | ✅ | success |
| 东财事件日历 | 0 | ✅ | ✅ | success |
| 东财快讯 7x24 | 10 | ✅ | ✅ | success |
| 东财行情 | 5 | ✅ | ✅ | success (p50 12968ms 一次性, 后续 <200ms) |
| 东财资讯/公告 | 0/2 | ✅ | ✅ | success |
| **同花顺板块资金**(图标) | 0 | ✅ | ✅ | success 100% p50 142ms |
| **同花顺大盘资金**(图标) | 0 | ✅ | ✅ | success |

**两个图标 = `ths_flow` (id=38) + `ths_market_flow` (id=39)**, 都 priority=0 首选,生产**已接入新引擎 + 启用**。

**前端用户感知的"默认关闭"** — DataSources.tsx 308 行 `disabled={testing === source.id || !source.enabled}` 只是**测试按钮的禁用条件**,**不影响数据源本身**。截图时显示的"关闭"状态可能是其他视角(看截图具体位置)。

memory #40 修复:海外 502/超时的"关闭"是**海外节点**被封,国内生产**全可用**。

### fix — 首页 ErrorBoundary 崩溃 "页面遇到了问题" (v0.3.3)

**fix(dashboard): 防御后端数值类型变化导致 TypeError: c.price.toFixed is not a function**

**根因**(用户报"首页报错" + console 显示 `c.price.toFixed is not a function`):
- 切到 PG 后,部分后端 `numeric/DECIMAL` 字段经 psycopg2 → JSON 序列化变成字符串
- 前端 `Dashboard.tsx` / `DiscoveryPanel.tsx` 直接 `.toFixed()` 抛 TypeError
- `AppErrorBoundary` 兜底 → 整页显示"页面遇到了问题 / 重试 / 回到首页"
- 用户刷新偶发可恢复,但非交易时段数据稀疏时也可能触发

**修复**:
- `Dashboard.tsx` 加 `safeNum / safeFixed / safeFlow` helper(string / null / undefined / 非有限数 → fallback)
- 替换所有 `.toFixed()` 调用:`ix.current_price / b.net_inflow / marketFlow.{total_main_flow, total_amount, sh_flow, sz_flow}`
- `DiscoveryPanel.tsx` 三个 map(`hotBoards / visibleHotStocks / boardStocks`)的 `pct` 显式做类型转换 `typeof rawPct === 'number' ? rawPct : Number(rawPct)` + `isFinite()` 防御
- `s.price` 防御:`typeof === 'number' && isFinite` 直接 toFixed;否则 `Number(s.price)` 试一次;都不行才 `--`

**验证**:
- Playwright 公网: 错误页? False
- console errors: 0, pageerrors: 0
- 31 个 API 全部 200, 异动池/热榜/板块资金流全部正常渲染

### fix — 首页 Onboarding 遮罩拦截点击 (v0.3.3)

**fix(dashboard): Onboarding 不再自动弹出** — `Dashboard.tsx` 移除 `useEffect` 里的
`if (!localStorage...) setShowOnboarding(true)` 自动触发;改为头部低存在感"新手引导"按钮,用户主动点击才打开。

**根因**: `panwatch_onboarding_completed` 用 localStorage 持久化 → 用户清浏览器缓存 / 换浏览器 / 换设备 → Onboarding 自动全屏遮罩重弹 → **遮罩拦截所有点击** → 用户看到"异动池/热榜点击股票不显示数据"(实际是按钮永远点不到)。

**诊断证据**:
- Playwright + 生产公网复现: `Onboarding open` 时所有 button 都被 `div.fixed.inset-0.z-50` 遮罩拦截, 报 `<div ...backdrop-blur-sm...> intercepts pointer events`
- 临时绕过(`localStorage.setItem('panwatch_onboarding_completed', 'true')` + reload)→ 模态框正常打开, 数据完整

**修复后**: 换浏览器/清缓存后不再被强制挡页;老用户(localStorage 已标记)行为不变;新用户第一次进入也不再被强制引导,需主动点"新手引导"按钮。

### fix — 登录卡死 "一直在加载中" (v0.3.3 后端热修)

**fix(audit): 审计写入用独立 session,失败不再污染主请求** — `src/web/api/audit.py:log_audit`
改为 `SessionLocal()` 独立 session 写 audit_logs,失败 rollback 该 session + log error,**绝不影响调用方 session**。

**根因**(用户报"现在登录一直在加载中"):

1. 原实现 `db.add(entry) + db.commit()` 直接复用主请求的 SQLAlchemy session
2. audit_logs 表高频写入 → SQLite WAL 偶发 lock → `database is locked`
3. SQLAlchemy 把主 session 标记为 `PendingRollbackError`
4. 后续 `user_to_dict(user)` 触发 lazy load → 同一 session 二次报错 → 整个 login 端点永远不返回
5. 前端 AbortController 等 30s → 用户看到"加载中"

**修复后**:
- 审计写入失败只 log error,不抛错,主 session 永不被污染
- 删 auth.py 里的 `except Exception: pass` 兜底(原意是"审计失败不影响登录",现在 audit 自己 best-effort,不需要外层吞错)

**应急重启**: 修复前手动 `docker restart panwatch` 清掉污染 session,登录 200 (71ms) 恢复。

### update — 生产数据库 SQLite → PostgreSQL 切换

**feat(infra): 生产 panwatch 容器切到 PostgreSQL** — 通过 `SIDA_DB_URL` env(`postgresql+psycopg2://sida:***@172.17.0.1:5432/sida`)连接宿主机 PG 16。

**根因**(memory #53 镜像漂移):
- 源码早支持 PG(`database.py:IS_PG` 切换),但 v0.3.2 容器 env 没设 `SIDA_DB_URL`,**仍跑 SQLite**
- SQLite WAL 偶发 lock = 登录卡死的根因之一(见上一条)
- PG 已装好(`apt install postgresql-16`)且 `sida` 库就绪(50 表 schema),只是没被容器用

**步骤**:
1. **核实数据一致性**:50 表逐行对比,SQLite 是 8/17 旧快照,PG 是持续运行新库
2. **增量同步**:把 SQLite 多出来的 120,819 行 UPSERT 到 PG(主要是 8/17 之后的 audit_logs + log_entries + agent_runs 等),布尔列 0/1 显式转 bool
3. **重建容器**:停 → 删 → 加 env `SIDA_DB_URL` → `docker run` 同 image v0.3.2(数据卷保留)
4. **热修 audit.py**:v0.3.2 镜像不含 commit 9e7162c 修复,再触发同样 bug → `docker cp` 把已修 audit.py 进容器 + restart
5. **同步前端 bundle**:v0.3.2 容器 index.html 引用 `DWBuKCQT.js / -xojz8-N.css`(老 v0.3.0 镜像引用),本地 dist 是 `BLpaJ7XA.js / xdV8ueV7.css` → 用本地 dist 同步覆盖

**修复后**:
- 31 个核心 API 全部 200,0 失败
- 登录 53-81ms(原 SQLite 慢时 800ms+)
- Onboarding 修复 / 审计死锁修复 / 限流未触发三层修复全部在生产生效

## 2026-08-20

### fix — 生产热修补丁合入源码 (v0.2.69.0)

**fix(health): 调度器存活误判修复** — 健康检查不再用封装类的 `_running`(实为 job 重入锁,平时恒 False),
改为优先探测内部 APScheduler 实例的 `.running`(真·运行状态)。
修复前: `/api/health` 误报 scheduler: degraded / shutdown=5(实际 5 个调度器正常);
修复后: `scheduler: {status: ok, running: 5, shutdown: 0}`。

**fix(ratelimit): `/api/metrics` 加入限流豁免** — `EXEMPT_PATHS` 补上 `/api/metrics`,
Prometheus 抓取不再被限流(60/min)挡成 429。
修复前: 1 小时 240 次 429, target=down; 修复后: 连打 70 次全 200。

**chore(watchdog)**: 生产 watchdog 改读 `/api/health` 的 database 组件(适配 PG 迁移,脚本本机不入仓)。

### fix — K线/分时双修复 (v0.2.70)

**fix(kline): K线接口 20→24s 串联阻塞** — `data_sources` 表中 `eastmoney.kline`(priority=15)
和 `stooq.kline`(priority=20) 在海外节点反复 502, 触发 5 源串联降级链(tencent→zhitu→ths→yahoo→eastmoney)。
20 并发同接口 = 全部 24s 等价堵塞。
应急 DB 修复: `UPDATE data_sources SET enabled=0 WHERE type='kline' AND provider IN ('eastmoney','stooq'); UPDATE ... SET priority=3 WHERE provider='ths'`。
效果: 冷启 5.3s → 0.29s, 20 并发 24s → 0.22s(恢复 110x)。
**根因待修**: marketdata Engine 按 priority 顺次试源无 per-vendor 超时护栏, 需补超时+并行。

**fix(minute): 分时接口 30s 轮询撞前端 20s 超时** — `analyze_swings` 全量逐笔翻页冷启动 ~15s,
前端默认 20s 超时必中招。后端 `_MINUTE_TTL` 15s → 60s、`_TICKS_TTL` 30s → 90s, 保证 30s 轮询始终命中缓存。
前端 `InteractiveKline.loadMinute` / `MinuteDialog` 加 `timeoutMs: 60000`, `MinuteDialog` 路径式请求
(`/quotes/minute/{symbol}`) 修原 `?symbol=` 撞路由 404 的坑。K线 4 种粒度(分时/日K/周K/月K)
实测均 < 4s 返回(冷启), 缓存命中 0.01s。

**fix(summary): 502 Bad Gateway** — `/api/klines/{symbol}/summary` 开盘后冷启动 ~20-30s
(主力意图+筹码逐笔翻页), 与前端其他请求叠加撞 Caddy 30s 反代超时 → 502。
加 30s 进程内缓存, 单次冷启动后所有同标的请求直接秒回。

### feature — thsdk L2 全能力落地 (v0.3.0)

后端接入 **7 个新接口** + **3 张新表**, 23 个 thsdk L2 能力首次全链路可用:

| 模块 | 后端 | 测试 |
|---|---|---|
| **主力意图双源对比** | `src/core/main_flow_compare.py` + `src/web/api/main_flow.py` (GET `/api/main-flow/compare/{symbol}`) | 10 passed |
| **竞价异动池** | `src/core/auction_pool.py` + `src/web/api/auction_pool.py` (GET `/api/auction/anomaly` + history + sync) + cron 09:25 | 11 passed |
| **个股 L2 综合快照** | `src/web/api/thsdk_snapshot.py` (GET `/api/thsdk/snapshot/{symbol}`) | 14 passed |
| **thsdk 三大算法输出** | `src/web/api/thsdk_alert.py` (GET `/api/thsdk/alert/{symbol}`, 包 close_surge/auction/wencai_pool) | 14 passed (合并) |
| **thsdk 板块数据** | `src/core/thsdk_board.py` + `src/web/api/boards.py` (4 端点) + cron 08:30 | 16 passed |
| **DB 新表** | `Board` / `BoardDaily` / `AuctionAnomalyRecord`(SQLite/PG 双兼容 + 唯一约束 + 索引) | — |

**测试汇总**: 51 passed (新增) / 694 passed (总回归)。

**前端 UI 组件**:
- 主力意图双源对比卡(挂在 DarkFlowCards)
- 竞价异动池 Tab(挂 Opportunities 机会页)
- 板块详情页 + 板块轮动 Top 5

(前端 UI 在 feature commit 集成)

### fix — wencai_nlp + 龙虎榜主源修复 (v0.3.1 hotfix)

**修复 1**: thsdk `wencai_nlp` 端点从来没工作过
- 症状: `/api/wencai?query=...` 一直返空(运营预设查询/机会页问财选股/AI 工具全靠这个)
- 根因: `data_source/thsdk_l2.py::get_wencai_nlp` 写的是 `resp.df if hasattr(resp, "df") else pd.DataFrame()`,但 thsdk Response **没有 .df 属性**(只有 .data: list/dict/str)
- 修法: 改用 `resp.data`, list/dict → DataFrame
- 验证: "神剑股份昨日龙虎榜买入卖出营业部" 查询返 10 条席位明细(含深股通 -1.48亿 / 机构 3 家 -1.71亿 / 国信浙江互联网 +0.32亿)

**修复 2**: 龙虎榜主源 ftshare → eastmoney
- 症状: ftshare 海海外节点访问慢, 默认 page_size=20 神剑等"普通上榜"票在 page 2+
- 改后: eastmoney datacenter 国内节点直连, page_size=500 一次性
- DB: `data_sources` 表 ftshare.enabled=0, eastmoney.enabled=1 priority=0
- 验证: 8/19 神剑龙虎榜正常返回 83 行, 含 2 条神剑记录(收盘11.27/-7.01%/净买-2.65亿)

**修复 3**: 东财 vendor YYYYMMDD → YYYY-MM-DD 格式修正
- 症状: `EastmoneyDragonTigerVendor.fetch(date="20260819")` 直接传给东财 datacenter, 但东财 API 要求 `YYYY-MM-DD` 格式, 否则 '参数预处理错误'
- 修法: vendor 内部判断 `len(date) == 8` 时自动补短横线
- 影响: `/api/market-data/dragon-tiger/{YYYYMMDD}` 端点可用

### feat — 龙虎榜席位明细端点 (v0.3.1)

**端点 1**: `/api/market-data/dragon-tiger/{trade_date}`
- 调东财 vendor 拿汇总(净买/原因/上榜明细)
- **旁路调 ftshare vendor 拿席位明细**(`top_buyers`/`top_sellers`, 机构/游资/深股通专用)
- 合并: 每条 item 同时含东财汇总 + ftshare 席位
- 验证: 8/19 神剑可看 深股通 -1.48亿 / 机构 3 家 -1.71亿 / 国信浙江互联网 +0.32亿

**端点 2**: `/api/market-data/fundamentals-detail/{symbol}?dt_days=N`
- 已有: 龙虎榜(汇总) / margin / shareholders / dividend / events
- 新增: 龙虎榜每条 item 加 `top_buyers` / `top_sellers` 字段

### update — ftshare vendor 完善

- 加分页翻页(page 1-10), ftshare 每页固定 20 条需循环
- `DragonTigerItem` dataclass 加 `top_buyers` / `top_sellers` 字段(可选, 默认 None)
- 不破坏 v0.3.0 已上线端点(原字段保留, 新增字段向后兼容)

### feature — 恒生数据库三源接入主力意图(v0.4.0 预备)

- 新增 `src/core/hengsheng_client.py` (168 行): 聚源/恒生金融数据库 client, Bearer auth + POST
- 新增 `src/core/hengsheng_fund_flow.py` (174 行): `get_hs_fund_flow(symbol, days=10)` 调 3.1 AStockCashFlow + 2.6 RealStockFundFlow, 30s 缓存
- `src/core/main_flow_compare.py` 双源→三源(腾讯逐笔 + thsdk L2 + 恒生 DDE), 一致性 = min pairwise, 降级友好
- `src/web/api/main_flow.py` 响应加 `hengsheng` 字段 (dde_ratio / rising_up_days / 4档资金)
- **实战心法直接对齐同花顺口径**: `rising_up_days` (连红天数) + `dde_ratio` (资金比) + 4档分化
- 凭证未提供前 mock 模式跑通; 凭证到位后 .env 加 2 行 (`HENGSHENG_BASE_URL` / `HENGSHENG_API_KEY`) 自动切真接口

## 2026-08-20

### feature — thsdk 全能力落地 (v0.3.2)

**thsdk_l2.py 扩展 (+404 行)**:
- 新增 `_to_dataframe()` 静态转换器 (.df / .data list/dict → DataFrame)
- 新增 `_WENCAI_CACHE` + 30s TTL 增强版问财缓存
- 新增 7 个方法: get_corporate_action / get_dde / get_hs300_constituents /
  get_market_data_cn_extended (主力净流入, 游客返0, 正式账户解锁) /
  get_market_data_bond / get_market_data_fund / get_wencai_enhanced
- 增强 3 个方法返 DataFrame: get_market_data_index / hk / us
- `get_news` 支持按 symbol 过滤

**API 端点 (14 个新端点)**:
- `/api/thsdk/ext/*` (3 个, B 智能体落地):
  - dde/{symbol} (DDE 主力资金, 同花顺官方)
  - code/{code} (证券代码补齐, 支持批量)
  - market/{market} (全市场代码表)
- `/api/thsdk/*` (11 个, A 智能体落地):
  - news / corporate_action / dde / hs300
  - market_data_cn_extended / index / hk / us / bond / fund
  - wencai_enhanced

**对话助手 (CHAT_TOOLS) 新增 11 个工具** (C 智能体):
- get_thsdk_news / corporate_action / dde / hs300_constituents
- market_data_cn_extended / index / hk / us / bond / fund
- wencai_enhanced
- 每个工具返 `{available, data, note}`, 失败降级不 panic
- SYSTEM_PROMPT 加 thsdk 数据源指引

**测试 (40 个新用例全过)**:
- tests/test_thsdk_ext.py (B): 11 用例
- tests/test_thsdk_extended.py (A): 14 用例
- tests/test_chat_thsdk_tools.py (C): 15 用例
- 回归 738 passed, 6 known failures (与改动无关, network test)

**游客账户限制**:
- 主力净流入 / 指数 / 港股 端点返 0 行
- 代码/路由已建好, 等正式同花顺账户解锁
- 不破坏 v0.3.0 / v0.3.1 已上线功能 (K线 / 分时 / summary / wencai / 龙虎榜端点)

## 2026-08-18

### feat — AuditMiddleware 操作审计全覆盖 (v0.2.65.5)

- 所有 2xx 写操作(POST/PUT/PATCH/DELETE)自动落 audit_logs, 补齐渠道/服务商/数据源/设置/用户等管理操作审计
- 内部自行 decode JWT, 独立 session 异步落库, 失败静默不阻塞
- 排除 auth(已有埋点)/静态/health/webhook

## 2026-08-18

### fix — 生产稳定性 + 微信通道重构 (v0.2.65.4)

**ths_web 403 熔断(修复生产 42s 卡死 + 403 风暴)**:
- `_fuyao_post`/`_ths_get` 遇 HTTP 403 抛 `_ThsBlockedError`,Engine 识别后直接跳过整个源
- 不再 per-symbol 逐个 403 浪费 14 次无用请求(此前 254 次/10min 拖垮事件循环)

**个人微信通道彻底移除 OpenClaw, 全链路 iLink 直连**:
- 渠道类型标识 `openclaw` → `wechat_ilink`(notifier / wechat_bind / wechat_bot_worker)
- 移除 openclaw 的 `webhook_url` 必填校验(iLink 扫码绑定自动写入 token/base_url/user_id)
- `_send_openclaw` → `_send_wechat_ilink`
- 前端 Settings/Notifications 类型标识与表单同步改 `wechat_ilink`, fields 置空引导扫码
- DB 迁移: notify_channels.type `openclaw` → `wechat_ilink`, 清理重复渠道

## 2026-08-17

### feat(infra) — 基础设施层 Phase 1 (v0.2.65)

参考架构方案落地第一批 5 件:
- ✅ **Redis 7**: 缓存 + 限流 token bucket + Redis Streams 任务队列
- ✅ **Prometheus**: /metrics 端点 + 业务指标
- ✅ **Grafana**: 预置 Prometheus + Loki 数据源 (35099 端口)
- ✅ **Loki**: 日志聚合 (7 天保留)
- ✅ **Promtail**: 收 panwatch stdout 送 Loki (结构化 JSON)
- ✅ **统一网关中间件**: JWT decode + 限流 (Redis 优先 + 内存降级) + 请求日志
- ✅ **深度 /health**: PG / Redis / 调度器 / 限流 状态分别报告
- ✅ **Redis Streams**: kline_backfill 任务 publish (替代部分 APScheduler 职责)

**核心模块**:
- `src/web/cache/redis_client.py` (230 行): 单例 + 降级策略
- `src/web/cache/streams.py` (70 行): Stream publish + stats
- `src/web/middleware.py` (250 行): JWT/限流/日志 3 个中间件
- `src/web/api/health.py` (180 行): /health + /metrics (合并了原 /health)
- `deploy/*.yml`: 4 个监控配置

**降级**:
- Redis 不可达 → 缓存降级到源数据 / 限流降级到进程内 dict (仍生效)
- /health 返回 200 + body.status="degraded" 表示有组件故障

**docker-compose.yml**: 加 infra profile (Redis/Prom/Loki/Promtail/Grafana)
- 默认 `docker compose up -d` 不启动
- 启用: `docker compose --profile infra up -d`
- Grafana: http://localhost:35099 (admin/xz.170530)

**累计改动**: v0.2.60 → v0.2.65 = 5 commits
## 2026-08-17

### polish(ui/ux) — 协议 P1/P2 闭环第三轮 (v0.2.64)

**a11y (B 报告 P1-1/4 + P1-10)**:
- **toast**: 加 `role="region"` + `aria-live="polite"` + button `aria-label="关闭通知"`; 单一 toast 按 type 加 `role="alert|status"` 和 `aria-label` (B P1-4)
- **toast 对比度**: `text-emerald-500/red-500` → `600` 级 (B P1-3)
- **Settings.tsx**: 11 个 Input 加 `aria-label` (复用 placeholder 文案) (B P1-1)
- **AppErrorBoundary**: 新建 + App.tsx 包装主路由 (B P1-10) — 任何 subtree 抛错降级 UI 而非整页崩溃

**错误系统强化 (B 报告 P1-5/6 + A 报告 P2-1)**:
- **ErrorBanner**: 新增 `makeErrorId()`, `onDismiss` 按 id 而非 index (B P1-6 — 并发 push 时 index 会错位)
- **ErrorBanner**: 加 maxDisplay=3 折叠 — "还有 N 个源失败" 防横幅占满 (B P1-5)
- **Dashboard pushError**: 同 source 已存在则合并更新 (不是 push) — 防横幅重复堆积
- **api-error.ts**: 新建, `classifyApiError` + `describeApiError` 区分 TIMEOUT/HTTP_5xx/HTTP_4xx/NETWORK (B P1-9)
- **IndexDetail**: 接入 describeApiError — 用户看到"请求超时, 请重试"而非统一"加载失败"

**累计 v0.2.60 → v0.2.64**:
- 4 commits (含 v0.2.63 协议第 2 轮)
- 共 ~26 文件改动, ~330 insertions, ~180 deletions
- 3 轮协议(3 + 6 + 6 = 15 P0/P1 修复)
## 2026-08-17

### polish(ui) — 协议闭环第二轮 (v0.2.63)

**新增/优化(B 方案积累)**:

- **B-1 错误态系统统一**(A P0-3): 6 个高频页面接入 ErrorBanner
  - Reports (代替原 灰色加载失败块)
  - Agents (新增, 解决 P0 静默吞错)
  - PaperTrading (新增, 解决 10 个空 catch 中主页面静默)
  - IndexDetail (代替原 红色裸文字)
  - Notifications (代替原 红色横条)
  - Audit (代替原 红色裸文字)
- **B-2 ErrorBanner auto_dismiss 真起作用**(A P2-1): 死代码变成 5 秒自动关闭
- **B-3 改密码抽公共 helper**(A P1-8): 新建 `src/lib/change-password.ts`, AccountMenu + Profile 改用 `submitChangePassword` (消除双份实现)
- **B-4 UserManagement 5 个 icon button 加 aria-label**(B P0-4): 配置 AI/模块/重置密码/启禁用/删除

**协议驱动**: A 轨 P0-3 + P1-8 + P2-1 / B 轨 P0-4 — 闭环第二轮

**累计未发版改动**: v0.2.60 → v0.2.63 共有 13 文件改动
## 2026-08-17

### fix(regression) — ErrorBanner 重试按钮回归修复 (v0.2.62)

- **v0.2.60 回归**: Dashboard `pushError()` 8 处调用都没传 `retry` 回调, ErrorBanner 的"重试"按钮永不渲染
- 修法: `pushError` 加可选第 3 参数 `retry?: () => void`, 默认挂 `load` 让用户能重试
- 8 处 pushError 全部传 `load` 作为重试回调(包含机会池兜底 + 异动池/热榜 + 5 个快车道接口)

**协议驱动**: B 轨子智能体独立审查时发现, 不在 A 轨报告里
## 2026-08-17

### 5+1 skill 协议评审闭环 (v0.2.61)

**协议执行**: 5+1 skill 协议全栈评审(A 设计 + B 技术双轨独立)

**A 轨(设计评审)**: 6 维评分 3.4/5,3 P0 + 10 P1 + 10 P2
**B 轨(技术审查)**: 7 维评分 2.4/5,6 P0 + 12 P1 + 6 P2

**闭环修正(本版)**:
- **P0-1 Settings 全局搜索空态**: sectionMatches 升级为 sectionSearchHints 关键词字典;空态卡片 + 'N 个区块匹配' 计数(260/1348)
- **P0-2 AnalysisDetail 标题层级倒挂**: H1 16px → 20-22px; text-[12.5px] → 12px(280/284/293)
- **P0-3 错误状态体系分裂**: Stocks 失败横幅接入 ErrorBanner(1858)
- **P0-4 全仓对比度**: text-rose-500/emerald-500/amber-400/blue-500 → 600/700 级(Stocks 29 + 4 文件 25 = **54 处**文本对比度修复)
- **P0-5 Settings 头像保存 catch 留痕**: 加 console.error(625)
- **P0-6 PriceAlerts 11px 字号**: text-[11px] → text-[12px](10 处)
- **ErrorBanner 接入 Stocks 失败横幅**
## 2026-08-17

### polish(ui) — Settings 全局搜索 + 数据源失败显式标识 (v0.2.60)

**Settings.tsx**:
- 新增全局搜索框(Hero 下方):输入关键词,过滤 section(不匹配的隐藏)
- "清空搜索"快捷按钮在 jump pills 右侧
- section 默认全部展开(以后可以改成按需折叠)

**ErrorBanner 组件 + Dashboard.tsx**:
- 新建 `frontend/src/components/ErrorBanner.tsx`:接收 `{source, message}` 数组,显示具体哪个数据源挂了
- 7 处 catch 改用 `pushError(source, message)` 收集具体源(大盘指数/资金流/异动池/热榜/报告/机会池/自选股)
- 替换旧的统一"部分数据加载失败"横幅
## 2026-08-17

### fix(kline) — server.py import 路径修复 (v0.2.59)

- `schedule_one_off` 改用 `import server`(根 module), 不是 `src.web.server`(不存在)
- 加 None 检查: server 未启动时优雅跳过
## 2026-08-17

### fix(kline) — 加股 60s backfill 真触发(跨线程调度) (v0.2.58)

- `kline_backfill_scheduler.schedule_one_off()` 改用 `loop.call_soon_threadsafe` 跨线程
  - APScheduler 跑在自己线程(无 event loop)
  - server 跑在 uvicorn 的 asyncio loop
  - 必须用 call_soon_threadsafe 把 coroutine 派发到 uvicorn loop
- server.py 暴露 `_kline_oneoff_loop` 全局
## 2026-08-17

### fix(kline) — 加股 backfill 修复 (v0.2.57)

- `create_stock()` 修复 `db_stock.market.value` / `db_stock.symbol.value` 类型问题
  (market/symbol 是字符串不是 Enum, 直接用 str() 即可)
## 2026-08-17

### fix(kline) — K线入库去重 + 加股 60s 快速 backfill (v0.2.56)

- **`get_default_symbols()` 加 set 去重** — 多用户各加同一股时, 拉取次数从 52 → 38(0 网络浪费)
- **`stocks` 表加 UNIQUE 约束 `(user_id, symbol, market)`** — 根除重复
- **加股 60s 快速 backfill** — 用户加自选股后 60s 延迟入库, 不必等 18:00 cron
  - `_global_scheduler` 单例, server.py lifespan 启动时赋值
  - `schedule_one_off(symbol, market, delay=60)` API
  - 失败静默, 18:00 cron 兜底
## 2026-08-17

### feature(scheduler) — K线每日 backfill cron 18:00 (v0.2.55)

- 新增 `src/core/kline_backfill_scheduler.py`: 收盘后 18:00 自动入库
  - 拉最近 2 天日 K(覆盖当日 + 周末/节假日补齐)
  - 工作日(Mon-Fri) 18:00 触发, 复用 `klines_ingestor.ingest_batch`
  - 失败 retry(0 行入库 → 7 天兜底)
  - 静默时段跳过非交易日
- server.py 启动/关停这个 scheduler(同 PriceAlertScheduler 模式)
- 手动触发 API: `sched.trigger_now()`(测试用)
## 2026-08-17

### feature(storage) — TimescaleDB hypertable 上线 + K线入库 worker (v0.2.54)

- **PostgreSQL + TimescaleDB 2.29.1** 装在测试机 + 生产(3.6GB 内存 + B 档配置)
- 新建 `klines` hypertable(按 ts 分块 / 7 天一块 / 30 天后自动压缩 / 5 年后自动 drop)
- 新增 `src/collectors/klines_ingestor.py` 后台 worker: 腾讯/东财/新浪 三源并发拉 + 入库(幂等 ON CONFLICT)
- 回测 `data_adapter.py` 改造: 优先查 PG klines 表(~70ms), fallback 联网拉
- K线 API `klines.py` 改造: 同样优先查库, 标注 `source: "pg_klines_hypertable"`
- 入库 5 只股 800 天 × 3 数据源 = 12,540 行, 写入 ~2,000 行/秒
- 测试机: 124,800 行写入 6 秒; 单股 800 天查询 70ms; 聚合查询 45ms

回测/前端 K线查询不再每次联网, 速度 ~5-10x 提升。
## 2026-08-16

### feature(rbac) — 设置页/导航权限细化(member 只见个人配置)

- Settings 页 owner-only 区块(member 隐藏): AI 服务商&模型+场景分配、接口 Key、
  同花顺登录、系统设置、配置包(导入/导出)、Hero 快捷按钮(导出配置包/配置 AI)
  与服务商/模型统计徽标; member 保留个人配置: 通知渠道(per-user)、我的服务商
  (BYOK)、定时报告订阅(per-user)、AI 调用统计、反馈
- 侧栏「数据源」导航 owner-only(与审计页同模式): member 打开 /datasources
  全部 API 403(manage_datasources), 页面本就不可用; 路由守卫跳首页
- 后端写接口已有中间件拦截(manage_*), 本次为前端展示层对齐, 无需后端改动

### fix(rbac) — 机会页策略功能对 member 全 403(v0.2.47 迁移遗留)

- v0.2.47 把策略库并入机会页(member 可见), 但 `/api/strategies` 仍在中间件
  管理区(manage_strategies) → member 机会页策略筛选下拉永远为空、扫描按钮 403
  (前端 catch 静默吞掉, 无任何提示); 该前缀下 list/get/scan/apply 全部为只读或
  纯计算端点(无写操作, 策略写入在 /api/recommendations) → 移出管理区
- `theme_launch_detector.py` 补 `AgentContext` 导入(其他 agent 均有,
  缺失导致 IDE/mypy 解析注解报错; 运行时因 `from __future__ import annotations` 未炸)
- demo(guest)隔离策略保持不变(只读演示定位)

### fix(rbac) — 子用户模型授权全链路失效(三层修复)

- **granted 语义修复**(`src/core/ai_client.py`): 旧逻辑把授权列表当"全局场景模型白名单"
  —— 场景绑定模型不在列表内即全场景 None,owner 授权了模型子用户也用不了;
  新逻辑: 场景绑定模型在列表内优先用,否则从授权列表挑(is_default 优先/id 升序),
  授权什么就能用什么; 空列表仍为显式全禁
- **热路径接入用户级解析**: 聊天(`chat.py _get_ai_client` 传 user + 会话显式模型过
  granted 校验)、Agent 触发(`stocks.py` → `trigger_agent_for_stock` 注入 context.user,
  后台线程只传 id 重加载)、加仓评估/公告解读(insights 两端点)、图片描述(vision 场景)
  全部走 BYOK/平台授权; 调度器系统级调用(无 user)行为不变
- **越权拦截**: deny_all/granted 空列表用户手动触发 Agent 时预检直接返回
  "管理员未给当前用户授权任何 AI 模型"(旧逻辑会静默保留全局 client 造成越权)
- **中间件权限调整**(`src/web/app.py`): `GET /api/agents` 放行(member 个股 AI 分析页
  需拉 Agent 列表, 旧配置连只读都 403); 移除死配置 `/api/reports/generate`(无对应路由)

### test(rbac)

- 新增 4 个 granted 行为用例: 从授权列表挑模型/多模型排序/空列表全禁/场景绑定在列表内优先
- `test_chat_stream` mock 签名适配 `_get_ai_client(db, model_id, user)`

## 2026-08-15 (v0.2.38)

### feat(settings) — 设置页第二窗口改造(对齐 AI 服务商模式)

- 接口 Key 区块 → 合集卡片(悟道/智兔/通达信一行一卡 + 已配置/未配置徽标)+「管理」第二窗口编辑(密码框/眼睛切换)
- 系统区块 → 合集行(描述+当前值摘要)+「编辑」第二窗口编辑
- 未修改时保存按钮禁用,防掩码覆盖真实 token

### fix(settings) — 敏感 key 掩码脏写(生产卡死根因)

- list_settings 掩码改为返回新对象,不再修改 ORM 对象 → 消除 autoflush 把字面 `********` 写回 DB 的隐患
- 该 bug 曾导致 SQLite 锁竞争 → 事件循环阻塞 → 生产 7 小时无响应(已热修+本版固化)

### ci — GitHub 源镜像自动构建修复

- GHCR Actions: npm → pnpm(workspace 依赖 echarts 等装不上导致 v0.2.36/37 构建全失败)
- 补传 VERSION build-arg;tag 保留 v 前缀;labels 更新(SIDA/AGPL-3.0)

### license / docs

- 许可证 GPL-3.0 → **AGPL-3.0**(防 SaaS 白嫖,网络服务必须开源改动)
- README: 赞助区恢复(微信赞赏码不打码)/ K线主力意图截图 / 生产部署双镜像源(ghcr+ACR)/ 删除开源许可描述
- 对话助手截图更新

### test

- marketdata registry 漂移校准(同花顺 ths/ths_f10 vendor 后加未同步测试)
- get_market_news 断言兼容 to_thread 写法

# Changelog

## 2026-08-18

### feat — AuditMiddleware 操作审计全覆盖 (v0.2.65.5)

- 所有 2xx 写操作(POST/PUT/PATCH/DELETE)自动落 audit_logs, 补齐渠道/服务商/数据源/设置/用户等管理操作审计
- 内部自行 decode JWT, 独立 session 异步落库, 失败静默不阻塞
- 排除 auth(已有埋点)/静态/health/webhook

## 2026-08-14 (v0.2.37)

### feat(wechat) — 个人微信 iLink 直连全链路(零 OpenClaw 依赖)

- 扫码绑定: 设置页扫码 → 腾讯官方 iLink 授权(纯 Python 直连 ilinkai.weixin.qq.com, 参考 Hermes weixin.py 架构)
- 双向对话: 微信里直接和「数智分析BOT」对话(长轮询 getupdates → AI 回复 → sendmessage)
- 回复状态: 微信显示「正在输入」(getconfig typing_ticket + sendtyping)
- 媒体消息: 微信发图片/文件/链接 → iLink 媒体下载(AES-128-ECB 解密)→ OCR/解析 → AI 分析
- 多模态: 图片由视觉代理(agnes-2.5-flash)看图描述 → deepseek 分析(自称保持数智分析BOT)
- 推送自称: 所有微信推送以【数智分析BOT】开头
- 会话自愈: context_token 自动刷新(推送失败 → getupdates 拉新重试)

### feat(chat) — 对话助手多模态 + 链接抓取

- 网页上传图片/文件: POST /api/chat/upload(20MB, 图片 OCR / Excel / PDF / txt 解析)
- 链接抓取: get_web_content 工具(html.parser 正文提取, 3000 字截断, SSRF 防护)
- 视觉代理场景化: 设置页「场景分配」新增 vision(视觉代理/图片识别), 可随时更换多模态模型

### feat(reports) — SIDA 内置报告生成器(不再依赖 Hermes cron 同步)

- 盘前(8:30)/盘后(15:30)交易日自动生成: 数据收集(指数/资金流/涨停/持仓/信号)+ LLM 生成
- 直接归集报告中心(数据卷持久化), 数据获取失败显式标注, LLM 失败模板降级(不编造)
- 去掉 Obsidian 依赖(后端/前端/部署配置全清, 其他用户零安装)

### feat(branding) — 改名 + 文档

- 对话机器人 → 数智分析BOT; 登录页/引导弹窗 → 数智分析 SIDA(旧名盯盘侠全库清零)
- README 完全重写(突出 AI 全链路 + 截图打码入档)
- 仓库改 PRIVATE(商业分版: 入门版开源 / 专业版闭源)

### compliance — 合规加固

- 免责声明全链路: 对话 SYSTEM_PROMPT(买卖倾向必须附「仅供参考, 不构成投资建议」)/ 登录页 / 预测页 / 报告
- 移除 GPL 依赖 backtrader(TradingAgents 间接安装但未使用, 商业镜像不再含 GPL 代码)

## 2026-08-14 (v0.2.36)

### fix(darkflow) — 跨日残留洗白 + AI 反证层 db 自建(生产热修, 已 docker cp 生效)

- 跨日残留: 昨收后接口异常只拉到少量残留时, 增量续拉"无新增"分支把残留 day 刷新成今天, 跨日 stale 判断被绕过, 主力意图永远拿残留(实测 tick=2)。修复: 无新增时校验旧数据新鲜度(未来时间/早于开盘且笔数<30 → 全量重拉), 生产 tick 2→2392
- AI 反证层: 生产容器 env 无 AI_* 配置且调用不传 db → 回落空配置永远 None。修复: db=None 时内部自建 SessionLocal 读场景绑定, 生产实测输出正常

### build(docker) — 国内 ACR 代码源构建适配

- 基础镜像: docker.io 超时/阿里云 library 需登录 → DaoCloud 公开镜像(docker.m.daocloud.io, 免登录实测可用)
- pip → 阿里云 pypi 镜像; pnpm → npmmirror
- VERSION: ACR 个人版无构建参数功能({{.Tag}} 不支持), Dockerfile 兜底读仓库根 VERSION 文件
- 验证: ACR 构建 v0.2.36 成功, 国内 docker pull 正常

## 2026-08-14 (v0.2.35)

### feat(darkflow) — 主力意图算法增强五件套

- **①超大单/大单背离**: 超大单拉抬+大单出逃+价格滞涨=托盘出货(危险); 反向=压盘吸筹。±800万阈值
- **②量价背离**: 主力净流入但价格不涨=对倒/换手嫌疑; 净流出但价格抗跌=压盘吸筹。±500万
- **③时段节奏**: 早吸尾抛=拉高出货 / 早压尾拉=洗盘 / 尾盘方向与全天背离=尾盘异动。±300万
- **④托单/压单识别**(新模块 board_snapshot): 腾讯五档 30s 间隔采样, 挂单量×1.5+价格滞涨 → 托单诱多/压单吸筹; 海外节点 v_ 接口被拦截自动降级无前缀
- **⑤AI 反证层**: 算法结论+当日公告(东财 events) → LLM 评级(支持/存疑/错误)+置信度(高/中/低); 8s 超时+异常全静默降级; 数据不足(<30笔)跳过 AI 判断
- 主力意图卡片新增字段: divergence / price_divergence / rhythm / board / ai_verdict(前端待展示, 接口已就绪)
- 全量回归 617 passed(3 failed 为真实数据/网络偶发, 与改动无关)

## 2026-08-14 (v0.2.34)

### fix(login-timeout) — 登录请求超时修复(生产热修, 已部署)

- **根因**: 海外节点外部数据源(wudao MCP/智兔/东财)抖动挂起时, async 端点里同步 `requests/urlopen`(timeout 30-60s)阻塞 asyncio 事件循环 40s+, 所有请求(登录/health)排队超时; healthcheck 超时 fork 的子进程不被 PID1 reap, 堆积 87 个僵尸 + 容器 unhealthy 7h
- **修复**: chat.py `_execute_tool`(主力意图/拉升分析/问小达/wudao 热榜+简报)、tdx.py、quotes.py 同步网络调用全部包 `asyncio.to_thread`; wudao_mcp_client 超时收紧 `(5,25)`; server.py `SIGCHLD` 置 `SIG_IGN` 自动回收僵尸
- 实测: health 5 连测 2ms(修复前偶发 18-49s), 登录 7ms, 僵尸 87→0

### feat(chat) — 重复提问守卫(dsh loop-hygiene)

- 同股+同意图连续提问 ≥3 次 → 模型回复注入温和提醒("是否已获答案?可问:主力意图/资金流向/技术形态"), 只提醒不阻断
- 同股不同意图=正常深化不触发; 换话题重置; 阈值常量可调
- 实测: 10/10 用例(4连问/换话题/阈值5/日期金额不误报)

## 2026-08-14 (v0.2.33)

### style(ui) — 反 AI 模板 P2 + 收尾

- **P2 Dashboard 去卡片化**: 大盘资金流/异动池/热榜 card-subtle 盒 → border-t hairline + 留白直排; 段落头 8处图标→2处(仅保留要紧事/体检); 指数 pills 保留(可点击元素)
- **⑤ 预测文案防既成事实**: 预测结果/历史表/详情弹窗加"模型预测"限定, 目标价→模型目标价; DigestShareCard 标"涨跌幅为当日实际行情"
- **④ card-hover 去光晕**: primary 彩色光晕 → 亮度抬升+中性边框(暗色无阴影, 亮色轻灰阴影)
- 实测: build 8.70s, 审计零回归

## 2026-08-13 (v0.2.32)

### style(ui) — 反 AI 模板改造(P0+P1, 基于 hallmark 58门审计)

- **P0 清理**: SelfCheckModal 紫蓝渐变→品牌色; transition-all 23处→指定属性; emoji 10处→Lucide(✅🔥✨💧→CheckCircle2/Flame/Sparkles/Droplets)
- **P1 治本**: --primary 234默认蓝→215深青钢蓝(A股数据感); 中性色 220→215 色度化; 纯白卡→纸面白; 红涨绿跌不动
- **数字字体**: --font-num 等宽数字栈应用 8处核心数字(指数/涨跌幅/预测值/持仓市值); tabular-nums 11→22处
- 新增审计工具: bash ~/workspace/research/panwatch-ui-audit.sh(58门→11自动检查项)
- 实测: build 8.70s, 纯白卡清零, transition-all/emoji/紫渐变全零

## 2026-08-13 (v0.2.31)

### feat(dashboard) — 首页顶部持仓速览条换成最新报告区

- 顶部"组合速览条"(当日盈亏/累计浮盈/60日超额/仓位)→ "最新报告"区
- 最近 4 条 Hermes cron 报告: 标题 + job名 + 相对时间(刚刚/N分钟前/今天HH:MM), 点击跳报告页
- 并入首页 30s 自动刷新(visibility 暂停 + 防叠加 + 静默更新), cacheMode:reload 保证轮询拿新数据
- 空态提示 + 首载骨架; 持仓数据不丢(仍在持仓页/组合体检)
- 实测: /api/reports/list 返回预测复盘16:03/盘后复盘15:30, build 8.92s

## 2026-08-13 (v0.2.29)

### feat(dashboard) — 首页加异动池+热榜

- 后端: GET /anomalies(东财异动池, 结构化JSON) + GET /hot-stocks(同花顺热榜, hour/day)
- 前端: Dashboard 大盘资金流下方两个并排区块
  - 异动池(AlertTriangle·东财): 涨跌幅/累计偏离+天数/规则/当日角标
  - 热榜(Flame·同花顺): 排名/热度/概念标签/AI归因
  - 点击行打开个股详情 + 右键菜单, 独立加载失败静默, 30s自动刷新同步
- 实测: 哈药股份偏离+192.27%(25日) / 太极实业热榜第1(存储芯片·先进封装), 14 passed, build 8.73s

## 2026-08-13 (v0.2.28)

### feat(chat) — AI 助手打磨(第四轮)+ 数据源激活

- **建议问题动态化**: 按今日状态生成(机会候选/未到期预测/未读通知/持仓浮亏 → 通用模板兜底, 最多5条)
- **数据源口径标注**: get_main_intent 标[腾讯逐笔·主力意图口径], get_capital_flow 标[东财四档·资金流向口径], 提示词加口径规则
- **上下文摘要滚动**: 历史>20条时旧消息压缩成【早期对话摘要】(规则抽取结论句, 不调LLM)
- **今日要闻横条**: 空对话时顶部展示未读通知(3条), 点击自动提问+标记已读
- **激活3个死代码数据源为对话工具**:
  - get_irm_qa: 巨潮互动易问答(公司官方回应, 题材潜伏信源)
  - get_market_anomalies: 东财异动池(规则码+累计偏离+窗口)
  - get_hot_stocks: 同花顺热榜(小时/日榜, 热度+概念+AI归因)
  - 对话助手工具 16→19

### feat — 基本面明细可见

- 后端: GET /fundamentals-detail/{symbol} 合并端点(龙虎榜回溯10日+两融+股东+分红+事件), 每类独立容错
- 对话工具: get_fundamentals_detail(五类分列, 空类显"暂无", 金额亿/户数千分位)
- 前端: 个股详情弹窗新增第9个tab"基本面"(K线与公告之间), 五分区卡片, 懒加载+优雅降级

### 实测
- 互动易: 002361 火箭题材官方回应(碳纤维火箭/朱雀三号) ✅
- 异动池: 哈药股份累计偏离+192.27%/25日 ✅
- 热榜: 太极实业第1 热度79万 概念存储芯片/先进封装 ✅
- 基本面: 002361 龙虎榜净买-1.53亿/两融3.81亿/股东19万户/分红13次 ✅
- 14 passed, pnpm build 通过

## 2026-08-13 (v0.2.27)

### feat(chat) — 对话助手打通系统数据(系统管家)

- 新增 4 个工具: `get_forecast`(读预测引擎库: 方向/目标价/置信度/到期状态)、
  `get_opportunities`(机会候选 active)、`get_strategy_signals`(策略买/关注信号)、
  `get_notifications`(通知/提醒, 支持未读过滤)
- 对话助手从"市场分析师"升级为"系统管家": 可回答"系统预测了什么/今天发现什么机会/哪个策略给了信号/有什么通知"
- 预测库独立文件只读连接(mode=ro), 主库走应用 Session

### feat(ui) — 第三轮打磨

- **预测权重透明度**: 预测结果区展示当前 4 模型权重(Kronos 44% / Chronos 34% / XGB 11% / 线性回归 11%, 按历史命中率动态调整; 数据来自生产落盘权重文件, 留接口化 TODO)
- **移动端底部导航**: 5 槽位改 首页/持仓/机会/预测/提醒(模拟盘移入"更多"下拉), 桌面端导航不变
- **机会页刷新反馈**: 提交后按钮变"刷新中"防重复提交, 10s 轮询任务状态 → 完成 toast + 自动重载, 超时提示"1-3 分钟"

### 实测
- pnpm build 8.70s, 4 个新工具真实库验证通过(002361 预测/今日候选/策略信号/通知全返回)
- 机会刷新轮询实测: 任务 14s 完成, running 变 false, 完成 toast 触发

## 2026-08-13 (v0.2.26)

### 数据质量 — 去噪音 + 验证闭环

- **扫描池 A 股化**: market_scan 只扫 CN, 港美股不再生成快照/候选(实测占库 65% 噪音)
- **候选池精选化**: entry_candidates 只存 active 有信号记录(原 72% 观望占位不再落库)
- **自选去重**: (symbol, market, user_id) 唯一约束迁移(跨账户各自保留, 只防同账户重复)
- **权重闭环修复**: 只聚合 runs/live 回测(弃 legacy 污染) + MIN_SAMPLES 10→3 + 贝叶斯收缩
  → linreg 0.262(最高)→0.110, kronos 0.441 主导; 双写文件打通
- **预测节流**: 同 symbol 未到期不重复预测(HTTP 409 + force 参数)
- **候选验证闭环**: 扫描集不截断+最老优先, 到期即 100% 验证(真实库 401/401), 缺口报告
- **dark_flow 跨日修复**: 逐笔缓存加交易日字段, 跨日强制全量重拉(曾返 2 条残留)

### UX — 流式 + 自动刷新 + 可视化

- **聊天流式输出(SSE)**: stage 阶段提示 + delta 打字机 + done 落库, 兼容非流式
- **Dashboard/通知 30s 自动刷新**(visibility 暂停 + 防叠加)
- **骨架屏组件**(SkeletonRows)接入 Dashboard/通知
- **预测页四模型分歧度可视化**(纯 CSS 区间条图)
- **机会卡片 👍/👎 反馈按钮** + 后端 GET 接口补齐
- MinuteLwcChart 存量 TS 错误清理(build 前置)

### 实测
- 624 passed(新增 5 个测试文件), pnpm build 通过
- 真实库副本迁移验证: 48 条不变(跨账户保留), 唯一索引拦截同账户重复

## 2026-08-13 (v0.2.25)

### fix(frontend) — 首页白屏(旧 SW 缓存 + SPA 资源回退 HTML)

**根因**: 两个问题叠加:
1. SW 缓存名固定(panwatch-v13), 发版后旧缓存不清, 用户浏览器回退旧 index.html
2. SPA fallback 对所有路径返回 index.html —— 旧 index.html 引用的旧 hash JS 已不存在,
   服务器返回 HTML → 浏览器把 HTML 当 JS 执行 → 语法错误 → 整页白屏

**修复**:
- server.py: 静态资源(.js/.css/图片等)不存在时返回 404, 绝不回退 index.html
- sw.js: CACHE_NAME 注入版本号(构建时 sed), 发版后 SW 字节变化 → 浏览器自动更新清旧缓存
- sw.js: 不再缓存 '/' (index.html) 且导航请求不回退旧 HTML —— 旧 HTML 残留是白屏根源

### 实测
- 前端 build 9.2s, dist/sw.js 缓存名 = panwatch-v0.2.25
- server.py 语法 OK

# Changelog

## 2026-08-18

### feat — AuditMiddleware 操作审计全覆盖 (v0.2.65.5)

- 所有 2xx 写操作(POST/PUT/PATCH/DELETE)自动落 audit_logs, 补齐渠道/服务商/数据源/设置/用户等管理操作审计
- 内部自行 decode JWT, 独立 session 异步落库, 失败静默不阻塞
- 排除 auth(已有埋点)/静态/health/webhook

## 2026-08-13 (v0.2.24)

### fix(ai) — 场景绑定跨服务商 404 "model is not found"

**根因**: 统一 LLM 配置中心场景绑定时只改 `ai_client.model` 字符串, base_url/api_key 未同步切换。
agent_configs 里 premarket_outlook/daily_report 绑定商汤 deepseek-v4-flash, reports 场景绑定 agnes-2.5-flash,
绑定后请求仍发往商汤 API + agnes 模型名 → 404 (商汤无此模型)。商汤 API 实测有 deepseek-v4-flash,
直接调用正常, 故 404 非 key/模型缺失, 而是绑定切换不完整。

**修复**:
- src/agents/base.py `apply_scene_binding`: 绑定命中时整体重建 AIClient(base_url+api_key+model 一起换), 保留 total_tokens_used
- src/web/api/insights.py 两处(评估/公告解读): 改走 `_client_from_scene_cfg` 整体重建(原实现 _coerce_bound_model 返回字符串, bound.get() 静默失败)

### 实测
- 模拟 build_context(商汤 deepseek) → apply_scene_binding(reports) → client 变为 agnes base_url/model, 真实 chat 成功
- 231 tests passed

# Changelog

## 2026-08-18

### feat — AuditMiddleware 操作审计全覆盖 (v0.2.65.5)

- 所有 2xx 写操作(POST/PUT/PATCH/DELETE)自动落 audit_logs, 补齐渠道/服务商/数据源/设置/用户等管理操作审计
- 内部自行 decode JWT, 独立 session 异步落库, 失败静默不阻塞
- 排除 auth(已有埋点)/静态/health/webhook

## 2026-08-13 (v0.2.23)

### refactor(settings) — 删除多余模型引擎配置(统一 LLM 配置中心)

- 删除设置页「预测引擎 LLM(情绪打分)」配置组(forecast_llm_base_url/model/api_key 三个输入框)
- 删除设置页「预测引擎模型清单」只读区块(loadForecastModels + /forecast/models 前端调用)
- 后端: SETTING_DESCRIPTIONS / SECRET_SETTING_KEYS 移除 forecast_llm_* 三键, 删除废弃路由 /forecast-llm-config 与 /forecast-llm-sync-guide
- 预测引擎 AI 裁判/情绪打分模型统一走「场景分配」(ai_scene_bindings), 旧 forecast_llm_* 仅存 DB fallback(无 UI 入口)

### 实测
- 前端 build 9.0s, 产物无 forecast_llm 残留
- 后端 settings 相关测试 17 passed

# Changelog

## 2026-08-18

### feat — AuditMiddleware 操作审计全覆盖 (v0.2.65.5)

- 所有 2xx 写操作(POST/PUT/PATCH/DELETE)自动落 audit_logs, 补齐渠道/服务商/数据源/设置/用户等管理操作审计
- 内部自行 decode JWT, 独立 session 异步落库, 失败静默不阻塞
- 排除 auth(已有埋点)/静态/health/webhook

## 2026-08-13 (v0.2.22)

### fix(shadow) — 影子账户页白屏(React #31)

- 根因: rules 是 ShadowRule 对象数组(含 human_text), 前端却当字符串直接渲染 → React error #31 → 整页空白
- 修复: ruleLabel() 统一取 human_text(兼容字符串/对象), 两处渲染(我的画像区 + 行为画像区)
- key 改用 rule_id / index(对象不能作 key)

### 实测
- 前端 build 8.4s

## 2026-08-13 (v0.2.21)

### feat(shadow) — 影子账户"我的画像"区

- 进页面自动加载已存画像(profile_text 全文 + 盈利回合/总回合/胜率/偏好市场/持仓中位 5 指标 + 规则标签)
- 无画像显示引导卡片; 失败静默不阻断上传
- "更新画像"按钮复用上传文件选择器; 上传完成后自动刷新画像区



### feat — 统一 LLM 配置中心

- **场景绑定**: ai_scene_bindings 表, 6 场景(对话助手/TradingAgents/报告/AI裁判/自检/机会评分)各自绑定模型池模型, 回落默认。
- **画像注入**: build_system_prompt 统一入口, 有画像自动追加"用户交易风格画像"段, 无画像用默认提示词。
- **前端**: 设置页"场景分配"区(6场景×模型下拉, 绑定/解绑)。
- **停用独立预测情绪打分**: 消息面判断由 AI 裁判接管(用户决策)。

### feat — 内盘外盘口诀 + 分时双卡片

- **7 条实战口诀规则**: 真金进攻/主力撤退/诱多出货/压盘吸筹/多空平衡/控盘洗盘/对倒造假(位置优先+量价结合)。
- **轻接口**: GET /api/dark-flow?symbol= → {main_intent, inner_outer, mnemonic}。
- **分时图双卡片**: 主力意图 + 内盘外盘, 渲染在量柱下方; 背离时"咨询AI助手"按钮带上下文跳对话。

### 实测
- 600 tests passed
- 002361: 内外盘 48.6/49.3 均衡无口诀命中(符合预期), signal=主力净流入(主动买占优)低位承接
- 前端 build 8.8s



### feat(shadow) — 影子账户画像落地

- **A 方案**: 交割单画像落库 users.shadow_profile_json(按用户隔离) + 对话助手 system prompt 注入用户交易风格(截断300字+前3条规则, 无画像零开销)。
- **B 方案**: AI 裁判评估时注入用户画像(仅影响建议贴合度/表达方式, 不改 verdict/direction 判断)。
- 新增 GET /api/shadow/profile 端点; database.py 迁移自动加列(旧库兼容)。

### fix(forecast) — 预测单日超涨跌停

- clip ±40% → ±25%(linreg 外推 +39.7% 未被截断污染投票)。
- 新增单日 ±10% 物理约束(超限等比压缩, 方向不变)。
- 不做首日温和化(活跃票涨停常态, 用户决策)。

### 实测
- 画像注入: 对话助手输出"你423笔回合216笔盈利, 持仓中位5天" ✅
- 预测: 002361 T+1 +9.7% 单日 ≤10% ✅
- 27 tests passed


### fix

- 增强主程序 Docker 镜像安装 Debian 系统依赖时的下载重试、读取超时和 HTTP 管线容错，避免字体及 Playwright 运行库因上游 EOF 或临时 500 响应导致构建中断。
- 增加主镜像版本文件的非空构建校验，避免磁盘写入异常时生成缺少版本标识的可部署镜像。
- 增加主镜像 Python 依赖下载容错及 SQLAlchemy、行情本地包的构建期导入校验，阻止依赖层不完整的镜像进入部署流程。
- 将定时价格提醒的完整扫描移入工作线程，避免同步 SQLite 等待占用 Web 事件循环并造成健康接口间歇性超时。
- 移除服务启动 15 秒后自动执行的完整后验评估补跑，避免历史数据较多时启动阶段长期占用解释器并阻塞 Web 请求；保留每 6 小时维护计划和手动执行入口。
- 将主镜像系统依赖安装升级为同一构建层内的整体重试：保留已下载的 Debian 包并补拉失败项，解决 APT 连接重试无法覆盖单包 CDN 500 响应的问题。

### update

- 将本地开发 Compose 的主程序与预测引擎镜像标签从 `dev-0.1.1` 升级为 `dev-0.2.0`，并同步主程序内置版本号，避免部署 v0.2.x 代码时仍显示旧开发版本。
- 增加预测引擎 Docker 构建安装 Python 依赖时的网络超时与重试配置，避免 SciPy、XGBoost 等大型依赖在慢速网络下读取超时导致构建中断。

### fix(shadow) — 交割单上传无反馈

- **前端 accept 缺 .pdf**: 文件选择器直接拒绝 PDF 交割单(用户点文件无反应),已加 `.pdf`。
- **前端 20s 超时**: fetchAPI 默认 20s,但 586 笔交割单分析实测 111s,请求被 abort 静默失败;已提升到 180s。
- 提示文案更新: 支持 .csv / .xlsx / .pdf。

### 实测
- 生产 PDF 上传成功(shadow_c5c4c54e, 111s), 后端无问题, 问题纯在前端。



### feat(forecast) — 预测引擎全面升级

- **模型体系重构**: 移除 Lag-Llama(50%命中+自回归爆炸),接入 **Chronos-Bolt-small**(Encoder无累积误差, CPU 0.07s),投票改**加权平均**(XGBoost 0.4 / Kronos 0.25 / Chronos 0.25 / 线性回归 0.1,保留±40% sanity clip)。
- **AI 裁判层**: 模型预测结果交给对话助手评估(调 get_main_intent 主力意图/资金流/技术面/K线形态),裁判可**强势改最终方向**(B方案),verdict+理由入响应,失败自动降级不阻断。
- **权重闭环**: 新增 model_weights.py,模型权重按历史命中率(命中率²强化+下限8%保护)动态调整,回测后自动更新。
- **置信度融合**: 置信度融合 4 模型分歧度(≥75%一致升档/≤50%降档)+ AI裁判意见(confirm背书/adjust质疑)。
- **LLM 情绪修正**: 修复埋点 bug(adjustment_pct 全 0 → 真实记录 score×0.75);接入**腾讯近5日主力资金流**(免key)进 LLM prompt,资金面影响情绪打分。
- **交易日口径统一**: prediction_outcome 改用交易日(工作日)计算 horizon,与回测引擎一致。
- **历史到期对照**: /forecast/history 增加 outcome_return_pct/outcome_status(hit/miss/pending),前端历史表展示预测vs实际。
- **前端**: 副标题更新(Kronos+Chronos-Bolt)、回测注脚诚实文案、自选/持仓一键预测(单选)、预测进行中锁定输入(严格单只)。
- **修复**: 服务 token sub 用 owner 真实 UUID(此前固定 "user" 导致 401)。

### 实测
- AI 裁判真实调用通过(conv_id=27, 裁判引用主力意图/技术面数据给出 confirm+up)
- 4 模型加权投票 vs 旧中位数: final 12.86 vs 12.5(XGB 权重生效)
- 历史到期对照: 002361 miss(-3.04%) / 000938 hit(-0.97%) / 002661 hit(+3.87%) 全对
- pytest 4 passed + 前端 build 8.5s



### fix(shadow) — 影子账户交割单上传

- **修复上传报错 422 `missing file`**: `fetchAPI` 对 FormData 不再强制设置 `Content-Type: application/json`(由浏览器自动带 multipart boundary),FastAPI 才能收到 `file` 字段。
- **修复交割单解析**: Excel/PDF 统一走"文本行 → 去 nan 占位/相邻重复 → 业务名称关键词定位 → 10 列对齐"解析,兼容券商交割单的合并单元格展开、表头不在首行、中文名称被拆开等情况。
- **新增 PDF 格式支持**: 允许上传 .pdf 交割单(pypdf 提取文本),与 Excel 版解析结果一致(实测 PDF 更完整,xlsx 导出偶有漏行)。
- 已实测: xlsx 562 笔 / PDF 586 笔解析成功,端到端上传 → 画像 → 报告全通,回归测试 14 passed。



### feat(auth)

- **修改密码功能**: 头像下拉菜单新增「修改密码」→ 弹窗(旧密码/新密码/确认新密码)。
  - 后端 `/api/auth/change-password` 增加旧密码校验(错误返回 400「旧密码不正确」),新密码 ≥ 8 位;
  - 改密成功后 `token_version += 1` 使该用户所有旧 token 失效,强制重新登录(防会话劫持);
  - 前端校验:新密码长度、两次输入一致(红色提示),成功 toast「密码已更新」,失败展示后端错误;
  - 已通过 pytest(12 认证测试) + tsc + 浏览器全流程实测。



### feat(ui)

- **PC化改造(5项, 多智能体流水线完成)**: ①桌面导航13项全平铺并按业务分组(行情/交易/系统), 不再藏进头像下拉; ②界面密度三档体系(compact/normal/comfortable, 通过 html[data-density] + CSS 变量驱动, 默认 normal 完全不变); ③PC快捷键(⌘/Ctrl+K 打开日志、⌘/Ctrl+, 跳设置、g→d 首页、g→p 持仓、? 帮助, 桌面端≥768px生效、输入框内自动失效); ④股票行右键菜单(加入自选/查看详情/复制代码/模拟买入, Stocks+Dashboard 共用 StockContextMenu 组件); ⑤Dashboard 多列工作台(≥1280px 三列: 今日要紧事3|组合体检6|机会精选3, 次级两列简报6|机会发现6, 1280px以下逐像素回退)。
- 全部改动经生产构建(vite build) + tsc 编译 + 浏览器实测验证。



### perf

- **汇率源新浪→腾讯**: 海外节点新浪超时(每次冷启动白等 3s×2), 腾讯 qt.gtimg.cn 秒回。
  失败短缓存 5 分钟 + 线程锁防并发重复等待 → **持仓/自选汇总 35.71s → 0.57s 冷 / 0.006s 热**
- **逐笔增量续拉**(用户设计): 缓存记录 last_page+last_seq, 过期只拉新增页合并去重,
  序号断裂(新交易日)才全量重拉 → summary 冷启动大幅加速, TTL 内零请求
- **summary 一次计算**: `_main_intent_both` 一次 compute_dark_flow 产出字符串+结构化(原各调一次翻倍耗时)
- **筹码分布腾讯优先**: 腾讯当日分价(0.17s 秒回)优先, 新浪(海外慢 8-10s)兜底 → summary 冷启动 4.16s→2.48s

### fix

- `data_status`/`tick_count` 透传到主力意图正常路径(与 insufficient 分支一致)

## 2026-08-12 (v0.2.12)

### fix

- **腾讯逐笔单行页被误判为空页(2026-08-12 盘中修复)**: 竞价/开盘初期只有 1 笔成交(如 09:25:00 竞价单)时, `_fetch_all_ticks` 的 `len(rows) < 2` 空页判定把它当空页丢弃 → 逐笔为空 → 主力意图返回 None → 前端"看不到"。修复: 空页判定改为 `['']`(真正空页), 单行页正常处理。
- **竞价/开盘初期数据量门槛**: 非竞价成交 <30 笔时 `data_status=insufficient`, 前端显示"数据不足(N笔)"占位, 不标误导性吸筹/派发箭头。
- **dark_flow 真实数据测试弹性化**: 断言从"盘后全天 >1000 笔"改为盘中/盘后自适应(盘中只要求非空), 盘中跑测试不再误报。

## 2026-08-12 (v0.2.10)
### fix

- **主力意图方向判据对齐 v14(2026-08-12 用户发现)**: `_main_intent_structured` 的 direction 原逻辑只看主力净额(>500万=买 / <-500万=卖), 与同花顺"表面结论"同样粗暴——神剑股份 002361 主力净流出-2466万(超大单+5967万/大单-8433万)被误判"派发"。修正为 v14 完整判据: 参与度≥35% 且 买占≥48% = 强吸筹力度, 净流出+强吸筹 → wash(洗盘吸筹), 净额平衡+强吸筹 → absorb(疑似吸筹)。K线 markers 同步支持 5 档(吸筹红↑/洗盘吸筹橙↑/疑似吸筹黄↑/派发绿↓/平衡), 图例加参与度/买占展示。注: 同花顺同数字判"派发"是它的结论规则问题(只看净额), 数据本身与我们逐笔一致。



### feature

- **TradingView Lightweight Charts v5 升级**(图表重构):
  - K线图 multi-pane 原生多面板(价格/量能/MACD/RSI 四区), 替代原 3 个独立 chart + 手动可见范围 sync
  - 主力意图 markers: 吸筹↑红/派发↓绿 标在最新 K 线, 近 60 根涨停/跌停点自动标注
  - 筹码叠加: 筹码峰/成本带上沿/下沿 price lines(黄线筹码峰, 灰线成本带)
  - 主力意图图例卡: 方向/净额/超大单大单/筹码峰/成本带/获利盘
  - 分时图从 ECharts 迁至 Lightweight(价格+均价+昨收虚线+量能 pane), 砍掉 ECharts 依赖, 前端统一一套图表库
- **TradingView Alert Webhook 接收端点**: `POST /api/webhooks/tradingview`(X-PanWatch-Secret 鉴权, 环境变量 PANWATCH_TV_WEBHOOK_SECRET; 未配置时禁用), 用户 Pine 策略 Alert 可直接推送 PanWatch 告警 → 站内通知 + 外发渠道

### fix

- klines summary API 新增 `main_intent_structured` 结构化字段(方向/净额/筹码峰/成本带), 前端 markers/筹码叠加不再依赖字符串解析

## 2026-08-12 (v0.2.9)

### fix

- **腾讯逐笔翻页截断修复**(`src/core/dark_flow.py`): 原逻辑遇第一个空页即 break, 腾讯偶发限流让单页返回空时, 盘中实时拉取会中途截断 → 主力意图算在残缺数据上。实测第60页首次请求为空、重试后正常返回。修复: 连续 2 个空页才停, 单页空(限流)自动跳过继续翻页。
- **事件溯源 v2**(盘中监测): 题材词改为包含匹配(修复"机器人概念"≠"机器人"导致题材词池为空), 命中条件=标题必须含该股题材词, 通用事件词仅用于发现不直接命中(修紫光股份 000938 误报火箭实验室新闻); 看涨形态去重限流(十字星变体合并, 同方向最多3个, 11→7个); 板块面新增"个股所属概念"行。
- **收盘复盘主力意图口径对齐**(`src/agents/daily_report.py`): 收盘复盘自选股详情新增"主力意图(逐笔V14)"行, 与盘中监测/AI助手三处口径对齐, 修"主力流出1.16亿"东财口径误判(逐笔口径实际超大单+5967万仍在吸筹)。

本文件记录项目中每一次可提交变更。新记录按日期倒序添加，并归入 `fix`、`feature`、`update` 或 `doc` 类别。

## 2026-08-11

### feature

- **主力意图独立段(盘中监测)**: 资金面外新增 `## 主力意图` 段,口径隔离(资金面=东财/腾讯静态资金流,主力意图=逐笔实时+筹码面+股东户数交叉验证)。包含: 主力方向(≥20万腾讯官方口径=超大单+大单)、参与度/买占比(同花顺暗盘强度口径)、5日阶段、竞价撮合、尾盘特征、吸筹价位、拆单识别(逆势+套牢位)、筹码分布(峰/获利盘/套牢盘/COST50)、股东户数变化(筹码集中度)。
- **暗盘资金算法 v14(主力买入强度口径)**: 同花顺"暗盘流入多=主力吸筹"确认后,主力信号=主力(≥20万或600手)净额+参与度/买占比;识别"主力净流出但参与度高=洗盘吸筹"、"超大单托盘+大单出货=派发"等分歧形态。竞价单(9:25-9:30)独立处理(非主动买卖,混入会方向反转)。
- **筹码分布计算器**(`src/core/chip_distribution.py`): 三角分布+换手率衰减模型(对齐通达信/同花顺,偏差<10%),输出 COST(10/50/90)、获利盘比例、筹码峰(主力成本区)、集中度。数据源=腾讯800天前复权日K,免费。
- **事件溯源+题材关联研判(新闻段)**: 个股新闻之外,用题材词(军工/航天/无人机等事件驱动型优先)+通用事件词(火箭/发射/卫星/获批等)反查 `news_by_keyword` 市场级事件;利好✅/利空⚠️/中性三级标记(失利/推迟/爆炸=⚠️利空,获批/中标/成功=✅利好),⚠️>✅>中性排序;研判指引强制 LLM 结合「主力意图」段综合判断事件性质对主力行为的影响(利空低吸/利好派发陷阱)。可关联"长征七号甲发射失利/朱雀三号推迟"类市场级事件(实测命中)。
- K线形态识别已在技术分析段(自研同花顺形态+TA-Lib 61种双引擎,含位置/强度/规则提示),本轮验证完整链路可用(神剑: 纺锤线看涨/上升三法看跌)。
- **主力意图三处落地**: ① 盘中监测Prompt独立段(LLM综合研判) ② 个股通知卡片结构化摘要(`_main_intent_summary`,不依赖LLM复述) ③ AI助手对话工具 `get_main_intent`(问答"主力意图/吸筹/派发")。
- **口径隔离修复**: AI助手System Prompt引导(主力意图以get_main_intent逐笔口径为准,get_capital_flow东财仅参考)+盘中监测资金面/主力意图段显式标注口径(东财四档vs腾讯逐笔,研判以逐笔为准)。修"神剑主力一致撤退"误判(东财-1.16亿 vs 逐笔超大单+5967万,逐笔已验证与同花顺暗盘对齐)。
- **个股K线窗口独立展示主力意图**: `/klines/{symbol}/summary` API 增加 `main_intent` 字段,前端 KlineSummaryDialog 底部玫瑰色区块展示(筹码峰/成本带/参与度/竞价)。
- **数据源开关**: `PANWATCH_DARK_SOURCE` 环境变量(L2预留: l2_tencent/l2_sina/l2_itick),`src/core/dark_l2.py` 占位,未接入自动回退腾讯逐笔。
- **性能打磨**: 腾讯逐笔30s缓存+单页重试(盘中轮询9.3s→0ms/标的),新浪分价表1h缓存(2.6s→0ms)。

### fix

- 修复预测/回测股票搜索检索不到部分深市主板股（如豫能控股 001896）的问题：① `query_all_stock` 盘中/未收盘时当天返回空列表，现回退最近 8 天找最近有完整列表的交易日；② 主板过滤白名单原为 `("60","00","002")`，漏掉深市主板新代码段 `001`（121 只，含豫能控股）和 `003`（42 只），现改为 `600/601/603/605 + 000/001/002/003`。引擎直连与 web 转发（`/api/stocks/search`）均已验证可搜到豫能控股。
- 修复盘中监测报告三大数据失真问题（神剑股份 002361 案例定位）：
  - **量比口径混用**：K 线口径量比（今日总量/5日均量）盘中系统性偏低，开盘初期可低至 0.04 被误判"缩量"，而实时量比 13.52（放量）。修复：新增 `get_realtime_volume_ratio()` 直取腾讯实时量比（30s 缓存），Prompt 量能段实时口径优先。
  - **KDJ 临界误报**：开盘瞬间 K≈D（如 K=79.2/D=79.3 差 0.15）时金叉/死叉随价格抖动翻转。修复：K/D 差值 <1.0 时状态标注"临界(金叉弱/死叉弱)"，Prompt 附"禁止据此单独判断方向"提示。
  - **快照时间误导**：报告把采集时刻快照说成确定事实。修复：Prompt 顶部注入数据时刻提醒，要求 AI 使用"当前/截至采集时刻"口径。
- `StockData` 增加 `volume_ratio` 字段透传实时量比（`md_stock_data` 同步）。
- 修复资金流 P0：东财 push2delay 开盘初期 f62/f184/分项全 0（数据未初始化）被当有效数据，导致盘中监测把"主力净流入 1.59 亿"报成"数据缺失"。现全 0 视为未就绪回退其他源（`_fetch_direct_flow`）。
- 资金流链路移除悟道 `intraday_main_flow`（9:15-10:30 限流且只给主力净额无四档），直接走东财 push2delay → Engine（新浪 T-1/东财）两级。
- 修复盘中监测"无需提醒"建议无分析内容：AI 输出 `[无需提醒]` 时直接 return 导致 signal/reason 为空，建议池里只有"持有"动作没有分析。现提取"无需提醒"后的原因作为 reason（无内容时兜底"AI 判断无需提醒"），signal 兜底"无异常"。
- AI 助手层数据接入修复（去悟道单点）：
  - `get_market_news` 改为**底层多源快讯优先**（`flash_news` 引擎：财联社/新浪/东财7x24 市场级快讯，多源主备+降级），悟道热榜/简报降级为补充，失败不影响主链路。
  - 新增 `auction_collector.py`（竞价统一入口）：悟道优先（独家 bidStrength/弱转强字段），限流窗口 9:15-10:30 快速失败不白等，悟道空返回/失败时降级腾讯批量行情算竞价高开榜；30s 缓存避免助手重复提问重复请求。
  - `_fetch_auction_context`（chat 助手）与 `auction_review`（竞价复盘 agent）改用 auction_collector，消除双实现。
- 盘中监测**均线临界保护**：现价与 MA5/MA10 距离 <1% 时 Prompt 注入警告行，禁止 AI 断言"站上/跌破"（修复神剑股份场景：现价 11.95 在 MA5 11.90 之上 0.42%，AI 却说"跌破MA5"）。新增模块级函数 `build_ma_critical_warnings`（build_prompt 与测试共用）。
- **腾讯证券数据源接入**（2026-08-11，网页端 gu.qq.com 同源接口）：
  - 新增 `TencentFundflowVendor`：`proxy.finance.qq.com/cgi/cgi-bin/fundflow/hsfundtab` 当日实时资金流（主力/超大/大/中/小四档 + 5日主力净额），作为东财 push2delay 之后的**第二实时源**（东财开盘全 0 未就绪时腾讯顶上）；口径：主力=超大单+大单。
  - 新增 `tencent_panel.py`：盘口大单占比（`qt.gtimg.cn/q=s_pk`）、大单分档统计（`stock.gtimg.cn/data/index.php?appn=dadan`）、分价表（`appn=price`）。
  - 盘中监测资金面追加"盘口大单占比 + 大单分档统计"段（腾讯面板，失败静默不影响主链路）。

### feature

- 新增**策略批量选股**：机会页新增「策略选股」区块（策略库 7 个 YAML 策略 → 全市场/自选池批量扫描 → 按分数排序输出）。
  - 后端 `POST /api/strategies/scan`：腾讯行情 100 只/批批量拉取（盘中含 PE/PB/市值全字段，非交易时段量能字段为 0 时前端提示），逐只跑策略硬过滤+因子打分，返回命中名单。
  - `_evaluate_strategy` 纯函数抽取：apply/scan 共用同一套过滤打分逻辑（原 apply 150 行重复代码删除）。
  - 腾讯行情 vendor 补 `pb_ratio` 字段（parts[46] 市净率，之前解析遗漏）；`pe_ratio` 自动规范化到 `pe_ttm`。
  - dual_low 策略补 `pe_ttm_min: 0.01`：排除负 PE 亏损公司混入"低估值"名单（真实扫描发现振华新材/晶科能源负 PE 入选）。
  - 全量测试 531 通过（新增 7 个策略评估单测）。

## v0.2.0 (2026-08-10) - 多用户系统

### 🆕 多用户(团队 4-5 人)
- **用户表+认证**: users 表(UUID 主键, role owner|member), 旧 admin 自动迁移为 owner, JWT 加 user_id/role, token_version 踢人
- **用户管理 API**(仅 owner): 建子账号/禁用/启用/重置密码/删除
- **数据隔离**: 持仓/自选/账户/渠道按 user_id 隔离(自己的+全局共享), 旧数据迁移归 owner
- **预测并发**: predict 信号量(2并发)+ 结果缓存(30min), backtest 单并发
- **推送隔离**: 渠道按用户, push_notification(user_id), 定时报告订阅(盘前/盘中/复盘/预测), cron 按订阅用户推送
- **前端**: 登录存用户, Settings 用户管理页+订阅开关, 非 owner 无管理权限
- 全量 524 测试通过

## 2026-08-10

### fix

- 修复 ECharts 分时图依赖无法按仓库 pnpm 工作区规范部署的问题：统一更新根 `pnpm-lock.yaml`、移除子包 npm 锁文件、迁移 pnpm 11 构建脚本白名单，并让 Docker 前端构建阶段预先加载全部 workspace 清单，确保 frozen-lockfile 构建可复现。
- 通知详情现会按 Trace ID 加载关联的 Agent 执行结果，显示完整分析、状态、耗时和模型；同时修复非交易时段实际已跳过却通知为“已完成”的问题，并兼容纠正已有历史通知。
- 增强通知管理中心左侧列表的选中态：增加主色竖条、背景与描边、图标光环、“正在查看”标记和高亮箭头，使深色主题下也能明确识别当前通知。
- 修复任务通知点击后跳到不存在的 `/stocks` 路由而显示空白页的问题：新通知改用持仓页链接，已存历史通知自动兼容转换，未知路由也不再停留在空壳页。
- 将持仓中的“提醒”建议和价格提醒操作改为圆形闹钟图标，已启用规则使用高亮状态并保留数量提示。
- 在持仓盈亏卡片中同时显示相关市场的当前状态（如“A股 · 盘前”或“美股 · 休市（周末）”），并升级静态缓存版本，避免旧页面继续显示过期的“今日盈亏”标签。
- 修复盘前或休市时把上一交易日涨跌误标为“今日盈亏”的问题：保留腾讯行情真实时间，并按报价日期显示“今日盈亏”“上一交易日盈亏（日期）”或“最近交易日盈亏”。
- 修复 PushPlus 设置页重复测试固定文案时被服务端判定为验证错误的问题；测试消息现带有唯一时间与编号，并提供更明确的失败提示。
- 修复右下角 Chat 助手未解析 GFM Markdown 的问题，现可渲染表格、代码块、列表和链接，并为窄屏表格提供横向滚动。
- 修复 PushPlus 渠道可能只留站内通知的问题：开启 `info` 外发、严格验证渠道配置和 API 回执，并在设置保存时自动发送测试消息。
- 修复预测容器内 PanWatch 地址和认证硬编码，改用 `PANWATCH_URL` 与数据库签发的短时 Token；同时将预测历史持久化到 `panwatch_forecast_data` 数据卷。
- 让 Docker Compose 预测引擎直接读取 PanWatch 数据库中的 LLM 配置，移除不适用于容器部署的 systemd 同步提示。

### feature

- PC 版 AI 助手支持拖动标题栏自由移动，移动过程自动限制在浏览器可视范围内；自由位置会在刷新后恢复，并在窗口尺寸或浏览器大小变化时重新约束，选择固定停靠位置可随时复位。
- PC 版 AI 助手新增窗口设置：支持紧凑、标准、大窗口、宽屏四档尺寸，以及左下、底部居中、右下三种停靠位置；窗口与悬浮入口同步移动并在浏览器中记忆选择，移动端仍保持全屏。
- 通知管理中心新增紧凑的推送渠道筛选，可与未读、推送失败和通知类型组合使用，支持 PushPlus 等实际渠道、仅站内通知及历史未记录渠道。
- 通知中心新增当次实际推送渠道回执：列表和详情会显示 PushPlus、Telegram、企业微信等渠道的名称及发送状态，混合渠道分别展示；只记录安全元数据，不保存 Token 或 Webhook，历史数据明确标注“未记录渠道”。
- 新增通知管理中心：支持全部、未读和推送失败筛选，可查看完整 Markdown 正文、站内送达状态、外部推送结果与错误、来源、Trace ID 及关联页面；顶栏消息现先进入详情中心。
- 新增电脑 Web 推送：用户可在设置页授权并测试浏览器系统通知，页面打开或在后台运行时会将新站内消息去重后推送到电脑。

### update

- 移除 PC 版 AI 助手的全屏遮罩和全屏背景模糊，窗口打开时仍可操作、点击和滚动其他页面区域；视觉聚焦改为仅作用在助手窗口周围的柔和模糊阴影，不再覆盖页面。
- 优化 PC 版 AI 助手打开后的视觉层级：增加自适应明暗主题的背景遮罩与轻微模糊，强化窗口描边和阴影，并支持点击遮罩关闭助手，使窗口边界更清晰；移动端全屏模式不受影响。
- 将通知中心三张大型统计卡收紧为带数量徽标的分段筛选栏，并与通知类型筛选合并到同一行，保留全部功能的同时减少空间占用。

### doc

- 明确开发阶段默认使用热加载或重启服务，仅在发布或修改未挂载的前端产物、依赖、Dockerfile、本地安装包时重建镜像。
- 将 Docker 打包后的资源清理纳入固定开发流程：新容器健康后清除未使用的旧 PanWatch 镜像，同时保护运行中镜像、数据卷和共享构建缓存。
- 建立 `CHANGELOG.md` 及每次可提交变更都必须同步记录的开发规则。

## 2026-08-09

### fix

- 无。

### feature

- 新增 `dev-0.1.1` 本地 Docker 开发环境，同时运行 PanWatch 主服务和预测引擎，并复用已有 `panwatch_data` 数据卷。
- 新增同花顺 Web 数据源 vendor（`ths_web.py`）：实时行情（fuyao 统一行情聚合接口，沪 market=17/深 market=33，免登录）、日K线（d.10jqka.com.cn）、快讯（news.10jqka.com.cn）、F10 基本面；已注册 quote/kline/flash_news/fundamentals 四类，香港节点实测可用。
- 新增同花顺板块/大盘资金数据源（`ths_flow.py`）：行业/概念资金流向页面版解析 + 全市场大盘资金汇总，已在数据源设置页可维护。
- 新增同花顺扫码登录 session 管理（`src/core/ths_auth.py` + `/api/ths/*`）：扫码生成二维码→轮询→自动登录+持久化，设置页可查看登录态并扫码续期，凭证自动续期无需人工干预。
- 新增影子账户（Shadow Account）页面端入口（`/shadow`）：拖拽上传交割单 → 行为画像（回合/胜率/持仓/偏好市场）+ 行为诊断（处置效应/过度交易/追涨/锚定）+ 归因分析（影子收益/实际收益/差值），支持 HTML/PDF 报告。
- 新增 K 线组合形态识别（`src/core/kline_pattern.py`，同花顺教学体系）：金针探底/双针探底/红三兵/涨停双响炮/揭竿而起/上升三法/小步上扬/放量突破 8 种形态；已接入技术指标 `kline_pattern` 字段（单根形态未命中时输出组合形态），并新增 AI 助手工具 `get_kline_patterns`（回答"K线什么形态"自动识别）。
- 新增八大看跌 K 线形态识别（同花顺第二篇学习文）：三只乌鸦/黑三兵/空方炮/倾盆大雨/黄昏之星/看跌尽头线/兄弟剃平头/二级倒锤头；高位判断含涨幅门槛（形态启动前前5日涨超3%）排除横盘误报；真实数据验证 000001 空方炮。
- 新增经典形态识别（同花顺《K线形态大全》可量化部分）：双底突破(W底)/双顶破位(M头)/上升三角形突破/下降三角形破位/上升旗形突破/下降旗形破位；双底双顶用两低/高点索引精确计算颈线；全量 502 测试通过。
- 新增 AI 助手集合竞价工具 `get_auction_data`（6 场景）：竞价全景（涨停/跌停/委买额/昨炸板反馈）/最强个股（bidStrength）/主线题材/弱转强/被核风险/盯盘名单；9:25 前返回"当日竞价未生成"提示。
- K线组合形态接入技术指标建议评分体系：后端 summary 新增 `kline_patterns`（全部识别到的组合形态含信号/位置）；前端技术指标建议按形态评分（看涨 +1/+2、看跌 -1/-2，强信号金针/双针/双响炮/三只乌鸦/黄昏之星等 ±2），形态作为可解释因子展示。
- 大宗商品轮动前瞻接入盘前事件驱动（同花顺学习文《大宗商品的轮动顺序》）：轮动剧本 能源冲锋（石油/煤炭）→ 金属狂潮（铜/铝/钢铁）→ 农产压轴（粮食/棉花/大豆）→ 黄金返场（避险）；`commodity_rotation.py` 从盘前事件流识别当前阶段 + 预判下一幕题材（能源涨→埋伏金属，金属涨→埋伏农产品，黄金启动→防御），盘前 Agent 报告自动注入。
- 地缘冲突传导链检测（学习文《以伊开战五波冲击》）：冲突关键词（开战/战争/袭击/导弹/制裁/中东/俄乌等）命中即进入地缘冲突阶段（优先级高于商品轮动）；五波传导 能源→大宗→通胀→货币→避险，联动板块 石油/油气/黄金/军工/国防；测试 9/9 通过。
- 八大进场信号识别（学习文《手把手带你看懂八大进场信号》）：早晨之星/底部十字星/底部强势大阳线/底部大长腿/大锤和小锤/大阳包小阴/大阴后两小阳/进击两阳线 8 种底部看涨/抄底形态；低位判断用形态启动前最低价（收盘已反弹也算低位），锤子线独立判定不卡小实体，底部十字星需前 5 日跌超 5% 排除横盘；真实数据 600519 进击两阳线；全量 514 测试通过。
- 接入 TA-Lib 61 种标准 K 线形态识别（ta-lib==0.7.1，新版自带二进制免编译）：`_detect_talib_patterns` 扫描全部 CDL* 函数 + 中文名映射；技术指标 summary `kline_patterns` = 自研 30 种（同花顺教学体系）+ TA-Lib 61 种合并；AI 助手返回 TA-Lib 标准形态（中文名+强度）；前端评分器适配 TA-Lib 字段（强信号锤子线/早晨之星/乌云盖顶等 ±2）；真实数据茅台 15 个 TA 形态；全量 515 测试通过。
- 修复 AI 助手技术面数据获取失败：`_fetch_technical_context` 引用不存在的 `DataCollector` 类（只有 DataCollectorManager）→ import 抛异常返回空；改用 KlineCollector + MarketCode，修正 summary 结构读取（无嵌套键兼容）与字段名（rsi_14→rsi6/rsi_status、support_level→support、resistance_level→resistance），并附带 K 线形态；验证 603061 金海通技术面完整返回。
- 盘中监控加 K 线形态提示 + 技术指标建议形态解释：盘中监控技术分析块输出自研同花顺形态 + TA-Lib 标准形态（信号方向/强度/位置 + 交叉确认规则）；技术指标建议弹窗为每个组合形态加独立 TechnicalBadge（看涨红/看跌绿）+ 悬停解释（来源/强度 + 50+ 形态中文含义映射表 TALIB_PATTERN_EXPLAIN）；typecheck+build 通过。
- 接入国内数据网关（115.190.177.213:8100，Debian12 + FastAPI）：香港节点东财资金流字段被风控断连（push2 ulist f62 间歇 RemoteDisconnected），网关用东财 push2delay 域名稳定返回今日实时四档资金流（主力/超大/大/中/小单）；`capital_flow_collector` 优先调网关（今日实时，含数据基准日标注），失败回退悟道/Engine（T-1）；`CN_GATEWAY_DISABLE=1` 测试禁用开关；验证神剑股份今日主力净流入 5781 万（与券商同源，不再是 T-1 的 2.62 亿）；全量 515 测试通过。
- 资金流双模式接入（大陆/海外）：`CN_FLOW_MODE=direct` 大陆本地直连东财 push2delay（不依赖网关）；`gateway` 海外走国内网关代理；`auto`（默认）先直连失败走网关自动检测；生产在韩国首尔节点，auto 模式实测直连 push2delay 可用、网关作兜底。
- 预测引擎隔夜事件源接入同花顺快讯（免key 7×24 新闻流）：根因是 wudao API 免费额度 50/50 用完 → `official_announcements` 拉取失败 → FCC 禁令类隔夜新闻没进模型 → 预测方向失准；新增同花顺快讯源按股票名/代码匹配（东财行情 f58 拿名称），覆盖公告源不含的财经新闻（FCC 禁令/行业政策/涨价）；兜底链 wudao公告→同花顺快讯→东财公告；验证克明食品增持利好 → 修正 +1.25%。
- TradingAgents 资金流接入今日实时（双模式网关）：之前 `md.capital_flow`（东财/新浪 T-1）与今日实时网关是两条独立路径；新增 `_fetch_ta_capital_flow` 优先走 CapitalFlowCollector 今日实时（direct/gateway/auto 双模式）失败回退引擎；TradingAgents 多 Agent 分析资金面不再用 T-1 数据；验证神剑股份主力 5781 万/超大单 4615 万。
- 修复分时K线不显示 + IndexDetail 大盘资金流：①分时路由 `/quotes/minute` 被 `/{symbol}` 抢占（404 行情不存在）→ 改 `/minute/{symbol}` 明确路径；②补 `import time as _time`（容器内 NameError 500）；③IndexDetail 页新增大盘资金流卡片（同花顺源，总流入/流出/净流入/板块数），替代"东财502成交额趋势替代"的空白提示；生产验证分时 267 点 + 大盘资金净流入 109.8 亿。
- 热门股票假展示修复 + 大盘资金流板块明细榜：①根因是东财 push2 clist 海外 502 → 回退 DB 旧快照（几天前数据=假展示）；国内网关新增 `/cn/hot-stocks` `/cn/hot-boards`（push2delay 国内直连），discovery 失败顺序 live→网关→DB快照，生产验证中际旭创/新易盛真实热门；②大盘资金流从"50板块求和"改为同花顺行业资金明细 流入Top10/流出Top10 板块榜，前端 IndexDetail+Dashboard 展示🔥流入/💧流出板块（贵金属+27.4/白酒+19.2，建材-15.3/电力-12.5）。
- 大盘资金流对齐同花顺APP：之前用同花顺 hyzjl 行业资金求和（总流入2611亿口径不对）；改为国内网关 `/cn/market-overview`（东财 push2delay 沪深指数主力净流入），两市主力净流入 -414.7亿（沪-136.5/深-278.2）、总成交额 25231亿（APP 25387 接近）、涨跌家数 3924/1266（APP 4068/1391）、上证点位 3966.59 +0.67% 与APP完全一致；前端展示主力净流入+成交额+涨跌家数+沪深分项+板块榜。
- 分时K线双修复：①指数分时代码冲突——上证指数000001与平安银行000001撞车，prefix 误判 sz → 显示个股数据；加指数代码白名单（000001/000300/000016/000905/000852→sh，399开头→sz），生产验证上证指数 242点 最新3966.59（与APP一致）；②分时图±分界线——后端返回 prev_close（腾讯 qt list[4] 昨收），前端 y 轴以昨收为对称中心 + 灰色虚线"昨收"基准线（昨收上=红涨下=绿跌，同花顺/腾讯标准分时）。
- 分时图切换 ECharts 6.1.0：新组件 MinuteEChart（价格线红涨绿跌+渐变面积、均价线黄色仅个股、昨收基准虚线±分界线、下方成交量柱红涨绿跌、十字光标+tooltip 价格/涨跌幅/均价/量、y轴以昨收对称±2.5%保底），替换 InteractiveKline 手写 SVG（删~100行）；数据卡改为现价/较昨收/均价(个股)/昨收/成交量；日K线仍用 Lightweight Charts（CDN 全局 window.LightweightCharts，unpkg+jsdelivr 双源降级）。
- 分时图白屏修复（v0.1.40→v0.1.44，本地 dev 完整错误栈定位）：①React 崩溃白屏=setOption 抛异常无错误边界→加 try/catch+init 防重入；②`undefined.get`=成交量 series xAxisIndex:1 但只定义 1 个 xAxis→补 xAxis[1]；③`xAxis and yAxis must use the same grid`=series xAxisIndex:0+yAxisIndex:1 跨 grid→series 改 xAxisIndex:1+yAxisIndex:1（双 grid 双 xAxis 双 yAxis 各自配对）；④容器 0 尺寸→init 后强制 resize+ResizeObserver；生产验证 canvas 渲染成功，价格线/昨收虚线/成交量柱正常，上证指数分时 3966.59。

### update

- 数据源设置页可维护全部数据源：`board_capital_flow`（板块资金）/`market_capital_flow`（大盘资金）加入类型注册与分类，设置页「同花顺登录」区块显示账号/UserID/过期时间。
- DB 数据源初始化：补齐同花顺实时行情/K线/快讯/基本面配置，生产环境同花顺实时行情成为默认主源（priority 0）。

### doc

- 无。


## 2026-08-25

### update

- 大盘区三个后端小改动(v0.4.7):
  - **资金流历史快照落表**: 新建 PG 表 `market_flow_snapshots`(CREATE TABLE IF NOT EXISTS, 双方言),
    接口成功返回后异步后台线程写一条快照(30s 同进程节流, 失败静默 logger.debug)。
    新增 `GET /api/market-data/market-capital-flow/history?hours=4` 返回当日 ts 序列(上限 500 条)。
  - **涨跌分布分桶**: 新增 `GET /api/market-data/breadth-distribution`, 9 档分桶
    (跌停/<-5%/-5~-3%/-3~-1%/-1~1%/1~3%/3~5%/>5%/涨停) + 60s biz_cache 缓存。
    数据源: 东财 push2 clist 全 A 股列表(f2 最新价、f3 涨跌幅%)。
  - **mainline 昨日排名**: 新建 PG 表 `mainline_rank_daily`(date, name, rank, score, PRIMARY KEY(date,name)),
    主线榜每次计算成功后 upsert 当日快照, 然后查昨日 max(date)<today 对比算
    `rank_change`(昨日 rank - 今日 rank, 正=上升), 首次无昨日数据 → rank_change=null。
