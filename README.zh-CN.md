<div align="center">

# SIDA · 数智分析

**开源 A 股 AI 投研终端** — 行情数据 → 多智能体 AI 分析 → 基础模型预测 → 自我验证闭环 → 微信推送，一个自托管系统全打通。

[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)
![Version](https://img.shields.io/badge/version-v0.4.38-green)
[![Docker](https://img.shields.io/badge/Docker-ghcr.io-blue?logo=docker)](https://github.com/xiaoze-hub/Stock-Intelligent-Data-Analytics/pkgs/container/stock-intelligent-data-analytics)

*语言：[English](README.md) · [中文](README.zh-CN.md)*

![SIDA 演示](docs/screenshots/demo.gif)

</div>

---

## 为什么是 SIDA？

大多数 A 股工具只给你**数据**。SIDA 打通闭环：**读市场、用 AI 分析师团队研判、用时间序列基础模型预测、到期自动验证自己的预测、在微信上跟你对话** — 每一步可追溯，每次调用可审计。

> **诚实的定位：SIDA 是一个精心工程的"缝合怪"。** 不重复造轮子，把市面上最强的开源量化项目 — TradingAgents、Kronos、Chronos-Bolt、XGBoost、TA-Lib、TimescaleDB — 缝合成一条真正闭环的流水线。每个零件都久经考验；**整合本身就是产品**。

- 🔍 **主力意图分析** — 逐笔委托流：吸筹/派发识别、拆单伪装识别、支撑压力博弈，带物理守卫（数据不合理直接拒判，不硬猜）
- 🔮 **基础模型集成预测 + 验证闭环** — Kronos（AAAI 2026）+ Chronos-Bolt + XGBoost 加权投票，**权重按历史命中率动态调整**，每条预测到期自动对照真实涨跌（命中/未命中）
- 🤖 **多智能体 AI 分析，基于 [TradingAgents](https://github.com/TauricResearch/TradingAgents)** — 一队专职 LLM 分析师（研究员、多空辩论、交易员）对任意个股跑完整研判流程
- 🏆 **事件驱动机会发现** — 7 类策略信号、三引擎共振选股（问小达 + 问财 + 策略库交叉确认）、异动池、题材启动探测 — 在题材起飞**之前**发现它
- 📱 **微信双向对话** — iLink 协议绑定个人微信，手机上随时问股票、收推送报告；对话支持图片/文件/链接多模态理解
- 📊 **自动报告** — 盘前 8:30 / 盘后 15:30 定时生成，真实数据 + AI 点评

## 🧩 开源技术整合

SIDA 不重复造轮子 — 把经过验证的开源项目整合成一条流水线（全部真实接入业务代码，无一装饰）：

| 项目 | SIDA 用它做什么 |
|---|---|
| [TradingAgents](https://github.com/TauricResearch/TradingAgents) | AI 智能体工作区背后的多智能体 LLM 分析师框架 |
| [Kronos](https://github.com/shiyu-coder/Kronos)（AAAI 2026） | 首个开源 K 线基础模型 — 负责集成预测中的 K 线形态预测 |
| [Chronos-Bolt](https://github.com/amazon-science/chronos-forecasting) | 亚马逊时序基础模型，集成预测第二模型 |
| [XGBoost](https://github.com/dmlc/xgboost) · [scikit-learn](https://github.com/scikit-learn/scikit-learn) | 梯度提升 + 特征工程；集成权重按命中率动态调优 |
| [TA-Lib](https://github.com/ta-lib/ta-lib-python) | 久经考验的技术指标库 |
| [FastAPI](https://github.com/fastapi/fastapi) + SQLAlchemy 2.0 | PostgreSQL 之上的异步 API 层 |
| [TimescaleDB](https://github.com/timescale/timescaledb) | 时序超表存储多源 K 线，范围查询毫秒级 |
| [Redis](https://github.com/redis/redis) | 两级业务缓存 + 调度器选主（多 worker 部署下定时任务不双跑） |
| [Lightweight Charts™](https://github.com/tradingview/lightweight-charts) | 浏览器里 TradingView 级别的 K 线渲染 |
| React 18 + [Vite](https://github.com/vitejs/vite) + [Tailwind CSS](https://github.com/tailwindlabs/tailwindcss) | 前端工具链 |
| [Prometheus](https://github.com/prometheus/prometheus) · [Grafana](https://github.com/grafana/grafana) · [Loki](https://github.com/grafana/loki) | 指标 / 大盘 / 日志聚合 — `docker-compose.infra.yml` 一条命令拉起 |
| [Playwright](https://github.com/microsoft/playwright) | 无干净 API 场景下的无头浏览器采集 |
| [akshare](https://github.com/akfamily/akshare) | 开源中国行情数据接口库 |

> 一条 `docker run` 启动应用；可观测栈为可选基础设施。

## 界面截图

||||
|:---:|:---:|:---:|
| ![首页](docs/screenshots/home.png) | ![K线主力意图](docs/screenshots/kline-mainintent.png) | ![预测](docs/screenshots/forecast.png) |
| ![机会](docs/screenshots/opportunities.png) | ![AI对话](docs/screenshots/chat.png) | ![模拟盘](docs/screenshots/portfolio.png) |

*仪表盘 · K线+主力意图 · 预测与验证 · 机会发现 · AI 对话 · 模拟盘*



## 快速开始

### Docker(推荐)

```bash
# GitHub 源(全球)或阿里云 ACR(国内加速)
docker pull ghcr.io/xiaoze-hub/stock-intelligent-data-analytics:latest
# 或: docker pull crpi-mte80ai8o78b1429.cn-shanghai.personal.cr.aliyuncs.com/xiaozexwz/xzxwz:v0.4.38

docker run -d --name sida -p 8000:8000 --restart unless-stopped \
  -v sida_data:/app/data \
  -e AUTH_USERNAME=admin \
  -e AUTH_PASSWORD=your_password \
  -e TZ=Asia/Shanghai \
  ghcr.io/xiaoze-hub/stock-intelligent-data-analytics:latest
```

打开 http://localhost:8000 即可。可选可观测栈：`docker-compose.infra.yml`（Prometheus + Grafana + Loki）。

### 开发环境

```bash
# 后端
pip install -r requirements.txt
python server.py

# 前端
cd frontend && pnpm install && pnpm dev
```

## 技术架构

```
┌─────────────┐   ┌──────────────┐   ┌───────────────┐   ┌────────────┐
│  行情数据    │ → │  AI 分析     │ → │    预测        │ → │   报告      │
│ 腾讯/东财/   │   │ 主力意图      │   │ Kronos+Chronos│   │ 盘前/盘后   │
│ 同花顺/竞价/  │   │ 技术面       │   │ +XGB 集成     │   │ 自动生成    │
│ TDX/问财     │   │ TradingAgents│   │ + AI 裁判     │   │ + 到期验证   │
└─────────────┘   └──────────────┘   └───────────────┘   └────────────┘
      │                  │                   │                 │
      └──── AI 助手 (数智分析BOT) ←─────────┘                  │
                  │ 图片/文件/链接多模态                        ▼
                  ▼                               ┌────────────────────┐
          ┌─────────────┐                         │ PostgreSQL+TSDB ·  │
          │  微信推送     │                         │ Redis · Prometheus │
          │  iLink P2P  │                          │ Grafana · Loki     │
          └─────────────┘                         └────────────────────┘
```

## 功能总览

| 模块 | 能力 |
|---|---|
| **仪表盘** | 实时指数、市场宽度、热门板块、情绪指标 |
| **个股详情** | K线、逐笔主力意图、技术面、事件、题材、股东 |
| **预测** | 4 模型集成 + AI 裁判，到期自动验证的命中历史 |
| **机会发现** | 事件驱动候选、三引擎共振筛选、异动池 |
| **模拟盘** | 影子账户、交割单解析、行为画像 |
| **报告中心** | 盘前/盘后自动报告、PDF 导出 |
| **价格预警** | 价格/条件预警推送到手机 |
| **AI 智能体** | TradingAgents 多智能体研判，完整推理轨迹 |

## 多用户 & AI 配置

- **账号隔离**：持仓、自选、通知渠道、微信绑定全部按用户隔离
- **统一 LLM 配置中心**：多供应商模型池、场景绑定（对话/报告/裁判/视觉…）、自带 Key 即可用
- **通知渠道**：微信（iLink）/ 企业微信 / PushPlus / Server酱 / 邮件

## 数据源

腾讯 / 东财 / 同花顺 / 新浪 / 通达信（问小达）/ 巨潮（互动易）— 行情、K线、分时、资金流、逐笔成交、竞价、涨停池、热门板块、龙虎榜、两融等。免费数据源，无需付费 Key。

- 每笔行情自带命中来源 `source`（如 `tencent`），前端按此标注数据出处，不猜测。
- 通达信TQ客户端网关自 v0.4.38 起**默认关闭**（需本机客户端＋网关）。开启需 `PANWATCH_ENABLE_TQ=1` 并在数据源页手动启用；关闭后行情/K线自动走腾讯/东财降级链。

## 免责声明

本项目仅用于技术研究与学习。所有 AI 生成的分析、预测和报告仅供参考，**不构成任何投资建议**。市场有风险，投资需谨慎。

---

## 赞助 Sponsor

如果 SIDA 对你有帮助，请作者喝杯咖啡 ☕ — 你的支持让项目持续下去！

| 方式 | 入口 |
|:---:|:---:|
| **微信赞赏** | <img src="./assets/sponsor-wechat.png" width="200" alt="WeChat reward QR" /> |

> 觉得不错的话，点一下右上角 ⭐ **Star** 支持一下吧。
