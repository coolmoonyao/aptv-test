# aptv-test · 直播源每日自动更新服务

从公共直播源抓取 → 关键词过滤 → ffprobe 探测分辨率/响应时间 → 合并去重排序 → 生成固定名 `live.m3u`，每日自动推送到本仓库。

**订阅地址（文件名固定，永久有效，使用者无需改动）：**

| 通道 | 订阅 URL |
|---|---|
| jsDelivr CDN（推荐，短、快） | `https://cdn.jsdelivr.net/gh/coolmoonyao/aptv-test@main/live.m3u` |
| GitHub Raw（兜底） | `https://raw.githubusercontent.com/coolmoonyao/aptv-test/main/live.m3u` |

在 APTV / TiviMate / IPTV 等 App 中粘贴上述任一地址即可，之后每天自动拉取更新。

---

## 过滤规则

| 规则 | 说明 |
|---|---|
| 白名单分组（整组保留） | 今日影视直播、咪视界直播、地方台直播、央卫视直播、4GTV直播 —— 这 5 组不参与关键词过滤、不限分辨率/响应时间，只要探测存活即保留 |
| 保留范围（其余分组） | 港澳台、美国、日本、韩国、央视(CCTV)、卫视、地方台 |
| 分辨率 | 其余分组仅保留 1080P（1920×1080）及以上 |
| 响应时间 | 其余分组 HTTP 首字节耗时 ≤ 1500 ms |
| 同名合并 | 同名节目合并为多链接备用源；分辨率降序，同分辨率按响应时间升序 |

> 说明：分辨率与响应时间通过本机 `ffprobe` 真实探测获得（源列表不含分辨率字段）。白名单分组仍会剔除死链（探测不到分辨率的条目）。

---

## 项目结构

```
aptv-test/
├── live.m3u          # 生成的最终文件（固定名，对外分发）
├── config.json       # 配置：源地址 + 关键词 + 阈值
├── fetcher.py        # 抓取 + 解析
├── prober.py         # ffprobe 探测
├── generator.py      # 过滤 + 合并排序 + 生成 M3U
├── pusher.py         # git 推送
├── main.py           # 总入口
├── api.py            # REST API
└── requirements.txt
```

## 快速开始

```bash
# 1. 安装依赖
brew install ffmpeg                    # 探测必需
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. 手动运行一次
python main.py

# 3. 启动 REST API（可选）
python api.py        # 默认 0.0.0.0:8000
```

## 配置（config.json）

- `sources`：直播源地址数组，支持**多个地址**。
- `user_agents`：候选 User-Agent 列表，抓取与探测阶段逐个轮询（排前面的优先）。不少源只认特定客户端 UA，如 `okHttp/Mod-1.5.0.0`。
- `referers`：候选 Referer 列表，`""` 表示不设 Referer。抓取阶段还会自动追加源站自身首页作为候选。
- `embed_extvlcopt`：是否在 `live.m3u` 中为每个流写入 `#EXTVLCOPT:http-user-agent / http-referrer`（VLC 系播放器按此头请求，解锁只认特定客户端的源）。
- `hit_headers_cache`：命中组合缓存文件名，记录每个流命中的 `(ua, referer)`，跨次运行复用。
- `keep_groups`：白名单分组数组（子串匹配 group-title）。命中的分组整组保留：不参与 include/exclude 关键词过滤，且豁免分辨率/响应时间门禁（仍剔除死链）。
- `include_keywords` / `exclude_keywords`：关键词数组，支持**多个关键词**，对「分组 + 名称」做包含/排除匹配（`keep_groups` 命中者不参与）。
- `min_width` / `min_height`：最低分辨率（默认 1920×1080）。
- `max_response_ms`：最大响应时间（默认 1000ms）。
- `concurrency`：探测并发数（默认 30）。
- `ffprobe_timeout_s`：单流探测超时（默认 10s）。
- `output_filename`：固定为 `live.m3u`（勿改，改则订阅地址失效）。
- `github_repo` / `github_branch`：推送目标仓库与分支。

## REST API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/config` | 查看完整配置 |
| PUT | `/api/config` | 整体更新配置（JSON body） |
| GET | `/api/sources` | 查看源地址列表 |
| POST | `/api/sources` | 新增源地址 `{"url": "..."}` |
| DELETE | `/api/sources/{idx}` | 删除源地址 |
| GET | `/api/keywords` | 查看关键词 |
| POST | `/api/keywords/include` | 新增保留关键词 `{"kw": "..."}` |
| DELETE | `/api/keywords/include/{idx}` | 删除保留关键词 |
| POST | `/api/keywords/exclude` | 新增排除关键词 `{"kw": "..."}` |
| DELETE | `/api/keywords/exclude/{idx}` | 删除排除关键词 |
| POST | `/api/run` | 手动触发一次抓取（后台执行） |
| GET | `/api/runs` | 运行历史日志 |
| GET | `/live.m3u` | 直接下载生成的 M3U |

## 每日定时

由 WorkBuddy 自动化任务每日定时运行 `python main.py`，完成后自动 `git push` 到本仓库。
