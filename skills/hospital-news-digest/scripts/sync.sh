#!/bin/sh
# 从远端同步本技能（供 SKILL.md 步骤 0 调用）。
#
# 为什么要有这个壳：技能可能装在 ~/.codebuddy/skills/ 下（软链或副本），
# 而同步脚本在仓库根的 scripts/ 里。这里把自身路径解析成真实路径，
# 再反推出仓库根，两种安装方式都能找对。
#
# 用法：
#   sh sync.sh              # 静默同步（6 小时内只访问一次远端）
#   sh sync.sh --check      # 看有没有更新
#   sh sync.sh --force      # 强制访问远端
#
# 失败一律 exit 0：技能同步不该阻断出报告。

# 本脚本位置：<仓库根>/skills/hospital-news-digest/scripts/sync.sh
# 往上三层才是仓库根（scripts → 技能目录 → skills → 仓库根）
SELF="$(readlink -f "$0" 2>/dev/null || echo "$0")"
REPO="$(cd "$(dirname "$SELF")/../../.." 2>/dev/null && pwd -P)" || exit 0

[ -n "$REPO" ] || exit 0
[ -f "$REPO/scripts/sync_skills.py" ] || exit 0
command -v python3 >/dev/null 2>&1 || exit 0

case "$*" in
  *--check*|*--status*) exec python3 "$REPO/scripts/sync_skills.py" "$@" ;;
  *)                    exec python3 "$REPO/scripts/sync_skills.py" --quiet "$@" ;;
esac
