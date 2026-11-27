# DevStudio web

PC 端完整页面（画面 / 文件 / 编译推送）。设备通道仍是板上 `application/services/studio`（TCP 7777）。

```text
devstudio web [--port 8081] [--product anypi_nova] [--repo 仓库根]
```

需要 Node.js ≥ 18。启动后打开 `http://localhost:8081`。

| 能力 | 实现 |
|:-----|:-----|
| 连板 / 画面 / 截图 / 文件 / 重启 | `server.mjs` 长连接 Studio 7777（与 `devstudio device` 同协议） |
| 编译 / 打包 | `wsl_build.sh` → 仓库 `./build.sh`（与 CLI 同一套构建） |
| 推应用 | 页面推送走 Studio 协议；也可用 `devstudio device push` |

本目录保留 UI + Node 服务；业务构建/设备协议与 DevStudio CLI 共用仓库与 7777，不另起一套设备逻辑。
