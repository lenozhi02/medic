# 医疗信息监测 · 共享仓库

本仓库用于**跨成员共享**本项目的技能与配置。各成员在 WorkBuddy 中跑在独立容器里，
`/workspace` 与 `~/.codebuddy/skills` 都是各自的私有盘，互相看不见。
项目内真正共享的是：**项目留言、项目网盘、项目技能配置**，以及本仓库。

## 目录结构

```
medic/
├── README.md
├── .gitignore
├── scripts/
│   ├── install_skills.py            # 接入：建软链到 ~/.codebuddy/skills/
│   └── sync_skills.py               # 同步：pull + 刷新，6 小时节流
└── skills/
    └── hospital-news-digest/        # 医疗新闻搜索汇总（按日期范围+关键词出报告）
        ├── SKILL.md
        ├── workbuddy.json
        ├── scripts/digest.py        # 单文件，仅依赖 requests
        ├── scripts/sync.sh          # 同步壳：解析安装路径后调 sync_skills.py
        └── cache/                   # 门户快照（官网 v49 / 日报 v43，2026-11-26）
```

## 一、首次接入（每个成员做一次）

```bash
git clone https://github.com/lenozhi02/medic.git
cd medic
python3 scripts/install_skills.py          # 建软链到 ~/.codebuddy/skills/
pip install requests
```

> 未登录 GitHub 时可先把仓库设为公开再 clone，凭据只会影响 `push`，不影响 `clone`。

然后**重开一次对话**，技能列表里出现「医疗新闻搜索汇总」即为成功。

`install_skills.py` 做的是建软链：`~/.codebuddy/skills/<技能名>` → `仓库/skills/<技能名>`。
这样 `git pull` 之后不用重装，技能自动是新的。Windows 无权限建软链时，
脚本会退化为复制模式（每次更新后需重跑一次）。

## 二、日常更新

### 成员侧：基本不用做什么

技能调用时会**自动同步**：`SKILL.md` 的「步骤 0」会跑一次

```bash
sh ~/.codebuddy/skills/hospital-news-digest/scripts/sync.sh
```

- 有更新就 `git pull`，软链模式下技能内容立刻变新版
- **6 小时内只真正访问一次远端**，之后秒回，不拖慢出报告
- 无更新、无网络时静默退出，不阻断出报告

想手动控制：

```bash
cd medic
python3 scripts/sync_skills.py            # 立即同步
python3 scripts/sync_skills.py --check    # 只看有没有更新
python3 scripts/sync_skills.py --force    # 忽略 6 小时节流
python3 scripts/sync_skills.py --status   # 看仓库/安装/定时状态
```

### 为什么不装定时任务

本项目的运行环境（容器沙箱）里**没有 crontab**，也没有 cron 守护进程，
所以「每天几点自动 pull」这种操作系统级定时装不上。`--install-cron` 保留着，
在装了 cron 的机器上可用；沙箱里它会给出提示并退出，不会报死错。

替代方案就是上面那个：**同步挂在技能调用路径上**。只要有人用技能，就一定先同步。
没人用的时候也不需要同步——技能闲着的时候不是新版也没有影响。

### 维护者侧：改完就推

```bash
cd medic && git add -A && git commit -m "更新 hospital-news-digest：xxx" && git push
```

## 三、推送权限（写代码的人需要）

本仓库所有权的写入需要 GitHub 凭据，读（clone / pull）不需要。任选一种：

| 方式 | 命令 | 适用 |
|---|---|---|
| `gh` 登录（推荐） | `gh auth login` 后 `gh auth setup-git` | 装了 GitHub CLI |
| 个人访问令牌 | 把令牌写进 `~/.git-credentials`，或用 `git remote set-url origin https://<user>:<token>@github.com/lenozhi02/medic.git` | 通用 |
| SSH | `git remote set-url origin git@github.com:lenozhi02/medic.git`，并配置好公钥 | 已有 SSH key |

> 令牌不要提交进仓库。`.gitignore` 已忽略 `.env`、`*.local.json`、`mail_conf.json`。

## 四、使用 hospital-news-digest

自然语言：「医疗新闻搜索，2026年8月10日到12日，关键词疫苗」

命令行：

```bash
python3 ~/.codebuddy/skills/hospital-news-digest/scripts/digest.py run \
  --start 20260810 --end 20260812 --keyword "疫苗"
```

产物落在当前目录 `output/`：docx + md + json 三件套。

### 自检（不联网、不写文件）

```bash
python3 ~/.codebuddy/skills/hospital-news-digest/scripts/digest.py parse-portal \
  --html ~/.codebuddy/skills/hospital-news-digest/cache/portal_official_20261126-0902.json \
  --type official --out /tmp/po.json
```

输出 `OK official: 662 条` 即正常。

## 五、两条限制（务必知道）

| 项 | 说明 |
|---|---|
| 取数通道 | 需能访问 `http://www.bjbug.cn:5000`，与账号无关 |
| 门户交叉核对 | 仓库内 `cache/` 是**静态快照**（官网 v49 / 日报 v43，读取于 2026-11-26）。能核对，但数据停在该时点。要拿最新门户数据，需在本机绑定腾讯文档连接器，由助手调用 `read_page_source` 重新读取并覆盖 `cache/`，否则查询晚于该时点的区间时报告会标「快照可能已过期」 |

## 六、这个仓库不做什么

- **不放门户页、不放数据**：门户地址与网盘目录见项目网盘根目录的 `AGENTS.md` §10 固定资产表。
- **不放脚本的私有配置**：`sites.py`、`mail_conf.json`、Supabase 凭据等按项目规则留在原有位置，不入库。
- **不放产出物**：`output/` 已在 `.gitignore` 中忽略。日报/周报归档走项目网盘。

## 七、相关入口

- 🚪 医疗信息监测 · 总入口：https://docs.qq.com/page/DUWJnbnViTm5JUkpC
- 📦 技能包（网盘）：项目网盘 → `技能包/hospital-news-digest-skill-v1.2.zip`
- 📘 项目准则：项目网盘根目录 `AGENTS.md`

## 版本

| 日期 | 变更 |
|---|---|
| 2026-11-26 | 仓库建立；纳入 hospital-news-digest v1.1 |
| 2026-11-26 | v1.2：加 `sync_skills.py`（6 小时节流）与 `scripts/sync.sh`，同步挂到技能调用路径 |
