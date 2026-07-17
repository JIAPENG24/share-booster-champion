# Booster Champion 3v3 策略框架架构分析

> **面向**: 仿真足球 Agent 参赛开发者
> **基准分支**: `experiment/power-shot`
> **Agent 身份**: `com.example.game3v3` v1.1.0

---

## §1 项目概览

本 Agent 运行于 **Booster Studio** 平台之上, 以 `booster_agent_framework` 为运行时基座。
Agent 入口为 `src/main.py:SoccerSimAgent` (第 20 行), 继承自 `AgentBase`。
核心依赖为 **py_trees==2.4.0** (声明于 `build.toml`), 整个决策系统构建于行为树 (Behavior Tree) 范式之上。

Agent 通过 `agent.toml` 声明元数据, 在 Booster Studio 内以虚拟机器人形态运行,
每个 Agent 控制一支球队 (3 个机器人)。

---

## §2 四层架构详解

项目采用严格单向依赖的四层架构, 底层不反向引用上层:

```
play → behavior_tree → tactics → soccer_framework
runtime → 所有层
```

### Layer 0: `soccer_framework/` — 公共数据契约层

零内部依赖, 是最底层的基础设施。定义了所有上层共享的数据类型和配置。

| 关键文件 | 职责 |
|---------|------|
| `__init__.py` (L1-98) | 公共 API 入口: 导出 PlayContext, RobotCommand, SoccerConfig 等 |
| `types.py` (L1-435) | 核心数据类型: Pose2D, BallState, RobotState, PlayContext, RobotCommand, PlayContextProvider 等 |
| `config.py` (L57-320) | SoccerStrategyTuning (调参集) + SoccerConfig (全局配置) |
| `game_state.py` | GameController JSON 编解码 |
| `ros_truth.py` | RosTruthProvider: ROS 地面真值适配器 |
| `robot.py` | TeamRobotManager + kick/control 适配器 (boosteros 封装) |
| `game_controller.py` | GameControllerRosProvider: GC 话题订阅 |
| `ros_adapter.py` | SoccerRosAdapter: ROS 节点/订阅/执行器管理 |
| `telemetry.py` | SoccerLogger + JSONL 结构化日志 |

### Layer 1: `tactics/` — 纯模型算法层

零 BT/ROS 依赖, 仅依赖 `soccer_framework`。处理坐标几何、导航、运动控制等纯数学问题。

| 关键文件 | 职责 |
|---------|------|
| `geometry.py` | 坐标变换 + 己方视角场地几何 |
| `navigation.py` | ObstacleCollector 障碍物收集 |
| `targeting/__init__.py` | Targeting 门面: 射门/传球/接应/追球评分 |
| `targeting/attack.py` | 射门角度、传球队友、盘带目标评分 |
| `targeting/support.py` | 支援站位算法 (含队友间距排斥) |
| `targeting/restart.py` | 定位球避让目标点计算 |
| `targeting/recovery.py` | 边线/底线恢复目标 |
| `motion.py` | MotionController: 避障行走/踢球指令生成 |
| `kick_hysteresis.py` | 踢球进入/退出迟滞模型 |
| `ready_stance.py` | READY 站位计算 |

### Layer 2: `behavior_tree/` — 行为树运行时框架

提供完整的 BT 基础设施: 黑板、节点、子树工厂、顶层组装。

| 关键文件 | 职责 |
|---------|------|
| `tree.py` (L1-458) | TeamStrategyTree + create_team_tree 顶层组装 |
| `blackboard.py` (L1-103) | BlackboardKeys 键表 + BlackboardClient 封装 |
| `safety_subtree.py` (L1-202) | SafetyGuards / SafetyOverrides 子树工厂 |
| `ready_subtree.py` | READY 阶段子树工厂 |
| `nodes/data.py` (L1-192) | 数据层叶子: UpdateClock, UpdatePlayContext, UpdateGameState 等 |
| `nodes/conditions.py` | 通用条件叶子: HasGameState, IsBallKnown, IsRobotFallen 等 |
| `nodes/actions.py` (L1-320) | 通用动作叶子: StopAll, CommitTeamCommands, TriggerGetUp 等 |
| `__init__.py` | 导出 TeamStrategyTree, create_team_tree |

### Layer 3: `play/` — PLAY 阶段策略核心

参赛者的主要修改入口。实现动态角色分配、策略决策和角色行为子树。

| 关键文件 | 职责 |
|---------|------|
| `playbook.py` (L1-421) | Playbook / DefaultPlaybook: assign_roles, select_chaser |
| `role.py` (L1-126) | RoleStrategy 基类 + RoleRegistry |
| `registry.py` (L1-116) | PlaybookRegistry + 全局 PLAYBOOKS 单例 |
| `default_roles.py` | 默认角色: ChaserRole, SupporterRole, GoalkeeperRole, DefenderRole |
| `play_subtree.py` | create_play_subtree: PLAY 子树工厂 |
| `nodes.py` | 共享叶子节点 + build_attack_subtree |
| `__init__.py` (L50) | DefaultPlaybook 注册: `PLAYBOOKS.register("default", DefaultPlaybook, default=True)` |

---

## §3 行为树完整结构

以下树形结构与 `docs/bt_structure.md` 完全一致, 仅缩略展开到关键分支层级:

```text
Sequence(TeamRoot)
├── Sequence(DataLayer)                       ← 每帧初更新黑板数据
│   ├── UpdateClock                           ← 写入 /clock/now
│   ├── UpdatePlayContext                     ← 写入 /play_context
│   ├── UpdateGameState                       ← GC 新鲜度过滤 (2.0s)
│   ├── UpdateRecentBall                      ← 球新鲜度过滤 (1.5s)
│   ├── UpdateRobotPoses                      ← 位姿新鲜度过滤 (2.0s)
│   ├── UpdateRobotStatus(1)
│   ├── UpdateRobotStatus(2)
│   └── UpdateRobotStatus(3)
│
├── Selector(MatchControl)                    ← 裁判状态驱动的核心决策
│   ├── Selector(SafetyGuards)                ← 全局安全守卫 (任一命中→全队停止)
│   │   ├── NoGameStop          (无GC数据→StopAll)
│   │   ├── AllInactiveStop     (全员罚下→StopAll)
│   │   ├── StoppedPlayStop     (stopped=true→StopAll)
│   │   ├── NonPlayingStop      (非比赛状态→StopAll)
│   │   └── NoPlayingBallStop   (PLAYING无球→StopAll)
│   │
│   ├── Sequence(ReadyPhase)                  ← READY 站位
│   │   ├── IsGameInState(READY)
│   │   └── Parallel(ReadySlots): GoReadyTarget(1/2/3)
│   │
│   ├── Sequence(PlayingPhase)                ← 正常比赛
│   │   ├── IsGameInState(PLAYING)
│   │   └── Sequence(PlaybookCore)
│   │       ├── AssignRoles                   ← 写入 /team/roles
│   │       └── Parallel(Roles): Player(1/2/3)
│   │           ├── Selector(Player(N))
│   │           │   ├── KickoffHold(N)        ← 对手开球前站位保持
│   │           │   ├── PenaltyAvoid(N)       ← 对手定位球避让
│   │           │   ├── AsChaser(N)           ← 追球手分支
│   │           │   ├── AsSupporter(N)        ← 支援者分支
│   │           │   ├── AsGoalkeeper(N)       ← 守门员分支
│   │           │   ├── AsDefender(N)         ← 后卫分支 (自定义扩展)
│   │           │   └── WaitForBall(N)        ← 兜底: 无角色时的安全停球
│   │
│   └── StopAll("unsupported state")          ← MatchControl 最终兜底
│
├── Parallel(SafetyOverrides)                 ← 硬件状态兜底 (SuccessOnAll)
│   ├── PlayerSafety(1): AllowedGuard → FallDownGuard → WalkModeGuard
│   ├── PlayerSafety(2): AllowedGuard → FallDownGuard → WalkModeGuard
│   └── PlayerSafety(3): AllowedGuard → FallDownGuard → WalkModeGuard
│
└── CommitTeamCommands                        ← 收集 cmd/{id} 并提交给 executor
```

**要点说明**:
- `MatchControl` 是 Selector: 每个 tick 只有第一个成功分支执行。`SafetyGuards` 排在第一位, 确保异常状态优先抢占。
- `SafetyOverrides` 在 MatchControl 之后执行: 它可以检查本轮已写入的 `/cmd/{player_id}`, 对需要 walk 的指令强制切换模式, 对不允许/摔倒的玩家覆盖 Stop。
- `SafetyGuards` 命中后设置 `/safety/active=True`, `CommitTeamCommands` 据此跳过罚下处理, 保持 SafetyGuards 的停止原因。

> 更详细的结构 (含 Mermaid 图和逐叶子展开) 参见 `docs/bt_structure.md`。

---

## §4 Blackboard 数据流

### 关键 Blackboard 键

| 键 | 类型 | 写入者 | 说明 |
|---|------|-------|------|
| `/clock/now` | `float` | UpdateClock | 当前 tick 时间戳 |
| `/play_context` | `PlayContext` | UpdatePlayContext | 过滤后的环境快照 (GC + 球 + 队友/对手位姿) |
| `/team/roles` | `RoleAssignment` | AssignRoles | 当前帧动态角色映射 |
| `/cmd/{player_id}` | `RobotCommand` | 各角色分支 / SafetyOverrides | 每个球员的命令槽 |
| `/robot_status/{player_id}` | `RobotRuntimeStatus` | UpdateRobotStatus | 机器人硬件运行时状态 |
| `/safety/active` | `bool` | StopAll (写True) / UpdateClock (复位False) | 全局安全是否激活 |
| `/team/kickoff_phase` | `int` | KickoffController | 己方开球阶段: 0=普通, 1=ActiveKickoff, 2=RoleLock |
| `/runtime/executor` | `TeamCommandExecutor` | 外部调用者 | BT 到 runtime 的命令执行握手 |

### 数据流向

```text
RosTruthProvider.get_snapshot()                  ← ROS 话题回调收集原始数据
          │
          v
UpdatePlayContext → Blackboard(/play_context)    ← 写入 PlayContext 快照
          │
          v
UpdateGameState / UpdateRecentBall               ← 新鲜度原地过滤
/ UpdateRobotPoses                                 (球 1.5s, 位姿 2.0s, GC 2.0s)
          │
          v
策略层只读 Blackboard                             ← AssignRoles + 各角色分支
(不直接对接 ROS)
          │
          v
CommitTeamCommands 收集 cmd/{id}                  ← 打包全队命令
          │
          v
executor.execute_team_commands()                  ← 调用 TeamRobotManager.apply_command()
```

### Staleness 过滤阈值

所有过滤集中在 `src/behavior_tree/nodes/data.py` (L33-35):

| 数据 | 阈值 | 过滤节点 |
|------|------|---------|
| 球位置 | 1.5s | `UpdateRecentBall` (L115-134) |
| 机器人位姿 | 2.0s | `UpdateRobotPoses` (L137-166) |
| GameController 状态 | 2.0s | `UpdateGameState` (L89-112) |

超时数据在原位被设为 `None`, 策略层通过 `if ball is not None` 判断即可, 无需自行检查时间戳。

---

## §5 扩展机制

### 5.1 Playbook 派生与注册

从 `DefaultPlaybook` 派生并仅覆盖两三个方法是最常见的扩展方式。以下示例来源于 `README.md` 第 187-225 行:

```python
from src.behavior_tree import TeamStrategyTree
from src.play import DefaultPlaybook, PLAYBOOKS, PlayContext, RoleAssignment
from src.runtime import SoccerKit
from src.soccer_framework import SoccerConfig


class AggressivePlaybook(DefaultPlaybook):
    """落后时门将变为支援者, 全员压上进攻。"""

    def assign_roles(self, context: PlayContext):
        base = super().assign_roles(context)
        game = context.known_game
        own_team = game.get_team_state(self.kit.config.team_id)
        other_team = next(
            (team for team in game.teams
             if team.team_number != self.kit.config.team_id),
            None,
        )
        if (
            own_team is not None
            and other_team is not None
            and own_team.score + 1 < other_team.score
        ):
            mapping = dict(base.by_player)
            goalkeeper = next(
                (player_id for player_id, role in base.by_player.items()
                 if role == "goalkeeper"), None)
            if goalkeeper is not None:
                mapping[goalkeeper] = "supporter"
            return RoleAssignment(mapping)
        return base


# 注册 (与 DefaultPlaybook 注册方式完全相同)
PLAYBOOKS.register("aggressive", AggressivePlaybook)

kit = SoccerKit(SoccerConfig())
tree = TeamStrategyTree(kit, PLAYBOOKS.create("aggressive", kit), context_provider)
```

注册接口定义于 `src/play/registry.py` (L52-76):

```python
PLAYBOOKS.register(name, factory, default=False)
# factory: Callable[[SoccerKit], Playbook] — 即 Playbook 类本身
```

DefaultPlaybook 在 `src/play/__init__.py` 第 50 行以同样方式注册:

```python
PLAYBOOKS.register("default", DefaultPlaybook, default=True)
```

### 5.2 Role 注册

在 `Playbook.__init__` 中调用 `register_role(RoleStrategy 子类实例)` 即可添加新角色。
注册顺序决定了 `Player(N)` Selector 中角色分支的优先级。接口定义于 `src/play/playbook.py` (L87-95):

```python
def register_role(self, role: RoleStrategy) -> "Playbook":
    self._registry.register(role)
    return self
```

### 5.3 新增角色示例 (Interceptor)

以下示例来源于 `README.md` 第 241-277 行, 展示如何添加一个拦截传球路线的角色:

```python
from src.play import (
    DefaultPlaybook, RoleStrategy, RoleAssignment,
    PlayContext, MoveToTarget,
)
from src.soccer_framework import Pose2D


class InterceptorRole(RoleStrategy):
    name = "interceptor"

    def target(self, kit, player_id: int, context: PlayContext) -> Pose2D:
        # 计算拦截传球路线的站位 Pose2D
        ...

    def build_subtree(self, kit, player_id: int):
        return MoveToTarget(
            kit, player_id,
            lambda context: self.target(kit, player_id, context),
            reason_fn=lambda: "interceptor hold",
        )


class TacticalPlaybook(DefaultPlaybook):
    def __init__(self, kit):
        super().__init__(kit)
        self.register_role(InterceptorRole())  # 注册顺序决定分支优先级

    def assign_roles(self, context):
        return RoleAssignment({
            1: "chaser", 2: "interceptor", 3: "goalkeeper"
        })
```

纯定位角色只需实现 `target()` 和 `build_subtree()`, 使用 `MoveToTarget` 叶节点。
需要条件踢球的角色 (如门将出击解围) 可额外实现 `wants_to_kick()` 和 `kick_target()`,
通过 `build_attack_subtree()` 组装。

---

## §6 SoccerStrategyTuning 配置体系

`SoccerStrategyTuning` (定义于 `src/soccer_framework/config.py` L57-173) 是集中调参的数据类, 包含 50+ 参数项, 按功能分组:

| 参数分组 | 示例参数 | 行号 |
|---------|---------|------|
| **速度限制** | `max_linear_speed`, `max_lateral_speed`, `max_angular_speed` | L70-73 |
| **踢球迟滞** | `soccer_kick_enter_distance`, `soccer_kick_exit_distance`, `soccer_kick_power` 等 5 项 | L77-82 |
| **定位球重启** | `restart_touch_distance`, `opponent_restart_avoid_distance_m` | L85-88 |
| **避障 (路径绕行)** | `opponent_obstacle_radius`, `teammate_obstacle_radius`, `obstacle_safety_margin` 等 5 项 | L96-103 |
| **偏航避让** | `yaw_avoid_horizon_sec`, `yaw_avoid_min_distance_m`, `yaw_avoid_bias_max` | L113-117 |
| **传球** | `pass_enabled`, `pass_min_score`, `pass_min_forward_m`, `pass_lane_clearance` | L125-128 |
| **盘带** | `dribble_advance_m`, `dribble_center_pull` | L131-132 |
| **支援站位** | `support_depth_m`, `support_lateral_m`, `support_min_spacing_m` | L135-137 |
| **守门员** | `goalkeeper_challenge_area_x_ratio`, `goalkeeper_kick_power`, `goalkeeper_guard_arc_radius` 等 10+ 项 | L140-166 |
| **球轨迹预测** | `ball_prediction_history_size`, PID 增益 (`kp/ki/kd`), `ball_prediction_friction` 等 6 项 | L152-157 |
| **边线恢复** | `sideline_recovery_margin_m`, `sideline_recovery_infield_m` 等 | L169-172 |

所有参数通过 `SoccerConfig.strategy.*` 访问。例如:

```python
config = SoccerConfig()
speed = config.strategy.max_linear_speed          # 0.8
kick_power = config.strategy.soccer_kick_power     # 10.0
```

`SoccerConfig` (L176-320) 持有 `strategy: SoccerStrategyTuning` 字段 (L210), 通过 `from_env()` 工厂方法仅从环境变量加载 `team_id` 和 `robot_names`, 其余参数使用默认值或构造函数传入。

---

## §7 框架红线对照 (AR-01 至 AR-10)

每条红线的定义来源为 `docs/策略开发红线与规则.md` §二 "框架架构红线"。以下映射具体代码位置。

| 红线 | 含义 | 代码映射 |
|------|------|---------|
| **AR-01** | 不可绕过 SafetyOverrides | `src/behavior_tree/safety_subtree.py` L190-202: `create_safety_overrides_subtree()` 在 `tree.py` L119 被挂载到 TeamRoot 中, 位于 MatchControl 之后、CommitTeamCommands 之前。必须在此阶段检查并覆盖 `cmd/{id}`, 不可跳过。 |
| **AR-02** | 不可绕过 DataLayer | `src/behavior_tree/nodes/data.py` L94-108: DataLayer Sequence 是每帧第一个执行的分支, 所有黑板数据经此写入。策略层通过 `blackboard.read()` 只读获取, 不得直接调用 ROS 源或 provider。 |
| **AR-03** | 不可绕过 CommitTeamCommands | `src/behavior_tree/nodes/actions.py` L233-275: 唯一握手点。收集所有 `/cmd/{player_id}` 后调用 `executor.execute_team_commands()`, 并清除命令槽防止过期残留。 |
| **AR-04** | `play/` 层不可导入 py_trees | `src/play/role.py` L49: `import py_trees` 被包裹在 `if TYPE_CHECKING:` 块内, 运行时不会执行。角色仅通过 `build_subtree()` 返回 `Behaviour` 对象, 由上层调用者负责 BT 组装。 |
| **AR-05** | 策略代码不可直接调用 boosteros | 策略代码仅输出 `RobotCommand` (定义于 `src/soccer_framework/types.py` L398-418)。实际 boosteros 调用集中在 `src/soccer_framework/robot.py:TeamRobotManager`, 由 runtime 通过 `execute_team_commands()` 统一调度 (`src/runtime.py` L339-345)。 |
| **AR-06** | 不可移除 Player 守卫分支 | `src/play/play_subtree.py`: `create_play_subtree` 中每个 `Player(N)` Selector 的 KickoffHold 和 PenaltyAvoid 分支是规则必需的守卫, 必须保留在角色分支之前。 |
| **AR-07** | 不可移除 WaitForBall 兜底 | `src/play/nodes.py`: WaitForBall 作为 `Player(N)` Selector 的最后一个 fallback 分支, 在无角色分配或数据缺失时提供安全停球指令, 防止 BT 级联失败。 |
| **AR-08** | 不可绕过 SafetyGuards | `src/behavior_tree/safety_subtree.py` L55-103: SafetyGuards Selector 在 `tree.py` L112-117 挂载为 MatchControl 的第一个分支, 执行优先级高于 ReadyPhase 和 PlayingPhase。包含 NoGameStop / AllInactiveStop / StoppedPlayStop / NonPlayingStop / NoPlayingBallStop 五条守卫。 |
| **AR-09** | SafetyOverrides 内三层守卫不可绕过 | `src/behavior_tree/safety_subtree.py` L111-187: 每个 `PlayerSafety(N)` 内包含 AllowedGuard (L151-166, 罚下/离场检查)、FallDownGuard (L135-149, 摔倒恢复)、WalkModeGuard (L167-182, 步态模式切换) 三层 Selector, 各含 `AlwaysSuccess` fallback 以保证 PlayerSafety 始终返回 SUCCESS。 |
| **AR-10** | set_velocity 与 SoccerKickManager 互斥 | `src/soccer_framework/robot.py:TeamRobotManager.apply_command()`: 同一机器人在同一时刻只能使用 walking (`set_velocity`) 或 kicking (`SoccerKickManager`) 之一。该互斥逻辑由底层 `PlayerKickStateMachine` 在机器人控制层保证。 |

---

> **扩展阅读**:
> - `docs/bt_structure.md` — 完整行为树逐叶子展开 (含 Mermaid 图)
> - `docs/developer_protocol.md` — 仿真环境数据协议 (ROS topics / GameController JSON / boosteros)
> - `docs/策略开发红线与规则.md` — 全部合规约束 (技术合规 + 框架架构 + 坐标配置 + 赛场规则)
> - `README.md` — 项目入口文档, 含代码地图和扩展示例
