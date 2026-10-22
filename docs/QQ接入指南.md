# QQ 机器人接入指南（扫码登录怎么做、员工能不能直连）

> 适用版本：`0.10.49+`　　最后核对：2026-10-22（生产实测）
>
> 一句话结论：**扫码发生在 QQ 协议端（NapCat），不在 DogBuddy 里**。
> DogBuddy 只是一个**反向 WebSocket 服务端**，等 NapCat 拨进来；它自己没有 QQ 协议实现，
> 也没有二维码。所谓"一键"= 一次配置 + 一次扫码，之后长期在线。

---

## 1. 两段式架构（先理解这个，后面的步骤才有意义）

```
手机 QQ  ←——扫码登录——→  NapCat（QQ 协议端，独立进程）
                              │  反向 WebSocket（OneBot v11）
                              ▼
                    DogBuddy 网关 :8095  ←—— 白名单校验（L1）
                              │
                              ▼
                        管理员会话（每个 QQ 号一份独立上下文）
```

- **NapCat / Lagrange**：真正登录 QQ、收发消息的进程。它们提供二维码登录（自带 WebUI）。
- **DogBuddy**：`dogbuddy/gateway/adapters/onebot_v11.py` 是个 WebSocket **服务端**
  （`0.0.0.0:8095`），只认 OneBot v11 协议，不实现 QQ 协议、不内置任何协议库。
- 配置真相：`dogbuddy/config.py::GatewayConfig`（`enabled / host / port / access_token / whitelist / push_target`）。

## 2. 现在的生产状态（2026-10-22 实测）

| 项 | 值 |
|---|---|
| `/etc/dogbuddy/config.yaml` → `gateway.enabled` | **false**（还没开） |
| `gateway.port` | 8095（`0.0.0.0`，`ss -ltn` 目前无监听） |
| `gateway.access_token` | 空（**强烈建议设**，否则同网段任何人都能连） |
| 盒子上的 NapCat/Lagrange | **未安装**（`/opt` 下只有 dogbuddy） |
| 盒子上可用的浏览器 | 无（`/usr/bin/firefox` 只是 snap 空壳）——与 QQ 无关，但说明"沙盒"能力受限 |

## 3. 装 NapCat 并扫码（推荐放另一台机器）

盒子是 Android/PRoot 容器，跑 QQ NT 客户端风险高（缺库、易崩）。**推荐把 NapCat 跑在
同网段的一台 Windows/Linux 上**，反向 WS 连到盒子：

1. 下载 NapCat（<https://github.com/NapNeko/NapCatQQ/releases>）并解压。
2. 启动（Windows 双击 `launcher.bat`，Linux 用 `./napcat.sh` 或 `docker run`）。
   首次启动会要求登录 → **打开它的 WebUI**（默认 `http://127.0.0.1:6099`）→
   页面直接给一张二维码 → **手机 QQ 扫码**（建议用小号，见 §6 风险）。
3. 登录成功后，在 NapCat 的「网络配置」里加一个 **WebSocket 客户端（反向）**：

   ```
   URL:   ws://192.168.3.43:8095/
   令牌:  你的 gateway.access_token（要和 DogBuddy 侧一致）
   心跳:  30000 ms
   ```

   注意是**反向**：NapCat 主动连 DogBuddy，不是 DogBuddy 连 NapCat。
4. 对方在 DogBuddy 侧确认连上了：日志出现
   `OneBot client connected`（`dogbuddy.gateway.adapters.onebot_v11`）。

## 4. 打开 DogBuddy 网关（两种改法）

**A. 控制台（只能改一部分）**：设置 → 「网关与回报」→ 勾「启用消息网关」，填 `push_target`
（机器人主动汇报的落点，如 `{"type":"group","id":"123456"}`）。**端口/token/白名单的
连接参数不在这个卡片里**，要改配置或用环境变量。

**B. 直接改配置（推荐，改完重启）**：

```yaml
gateway:
  enabled: true
  host: 0.0.0.0
  port: 8095
  access_token: "改成一段随机串，与 NapCat 那边一致"   # 或用 DOGBUDDY_GATEWAY_TOKEN 注入
  whitelist: [123456789]        # 允许对话的 QQ 号（其余静默丢弃）
  push_target: {type: group, id: "987654321"}
```

```bash
# 重启（不要在跑任务时重启：会打断在跑的 run）
PID=$(curl -s http://127.0.0.1:8100/healthz | sed -E 's/.*"pid":([0-9]+).*/\1/'); kill $PID
sleep 8; curl -s http://127.0.0.1:8100/healthz
```

白名单也可以走控制台「设置 → 白名单」（增删 QQ + 备注 + 角色），`POST /api/settings/whitelist`。

## 5. 验证清单（三步，缺一步就是"机器人不回话"）

1. `ss -ltn | grep 8095` —— 有监听（= `enabled: true` 生效）
2. NapCat 侧「网络配置」显示**已连接**；DogBuddy 日志有 `OneBot client connected`
3. 用白名单 QQ **私聊**机器人发「你好」——管理员回复即通。不在白名单 → 日志
   `dropped message from non-whitelisted QQ xxx`（**故意静默**，机器上不回任何提示）

## 6. 风险与注意事项

- **封号风险**：用第三方 QQ 协议端（NapCat/Lagrange）属于非官方客户端，腾讯有封号先例。
  **务必用小号**，不要用主号。
- **群里会被"每句话"触发**：适配器没有 @ 过滤——群里任何白名单成员的每条消息都会跑一轮
  管理员逻辑（烧 token）。要么只用私聊，要么把群成员严格控制在小范围。
- **access_token 必须设**：`0.0.0.0:8095` 裸奔 = 同网段任何人都能以你的身份对话（还能间接
  指挥工位干活）。
- **一个 QQ 号 = 一份管理员会话**（`admin:<qq_id>`），多人白名单就是多份独立上下文，互不串。

## 7. 员工能不能直连 QQ？（现状：不能，只能经管理员）

当前路由是写死的：**所有白名单 QQ → 管理员**（`gateway/router.py` → `admin.handle_user_message`）。
`whitelist` 表里那列 `role` 只存不用（没有代码读它），也**没有** `qq_id → 工位` 的绑定。

所以"让员工接 QQ"目前只有一条路：**管理员在 QQ 里派活**——用户说「让前端把首页改一下」，
管理员调 `task_create` / `member_dispatch` 派给具体工位，结果再回到这个对话。

要真正做到"某 QQ 直接对话某员工"，需要加一层绑定（尚未实现，方案如下）：

1. `whitelist` 表加 `window_id`（或 `group_id`）列 + 迁移（schema 16）；
2. `GatewayConfig` 加 `routing: "admin" | "by_whitelist"`；
3. `router.route_in` 按 `event.user_id` 查绑定：命中 → `windows.dispatch(window_id, text)`，
   未命中 → 回落管理员；
4. 控制台白名单卡片加一个「绑定工位」下拉；
5. 回归：未绑定仍进管理员、绑定的回复落回原会话、绑定/解绑即时生效、工位不存在时 fail-closed。

评估：约半天工作量（含测试与部署）。**这一条等你拍板再做**——它会改变"谁有权指挥工位"的
安全边界，得先想清楚白名单里的人能不能直接驱使某个工位。

## 8. 排障对照表

| 现象 | 先查什么 |
|---|---|
| NapCat 连不上 | token 不一致 / URL 写成 `ws://…:8095` 少了结尾 `/` / 盒子防火墙 / `gateway.enabled` 还是 false |
| 连上了但不回话 | QQ 号不在 `whitelist`（日志有 `dropped message from non-whitelisted`） |
| 回话到错误的群 | `push_target` 与群号；群聊回包用 `session_id=group:<gid>`，私聊用 `private:<qq>` |
| 第一次扫码后掉线 | NapCat 掉线不会自动重登（取决于其配置）；DogBuddy 侧只需要 WS 重连，不用重启 |
| 想换号 | NapCat WebUI 退出登录 → 重新扫码；DogBuddy 侧只改白名单 |

## 9. 一句话操作顺序

NapCat 装好 → WebUI 扫码登录 → 加反向 WS 指向 `ws://192.168.3.43:8095/`（带 token）→
DogBuddy 配置 `enabled: true` + 设 `access_token` + 加白名单 QQ → 重启 → 私聊机器人说话。
