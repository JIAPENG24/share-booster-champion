# Booster Champion 3v3 — 仿真足球 Agent 开发指南

##Booster Studio开发者社群
飞书连接：
https://applink.feishu.cn/client/chat/chatter/add_by_link?link_token=aa7ja218-5029-4473-bbfa-b94628a4ffcf

## §1 项目简介 (Project Overview)

> **[中文]** 基于 `booster_agent_framework` 的 3v3 仿真足球 Agent 示例项目，
> 面向 Booster Champion 参赛开发者。读取团队视角 ROS2 仿真真值与
> GameController 裁判状态, 通过 `py_trees` 行为树驱动策略, 向己方机器人
> 输出行走/踢球指令。
>
> **[English]** A 3v3 simulation soccer Agent example built on
> `booster_agent_framework`. It reads team-view ROS2 ground truth and
> GameController referee state, runs a `py_trees` behavior-tree strategy,
> and sends walking or kicking commands to the configured team robots.
>
> **平台**: [Booster Studio](https://studio.booster.tech/) 　
> **核心栈**: Python + `py_trees==2.4.0` 行为树

---

## §2 快速上手 (Quick Start)

```bash
# 1. 克隆项目并切换到 experiment/power-shot 分支
git clone <repo-url> && cd game3V3 && git checkout experiment/power-shot

# 2. 在 Booster Studio 中导入项目, 修改 src/play/ 下的策略代码

# 3. 构建 (Build) → 部署到虚拟机器人 (Deploy)
```

三步即可运行你的第一支 3v3 球队, 策略主入口在 `src/play/`, 详见 [§7](#7-策略开发指南-strategy-development-guide)。

## §3 架构一图 (Architecture)

```
play → behavior_tree → tactics → soccer_framework
runtime → 所有层
```

四层单向依赖: `soccer_framework` 为零依赖数据契约层, 向上提供
`PlayContext` / `RobotCommand` / `SoccerConfig` 等公共类型。
`runtime` 横跨所有层负责系统装配。参赛者主要在 `play/` 和 `tactics/` 层编写策略。

> 详细架构分析见 [docs/strategy-framework-analysis.md](docs/strategy-framework-analysis.md)

---

## §4 Runtime Environment

Agent 运行于 **Booster Studio** 平台, 以虚拟机器人形态执行。
下载地址: <https://studio.booster.tech/>

典型 Agent 项目结构:

```text
.
├── res/           # 静态资源 (logo 等)
├── src/           # Agent 逻辑代码
├── agent.toml     # Agent 入口: src/main.py:SoccerSimAgent
└── build.toml     # 依赖: py_trees==2.4.0, 平台: sim_x86_64 / sim_aarch64 / real_jetson
```

Agent 身份: `com.example.game3v3` v1.1.0, 支持模型: Booster T1 / Booster K1。

---

## §5 Code Map

回答两个问题: **代码长什么样** 和 **从哪里改起**。

### 5.1 目录结构

```text
src/
├── soccer_framework/   # 公共数据契约层 (零内部依赖)
│   ├── __init__.py     # PlayContext, RobotCommand, SoccerConfig
│   ├── types.py        # Pose2D, BallState, RobotState, PlayContextProvider
│   ├── config.py       # SoccerConfig, SoccerStrategyTuning
│   ├── game_state.py / ros_truth.py / game_controller.py  # GC + ROS 适配
│   ├── ros_adapter.py  # ROS 节点/订阅管理
│   ├── robot.py        # TeamRobotManager + kick/control
│   └── telemetry.py    # SoccerLogger + JSONL 日志
├── runtime.py          # SoccerKit + SoccerTeamRuntime 装配与控制循环
├── main.py             # Booster Agent 入口 (SoccerSimAgent)
├── tactics/            # 纯模型算法层 (无 BT/ROS 依赖)
│   ├── geometry.py / navigation.py     # 坐标变换 + 障碍物收集
│   ├── targeting/      # 射门/传球/接应/追球评分
│   ├── motion.py / kick_hysteresis.py  # 运动控制 + 踢球迟滞
│   └── ready_stance.py # READY 站位计算
├── behavior_tree/      # 行为树运行时框架
│   ├── tree.py / blackboard.py       # 组装 + 黑板
│   ├── safety_subtree.py / ready_subtree.py  # SafetyGuards + READY
│   └── nodes/          # Data / Conditions / Actions 叶子节点
└── play/               # PLAY 阶段策略 (参赛者主入口)
    ├── playbook.py / registry.py  # Playbook + 注册表
    ├── role.py / default_roles.py # 角色抽象 + 默认实现
    └── play_subtree.py / nodes.py # 子树工厂 + attack builder
```

### 5.2 依赖方向

```text
play             -> behavior_tree + tactics + soccer_framework
behavior_tree    -> runtime (types only) + tactics + soccer_framework
tactics          -> soccer_framework
runtime          -> play + behavior_tree + tactics + soccer_framework
soccer_framework -> (无内部依赖)
```

`SoccerKit`(runtime) 与 `play` 通过 `Playbook` 协议解耦: `SoccerKit` 提供工具, `play` 实现决策。

## §6 Behavior Tree Overview

```text
TeamRoot
├── DataLayer
│   ├── UpdateClock / UpdatePlayContext
│   ├── UpdateGameState / UpdateRecentBall / UpdateRobotPoses
│   └── UpdateRobotStatus(N)
├── MatchControl
│   ├── SafetyGuards (no GC / inactive / stopped / non-playing → StopAll)
│   ├── ReadyPhase → ReadySlots: GoReadyTarget(N)
│   ├── PlayingPhase → PlaybookCore: AssignRoles + Roles(Player(N))
│   └── unsupported state → StopAll
├── SafetyOverrides → PlayerSafety(N): allowed / fall-down / walk-mode
└── CommitTeamCommands
```

> 完整叶节点展开见 [docs/bt_structure.md](docs/bt_structure.md)

**坐标系约定**: 数据已归一化为己方视角: 己方球门 `x=-7.0`, 对方球门 `x=+7.0`, `+x` 为进攻方向。
**不要在 `play/` 或 `tactics/` 层二次镜像坐标。**
如有绝对坐标输入, 归一化应在 `PlayContextProvider` 适配层完成。

## §7 策略开发指南 (Strategy Development Guide)

### 7.1 快速修改入口 (Where To Start)

| 你想改什么 | 入口 |
|---|---|
| **角色分配** (如落后时全员进攻) | `Playbook.assign_roles` — [src/play/playbook.py](src/play/playbook.py) |
| **射门或传球目标** | `ChaserRole.kick_target` — [src/play/default_roles.py](src/play/default_roles.py) |
| **支援站位** | `SupporterRole.target` — [src/play/default_roles.py](src/play/default_roles.py) |
| **防守站位** | `DefenderRole.target` — [src/play/default_roles.py](src/play/default_roles.py) |
| **门将站位** | `GoalkeeperRole.target` — [src/play/default_roles.py](src/play/default_roles.py) |
| **新增角色** (如拦截者) | 继承 `RoleStrategy` — [src/play/role.py](src/play/role.py), `register_role()` |
| **注册新 Playbook** | `PLAYBOOKS.register(name, factory)` — [src/play/registry.py](src/play/registry.py) |
| **追球手选择** | `DefaultPlaybook.select_chaser` — [src/play/playbook.py](src/play/playbook.py) |
| **安全兜底** (数据缺失时停球) | `SafetyGuards` — [src/behavior_tree/safety_subtree.py](src/behavior_tree/safety_subtree.py) |
| **无角色球员回退行为** | `Playbook.waiting_command` — [src/play/playbook.py](src/play/playbook.py) |

### 7.2 三步快速调参

1. **调参** → `SoccerStrategyTuning` in [src/soccer_framework/config.py](src/soccer_framework/config.py):
   移动速度、踢球力度、避障距离等
2. **改角色** → [src/play/default_roles.py](src/play/default_roles.py):
   `ChaserRole` / `SupporterRole` / `GoalkeeperRole` 的目标与行为
3. **改战术** → [src/play/playbook.py](src/play/playbook.py):
   `assign_roles()` 角色分配、`select_chaser()` 追球评分

> 当前策略评价见 [docs/strategy-evaluation.md](docs/strategy-evaluation.md)

## §8 Playbook 扩展模板

### 8.1 AggressivePlaybook (落后时门将转支援)

```python
from src.play import DefaultPlaybook, PLAYBOOKS, PlayContext, RoleAssignment
from src.runtime import SoccerKit
from src.soccer_framework import SoccerConfig


class AggressivePlaybook(DefaultPlaybook):
    def assign_roles(self, context: PlayContext):
        base = super().assign_roles(context)
        game = context.known_game
        own_team = game.get_team_state(self.kit.config.team_id)
        other_team = next(
            (t for t in game.teams if t.team_number != self.kit.config.team_id), None
        )
        if (
            own_team is not None
            and other_team is not None
            and own_team.score + 1 < other_team.score
        ):
            mapping = dict(base.by_player)
            goalkeeper = next(
                (pid for pid, role in base.by_player.items() if role == "goalkeeper"), None
            )
            if goalkeeper is not None:
                mapping[goalkeeper] = "supporter"
            return RoleAssignment(mapping)
        return base


PLAYBOOKS.register("aggressive", AggressivePlaybook)
kit = SoccerKit(SoccerConfig())
tree = TeamStrategyTree(kit, PLAYBOOKS.create("aggressive", kit), context_provider)
```

也可跳过注册表: `TeamStrategyTree(kit, AggressivePlaybook(kit), context_provider)`

### 8.2 InterceptorRole (拦截传球线路)

```python
from src.play import (
    DefaultPlaybook, RoleStrategy, RoleAssignment, PlayContext, MoveToTarget,
)
from src.soccer_framework import Pose2D


class InterceptorRole(RoleStrategy):
    name = "interceptor"

    def target(self, kit, player_id: int, context: PlayContext) -> Pose2D:
        # 计算站位 Pose2D, 如对方传球线路上的拦截点
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
        self.register_role(InterceptorRole())  # 注册顺序 = Selector 分支优先级

    def assign_roles(self, context):
        return RoleAssignment({1: "chaser", 2: "interceptor", 3: "goalkeeper"})


kit = SoccerKit(SoccerConfig())
tree = TeamStrategyTree(kit, TacticalPlaybook(kit), context_provider)
```

### 8.3 GoalkeeperRole (条件清球)

满足条件时踢球, 否则移动到站位点:

```python
from src.play import AttackSubtreeConfig, RoleStrategy, build_attack_subtree
from src.soccer_framework import Pose2D


class GoalkeeperRole(RoleStrategy):
    name = "goalkeeper"

    def target(self, kit, player_id, context):
        return kit.ready_stance.goalkeeper_guard_target(context.known_ball)

    def wants_to_kick(self, kit, player_id, context):
        return kit.targeting.ball_in_own_defensive_area(context.known_ball)

    def kick_target(self, kit, player_id, context):
        return Pose2D(kit.field.opponent_goal_x(), 0.0, 0.0)

    def build_subtree(self, kit, player_id):
        return build_attack_subtree(
            kit, player_id,
            AttackSubtreeConfig(
                target_fn=lambda ctx: self.target(kit, player_id, ctx),
                kick_target_fn=lambda ctx: self.kick_target(kit, player_id, ctx),
                wants_kick_fn=lambda ctx: self.wants_to_kick(kit, player_id, ctx),
                reason_fn=lambda: "goalkeeper guard",
                kick_reason_fn=lambda t: kit.targeting.kick_reason(t, default="goalkeeper clear"),
            ),
        )
```

行为树每帧检查 `wants_to_kick` + `IsInKickRange` 决定踢球还是移动。
角色名在黑板上始终为 `"goalkeeper"`, 无需在 `assign_roles` 中临时改写。

---

## §9 红线速查 (Red Lines Quick Reference)

> 完整列表: [docs/策略开发红线与规则.md](docs/策略开发红线与规则.md)

| 类别 | 概要 |
|---|---|
| **TC 技术合规** | 仅使用官方公开 API, 禁止访问仿真内部接口、伪造命令、端口扫描等 (TC-01~TC-09) |
| **AR 框架架构** | 不可绕过 SafetyOverrides / DataLayer / CommitTeamCommands (AR-01~AR-10) |
| **CR 坐标配置** | 从环境变量读取 `team_id` / `robot_names`, 遵循己方坐标系 (CR-01~CR-05) |

---

## §10 Documentation

- [docs/strategy-framework-analysis.md](docs/strategy-framework-analysis.md) — 四层架构分析
- [docs/strategy-evaluation.md](docs/strategy-evaluation.md) — 当前策略评价
- [docs/bt_structure.md](docs/bt_structure.md) — 行为树叶节点展开
- [docs/developer_protocol.md](docs/developer_protocol.md) — 仿真数据协议 (ROS topics, GameController JSON, boosteros)
- [docs/策略开发红线与规则.md](docs/策略开发红线与规则.md) — 全部合规约束 (技术/架构/坐标/赛场)
- [docs/赛场规则.md](docs/赛场规则.md) — 比赛规则与场地参数
- [docs/增加Console输出日志操作文档.md](docs/增加Console输出日志操作文档.md) — 日志调试指南
