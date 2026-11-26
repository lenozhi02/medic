---
name: hospital-news-digest
description: 按「日期范围 + 关键词」汇总医院官网新闻与医院公众号日报，交叉核对两个腾讯文档门户页，输出 Word（.docx）与 Markdown（.md）双格式报告。数据来自 bjbug 医疗舆情系统（www.bjbug.cn:5000）的「查询结果」与「公众号日报」两个页面。触发词：医疗新闻搜索、医院新闻搜索、医疗舆情汇总、汇总医疗资讯、按日期查新闻、官网新闻汇总、公众号日报汇总、医疗信息汇总报告、导出一段时间的医疗新闻、日期范围加关键词出报告。
---

# 医疗资讯汇总（官网新闻 + 公众号日报）

把「某段时间 + 某关键词」的医疗资讯拉齐、去重、核对、排版成一份可直接分发的 Word/Markdown 报告。

## 一、三个地址与它们的真实角色

| 用户看到的地址 | 角色 | 怎么用 |
|---|---|---|
| `https://docs.qq.com/page/DUVJqSUV0a2piQkhw` 医院官网新闻监测 | **展示层**（门户页） | 只用于交叉核对，不作统计口径 |
| `https://docs.qq.com/page/DUXJFWXRGUXdRYVNB` 医院公众号日报 | **展示层**（门户页） | 同上 |
| `www.bjbug.cn:5000` →「🔍 查询结果」`index.html` | **数据层** | `GET /api/v1/search/by-date` |
| `www.bjbug.cn:5000` →「📰 公众号日报」`hospital-wechat.html` | **数据层** | `GET /api/v1/hospital-wechat/results` |

> 两个门户页里内联了完整数据块（官网 `__NEWS_DATA__`、日报 `const DATA`），所以「交叉核对」是真比对，不是走过场。
> 但**统计口径只认接口**：门户是展示层，不能拿它当数据源做二次统计（项目铁律 6）。

### ⚠ 关键事实：官网的两条通道不是同一份数据

实测（2026-08-10~12、2026-11-20~26 两个区间复核）：

| | 门户 `__NEWS_DATA__` | 接口 `search/by-date` |
|---|---|---|
| 来源标记 | 来源通道=**官网直采** | channel=**baidu**（检索通道） |
| 内容性质 | 医院官网原文，权威 | 百度检索结果，含腾讯/网易等**二手转载** |
| 标题重合度 | 与接口**交集≈0** | 同上 |

所以本技能**不把它们合并计数**，而是分区呈现：

- `2.1 官网直采`（门户）— 权威，可直接采用
- `2.2 检索通道补充`（接口独有）— 标 ⚠，按铁律 2 需追溯白名单原文后才可采用

公众号侧两侧基本同源，差异只来自批次时点与去重。

## 二、环境要求

| 项 | 要求 |
|---|---|
| Python | 3.9+ |
| 第三方库 | `requests`（其余全是标准库） |
| 网络 | 能访问 `www.bjbug.cn:5000`；门户侧不联网，读本地 `cache/` |
| 安装 | 整个 `hospital-news-digest/` 目录放进项目的 `.codebuddy/skills/` 即可，无需构建 |

自检（不联网、不写文件）：

```bash
cd <项目根> && python3 .codebuddy/skills/hospital-news-digest/scripts/digest.py parse-portal \
  --html .codebuddy/skills/hospital-news-digest/cache/portal_official_20261126-0902.json \
  --type official --out /tmp/po.json
```

能输出条目数就说明脚本、缓存、路径三者都正常。

## 三、输入

| 参数 | 必填 | 说明 |
|---|---|---|
| `--start` | 是 | 起始日期，`20260810` 或 `2026-08-10` 均可 |
| `--end` | 是 | 截止日期，同上；闭区间 |
| `--keyword` | 否 | 关键词，多个用逗号分隔（**OR** 逻辑，分别请求后按 URL 去重合并）。不传=全量 |
| `--portal-official` | 否 | 官网门户快照。默认 `auto`＝自动取 `cache/` 最新；`none`＝跳过；也可传具体路径 |
| `--portal-wechat` | 否 | 公众号日报门户快照，取值同上 |
| `--outdir` | 否 | 输出目录，默认 `<当前工作目录>/output` |

## 四、执行流程

**步骤 1｜归一化输入**
日期统一成 `YYYY-MM-DD`，`start > end` 直接报错，不要猜。

**步骤 2｜门户快照（零登录，默认已就绪）**

先说结论：**读门户不需要任何人登录腾讯文档**，也不需要在浏览器里打开门户页。

| 通道 | 是否需要登录 | 实测 |
|---|---|---|
| 直接 HTTP 抓 `docs.qq.com/page/...` | 不可用 | 匿名返回 SPA 空壳，权限位 `isAuth:false` / `canRead:false`；`dop-api` 直接 401 |
| 助手调用 MCP `aicanvas_read_page_source` | **不需要** | 走平台侧连接器授权，一次绑定长期有效；实测直接返回完整源码（官网 v49 / 日报 v43） |

所以正确姿势是：**助手调 MCP 读源码 → 落盘到 `cache/` → 脚本读文件**。脚本本身不碰网络，也就不涉及 cookie。

刷新缓存（仅在需要比现有快照更新的数据时）：

1. 调 `aicanvas_read_page_source`，`end_line` 传 `-1`
   - 官网新闻：`file_id = QRjIEtkjbBHp`
   - 公众号日报：`file_id = QrEYtFQwQaSA`
2. 把返回的 JSON（含 `html` 字段）原样保存为
   `cache/portal_official_<YYYYmmdd-HHMM>.json` / `cache/portal_wechat_<YYYYmmdd-HHMM>.json`
3. 重跑脚本，`--portal-*` 保持默认 `auto` 即可命中最新一份

脚本内置两条保护：
- 快照读取日期 **早于** 查询截止日 → 控制台与文档都会标「快照可能已过期」，并说明差异属时点差
- 两侧都是 0 条 → 结论写「两侧均无数据」，不写「一致」

MCP 报 `not_authorized` / 401 时，让助手重新连接 `tdocs_app_tencent_docs` 连接器即可，不需要用户手动登录。

**步骤 3｜跑脚本**

最简形态（门户快照自动取 `cache/`，输出到 `./output`）：

```bash
cd <项目根>
python3 .codebuddy/skills/hospital-news-digest/scripts/digest.py run \
  --start 20260810 --end 20260812 --keyword "疫苗,卒中"
```

需要指定快照或输出位置时再显式传：

```bash
python3 .codebuddy/skills/hospital-news-digest/scripts/digest.py run \
  --start 20260810 --end 20260812 \
  --portal-official none --portal-wechat none \
  --outdir /tmp/digest_out
```

**步骤 4｜汇报**
读脚本输出的 JSON 摘要，把「官网几条 / 公众号几条 / 核对结论 / 有无登录态失效」四件事讲清楚，再 `present_files` 给出 md 与 docx。

## 五、目录与缓存

```
hospital-news-digest/
├── SKILL.md
├── scripts/digest.py          # 取数 + 核对 + 出文档，单文件无第三方依赖
└── cache/                     # 门户快照（MCP 读源码后落盘）
    ├── manifest.json          # 记录当前快照的 version / 读取时间
    ├── portal_official_<时间戳>.json
    └── portal_wechat_<时间戳>.json
```

`cache/` 已随技能带了一份可用快照（官网 v49、日报 v43，2026-11-26 读取），
开箱即可做交叉核对；要更新就按步骤 2 覆盖，脚本自动取最新。

## 六、单独使用的子命令

```bash
# 只取数
python3 digest.py collect --start 20260810 --end 20260812 --out data.json

# 只解析门户快照（先验证快照可用）
python3 digest.py parse-portal --html portal.json --type official --out po.json
python3 digest.py parse-portal --html portal.json --type wechat   --out pw.json
```

## 七、输出

| 文件 | 内容 |
|---|---|
| `医疗资讯汇总_<start>_<end>[_关键词].docx` | 完整报告：封面、概览统计、两类条目明细、交叉核对、批次状态、口径与质量提示 |
| 同名 `.md` | 同内容 Markdown 版，链接可直接复制 |
| 同名 `.json` | 机器可读摘要（条数、核对结果、批次状态），供下游或二次汇总 |

报告结构：
1. **总体概览** — 两类条数、机构分布、检索通道、来源域名、标签分布
2. **官网新闻明细** — 分 `2.1 官网直采` 与 `2.2 检索通道补充` 两块；每条含机构/时间/通道/域名/标签/AI 摘要/原文摘要/实体/原文链接
3. **公众号日报明细** — 每条：监测机构/公众号/批次/摘要/原文链接
4. **门户交叉核对** — 接口条数 vs 门户条数、差值、仅单边存在的标题清单
5. **检索状态与口径** — 批次登录态、时间窗口、关键词规则、数据质量提示（异常日期、疑似推广）

## 八、必须知道的坑

- **`channel` 传空串会返回 0 条**，必须传 `all`。这是接口行为，不是没数据。
- **公众号日报可能整批登录态失效**（微信读书 `ANONYMOUS`）。此时 0 条要写成「未完成检索」，不能写「今日无新闻」。脚本会在 5.1 用红字标出。
- **`publish_date` 有脏数据**（实测有 2020 年的日期挂在 2026 年文章上，也有空值）。脚本不改写，只单列到「5.3 数据质量提示」。
- **`channel` 字段是检索通道**（百度/公众号/官网），门户标注的「来源通道：官网直采」是落地方式，两者不能混为一谈。
- **门户页匿名访问只有壳**（`isAuth=false`、`canRead=false`，`dop-api` 401）。这是文档没开公开分享，不是 UA 或 cookie 问题——换 UA、换移动端、加 Referer 都试过，一律空壳。只能走 MCP 连接器。
- 公众号条目 `account` 可能为空，`desc` 是微信搜索结果摘要，可能夹带无关正文。
- **门户官网数据里混有非医院主体**（如「盛诺一家」这类出国看病中介），机构分布表会照实显示，不要当成医院。
- **门户版本不一定随时刷新**（实测 version 连续多次读取不变），核对出现数量差时先看快照时间。
- **带关键词时，门户侧是本地匹配**（标题／栏目／医院／公众号名／摘要），接口侧是服务端检索，两边字段与算法不同：接口能命中正文，门户只能命中上述字段。所以带关键词时「仅门户有」偏多属正常，不要当成数据错误。

## 九、红线

- 拿不到 URL 的条目不进正文清单，剔除数写进口径说明。
- 不润色标题、不改日期、不合并相似条目。
- 商业推广信号只**标注**不自动删（避免误伤真新闻），最终去留由人定。
- 核对未执行时，绝不写「与门户一致」。
- 失败要显式说明：哪一步、什么错、影响几条。
