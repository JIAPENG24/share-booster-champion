# Booster Champion 3v3 策略评估文档

> **分支**: `experiment/power-shot`
> **基线**: `main`
> **撰写日期**: 2026-07-17
> **阅读对象**: 3v3 仿真足球 Agent 参赛开发者

---

## §1 文档说明

### 1.1 分支信息

本文档基于 `experiment/power-shot` 分支撰写，评价当前策略的 7 大模块。该分支相对于 `main` 的差异主要为 26 个文件共 3107 行新增/204 行删除，核心目标是统一踢球力度为 `soccer_kick_power=10.0`（`src/soccer_framework/config.py:79`），同时引入 GK 三状态机、BallPredictor 球轨迹预测、开球四阶段系统等新策略模块。

`aggressive-attempt` 分支在 power-shot 之上追加了 FIX-1~FIX-4 稳定性修复（新增 3957 行/删除 513 行），但**尚未合并**到 power-shot。本文档仅客观描述差异，不推荐合入。

### 1.2 评价标准

每个策略模块从以下四个维度评价：

| 维度 | 说明 |
|------|------|
| **正确性** | 策略逻辑是否合理，是否符合比赛规则和物理约束 |
| **稳定性** | 是否存在振荡、切换频繁、或边界条件处理不当 |
| **可扩展性** | 参数/结构是否便于调试和定制 |
| **代码质量** | 命名清晰度、注释完整度、接口一致性 |

---

## §2 角色分配策略

### 2.1 参考源码

- `src/play/playbook.py:133-185` — `DefaultPlaybook.assign_roles`
- `src/play/playbook.py:297-326` — `_slot_can_challenge`（KEEPER 挑战资格与滞回）

### 2.2 当前行为

`DefaultPlaybook.assign_roles` 在每个 tick 执行三步分配：

1. **选 GK**: `_configured_goalkeeper()` 获取配置的 GK player_id（`playbook.py:189-190`）
2. **选 chaser**: `select_chaser()` 基于 `ball_claim_score` 评分选出最适合追球的队友（`playbook.py:192-295`，详见 §3）
3. **剩余 → supporter**: 既不是 GK 也不是 chaser 的球员全部设为 supporter（`playbook.py:177-178`）

最终产出一个 `RoleAssignment({goalkeeper_id: "goalkeeper", chaser_id: "chaser", 剩余: "supporter"})`。

固定 1-1-1 阵型：场上永远保持 1 个 chaser（追球）、1 个 supporter（支援）、1 个 GK（防守）。

KEEPER 的挑战资格通过 `_slot_can_challenge`（`playbook.py:297-326`）控制，使用 `ball_in_own_defensive_area` 判断球是否进入防守区域，配合 `goalkeeper_challenge_hysteresis_m=0.30`（`config.py:142`）的滞回和 `goalkeeper_clear_hold_sec=1.5`（`config.py:143`）的最短持有时间防止振荡。

### 2.3 评价

**优点**:
- 逻辑简单、可靠：三个角色各司其职，不会出现无人追球或无人守门
- 角色数量固定为 3，不会因为动态 role 数量变化导致 BT 子树重建
- KEEPER 挑战有滞回+持有时长双重保护，避免 GK 频繁进出挑战状态

**缺点**:
- 无法根据比分动态调整阵型：落后时仍然保持 1-1-1，GK 不能临时变角色参与进攻
- 没有考虑球区域对角色分配的反馈：球在对方半场时 GK 其实可以前压（support）
- 固定分配让纯定位策略场景的灵活性受限

### 2.4 改进建议

在 `assign_roles` 中增加比分感知逻辑（`playbook.py:166` 附近），当比分落后且球在对方半场时，将 GK 的 role 改为 `supporter` 实现「全体压上」。示例伪代码：

```
if trailing and ball.x > 0:
    mapping[goalkeeper_id] = ROLE_SUPPORTER
```

或者通过 `Playbook.register_role(DefenderRole())` 注册一个 Defender 角色，落后时让 GK 变 chaser/supporter 而 Defender 接管后场。

---

## §3 追球选择算法

### 3.1 参考源码

- `src/play/playbook.py:192-295` — `DefaultPlaybook.select_chaser`
- `src/tactics/targeting/predicates.py:95-114` — `ball_claim_score`
- `src/soccer_framework/config.py:120-121` — `teammate_challenge_tie_margin_m=0.15`

### 3.2 当前行为

`select_chaser` 每个 tick 评估所有符合挑战资格的 teammates，选出最低 `ball_claim_score` 的球员作为 chaser。

**ball_claim_score 评分规则**（`predicates.py:95-114`）:

| ReadySlot | 得分公式 | 说明 |
|-----------|----------|------|
| KEEPER | `distance + field_length` 或 `distance - 0.75` | 球在防守区时减 0.75 优惠；否则加 field_length 惩罚（≈14m），基本阻止 GK 追球 |
| CENTER | `distance - 0.20` | 优先鼓励 CENTER 追球 |
| SIDE | `distance - 0.10` | 略低于 CENTER |

**动态锁定期**（`playbook.py:236-262`）:

| 球位置条件 | 锁定期时长 | 说明 |
|-----------|-----------|------|
| `ball.x < own_goal_x + 1.5m` | 3.0s | 近门线，极度危险 |
| `ball.x < area_x`（防守区） | 2.0s | 防守区，持续刷新锁 |
| 其他（进攻区） | 0.5s | 正常比赛 |

当新选出的 chaser 与当前 chaser 不同时，若当前 chaser 的 claim score 仍在 `best_score + 0.5` 以内且锁未到期，则保持当前 chaser 不变（`playbook.py:254-262`）。

锁刷新机制：球在防守区时每个 tick 都会将 `_chaser_lock_until` 推前（`playbook.py:248-251`），确保防守状态下 chaser 不会中途被切换。

**平局处理**: 先按 claim_score 排名，再用 `teammate_challenge_tie_margin_m=0.15`（`config.py:121`）做平局 band，最后选最低 player_id（`playbook.py:227-233`）。

### 3.3 评价

**优点**:
- 锁定期有效防止追球员的乒乓切换，防守区 2s 持有确保 chaser 有充足时间到达球附近
- KEEPER 的 field_length 惩罚合理，GK 只在球进入防守区时才被允许挑战
- 平局选最低 player_id 策略确定性好，便于调试和日志审计

**缺点**:
- 锁定期使用 `time.time()`（`playbook.py:247`, `playbook.py:265`），而 BT 框架（如 BallPredictor, default_roles.py 中的 `time.monotonic()`）使用 `time.monotonic()`。两者时钟源不一致，在运行时可能因系统时间调整（NTP 同步、手动修改）导致锁定期计算错误
- 锁刷新逻辑（防守区每 tick 推前）意味着只要球一直在防守区、chaser 永远不会被切换，即使是更优的队友出现了
- claim_score 评分仅考虑距离和 slot，没有考虑球员朝向（theta）——背对球的球员和面对球的球员得分相同

### 3.4 改进建议

1. **统一时钟源**（`playbook.py:247`）: 将 `time.time()` 替换为 `time.monotonic()`，与 `ball.last_seen_at` 和 `GoalkeeperRole._last_update_time`（`default_roles.py:457`）保持一致
2. 在 `ball_claim_score` 中增加朝向因子：球员面对球方向（theta 接近球方向）得分更低，背对球加惩罚

---

## §4 GK 三状态机

### 4.1 参考源码

- `src/play/default_roles.py:417-572` — `GoalkeeperRole._update_gk_state`（状态机核心）
- `src/play/default_roles.py:573-612` — `GoalkeeperRole._transition`（状态转换与日志）
- `src/play/default_roles.py:618-762` — `target`/`wants_to_kick`/`kick_target`（公共接口）
- `src/play/default_roles.py:768-845` — `_rush_out_target`/`_lateral_target`（状态目标计算）
- `src/play/default_roles.py:851-875` — `_smooth_target`（轨迹平滑）
- `src/tactics/targeting/ball_prediction.py:1-336` — `BallPredictor`（全文件，球轨迹预测）
- `src/tactics/ready_stance.py:178-204` — `goalkeeper_guard_target`（弧线站位公式）
- `src/soccer_framework/config.py:139-166` — GK 相关参数

### 4.2 当前行为

GK 采用三状态机驱动（`default_roles.py:437-440`）：

```
_GUARD = 0      # 默认弧线站位
_RUSH_OUT = 1   # 球预测停在防守区 → 出击拦截
_LATERAL = 2    # 球预测穿过门线 → 横向封堵
```

**状态转换条件**（`_update_gk_state`, `default_roles.py:501-571`）:

| 转换 | 条件 |
|------|------|
| → GUARD | 球在对方半场（`ball.x >= 0.0`）强制切回；或 RUSH_OUT / LATERAL 条件不再满足 |
| → RUSH_OUT | 球预测停止点（`predict_rest_point`）落在防守区内：`rest_x < area_x - rush_margin` 且 `abs(rest_y) <= area_y`，entry margin=0.8m，exit margin=0.3m（实现出入滞回） |
| → LATERAL | 球预测穿过门线且在门柱范围内（`predict_goal_crossing` 返回 `is_on_target=True`），优先级最高 |

**防抖机制**:
- 进入新状态：需 `gk_state_confirm_frames=2` 连续帧确认（`config.py:160`）
- 退出 RUSH_OUT：需 `gk_state_release_frames=4` 连续帧确认（`config.py:161`），更难退出
- LATERAL 最小保持 `gk_lateral_hold_min_sec=0.8s`（`config.py:165`），防止在门框边界附近抖动

**各状态的目标计算**:

| 状态 | 目标逻辑 | 源码 |
|------|----------|------|
| GUARD | 弧线站位:球-to-门线中心线与 circle(GK, `goalkeeper_guard_arc_radius=1.4m`) 的交点，再 blend goal_line + `guard_depth_m=1.3m` | `ready_stance.py:178-204` |
| RUSH_OUT | 走向球预测停止点（`predict_rest_point`），对齐 kick_target 方向的 approach 偏移 | `default_roles.py:768-809` |
| LATERAL | 在 guard_depth 线上横向移动到球预计过门线的 y 坐标，clamp 在门柱范围内 | `default_roles.py:811-845` |

**BallPredictor 球轨迹预测**（`ball_prediction.py:1-336`）:
- 速度估计：最近 N=10 个样本的**最小二乘法线性回归**（更抗噪，优于两点差分）
- 速度平滑：PID 滤波器（kp=0.6, ki=0.05, kd=0.1），带积分抗饱和（clamp ±5.0）
- 轨迹外推：指数摩擦模型 `v(t) = v0 * exp(-mu * t)`，`x(t) = x0 + (v0/mu) * (1 - exp(-mu * t))`
- 在线摩擦估计：从速度衰减方向投影加速度到速度向量，指数平滑更新：`mu = 0.9 * mu + 0.1 * decel`
- 预测 horizon：`max_horizon_sec=2.0s`

**desperation clear**（`default_roles.py:626-640`）:
- 触发条件：`ball.x < own_goal_x + gk_desperation_clear_margin_m`（1.5m from goal line）
- 行为：跳过状态机，直接 approach 球 → kick 到对方球门中心
- 优先级最高，即使在其他状态也会覆盖 target

**kick 方向选择**（`default_roles.py:691-762`）:
- 从 5 个候选方向选最优：center、top post、bottom post、top sideline、bottom sideline
- 评分：`lane_clear_score - 0.3 * turn`（lane 越干净 + 转身角度越小 = 分数越高）
- 方向锁定：`_last_kick_dir_index` 在首次进入 kick 模式时选定，状态切换（`_transition`）时重置

### 4.3 评价

**优点**:
- 三状态设计覆盖了 GK 的主要防守场景：站位（GUARD）、出击（RUSH_OUT）、封堵（LATERAL），逻辑完整
- BallPredictor 的线性回归+PID 速度估计比简单差分更抗噪；在线摩擦估计使预测能自适应场地摩擦变化
- 状态切换防抖完备：entry/exit 不同阈值、LATERAL 最小保持时间、RUSH_OUT entry/exit margin 滞回，整体振荡风险低
- desperation clear 作为最后防线确保极端场景下的强制解围
- kick 方向从 5 候选选最优 + 方向锁定，避免每次踢球方向抖动

**缺点**:
- 摩擦模型假设**指数衰减**（`ball_prediction.py:8-9`），但实际仿真物理可能不是严格的指数摩擦。如果实际是线性摩擦或 Coulomb 摩擦，预测会偏小或偏大
- BallPredictor 的预测 horizon 限制为 `max_horizon_sec=2.0s`，对于远处低速滚动的球，可能不需要这么长，但对快速球（如大脚解围）可能不够
- desperation clear 的 margin=1.5m 是合理的安全距离，但 ball.x 低于此线时直接放弃站位防守，如果球速很慢，其实可以同时站位
- goalkeeper_guard_target 的 clamp_y=1.35（`ready_stance.py:199`）硬编码，未与 goal_width 联动

### 4.4 改进建议

1. **摩擦模型验证**（`ball_prediction.py:8-9`）: 在仿真环境中采集真实球的 x(t) 数据，与指数模型拟合度对比。如偏差较大，考虑增加线性摩擦或 Coulomb 摩擦模式的可选分支
2. **guard Y clamp**（`ready_stance.py:199`）: 将 `clamp(arc_y, -1.35, 1.35)` 的 1.35 替换为 `goal_width / 2.0 + 0.15`，与 `config.goal_width` 联动

---

## §5 开球四阶段系统

### 5.1 参考源码

- `src/play/play_subtree.py:284-483` — `PlayKickoffController`（阶段控制器）
- `src/play/play_subtree.py:490-561` — `create_play_subtree`（主工厂函数）
- `src/play/play_subtree.py:138-207` — `_create_active_kickoff_roles`（Phase 1: 我方开球）
- `src/play/play_subtree.py:215-276` — `_create_opp_defense_roles`（Phase 3: 对方开球防守）

### 5.2 当前行为

开球阶段通过 `BlackboardKeys.KICKOFF_PHASE` 在 0~3 之间切换，由 `PlayKickoffController` 在每个 tick 评估转换条件，然后 `create_play_subtree` 的 Selector 按优先级分发到对应子 BT。

**Phase 流转图**:

```
Phase 0 (NormalPlay)
    │
    ├── 我方开球 (kicking_team=us, ball near center)
    │   └─→ Phase 1 (ActiveKickoff)
    │          │ CENTER 45° 踢球, SIDE 跑 landing point
    │          │ ball moved > 0.15m → wait 0.5s
    │          └─→ Phase 2 (RoleLock)
    │                 │ SIDE→chaser, CENTER→supporter
    │                 │ side 接近球 (0.3m) → wait 1.0s
    │                 │ 或 2s timeout
    │                 └─→ Phase 0
    │
    └── 对方开球结束 (secondary_time 下降沿)
        └─→ Phase 3 (OppDefense)
               │ CENTER 0.5x, SIDE 1.3x, KEEPER 1.0x 速度压迫
               │ 我方球员碰到球 (0.3m) 或 5s timeout
               └─→ Phase 0
```

**Phase 1: ActiveKickoff**（`play_subtree.py:138-207`）:
- **CENTER**: approach ball → 在 kick range 内以 `kickoff_kick_power=1.0`（`config.py:80`）向 45°（`_KICKOFF_ANGLE=0.785 rad`）踢球
- **SIDE**: 走向 45° 方向 `_KICKOFF_LANDING_DIST=2.5m` 处的预测落点（`MoveToLandingPoint`），速度 multiplier 1.3x
- **KEEPER**: 正常 guard 站位

**Phase 1→2 转换**: 检测到球位移 >0.15m 后等待 0.5s 延迟（`play_subtree.py:405-419`），确保球已离开开球点

**Phase 2: RoleLock**（`play_subtree.py:520-533`）:
- 固定角色: SIDE → chaser, CENTER → supporter, KEEPER → goalkeeper
- 退出条件: SIDE 接近球 <0.3m 并等待 1.0s 确认，或 2s timeout

**Phase 3: OppDefense**（`play_subtree.py:215-276`）:

| ReadySlot | 行为 | 速度乘数 | 说明 |
|-----------|------|---------|------|
| CENTER | approach ball + kick at attack_theta, power=10.0 | 0.5x | 慢速压迫，确保不冲过头 |
| SIDE | approach ball（纯移动） | 1.3x | 快速压迫 |
| KEEPER | approach ball（纯移动） | 1.0x | 正常速度压迫 |

**Phase 3→0 转换**: 任一队友接近球 <0.3m，或 5s timeout

### 5.3 评价

**优点**:
- 四阶段覆盖了开球→过渡→防守的全流程，从 ball 未离开中心到恢复 NormalPlay 的每一步都有明确的角色和行为
- Phase 1→2 的 0.5s 延迟 + ball moved >0.15m 双条件，有效防止因传感器噪声导致的误切换
- Phase 2 的角色交换（CENTER 从 kicker 变成 supporter, SIDE 变成 chaser）合理：SIDE 本来就跑 landing point，自然接近球
- Phase 3 的差异化速度乘数（0.5x/1.3x/1.0x）实现分层次压迫，CENTER 慢速避免冲球过头，SIDE 快速给压力
- Phase 2 退出使用 1.0s delay 确认（`play_subtree.py:444`），防止瞬间接近球后立即切回 NormalPlay 导致角色震荡

**缺点**:
- Phase 3 timeout=5.0s 硬编码（`play_subtree.py:354`），没有从 config 读取，不能在运行时调整
- Phase 2 timeout=2.0s 同样硬编码（`play_subtree.py:425`）
- Phase 3 的 ball detection（我方碰球）判断阈值 0.3m（`play_subtree.py:376`）硬编码
- KICKOFF_PHASE blackboard key 没有在 BlackboardKeys 枚举中显式声明初始化值（依赖 PlayKickoffController 的首帧写入），可能导致 Phase 初始值未定义

### 5.4 改进建议

1. **参数化 timeout**（`play_subtree.py:354`, `play_subtree.py:425`）: 将 5.0s 和 2.0s 提取到 `SoccerStrategyTuning` 中（如 `opp_defense_timeout_sec`、`role_lock_timeout_sec`），与其他策略参数统一管理
2. **Phase 初始化**（BlackboardKeys 相关）: 在 BT DataLayer 显式初始化 `KICKOFF_PHASE=0`，避免未定义初值

---

## §6 Chaser 踢球决策

### 6.1 参考源码

- `src/play/default_roles.py:35-185` — `ChaserRole`（含 `_kick_power`）
- `src/tactics/targeting/attack.py:52-95` — `select_kick_target`（决策链）
- `src/tactics/targeting/attack.py:145-196` — `best_pass_target`（传球评分）
- `src/tactics/targeting/attack.py:199-262` — `shot_lane_is_clear` + `lane_clear_score`

### 6.2 当前行为

ChaserRole 在每个 tick 调用 `kit.targeting.select_kick_target` 决策踢球目标，决策链优先级（`attack.py:62-95`）:

```
1. sideline 恢复  → ball_near_sideline → sideline_recovery_target
2. restart 触球   → should_make_restart_touch (kickoff/throw-in/indirect FK)
                     → pass to nearest teammate (restart)
3. shoot          → shot_lane_is_clear (lane 无障碍) → opponent goal center
4. pass           → best_pass_target 评分 > pass_min_score=0.60 → 传队友
5. dribble        → 以上都不满足 → dribble_target 向前运球
```

**射门 lane 迟滞**（`attack.py:199-223`）:

```
进入 shoot: lane_clear_score >= 0.45 (was_shooting=False)
保持 shoot: lane_clear_score >= 0.25 (was_shooting=True)
```

即进入需要更严格的条件（0.45），保持时放宽（0.25），防止 lane 边界处 shoot/dribble 振荡。

**lane_clear_score 算法**（`attack.py:226-262`）:
- 将每个 opponent 投影到球-to-目标线段上
- 如果 opponent 在线段中间且侧向距离 < `max(pass_lane_clearance=0.75, obstacle.radius)`，则降低 lane score
- 多个 obstacle 取最差（min）score
- 最终 score ∈ [0, 1]，1.0 表示完全无障碍

**传球评分**（`attack.py:145-196`）:

| 权重 | 因子 | 说明 |
|------|------|------|
| 0.55 | `lane_clear_score` | 传球队友之间的 lane 通畅度 |
| 0.30 | `forward_gain / field_length` | 向前推进的归一化距离 |
| 0.15 | `1 - abs(y) / (field_width/2)` | 靠近中路的奖励 |
| -penalty | `clamp(distance/12, 0, 0.25)` | 距离惩罚，最远 -0.25 |

候选队友还需满足 `forward_gain >= pass_min_forward_m=0.35`（至少向前 0.35m），总 score >= `pass_min_score=0.60`。

**Chaser 踢球力度**（`default_roles.py:130-142`）:
- 己方半场（`robot.pose.x < 0`）+ shooting → **7.5**
- 其余情况（对方半场、非 shooting）→ **10.0**（即 `soccer_kick_power`）

**ChaserRole approach**（`default_roles.py:43-56`）:
- target = 球的 `approach_target`，偏移 `_CHASER_APPROACH_OFFSET=0.18m`（未在类中显式定义，使用默认值 `_APPROACH_OFFSET`），对齐 kick_theta 方向
- speed_multiplier=2.0x

### 6.3 评价

**优点**:
- 决策链清晰：从紧急情况（sideline/restart）到常规进攻（shoot/pass/dribble），优先级递减合理
- shot lane 迟滞有效：threshold 0.45 vs 0.25 有 0.20 的 hysterisis gap，防止 lane score 在边界处轻微波动导致 shoot/dribble 振荡
- 传球评分三维度（lane + forward + center）全面，weight 设计合理（lane 最大权重，forward 次之，center 最小）
- `shoot_lane_is_clear` 使用 `max(pass_lane_clearance, obstacle.radius)` 作为 clearance 容忍，对宽度大的 obstacle 有自适应

**缺点**:
- 己方半场射门力度 7.5（`default_roles.py:140-141`）偏弱。如果球在己方半场靠近中线位置，对手 GK 站位靠前，射门的飞行时间偏长，更容易被拦截或出底线
- `select_kick_target` 的决策链中没有考虑球是否在己方防守区内的安全性：己方防守区的 chaser 应该优先 clear 而非 shoot
- `_last_decision` 仅在 chaser 实例内部记忆，如果 chaser 角色切换（锁定期除外），新的 chaser 没有之前的决策状态，`was_shooting` 会重置为 False，可能导致 lane 评分刚够 0.25 时突然停止射门

### 6.4 改进建议

1. **己方半场射门力度调整**（`default_roles.py:140-141`）: 对己方半场射门，考虑球到对方球门的距离动态调整力度。距离越近力度越小，距离远（如中线附近远射）力度加大到 10.0
2. **chaser 决策状态传递**: 在 chaser 切换时将 `_last_decision` 写入 blackboard，新的 chaser 从中读取以保持 `was_shooting` 连续性

---

## §7 Supporter 站位

### 7.1 参考源码

- `src/play/default_roles.py:193-373` — `SupporterRole`（含 `wants_to_kick`）
- `src/tactics/targeting/support.py:25-239` — `support_target`（全文件，站位主算法）
- `src/soccer_framework/config.py:134-137` — support 相关参数

### 7.2 当前行为

**站位算法**（`support.py:25-92`）:

Supporter 以 **chaser（追球队友）为参照物**而非球，在三者（chaser, supporter, ball）之间形成三角站位：

1. **找 chaser**: 非 GK 队友中最接近球的（`support.py:53-69`）
2. **方向**: ball → chaser 的"后方"（behind-direction），即 supporter 站在 chaser 背后（`support.py:109-117`）
3. **距离 clamp**: supporter→chaser 当前距离 clamp 到 [1, 4] m（`support.py:124-130`）
   - <1m → target at 1m（后退，strafe 模式保持面向球）
   - >4m → target at 4m（靠近）
   - 1~4m → 保持距离，仅侧向调整角度
4. **角度偏移**: 10°（防守端，ball.x=-7）→ 45°（进攻端，ball.x=+7），线性插值，侧向方向由 player_id 奇偶性决定（`support.py:132-143`）

**fallback**（`support.py:148-161`）: 当没有 chaser 或自身 pose 不可用时，沿 ball→own-goal 方向后退 2.5m

**teammate spacing 排斥**（`support.py:164-239`）:
- 如果 target 距离最近队友 < `support_min_spacing_m=0.9`（`config.py:137`），沿 teammate→target 方向推开 target 到 0.9m
- 极端情况下（target 几乎重叠 teammate），用 lane_sign（基于球侧或 player_id 奇偶性）选择推开方向
- 如果 push 后 target 被 clamp 到 field 内导致距离又 <0.9m，不做二次迭代（文档标注 this is rare with at most three teammates）

**Supporter 踢球**（`default_roles.py:242-272`）:
- `wants_to_kick`: 当此 supporter 是所有非 GK 队友中距离球最近的（且至少近 0.3m），返回 True
- 这确保 supporter 不会与 chaser 争球

**SupporterRole 参数**（`default_roles.py:355-372`）:
- kick_power=10.0
- speed_multiplier=2.0x
- strafe=True, hold_vyaw=0.25

### 7.3 评价

**优点**:
- 以 chaser 为参照的动态站位比以球为参照更合理：supporter 在 chaser "身后"支持，chaser 前方断球时 supporter 在第二线接应
- 距离 clamp [1,4] 避免 supporter 和 chaser 挤在一起或距离太远无法接应
- 角度偏移 10°→45° 渐变：防守端贴近 chaser 身后（10°），进攻端拉开角度（45°）提供传球空间
- teammate spacing 排斥防止多个 supporter 堆叠（虽然当前只有 1 个 supporter，但架构支持多个）
- supporter kick 与 chaser 不冲突：`wants_to_kick` 中"至少近 0.3m"确保 chaser 先到球

**缺点**:
- 距离 clamp 使用当前 supporter→chaser 距离作为 desired 的输入（`support.py:124-130`），在 chaser 快速移动时 supporter 的反应可能滞后
- fallback（沿 own-goal 方向后退 2.5m）在己方半场球靠近边线时可能把 supporter 拉到边线外被 clamp 回来
- 没有对 opponent 位置的感知：supporter 的理想站位应该是 chaser 和 opponent 之间的空隙，而不是 chaser 的正后方

### 7.4 改进建议

1. **opponent 感知**: 在 `_chaser_relative_target`（`support.py:95`）中增加对最近 opponent 的检测，选择 opponent 不在的那一侧作为 angle_rad 偏移方向（覆盖 player_id 奇偶性选择）
2. **距离预测**: 用 chaser 的移动方向预测未来的 chaser 位置，而非当前 chaser 位置，减少 supporter 滞后

---

## §8 避障系统

### 8.1 参考源码

- `src/tactics/motion.py:191-310` — `_avoidance_target`（路径绕障层）
- `src/tactics/motion.py:447-570` — `_apply_yaw_avoidance`（yaw 回避层）
- `src/tactics/targeting/restart.py:92-134` — `opponent_restart_target`（对方 restart 避让）
- `src/soccer_framework/config.py:91-117` — 避障相关参数

### 8.2 当前行为

避障系统分两层：

**第 1 层: 路径绕障**（`motion.py:191-310`）:
- `_avoidance_target` 在 player-to-target 路径上检测首个 blocking obstacle
- 判断逻辑（`_first_blocking_obstacle`, `motion.py:215-256`）:
  - 忽略靠近起点 (`obstacle_start_ignore_distance=0.35m`) 和靠近终点 (`obstacle_target_ignore_distance=0.35m`) 的障碍
  - lateral 距离 < `obstacle.radius + obstacle_safety_margin=0.22m` 视为阻挡
  - 多个 blocking obstacle 选取 nearest（最小的 along 值）
- 首次生成 via point 时选择绕行方向（`_choose_avoid_side`, `motion.py:258-277`）：障碍物在路径左侧 → 右侧绕行，反之亦然，选较小的偏离
- via point 位于障碍物侧方，offset = `obstacle.radius + obstacle_safety_margin`
- **侧向记忆**：`_avoid_side_by_player` dict 跨 tick 持久化绕行方向，避免同一障碍物每帧左右摇摆
- via point 生成后被 clamp 到场内（`motion.py:213`）

obstacle radius:
| 类型 | radius | 说明 |
|------|--------|------|
| opponent | 0.55m | 对手不可预测，较大半径 |
| teammate | 0.48m | 队友可预测，较小半径 |

**第 2 层: yaw 回避**（`motion.py:447-570`）:
- 针对邻居（teammate 始终；opponent 仅在 READY/recovery 阶段）做预测性碰撞回避
- 预测当前距离和预测最近距离（horizon=1.0s 内），如果 < `yaw_avoid_min_distance_m=0.78m`，施加 vyaw bias
- 邻居在左侧 → 右转（负 vyaw），右侧 → 左转，正后方 → player_id 奇偶性决定
- 每邻居最多贡献 `yaw_avoid_bias_max=0.6 rad/s` × scale
- 最终 vyaw clamp 在 `[-max_angular_speed, +max_angular_speed]`（`motion.py:518-522`）
- **PLAY 阶段不回避对手**（`include_opponents=False`），避免 chaser 被对手推离球

**对手 restart 避让**（`restart.py:92-134`）:
- 对手开球时，我方球员需保持 `opponent_restart_avoid_distance_m=1.6m`（规则 1.45m + 0.15m buffer）
- 若当前距离 < min_distance + 0.25m，进入 escape 模式（额外 +0.35m buffer）
- 使用 `_apply_safety_chain` 安全链进一步确保避让

### 8.3 评价

**优点**:
- 双层设计互补：路径绕障处理"远处"阻挡（规划新路径），yaw 回避处理"近处"邻居（微调朝向），两个层次各司其职
- via point 侧向记忆跨 tick 持久化，解决了逐帧重复计算导致的左右摇摆问题
- yaw 回避的预测模型（`_yaw_avoid_scale`, `motion.py:528-570`）同时考虑当前 distance 和预测 closest distance，实现提前避让
- PLAY 阶段 exclude opponents 从 yaw 回避的设计合理：chaser 追球时不应被对手"推开"
- 障碍半径区分 teammate (0.48m) 和 opponent (0.55m) 有实际意义

**缺点**:
- yaw 回避的 vyaw bias 直接加到 `intent.vyaw` 后再 clamp 到 `[-max_angular_speed, +max_angular_speed]`（`motion.py:518-522`）。如果原始 vyaw 已接近 ±1.0 rad/s，bias 可能被 clamp 失效
- 路径绕障使用 `obstacle_start_ignore_distance=0.35m` 和 `obstacle_target_ignore_distance=0.35m`（`config.py:102-103`）。当球非常近（<0.35m）时，即使 opponent 挡路也不绕障，可能导致 chaser 直接撞到对手
- 对手 restart 避让的 `_apply_safety_chain` 功能未在本文档深入分析（调用链较长），但 basic escape + safety chain 的双层设计对复杂边界情况可能有遗漏

### 8.4 改进建议

1. **yaw bias 饱和处理**（`motion.py:518-522`）: 在 `intent.vyaw + yaw_bias` 前先对 intent.vyaw 做 scaling（如乘以 0.8），为 yaw_bias 留出空间。或使用 soft clamp（如 tanh）而非硬 clamp
2. **近距离绕障阈值**（`config.py:102`）: 考虑将 `obstacle_start_ignore_distance` 减小到 0.20m 或以 ball 距离动态调整

---

## §9 分支差异 (power-shot vs aggressive-attempt)

### 9.1 power-shot 分支概况

`git diff main...experiment/power-shot --stat` 结果：

```
26 files changed, 3107 insertions(+), 204 deletions(-)
```

主要变更：
- 新增文件: `ball_prediction.py` (336 行), `docs/` 下 4 个策略文档
- 核心策略: `default_roles.py` (+723 行，GK 三状态机)、`play_subtree.py` (+496 行，开球四阶段)、`playbook.py` (+215 行，select_chaser 锁定期)
- 参数调整: `config.py` (+49 行)，统一 `soccer_kick_power=10.0`，新增 GK/避障/ball prediction 参数

### 9.2 aggressive-attempt 分支概况

`git diff experiment/power-shot...experiment/aggressive-attempt --stat` 结果：

```
25 files changed, 3957 insertions(+), 513 deletions(-)
```

aggressive-attempt 新增 3957 行，其中包含 FIX-1~FIX-4 四个核心修复。以下逐一列出差异，**所有修复均未合入 power-shot 分支**。

---

#### FIX-1: LKG 缓冲系统

**新增文件**:
- `src/soccer_framework/ball_lkg.py` (100 行) — ball 数据丢失时的外推补偿
- `src/soccer_framework/pose_lkg.py` (152 行) — robot pose 数据丢失时的外推补偿

**修改文件**:
- `src/behavior_tree/nodes/data.py` (+140 行) — DataLayer 集成 LKG

**功能**: 当短时（如 1~3 帧）ROS 数据丢失时，用上一帧的速度和位置做简单外推填充 `BallState` 和 robot pose，防止 SafetyGuards 误触发 StopAll（因 "ball is None" 导致全队停摆）。

**影响评估**: 高。无 LKG 时，任何单帧数据丢失都会导致全队停止并等待一个完整的 READY→PLAYING 循环恢复。LKG 能显著减少此类停摆。

---

#### FIX-2: READY 诊断日志

**修改文件**:
- `src/play/nodes.py` (+8 行) — 增加 ReadySlot 诊断日志
- `src/soccer_framework/__init__.py` (+4 行)
- `src/soccer_framework/game_controller.py` (+2 行) — 修复 GC 数据解析

**功能**: 在 READY 阶段的 `GoReadyTarget` 中增加日志输出，记录每个球员的 ready slot、目标位置和当前姿态，便于排查 READY 阶段球员不动或站错位置的问题。

**影响评估**: 低（仅诊断）。对比赛策略无直接影响，但显著提升调试效率。

---

#### FIX-3: Chaser 锁定期优化 + Lane EMA 平滑

**修改文件**:
- `src/play/playbook.py` (+78 行) — chaser 锁定期逻辑优化
- `src/tactics/targeting/attack.py` (+166 行) — lane clear score 的 EMA 平滑
- `src/tactics/targeting/predicates.py` (+63 行) — ball_claim_score 优化

**功能**:
1. Chaser 锁定期在防守区增加了更细粒度的条件判断（不仅仅是 `ball.x < area_x`）
2. `shot_lane_is_clear` 中的 lane_clear_score 加入 EMA（指数移动平均）平滑，减少单帧噪声导致的 shoot/dribble 切换
3. `ball_claim_score` 优化（具体公式调整见 `predicates.py` 实际 diff）

**影响评估**: 中。减少策略层面的高频振荡，但 EMA 平滑会引入约 0.1~0.2s 的延迟。

---

#### FIX-4: GK 系统重构

**修改文件**:
- `src/play/default_roles.py` (+542 行) — GK 角色大幅重构
- `src/tactics/ready_stance.py` (+28 行) — goalkeeper_guard_target 调整
- `src/tactics/targeting/ball_prediction.py` (+83 行) — BallPredictor 增强
- `src/tactics/targeting/support.py` (+350 行) — 可能包含新的 support 模式

**功能**（基于文档和代码结构推断）:
1. **去掉 desperation clear**: GK 不再在 ball.x < own_goal_x + 1.5m 时直接冲向球，而是依赖于两阶段 rush-out
2. **两阶段 rush-out**: 将原先的单一 RUSH_OUT 行为拆分为 approach 阶段（走向 rest point）和 engage 阶段（进入 kick range 后踢球），两个阶段有不同的速度乘数和 kick 逻辑
3. **最大距离过滤**: 球预测停止点距离 GK 当前位置超过一定阈值时不触发 RUSH_OUT，避免 GK 冲出去追不到球
4. **队友拦截检测**: 如果非 GK 队友已经在防守区内且距离球更近，GK 不进入 RUSH_OUT，让队友处理
5. **动态站位**: goalkeeper_guard_target 的弧线半径和 blend 参数可能根据球的位置动态调整

**影响评估**: 高。GK 从三状态机重构为更复杂的模型，行为变化显著，需要充分的仿真验证。

---

### 9.3 汇总对比

| 项目 | power-shot | aggressive-attempt |
|------|-----------|-------------------|
| GK 状态机 | 3 状态 (GUARD/RUSH_OUT/LATERAL) + desperation | 去掉 desperation，两阶段 rush-out，队友拦截检测 |
| 数据丢失处理 | 无，依赖 SafetyGuards StopAll | LKG 缓冲系统外推补偿 |
| Chaser lane score | 原始 lane_clear_score | EMA 平滑 lane_clear_score |
| BallPredictor | 基础版本 | 增强版本 (+83 行) |
| READY 诊断 | 基础日志 | 扩展诊断日志 |

### 9.4 建议

aggressive-attempt 的 FIX-1 (LKG) 和 FIX-3 (lane EMA) 是两个最有价值的独立修复，可考虑 cherry-pick。FIX-4 (GK 重构) 改动最大，建议先在独立分支上充分测试再评估合入。当前 power-shot 分支作为参赛基准，保持稳定优先，不建议批量合入 aggressive-attempt 的全部变更。

---

*文档结束。所有策略行为均基于 `experiment/power-shot` 分支源码核实。参数值交叉校验自 `src/soccer_framework/config.py:57-173` (SoccerStrategyTuning)。*
