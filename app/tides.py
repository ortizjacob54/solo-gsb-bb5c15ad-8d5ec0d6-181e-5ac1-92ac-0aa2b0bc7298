"""潮汐通航窗口核心计算（纯标准库，便于单测）。

规则：
- 实际水深(t) = 航道基准水深 + 潮高(t)；
- 相邻预报点之间潮高按线性插值；
- 只在预报覆盖时段 [首个预报点, 末个预报点] 内计算；
- 水深 >= 吃水 + 安全富余 的连续时段为达标窗口，端点恰好达标也纳入；
- 可出发时段 = [窗口起点, 窗口终点 − 通航时长]；窗口长度不足通航时长的舍弃；
- 预约水深复核：在排定通航区间内求水深低于所需水深的连续时段（相邻段合并，
  附各段最大欠深）；预报首末点未覆盖的通航时段单独返回，不得视为安全。
- 最大可通航吃水：在允许出发范围内，对“所需水深 = 吃水 + 安全富余”二分搜索，
  复用达标窗口/可出发时段规则判定可行性，求允许范围内全程水深达标所能支持的
  最大所需水深；吃水 = 最大所需水深 − 安全富余，由调用方按 0.01 米向下取整。
- 预报影响预演：拟录入的预报点与当前预报按“同一时刻后者覆盖前者”合并成
  临时预报，复用 depth_deficit_audit 分别判定全程达标与否（纯内存计算）。
- 实测潮位校准：仅采用落在预报覆盖范围内的观测（残差 = 实测 − 观测时刻的
  线性插值预报），以预报首末时刻的零残差为边界锚点（同刻有观测则以观测为准），
  锚点间线性插值残差并叠加原预报；把预报点与有效观测点时刻合并为折点后，
  即可复用现有通航窗口计算（纯内存计算，不改原预报）。
- 实测潮高异常点质检：对落在预报覆盖范围内的每条实测记录按线性插值求残差；
  以该时刻前后各 120 分钟（含）内其他有效实测残差的中位数为局部基线，邻点
  至少 3 个才评估；局部 MAD 为邻点残差与基线差的绝对值中位数，异常阈值取
  max(0.3 米, 3 × 1.4826 × MAD)，仅返回偏离基线严格超过阈值的点（纯内存
  只读计算，不影响校准窗口与预约）。
- 可预约时段查询：在潮汐可出发时段与请求允许范围的交集上，按“每条有效预约
  占用 [a, b] 禁止出发 (a−时长, b)”逐条扣除（开区间，端点保留即首尾相接允许），
  零长度但可用的单点出发时刻保留，返回剩余全部可预约出发时段（按时间升序）。
- 桥梁净空通航窗口：水深条件（基准水深 + 潮高 >= 吃水 + 水深富余）与净空条件
  （梁底高程 − 潮高 >= 船舶水面以上高度 + 净空富余，等价于对取反潮高序列复用
  达标窗口计算）在预报覆盖时段内按相邻点线性插值判定，等号可通行；求两条件的
  连续交集（相接区间合并）后减去通航时长得到可出发时段，不足时长的舍弃。
- 潮位预警时段扫描：按方向（高潮/低潮）把潮高序列作符号变换后统一为
  “升至触发值进入、降至解除值结束”的迟滞扫描（高潮解除阈值低于触发阈值，
  低潮反之）；首点已达触发阈值则从首点预警，否则于首次达到触发阈值的交点
  进入，达到解除阈值的交点结束，交点作为边界；覆盖结束仍未解除标记未闭合；
  仅保留持续时长达到最短要求的时段，并给出期间极值潮高（高潮取最高、低潮
  取最低）。
- 分段航道通行时段：每段自带基准水深、相对出发时刻的进入偏移与正数通过
  时长；按各段基准水深复用达标窗口计算，减去通过时长得到该段全程达标的
  进入时段，再整体前移进入偏移反推为出发时段；所有段的出发时段取交集
  （相接合并、单点保留）即全部可出发时段。各段进入时段本身限定在预报
  覆盖时段内，交集中的出发时刻自然保证所有航段均处于预报覆盖内。
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import datetime, timezone

_EPS = 1e-9  # 浮点容差，保证“恰好达标”的端点被纳入

ForecastPoint = tuple[datetime, float]  # (预报时刻, 潮高)


def _cross_time(t0: float, d0: float, t1: float, d1: float, required: float) -> float:
    """线性插值下水深恰好等于 required 的时刻（Unix 秒）。"""
    frac = (required - d0) / (d1 - d0)
    return t0 + frac * (t1 - t0)


def sufficient_depth_windows(
    points: list[ForecastPoint],
    reference_depth: float,
    required_depth: float,
) -> list[tuple[float, float]]:
    """返回水深达标的连续窗口列表 [(start_ts, end_ts)]（Unix 秒，按时间升序）。

    points 需按时刻升序且时刻唯一；不足 2 个预报点时无插值区间，返回空。
    """
    if len(points) < 2:
        return []

    ts = [p[0].timestamp() for p in points]
    depths = [reference_depth + p[1] for p in points]

    windows: list[tuple[float, float]] = []
    cur_start: float | None = None
    cur_end: float | None = None

    def flush() -> None:
        nonlocal cur_start, cur_end
        if cur_start is not None:
            windows.append((cur_start, cur_end))  # type: ignore[arg-type]
            cur_start = cur_end = None

    for i in range(len(points) - 1):
        t0, t1 = ts[i], ts[i + 1]
        d0, d1 = depths[i], depths[i + 1]
        above0 = d0 >= required_depth - _EPS
        above1 = d1 >= required_depth - _EPS

        if above0 and above1:
            seg = (t0, t1)
        elif not above0 and not above1:
            # 线性段两端都不达标，整段不达标；关闭当前窗口
            flush()
            continue
        elif above0:
            seg = (t0, _cross_time(t0, d0, t1, d1, required_depth))
        else:
            seg = (_cross_time(t0, d0, t1, d1, required_depth), t1)

        if cur_start is None:
            cur_start, cur_end = seg
        elif seg[0] <= cur_end + 1e-6:  # type: ignore[operator]
            # 相邻段首尾相接，合并为同一连续窗口
            cur_end = max(cur_end, seg[1])  # type: ignore[type-var]
        else:
            flush()
            cur_start, cur_end = seg

    flush()
    return windows


def _to_departure_windows(
    windows_ts: list[tuple[float, float]], duration_seconds: float
) -> list[tuple[datetime, datetime]]:
    """达标窗口（Unix 秒）减去通航时长得到可出发时段；不足时长的窗口舍弃。"""
    result: list[tuple[datetime, datetime]] = []
    for start_ts, end_ts in windows_ts:
        if end_ts - start_ts >= duration_seconds - _EPS:
            result.append((_to_utc(start_ts), _to_utc(end_ts - duration_seconds)))
    return result


def departure_windows(
    points: list[ForecastPoint],
    reference_depth: float,
    required_depth: float,
    duration_seconds: float,
) -> tuple[list[tuple[datetime, datetime]], datetime | None]:
    """计算全部可出发时段与最早出发时刻。

    返回 ([(start, end), ...], earliest)，时间均为 UTC datetime。
    """
    result = _to_departure_windows(
        sufficient_depth_windows(points, reference_depth, required_depth),
        duration_seconds,
    )
    earliest = min((w[0] for w in result), default=None)
    return result, earliest


def intersect_windows(
    a: list[tuple[float, float]], b: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    """两组连续窗口（Unix 秒闭区间，各自按时间升序且互不重叠）的连续交集。

    等号可通行：两窗口端点相接（含浮点容差）视为相交于该点；
    相接的交集段合并为同一连续区间，结果仍按时间升序。
    """
    merged: list[tuple[float, float]] = []
    i = j = 0
    while i < len(a) and j < len(b):
        lo = max(a[i][0], b[j][0])
        hi = min(a[i][1], b[j][1])
        if lo <= hi + _EPS:
            hi = max(lo, hi)  # 容差内相接：视为交于一点
            if merged and lo <= merged[-1][1] + 1e-6:
                # 与上一段交集首尾相接，合并为同一连续区间
                merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
            else:
                merged.append((lo, hi))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return merged


def bridge_departure_windows(
    points: list[ForecastPoint],
    reference_depth: float,
    required_depth: float,
    bridge_bottom_elevation: float,
    required_clearance: float,
    duration_seconds: float,
) -> tuple[list[tuple[datetime, datetime]], datetime | None]:
    """桥梁净空通航窗口：水深与净空两条件的连续交集减去通航时长。

    - 水深条件：基准水深 + 潮高 >= 所需水深（吃水 + 水深富余）；
    - 净空条件：梁底高程 − 潮高 >= 所需净空（船舶水面以上高度 + 净空富余），
      等价于对取反潮高序列复用 sufficient_depth_windows；
    - 两条件均在预报覆盖时段内按相邻点线性插值判定，等号可通行；
    - 连续交集的相接区间合并后，再减去通航时长得到可出发时段
      （不足时长的窗口舍弃）。

    返回 ([(start, end), ...], earliest)，时间均为 UTC datetime。
    """
    depth_windows = sufficient_depth_windows(points, reference_depth, required_depth)
    # 梁底高程 − 潮高 >= 所需净空  ⟺  梁底高程 + (−潮高) >= 所需净空
    clearance_windows = sufficient_depth_windows(
        [(t, -h) for t, h in points], bridge_bottom_elevation, required_clearance
    )
    result = _to_departure_windows(
        intersect_windows(depth_windows, clearance_windows), duration_seconds
    )
    earliest = min((w[0] for w in result), default=None)
    return result, earliest


def earliest_feasible_departure(
    points: list[ForecastPoint],
    reference_depth: float,
    required_depth: float,
    duration_seconds: float,
    allowed_start: datetime,
    allowed_end: datetime,
) -> datetime | None:
    """某所需水深下，允许出发范围 [allowed_start, allowed_end] 内的最早可行出发时刻。

    复用 sufficient_depth_windows 的插值规则求达标窗口（已限定在预报覆盖
    时段内），减通航时长得到可出发时段后再与允许出发范围取交集。交集在
    原始（Unix 秒）时间轴上完成，避免整秒取整把可行性边界外扩；仅返回的
    展示时刻按既有约定四舍五入到整秒。无可行时刻返回 None。
    """
    lo, hi = allowed_start.timestamp(), allowed_end.timestamp()
    feasible: list[float] = []
    for start_ts, end_ts in sufficient_depth_windows(
        points, reference_depth, required_depth
    ):
        if end_ts - start_ts >= duration_seconds - _EPS:
            departure = max(start_ts, lo)
            if departure <= min(end_ts - duration_seconds, hi) + _EPS:
                feasible.append(departure)
    return _to_utc(min(feasible)) if feasible else None


def max_supported_required_depth(
    points: list[ForecastPoint],
    reference_depth: float,
    min_required: float,
    duration_seconds: float,
    allowed_start: datetime,
    allowed_end: datetime,
) -> tuple[float, datetime | None]:
    """允许出发范围内全程水深达标所能支持的最大所需水深（二分搜索）。

    可行性关于所需水深单调：所需水深越低越容易找到可行出发时刻。
    返回 (最大所需水深, 该水深下的最早可行出发时刻)；
    连 min_required（对应 0 米吃水 + 安全富余）都不可行时返回 (min_required, None)。
    """
    if (
        earliest_feasible_departure(
            points, reference_depth, min_required,
            duration_seconds, allowed_start, allowed_end,
        )
        is None
    ):
        return min_required, None

    # 上界取预报覆盖时段内的峰值水深：更高的所需水深必然不可行
    lo = min_required
    hi = max((reference_depth + h for _, h in points), default=min_required)
    for _ in range(80):  # 80 次二分，精度远超 0.01 米取整所需
        mid = (lo + hi) / 2
        if (
            earliest_feasible_departure(
                points, reference_depth, mid,
                duration_seconds, allowed_start, allowed_end,
            )
            is not None
        ):
            lo = mid
        else:
            hi = mid
    departure = earliest_feasible_departure(
        points, reference_depth, lo, duration_seconds, allowed_start, allowed_end
    )
    return lo, departure


def _remaining_departure_candidates(
    departable_windows: list[tuple[datetime, datetime]],
    duration_seconds: float,
    allowed_start: datetime,
    allowed_end: datetime,
    blockers: list[tuple[datetime, datetime]],
) -> list[tuple[float, float]]:
    """扣除已有预约占用后剩余的可出发区间（Unix 秒，按时间升序）。

    - 候选区间 = 潮汐可出发时段 ∩ 请求允许范围（含端点）；
    - 已有占用 [a, b] 会禁止 d ∈ (a-时长, b)：d == a-时长（首尾相接）
      与 d == b（紧随其后）均允许，因此从候选区间中挖掉开区间、保留两个端点；
    - 零长度区间（单个可出发时刻）保留；
    - 每条占用最多把每个候选区间拆为左右两段，结果始终按时间升序。
    """
    range_lo, range_hi = allowed_start.timestamp(), allowed_end.timestamp()

    # 候选出发区间 = 潮汐可出发时段 ∩ 请求允许范围
    candidates: list[tuple[float, float]] = []
    for start, end in departable_windows:
        lo = max(start.timestamp(), range_lo)
        hi = min(end.timestamp(), range_hi)
        if lo <= hi:
            candidates.append((lo, hi))

    for occ_start, occ_end in sorted(blockers, key=lambda x: x[0].timestamp()):
        forbidden_lo = occ_start.timestamp() - duration_seconds
        forbidden_hi = occ_end.timestamp()
        remaining: list[tuple[float, float]] = []
        for lo, hi in candidates:
            if lo <= forbidden_lo:  # 左侧 [lo, forbidden_lo] 可出发（含相接端点）
                remaining.append((lo, min(hi, forbidden_lo)))
            if hi >= forbidden_hi:  # 右侧 [forbidden_hi, hi] 可出发（含相接端点）
                remaining.append((max(lo, forbidden_hi), hi))
        candidates = remaining
        if not candidates:
            break
    return candidates


def earliest_scheduled_departure(
    departable_windows: list[tuple[datetime, datetime]],
    duration_seconds: float,
    allowed_start: datetime,
    allowed_end: datetime,
    blockers: list[tuple[datetime, datetime]],
) -> datetime | None:
    """在潮汐可出发时段内、请求允许范围内，安排不与已有预约重叠的最早出发时刻。

    - departable_windows：departure_windows 求出的可出发时段 [s, e]
      （出发时刻 d ∈ [s, e] 即可保证整个通航过程 [d, d+时长] 水深达标）；
    - allowed_start/end：请求方允许的出发时刻范围（含端点）；
    - blockers：该港口已有有效预约的通航占用区间 [占用起点, 占用终点]；
      首尾相接允许（新区间端点恰好贴住已有区间不算重叠）。

    返回最早可出发时刻（UTC datetime）；无可用时刻返回 None。
    """
    candidates = _remaining_departure_candidates(
        departable_windows,
        duration_seconds,
        allowed_start,
        allowed_end,
        blockers,
    )
    if not candidates:
        return None
    return _to_utc(min(lo for lo, _ in candidates))


def bookable_departure_windows(
    departable_windows: list[tuple[datetime, datetime]],
    duration_seconds: float,
    allowed_start: datetime,
    allowed_end: datetime,
    blockers: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    """扣除已有预约占用后，允许范围内的全部可预约出发时段（按时间升序）。

    与 earliest_scheduled_departure 共用同一套扣除规则（按请求通航时长把每条
    占用 [a, b] 换算为禁止出发开区间 (a-时长, b)，首尾相接端点保留，零长度但
    可用的单点出发时刻保留）。返回 [(start, end), ...]；无可用时段时为空列表。
    """
    return [
        (_to_utc(lo), _to_utc(hi))
        for lo, hi in _remaining_departure_candidates(
            departable_windows,
            duration_seconds,
            allowed_start,
            allowed_end,
            blockers,
        )
    ]


def merge_forecast_points(
    current: list[ForecastPoint],
    submitted: list[ForecastPoint],
) -> list[ForecastPoint]:
    """合并当前预报与一批拟录入的预报点，形成临时预报（纯内存，不写库）。

    同一时刻按提交顺序以后者覆盖前者：先放入当前预报，再按提交顺序
    依次覆盖，因此拟录入点可更正当前值，同批重复时刻以最后提交为准。
    返回按时刻升序的 (时刻, 潮高) 列表。
    """
    merged: dict[datetime, float] = {}
    for t, h in current:
        merged[t] = h
    for t, h in submitted:
        merged[t] = h
    return sorted(merged.items(), key=lambda kv: kv[0])


def _piecewise_linear(points: list[ForecastPoint], t: datetime) -> float:
    """按相邻点线性插值求时刻 t 的值；t 恰落在折点上时取该折点值。

    调用方需保证 t 位于 points 覆盖范围（首末时刻）之内。
    """
    times = [p[0] for p in points]
    i = bisect_right(times, t) - 1
    if i < 0:
        return points[0][1]
    if i >= len(points) - 1:
        return points[-1][1]
    t0, v0 = points[i]
    t1, v1 = points[i + 1]
    if t1 == t0:
        return v1
    frac = (t - t0).total_seconds() / (t1 - t0).total_seconds()
    return v0 + (v1 - v0) * frac


def calibrated_forecast_points(
    forecast: list[ForecastPoint],
    observations: list[ForecastPoint],
) -> list[ForecastPoint]:
    """用实测潮位校准预报，返回校准后的潮高折点序列（纯内存，不改入参）。

    - 仅采用落在预报覆盖范围 [首预报时刻, 末预报时刻] 内的观测，范围外忽略；
    - 观测时刻的预报潮高由最新预报相邻点线性插值得到，残差 = 实测 − 预报；
    - 以预报首末时刻的零残差为边界锚点；边界时刻同刻有观测时以观测为准
      （残差为实测 − 该时刻预报）；观测同刻重复时以最后一个为准；
    - 锚点（边界 + 有效观测）间残差线性插值；校准潮高 = 原预报潮高 + 残差；
    - 折点时刻 = 全部预报点时刻 ∪ 有效观测点时刻，折点间为线性，
      可直接复用 sufficient_depth_windows / departure_windows。
    - 无有效观测（或预报不足 2 点）时，校准结果与原预报完全一致。
    """
    if len(forecast) < 2:
        return list(forecast)

    first_t, last_t = forecast[0][0], forecast[-1][0]

    # 有效观测：落在预报覆盖范围内（含边界），同刻以最后写入为准
    valid: dict[datetime, float] = {}
    for t, observed in observations:
        if first_t <= t <= last_t:
            valid[t] = observed
    if not valid:
        return list(forecast)

    # 残差锚点：预报首末时刻零残差，随后由有效观测覆盖（同刻观测优先）
    anchors: dict[datetime, float] = {first_t: 0.0, last_t: 0.0}
    for t, observed in valid.items():
        predicted = _piecewise_linear(forecast, t)
        anchors[t] = observed - predicted

    anchor_times = sorted(anchors)
    anchor_points: list[ForecastPoint] = [
        (t, anchors[t]) for t in anchor_times
    ]
    # 折点时刻 = 预报点时刻 ∪ 有效观测点时刻；观测点处校准值即实测值
    observed: dict[datetime, float] = dict(valid.items())
    result: list[ForecastPoint] = []
    for t, predicted in forecast:
        if t in observed:
            result.append((t, observed.pop(t)))
        else:
            result.append((t, predicted + _piecewise_linear(anchor_points, t)))
    for t, observed_height in observed.items():
        result.append((t, observed_height))
    result.sort(key=lambda p: p[0])
    return result


# ---------------------------------------------------------------------------
# 实测潮高异常点质检
# ---------------------------------------------------------------------------

_ANOMALY_WINDOW_SECONDS = 120 * 60  # 前后各 120 分钟（含端点）取邻点
_ANOMALY_MIN_NEIGHBORS = 3          # 邻点少于 3 个不评估
_ANOMALY_MIN_THRESHOLD = 0.3        # 异常阈值下限（米）
_ANOMALY_MAD_K = 1.4826             # MAD → 标准差的一致性系数
_ANOMALY_SIGMA = 3.0                # 3σ 判异


def _median(values: list[float]) -> float:
    """中位数（偶数个时取中间两个的算术平均），调用方需保证非空。"""
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def observation_anomalies(
    forecast: list[ForecastPoint],
    observations: list[ForecastPoint],
) -> list[tuple[datetime, float, float, float, float]]:
    """检测落在最新预报覆盖范围内的实测潮高异常点（纯内存，不改入参）。

    对每条覆盖范围内的实测记录：
    - 残差 = 实测潮高 − 观测时刻按预报相邻点线性插值得到的预报潮高；
    - 邻点 = 实测时刻前后各 120 分钟（含）内的**其他**有效实测记录
      （落在预报覆盖范围内才有残差，范围外的观测不参与）；
    - 局部基线 = 邻点残差的中位数；邻点不足 3 个时不评估该记录；
    - 局部 MAD = 邻点残差与基线之差绝对值的中位数；
    - 异常阈值 = max(0.3, 3 × 1.4826 × MAD)；
    - 仅返回 |残差 − 基线| 严格大于阈值的记录。

    返回按时刻升序的 (时刻, 实测潮高, 残差, 基线, 偏离量)；预报不足 2 点
    或无足够邻点时返回空列表。
    """
    if len(forecast) < 2:
        return []

    first_t, last_t = forecast[0][0], forecast[-1][0]
    # 有效观测：落在预报覆盖范围内（含边界），同刻以最后一个为准
    valid: dict[datetime, float] = {}
    for t, observed in observations:
        if first_t <= t <= last_t:
            valid[t] = observed
    # 自身之外至少要有 3 个邻点才可能评估，少于 4 个点直接返回空
    if len(valid) < _ANOMALY_MIN_NEIGHBORS + 1:
        return []

    valid_times = sorted(valid)
    ts = [t.timestamp() for t in valid_times]
    residuals: dict[datetime, float] = {}
    for t in valid_times:
        residuals[t] = valid[t] - _piecewise_linear(forecast, t)

    anomalies: list[tuple[datetime, float, float, float, float]] = []
    for idx, t in enumerate(valid_times):
        center = ts[idx]
        lo = bisect_left(ts, center - _ANOMALY_WINDOW_SECONDS)
        hi = bisect_right(ts, center + _ANOMALY_WINDOW_SECONDS)
        neighbor_res = [
            residuals[valid_times[j]] for j in range(lo, hi) if j != idx
        ]
        if len(neighbor_res) < _ANOMALY_MIN_NEIGHBORS:
            continue
        baseline = _median(neighbor_res)
        mad = _median([abs(r - baseline) for r in neighbor_res])
        threshold = max(
            _ANOMALY_MIN_THRESHOLD, _ANOMALY_SIGMA * _ANOMALY_MAD_K * mad
        )
        deviation = abs(residuals[t] - baseline)
        if deviation > threshold + _EPS:
            anomalies.append((t, valid[t], residuals[t], baseline, deviation))
    return anomalies


def depth_deficit_audit(
    points: list[ForecastPoint],
    reference_depth: float,
    required_depth: float,
    start: datetime,
    end: datetime,
) -> tuple[list[tuple[datetime, datetime, float]], list[tuple[datetime, datetime]]]:
    """复核通航区间 [start, end] 的水深（按最新预报相邻点线性插值）。

    返回 (deficits, uncovered)：
    - deficits：通航区间内水深低于 required_depth 的连续时段
      [(起点, 终点, 最大欠深)]，跨预报点的连续欠深已合并，
      最大欠深 = required_depth − 时段内最低水深；
    - uncovered：预报首末点未覆盖的通航时段 [(起点, 终点)]，
      这些时段水深未知，调用方不得据此判为安全。
    """
    start_ts = start.timestamp()
    end_ts = end.timestamp()

    if len(points) < 2:
        # 无插值区间：整个通航区间都未被预报覆盖
        uncovered = [(start, end)] if start_ts < end_ts else []
        return [], uncovered

    ts = [p[0].timestamp() for p in points]
    depths = [reference_depth + p[1] for p in points]
    first_ts, last_ts = ts[0], ts[-1]

    uncovered_ts: list[tuple[float, float]] = []
    if start_ts < min(end_ts, first_ts):  # 通航起点早于首个预报点
        uncovered_ts.append((start_ts, min(end_ts, first_ts)))
    if max(start_ts, last_ts) < end_ts:  # 通航终点晚于末个预报点
        uncovered_ts.append((max(start_ts, last_ts), end_ts))

    lo = max(start_ts, first_ts)
    hi = min(end_ts, last_ts)

    deficits_ts: list[tuple[float, float, float]] = []
    cur_start: float | None = None
    cur_end: float | None = None
    cur_max = 0.0

    def flush() -> None:
        nonlocal cur_start, cur_end, cur_max
        if cur_start is not None:
            deficits_ts.append((cur_start, cur_end, round(cur_max, 6)))  # type: ignore[arg-type]
            cur_start = cur_end = None
            cur_max = 0.0

    for i in range(len(points) - 1):
        t0, t1 = ts[i], ts[i + 1]
        a, b = max(t0, lo), min(t1, hi)  # 插值段与通航区间（覆盖范围内）的交集
        if a >= b:
            continue
        # 线性插值出交集端点处的水深
        d0 = depths[i] + (depths[i + 1] - depths[i]) * (a - t0) / (t1 - t0)
        d1 = depths[i] + (depths[i + 1] - depths[i]) * (b - t0) / (t1 - t0)
        below0 = d0 < required_depth - _EPS
        below1 = d1 < required_depth - _EPS

        if not below0 and not below1:
            # 线性段两端都不欠深，整段达标；关闭当前欠深时段
            flush()
            continue
        if below0 and below1:
            seg = (a, b)
            seg_max = required_depth - min(d0, d1)
        elif below0:
            seg = (a, _cross_time(a, d0, b, d1, required_depth))
            seg_max = required_depth - d0
        else:
            seg = (_cross_time(a, d0, b, d1, required_depth), b)
            seg_max = required_depth - d1

        if cur_start is None:
            cur_start, cur_end, cur_max = seg[0], seg[1], seg_max
        elif seg[0] <= cur_end + 1e-6:  # type: ignore[operator]
            # 相邻段首尾相接（如共享的预报点同样欠深），合并为同一连续欠深时段
            cur_end = max(cur_end, seg[1])  # type: ignore[type-var]
            cur_max = max(cur_max, seg_max)
        else:
            flush()
            cur_start, cur_end, cur_max = seg[0], seg[1], seg_max

    flush()

    deficits = [(_to_utc(s), _to_utc(e), m) for s, e, m in deficits_ts]
    uncovered = [(_to_utc(s), _to_utc(e)) for s, e in uncovered_ts]
    return deficits, uncovered


def _to_utc(ts: float) -> datetime:
    """Unix 秒转 UTC datetime，四舍五入到整秒避免浮点噪声。"""
    return datetime.fromtimestamp(round(ts), tz=timezone.utc)


# ---------------------------------------------------------------------------
# 潮位预警时段扫描
# ---------------------------------------------------------------------------


def tide_alert_periods(
    points: list[ForecastPoint],
    direction: str,
    trigger_height: float,
    clear_height: float,
    min_duration_seconds: float,
) -> list[tuple[datetime, datetime, bool, float]]:
    """按最新预报扫描潮位预警时段（纯内存计算，不改入参）。

    - direction："high"（高潮）或 "low"（低潮）。高潮：潮高达到触发潮高进入
      预警、降至解除潮高结束（解除潮高低于触发潮高）；低潮反之：潮高降至
      触发潮高进入、升至解除潮高结束（解除潮高高于触发潮高）。两阈值之间的
      迟滞区间使潮高在触发阈值附近抖动时预警不解除、不拆分；
    - 相邻预报点线性插值，仅在预报覆盖时段 [首点, 末点] 内扫描（不外推）；
    - 首点已达到触发阈值则从首点预警，否则于首次达到触发阈值的交点进入；
      达到解除阈值的交点结束，交点作为时段边界；
    - 预报覆盖结束仍未解除的时段标记未闭合，终点取末个预报时刻；
    - 仅返回持续时长达到 min_duration_seconds 的时段，按起点升序。

    返回 [(起点, 终点, 未闭合标记, 期间极值潮高)]：高潮预警为期间最高潮高，
    低潮预警为期间最低潮高；预报不足 2 点（无插值区间）返回空列表。
    """
    if len(points) < 2:
        return []

    # 符号变换 g = sign × 潮高，使两种方向统一为“g 升至触发值进入、
    # 降至解除值结束”（校验保证解除值 < 触发值）；期间极值 = sign × max(g)
    sign = 1.0 if direction == "high" else -1.0
    ts = [p[0].timestamp() for p in points]
    gs = [sign * p[1] for p in points]
    trig = sign * trigger_height
    clr = sign * clear_height

    periods: list[tuple[datetime, datetime, bool, float]] = []
    in_alert = False
    start_ts = 0.0
    cur_max = 0.0  # 当前预警时段内 g 的最大值

    def flush(end_ts: float, unclosed: bool) -> None:
        # 仅保留持续时长达到最短要求的时段
        if end_ts - start_ts >= min_duration_seconds - _EPS:
            periods.append(
                (_to_utc(start_ts), _to_utc(end_ts), unclosed, sign * cur_max)
            )

    # 首点已达到触发阈值则从首点预警
    if gs[0] >= trig - _EPS:
        in_alert = True
        start_ts = ts[0]
        cur_max = gs[0]

    for i in range(len(points) - 1):
        t0, t1 = ts[i], ts[i + 1]
        g0, g1 = gs[i], gs[i + 1]

        if in_alert:
            if g1 <= clr + _EPS:
                # 达到解除阈值：预警于交点结束（段内单调，极值已在段首计入）
                if g0 <= clr + _EPS:
                    cross = t0  # 防御：段首已低于解除阈值，立即结束
                else:
                    frac = (g0 - clr) / (g0 - g1)
                    cross = t0 + min(max(frac, 0.0), 1.0) * (t1 - t0)
                flush(cross, unclosed=False)
                in_alert = False
            else:
                # 段内未解除：末点计入期间极值
                cur_max = max(cur_max, g1)
        elif g1 >= trig - _EPS:
            # 首次达到触发阈值：于交点进入预警
            if g0 >= trig - _EPS:
                cross = t0  # 防御：段首已达触发阈值
            else:
                frac = (trig - g0) / (g1 - g0)
                cross = t0 + min(max(frac, 0.0), 1.0) * (t1 - t0)
            in_alert = True
            start_ts = cross
            cur_max = max(trig, g0, g1)

    if in_alert:
        # 预报覆盖结束仍未解除 → 未闭合，终点取末个预报时刻
        flush(ts[-1], unclosed=True)

    return periods


# ---------------------------------------------------------------------------
# 分段航道通行时段
# ---------------------------------------------------------------------------

# 航段参数：(该段基准水深, 相对出发时刻的进入偏移秒, 通过秒数)
RouteSegment = tuple[float, float, float]


def route_departure_windows(
    points: list[ForecastPoint],
    required_depth: float,
    segments: list[RouteSegment],
) -> tuple[list[tuple[datetime, datetime]], datetime | None]:
    """分段航道通行时段：各段进入时段按进入偏移反推为出发时段后取交集。

    - segments：按航行顺序排列的 (基准水深, 进入偏移秒, 通过秒数)；进入偏移
      为相对出发时刻的秒数（首段为 0，后段不早于前段离开，由调用方校验）；
    - 每段在其基准水深下复用 sufficient_depth_windows 求
      基准水深 + 潮高 >= 所需水深 的达标窗口（等号达标），减去通过时长得到
      该段全程达标的进入时段（窗口恰等于通过时长时退化为单点进入，有效）；
    - 进入时段整体前移进入偏移即该段允许的出发时段；所有段的出发时段取
      连续交集（相接合并、零长度单点保留），即为全部可出发时段；
    - 各段进入时段本身限定在预报覆盖时段内，交集中的任意出发时刻自然
      保证所有航段的通过过程均处于预报覆盖内；预报不足 2 点（无插值区间）
      时返回空。

    返回 ([(start, end), ...], earliest)，时间均为 UTC datetime，按时间升序。
    """
    shifted: list[tuple[float, float]] | None = None
    for reference_depth, offset_seconds, transit_seconds in segments:
        # 该段全程水深达标的进入时段（Unix 秒），再前移进入偏移 → 出发时段
        entry_windows = [
            (start_ts, end_ts - transit_seconds)
            for start_ts, end_ts in sufficient_depth_windows(
                points, reference_depth, required_depth
            )
            if end_ts - start_ts >= transit_seconds - _EPS
        ]
        departed = [
            (s - offset_seconds, e - offset_seconds) for s, e in entry_windows
        ]
        shifted = departed if shifted is None else intersect_windows(shifted, departed)
        if not shifted:
            break
    result = [(_to_utc(s), _to_utc(e)) for s, e in (shifted or [])]
    earliest = min((w[0] for w in result), default=None)
    return result, earliest