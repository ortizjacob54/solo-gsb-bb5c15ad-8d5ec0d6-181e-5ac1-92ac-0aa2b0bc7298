# 港口潮汐通航窗口与单向航道预约 API

基于 **Python + FastAPI + SQLite** 的港口潮汐通航窗口计算与单向航道预约服务。

- 录入港口的**潮高预报点**与**航道基准水深**；
- 船舶提交**吃水**、**安全富余**和**正数通航时长**；
- 相邻预报点间按**线性插值**求潮高，仅在**预报覆盖时段**内计算
  `水深 = 基准水深 + 潮高 ≥ 吃水 + 安全富余` 的连续达标窗口（**端点恰好达标也纳入**）；
- 每个达标窗口的**结束时刻减去通航时长**得到**可出发时段**，不足所需时长的窗口舍弃；
- 返回**全部可出发时段**和**最早出发时刻**；
- 预报点更正（同一时刻重复提交即覆盖）后，查询自动按最新数据实时重算；
- **单向航道预约**：在请求方允许的出发时刻范围内，按最新预报安排**最早**出发时刻，
  使整个通航过程不与该港口现有**有效预约**重叠（两船**首尾相接允许**）；
  无可用时刻返回 **409** 且不写入；同港口并发创建原子地“检查 + 落库”，杜绝双重占用；
- 已落库预约记录的是排定的确定时刻，**预报更正不追溯调整或取消已有预约**；
- **预约改期**：`PATCH /reservations/{id}/schedule` 只提交**新的允许出发起止时刻**，
  沿用原预约的吃水、安全富余与通航时长，以**请求时最新预报**重算可出发时段；
  在同港有效预约中**排除该预约自身原占用**，选出新范围内不与其他占用重叠的
  **最早**出发时刻（首尾相接允许）；仅**有效**预约可改，排程检查与更新在
  **同一写事务**内完成；无可行时刻返回 **409** 并**保留原排程与允许范围**；
  成功时预约 ID、船名与创建时间不变；
- **预约水深复核**：`GET /reservations/{id}/depth-audit` 按**查询时最新预报**复算
  预约排定通航区间的水深，合并返回欠深时段及各段**最大欠深**；预报首末点未覆盖
  整个通航区间时明确返回未覆盖时段且**不判为安全**；复核**只读**，不改动预约或原排程。
- **最大可通航吃水查询**：`POST /ports/{id}/max-draft` 提交**安全富余**、
  **正数通航分钟数**与**允许出发起止时刻**，按查询时最新预报与线性插值规则求
  允许范围内全程水深达标的**最大非负吃水**（按 **0.01 米向下取整**）；同一吃水
  有多个可行时刻时返回**最早出发**及**通航结束时刻**；只在预报覆盖的连续时段内
  求解，范围内无可行的 0 米吃水时返回 **409**。
- **预报影响预演**：`POST /ports/{id}/forecast-impact-preview` 提交一批**拟录入**的
  潮高预报点，与库存预报按 UTC 规则合并（同一时刻按提交顺序**后者覆盖前者**）形成
  **临时预报**；基于一次读取的当前预报与该港口**有效预约**，分别用当前与临时预报
  按线性插值、水深复核规则判定每条预约排定通航区间是否**全程达标**（未被预报覆盖
  的区间判为不达标）；仅返回**达标状态变化**的预约（按出发时刻升序），预演**只读**，
  不写入预报、不改动预约。
- **实测潮位校准窗口**：`PUT /ports/{id}/tide-observations` 批量写入**实测潮高**
  （同港同一 UTC 时刻重复写入**覆盖旧值**）；`POST /ports/{id}/calibrated-windows`
  沿用吃水、安全富余、通航时长请求与窗口响应——查询时用**最新预报线性插值**得到
  观测时刻的预报潮高，**残差 = 实测 − 预报**；仅采用**预报覆盖范围内**的观测，以
  预报首末时刻的**零残差**为边界锚点（同刻有观测则**观测优先**），锚点间**线性
  插值残差**并叠加原预报；把预报点与有效观测点时刻合并为折点后**复用现有通航窗口
  计算**；无有效观测时等同原窗口。校准查询**只读**，不改原预报和预约。
- **实测潮高异常点质检**：`GET /ports/{id}/observation-anomalies` 对落在**最新预报
  覆盖范围内**的每条实测记录，按预报**线性插值**求残差；以该时刻**前后各 120 分钟**
  内其他有效实测残差的**中位数**为局部基线（邻点**至少 3 个**才评估），局部
  **MAD** 为邻点残差与基线差的绝对值中位数，异常阈值取
  **max(0.3 米, 3×1.4826×MAD)**；仅返回残差偏离基线**严格超过阈值**的点
  （含时刻、实测潮高、残差、基线与偏离量，按时刻升序）；预报不足两点或邻点不足
  返回空列表；质检**只读**，不影响校准窗口及预约。
- **可预约时段查询**：`POST /ports/{id}/bookable-windows` 提交**吃水**、**安全富余**、
  **正数通航分钟数**与**允许出发起止时刻**；先按**最新预报**复用潮汐可出发窗口，
  再扣除同港**有效预约**的占用——每条预约按请求通航时长换算为**禁止出发开区间**
  （占用起点 − 通航时长, 占用终点），与既有通航**首尾相接允许**（端点保留），
  **零长度但可用的单点出发时刻**同样保留；返回允许范围内**全部可预约出发时段**
  与**最早出发时刻**（按时间升序）；**已取消预约不占用**，查询**只读**。
- **桥梁净空通航窗口**：`PUT /ports/{id}/bridge` 为港口配置**一处桥梁**的
  **梁底高程**（相对潮高基准面，米；重复提交即覆盖更正），`GET /ports/{id}/bridge`
  查询配置；`POST /ports/{id}/bridge-windows` 提交**吃水**、**水深富余**、
  **船舶水面以上高度**、**净空富余**与**正数通航分钟数**，按**最新潮高预报**
  逐段线性插值，同时要求 `基准水深 + 潮高 ≥ 吃水 + 水深富余` 且
  `梁底高程 − 潮高 ≥ 船舶水面以上高度 + 净空富余`（**等号可通行**），求两条件的
  **连续交集**（**相接区间合并**）后减去通航时长，返回**全部可出发时段**与
  **最早出发时刻**；仅在**预报覆盖范围**内计算，预报更正后实时重算；
  港口或桥梁配置不存在时返回 **404**。
- **港口潮位预警**：`PUT /ports/{id}/tide-alert-rule` 为港口配置**一条**潮位
  预警规则（**高潮或低潮方向**、**触发潮高**、**解除潮高**与**最短持续分钟**；
  高潮的解除阈值须**低于**触发阈值，低潮反之，时长非负；重复提交即覆盖），
  `GET /ports/{id}/tide-alert-rule` 查询规则；`GET /ports/{id}/tide-alerts`
  按**最新预报**相邻点线性插值，仅在**预报覆盖区间**内扫描：首点已达触发
  阈值则**从首点预警**，否则于**首次达到触发阈值的交点**进入，**达到解除
  阈值的交点**结束（交点作为边界）；覆盖结束仍未解除标记**未闭合**；仅返回
  持续达到最短分钟的时段，按起点排序，给出起止时刻、未闭合标记与
  **期间极值潮高**；预报不足两点返回空列表；查询**只读**，预报更正后实时重算。

## 快速开始（单条命令）

```bash
docker compose up --build
```

该命令自动完成：依赖安装 → SQLite 数据库初始化（服务启动时幂等建表）→ 启动服务。

启动后访问：

- **API 地址**：http://localhost:8000
- **交互式文档（Swagger UI）**：http://localhost:8000/docs
- **健康检查**：http://localhost:8000/health

停止：`docker compose down`（加 `-v` 可同时删除数据卷）。

## 配置

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| 服务端口 | `8000` | 修改 `docker-compose.yml` 中 `ports` 左侧的宿主机端口即可 |
| `DATABASE_PATH` | `/data/tide.db`（容器内）/ `tide.db`（本地） | SQLite 数据库文件路径，通过环境变量配置 |
| 数据持久化 | 命名卷 `tide-data` → `/data` | 容器重建后数据保留 |

时间约定：所有时刻使用 ISO 8601；不带时区信息时按 **UTC** 处理，响应统一返回 UTC 时刻。
长度单位为米，时长单位为分钟。

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/ports` | 录入港口（名称 + 航道基准水深） |
| `GET` | `/ports` / `/ports/{id}` | 查询港口 |
| `PATCH` | `/ports/{id}` | 更正港口名称 / 基准水深 |
| `PUT` | `/ports/{id}/tide-forecasts` | 批量录入/更正潮高预报点（同一时刻重复提交即覆盖） |
| `GET` | `/ports/{id}/tide-forecasts` | 查看当前预报点 |
| `PUT` | `/ports/{id}/tide-observations` | 批量写入实测潮高点（同港同一 UTC 时刻重复写入覆盖旧值） |
| `GET` | `/ports/{id}/tide-observations` | 查看已写入的实测潮高点 |
| `GET` | `/ports/{id}/observation-anomalies` | 实测潮高异常点质检：邻点中位数基线 + 局部 MAD 阈值（只读） |
| `POST` | `/ports/{id}/windows` | 提交吃水、安全富余、通航时长，计算可出发时段 |
| `POST` | `/ports/{id}/calibrated-windows` | 实测潮位校准窗口：请求/响应同 `/windows`，按实测残差校准后计算（只读） |
| `PUT` | `/ports/{id}/bridge` | 设置/更正桥梁梁底高程（每港一处，相对潮高基准面，重复提交即覆盖） |
| `GET` | `/ports/{id}/bridge` | 查询桥梁净空配置（港口或桥梁配置不存在返回 404） |
| `POST` | `/ports/{id}/bridge-windows` | 桥梁净空通航窗口：水深 + 净空双条件连续交集的可出发时段（只读） |
| `PUT` | `/ports/{id}/tide-alert-rule` | 设置/更正潮位预警规则（每港一条：方向、触发/解除潮高、最短持续分钟） |
| `GET` | `/ports/{id}/tide-alert-rule` | 查询潮位预警规则（港口或规则不存在返回 404） |
| `GET` | `/ports/{id}/tide-alerts` | 潮位预警时段：按最新预报扫描触发/解除时段、未闭合标记与期间极值潮高（只读） |
| `POST` | `/ports/{id}/max-draft` | 提交安全富余、通航时长、允许出发起止，求最大可通航吃水 |
| `POST` | `/ports/{id}/reservations` | 创建单向航道预约（自动排最早出发时刻） |
| `POST` | `/ports/{id}/bookable-windows` | 可预约时段查询：扣除有效预约占用后的全部可预约出发时段（只读） |
| `GET` | `/ports/{id}/reservations` | 按港口列出**有效**预约（按出发时刻升序） |
| `DELETE` | `/reservations/{id}` | 取消预约（释放航道占用） |
| `PATCH` | `/reservations/{id}/schedule` | 预约改期：提交新的允许出发起止，按最新预报重排最早可行时刻 |
| `GET` | `/reservations/{id}/depth-audit` | 预约水深复核：按最新预报复算排定通航区间水深（只读） |
| `POST` | `/ports/{id}/forecast-impact-preview` | 预报影响预演：拟录入预报点对有效预约达标状态的影响（只读） |

### 完整示例

```bash
# 1. 录入港口：航道基准水深 4.5 米
curl -X POST http://localhost:8000/ports \
  -H "Content-Type: application/json" \
  -d '{"name": "示例港", "reference_depth": 4.5}'

# 2. 录入潮高预报点（每 3 小时一个点）
curl -X PUT http://localhost:8000/ports/1/tide-forecasts \
  -H "Content-Type: application/json" \
  -d '{"points": [
        {"forecast_time": "2026-09-24T00:00:00Z", "tide_height": 0.5},
        {"forecast_time": "2026-09-24T03:00:00Z", "tide_height": 3.0},
        {"forecast_time": "2026-09-24T06:00:00Z", "tide_height": 0.5}
      ]}'

# 3. 船舶查询：吃水 5.0 米，安全富余 0.5 米，需要 60 分钟连续通航
curl -X POST http://localhost:8000/ports/1/windows \
  -H "Content-Type: application/json" \
  -d '{"draft": 5.0, "safety_margin": 0.5, "duration_minutes": 60}'
```

响应（所需水深 = 5.0 + 0.5 = 5.5 米；水深 5.0→7.5→5.0，线性插值得达标窗口
`[00:36, 05:24]`，减去 60 分钟通航时长）：

```json
{
  "port_id": 1,
  "required_depth": 5.5,
  "duration_minutes": 60.0,
  "earliest_departure": "2026-09-24T00:36:00+00:00",
  "windows": [
    {"start": "2026-09-24T00:36:00+00:00", "end": "2026-09-24T04:24:00+00:00"}
  ]
}
```

```bash
# 4. 更正 03:00 的预报点（3.0 → 1.0），随后查询自动按最新数据重算
curl -X PUT http://localhost:8000/ports/1/tide-forecasts \
  -H "Content-Type: application/json" \
  -d '{"points": [{"forecast_time": "2026-09-24T03:00:00Z", "tide_height": 1.0}]}'

curl -X POST http://localhost:8000/ports/1/windows \
  -H "Content-Type: application/json" \
  -d '{"draft": 5.0, "safety_margin": 0.5, "duration_minutes": 60}'
# → {"windows": [], "earliest_departure": null, ...}（峰值水深仅 5.5，窗口长度不足 60 分钟）
```

### 参数校验

- `duration_minutes` 必须为**正数**（`> 0`），否则返回 422；
- `draft`、`safety_margin` 必须为非负数；
- 预报点不足 2 个（无插值区间）或全程不达标时，返回空窗口列表与 `earliest_departure: null`。

## 单向航道预约

创建预约时提交：船名、吃水、安全富余、通航分钟数，以及**允许出发的起止时刻**
（`allowed_departure_from` ≤ `allowed_departure_to`，含端点；均按上面的 UTC 规则解释）。

排程规则：

1. 基于该港口**最新预报**计算潮汐可出发时段（同 `/windows`）；
2. 与请求的允许出发范围取交集；
3. 在其中选择**最早**的出发时刻，使整个通航区间 `[出发, 出发 + 通航时长]`
   不与该港口任何**有效预约**重叠——两船**首尾相接允许**
   （上一条 01:00 结束，下一条 01:00 出发合法）；
4. 无可用时刻返回 **409 Conflict** 且**不写入**任何数据。

同港口并发创建：服务先按港口在进程内串行化，再于 SQLite `BEGIN IMMEDIATE`
写事务中原子地完成“检查最新预报与现有预约 → 插入”，并设置 `busy_timeout`
让并发请求排队等待，因此不会产生双重占用。

```bash
# 沿用上面的示例港：可出发时段 [00:36, 04:24]，甲船申请 00:00–04:24 出发
curl -X POST http://localhost:8000/ports/1/reservations \
  -H "Content-Type: application/json" \
  -d '{"ship_name": "甲船",
       "draft": 5.0,
       "safety_margin": 0.5,
       "duration_minutes": 60,
       "allowed_departure_from": "2026-09-24T00:00:00Z",
       "allowed_departure_to":   "2026-09-24T04:24:00Z"}'
```

响应（`201 Created`；潮汐最早 00:36 可出发，排定 00:36–01:36）：

```json
{
  "id": 1,
  "port_id": 1,
  "ship_name": "甲船",
  "draft": 5.0,
  "safety_margin": 0.5,
  "duration_minutes": 60.0,
  "allowed_departure_from": "2026-09-24T00:00:00+00:00",
  "allowed_departure_to": "2026-09-24T04:24:00+00:00",
  "departure_start": "2026-09-24T00:36:00+00:00",
  "departure_end": "2026-09-24T01:36:00+00:00",
  "status": "active",
  "created_at": "2026-09-24T10:00:00.000000+00:00",
  "canceled_at": null
}
```

```bash
# 乙船只允许在 01:30 前出发：01:30 出发会在 [01:30,02:30] 与甲船重叠 → 409
curl -i -X POST http://localhost:8000/ports/1/reservations \
  -H "Content-Type: application/json" \
  -d '{"ship_name": "乙船",
       "draft": 5.0, "safety_margin": 0.5, "duration_minutes": 60,
       "allowed_departure_from": "2026-09-24T00:00:00Z",
       "allowed_departure_to":   "2026-09-24T01:30:00Z"}'
# HTTP/1.1 409 Conflict
# {"detail":"允许出发范围内无可用时刻（潮高不足或与现有预约冲突）"}

# 丙船允许到 04:24：最早可紧随甲船 01:36 出发（首尾相接）
curl -X POST http://localhost:8000/ports/1/reservations \
  -H "Content-Type: application/json" \
  -d '{"ship_name": "丙船",
       "draft": 5.0, "safety_margin": 0.5, "duration_minutes": 60,
       "allowed_departure_from": "2026-09-24T00:00:00Z",
       "allowed_departure_to":   "2026-09-24T04:24:00Z"}'
# → departure_start 01:36，departure_end 02:36

# 列出该港口全部有效预约（已取消的不在其中）
curl http://localhost:8000/ports/1/reservations

# 取消预约 1：立即释放占用；取消未知预约 404，重复取消 409
curl -X DELETE http://localhost:8000/reservations/1
```

说明：

- **预报更正不追溯**：预约创建后即固定 `departure_start/end`；之后更正预报点，
  已有预约既不会被自动取消，时刻也不会变化（仅影响此后新建预约的排程）；
- 取消后的预约状态变为 `cancelled`，不再占用航道、不出现在有效列表中；
- 创建预约的校验与 `/windows` 一致（通航时长为正、吃水/富余非负），
  此外允许出发范围起止颠倒返回 422，港口不存在返回 404。

## 可预约时段查询

`POST /ports/{id}/bookable-windows`（服务默认监听 **8000** 端口，
访问地址 http://localhost:8000/ports/{id}/bookable-windows ）在**不创建预约**
的前提下，查询允许出发范围内当前全部可预约的出发时段：

- 提交**吃水**、**安全富余**、**正数通航分钟数**与**允许出发起止时刻**
  （`allowed_departure_from` ≤ `allowed_departure_to`，含端点；均按 UTC 规则解释）；
- 先按该港口**最新预报**复用潮汐可出发窗口计算（同 `/windows`），再与请求的
  允许出发范围取交集；
- 再扣除同港**有效预约**的占用：每条预约的通航区间 `[a, b]` 按请求通航时长
  换算为**禁止出发开区间** `(a − 通航时长, b)`——与既有通航**首尾相接允许**
  （`a − 通航时长` 与 `b` 两个端点均可出发）；
- **零长度但可用的单点出发时刻保留**（响应中 `start == end`，例如两条预约
  之间的间隙恰好等于通航时长时，仅剩的那一个可出发时刻）；
- 返回允许范围内**全部可预约出发时段** `windows` 与**最早出发时刻**
  `earliest_departure`，均按时间升序；无可预约时段时 `windows` 为空、
  `earliest_departure` 为 `null`；
- **已取消预约不占用**航道；查询**只读**，不写库、不改动任何预约；
  校验与 `/windows` 一致（通航时长为正、吃水/富余非负），
  允许出发范围起止颠倒返回 422，港口不存在返回 404。

```bash
# 沿用上文示例：甲船预约排定 00:36–01:36，潮汐可出发时段 [00:36, 04:24]
curl -X POST http://localhost:8000/ports/1/bookable-windows \
  -H "Content-Type: application/json" \
  -d '{"draft": 5.0,
       "safety_margin": 0.5,
       "duration_minutes": 60,
       "allowed_departure_from": "2026-09-24T00:00:00Z",
       "allowed_departure_to":   "2026-09-24T04:24:00Z"}'
```

响应（甲船占用 00:36–01:36，禁止出发开区间 (前一日 23:36, 01:36)，
剩余可预约时段为 01:36 起紧随其后的 [01:36, 04:24]）：

```json
{
  "port_id": 1,
  "required_depth": 5.5,
  "duration_minutes": 60.0,
  "allowed_departure_from": "2026-09-24T00:00:00+00:00",
  "allowed_departure_to": "2026-09-24T04:24:00+00:00",
  "earliest_departure": "2026-09-24T01:36:00+00:00",
  "windows": [
    {"start": "2026-09-24T01:36:00+00:00", "end": "2026-09-24T04:24:00+00:00"}
  ]
}
```

若再有一条预约占用 02:36–03:36，两条预约之间的间隙恰好 60 分钟，
则响应为（01:36 出发、02:36 结束，与前后均首尾相接的**单点**保留）：

```json
{
  "earliest_departure": "2026-09-24T01:36:00+00:00",
  "windows": [
    {"start": "2026-09-24T01:36:00+00:00", "end": "2026-09-24T01:36:00+00:00"},
    {"start": "2026-09-24T03:36:00+00:00", "end": "2026-09-24T04:24:00+00:00"}
  ]
}
```

## 预约改期

`PATCH /reservations/{id}/schedule` 为已排定的**有效**预约改期：请求体只提供
**新的允许出发起止时刻**（`allowed_departure_from` ≤ `allowed_departure_to`，
含端点；均按上面的 UTC 规则解释），吃水、安全富余与通航时长**沿用原预约**。

排程规则与创建预约一致：

1. 以**请求时最新预报**重算潮汐可出发时段；
2. 同港**有效**预约中**排除该预约自身的原占用**（改期不会与自己冲突），
   其余占用照常避让（两船**首尾相接允许**）；
3. 在新允许范围内选出**最早**可行出发时刻，更新允许范围与通航起止；
4. 排程检查与更新在**同一 SQLite 写事务**内完成（同港先在进程内串行化），
   并发改期/新建不会产生双重占用；
5. 新范围内**无可行时刻**返回 **409 Conflict**，且**保留原排程与允许范围**
   （不改动任何数据）。

成功时预约 **ID、船名、创建时间保持不变**，状态仍为 `active`，返回更新后的
预约详情；预约不存在返回 404，已取消的预约返回 409，范围起止颠倒返回 422。

```bash
# 沿用上文示例：丙船（预约 2）排定 01:36–02:36，现改为允许 03:00–04:24 出发
curl -X PATCH http://localhost:8000/reservations/2/schedule \
  -H "Content-Type: application/json" \
  -d '{"allowed_departure_from": "2026-09-24T03:00:00Z",
       "allowed_departure_to":   "2026-09-24T04:24:00Z"}'
```

响应（`200 OK`；新范围内最早 03:00 可出发，ID、船名与创建时间不变）：

```json
{
  "id": 2,
  "port_id": 1,
  "ship_name": "丙船",
  "draft": 5.0,
  "safety_margin": 0.5,
  "duration_minutes": 60.0,
  "allowed_departure_from": "2026-09-24T03:00:00+00:00",
  "allowed_departure_to": "2026-09-24T04:24:00+00:00",
  "departure_start": "2026-09-24T03:00:00+00:00",
  "departure_end": "2026-09-24T04:00:00+00:00",
  "status": "active",
  "created_at": "2026-09-24T10:05:00.000000+00:00",
  "canceled_at": null
}
```

```bash
# 新范围内无可行时刻（潮高不足或与其他预约冲突）→ 409，原排程与允许范围保留
curl -i -X PATCH http://localhost:8000/reservations/2/schedule \
  -H "Content-Type: application/json" \
  -d '{"allowed_departure_from": "2026-09-25T00:00:00Z",
       "allowed_departure_to":   "2026-09-25T04:00:00Z"}'
# HTTP/1.1 409 Conflict
# {"detail":"新允许出发范围内无可用时刻（潮高不足或与现有预约冲突），已保留原排程"}
```

## 预约水深复核

`GET /reservations/{id}/depth-audit`（服务默认监听 **8000** 端口）对已排定的预约
做**只读**水深复核：

- 读取预约排定的通航起止（`departure_start` / `departure_end`）、吃水与安全富余，
  按该港口**查询时刻的最新预报**相邻点线性插值复算水深；
- `deficits`：通航区间内水深低于所需水深（吃水 + 安全富余）的连续时段，
  相邻段已合并，并给出各段**最大欠深**（所需水深 − 时段内最低水深）；
- 预报首末点未覆盖整个通航区间时，`coverage_complete` 为 `false`，
  `uncovered` 列出未覆盖时段——这些时段水深未知，**不判为安全**，
  此时 `sufficient` 一律为 `false`；
- 结果只反映查询时的预报，**不改动预约或原排程**；预约不存在返回 404，
  已取消的预约也可复核（`status` 为 `cancelled`）。

```bash
# 复核预约 1（沿用上文示例：甲船排定 00:36–01:36，所需水深 5.5 米）
curl http://localhost:8000/reservations/1/depth-audit
```

预报未变时，排定区间内水深全程达标：

```json
{
  "reservation_id": 1,
  "port_id": 1,
  "ship_name": "甲船",
  "status": "active",
  "departure_start": "2026-09-24T00:36:00+00:00",
  "departure_end": "2026-09-24T01:36:00+00:00",
  "draft": 5.0,
  "safety_margin": 0.5,
  "required_depth": 5.5,
  "coverage_complete": true,
  "uncovered": [],
  "deficits": [],
  "sufficient": true
}
```

若 03:00 的预报点被更正为 1.0（见上文"完整示例"第 4 步），同一预约复核即
发现全程欠深（00:36 处欠 0.4 米），但预约本身的排定时刻不受影响：

```json
{
  "reservation_id": 1,
  "port_id": 1,
  "ship_name": "甲船",
  "status": "active",
  "departure_start": "2026-09-24T00:36:00+00:00",
  "departure_end": "2026-09-24T01:36:00+00:00",
  "draft": 5.0,
  "safety_margin": 0.5,
  "required_depth": 5.5,
  "coverage_complete": true,
  "uncovered": [],
  "deficits": [
    {"start": "2026-09-24T00:36:00+00:00", "end": "2026-09-24T01:36:00+00:00", "max_deficit": 0.4}
  ],
  "sufficient": false
}
```

若最新预报只覆盖到 01:00（首末点未覆盖整个通航区间），则返回
`coverage_complete: false`、`uncovered: [{"start": "…01:00…", "end": "…01:36…"}]`
且 `sufficient: false`——覆盖不足**不会**被当作安全。

## 最大可通航吃水

`POST /ports/{id}/max-draft`（服务默认监听 **8000** 端口）反求允许出发范围内
全程水深达标的**最大非负吃水**：

- 提交**安全富余**、**正数通航分钟数**与**允许出发起止时刻**
  （`allowed_departure_from` ≤ `allowed_departure_to`，含端点；均按 UTC 规则解释）；
- 按**查询时最新潮高预报**与相邻点线性插值规则，在允许出发范围内寻找
  `水深 = 基准水深 + 潮高 ≥ 吃水 + 安全富余` 全程成立的最大吃水，
  结果按 **0.01 米向下取整**；
- 只在**预报覆盖的连续时段**内求解（不外推）；同一吃水有多个可行时刻时，
  返回**最早出发时刻**及对应的**通航结束时刻**（出发时刻 + 通航时长）；
- 允许范围内连 **0 米吃水**都不可行（预报未覆盖、通航时长超过覆盖时段或
  水深不足）时返回 **409**；查询**只读**，不写库。

```bash
# 新港口：基准水深 4.0 米，潮高 1.0→3.0→1.0（水深 5.0→7.0→5.0）
curl -X POST http://localhost:8000/ports \
  -H "Content-Type: application/json" \
  -d '{"name": "吃水港", "reference_depth": 4.0}'

curl -X PUT http://localhost:8000/ports/2/tide-forecasts \
  -H "Content-Type: application/json" \
  -d '{"points": [
        {"forecast_time": "2026-09-24T00:00:00Z", "tide_height": 1.0},
        {"forecast_time": "2026-09-24T02:00:00Z", "tide_height": 3.0},
        {"forecast_time": "2026-09-24T04:00:00Z", "tide_height": 1.0}
      ]}'

# 安全富余 0.5 米、需要 60 分钟连续通航、允许 00:00–04:00 出发
curl -X POST http://localhost:8000/ports/2/max-draft \
  -H "Content-Type: application/json" \
  -d '{"safety_margin": 0.5,
       "duration_minutes": 60,
       "allowed_departure_from": "2026-09-24T00:00:00Z",
       "allowed_departure_to":   "2026-09-24T04:00:00Z"}'
```

响应（最优通航区间为以高潮为中心的 01:30–02:30，全程最低水深 6.5 米，
最大吃水 = 6.5 − 0.5 = 6.0 米）：

```json
{
  "port_id": 2,
  "safety_margin": 0.5,
  "duration_minutes": 60.0,
  "allowed_departure_from": "2026-09-24T00:00:00+00:00",
  "allowed_departure_to": "2026-09-24T04:00:00+00:00",
  "max_draft": 6.0,
  "required_depth": 6.5,
  "departure_start": "2026-09-24T01:30:00+00:00",
  "departure_end": "2026-09-24T02:30:00+00:00"
}
```

```bash
# 允许出发范围完全落在预报覆盖时段（00:00–04:00）之外：0 米吃水也不可行 → 409
curl -i -X POST http://localhost:8000/ports/2/max-draft \
  -H "Content-Type: application/json" \
  -d '{"safety_margin": 0.5,
       "duration_minutes": 60,
       "allowed_departure_from": "2026-09-24T05:00:00Z",
       "allowed_departure_to":   "2026-09-24T06:00:00Z"}'
# HTTP/1.1 409 Conflict
# {"detail":"允许出发范围内无可行的 0 米吃水（预报未覆盖或水深不足）"}
```

说明：

- 校验与 `/windows` 一致（通航时长为正、安全富余非负），
  允许出发范围起止颠倒返回 422，港口不存在返回 404；
- 结果随预报更正实时变化（与 `/windows` 相同，每次查询都按最新预报重算）；
- 返回的 `departure_start` / `departure_end` 是**取整后**最大吃水下的
  最早可行出发与通航结束时刻，可直接用于参考排程。

## 预报影响预演

`POST /ports/{id}/forecast-impact-preview`（服务默认监听 **8000** 端口）在
**不写库**的前提下预演一批拟录入预报点对现有预约的影响：

- 请求体与 `PUT /ports/{id}/tide-forecasts` 相同（`points` 至少 1 个点，
  时刻按 UTC 规则解释）；拟录入点与库存预报合并形成**临时预报**——
  同一时刻按提交顺序**后者覆盖前者**（可更正库存点、新增点，
  同批重复时刻以最后提交的为准）；
- 基于**一次读取**的当前预报与该港口**有效预约**（已取消的不参与），
  对每条预约排定的通航区间分别用**当前预报**与**临时预报**按
  `GET /reservations/{id}/depth-audit` 相同的线性插值、水深复核规则判定
  是否**全程达标**——欠深或**预报未覆盖**的区间均判为不达标；
- 仅返回达标状态**发生变化**的预约（`reservation_id`、原达标值
  `current_sufficient`、预演达标值 `preview_sufficient`），按出发时刻升序；
  无变化时返回空列表 `[]`；
- 预演**只读**：不写入预报点、不改动任何预约；港口不存在返回 404。

```bash
# 沿用上文示例港（基准水深 4.5 米，潮高 0.5→3.0→0.5，
# 甲船预约排定 2026-09-24 00:36–01:36，所需水深 5.5 米）：
# 预演“拟把 03:00 的潮高预报从 3.0 更正为 1.0”会影响哪些预约
curl -X POST http://localhost:8000/ports/1/forecast-impact-preview \
  -H "Content-Type: application/json" \
  -d '{"points": [{"forecast_time": "2026-09-24T03:00:00Z", "tide_height": 1.0}]}'
```

响应（预约 1 的通航区间在临时预报下全程欠深，达标状态由达标变为不达标）：

```json
[
  {
    "reservation_id": 1,
    "current_sufficient": true,
    "preview_sufficient": false
  }
]
```

预演后库存预报与预约保持不变（可用 `GET /ports/1/tide-forecasts` 与
`GET /reservations/1/depth-audit` 验证）；若提交的拟录入点不改变任何预约的
达标状态（例如与当前预报一致），响应为空列表 `[]`。

## 实测潮位校准窗口

潮位站实测数据可对预报做残差校准，用于更贴近实际地评估通航窗口：

- `PUT /ports/{id}/tide-observations`：批量写入实测潮高点
  （`obs_time` + `tide_height`，时刻按 UTC 规则解释）；**同港同一 UTC 时刻
  重复写入即覆盖旧值**（与预报点的更正语义一致），可用 `GET
  /ports/{id}/tide-observations` 查看已写入的实测点；
- `POST /ports/{id}/calibrated-windows`：**校准窗口查询（只读）**，请求体与
  窗口响应与 `POST /ports/{id}/windows` **完全一致**
  （吃水、安全富余、正数通航分钟数；返回 `required_depth`、全部可出发时段
  `windows` 与 `earliest_departure`）。

校准规则：

1. 查询时读取该港口**最新预报**与全部实测点；对每个观测时刻用相邻预报点
   **线性插值**得到预报潮高，**残差 = 实测潮高 − 预报潮高**；
2. **仅采用落在预报覆盖范围 [首预报时刻, 末预报时刻] 内的观测**，范围外的
   观测忽略（不做外推）；
3. 以**预报首末时刻的零残差**为边界锚点；边界时刻同刻有观测时**以观测为准**；
4. 锚点之间对残差做**线性插值**，校准潮高 = 原预报潮高 + 插值残差；
   观测点处的校准潮高即实测值；
5. 把**全部预报点时刻与有效观测点时刻合并为折点**（折点之间为线性），再
   **复用现有通航窗口计算**（同样的达标判定、减通航时长、覆盖不外推规则）；
6. **无有效观测**（未写入、全部落在覆盖范围外，或预报不足 2 点）时，
   校准结果与原预报完全一致，响应等同 `/windows`。

校准查询**只读**：不改原预报、不影响已有预约与新预约排程（预约始终按原预报
计算），实测点仅作用于本接口。

```bash
# 沿用上文示例港（基准水深 4.5 米，预报潮高 0.5→3.0→0.5，00:00/03:00/06:00）
# 写入 03:00 的实测潮高 2.0 米（预报 3.0，残差 -1.0 米）
curl -X PUT http://localhost:8000/ports/1/tide-observations \
  -H "Content-Type: application/json" \
  -d '{"points": [{"obs_time": "2026-09-24T03:00:00Z", "tide_height": 2.0}]}'
# → [{"obs_time": "2026-09-24T03:00:00+00:00", "tide_height": 2.0}]

# 校准窗口查询：校准后水深 5.0→6.5→5.0，所需水深 5.5
curl -X POST http://localhost:8000/ports/1/calibrated-windows \
  -H "Content-Type: application/json" \
  -d '{"draft": 5.0, "safety_margin": 0.5, "duration_minutes": 60}'
```

响应（校准后达标窗口由原 `[00:36, 05:24]` 变为 `[01:00, 05:00]`，
减去 60 分钟通航时长）：

```json
{
  "port_id": 1,
  "required_depth": 5.5,
  "duration_minutes": 60.0,
  "earliest_departure": "2026-09-24T01:00:00+00:00",
  "windows": [
    {"start": "2026-09-24T01:00:00+00:00", "end": "2026-09-24T04:00:00+00:00"}
  ]
}
```

```bash
# 同刻重复写入覆盖旧值；写回与预报一致的 3.0 后，校准窗口等同原窗口
curl -X PUT http://localhost:8000/ports/1/tide-observations \
  -H "Content-Type: application/json" \
  -d '{"points": [{"obs_time": "2026-09-24T03:00:00Z", "tide_height": 3.0}]}'
```

说明：

- 参数校验与 `/windows` 一致（通航时长为正、吃水/富余非负），
  实测点列表为空返回 422，港口不存在返回 404；
- 实测潮高独立于预报存储（`tide_observations` 表），写入实测点不会修改任何
  预报点；`/windows`、预约排程与水深复核均继续使用原预报；
- 预报更正后再次查询校准窗口，残差会按**更正后的最新预报**重新计算。

## 实测潮高异常点质检

`GET /ports/{id}/observation-anomalies`（服务默认监听 **8000** 端口，
访问地址 http://localhost:8000/ports/{id}/observation-anomalies ）基于已写入的
实测潮高点与最新预报做**只读**质检，用于发现明显跳变/粗差的实测记录：

1. 对**落在最新预报覆盖范围** [首预报时刻, 末预报时刻] **内**的每条实测记录，
   按相邻预报点**线性插值**求观测时刻预报潮高，
   **残差 = 实测潮高 − 预报潮高**（范围外观测忽略，不做外推）；
2. 取该实测时刻**前后各 120 分钟（含端点）**窗口内的**其他**有效实测记录，
   以它们残差的**中位数**作为该点的**局部基线**；窗口内邻点**少于 3 个**时
   **不评估**该点；
3. **局部 MAD** = 邻点残差与基线之差绝对值的中位数；异常阈值取
   **max(0.3 米, 3 × 1.4826 × MAD)**——0.3 米是阈值下限，抖动剧烈的时段由
   MAD 自适应抬高阈值，避免把正常波动误报为异常；
4. 仅当**偏离量 = |残差 − 基线| 严格大于阈值**时返回该点（恰等不算）；
   响应按 `obs_time` **升序**，每条含 `obs_time`（时刻）、`tide_height`
   （实测潮高）、`residual`（残差）、`baseline`（局部基线）与 `deviation`
   （偏离量）；
5. 预报**不足两点**（无插值区间）或没有任何点具备足够邻点时，返回空列表 `[]`。

质检完全**只读**：不写库、不改动实测点与预报点，也不影响校准窗口与预约排程；
港口不存在返回 404。

```bash
# 平潮预报（恒 0.0，覆盖 00:00–08:00），每小时一条实测，03:00 出现 +1.0 米尖峰
curl -X PUT http://localhost:8000/ports/1/tide-forecasts \
  -H "Content-Type: application/json" \
  -d '{"points": [
        {"forecast_time": "2026-09-24T00:00:00Z", "tide_height": 0.0},
        {"forecast_time": "2026-09-24T08:00:00Z", "tide_height": 0.0}
      ]}'

curl -X PUT http://localhost:8000/ports/1/tide-observations \
  -H "Content-Type: application/json" \
  -d '{"points": [
        {"obs_time": "2026-09-24T00:00:00Z", "tide_height": 0.0},
        {"obs_time": "2026-09-24T01:00:00Z", "tide_height": 0.0},
        {"obs_time": "2026-09-24T02:00:00Z", "tide_height": 0.0},
        {"obs_time": "2026-09-24T03:00:00Z", "tide_height": 1.0},
        {"obs_time": "2026-09-24T04:00:00Z", "tide_height": 0.0},
        {"obs_time": "2026-09-24T05:00:00Z", "tide_height": 0.0},
        {"obs_time": "2026-09-24T06:00:00Z", "tide_height": 0.0}
      ]}'

# 质检：03:00 残差 +1.0，邻点残差中位数（基线）为 0，偏离 1.0 > 0.3 → 异常
curl http://localhost:8000/ports/1/observation-anomalies
```

响应（按时刻升序；覆盖两端 00:00/06:00 的实测因 ±120 分钟内不足 3 个邻点不评估，
正常整点残差为 0 不判异，仅返回尖峰点）：

```json
[
  {
    "obs_time": "2026-09-24T03:00:00+00:00",
    "tide_height": 1.0,
    "residual": 1.0,
    "baseline": 0.0,
    "deviation": 1.0
  }
]
```

```bash
# 无异常点（或预报不足两点、邻点不足）时返回空列表
curl http://localhost:8000/ports/1/observation-anomalies
# → []
```

说明：

- 质检与校准窗口共享实测点数据但相互独立：质检结果不影响
  `POST /ports/{id}/calibrated-windows`，实测点也始终不参与预约排程；
- 预报点更正后再次质检，残差按**更正后的最新预报**重新插值计算；
- 单位与时间约定同其他接口：长度为米、时刻为 ISO 8601 UTC
  （不带时区按 UTC 处理）。

## 桥梁净空通航窗口

港口航道上方有桥梁时，通航除水深外还需满足净空条件。每港可配置**一处**桥梁：

- `PUT /ports/{id}/bridge`：设置/更正桥梁**梁底高程**（米，相对潮高基准面，
  与潮高预报同一基准），重复提交即覆盖；`GET /ports/{id}/bridge` 查询当前配置；
- `POST /ports/{id}/bridge-windows`（服务默认监听 **8000** 端口，
  访问地址 http://localhost:8000/ports/{id}/bridge-windows ）：**桥梁净空窗口
  查询（只读）**，提交**吃水**、**水深富余**、**船舶水面以上高度**、**净空富余**
  与**正数通航分钟数**。

窗口规则（与 `/windows` 相同的线性插值与覆盖范围约定）：

1. 按查询时**最新潮高预报**逐段线性插值，仅在**预报覆盖时段**内计算（不外推）；
2. 水深条件：`基准水深 + 潮高 ≥ 吃水 + 水深富余`；
3. 净空条件：`梁底高程 − 潮高 ≥ 船舶水面以上高度 + 净空富余`；
4. **等号可通行**（恰好相等也算达标）；求两条件的**连续交集**并**合并相接区间**；
5. 交集窗口**结束时刻减去通航时长**得到可出发时段，不足时长的窗口舍弃；
6. 返回全部可出发时段 `windows` 与最早出发时刻 `earliest_departure`；
   预报更正后再次查询自动按最新数据重算。

港口不存在或未配置桥梁时均返回 **404**（错误消息明确区分两种情形）；
校验与 `/windows` 一致（通航时长为正，吃水/水深富余/水面以上高度/净空富余非负，
否则返回 422）；查询**只读**，不写库。

```bash
# 沿用示例港（基准水深 4.5 米，潮高 0.5→3.0→0.5）：配置梁底高程 27.5 米
curl -X PUT http://localhost:8000/ports/1/bridge \
  -H "Content-Type: application/json" \
  -d '{"bridge_bottom_elevation": 27.5}'

# 吃水 5.0 + 水深富余 0.5；水面以上高度 24.0 + 净空富余 1.0；通航 60 分钟
curl -X POST http://localhost:8000/ports/1/bridge-windows \
  -H "Content-Type: application/json" \
  -d '{"draft": 5.0, "depth_margin": 0.5,
       "air_draft": 24.0, "clearance_margin": 1.0,
       "duration_minutes": 60}'
```

响应（水深条件窗口 `[00:36, 05:24]`；净空条件要求潮高 ≤ 2.5 米，即
`[00:00, 02:24]` 与 `[03:36, 06:00]`；交集各 108 分钟，减去 60 分钟通航时长）：

```json
{
  "port_id": 1,
  "bridge_bottom_elevation": 27.5,
  "required_depth": 5.5,
  "required_clearance": 25.0,
  "duration_minutes": 60.0,
  "earliest_departure": "2026-09-24T00:36:00+00:00",
  "windows": [
    {"start": "2026-09-24T00:36:00+00:00", "end": "2026-09-24T01:24:00+00:00"},
    {"start": "2026-09-24T03:36:00+00:00", "end": "2026-09-24T04:24:00+00:00"}
  ]
}
```

## 港口潮位预警

每港可配置**一条**潮位预警规则，用于按最新潮高预报圈定潮位越限（高潮/低潮）
时段：

- `PUT /ports/{id}/tide-alert-rule`：设置/更正规则，重复提交即覆盖；
  `GET /ports/{id}/tide-alert-rule` 查询当前规则（港口或规则不存在返回 404）；
- `GET /ports/{id}/tide-alerts`（服务默认监听 **8000** 端口，
  访问地址 http://localhost:8000/ports/{id}/tide-alerts ）：**预警时段查询
  （只读）**，无需请求体。

规则字段：

- `direction`：`high`（高潮）或 `low`（低潮）；
- `trigger_height`：触发潮高（米，相对海图基准面）；
- `clear_height`：解除潮高（米）；**高潮的解除阈值须低于触发阈值，低潮反之**
  （两阈值之间形成迟滞区间，潮高在触发阈值附近抖动时预警不解除、不拆分）；
- `min_duration_minutes`：最短持续分钟（**非负**；持续不足该时长的时段不返回）。

扫描规则（与 `/windows` 相同的线性插值与覆盖范围约定）：

1. 按查询时**最新预报**相邻点线性插值，仅在**预报覆盖时段**内扫描（不外推）；
2. 首点已达到触发阈值则**从首点预警**，否则于**首次达到触发阈值的交点**进入
   预警（高潮：潮高 ≥ 触发潮高；低潮：潮高 ≤ 触发潮高）；
3. **达到解除阈值的交点**结束预警（高潮：潮高 ≤ 解除潮高；低潮：潮高 ≥ 解除
   潮高），交点作为时段边界；期间回落至触发阈值以下但未达解除阈值**不解除**；
4. 预报覆盖结束仍未解除的时段标记 `unclosed: true`，终点取末个预报时刻；
5. 仅返回**持续时长达到最短持续分钟**的时段，按起点升序，每条给出
   `start` / `end` / `unclosed` 与**期间极值潮高** `extreme_tide_height`
   （高潮预警为期间最高潮高，低潮预警为期间最低潮高）；
6. 预报**不足两点**返回空列表 `[]`；查询**只读**，预报更正后再次查询自动重算。

```bash
# 沿用示例港（潮高 0.5→3.0→0.5，00:00/03:00/06:00）：
# 配置高潮预警：触发 2.5 米、解除 1.0 米、最短持续 30 分钟
curl -X PUT http://localhost:8000/ports/1/tide-alert-rule \
  -H "Content-Type: application/json" \
  -d '{"direction": "high", "trigger_height": 2.5,
       "clear_height": 1.0, "min_duration_minutes": 30}'

curl http://localhost:8000/ports/1/tide-alerts
```

响应（02:24 潮高升至 2.5 进入预警，05:24 降至 1.0 解除，持续 180 分钟，
期间最高潮高 3.0 米）：

```json
[
  {
    "start": "2026-09-24T02:24:00+00:00",
    "end": "2026-09-24T05:24:00+00:00",
    "unclosed": false,
    "extreme_tide_height": 3.0
  }
]
```

说明：

- 规则校验：高潮 `clear_height` 必须**严格低于** `trigger_height`，低潮必须
  **严格高于**，`min_duration_minutes` 必须非负，否则返回 422；
- 预警判定直接针对**潮高**（不叠加航道基准水深）；
- 港口不存在或未配置规则时，`GET /ports/{id}/tide-alerts` 返回 404。

## 本地开发（不用 Docker）

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt   # 运行依赖 + pytest/httpx
uvicorn app.main:app --reload         # 默认使用 ./tide.db，可用 DATABASE_PATH 覆盖
pytest                                # 运行全部测试（核心算法 + API 端到端）
```

## 项目结构

```
app/
  main.py       # FastAPI 路由与生命周期（启动时初始化数据库）
  tides.py      # 核心算法：线性插值、达标窗口、可出发时段、水深复核、最大可通航吃水、预报合并、实测残差校准、实测异常点质检、可预约时段扣除占用、桥梁净空双条件交集窗口、潮位预警时段扫描
  database.py   # SQLite 连接与幂等建表
  schemas.py    # Pydantic 请求/响应模型与校验
tests/
  test_tides.py # 核心算法单元测试（仅标准库）
  test_api.py   # API 端到端测试
Dockerfile / docker-compose.yml / requirements*.txt
```
