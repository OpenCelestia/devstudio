#!/bin/bash
# wsl_build.sh - runs inside bash (Windows: wsl.exe bash -s; Linux/macOS: bash -s).
#
# Env (set by server.mjs):
#   MODE      build | package
#   TREE      repo path in WSL (default ~/celestia/develop)
#   PRODUCT   product name under project/ (default anypi_nova_dev)
#   MODULE    config.mk module name without CONFIG_ prefix (e.g. APP_STUDIO)
#   APP_ID    app package id (package mode only; empty => derive from artifact)
#
# Output protocol on stdout:
#   build   -> build.sh output streamed as-is, final line "ARTIFACT:<path>"
#   package -> staging done in /tmp/studio_push_stage/<pkg>, final line
#              "MANIFEST:<json>" ({"type","pkg","app_id","version","artifact"})
#              PC then runs `tar -C /tmp/studio_push_stage/<pkg> -czf - .` to fetch it.
set -u

MODE="${MODE:-build}"
TREE="${TREE:-$HOME/celestia/develop}"
PRODUCT="${PRODUCT:-anypi_nova}"
MODULE="${MODULE:-APP_STUDIO}"
APP_ID="${APP_ID:-}"
case "$TREE" in
    "~/"*) TREE="$HOME/${TREE:2}" ;;
esac

cd "$TREE" || { echo "FATAL: tree not found: $TREE"; exit 1; }

# GNU stat -c, or BSD stat -f on macOS.
file_mtime() {
    stat -c %Y "$1" 2>/dev/null || stat -f %m "$1" 2>/dev/null || echo 0
}
file_size() {
    stat -c %s "$1" 2>/dev/null || stat -f %z "$1" 2>/dev/null || echo 0
}

# Newest regular file among the given paths. Empty if none exist.
newest_file() {
    best=""
    best_t=0
    for f in "$@"; do
        [ -f "$f" ] || continue
        t=$(file_mtime "$f")
        if [ "$t" -gt "$best_t" ]; then
            best=$f
            best_t=$t
        fi
    done
    printf '%s\n' "$best"
}

# App tree the module install just wrote. utils/usr/apps is the compile output;
# install/apps is the U-disk tree from a full image and can be older.
app_dir() {
    id=$1
    best=""
    best_t=0
    for d in \
        out/$PRODUCT/build/system/*/*/rootfs/utils/usr/apps/$id \
        out/$PRODUCT/build/application/packages/tablet/app_sos/$id \
        out/$PRODUCT/install/application/$id \
        out/$PRODUCT/build/system/*/*/rootfs/install/apps/$id \
        out/$PRODUCT/build/system/*/*/image/install/apps/$id \
        out/$PRODUCT/build/system/*/*/tools/burntool/install/apps/$id
    do
        [ -d "$d" ] || continue
        mark=$d/app.json
        for so in "$d"/lib/*.so; do
            [ -f "$so" ] && mark=$so && break
        done
        t=$(file_mtime "$mark")
        if [ "$t" -gt "$best_t" ]; then
            best=$d
            best_t=$t
        fi
    done
    printf '%s\n' "$best"
}

# Product profile decides which tablet apps stay in the read-only user area.
# Everything else is copied onto the USB disk.
product_file() {
    # Prefer in-tree path under the Celestia repo root ($TREE).
    for cand in \
        "$TREE/devstudio/web/products/${PRODUCT}.json" \
        "devstudio/web/products/${PRODUCT}.json" \
        "$TREE/scripts/studio/products/${PRODUCT}.json"
    do
        if [ -f "$cand" ]; then
            printf '%s\n' "$cand"
            return 0
        fi
    done
    printf 'devstudio/web/products/%s.json\n' "$PRODUCT"
}
in_json_array() {
    f=$(product_file)
    [ -f "$f" ] || return 1
    awk -v key="$1" -v val="$2" '
        $0 ~ "\"" key "\"" { grab=1 }
        grab && $0 ~ "\"" val "\"" { found=1 }
        grab && index($0, "]") { grab=0 }
        END { exit found ? 0 : 1 }
    ' "$f"
}
json_string() {
    f=$(product_file)
    [ -f "$f" ] || return 1
    sed -n "s/.*\"$1\"[[:space:]]*:[[:space:]]*\"\\([^\"]*\\)\".*/\\1/p" "$f" | head -1
}
app_place() {
    if in_json_array usrApps "$1"; then
        printf 'usr\n'
    else
        printf 'usb\n'
    fi
}

stage_version() {
    sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$1" 2>/dev/null | head -1
}

# Locate what this module just produced and where it belongs on the device.
#   place=usb  APP tree that the image puts on the USB disk
#   place=usr  file or app that stays in the read-only user area (/usr)
locate_output() {
    ART=""
    PKG=""
    TYPE=""
    PLACE=""
    DEST=""
    KIND=""
    case "$MODULE" in
        APP_STUDIO)
            ART=$(newest_file \
                out/$PRODUCT/build/system/*/*/rootfs/utils/usr/lib/libstudio.so \
                out/$PRODUCT/install/application/lib/libstudio.so \
                application/services/studio/build/libstudio.so)
            PKG=studio
            TYPE=so
            PLACE=usr
            DEST=/usr/lib/libstudio.so
            KIND=file
            ;;
        APP_TABLET_*)
            ID="${APP_ID:-$(echo "$MODULE" | sed -n 's/.*APP_TABLET_//;T;p' | tr 'A-Z' 'a-z')}"
            ART=$(app_dir "$ID")
            PKG=$ID
            TYPE=app
            PLACE=$(app_place "$ID")
            if [ "$PLACE" = "usr" ]; then
                DEST=/usr/apps/$ID
            else
                USB_ROOT=$(json_string usbRoot)
                [ -z "$USB_ROOT" ] && USB_ROOT=/mnt/install/apps
                DEST=$USB_ROOT/$ID
            fi
            KIND=dir
            ;;
        *)
            echo "FATAL: no push rule for $MODULE"
            exit 3
            ;;
    esac
    if [ -z "$ART" ] || [ ! -e "$ART" ]; then
        echo "FATAL: artifact not found for $MODULE under out/$PRODUCT"
        exit 2
    fi
}

case "$MODE" in
build)
    # Pass the layer flag. --build-module alone makes build.sh compile every layer.
    case "$MODULE" in
        APP_*) LAYER=-a ;;
        FW_*) LAYER=-f ;;
        HW_*) LAYER=-h ;;
        THIRD_PARTY_*) LAYER=-t ;;
        *) echo "FATAL: no build layer for $MODULE"; exit 3 ;;
    esac
    ./build.sh --product-name "$PRODUCT" "$LAYER" --build-module "$MODULE" --jobs 8
    RC=$?
    [ $RC -ne 0 ] && { echo "FATAL: build failed rc=$RC"; exit $RC; }
    locate_output
    echo "ARTIFACT:$ART"
    ;;

package)
    locate_output
    STAGE="/tmp/studio_push_stage/$PKG"
    rm -rf "$STAGE" && mkdir -p "$STAGE"
    if [ "$KIND" = "file" ]; then
        mkdir -p "$STAGE/lib"
        cp -a "$ART" "$STAGE/lib/"
    else
        cp -a "$ART/." "$STAGE/"
        if [ -n "$ID" ] && [ -f "application/packages/tablet/$ID/app.json" ]; then
            cp -f "application/packages/tablet/$ID/app.json" "$STAGE/app.json"
            echo "PACK: using source app.json for $ID"
        fi
    fi

    VERSION=$(stage_version "$STAGE/app.json" 2>/dev/null)
    [ -z "$VERSION" ] && VERSION="0.0.0"
    # manifest must be a single line: PC splits on the last MANIFEST: prefix
    printf 'MANIFEST:{"type":"%s","pkg":"%s","app_id":"%s","version":"%s","artifact":"%s","place":"%s","dest":"%s","kind":"%s"}\n' \
        "$TYPE" "$PKG" "${APP_ID:-$PKG}" "$VERSION" "$ART" "$PLACE" "$DEST" "$KIND"
    ;;

usrimage)
    # Rebuild the read-only user partition (usr.sqsh4 / APP) with this module's output.
    locate_output
    [ "$PLACE" = "usr" ] || { echo "FATAL: $MODULE is not user-area content"; exit 3; }
    sq=""
    for c in out/$PRODUCT/build/system/*/*/rootfs/mksquashfs; do
        [ -x "$c" ] || continue
        sq=$c
    done
    [ -n "$sq" ] || { echo "FATAL: mksquashfs not found; package the system image once first"; exit 2; }
    root=$(dirname "$sq")
    tree="$root/rootfs/usr"
    [ -d "$tree" ] || { echo "FATAL: $tree missing"; exit 2; }
    rel=${DEST#/usr/}
    case "$rel" in
        ""|/*|..|../*|*"/.."*) echo "FATAL: bad dest $DEST"; exit 3 ;;
    esac
    if [ "$KIND" = "file" ]; then
        mkdir -p "$tree/$(dirname "$rel")"
        rm -f "$tree/lib/libsync_agent.so"
        cp -a "$ART" "$tree/$rel"
    else
        rm -rf "$tree/$rel"
        mkdir -p "$tree/$rel"
        cp -a "$ART/." "$tree/$rel/"
    fi
    img=/tmp/studio_usr.sqsh4
    if [ -f "$root/install.exclude" ]; then
        ( cd "$root" && ./mksquashfs rootfs/usr "$img" -noappend -comp xz -ef install.exclude ) || exit 4
    else
        ( cd "$root" && ./mksquashfs rootfs/usr "$img" -noappend -comp xz ) || exit 4
    fi
    sz=$(file_size "$img")
    max=$((0x8AC000))
    if [ "$sz" -gt "$max" ]; then
        echo "FATAL: usr.sqsh4 is $sz bytes, APP partition is $max"
        exit 4
    fi
    echo "USRIMAGE:$img"
    echo "USRSIZE:$sz"
    echo "ARTIFACT:$ART"
    ;;

*)
    echo "FATAL: bad MODE $MODE"
    exit 1
    ;;
esac
