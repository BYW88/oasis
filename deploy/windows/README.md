# Windows 私人局域网部署（不需要 Docker Desktop）

该方案使用普通账户运行 Python 后端和本机 Next.js 生产前端，仅公开一个 HTTPS 端口。它通过账户权限减小文件暴露范围，不具备虚拟机级隔离。

## 安全行为变化

- 默认本机和局域网请求都需要随机存取码，不再把 Host/回环来源当作身份。
- 启动时在本机生成超过 128 位随机存取码；网页不能切换、撤销、显示存取码，也不能触发自动更新或桌面播放器。管理操作在本机完成。
- Python 媒体请求逐跳校验全部 DNS 结果并连接固定公网 IP，保留原主机的 TLS 验证；拒绝代理、内网、回环及链路本地目标。
- 封面必须是可解码的栅格图片，并重新编码为 JPEG；旧 SVG 不再提供，封面禁用共享缓存并加 CSP sandbox。
- 默认关闭向第三方发送标题的自动翻译；运行包不包含翻译提供者。
- 保留 Chrome 自身沙盒；最多同时分析两个网址。
- HTTPS 访问日志关闭，以免 URL 中的流媒体存取码进入日志。
- Python、Node、Chrome 子进程树共限制为 2 GiB 内存、128 个进程。限制不是 CPU 或磁盘配额。

浏览器、FFmpeg、yt-dlp 等外部工具仍处理不可信内容。Python HTTP 限制不能覆盖所有外部工具的网络行为，专用账户也不能隔离它有权访问的内网服务。请只允许可信设备连接，及时更新浏览器/媒体工具，并保留独立备份。

## 构建

使用 Windows x64、Python 3.13 和 Node.js 24。后端精确运行版本位于 `requirements-win.lock`；仅开发测试需要 `backend/requirements-dev.txt`。

```powershell
python -m pip install -r deploy/windows/requirements-win.lock
cd web
npm ci
$env:OASIS_LAN_BUILD = '1'
$env:NEXT_PUBLIC_SELF_HOSTED = '1'
$env:NEXT_TELEMETRY_DISABLED = '1'
npm run build
```

便携包的目录结构：

```text
app/                         后端 Python 文件及 sites/
frontend/                    web/.next/standalone 的内容
frontend/.next/static/       web/.next/static 的内容
frontend/public/             web/public 的内容
runtime/python/              官方 Python 便携运行时及锁定依赖
runtime/node.exe             Node.js
runtime/chrome/chrome-win64/ 官方 Chrome for Testing
runtime/bin/chromedriver.exe 与 Chrome 对应的官方驱动
runtime/bin/ffmpeg.exe        FFmpeg
initialize_lan.py
lan_server.py
windows_limits.py
install-lan.ps1
show-access-code.ps1
rotate-access-code.ps1
stop-lan.ps1
bundle-manifest.json          源码/运行时文件的 SHA-256 清单
```

仅从官方来源或已验证发行包准备运行时，验证下载摘要。构建前端时会下载字体；部署完成后前端由本机提供。保留各工具随附的许可证；FFmpeg 使用所选发行包的许可证。

## 安装

确认所选网络是自己的可信私人局域网。以管理员身份运行下面的示例，将参数换成本机实际值；本仓库不保存具体 IP、用户 SID 或凭据。

```powershell
.\install-lan.ps1 `
  -BundlePath 'D:\Oasis-LAN' `
  -ListenAddress '192.168.1.100' `
  -AllowedSubnet '192.168.1.0/24' `
  -InterfaceAlias 'Wi-Fi' `
  -OwnerSid '实际日常账户的 SID'
```

管理员安装程序会：

1. 验证包中清单记录的文件摘要，并检查没有同名安装/账户/任务。
2. 安装至 `%ProgramData%\OasisLAN`，创建 `OasisSvc` 普通账户。随机账户密码只交给 Windows 计划任务保存，不写入文本文件。
3. 服务账户对程序只有读取/执行权限，对 `data/`、`logs/` 有写入权限；指定的日常账户只能读取安装目录。
4. 在目标机器生成自签名证书、私钥和存取码，不复制构建机器上的私密数据。
5. 将指定网络接口标记为 Private，并只对指定接口/IP/子网开放 TCP 8443；不会开放 8000 或 3031。
6. 注册开机启动计划任务 `OasisLAN`，以普通账户立即启动。Next.js 仅监听 `127.0.0.1:3031`。

首次用手机访问 `https://<局域网IP>:8443` 时需要确认自签名证书。应先在本机核对证书 SHA-256 指纹，不要忽略其他网站的证书错误。证书有效期一年；IP 变化时要更新监听、证书和防火墙规则。建议在路由器配置 DHCP 地址保留。

本机运行 `show-access-code.ps1` 查看存取码，在各设备首次进入网页时输入。不要通过聊天、GitHub 或截图分享整个认证 JSON/数据库/私钥。查看存取码不需要管理员身份。

需要撤销所有已配对设备时，以管理员身份运行 `rotate-access-code.ps1`；设备需要重新输入新码。

检查运行状态（管理员 PowerShell）：

```powershell
Get-ScheduledTask -TaskName OasisLAN
Get-ScheduledTaskInfo -TaskName OasisLAN
Get-NetFirewallRule -Name OasisLAN-HTTPS
Get-NetTCPConnection -State Listen -LocalPort 8443,3031
```

`stop-lan.ps1` 停止计划任务并禁用局域网规则，不删除数据。需要恢复时由管理员启用该规则并启动任务。更新采用审核过的分支和重新构建的运行包，避免上游自动更新覆盖安全修补。

本次依赖验证：Windows 运行依赖使用 PyPI 版本漏洞记录检查，前端生产依赖使用 `npm audit --omit=dev`。全量前端审计仍有 ESLint 开发依赖链的 `braces` 告警（无兼容修复）；不将这些开发依赖部署到服务。扫描结果有时效性，不代表不存在未知漏洞。
