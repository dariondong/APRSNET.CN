# APRSNETCN OA 后端

活动页原本读一份静态的 `oa/roster.json`，名单靠人往里写。这个后端把那份名单接上
APRS-IS：收到谁发出的 `CQ CNNET` 与 `U CNNET`，就把哪一台并进当轮名单，主控在
操作台上点名、回 ACK、删改，页面上立刻就是最新的那一份。

只用 Python 3 标准库；签到表、网格表与每台的奖状那几张 PNG 用 Pillow 画（没装时那几个接口回 503，别的照常）。

## 一条报文怎么变成一行名单

1. 连 APRS-IS（只收，`pass -1`），按区域过滤中国上空的报文，另外单独收发给轮值呼号的报文；
   过滤串里那段 `g/呼号` 跟着当轮主控的呼号走（换了主控自己重连一次），区域那几段照旧；
2. 每行拆出呼号、路径、位置、网格、文字，文字里认出 `CQ CNNET`（喊到）、`U CNNET`（确认），
   以及不发 CQ 的 `CNNET …`（同样算喊到）；
3. 喊到的台站进当轮名单，状态 `called`；回过 U 的升到 `confirmed`；主控回过 ACK 的记 `acked`；
4. 有 `OA_TX_PASSCODE` 时，谁喊到就自动回一条 `ACK`（一台一轮只回一次），回过的改 `acked`；
   名单里那一台的「消息」是自己写在签到后面的那句话，呼号与网格不算；
5. 发给轮值呼号、又不是签到那两步的报文（闲聊、写错格式、少写了 CQ），回一句怎么做；
6. 名单从 `stations` 表导出，格式和页面上 `roster.js` 读的那份 `roster.json` 一模一样。

射频还是网络：路径里有真的中继台呼号、有 WIDE 这类别名、有 DMR 这类模式标记，就按射频，
中继按经过顺序写（别名与模式标记不写进中继）。网关从射频转上来的 `qAR` / `qAr` 算射频，
`qAO` / `qAo` 只在没带 `TCPIP*` 且后面不跟本台呼号时算射频；`qAC` / `qAX` / `qAU` / `qAS` /
`qAZ` / `qAI` 与 `TCPIP*` / `TCPXX*` 都算网络。q 构造后面的网关与服务器不算中继。
名单里那一行的路径按喊到那一下算，后面换了链路再发也不改它。

网格先按报文里的位置算；没有位置就用文字里写的那个；主控台里都能改。

## 接口

| 路径 | 说明 |
| --- | --- |
| `GET /` | 活动页（`oa/index.html`），页面上的名单自动指向 `/api/roster` |
| `GET /oa/data.html` | 数据面板，同上 |
| `GET /en/` | 英文活动页 |
| `GET /api/roster` | 名单，与静态 `roster.json` 同格式，顶层多一份 `meta`（页面抬头那两行字） |
| `GET /api/board.png` | 签到表那张 PNG，`?date=2026-10-17` 指定哪一轮，`?lang=en` 要英文 |
| `GET /api/grid.png` | 网格表那张 PNG，参数同上；旧的 `/api/points.png` 302 到这里 |
| `GET /api/cert.png` | 一台一份的参与证书 PNG，`?call=BG7LZQ-2` 必写，`?date=` 与 `?lang=` 同上 |
| `GET /api/health` | 连接状态、收到多少行、当轮、报文数 |
| `GET /admin` | 主控台（要口令） |
| `GET /api/admin/state` | 主控台读的一切：状态、轮次、本轮数字、名单、流水 |
| `GET /api/admin/export` | 下载 roster.json；`?format=csv&what=stations` 或 `what=packets` 下载两份 CSV |
| `POST /api/admin/round` | 开一轮：`{"date":"2026-10-17","duty":"BG7LZQ-2"}` |
| `POST /api/admin/settings` | 切全自动：`{"auto":true}` / `{"auto":false}`；同一处写页面抬头那两行字：`{"subtitle_zh":"…","subtitle_en":"…","op_name":"…"}`，空串就是抹掉 |
| `POST /api/admin/close` | 结束一轮，并把这一轮归档 |
| `POST /api/admin/archive` | 单独归档一轮：`{"date":"2026-10-17"}` |
| `POST /api/admin/duty` | 换轮值呼号 |
| `POST /api/admin/station` | 加或改一台：呼号、网格、路径、中继、时间、状态 |
| `POST /api/admin/station/delete` | 删一台 |
| `POST /api/admin/ack` | 回 ACK：记下报文并标 `acked`，配了口令才真发 |
| `POST /api/inject` | 拿一行报文试一下：`{"line":"BG7XXX-9>APRS:…"}` |

管理接口认 `X-OA-Token` 头、`token=` 参数或 `oa_token` cookie 里的口令，就是配置里的
`OA_ADMIN_TOKEN`。没设口令时管理接口一律拒绝，不会敞着。

## 出图

数据面板上那两个按钮（签到表 PNG / 网格表 PNG）点的是 `/api/board.png` 与 `/api/grid.png`。
签到表一台一行，列是呼号、网格、路径、状态、时间；网格图画的是世界网格地图，宽 1944 像素，
底图是仓库里 `oa/world.png`（Natural Earth 110m 陆地，公有领域，1800×900 每度 5 像素），
180×180 个 4 位格子都在，有签到的格子点亮，下面跟着每个点亮的格子里的呼号。网格按前 4 位算，
`OL62AA` 与 `OL62CQ` 是同一格 `OL62`。两张图的图底都写着哪一轮、轮值呼号与落款时间，页面上的按钮自己带着
`?date=` 与 `?lang=en`，点了就是当前选中那一轮。

名单里每一台还有自己那份参与证书：`/api/cert.png?call=BG7LZQ-2`。图上是呼号、哪一轮、
网格 / 路径 / 时间，脚上一个编号（呼号与轮次算出来的八位十六进制，同一台同一轮永远一样）。
呼号先按整个找，找不到再按去掉 SSID 的呼号找，只有唯一一台时才认；没带呼号回 400，
这一轮里没这台回 404。名单里每台那一行的右端有一个「奖状」链接，点的是同一台同一轮的这份证书。

- 出图要 Pillow，没装回 503，页面别的照常；
- 中文字体先找 `/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc`，找不到就退回 Pillow 自带的位图字；
- `OA_TEST_NOTE`：写在两张图副标题后面的一句小字（红），本机写着「测试轮次」；空着就不写。
  英文图单独看 `OA_TEST_NOTE_EN`，空着就用 `OA_TEST_NOTE` 那一句。

## 发报文

回 ACK 要轮值呼号的报文口令，写进 `OA_TX_PASSCODE` 才发得出去（口令按呼号算，站点上
`passcode.html` 能核对）。口令对了登录回一句 `verified`，不对会被服务器当只收处理。

- `OA_AUTO_ACK=1`：发给轮值台的每一条报文都回一条机器 ack，同一个编号只回一次。带编号的回
  `ack<编号>`，没编号的没得引，回配的那句 `OA_ACK_TEXT`，一台一轮一次。不在名单里的也回；
- `OA_ACK_MAX_PER_MINUTE=30`：一分钟最多发这么多条，防着热闹起来往外刷。发送用的是
  一条登录好的长连接，有名额就立刻发，超了名的那几条隔一会儿就轮到，不丢；
- `OA_ACK_TEXT=ACK`：对方那条没写编号时回的那句话（没编号就引不了 `ack<编号>`）；
- `OA_CQ_REPLY=Got {grid}, you are #{rank} on the roster. 73`：回完确认再跟的一句人话（`{grid}` 是他报的
  网格，`{rank}` 是那一台在当轮名单上排第几）。确认是给机器看的，这一句是给人的；回话一律英文，
  中文到多数台与手持机上是乱码。同一台 30 秒内只跟一句，手机把同一条重发两遍也只回一次，
  不要就留空；
- `OA_TNX_TEXT=73 TNX!`：一台走完 `U CNNET` 回的一句 73，一台一轮一份；
- 对方那条末尾带编号（`…{12`）时，回的是 `ack12`，不是那句 ACK。我们发出去的每一条末尾
  也带 `{编号`，对方客户端回 `ack<编号>`，主控台的发件箱就会多一句「对方已确认」；
- `OA_BAD_TEXT=INVALID CHECKIN, RESEND: CQ CNNET <CALL> <GRID>`：签到那行没写出网格时回的那句话，
  同一台同一轮只回一条。名单里的网格就从这行报的那句话里读（读不到网格的不进名单）；
- `OA_HELP_TEXT=APRSNETCN OA: CQ CNNET <CALL> <GRID>, THEN U CNNET`：发给轮值呼号、又不是
  签到那两步时回的那句话，同样一台一轮只回一条；
- 发出去的每一条都记在 `outbox` 表，主控台的「发件箱」里看得到，发失败的带原因；
- 没配口令时，点「回 ACK」只把报文记下来并复制，主控自己发。

## 轮次与归档

- `OA_AUTO_ROUND=1`：北京时间一到周六就自己开当天的轮，轮值呼号取 `OA_ROUND_DUTY`；周六过完
  自动收轮并归档。关掉以后整轮都得主控自己开、自己收。
- `OA_ARCHIVE_DIR`：归档写到哪，默认在数据目录下的 `archive/`。一轮收档写下三份，
  `round-<日期>.json`（轮次、数字、名单、流水全在里面）、`round-<日期>-stations.csv`、
  `round-<日期>-packets.csv`（带 BOM，Excel 直接打开不乱码）。
- 主控台每 10 秒自己刷一次，顶部那条「本轮」是台站数、已确认、已回 ACK、只喊到、射频、网络、
  中继、本轮报文、最后一条时间；再下面是「待办」，回过的还在等 `U CNNET`，没回的还等着回 ACK，
  每行能直接点。

## 全自动模式

`OA_AUTO=1`（默认开着）就是全自动：一轮走完不用人守。

- 北京时间一到周六就自己开当天的轮，周六过完自己收轮并归档；
- 有人喊到就自己回一条 ACK；
- 没回成的（发不出去或者连不上）过一分钟自己补，一台一轮最多补 `OA_ACK_RETRY` 次
  （默认 3，设 0 就是不补）；
- 名单有变化就写一份到 `OA_PUBLISH_ROSTER`（配了才写）；
- 主控台的「待办」只列还没回上 ACK 的，等对方回 `U CNNET` 的不再占着。

主控台顶上那个「全自动」勾选框能当场开关，开关写在库里的 `settings` 表，重启也记得，不用改
env 再重启。关掉以后退回手动：开轮、收轮、回 ACK 都得自己在台面上点。

## 页面上那两行字

副标题与主控昵称由主控在台面「页面上那两行字」那块填，写在库里的 `settings` 表，跟全自动开关
同一处。副标题中英各一条（`subtitle_zh` / `subtitle_en`），页面按 `<html lang>` 取自己那一条；
昵称一条（`op_name`），写成「 · 昵称」跟在轮值呼号后面。三件都跟名单一起从 `/api/roster` 的
`meta` 发出去，主控填完用户刷新就换，不用重新发布页面。留空就是这块不显示。这两行字不随周换，
换一轮也不用重填。

## 配置

复制 `oa-server.env.example` 改成自己的，用 `--env` 指过去，或者直接给环境变量。
`OA_ADMIN_TOKEN` 必填，`OA_TX_PASSCODE` 空着就是只记不发。

## 跑起来

```sh
python3 oa_server.py --env oa-server.env serve      # 接收 + 页面 + 主控台
python3 oa_server.py --env oa-server.env export out.json
python3 oa_server.py --env oa-server.env feed 几行报文.txt
python3 oa_server.py --env oa-server.env listen 30  # 只连 APRS-IS 看一眼
python3 selftest.py                                 # 不连网的自检
```

systemd 单元见 `oa-server.service`，路径按本机改成对的。

## 两件要知道的事

- 只听得见上了 APRS-IS 的报文。完全走射频、没有网关收到的台站，主控自己在台上抄到了，
  就在主控台的「手动加一台」里补一行。
- 自动回 ACK 是拿轮值呼号的名义发到 APRS-IS 上的真报文。不想让它自动发，把 `OA_AUTO_ACK` 设成 0，
  改成主控在台面上一条条点。
