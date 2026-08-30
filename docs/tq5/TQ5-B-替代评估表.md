# TQ5-B · 通达信 vs SIDA 现有数据源 替代/补齐评估表

> 对照对象: SIDA 仓库 packages/marketdata/src/marketdata/vendors/ 实际 vendor 清单(40+ 源, 17 类)
> 评估口径: **替代**=TDX 可完全顶替(降成本/提实时/去风控) · **补齐**=新增维度 · **保留**=TDX 补不了

| SIDA 数据类 | 现有 vendor | TDX 对应能力 | 判定 | 理由/条件 |
|---|---|---|---|---|
| A股实时行情 | eastmoney / sina / tencent | TQ get_market_snapshot | **替代**(首选 tq) | 本地 <30ms、无频率风控; 外盘容灾保留 1 个现有源 |
| A股 K线(≤800日) | tencent / eastmoney / kline | TQ get_market_data+refresh | **替代**(≤800日) | 前复权; >800 日或周月线仍用现有源 |
| 个股资金流 | capital_flow(east/sina) / tencent_fundflow | TQ Zjl(主力净额) + TQ4 自建分档 | **补齐** | Zjl 口径待时序定标; 自建分档(逐笔切档)新增暗盘维度; 东财口径保留对照 |
| 板块资金流 | board_fund_flow / ths_flow / market_flow | get_bkjy_value(**可接入**, 未实测) | **待定** | 实测 bkjy 后再判; 先保留 |
| 龙虎榜 | ftshare(龙虎榜/两融) | GP 系列龙虎榜表(**可接入**) | **待定→替代候选** | 专业数据包已解锁, 表名实测后可去 ftshare 依赖 |
| 涨停/情绪 | ths_hot / ths_web / discovery | SC03(涨停/炸板) + GP24/GP15 | **替代**(SC03 部分) + **补齐**(封单结构/首停时间) | SC03 本地直取, 免费/稳/实时落库已上线 |
| 两融 | ftshare margin | TDX 无对应 | **保留** | — |
| 分红/公司行动 | fundamentals / cninfo_irm | more_info 分红字段(雏形) | **保留**(TDX 仅辅助) | 权威性/完整性现有源更优 |
| 股东/股本 | fundamentals | more_info FreeLtgb/股东数雏形 | **保留** | 同上 |
| 基本面 PE/PB | fundamentals(east/tencent) | more_info DynaPE/StaticPE_TTM/MorePE/PB_MRQ | **替代候选**(轻量场景) | TQ3 已验证茅台 PE 18.22 精确; 深度财务仍用现有源 |
| 快讯/新闻/公告 | flash_news / news / cninfo_irm | TDX 无 | **保留** | — |
| 事件日历 | events | TDX 无 | **保留** | — |
| 北向资金 | northbound(hexin) | 客户端北向 .dat(**可接入**, TASK 已解部分) | **待定→补齐候选** | .dat 解析复用后可做备份通道 |
| 异动/发现 | em_anomaly / discovery | TDX 异动雷达(GUI, 不落盘) | **保留**(信号可用自建近似: 分时突破) | TQ5-C 分时突破覆盖部分场景 |
| 美股/全球 | yfinance / twelvedata / alphavantage | TDX 无(CN 市场专属) | **保留** | — |
| L2 暗盘/分档 | thsdk_l2(游客模式, 不稳) | .tck 自建分档(TQ4 已交付) | **替代**(已落地) | 稳定性/准确性双提升; thsdk 降为备份 |
| 十档盘口 | (SIDA 无) | .img 十档序列 | **补齐** | 新维度: 盘口意图/分时突破输入 |
| 涨停封单结构 | (SIDA 无) | GP15/GP24 | **补齐** | 新维度: 妖股基因分输入 |
| 交易通道数据 | (SIDA 无) | 券商账户 | **拿不到**(禁碰) | — |

## 汇总
- **可替代 4 类**: A股行情、≤800日K线、涨停情绪统计、L2暗盘分档(已落地)
- **补齐 5 维度**: 主力净额/撤单、封单结构+首停时间、十档盘口、暗盘分档、板块研值(待实测)
- **待实测定 3 项**: 龙虎榜 GP 表、板块 bkjy、北向 .dat 备份
- **保留 7 类**: 快讯新闻、事件、两融、分红股东权威源、美股全球、异动雷达、券商通道
- 风险提示: TQ 数据源单点在本机通达信客户端(客户端假死=源断), 降级链(fallback)必须保留现有源作为兜底 —— Engine 的 vendors={} 多源机制正好支持"tq 优先、现有源兜底"
