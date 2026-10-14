# 谱面库上线（`proj7k.catalog`，ADR-0026）

网站本身只读，只监听本机 `127.0.0.1:7780`，对外的 HTTPS 交给前面的一层。两种做法：

- **A. 本机 + Cloudflare Tunnel（Windows）**：不花钱、不用公网 IP、不开端口，电脑要一直开着。
- **B. Linux VPS + Caddy**：电脑可以关机，每月几十元。

两种都要先把域名放在 Cloudflare（A）或给子域名加 A 记录（B）。下文的子域名用 `7k.kaiz.dev`。

# A. 本机 + Cloudflare Tunnel（Windows）

隧道由本机的 `cloudflared` 主动连到 Cloudflare，访客经 Cloudflare 进来，所以在 NAT 后面、路由器不归自己管也能用。

1. **把域名放进 Cloudflare**：注册免费账号，Add a domain 填 `kaiz.dev`，确认自动导入的记录（根域名指向 GitHub Pages 的 4 条 A 记录、`www` 等）都在，然后到注册商把 NS 改成 Cloudflare 给的两个。生效后 Cloudflare 会发邮件。
2. **建隧道**：Cloudflare 控制台 → Zero Trust → Networks → Tunnels → Create a tunnel（Cloudflared），起名 `proj7k-catalog`。页面给出一条 `cloudflared.exe service install <token>`，在管理员 PowerShell 里运行即可装成开机自启的 Windows 服务（`cloudflared.exe` 从 https://github.com/cloudflare/cloudflared/releases 下载）。
3. **配路由**：同一页 Public Hostname 加一条：子域名 `7k`，域名 `kaiz.dev`，Service `HTTP`，URL `127.0.0.1:7780`。DNS 记录会自动建好。
4. **网站开机自启**：管理员 PowerShell：

   ```powershell
   $repo = "C:\Users\Administrator\Documents\proj7k"
   $db   = "$env:USERPROFILE\.cache\proj7k\catalog.sqlite3"
   schtasks /Create /TN "proj7k catalog" /SC ONSTART /RU SYSTEM /RL HIGHEST /F `
     /TR "`"$repo\deploy\catalog\windows\catalog-serve.cmd`" `"$db`""
   # 默认设置不适合常驻网站：优先级 7（低于正常，电脑忙时会超时）、72 小时后自动停止、用电池时停止。
   $s = New-ScheduledTaskSettingsSet -Priority 5 -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
     -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
   Set-ScheduledTask -TaskName "proj7k catalog" -Settings $s
   schtasks /Run /TN "proj7k catalog"
   ```

   建好这个任务后，根目录的 `restart-services.cmd` 重启网站时会改为重启这个任务，不会再从主目录另起一份。

5. **定时更新（可选）**：两个以自己账号运行的任务（凭据只在环境变量里，不进任何文件）。
   - `catalog-update.cmd`，每 6 小时：`update` 只取上次以来 osu! 上变了的东西（新传和重传的谱、上架/Loved/进坟场等状态变化、删掉的谱和难度），一次几十个请求、一两分钟，只为新难度跑引擎。
   - `catalog-crawl.cmd`，每周一次：`crawl --status all` 全量对一遍，补上 `update` 看不到的变化（osu! 重算官方 SR、下架回坟场、下载失败的谱），约 15–30 分钟，大部分时间在等网络。

   两个任务都要指向**网站任务用的同一份检出**（上面的 `$repo`）和同一个数据库：引擎版本号不同的话，之后的 `refresh` 会把整库重算一遍。先把 `OSU_CLIENT_ID`、`OSU_CLIENT_SECRET` 设为自己账号的用户环境变量，然后在 PowerShell 里：

   ```powershell
   $repo = "C:\Users\Administrator\Documents\proj7k"      # 与网站任务相同
   schtasks /Create /TN "proj7k catalog update" /SC HOURLY /MO 6 /ST 03:30 /IT /F `
     /TR "`"$repo\deploy\catalog\windows\catalog-update.cmd`""
   schtasks /Create /TN "proj7k catalog crawl" /SC WEEKLY /D MON /ST 04:00 /IT /F `
     /TR "`"$repo\deploy\catalog\windows\catalog-crawl.cmd`""
   # 默认优先级 7 在电脑忙时会被饿住；这两个任务大多在等网络，正常优先级也不占 CPU。
   $s = New-ScheduledTaskSettingsSet -Priority 5 -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
     -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew
   Set-ScheduledTask -TaskName "proj7k catalog update" -Settings $s
   Set-ScheduledTask -TaskName "proj7k catalog crawl" -Settings $s
   schtasks /Run /TN "proj7k catalog update"
   ```

   `/IT` 表示只在自己登录着的时候跑（不用把密码交给任务计划程序）；错过的时间点登录后补跑（`-StartWhenAvailable`）。想在注销时也跑，把 `/IT` 换成 `/RU $env:USERNAME /RP`（会提示输入密码）。网站进程会自己发现库变了，不用重启。手动跑一次：`schtasks /Run /TN "proj7k catalog update"`；也可以 `update --since 2026-10-01` 指定从哪天起查。
6. 打开 `https://7k.kaiz.dev/beatmapsets` 检查。可以在 Cloudflare 里给 `/static/*` 和 `/api/*` 开缓存，接口本身已带 `Cache-Control`。

电脑关机、重启后两项服务都会自己起来；断网期间访客会看到 Cloudflare 的 502 页。

# B. Linux VPS + Caddy

一台能跑 Python 的 Linux 小机器（2 核 2 GB、20 GB 磁盘够用）。Caddy 对外提供 HTTPS；定时器每 6 小时增量更新一次（`update`），每周全量对一遍（`crawl --status all`），之后都 `refresh`。

## B1. DNS

在域名的 DNS 里给子域名（下文用 `7k.kaiz.dev`）加一条 A 记录，指向服务器 IP。`.dev` 只能走 HTTPS，证书由 Caddy 自动申请。

## B2. 服务器

Ubuntu 24.04 为例，以 root 执行：

```bash
apt update && apt install -y git caddy
curl -LsSf https://astral.sh/uv/install.sh | sh          # Python 3.14 由 uv 提供
useradd --system --home /var/lib/proj7k --create-home proj7k
git clone https://github.com/ka1z07/proj7k /opt/proj7k
cd /opt/proj7k && ~/.local/bin/uv venv --python 3.14 .venv && ~/.local/bin/uv pip install --python .venv -r requirements.txt
```

## B3. 数据库

在已经抓好库的机器上导出一份压缩过的副本（不带 WAL），传到服务器：

```bash
PYTHONPATH=src python3 -m proj7k.catalog export catalog-upload.sqlite3
scp catalog-upload.sqlite3 root@SERVER:/var/lib/proj7k/catalog.sqlite3
ssh root@SERVER chown proj7k: /var/lib/proj7k/catalog.sqlite3
```

也可以不传，直接在服务器上从零抓（`crawl --status all`，全量约一天）。

数据库从 Windows 搬到 Linux 后，如果 Windows 上的仓库是 2026-10-12 之前检出的，引擎版本号会对不上，全部难度显示「待重算」，第一次定时任务里的 `refresh` 会补上（2 核约 1–2 小时，期间网站照常可用）。

## B4. 服务

```bash
cp deploy/catalog/*.service deploy/catalog/*.timer /etc/systemd/system/
cp deploy/catalog/Caddyfile /etc/caddy/Caddyfile        # 改成你的子域名
mkdir -p /etc/proj7k && install -m 600 /dev/null /etc/proj7k/osu.env
# 编辑 /etc/proj7k/osu.env：OSU_CLIENT_ID=… 和 OSU_CLIENT_SECRET=…（osu! 设置 → OAuth）
systemctl daemon-reload
systemctl enable --now proj7k-catalog proj7k-catalog-update.timer proj7k-catalog-crawl.timer
systemctl reload caddy
```

打开 `https://7k.kaiz.dev/beatmapsets` 检查。谱师页的索引在启动后后台构建，全库约半分钟。

## B5. 日常

- 看日志：`journalctl -u proj7k-catalog -u proj7k-catalog-update -u proj7k-catalog-crawl`。
- 手动更新一次：`systemctl start proj7k-catalog-update`；全量对一遍：`systemctl start proj7k-catalog-crawl`。
- 更新代码：`cd /opt/proj7k && git pull && systemctl restart proj7k-catalog && systemctl start proj7k-catalog-update`（引擎变了的话，抓取后的 `refresh` 会重算全部难度）。
- 备份：`python -m proj7k.catalog --db /var/lib/proj7k/catalog.sqlite3 export /root/backup-$(date +%F).sqlite3`。
