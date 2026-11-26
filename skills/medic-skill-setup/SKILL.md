---
name: medic-skill-setup
description: 一句话把本项目（医疗信息监测）共享仓库里的技能装到本机——成员只需说「帮我安装」「安装医疗技能」「同步一下技能」「接入项目技能」，本技能自动完成 git clone、装依赖、建软链、跑自检、出结果，不需要成员懂 git 或命令行。装好后 hospital-news-digest 等技能会自动跟随仓库更新。触发词：帮我安装、安装技能、装一下技能、接入项目技能、同步技能、更新技能、安装医疗技能、医疗新闻技能装一下。
agent_created: true
allowed-tools: Bash, Read, Write
disable: false
---

# 一键接入项目技能

**成员的体验应该只有一句话：「帮我安装」。剩下的全由这个技能做完。**

本技能是自举的：它自己也住在同一个仓库里。
已经是项目技能配置成员时，这句话能直接命中；还没配的成员，把本文件（`SKILL.md`）内容发给他，
让他直接说「照这个装」，也能走完同样的流程。

---

## 一、要装的东西

| 项 | 值 |
|---|---|
| 仓库 | `https://github.com/lenozhi02/medic` |
| 技能 | `hospital-news-digest`（按日期范围 + 关键词汇总医疗资讯，出 docx/md） |
| 装到 | `~/.codebuddy/skills/hospital-news-digest`（软链，指向仓库里的实现） |
| 依赖 | `requests` |
| 自检 | `digest.py parse-portal` 输出 `OK official: 662 条` |

---

## 二、执行步骤（严格按序，一步一判）

### 步骤 1 · 找仓库：先找现成的，再决定克不克隆

按顺序找，**第一个命中的就用它**，不要重复克隆：

```bash
# 1) 只在 HOME 下找，别把当前目录的建议一起扫进来（/workspace 往往不是这个仓库）
for d in "$HOME/medic" "$HOME/workspace/medic" "$HOME/repos/medic"; do
  if [ -d "$d/.git" ] && git -C "$d" remote get-url origin 2>/dev/null | grep -q "lenozhi02/medic"; then
    echo "FOUND:$d"; break
  fi
done

# 2) 兜底：读已装技能的软链指向（若之前装过）
readlink -f "$HOME/.codebuddy/skills/hospital-news-digest" 2>/dev/null
```

> 注意：不要用 `[ -d ... ] && git ... && { ...; break; }` 这种链式写法。
> 某一路判断失败时，`break` 可能作用在外层循环上，导致整轮探测直接中断。
> 用 `if` 块，判断与动作分开。

- 命中 → 记为 `REPO`，跳到步骤 2
- 没命中 → 克隆到 `$HOME/medic`。**网络不稳，必须重试**：

```bash
for i in 1 2 3 4 5; do
  GIT_TERMINAL_PROMPT=0 git clone --quiet https://github.com/lenozhi02/medic.git "$HOME/medic" && break
  rm -rf "$HOME/medic"; sleep 2
done
[ -d "$HOME/medic/.git" ] || echo "CLONE_FAILED"
```

> 实测：`github.com` 的首连经常卡住 90s 以上，重试 2~5 次几乎都能通。
> 如果 5 次全失败，走步骤 6 的离线方案，**不要**反复硬试。

### 步骤 2 · 装依赖

```bash
python3 -c "import requests" 2>/dev/null || sudo pip3 install -q requests
```

`requests` 装不上但仓库里已有本机可用版本时不影响主流程；仅在真报 `ImportError` 时才停下来告知成员。

### 步骤 3 · 建软链

```bash
python3 "$REPO/scripts/install_skills.py"
```

它会：
- 在 `~/.codebuddy/skills/<技能名>` 建软链 → 仓库内实现
- 校验 `SKILL.md` 的 frontmatter
- 打印后续三步提示

Windows 或无建链权限时自动退化为复制模式，此时**每次更新都要重跑本脚本**（脚本输出里会写）。

### 步骤 4 · 自检（不联网、不写报告）

```bash
python3 "$HOME/.codebuddy/skills/hospital-news-digest/scripts/digest.py" parse-portal \
  --html "$HOME/.codebuddy/skills/hospital-news-digest/cache/portal_official_20261126-0902.json" \
  --type official --out /tmp/_po_check.json
```

期望输出 `OK official: 662 条`。

> 这个数字来自仓库内 `cache/` 里的**静态快照**，所以它是固定的。
> 如果输出变了，说明快照文件被换过，属正常维护，不是错误——只要不是报错就算通过。

### 步骤 5 · 同步机制自检

```bash
sh "$HOME/.codebuddy/skills/hospital-news-digest/scripts/sync.sh" --check
```

- 输出 `· 已是最新` 或 `有 N 个新提交待拉取` → 通过
- 输出 `! 拉取远端失败` → **不算失败**，网络抖动而已，技能照用；下次调用会自动再试

### 步骤 6 · 离线兜底（仅当克隆 5 次全失败）

让成员从**项目网盘 → `技能包/`** 下载 `hospital-news-digest-skill-v1.2.zip`，解压后：

```bash
mkdir -p "$HOME/.codebuddy/skills"
unzip -q hospital-news-digest-skill-v1.2.zip -d "$HOME/.codebuddy/skills/hospital-news-digest"
```

离线包是**复制模式**：不自动更新，要更新就再下一次包。要在回复里明确告诉成员这一点。

---

## 三、装完要对成员说什么

用**成员能看懂的话**回，不要甩命令。模板：

```
装好了。

- 技能：医疗新闻搜索汇总（hospital-news-digest）
- 装到：~/.codebuddy/skills/hospital-news-digest（软链，跟随仓库更新）
- 自检：通过（门户快照 662 条解析正常）

以后直接用就行，比如：
「医疗新闻搜索，2026年8月10日到12日，关键词疫苗」

它会出一份 Word + Markdown 报告，落在当前目录的 output/ 里。
以后我在仓库里更新技能，你这边不用重装，调用时会自动同步（6 小时内最多查一次远端）。

⚠️ 需要重开一次对话，技能才会出现在「已添加技能」面板里。
```

然后**确认一句**：「重开对话后，技能列表里能看到『医疗新闻搜索汇总』吗？」
看不到就走下面的排查。

---

## 四、常见问题（按这个顺序查）

| 现象 | 原因 | 处理 |
|---|---|---|
| 面板里没有新技能 | 面板只在会话启动时读 `~/.codebuddy/skills` | 重开对话；仍未出现就 `ls ~/.codebuddy/skills/hospital-news-digest/SKILL.md` 确认文件在 |
| `git clone` 一直超时 | 连 github 首连卡住 | 重试；仍失败走步骤 6 离线包 |
| `python3: command not found` | 环境无 python3 | 直接告知成员，**不要**改成别的语言硬写 |
| `ModuleNotFoundError: requests` | 依赖没装上 | `sudo pip3 install requests`，再跑步骤 4 |
| `parse-portal` 报快照文件不存在 | `cache/` 没克隆下来 | 确认 `REPO/skills/hospital-news-digest/cache/` 有 3 个文件；缺了就重新克隆 |
| 软链建完访问报 `No such file` | 用了绝对路径软链，换机器就断 | 重跑 `install_skills.py`（它建的是相对软链） |
| 「已添加技能」里能看到但触发不了 | 描述里的触发词没匹配上 | 直接说技能全名「医疗新闻搜索汇总」，或显式说「用 hospital-news-digest」 |

---

## 五、红线

- **不许**替成员改项目配置、改定时任务、动别人的目录。
- **不许**在成员不知情时把仓库克隆到奇怪的位置——固定 `$HOME/medic`，除非已经存在别的副本。
- **不许**把「克隆失败」说成「装好了」。失败就明说哪一步失败、影响什么、要不要人工。
- **不许**在回复里贴出仓库的写凭据。读仓库不需要任何凭据。
- 复制模式一定要**主动告知**「不会自动更新」，别让成员以为和软链模式一样。

---

## 六、版本

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-11-26 | 首版：一句话接入，含克隆重试、软链、自检、离线兜底与排查表 |
