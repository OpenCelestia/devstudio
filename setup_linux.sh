#!/bin/sh
set -eu
ROOT=$(cd "$(dirname "$0")" && pwd)
VENV_PYTHON="$ROOT/.venv/bin/python"
BIN="$HOME/.local/bin/devstudio"


disable_broken_launchers() {
  # Drop pip console-script leftovers that cannot import the package.
  old_IFS=$IFS
  IFS=:
  for dir in $PATH; do
    [ -n "$dir" ] || continue
    case "$dir" in
      "$HOME/.local/bin") continue ;;
      */Anyka/DevStudio|*/Anyka/DevStudio/) continue ;;
      /mnt/c/Users/*/AppData/Local/Anyka/DevStudio) continue ;;
    esac
    for cand in "$dir/devstudio"; do
      [ -e "$cand" ] || continue
      [ "$cand" = "$BIN" ] && continue
      if ! "$cand" --version >/dev/null 2>&1; then
        mv "$cand" "$cand.broken-by-devstudio-setup" 2>/dev/null || rm -f "$cand"
        echo "已停用损坏的 CLI 入口：$cand"
      fi
    done
  done
  IFS=$old_IFS
}

install_runtime() {
  PY=""
  for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then
      if "$c" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"; then
        PY=$c
        break
      fi
    fi
  done
  if [ -z "$PY" ]; then
    echo "未找到可用的 Python 3.11 或更高版本。请先安装 Python 3.11+，再执行 ./setup_linux.sh。"
    exit 1
  fi
  if [ ! -x "$VENV_PYTHON" ]; then
    "$PY" -m venv "$ROOT/.venv"
  fi
  mkdir -p "$HOME/.local/bin"
  cat > "$BIN" <<EOF
#!/bin/sh
export DEVSTUDIO_ROOT="$ROOT"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export PYTHONPATH="$ROOT/src\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$ROOT/.venv/bin/python" -m devstudio "\$@"
EOF
  chmod +x "$BIN"
  "$BIN" --version
  disable_broken_launchers
  echo "已安装 devstudio。启动器：$BIN"
  echo "请保留这个 DevStudio 目录，Adapter 和 Skill 从这里读取。"
  echo "若新终端找不到 devstudio，请把 ~/.local/bin 加入 PATH。"
}

is_chip() {
  dir=$1
  if [ -d "$dir/application" ] && [ -d "$dir/framework" ] && [ -d "$dir/hardware" ] && [ -f "$dir/build.sh" ]; then
    return 0
  fi
  base=$(basename "$dir")
  if [ -d "$dir/os" ] && [ -d "$dir/platform" ] && [ -d "$dir/tools" ] && printf '%s' "$base" | grep -Eq '^V[0-9]'; then
    return 0
  fi
  if [ -d "$dir/system" ]; then
    find "$dir/system" -mindepth 2 -maxdepth 2 -type d -name 'V*' | grep -q .
    return $?
  fi
  return 1
}

usage() {
  echo "用法："
  echo "  ./setup_linux.sh                              # 安装（如需要）并识别工程后 init"
  echo "  ./setup_linux.sh <工程目录>                   # 明确指定 Celestia 或 SDK 版本目录"
  echo "  ./setup_linux.sh [客户工程目录] --sdk <目录>  # 客户应用工程，明确指定 SDK 版本目录"
  echo "  可追加 --product <产品名>                     # 多产品 Celestia 工程明确选择产品"
  echo "  ./setup_linux.sh --install-only               # 只安装运行时，不 init"
}

INSTALL_ONLY=0
PROJECT_ARG=""
SDK_ARG=""
PRODUCT_ARG=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --install-only)
      INSTALL_ONLY=1
      shift
      ;;
    --sdk)
      [ "$#" -ge 2 ] || { usage; exit 2; }
      SDK_ARG=$2
      shift 2
      ;;
    --product)
      [ "$#" -ge 2 ] || { usage; exit 2; }
      PRODUCT_ARG=$2
      shift 2
      ;;
    -*)
      usage
      exit 2
      ;;
    *)
      [ -z "$PROJECT_ARG" ] || { usage; exit 2; }
      PROJECT_ARG=$1
      shift
      ;;
  esac
done

if [ ! -x "$VENV_PYTHON" ] || [ ! -x "$BIN" ]; then
  install_runtime
fi

if [ "$INSTALL_ONLY" -eq 1 ]; then
  echo "请新开一个终端，执行 devstudio --version。已经打开的 IDE 请重启。"
  echo "然后进入工程目录执行 ./setup_linux.sh，或直接执行 devstudio init。"
  exit 0
fi

if [ -n "$SDK_ARG" ]; then
  if [ -n "$PROJECT_ARG" ]; then
    PROJECT=$(cd "$PROJECT_ARG" && pwd)
  else
    PROJECT=$(dirname "$ROOT")
  fi
  case "$SDK_ARG" in
    /*) SDK_CANDIDATE=$SDK_ARG ;;
    *) SDK_CANDIDATE=$PROJECT/$SDK_ARG ;;
  esac
  SDK=$(cd "$SDK_CANDIDATE" && pwd)
  SDK_BASE=$(basename "$SDK")
  if [ ! -d "$SDK/os" ] || [ ! -d "$SDK/platform" ] || [ ! -d "$SDK/tools" ] ||
     ! printf '%s' "$SDK_BASE" | grep -Eq '^V[0-9]'; then
    echo "指定目录不是具体的 SDK 版本目录：$SDK"
    echo "目录必须是 <平台族>/<版本>，并包含 os、platform、tools。"
    exit 1
  fi
elif [ -n "$PROJECT_ARG" ]; then
  PROJECT=$(cd "$PROJECT_ARG" && pwd)
  is_chip "$PROJECT" || { echo "这个目录不是 Celestia 工程，也不是芯片 SDK：$PROJECT"; usage; exit 1; }
else
  PARENT=$(dirname "$ROOT")
  if is_chip "$PARENT"; then
    PROJECT=$PARENT
  else
    PROJECT=""
    for dir in "$PARENT"/*; do
      [ "$dir" = "$ROOT" ] && continue
      if [ -d "$dir" ] && is_chip "$dir"; then
        if [ -n "$PROJECT" ]; then
          echo "找到多个工程或 SDK 版本，请明确指定：./setup_linux.sh <工程或版本目录>"
          exit 1
        fi
        PROJECT=$dir
      fi
    done
    if [ -z "$PROJECT" ]; then
      echo "未自动识别工程，请选择源码组合："
      echo "  1) Celestia + SDK"
      echo "  2) 只有芯片 SDK"
      echo "  3) 客户应用代码 + SDK"
      printf "请选择 [1/2/3]："
      read -r choice
      case "$choice" in
        1)
          printf "Celestia 工程根目录："
          read -r selected
          exec "$0" "$selected"
          ;;
        2)
          printf "具体 SDK 版本目录："
          read -r selected
          exec "$0" "$selected"
          ;;
        3)
          printf "客户项目根目录 [%s]：" "$PARENT"
          read -r selected
          [ -n "$selected" ] || selected=$PARENT
          printf "SDK 版本子目录（例如 sdk/AnyCloud39AV100_V2/V2.05）："
          read -r selected_sdk
          [ -n "$selected_sdk" ] || { echo "客户应用场景必须指定 SDK 版本目录。"; exit 2; }
          exec "$0" "$selected" --sdk "$selected_sdk"
          ;;
        *)
          echo "未选择有效的源码组合。"
          exit 2
          ;;
      esac
    fi
  fi
fi

set -- -m devstudio init
[ -z "$SDK_ARG" ] || set -- "$@" --state sdk_customer_app --sdk "$SDK"
[ -z "$PRODUCT_ARG" ] || set -- "$@" --product "$PRODUCT_ARG"
(cd "$PROJECT" && \
  DEVSTUDIO_ROOT="$ROOT" \
  DEVSTUDIO_BIN="$BIN" \
  PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" \
  "$VENV_PYTHON" "$@")
if [ ! -f "$PROJECT/devstudio.yaml" ]; then
  echo "未初始化工程；没有写入 devstudio.yaml。"
  exit 0
fi
echo "已加载 $PROJECT。"
echo "请在 VS Code/Cursor 的 Agent 面板中提出研发需求。"
echo "DevStudio 使用 IDE 自带模型，不读取或保存模型 API Key。"
