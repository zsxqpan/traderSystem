# 雪球 Playwright 采集 · 设计文档

> 日期：2026-08-25 ｜ 分支：dev/2026-08-25
> 方法：grilling（四决策对齐）+ brainstorming（architectural 路径）
> 前置：雪球站内被阿里云 WAF 硬挡（requests 系抓不了正文，2026-08-24 实测）；
>       搜索引擎只能拿标题+摘要（xueqiu_search 已落地）
> 决策（用户确认）：对话按需抓取+入库 / 不登录抓公开页 / 大V 主页+文章正文 / 本机模块
> 代码实现留待本 spec 审阅通过后实施

---

## 1. 背景与目标

### 1.1 背景
- 雪球站内 API 与页面正文被阿里云 WAF 拦截（JS 挑战，requests 无法抓取正文）；
- 搜索引擎（必应 site:xueqiu.com / 搜狗 / 360）能拿到**标题 + 摘要片段 + URL**，但拿不到**完整文章正文 / 大V 完整观点**；
- big-v-monitor（大V 监控：最近观点/历史观点/胜率/风格/擅长方向）需要完整内容支撑。

### 1.2 目标
1. 新增 `invest/data/xueqiu_fetch.py`：**Playwright 真实浏览器**抓雪球大V 用户主页动态 + 文章正文，解析入库；
2. 对话按需触发：big-v-monitor 先查本地表（big_v_profile/big_v_opinion）→ 表无/旧 → Playwright 抓取 → 入库复用；
3. 不登录先抓公开页（无头浏览器 + 反检测配置过 WAF）；
4. 抓取结果幂等（按文章 URL 去重 upsert）。

### 1.3 非目标（本阶段不做）
- 不做登录态抓取（胜率/粉丝数等需登录字段，二期）；
- 不做社区热帖/讨论页抓取（d28 升级，二期）；
- 不做定时批量采集固定大V 名单（二期）。

---

## 2. 架构设计

### 2.1 模块：`invest/data/xueqiu_fetch.py`

```
xueqiu_fetch.py
├── fetch_user_statuses(user_url_or_id, limit=10) -> list[dict]
│     # 大V 用户主页动态列表（文章标题/时间/URL/摘要）
├── fetch_article(url) -> dict | None
│     # 单篇文章正文（标题/发布时间/正文/点赞评论数）
├── _browser_context()   # Playwright chromium 上下文（反检测配置）
└── _parse_user_page(html) / _parse_article_page(html)
```

### 2.2 反检测配置（关键：过阿里云 WAF）
- 真实 UA（Chrome/Edge）+ Accept-Language + viewport（1280x800）；
- **禁 headless 可探测标志**：`--disable-blink-features=AutomationControlled`、`navigator.webdriver` 置 undefined；
- 首次访问 `https://xueqiu.com/` 拿 cookie（acw_tc 等）再抓目标页（模拟真实浏览器路径）；
- 页面加载后等待 WAF 挑战自动通过（`page.wait_for_selector` 目标内容，超时兜底）；
- 抓取间隔（每次启动浏览器、用完关闭；串行单实例）。

### 2.3 工具接入（对话按需）
- `invest/agent/tools.py` 新增：
  - `xueqiu_fetch_article(url)` → 抓单篇文章正文 → 写 big_v_opinion（按 url 去重）→ 返回内容
  - `xueqiu_fetch_user(user)` → 抓用户主页动态 → 返回列表 + 更新 big_v_profile
- 注册 TOOL_SCHEMAS + `_IMPLEMENTATIONS`；CHAT_SYSTEM 规则 1 补"雪球正文用 xueqiu_fetch_article（Playwright）"；
- **不设新鲜度守卫**（联网抓取，实时即最新）。

### 2.4 数据流
```
用户问"某大V 最近观点"
  → big-v-monitor skill
  → query_big_v（先查表：big_v_opinion 最近记录）
  → 表无/记录旧（>3 天）→ xueqiu_fetch_user(大V) 抓主页 → 对每篇新文章 xueqiu_fetch_article 抓正文
  → 解析观点（标题/时间/正文前 200 字/URL）→ upsert big_v_opinion
  → 回答（画像 + 最近观点 + 一致性）
```

### 2.5 资源与容错
- 每次抓取启动独立 browser 实例，用完关闭（`context.close()`），不常驻；
- 单篇超时 30s；失败静默返回 `{"error": ...}`（不阻断对话）；
- 串行执行（无并发，避免雪球限流）；
- 抓取结果缓存：big_v_opinion 表已有记录且 <3 天 → 直接读表不重抓。

---

## 3. 数据库（沿用现有表，无新表）

- `big_v_profile`：画像（style/strengths/win_rate 等）——fetch_user 更新；
- `big_v_opinion`：观点（opinion_date/symbol/view/bias/url）——fetch_article 写入，**按 url 去重**（查询已存在则不重复插入）。

---

## 4. 测试

1. 单测（全 mock，不连网）：
   - `_parse_article_page(html)`：mock WAF 已过后的 HTML → 断言标题/正文/时间解析；
   - `xueqiu_fetch_article`：mock playwright page 内容 → 断言入库 + 去重（同 url 二次调用不重复）；
   - `fetch_user_statuses`：mock 主页 HTML → 断言动态列表解析。
2. 集成（手动/可选）：真实抓一篇文章验证 WAF 可过（chromium 装好后）。
3. `ruff check` 0 错误。

---

## 5. 实施步骤

1. 装 playwright + chromium（已完成库安装，chromium 下载中）；
2. **可行性验证**：写最小脚本无头抓 xueqiu.com 文章页 → 确认 WAF 可过 → 若被挡，调整反检测（加 stealth 参数/非无头/换 UA）直到可过；
3. 实现 `invest/data/xueqiu_fetch.py`（抓取 + 解析 + 反检测）；
4. tools.py 注册两个工具 + CHAT_SYSTEM 规则；
5. 测试 + ruff。

---

## 6. 二期规划

- 登录态（cookie）抓取需登录字段（胜率/粉丝数/关注列表）；
- 社区热帖/讨论页抓取（d28 社区热议升级，替代搜索摘要）；
- 定时批量采集固定大V 名单（关注列表监控）；
- 抓取频率/限流自适应（失败退避）。

---

## 7. 待确认项

1. 抓取文章后**观点入库范围**：全篇入库还是前 N 字摘要 + URL？（推荐：全篇前 500 字 + URL，省 token 且可回溯）
2. 触发入口：仅 big-v-monitor skill 用，还是也暴露给 opinion-analysis？（推荐先 big-v-monitor）
3. chromium 装好后先做可行性验证再进入正式实现（若 WAF 对无头仍挡，需加 stealth 插件或非无头模式，届时再对齐）。
