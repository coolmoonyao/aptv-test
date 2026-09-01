# 学习笔记：直播源的防盗链、User-Agent 与多 UA 解锁

> 整理自一次排查「静态直播源大量打不开 → 播放器填 `okHttp/Mod-1.5.0.0` 后很多能播」的过程。
> 目标是讲清「为什么」以及「怎么利用这一点多捞频道」，供以后复用。

---

## 1. 先澄清一个最绕的点：User-Agent 为什么是一串字符，而不是 IP

中文里「代理」撞了两个完全不同的英文词，别混：

| 中文 | 英文 | 本质 | 是什么 |
|---|---|---|---|
| 用户代理 | **User-Agent** | HTTP 请求头里的一个**字段** | 一行字符串，向服务器自我介绍「我是哪个软件/设备」 |
| 代理服务器 | **Proxy** | 一台**有 IP 的机器** | 帮你转发流量的中间人 |

- `User-Agent: okHttp/Mod-1.5.0.0` —— 这是「名片」，从来就不是地址。
- `IP 1.2.3.4 / 5.6.7.8` 带地址的 —— 那才是 Proxy（代理服务器）。

**一句话**：User-Agent 从诞生起就是字符串字段，只是中文翻译撞了「代理」两个字而已。

---

## 2. 这里发生了什么：直播源的「用户代理白名单」防盗链

很多直播源（尤其是盗转 / 聚合 OTT 源）的服务器或 CDN 网关，会检查请求头里的 `User-Agent` 是谁，维护一张白名单：

> 只有「自己人」客户端才放行，别的 UA 一律 403 / 404 / 空流。

- `okHttp` = 安卓生态最常用的网络库（Square 公司的 OkHttp）。
- `Mod-1.5.0.0` = 某个魔改版（Mod）的版本号。

也就是说，这串字符是**某个电视直播 App / 直播源采集工具的客户端标识**，那些源的白名单里恰好放行了它。播放器把 UA 从默认的 `VLC/...` 或 `Lavf/...` 换成它，等于「换了张被认可的名片」，服务器一看是自己人，就给流了。

**结论**：UA 不是万能钥匙，但它是很多「打不开」的源的第一道门禁，换对了就能过。

---

## 3. 直播源的三道防盗链门槛（这是核心模型）

| 门槛 | 伪装的是 | 举例 |
|---|---|---|
| User-Agent | 「我是什么客户端」 | `okHttp/Mod-1.5.0.0` |
| Referer | 「我从哪个页面来」 | `https://live.445569.xyz/` |
| token / 签名 | 「我有没有票」 | TVB 的 Akamai `hdnea`、iptv807 的 `sign=时间戳-签名` |

- UA、Referer 是**静态可写死**的字符串，放对就能过。
- token/签名是**动态时效**的，几分钟~几小时就过期、还常绑定 IP，**写不进静态 M3U**。

---

## 4. HLS 直播源的三类形态（决定能不能写成固定 M3U）

| 类型 | 特征 | 能否写死固定 URL |
|---|---|---|
| 静态 Master | 多数无 token，URL 固定 | ✅ 可固定 |
| 动态 token | YouTube 6h 过期、TVB `hdnea` 绑 IP + 小时刷新、iptv807 `sign` 几分钟过期 | ❌ 只能实时取 |
| 切片 (.ts) | 播放器实时追，几秒一换 | ❌ 不适用 |

之前探索过的几条「看似有固定 URL 实则不行」的结论：

- **YouTube 直播**：音视频分离（无合一 HLS）、URL 约 6 小时过期、需代理。
- **TVB 直播**（`news.tvb.com`）：接口 `POST /app/public/live/stream/{频道}` 返回 `stream_url`，Akamai `hdnea` 签名含 `ip=`（绑定调用方公网 IP）+ `exp`（签 30 天但服务端 `expire_time`≈1h、`refresh_interval=3600`），只有 720P。**不能跨网络共享，只能本机/同局域网实时看。**
- **iptv807.com**：播放页 JS 解密（base64+XOR+反转）→ `p.iptv200.com/play.php` 302 → `t1/t2/t3.iptv200.com:8443/live/xxx.m3u8?sign=...`，流服务器在美国被墙、sign 几分钟过期，不可做静态 M3U。
- **Cloudflare 免费版**：ToS 2.8 禁视频、大陆无节点、不缓存 `.m3u8/.ts`。

---

## 5. 怎么利用「多 UA」多捞频道（本次代码改造的核心思路）

原理：**UA 负责「放进来」，探测负责「留下好的」**，两者配合才是完整闭环。

改动点（已落地到 `aptv-test`）：

1. **UA 做成候选列表**，不再写死一个值：
   ```json
   "user_agents": [
     "okHttp/Mod-1.5.0.0",
     "Mozilla/5.0 (Macintosh; ...) Chrome/122.0.0.0 Safari/537.36",
     "Mozilla/5.0 (Linux; Android 13; ...) Chrome/120.0.0.0 Mobile Safari/537.36",
     "Dart/3.2.0 (dart:io)"
   ]
   ```
2. **每个源 / 每个流用多组合重试**：抓取（`fetcher.py`）和探测（`prober.py`）都依次尝试 UA×Referer，命中即停。
3. **Referer 不搞一刀切**：候选列表含「空(不设)」+ 源站自身首页 + 已知值。
4. **记录命中组合**：`prober.py` 把每个流命中的 `(ua, referer)` 存进 `hit_headers.json`，下次优先复用，省去重复遍历。
5. **生成 `#EXTVLCOPT` 头**：在 `live.m3u` 里为每个流写：
   ```
   #EXTVLCOPT:http-user-agent=okHttp/Mod-1.5.0.0
   #EXTVLCOPT:http-referrer=https://live.445569.xyz/
   ```
   VLC 及多数 IPTV 播放器会按此头请求，从而**每个频道用各自正确的 UA/Referer**。

**注意性能**：死链会遍历全部候选组合，故把「最可能命中的组合」放列表最前（`okHttp/Mod-1.5.0.0` + 空 Referer），常见情况下每个流只做 1 次 HTTP 探测 + 1 次 ffprobe。

---

## 6. 手动验证一条源「要什么 UA/Referer」的步骤

```bash
# 换 UA 试拉一条 m3u8，看哪个返回 200
curl -s -o /dev/null -w "%{http_code}\n" \
  -A "okHttp/Mod-1.5.0.0" \
  "http://某流/index.m3u8"

# 带 Referer 再试
curl -s -o /dev/null -w "%{http_code}\n" \
  -A "okHttp/Mod-1.5.0.0" -e "https://源站首页/" \
  "http://某流/index.m3u8"

# ffprobe 看真实分辨率（探测阶段用的就是这个）
ffprobe -v error -user_agent "okHttp/Mod-1.5.0.0" \
  -select_streams v:0 -show_entries stream=width,height \
  -of default=noprint_wrappers=1:nokey=1 \
  "http://某流/index.m3u8"
```

> 经验：能播但打不开，先试 UA（换 `okHttp/Mod-1.5.0.0`），再试 Referer（源站域名），最后才怀疑是时效 token（那基本没救）。

---

## 7. 一句话总结

- 直播源「打不开」多数不是真死了，而是**防盗链**：UA、Referer、token 三道门。
- `okHttp/Mod-1.5.0.0` 是某类采集工具的白名单 UA，换对就「假死复活」。
- 用**多 UA×多 Referer 轮询 + 记录命中组合 + EXTVLCOPT 按频道下发**，能把更多源纳入流水线，再由分辨率 + 首字节响应时间筛出真正低延迟稳定的频道。
- 但能被 UA 解锁的源很多本身带时效 token、线路不稳，**UA 只负责放进来，探测才负责留好的**。
