"""Pydantic 请求/响应模型。"""
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, Field, PlainSerializer, model_validator

# 响应中 UTC 时刻统一序列化为带显式偏移的 ISO 8601（...+00:00），
# 不随 pydantic 版本改用 “Z” 后缀，保证文档与响应格式稳定。
UtcDatetime = Annotated[
    datetime,
    PlainSerializer(
        lambda dt: dt.astimezone(timezone.utc).isoformat() if dt is not None else None,
        return_type=str,
        when_used="json",
    ),
]


class PortCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100, description="港口名称")
    reference_depth: float = Field(description="航道基准水深（米）")


class PortUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    reference_depth: float | None = Field(default=None, description="航道基准水深（米）")


class PortOut(BaseModel):
    id: int
    name: str
    reference_depth: float


class TideForecastPoint(BaseModel):
    forecast_time: datetime = Field(description="预报时刻（ISO 8601；无时区信息按 UTC 处理）")
    tide_height: float = Field(description="潮高（米，相对海图基准面）")


class TideForecastUpsert(BaseModel):
    """批量录入/更正预报点：同一港口同一时刻重复提交即覆盖（更正）。"""

    points: list[TideForecastPoint] = Field(min_length=1)


class TideForecastOut(BaseModel):
    forecast_time: UtcDatetime
    tide_height: float


class TideObservationPoint(BaseModel):
    obs_time: datetime = Field(description="实测时刻（ISO 8601；无时区信息按 UTC 处理）")
    tide_height: float = Field(description="实测潮高（米，相对海图基准面）")


class TideObservationUpsert(BaseModel):
    """批量写入实测潮高点：同港同一 UTC 时刻重复写入即覆盖旧值。"""

    points: list[TideObservationPoint] = Field(min_length=1)


class TideObservationOut(BaseModel):
    obs_time: UtcDatetime
    tide_height: float


class ObservationAnomaly(BaseModel):
    """一条实测潮高异常点：残差相对局部基线的偏离严格超过阈值。"""

    obs_time: UtcDatetime = Field(description="实测时刻")
    tide_height: float = Field(description="实测潮高（米，相对海图基准面）")
    residual: float = Field(description="残差（米）= 实测潮高 − 观测时刻线性插值预报潮高")
    baseline: float = Field(
        description="局部基线（米）= 前后各 120 分钟内其他有效实测残差的中位数"
    )
    deviation: float = Field(
        description="偏离量（米）= |残差 − 基线|，严格超过 max(0.3, 3×1.4826×MAD) 才返回"
    )


class WindowQuery(BaseModel):
    draft: float = Field(ge=0, description="船舶吃水（米）")
    safety_margin: float = Field(ge=0, description="安全富余（米）")
    duration_minutes: float = Field(gt=0, description="所需通航时长（分钟），必须为正数")


class DepartureWindow(BaseModel):
    start: UtcDatetime = Field(description="可出发时段起点")
    end: UtcDatetime = Field(description="可出发时段终点（达标窗口结束时刻 − 通航时长）")


class WindowQueryResult(BaseModel):
    port_id: int
    required_depth: float = Field(description="所需水深 = 吃水 + 安全富余（米）")
    duration_minutes: float
    earliest_departure: UtcDatetime | None = Field(
        description="最早可出发时刻；无可出发时段时为 null"
    )
    windows: list[DepartureWindow] = Field(description="全部可出发时段")


class BookableWindowQuery(BaseModel):
    """可预约时段查询：吃水、安全富余、正数通航分钟数与允许出发起止时刻。"""

    draft: float = Field(ge=0, description="船舶吃水（米）")
    safety_margin: float = Field(ge=0, description="安全富余（米）")
    duration_minutes: float = Field(gt=0, description="所需通航时长（分钟），必须为正数")
    allowed_departure_from: datetime = Field(description="允许出发范围起点（ISO 8601；无时区信息按 UTC）")
    allowed_departure_to: datetime = Field(description="允许出发范围终点（ISO 8601；无时区信息按 UTC）")

    @model_validator(mode="after")
    def _validate_range(self):
        start = self.allowed_departure_from
        end = self.allowed_departure_to
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if start > end:
            raise ValueError("allowed_departure_from 不得晚于 allowed_departure_to")
        return self


class BookableWindowResult(BaseModel):
    port_id: int
    required_depth: float = Field(description="所需水深 = 吃水 + 安全富余（米）")
    duration_minutes: float
    allowed_departure_from: UtcDatetime = Field(description="请求的允许出发范围起点")
    allowed_departure_to: UtcDatetime = Field(description="请求的允许出发范围终点")
    earliest_departure: UtcDatetime | None = Field(
        description="最早可预约出发时刻；无可预约时段时为 null"
    )
    windows: list[DepartureWindow] = Field(
        description="扣除同港有效预约占用后的全部可预约出发时段（按时刻升序；"
        "start == end 表示仅该单点可出发）"
    )


class MaxDraftQuery(BaseModel):
    """最大可通航吃水查询：提交安全富余、正数通航分钟数与允许出发起止时刻。"""

    safety_margin: float = Field(ge=0, description="安全富余（米）")
    duration_minutes: float = Field(gt=0, description="所需通航时长（分钟），必须为正数")
    allowed_departure_from: datetime = Field(description="允许出发范围起点（ISO 8601；无时区信息按 UTC）")
    allowed_departure_to: datetime = Field(description="允许出发范围终点（ISO 8601；无时区信息按 UTC）")

    @model_validator(mode="after")
    def _validate_range(self):
        start = self.allowed_departure_from
        end = self.allowed_departure_to
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if start > end:
            raise ValueError("allowed_departure_from 不得晚于 allowed_departure_to")
        return self


class MaxDraftResult(BaseModel):
    port_id: int
    safety_margin: float = Field(description="安全富余（米）")
    duration_minutes: float
    allowed_departure_from: UtcDatetime = Field(description="请求的允许出发范围起点")
    allowed_departure_to: UtcDatetime = Field(description="请求的允许出发范围终点")
    max_draft: float = Field(
        description="允许范围内全程水深达标的最大非负吃水（米，按 0.01 米向下取整）"
    )
    required_depth: float = Field(description="对应所需水深 = 最大吃水 + 安全富余（米）")
    departure_start: UtcDatetime = Field(description="该吃水下最早可行出发时刻")
    departure_end: UtcDatetime = Field(description="通航结束时刻 = 出发时刻 + 通航时长")


class ReservationCreate(BaseModel):
    ship_name: str = Field(min_length=1, max_length=100, description="船名")
    draft: float = Field(ge=0, description="船舶吃水（米）")
    safety_margin: float = Field(ge=0, description="安全富余（米）")
    duration_minutes: float = Field(gt=0, description="所需通航时长（分钟），必须为正数")
    allowed_departure_from: datetime = Field(description="允许出发范围起点（ISO 8601；无时区信息按 UTC）")
    allowed_departure_to: datetime = Field(description="允许出发范围终点（ISO 8601；无时区信息按 UTC）")

    @model_validator(mode="after")
    def _validate_range(self):
        start = self.allowed_departure_from
        end = self.allowed_departure_to
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if start > end:
            raise ValueError("allowed_departure_from 不得晚于 allowed_departure_to")
        return self


class ReservationReschedule(BaseModel):
    """预约改期请求：只提供新的允许出发起止时刻，其余沿用原预约。"""

    allowed_departure_from: datetime = Field(description="新的允许出发范围起点（ISO 8601；无时区信息按 UTC）")
    allowed_departure_to: datetime = Field(description="新的允许出发范围终点（ISO 8601；无时区信息按 UTC）")

    @model_validator(mode="after")
    def _validate_range(self):
        start = self.allowed_departure_from
        end = self.allowed_departure_to
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if start > end:
            raise ValueError("allowed_departure_from 不得晚于 allowed_departure_to")
        return self


class ReservationOut(BaseModel):
    id: int
    port_id: int
    ship_name: str
    draft: float
    safety_margin: float
    duration_minutes: float
    allowed_departure_from: UtcDatetime = Field(description="请求的允许出发范围起点")
    allowed_departure_to: UtcDatetime = Field(description="请求的允许出发范围终点")
    departure_start: UtcDatetime = Field(description="排定的出发时刻")
    departure_end: UtcDatetime = Field(description="排定的通航结束时刻 = 出发时刻 + 通航时长")
    status: str = Field(description="状态：active / cancelled")
    created_at: UtcDatetime
    canceled_at: UtcDatetime | None = None


class DepthAuditDeficit(BaseModel):
    start: UtcDatetime = Field(description="欠深时段起点")
    end: UtcDatetime = Field(description="欠深时段终点")
    max_deficit: float = Field(description="该时段最大欠深（米）= 所需水深 − 时段内最低水深")


class DepthAuditUncovered(BaseModel):
    start: UtcDatetime = Field(description="未覆盖时段起点")
    end: UtcDatetime = Field(description="未覆盖时段终点")


class DepthAuditResult(BaseModel):
    reservation_id: int
    port_id: int
    ship_name: str
    status: str = Field(description="预约状态：active / cancelled")
    departure_start: UtcDatetime = Field(description="排定的出发时刻")
    departure_end: UtcDatetime = Field(description="排定的通航结束时刻")
    draft: float = Field(description="船舶吃水（米）")
    safety_margin: float = Field(description="安全富余（米）")
    required_depth: float = Field(description="所需水深 = 吃水 + 安全富余（米）")
    coverage_complete: bool = Field(description="最新预报是否覆盖整个通航区间")
    uncovered: list[DepthAuditUncovered] = Field(
        description="预报未覆盖的通航时段（水深未知，不得视为安全）"
    )
    deficits: list[DepthAuditDeficit] = Field(
        description="通航区间内水深低于所需水深的连续时段（相邻段已合并）"
    )
    sufficient: bool = Field(
        description="整个通航区间水深是否均达标；预报覆盖不足时一律为 false"
    )


class ForecastImpactPreview(BaseModel):
    """预报影响预演请求：一批拟录入的潮高预报点。

    与库存预报按 UTC 规则合并（同一时刻按提交顺序后者覆盖前者）形成
    临时预报，仅用于预演判定，不写入数据库。
    """

    points: list[TideForecastPoint] = Field(min_length=1)


class ForecastImpactChange(BaseModel):
    """一条预约在预演前后的达标状态变化（仅状态发生变化的预约才会返回）。"""

    reservation_id: int = Field(description="达标状态发生变化的预约 ID")
    current_sufficient: bool = Field(
        description="按当前库存预报判定：排定通航区间是否全程水深达标"
    )
    preview_sufficient: bool = Field(
        description="按合并后临时预报判定：排定通航区间是否全程水深达标"
    )


class BridgeConfigUpsert(BaseModel):
    """设置桥梁净空配置：每港一处桥梁，重复提交即覆盖（更正）。"""

    bridge_bottom_elevation: float = Field(
        description="桥梁梁底高程（米，相对潮高基准面，与潮高预报同一基准）"
    )


class BridgeConfigOut(BaseModel):
    port_id: int
    bridge_bottom_elevation: float = Field(
        description="桥梁梁底高程（米，相对潮高基准面）"
    )


class BridgeWindowQuery(BaseModel):
    """桥梁净空通航窗口查询：吃水、水深富余、船舶水面以上高度、净空富余与正数通航分钟数。"""

    draft: float = Field(ge=0, description="船舶吃水（米）")
    depth_margin: float = Field(ge=0, description="水深富余（米）")
    air_draft: float = Field(ge=0, description="船舶水面以上高度（米）")
    clearance_margin: float = Field(ge=0, description="净空富余（米）")
    duration_minutes: float = Field(gt=0, description="所需通航时长（分钟），必须为正数")


class BridgeWindowQueryResult(BaseModel):
    port_id: int
    bridge_bottom_elevation: float = Field(description="桥梁梁底高程（米，相对潮高基准面）")
    required_depth: float = Field(description="所需水深 = 吃水 + 水深富余（米）")
    required_clearance: float = Field(
        description="所需净空 = 船舶水面以上高度 + 净空富余（米）"
    )
    duration_minutes: float
    earliest_departure: UtcDatetime | None = Field(
        description="最早可出发时刻；无可出发时段时为 null"
    )
    windows: list[DepartureWindow] = Field(
        description="水深与净空两条件连续交集减去通航时长后的全部可出发时段"
    )


class TideAlertRuleUpsert(BaseModel):
    """设置潮位预警规则：每港一条，重复提交即覆盖（更正）。"""

    direction: Literal["high", "low"] = Field(
        description="预警方向：high=高潮（潮高升至触发潮高进入预警），"
        "low=低潮（潮高降至触发潮高进入预警）"
    )
    trigger_height: float = Field(description="触发潮高（米，相对海图基准面）")
    clear_height: float = Field(description="解除潮高（米，相对海图基准面）")
    min_duration_minutes: float = Field(
        ge=0, description="最短持续分钟（非负；持续不足该时长的预警时段不返回）"
    )

    @model_validator(mode="after")
    def _validate_thresholds(self):
        if self.direction == "high" and self.clear_height >= self.trigger_height:
            raise ValueError("高潮预警的解除潮高必须低于触发潮高")
        if self.direction == "low" and self.clear_height <= self.trigger_height:
            raise ValueError("低潮预警的解除潮高必须高于触发潮高")
        return self


class TideAlertRuleOut(BaseModel):
    port_id: int
    direction: str = Field(description="预警方向：high=高潮 / low=低潮")
    trigger_height: float = Field(description="触发潮高（米，相对海图基准面）")
    clear_height: float = Field(description="解除潮高（米，相对海图基准面）")
    min_duration_minutes: float = Field(description="最短持续分钟（非负）")


class TideAlertPeriod(BaseModel):
    """一段潮位预警时段（按起点升序）。"""

    start: UtcDatetime = Field(
        description="预警开始时刻（首次达到触发潮高的交点；首点已触发则为首预报时刻）"
    )
    end: UtcDatetime = Field(
        description="预警结束时刻（达到解除潮高的交点；未闭合时为末个预报时刻）"
    )
    unclosed: bool = Field(description="预报覆盖结束时预警是否仍未解除")
    extreme_tide_height: float = Field(
        description="预警期间极值潮高（米）：高潮预警为期间最高潮高，低潮预警为期间最低潮高"
    )


class RouteSegment(BaseModel):
    """一段航段：基准水深、相对出发时刻的进入分钟数与正数通过分钟数。"""

    reference_depth: float = Field(description="该航段基准水深（米）")
    enter_offset_minutes: float = Field(
        description="相对出发时刻进入该航段的分钟数（首段必须为 0；"
        "后段不得早于前段离开）"
    )
    transit_minutes: float = Field(
        gt=0, description="通过该航段所需分钟数，必须为正数"
    )


class RouteWindowQuery(BaseModel):
    """分段航道通行时段查询：吃水、安全富余与按航行顺序排列的非空航段列表。"""

    draft: float = Field(ge=0, description="船舶吃水（米）")
    safety_margin: float = Field(ge=0, description="安全富余（米）")
    segments: list[RouteSegment] = Field(
        min_length=1, description="按航行顺序排列的航段列表（非空）"
    )

    @model_validator(mode="after")
    def _validate_segments(self):
        if self.segments[0].enter_offset_minutes != 0:
            raise ValueError("首段进入分钟数必须为 0")
        for prev, cur in zip(self.segments, self.segments[1:]):
            prev_leave = prev.enter_offset_minutes + prev.transit_minutes
            if cur.enter_offset_minutes < prev_leave:
                raise ValueError("后段进入分钟数不得早于前段离开时刻")
        return self


class RouteWindowResult(BaseModel):
    port_id: int
    required_depth: float = Field(description="所需水深 = 吃水 + 安全富余（米）")
    earliest_departure: UtcDatetime | None = Field(
        description="最早可出发时刻；无可出发时段时为 null"
    )
    windows: list[DepartureWindow] = Field(
        description="全部可出发时段（按时刻升序；start == end 表示仅该单点可出发）"
    )
