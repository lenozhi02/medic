#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从远端拉取仓库更新，并让本机技能跟上。

技能装法分两种（见 install_skills.py）：
  - 软链模式：~/.codebuddy/skills/<n> 指向 仓库/skills/<n>
              → git pull 之后技能自动是新的，本脚本只需要 pull
  - 复制模式：是独立副本
              → pull 之后还要把仓库内容重新拷过去

用法：
    python3 scripts/sync_skills.py                    # 拉取 + 校验
    python3 scripts/sync_skills.py --check            # 只看有没有更新，不动手
    python3 scripts/sync_skills.py --quiet            # 供会话启动钩子调用，无事则静默
    python3 scripts/sync_skills.py --install-cron "06:30"   # 装每天定时同步
    python3 scripts/sync_skills.py --remove-cron
    python3 scripts/sync_skills.py --status           # 看当前落后几个提交

退出码：0 成功（含「已是最新」）/ 1 出错（网络、冲突、校验失败）
"""

import argparse
import os
import shutil
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "skills")
DEST = os.path.join(os.path.expanduser("~"), ".codebuddy", "skills")
LOG = os.path.join(DEST, ".sync.log")
MARK = ".installed-from-medic"
CRON_TAG = "medic-skill-sync"
STAMP = ".last_sync"          # 节流时间戳
THROTTLE = 6 * 3600           # 6 小时内不重复访问网络
FETCH_TRIES = 3               # 网络抖动时重试次数（第一次常常只是卡在连 TCP）
FETCH_BUDGET = 40             # 首次尝试的超时；后两次用 FETCH_BUDGET * 1.5


# ---------- 基础工具 ----------

def run(args, cwd=REPO, timeout=90):
    """跑一条命令，返回 (returncode, stdout+stderr)。永不抛异常。"""
    try:
        p = subprocess.run(args, cwd=cwd, timeout=timeout,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True)
        return p.returncode, (p.stdout or "").strip()
    except subprocess.TimeoutExpired:
        return 124, f"超时（{timeout}s）：{' '.join(args)}"
    except FileNotFoundError:
        return 127, f"命令不存在：{args[0]}"
    except OSError as e:
        return 1, f"执行失败：{e}"


def log(msg, quiet=False):
    os.makedirs(DEST, exist_ok=True)
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    if not quiet:
        print(line)


def repo_skills():
    if not os.path.isdir(SRC):
        return []
    return sorted(d for d in os.listdir(SRC)
                  if os.path.isfile(os.path.join(SRC, d, "SKILL.md")))


# ---------- 节流 ----------

def stamp_path():
    return os.path.join(DEST, STAMP)


def recently_synced():
    try:
        return (time.time() - os.path.getmtime(stamp_path())) < THROTTLE
    except OSError:
        return False


def touch_stamp():
    try:
        os.makedirs(DEST, exist_ok=True)
        with open(stamp_path(), "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
    except OSError:
        pass


# ---------- 同步动作 ----------

def has_remote():
    rc, out = run(["git", "remote", "get-url", "origin"])
    return rc == 0 and bool(out)


def fetch():
    """拉远端。网络抖（连 github 第一次常常超时）时重试几次，别一次失败就放弃。

    实测：同一台机器上，git fetch 首次可能卡在 TCP 连接 90s 超时，
    紧接着重试 1 秒就通。所以这里默认给 3 次机会，第一次 40s、之后 60s。
    """
    last = (1, "未执行")
    for i in range(FETCH_TRIES):
        timeout = FETCH_BUDGET if i == 0 else int(FETCH_BUDGET * 1.5)
        rc, out = run(["git", "fetch", "--prune", "--quiet", "origin"],
                      timeout=timeout)
        if rc == 0:
            return rc, out
        last = (rc, out)
        if rc == 127:          # 没装 git，重试无意义
            break
        time.sleep(1)
    return last


def local_branch():
    rc, out = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    return out if rc == 0 else "main"


def behind_count():
    """本地落后远端几个提交。返回 (数量, 说明)；算不出来时数量为 -1。"""
    branch = local_branch()
    rc, upstream = run(["git", "rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}"])
    ref = upstream if rc == 0 else f"origin/{branch}"
    rc, out = run(["git", "rev-list", "--count", f"HEAD..{ref}"])
    if rc != 0:
        return -1, out
    try:
        return int(out), ref
    except ValueError:
        return -1, out


def pull():
    branch = local_branch()
    return run(["git", "pull", "--ff-only", "--quiet", "origin", branch], timeout=120)


def sync_copy_mode(name):
    """复制模式：把仓库内容刷到 ~/.codebuddy/skills/<name>。"""
    dst = os.path.join(DEST, name)
    if not os.path.exists(dst):
        return False, "未安装"
    if os.path.islink(dst):
        return False, "软链模式，无需复制"
    src = os.path.join(SRC, name)
    try:
        shutil.rmtree(dst)
        shutil.copytree(src, dst,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        return True, "已重新复制"
    except OSError as e:
        return False, f"复制失败：{e}"


def check_skills():
    """校验已安装技能能否被加载。返回 [(name, ok, why)]。"""
    results = []
    for n in repo_skills():
        dst = os.path.join(DEST, n)
        if not os.path.exists(dst):
            results.append((n, False, "未安装"))
            continue
        md = os.path.join(dst, "SKILL.md")
        if not os.path.isfile(md):
            results.append((n, False, "缺 SKILL.md（软链可能断了）"))
            continue
        try:
            head = open(md, encoding="utf-8").read(2000)
        except OSError as e:
            results.append((n, False, f"读取失败：{e}"))
            continue
        if not head.startswith("---") or "name:" not in head.split("---")[1]:
            results.append((n, False, "frontmatter 不合法"))
            continue
        results.append((n, True, "ok"))
    return results


# ---------- cron ----------

def read_crontab():
    rc, out = run(["crontab", "-l"])
    return out if rc == 0 else ""


def install_cron(at, quiet=False):
    try:
        hh, mm = at.split(":")
        hh, mm = int(hh), int(mm)
        assert 0 <= hh < 24 and 0 <= mm < 60
    except (ValueError, AssertionError):
        print(f"× 时间格式应为 HH:MM，收到：{at}")
        return 1

    current = read_crontab()
    kept = [l for l in current.splitlines() if CRON_TAG not in l]
    cmd = (f"{sys.executable} {os.path.join(REPO, 'scripts', 'sync_skills.py')} "
           f"--quiet >> {LOG} 2>&1 # {CRON_TAG}")
    kept.append(f"{mm} {hh} * * * {cmd}")
    body = "\n".join(l for l in kept if l.strip()) + "\n"

    tmp = "/tmp/.crontab_medic"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(body)
    rc, out = run(["crontab", tmp])
    try:
        os.unlink(tmp)
    except OSError:
        pass
    if rc != 0:
        print(f"× 写入计划任务失败：{out}")
        if "命令不存在" in out:
            print()
            print("  本环境没有 crontab（常见于容器沙箱），定时同步无法安装。")
            print("  这不影响使用：技能调用时会自动同步一次，有效期内不重复拉取。")
            print("  想立刻同步，直接跑：")
            print(f"    python3 {os.path.join(REPO, 'scripts', 'sync_skills.py')} --force")
        return 1
    if not quiet:
        print(f"✓ 已设置每天 {hh:02d}:{mm:02d} 自动同步（{CRON_TAG}）")
        print("  注意：容器休眠期间 cron 不会执行，唤醒后下一次触发才补上。")
    return 0


def remove_cron():
    current = read_crontab()
    if CRON_TAG not in current:
        print("· 没有找到本脚本设置的计划任务")
        return 0
    kept = [l for l in current.splitlines() if CRON_TAG not in l]
    body = ("\n".join(l for l in kept if l.strip()) + "\n") if kept else ""
    tmp = "/tmp/.crontab_medic"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(body)
    rc, out = run(["crontab", tmp])
    try:
        os.unlink(tmp)
    except OSError:
        pass
    if rc != 0:
        print(f"× 移除失败：{out}")
        return 1
    print("✓ 已移除自动同步计划任务")
    return 0


# ---------- 主流程 ----------

def do_sync(args):
    if not args.force and not args.check and recently_synced():
        # 刚同步过：直接校验本地完整性，不碰网络
        bad = [r for r in check_skills() if not r[1]]
        for name, ok, why in bad:
            log(f"  ✗ {name}：{why}", args.quiet)
        return 1 if bad else 0

    if not os.path.isdir(os.path.join(REPO, ".git")):
        log(f"× {REPO} 不是 git 仓库，无法同步", args.quiet)
        return 1
    if not has_remote():
        log("× 仓库没有配置 origin，无法同步", args.quiet)
        return 1

    rc, out = fetch()
    if rc != 0:
        # 网络问题不当作致命错误：技能还能用旧版本，只是不更新
        touch_stamp()          # 失败也打戳：6 小时内不再重试，避免拖慢每次调用
        log(f"! 拉取远端失败（技能保持当前版本）：{out.splitlines()[0] if out else rc}",
            args.quiet)
        return 0

    n, ref = behind_count()
    if n < 0:
        log(f"! 无法比较本地与远端：{ref}", args.quiet)
        return 0
    if n == 0:
        touch_stamp()
        if not args.quiet:
            log("· 已是最新", False)
        return 0

    if args.check:
        print(f"有 {n} 个新提交待拉取（{ref}）")
        rc, out = run(["git", "log", "--oneline", f"HEAD..{ref}"])
        if rc == 0:
            for line in out.splitlines()[:10]:
                print(f"  {line}")
        return 0

    rc, out = pull()
    if rc != 0:
        log(f"× 拉取失败（可能有本地改动冲突）：{out.splitlines()[0] if out else rc}",
            args.quiet)
        return 1
    touch_stamp()
    log(f"✓ 已拉取 {n} 个提交", args.quiet)

    for name in repo_skills():
        changed, why = sync_copy_mode(name)
        if changed:
            log(f"  · {name}：{why}", args.quiet)

    bad = [r for r in check_skills() if not r[1]]
    for name, ok, why in bad:
        log(f"  ✗ {name}：{why}", args.quiet)
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser(description="从远端同步技能")
    ap.add_argument("--check", action="store_true", help="只看有没有更新")
    ap.add_argument("--quiet", action="store_true", help="无事则静默（供 cron/hook）")
    ap.add_argument("--force", action="store_true",
                    help="忽略 6 小时节流，强制访问远端")
    ap.add_argument("--status", action="store_true", help="打印仓库与安装状态")
    ap.add_argument("--install-cron", metavar="HH:MM", help="装每日定时同步")
    ap.add_argument("--remove-cron", action="store_true", help="移除定时同步")
    args = ap.parse_args()

    if args.install_cron:
        return install_cron(args.install_cron, args.quiet)
    if args.remove_cron:
        return remove_cron()

    if args.status:
        print(f"仓库：{REPO}")
        rc, url = run(["git", "remote", "get-url", "origin"])
        print(f"远端：{url if rc == 0 else '（未配置）'}")
        rc, head = run(["git", "log", "-1", "--format=%h %ad %s", "--date=short"])
        print(f"本地：{head}")
        rc, out = run(["crontab", "-l"])
        cron = [l for l in out.splitlines() if CRON_TAG in l] if rc == 0 else []
        print(f"定时：{cron[0].split('#')[0].strip() if cron else '（未设置）'}")
        print("技能：")
        for name, ok, why in check_skills():
            dst = os.path.join(DEST, name)
            mode = "软链" if os.path.islink(dst) else ("复制" if os.path.exists(dst) else "未装")
            print(f"  {'✓' if ok else '✗'} {name}  [{mode}]  {why}")
        return 0

    return do_sync(args)


if __name__ == "__main__":
    sys.exit(main())
