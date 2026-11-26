#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把本仓库 skills/ 下的技能接入本机 WorkBuddy。

做两件事：
  1. 在 ~/.codebuddy/skills/<技能名> 建软链指向 仓库/skills/<技能名>
  2. 校验能否被加载（检查 SKILL.md 存在且 frontmatter 正常）

建软链的好处：git pull 之后技能自动是新的，不用重装。
Windows 无建链权限时自动退化为复制，并在输出里提示「更新后需重跑本脚本」。

用法：
    python3 scripts/install_skills.py            # 安装/更新
    python3 scripts/install_skills.py --copy     # 强制复制模式
    python3 scripts/install_skills.py --dry-run  # 只看会做什么
    python3 scripts/install_skills.py --uninstall  # 移除本仓库安装的技能
"""

import argparse
import os
import shutil
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "skills")
DEST = os.path.join(os.path.expanduser("~"), ".codebuddy", "skills")
MARK = ".installed-from-medic"          # 标记文件，用于安全卸载


def skills_in_repo():
    if not os.path.isdir(SRC):
        return []
    return sorted(d for d in os.listdir(SRC)
                  if os.path.isfile(os.path.join(SRC, d, "SKILL.md")))


def check_frontmatter(path):
    """SKILL.md 必须以 --- 开头的 frontmatter，且含 name 字段。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read(2000)
    except OSError as e:
        return False, f"读取失败：{e}"
    if not text.startswith("---"):
        return False, "SKILL.md 缺少 frontmatter"
    if "name:" not in text.split("---")[1]:
        return False, "frontmatter 缺少 name 字段"
    return True, "ok"


def link_or_copy(src, dst, force_copy, dry):
    """先清后建。返回 (方式, 说明)。"""
    if os.path.islink(dst) or os.path.exists(dst):
        if dry:
            return "skip-exists", f"{dst} 已存在，将被替换"
        if os.path.islink(dst):
            os.unlink(dst)
        else:
            shutil.rmtree(dst)
    elif dry:
        return "dry", "将创建"

    if not force_copy:
        try:
            os.symlink(os.path.relpath(src, os.path.dirname(dst)), dst)
            return "link", "已建软链"
        except (OSError, NotImplementedError) as e:      # Windows 无权限等
            last_err = e
        else:
            last_err = None
    else:
        last_err = None

    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    note = "已复制"
    if not force_copy and last_err is not None:
        note += f"（软链失败：{last_err}；复制模式下 git pull 后需重跑本脚本）"
    return "copy", note


def write_marker(dirpath):
    """标记写在链接指向的真实目录里，且已在 .gitignore 中忽略。

    注意：若 dst 是软链，写入会落到仓库内的 skills/<n>/ 下，
    所以 .gitignore 必须包含 .installed-from-medic，否则会被 git 跟踪。
    """
    real = os.path.realpath(dirpath)
    try:
        with open(os.path.join(real, MARK), "w", encoding="utf-8") as f:
            f.write(REPO + "\n")
    except OSError:
        pass


def main():
    ap = argparse.ArgumentParser(description="把仓库内技能接入本机 WorkBuddy")
    ap.add_argument("--copy", action="store_true", help="强制复制，不建软链")
    ap.add_argument("--dry-run", action="store_true", help="只预览，不落盘")
    ap.add_argument("--uninstall", action="store_true", help="移除本仓库安装的技能")
    args = ap.parse_args()

    names = skills_in_repo()
    if not names:
        print(f"× 仓库内没找到技能（{SRC}）")
        return 1

    print(f"仓库：{REPO}")
    print(f"目标：{DEST}")
    print(f"技能：{', '.join(names)}\n")

    if args.uninstall:
        rc = 0
        for n in names:
            dst = os.path.join(DEST, n)
            if not os.path.exists(dst) and not os.path.islink(dst):
                print(f"  - {n}：未安装，跳过")
                continue
            if not os.path.exists(os.path.join(dst, MARK)):
                print(f"  ! {n}：不是本仓库安装的，拒绝删除（请手工确认）")
                rc = 1
                continue
            if args.dry_run:
                print(f"  - {n}：将删除 {dst}")
                continue
            if os.path.islink(dst):
                os.unlink(dst)
            else:
                shutil.rmtree(dst)
            print(f"  ✓ {n}：已移除")
        return rc

    os.makedirs(DEST, exist_ok=True)
    failed = 0
    for n in names:
        src = os.path.join(SRC, n)
        dst = os.path.join(DEST, n)

        ok, why = check_frontmatter(os.path.join(src, "SKILL.md"))
        if not ok:
            print(f"  ✗ {n}：{why}")
            failed += 1
            continue

        mode, note = link_or_copy(src, dst, args.copy, args.dry_run)
        if mode not in ("dry", "skip-exists"):
            write_marker(dst)
        print(f"  {'·' if mode in ('dry', 'skip-exists') else '✓'} {n}：{note}")

    if args.dry_run:
        print("\n（dry-run，未改动任何文件）")
        return 0

    if failed:
        print(f"\n× {failed} 个技能未安装成功")
        return 1

    print("\n下一步：")
    print("  1. pip install requests")
    print("  2. 重开一次对话，技能列表里应出现「医疗新闻搜索汇总」")
    print("  3. 自检：")
    print(f"     python3 {os.path.join(DEST, names[0], 'scripts/digest.py')} parse-portal \\")
    print(f"       --html {os.path.join(DEST, names[0], 'cache', 'portal_official_20261126-0902.json')} \\")
    print("       --type official --out /tmp/po.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
