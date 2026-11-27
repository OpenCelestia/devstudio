---
name: git-gitee
description: >-
  Celestia 多仓（repo 管理）的 Gitee 提交/同步 skill：配置 Gitee https 凭据
  （credential.helper store，免密 push/pull），并在 7 个子仓库（framework / project /
  application / scripts / hardware / system/core / third_party，远程均在 gitee.com/celestia-anypi，
  默认分支 develop）上批量 status / pull / push / merge，含提交信息格式、可执行位陷阱、repo sync 失败时的逐仓快进。
  何时用：提交或推送代码、repo sync / 拉取更新、看哪些仓有未推送提交、写 commit message、准备提交 / code review、脚本 Permission denied 时。
  When: commit or push, repo sync / pull, list unpushed commits, write commit messages, prep-commit / code review, or script Permission denied.
---

# Git / Gitee 提交与同步（Celestia 多仓）

Celestia 是 `.repo` 管理的多仓项目，根目录 **不是** git 仓库。各子仓独立，远程都在
`https://gitee.com/celestia-anypi/<repo>.git`，默认/集成分支为 `develop`；特性开发在
各自的特性分支上进行，完成后合并回 `develop`。

## 中英文切换（跟用户语言）

按用户**最新一句**判断语言（含汉字 / CJK → 中文；否则英文），再切换本 skill 的对话能力：

| 能力 | 中文用户 | 英文用户 |
|------|----------|----------|
| 回复、计划、Ask 标题/选项 | 中文 | English |
| 准备提交：改动摘要、自检清单、风险说明 | 中文 | English |
| Code review 意见（聊天里） | 中文 | English |
| **Git commit message**（写入仓库） | **始终英文**（见下方格式；与用户口语语言无关） | 同左 |
| PR / 合并说明若用户要求写入远端 | 跟用户语言；未说明则跟最新一句 | 同左 |

准备提交或 review 时，若改动触及各仓 `README.md`、`docs/` 或 DevStudio Ask 文案：

1. **跟用户语言**写审查结论与修改建议（不要固定只用中文）。
2. 用户要**改**这些文件时：用户界面可见段落与用户语言一致；保留已有另一语种对照句（如 `EN | 中文`）时勿删；技术标识符、命令、路径保持原文。
3. DevStudio `NEED_* ASK_UI`：Agent 展示 Ask 时把 TITLE/PROMPT/OPTIONS **本地化成用户语言**（源文常为中英并列，择一呈现即可）。
4. 代码注释 / 对外 API 文档字符串仍按 [celestia-contributing](../celestia-contributing/SKILL.md)（注释用英文）；与「聊天用用户语言」分开。

## 子仓清单

| 目录 | 远程 repo |
|------|-----------|
| `framework`     | framework |
| `project`       | project |
| `application`   | application |
| `scripts`       | scripts |
| `hardware`      | hardware |
| `system/core`   | core |
| `third_party`   | third_party（大仓，git log/status 慢，用 `--count` / `for-each-ref`） |

`SUBREPOS="framework project application scripts hardware system/core third_party"`

> `manifest/default.xml` 共管 **9** 个 project——上表 7 个加 `docs` / `devstudio`；
> `docs`、`devstudio` 不进日常代码提交循环，需要时单独 `git -C docs …` / `git -C devstudio …`。

## 账号与凭据（一次性配置）

Gitee https 免密通过 credential store 实现。将凭据写入 `~/.git-credentials`（**勿提交到仓库**）：

```bash
git config --global credential.helper store
# 按提示首次 push 时输入密码，或手动写入凭据行：
# https://<your-gitee-username>:<password>@gitee.com
```

> 在 Windows/Linux 跨文件系统挂载环境下，git 可能报 *dubious ownership*，可用：
> ```bash
> git config --global --add safe.directory '*'
> ```

## 常用操作

### 查看所有仓未推送状态
```bash
for d in $SUBREPOS; do
  echo "== $d =="
  git -C "$d" status -sb | head -1
  echo "  unpushed: $(git -C "$d" rev-list --count @{u}..HEAD 2>/dev/null)"
done
```
> third_party 慢，单独用 `git -C third_party for-each-ref \
> --format='%(refname:short) %(upstream:track)' refs/heads` 判断是否 ahead。

### 一键 push 所有有未推送提交的仓
```bash
for d in $SUBREPOS; do
  n=$(git -C "$d" rev-list --count @{u}..HEAD 2>/dev/null)
  [ "${n:-0}" -gt 0 ] && { echo ">> push $d ($n)"; git -C "$d" push; }
done
```

### 一键 pull（rebase）所有仓
```bash
for d in $SUBREPOS; do echo "== pull $d =="; git -C "$d" pull --rebase; done
```

### repo sync 与失败时的逐仓快进

`repo` 不一定在 PATH 里，可直接用 `python3 .repo/repo/repo sync -j4 --no-clone-bundle`（在 WSL `~/celestia/develop` 下跑；PowerShell 经 `wsl -e bash -lc '…'` 传含 `$` 的命令容易被拆坏，写成脚本再 `wsl -e bash 脚本`）。

若 checkout 阶段报 `NotADirectoryError: '../../.repo/projects/<x>.git/logs' -> '<x>/.git/logs'`：说明子仓的 `.git` 是普通目录而非 repo 链接布局，**fetch 已成功**，只是不能 checkout。不要去改 `.git`，改为逐仓快进：

```bash
for d in $(cat .repo/project.list); do
  echo "== $d behind=$(git -C "$d" rev-list --count HEAD..gitee/develop) ahead=$(git -C "$d" rev-list --count gitee/develop..HEAD)"
  git -C "$d" merge --ff-only gitee/develop
done
```

- 远程名是 `gitee`；`ahead>0` 时不能快进，先 `pull --rebase`。
- 报 `Your local changes … would be overwritten` 的仓：先看 `git diff HEAD gitee/develop -- <文件>` 与本地改动是否同一处，经用户同意后 `git stash push -m … && git merge --ff-only gitee/develop && git stash pop`；pop 冲突时 stash 保留，把冲突文件交给用户。
- `devstudio` 已在 manifest 中（远程 `gitee.com/celestia-anypi/devstudio`）；`docs` / `devstudio`
  不在 `SUBREPOS` 日常循环里，需要时单独 `git -C docs …` / `git -C devstudio …`。

### 单仓提交并推送
```bash
git -C <dir> add -A
git -C <dir> commit -m "type: short summary"   # see 提交信息格式 below
git -C <dir> push
```

## Merge 约定

- 特性开发在特性分支上完成，合并到 `develop` / 主干前先 `git -C <dir> pull --rebase`。
- 合并：`git -C <dir> checkout develop && git -C <dir> merge --no-ff <feature-branch>`，
  冲突按层归属（见 `celestia` 路由 skill）解决后再 push。
- 跨仓改动需保持各仓提交原子、消息前缀一致（feat/fix/refactor/chore/docs…），并遵循
  上面的提交信息格式（英文 summary + bullet 详情，一仓一提交）。

## 提交信息格式（Commit Message Format）

每个子仓的集成分支提交使用**英文**（即使用户用中文对话，commit message 仍英文；可用中文向用户解释将要提交的内容）。单行 summary + 空行 + bullet 详情。summary 用
`<type>: <概述>` 前缀（feat / fix / refactor / chore / docs …），概述列出该提交涉及的
主要改动点；bullet 逐项说明每类改动的来龙去脉（为什么改、改了什么、影响范围）。
**一个子仓一个提交**（squash 合并），不要带 `Co-Authored-By` 尾注，不要写 "merge"。

模板：

```
<type>: <short summary of the change set>

- <what changed and why, one bullet per logical change>
- <mention renames / config switches / gating macros explicitly>
- <note any cross-repo dependency, e.g. "follows system/core's rename of ...">
```

要点：
- summary 一行，概括该仓本次提交的全部改动；多类改动用 ` + ` 串联。
- bullet 用 `- `，每条聚焦一个逻辑改动，说清因果（避免只罗列文件名）。
- 涉及头文件重命名 / 宏开关 / gating 变更时必须显式写出旧→新。
- 跨仓改动在 bullet 中点名依赖（如 "follows system/core's rename"）。

## 文件权限（可执行位）陷阱

`repo sync` / fresh checkout 后，`.sh` 脚本丢可执行位会导致 `Permission denied`（典型：
`./scripts/build_kernel.sh: Permission denied`）。常见原因：**重命名/拷贝提交时丢了
+x**——`git mv` 保留模式，但先 `cp`（未 `-p`）/Windows 文件系统再 `git add` 会把
`100755` 降成 `100644`（Windows NTFS 无 unix 模式，git 取 index 记录的位）。

排查（列出所有应可执行却是 `100644` 的 .sh）：
```bash
git -C <dir> ls-files -s | grep '\.sh' | grep '100644'
```

修复（直接改 git index，文件系统 chmod 在 Windows 不生效）：
```bash
git -C <dir> update-index --chmod=+x <file>   # 逐个
# 或批量：
git -C <dir> ls-files -s | grep '\.sh' | grep '100644' | awk '{print $4}' \
  | while read f; do git -C <dir> update-index --chmod=+x "$f"; done
git -C <dir> commit -m "fix: restore exec mode on scripts"
```

要点：
- 凡是用 `cp` / 跨目录搬运 / 跨分支捡文件方式纳入 git 的脚本，提交前都务必
  `git ls-files -s | grep '\.sh' | grep 100644` 核对可执行位，发现丢失用
  `git update-index --chmod=+x` 补回（`git mv` 保留模式，无需此举）。
- 判定规则只看"该文件是否会被执行"：会被 `source`/`sh`/开机拉起的脚本必须 `100755`；
  `defconfig`、`*.json`、`*.ini`、`*.dts` 等数据/配置文件保持 `100644`。

## SDK（system/AnyCloud39AV\*）不在 manifest

`manifest/default.xml` 只管 9 个子仓（application/docs/devstudio/framework/hardware/project/
scripts/system/core/third_party），**不含 `system/AnyCloud39AV100_V2`、
`system/AnyCloud39AV200` 等 vendor SDK**（含 `os/linux.tar.gz`、`platform/`、`scripts/`、
`rootfs/`）。

因此 fresh `repo init && repo sync` 后，`system/AnyCloud39AV*` 是空的，内核构建会报
`No rule to make target '<chip>_mini_defconfig'`（kernel 源码树缺失）。

处理：
- SDK 需单独获取（解压 vendor 包到 `system/AnyCloud39AV*/`），`repo sync` 不会拉取。
- 板级 defconfig / build_kernel.sh 等在 `project/<product>/system/AnyCloud39AV*/V*/`
  下（board overlay / patch），由 `build_soc_system.sh` 的 `cp -rf` 叠加到 out/ 内核树。
- 确认 SDK 完整：`ls system/AnyCloud39AV*/V*/os/linux.tar.gz`、
  `.../scripts/build_kernel.sh`（须有 +x，见上节）。

## 注意

- 不要把 `~/.git-credentials` 或明文密码写进任意子仓的文件。
- push 失败优先看是否被远程 reject（need pull）或凭据失效（重写凭据行）。
