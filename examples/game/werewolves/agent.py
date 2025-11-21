# -*- coding: utf-8 -*-
"""智能狼人杀Agent - 简化但高效的实现"""
import os
import re
from typing import Optional, Dict, Any
from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeMultiAgentFormatter
from agentscope.model import DashScopeChatModel
from agentscope.message import Msg


#############################################
# Role Adaptation Module (Phase 1 Implementation)
# Usage: PlayerAgent will automatically select a strategy based on
#        `current_role` and inject prompt fragments + decision shaping.
#############################################

class _RoleStrategy:
    # 中文说明：
    # 该基类定义“身份策略”统一接口，用于：
    # 1. prompt()：返回该身份的策略描述片段（供系统提示词动态拼接）。
    # 2. adjust_night_decision()：对夜晚阶段初步决策进行角色定制微调（如增加特别理由、过滤目标等）。
    # 3. adjust_day_decision()：对白天发言与投票建议进行再加工（如补充风险说明、温和化表达）。
    # 设计思想：保持极简、可扩展。后续需要增加“欺骗检测”“风险权重”“动态学习”等功能时，
    # 只需在子类中添加新字段或覆盖方法，不破坏现有 PlayerAgent 使用方式。
    # 注意：按照项目规范，正式 docstring 仍保持英文；中文仅作为辅助阅读注释。
    """Base class for role-specific strategy logic.

    Each concrete role strategy should implement prompt fragments and
    optional hooks for decision shaping in night/day phases.

    The design keeps it lightweight – later phases can extend with
    richer reasoning, deception modeling, or dynamic weighting.

    Args:
        role (`str`):
            Role name handled by the strategy.
    """

    def __init__(self, role: str):
        self.role = role

    def prompt(self) -> str:
        """Return role-specific prompt fragment.

        Returns:
            `str`:
                Prompt fragment describing strategy guidance for the role.
        """
        return ""

    def adjust_night_decision(self, decision: Dict[str, Any]) -> Dict[str, Any]:
        """Hook to adjust night decision dictionary.

        Args:
            decision (`dict`):
                Raw decision generated before role-specific shaping.

        Returns:
            `dict`:
                Possibly modified decision.
        """
        return decision

    def adjust_day_decision(self, decision: Dict[str, Any]) -> Dict[str, Any]:
        """Hook to adjust day decision dictionary.

        Args:
            decision (`dict`):
                Raw daytime decision (speech & vote suggestions).

        Returns:
            `dict`:
                Possibly modified decision.
        """
        return decision


class _WerewolfStrategy(_RoleStrategy):
    def prompt(self) -> str:  # noqa: D401 - short override
        return (
            "# 狼人策略 🐺\n\n"
            "## 角色定位\n"
            "你是黑暗阵营的核心输出与信息扰动者。你的主要目标是：\n"
            "1. 在白天以可信逻辑伪装成好人（村民或功能角色）\n"
            "2. 夜晚高效协作选择击杀目标，优先破坏好人信息结构\n"
            "3. 控制节奏避免早期集中暴露，拖入中后期形成人数优势\n\n"
            "## 目标优先级（夜晚击杀顺序参考）\n"
            "1. 已显露或高度疑似的预言家 🔮\n"
            "2. 行为、语言像女巫的玩家（救人/毒人节奏影响大）🧙‍♀️\n"
            "3. 发言缜密、带动推理的强势玩家（可能是猎人或关键好人）🏹\n"
            "4. 中期开始清理中等活跃度的普通村民以制造信息缺口\n\n"
            "## 夜晚策略细化\n"
            "- 第一夜：信息不足，建议避开明显‘边缘’玩家，挑选活跃或可能是功能角色者。\n"
            "- 中后期：结合白天发言与投票行为评估功能角色真实概率，定向清除。\n"
            "- 协作方式：提出2~3个候选 → 快速理由归纳 → 收敛到风险最低且收益最高目标。\n"
            "- 避免连续两夜击杀同类型（如连续抓极跳的人），防止好人建立你们选择模型。\n\n"
            "## 白天发言结构模板\n"
            "1. 开局复述客观信息（显示你在整理局势）\n"
            "2. 选取 1~2 条别人忽略的细节做‘中度分析’\n"
            "3. 给出一个温和的可疑名单（含 2~3 人，不要唯一指向）\n"
            "4. 明确自己的投票倾向但保持可回旋余地\n"
            "5. 避免：空洞防御 / 过度追击单人 / 与同伴互相强互动\n\n"
            "## 跳身份与伪装\n"
            "- 预言家伪装：仅在真预言家已高度被怀疑或已死亡且你能给出连续验人逻辑时使用。\n"
            "- 女巫伪装：少见，不推荐主动跳；可在被点杀时用来转移视线。\n"
            "- 村民伪装：默认安全选项，适度逻辑+跟进即可。\n"
            "- 伪装一致性：保持你前几轮的语言风格、节奏密度、关注点类型稳定。\n\n"
            "## 欺骗与反侦测技巧\n"
            "- 模仿好人推理链：先列事实→标记不确定→给出‘试探性假设’\n"
            "- 控制攻击阈值：不要第一时间攻击真正逻辑清晰者，可延后至其第二次逻辑出现微瑕疵时再切入。\n"
            "- 制造信息噪声：提出互斥的两种可能场景，鼓励好人内部分歧。\n"
            "- 避免：刻意为狼同伴洗白（过度防守会暴露阵营网络）。\n\n"
            "## 投票与节奏控制\n"
            "- 票型策略：早期分散，避免狼队集体押一人；中期开始适度集中形成‘多数引导’假象。\n"
            "- 临界局势（人数接近终局）：通过‘犹豫—跟进’节奏制造你是摇摆好人的印象。\n"
            "- 候选对冲：当同伴被强推时，构造另一个合理的高危目标分散票。\n\n"
            "## 风险控制\n"
            "- 高风险行为：连续强跳身份 / 与已死亡的好人逻辑反复冲突。\n"
            "- 监控指标：是否出现多人同时标记你与另一名玩家为‘阵营组合’。\n"
            "- 降风险手段：中途自检发言结构 → 减少主观口吻 → 增加条件语。\n\n"
            "## 应急处理\n"
            "- 自身被集火：及时承认部分逻辑疏漏 → 转为结构化补充 → 争取“再看一轮”空间。\n"
            "- 同伴暴露：迅速切割（减少互动引用），用“其行为与我早期判断不符”建立距离。\n"
            "- 终局少狼：争取形成‘错误团队协作’假象，让好人怀疑彼此误导。\n\n"
            "## 决策流程（夜晚简版）\n"
            "1. 收集白天指向与功能角色线索\n"
            "2. 评估剩余功能角色存活概率\n"
            "3. 列出 2~3 名击杀候选及收益/风险\n"
            "4. 快速协商锁定目标\n"
            "5. 记录当晚选择逻辑以备次日发言伪装引用\n"
        )

    def adjust_day_decision(self, decision: Dict[str, Any]) -> Dict[str, Any]:
        # Slightly temper aggressive vote suggestions by adding reasoning.
        if isinstance(decision, dict) and 'vote' in decision and decision['vote']:
            decision['vote_reasoning'] = (
                "Maintain moderate pressure; avoid leading too strongly to reduce suspicion."
            )
        return decision


class _VillagerStrategy(_RoleStrategy):
    def prompt(self) -> str:  # noqa: D401 - short override
        return (
            "# 村民策略 👨‍🌾\n\n"
            "## 角色定位\n"
            "你是阵营信息结构的基石。虽无夜晚技能，但通过‘发言质量 + 投票精准度’决定好人阵营推进速度。\n\n"
            "## 核心目标\n"
            "1. 快速区分“主动构造逻辑者”与“附和型发言者”\n"
            "2. 建立自己稳定可信的分析风格\n"
            "3. 协助锁定功能角色并保护其信息产出\n\n"
            "## 信息采集重点\n"
            "- 发言结构：是否有事实→推断→结论三段式？\n"
            "- 时间节奏：关键轮次（首轮/查杀公布后）谁刻意降活跃度？\n"
            "- 投票行为：谁在避免站队、谁在翻票、谁在关键时刻补票。\n"
            "- 语言迹象：反复使用模糊评价 vs. 提供可验证链条。\n\n"
            "## 白天发言模板\n"
            "1. 开局：复盘夜晚结果 + 标记信息缺口（例如：未知女巫是否出药）\n"
            "2. 中段：挑选 1~2 个异常点做结构化拆解（发言矛盾/投票反常）\n"
            "3. 后段：给出候选投票对象（A>优先，B>次选），并简述放弃其他人的理由\n"
            "4. 保留：若功能角色未跳，不轻易自曝身份猜测\n\n"
            "## 投票策略\n"
            "- 前期：跟随可信主导逻辑，但保留独立判断说明\n"
            "- 中期：若出现多个跳身份，优先验证逻辑稳定性而非情绪表达\n"
            "- 后期：根据剩余人数计算狼最低存活数，进行逆向排除\n"
            "- 避免：纯情绪票 / 无分析的跟票\n\n"
            "## 风险控制\n"
            "- 低质量急躁连发→易被狼利用引导\n"
            "- 过度防守单人→被视作‘绑定’\n"
            "- 防范狼方伪逻辑：检查其引用的前置事实是否真实出现过\n\n"
            "## 常见误区\n"
            "- 迷信首轮小细节（狼可故意投放噪声）\n"
            "- 将‘沉默’等同于‘狼’（功能角色亦可能保守）\n"
            "- 逻辑链未显式标注假设条件导致后期被反驳\n\n"
            "## 应急处理\n"
            "- 被误认为狼：冷静列出你所有已公开的推理要点与其正向作用\n"
            "- 盘错局势：及时承认假设失效并调整（展示你不是死扛）\n\n"
            "## 简易决策流程\n"
            "1. 汇总夜晚信息 → 是否有功能行动迹象\n"
            "2. 标记与上轮发言风格变化大的玩家\n"
            "3. 投票前再审查主推对象逻辑闭环性\n"
            "4. 公布票意并给出可验证理由\n"
        )


class _SeerStrategy(_RoleStrategy):
    def prompt(self) -> str:  # noqa: D401 - short override
        return (
            "# 预言家策略 🔮\n\n"
            "## 角色定位\n"
            "信息锚点制造者。你的验人结果决定好人阵营是否能形成稳定叙事与精准打击。存活价值远大于单次正确指认。\n\n"
            "## 核心节奏\n"
            "- 前期（第1~2夜）：隐匿 + 验活跃或可能影响舆论者\n"
            "- 中期：若已有 2 次高价值验杀或局势混乱 → 公开身份建立验人日志\n"
            "- 后期：确保验人链完整转移到可信替代发言者（防被毒或击杀后断档）\n\n"
            "## 验人优先级\n"
            "1. 语言组织度极高且企图引导结构者（可能是狼带节奏）\n"
            "2. 对局势不断‘模糊处理’、避免明确立场者\n"
            "3. 跳身份者（检验真假）\n"
            "4. 中后期若剩余人数少：查验潜在终局关键票持有者\n\n"
            "## 隐匿技巧\n"
            "- 早期发言控制在“补充+轻度推理”层面，避免连续提出高度结构化链条。\n"
            "- 刻意保留对部分玩家的评价延后一轮释放，避免被狼推测你查验方向。\n"
            "- 若被误指为功能角色可暂不强烈否认，收集更多指向再择机翻转。\n\n"
            "## 公开身份时机判断\n"
            "满足以下至少两条即可考虑跳：\n"
            "- 已验出狼人或强伪装对象\n"
            "- 狼方明显开始构造你是‘伪预言家’的节奏\n"
            "- 你的存活被多方票或毒的风险上升\n"
            "- 局势高度混乱需要锚定事实\n\n"
            "## 验人日志格式（公开后建议）\n"
            "示例：\n"
            "- N1：验 Player3 → 好人（理由：高活跃+试探性逻辑）\n"
            "- N2：验 Player7 → 狼（理由：刻意回避投票讨论）\n"
            "- N3：计划验 Player5（当前摇摆立场，可能关键票）\n\n"
            "## 风险与防护\n"
            "- 最大威胁：狼人定向击杀 + 女巫误毒\n"
            "- 防范：提前建立可信‘支持者’（村民中逻辑清晰者）作为信息缓冲。\n"
            "- 若跳身份后被质疑：使用“可验证结构”优先（列事实→顺序→时间线）。\n\n"
            "## 常见错误\n"
            "- 过早公开且无高价值结果\n"
            "- 未保持验人逻辑的一致标准（会被狼攻击为伪造）\n"
            "- 忽视自身被击杀后信息断层问题\n\n"
            "## 应急处理\n"
            "- 若被标记伪预言家：快速比较你与对跳者的‘验人选择合理度’与‘时间线一致性’。\n"
            "- 若晚间可能被集火：预先在当日尾声投递“保底下一夜验人计划”。\n\n"
            "## 决策流程\n"
            "1. 归纳上一白天发言异常点\n"
            "2. 列出 2 名潜在高价值查验对象\n"
            "3. 选择能最大化后续昼夜信息收益者\n"
            "4. 更新内部验人链并评估是否进入跳身份窗口\n"
        )

    def adjust_night_decision(self, decision: Dict[str, Any]) -> Dict[str, Any]:
        # Ensure a reasoning field clarity.
        if 'reasoning' in decision:
            decision['reasoning'] += " | Focus on information-rich targets."
        return decision


class _WitchStrategy(_RoleStrategy):
    def prompt(self) -> str:  # noqa: D401 - short override
        return (
            "# 女巫策略 🧙‍♀️\n\n"
            "## 角色定位\n"
            "信息与生存节奏调控者。你的一瓶救与一瓶毒是改变胜负曲线的强力杠杆。\n\n"
            "## 解药使用原则\n"
            "- N1 若死亡者发言结构成熟或疑似预言家可考虑救，但避免无依据滥用。\n"
            "- 若已确认预言家被击杀且人数尚多：优先保证其继续产出。\n"
            "- 中后期慎救‘被普遍怀疑者’，防止被狼利用制造错觉。\n\n"
            "## 毒药使用原则\n"
            "满足 ≥2 条再考虑：\n"
            "1. 多轮逻辑风格自洽但结果指向反常（伪预言家嫌疑）\n"
            "2. 频繁引导错误票或反复拆真逻辑链\n"
            "3. 夜晚击杀与其白天建议存在正相关\n"
            "4. 投票行为与其公开立场显著不一致\n\n"
            "## 隐藏策略\n"
            "- 早期避免对“是否救人”话题过度深入分析以免暴露信息权限。\n"
            "- 发言保持‘延迟确认’模式：先说不确定，再在后期基于更多线索给出倾向。\n"
            "- 若自身即将被强推，可部分公开用药情况增强可信度。\n\n"
            "## 信息再利用\n"
            "- 记录每次死亡与当日投票/发言交叉，帮助锁定狼队打击模型。\n"
            "- 若成功救人：评估被救者后续发言是否显著提升（判断其真实功能价值）。\n\n"
            "## 风险控制\n"
            "- 过早两药全出 → 后期失去调节能力。\n"
            "- 毒错好人 → 导致阵营信任结构崩塌。\n"
            "- 被狼方诱导用药：警惕‘一致性过强’的群体推毒。\n\n"
            "## 常见误区\n"
            "- 仅依据‘被多数怀疑’就下毒\n"
            "- 忽视投票反差信号\n"
            "- 解药与毒药同夜使用导致身份完全暴露\n\n"
            "## 应急处理\n"
            "- 被质疑用药不合理：给出当时信息集合 + 决策条件 + 备选方案否定理由。\n"
            "- 药已用尽：转为辅助信息分析角色，明确声明‘无剩余药’防狼试探。\n\n"
            "## 决策流程（夜间）\n"
            "1. 识别被杀者信息价值\n"
            "2. 判断救后是否能显著提高胜率曲线\n"
            "3. 评估是否存在高置信度毒对象（≥2 证据）\n"
            "4. 若均不满足 → 保留药\n"
        )


class _HunterStrategy(_RoleStrategy):
    def prompt(self) -> str:  # noqa: D401 - short override
        return (
            "# 猎人策略 🏹\n\n"
            "## 角色定位\n"
            "终局威慑与反制工具。你的开枪目标可直接影响人数与信息再分布。\n\n"
            "## 存活价值\n"
            "- 存活期间：提供中度独立分析，避免极跳防止被女巫毒。\n"
            "- 即将被票/毒风险升高：可半公开身份争取正确投票或防误毒。\n\n"
            "## 枪的使用原则\n"
            "- 必须≥70% 置信度才开枪（来源：投票行为 + 发言矛盾 + 验人链冲突）。\n"
            "- 若不确定且剩余人数临近终局：宁可不开枪保留阵营结构。\n"
            "- 优先带走：高引导疑似狼 / 伪跳功能角色者。\n\n"
            "## 身份隐藏技巧\n"
            "- 避免刻意自称‘如果我是猎人…’类型句式。\n"
            "- 投票与普通村民风格一致，勿频繁制造极端反差。\n"
            "- 中期可偶尔精准辅助推理，建立可信度为终局枪铺路。\n\n"
            "## 开枪前判断\n"
            "1. 该玩家是否多次引导错误节奏？\n"
            "2. 是否与已确认好人持续对立？\n"
            "3. 是否其发言时间线中出现自我否定未解释？\n"
            "4. 是否存在更高风险但更高收益的替代对象？\n\n"
            "## 风险控制\n"
            "- 误枪好人 → 阵营进入被动；避免仓促情绪决策。\n"
            "- 被女巫毒死不能开枪：因此早期不要暴露过多‘终局思考’。\n\n"
            "## 应急处理\n"
            "- 被强推：冷静表态身份并给出现阶段拟定枪目标，争取改票。\n"
            "- 低置信度局：明确阐述不开枪理由，防止被指责‘逃避责任’。\n\n"
            "## 简易流程\n"
            "1. 收集狼嫌疑 Top2\n"
            "2. 标记其与好人互动模式\n"
            "3. 评估枪收益 vs. 误伤惩罚\n"
            "4. 决定是否公开枪意向或保留\n"
        )


class RoleAdapter:
    """Role adaptation manager translating a role into dynamic strategy object.

    Provides prompt fragments and decision shaping via registered strategy
    instances. Keeps extension simple: add new role by defining a new
    `_RoleStrategy` subclass and registering it in `_strategies`.

    Methods are intentionally narrow to preserve existing PlayerAgent flow
    without invasive refactoring.
    """

    def __init__(self) -> None:
        self._strategies: Dict[str, _RoleStrategy] = {
            'werewolf': _WerewolfStrategy('werewolf'),
            'villager': _VillagerStrategy('villager'),
            'seer': _SeerStrategy('seer'),
            'witch': _WitchStrategy('witch'),
            'hunter': _HunterStrategy('hunter'),
        }

    def get_strategy(self, role: Optional[str]) -> Optional[_RoleStrategy]:
        """Return strategy instance for role (or None if unsupported)."""
        if not role:
            return None
        return self._strategies.get(role)

    def get_prompt_fragment(self, role: Optional[str]) -> str:
        """Return role-specific prompt fragment or empty string.

        Args:
            role (`str | None`):
                Current role name.

        Returns:
            `str`:
                Fragment for inclusion in system prompt.
        """
        strategy = self.get_strategy(role)
        return strategy.prompt() if strategy else ""

    def shape_night_decision(self, role: Optional[str], decision: Dict[str, Any]) -> Dict[str, Any]:
        """Apply role-specific adjustments to a night decision."""
        strategy = self.get_strategy(role)
        return strategy.adjust_night_decision(decision) if strategy else decision

    def shape_day_decision(self, role: Optional[str], decision: Dict[str, Any]) -> Dict[str, Any]:
        """Apply role-specific adjustments to a day decision."""
        strategy = self.get_strategy(role)
        return strategy.adjust_day_decision(decision) if strategy else decision


class DecisionMaker:
    """决策制定模块 - 负责多阶段决策"""
    
    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self.decision_history = []
    
    def night_phase_decision(self, role: str, game_state: dict) -> dict:
        """夜间阶段决策
        
        Args:
            role: 当前角色
            game_state: 游戏状态信息
            
        Returns:
            决策建议字典
        """
        if role == 'werewolf':
            return self.werewolf_kill_decision(game_state)
        elif role == 'seer':
            return self.seer_check_decision(game_state)
        elif role == 'witch':
            return self.witch_potion_decision(game_state)
        elif role == 'hunter':
            return self.hunter_shoot_decision(game_state)
        else:
            return {}
    
    def werewolf_kill_decision(self, game_state: dict) -> dict:
        """狼人杀人决策"""
        alive_players = game_state.get('alive_players', [])
        # 优先级：预言家 > 女巫 > 猎人 > 村民
        priority_targets = []
        
        # 基于历史发言分析可能的预言家
        suspected_seer = self._identify_seer_from_speeches(game_state)
        if suspected_seer:
            priority_targets.append(suspected_seer)
        
        # 如果没有明确目标，选择活跃玩家
        if not priority_targets and alive_players:
            priority_targets = alive_players[:1]
        
        return {
            'suggested_targets': priority_targets,
            'reasoning': '优先消灭预言家或活跃玩家'
        }
    
    def seer_check_decision(self, game_state: dict) -> dict:
        """预言家查验决策"""
        alive_players = game_state.get('alive_players', [])
        speeches = game_state.get('speeches', [])
        
        # 优先查验发言可疑或跳预言家的人
        suspicious_players = self._identify_suspicious_players(speeches)
        
        suggested = suspicious_players[0] if suspicious_players else (alive_players[0] if alive_players else None)
        
        return {
            'suggested_target': suggested,
            'reasoning': '查验发言可疑的玩家'
        }
    
    def witch_potion_decision(self, game_state: dict) -> dict:
        """女巫用药决策"""
        killed_player = game_state.get('killed_player')
        has_heal = game_state.get('has_heal', True)
        has_poison = game_state.get('has_poison', True)
        night_count = game_state.get('night_count', 1)
        
        decision = {
            'use_heal': False,
            'use_poison': False,
            'poison_target': None,
            'reasoning': ''
        }
        
        # 第一晚倾向于救人（可能是关键角色）
        if has_heal and killed_player and night_count <= 2:
            decision['use_heal'] = True
            decision['reasoning'] = '第一晚救人，可能是关键角色'
        
        return decision
    
    def hunter_shoot_decision(self, game_state: dict) -> dict:
        """猎人开枪决策"""
        alive_players = game_state.get('alive_players', [])
        suspected_wolves = game_state.get('suspected_wolves', [])
        
        # 如果有明确的狼人嫌疑，优先带走
        target = suspected_wolves[0] if suspected_wolves else None
        
        return {
            'should_shoot': bool(target),
            'target': target,
            'reasoning': '带走确定的狼人' if target else '不确定目标，选择不开枪'
        }
    
    def day_phase_decision(self, game_state: dict) -> dict:
        """白天阶段决策"""
        role = game_state.get('role')
        alive_players = game_state.get('alive_players', [])
        
        # 生成发言建议
        speech_suggestion = self.generate_speech(game_state)
        
        # 生成投票建议
        vote_suggestion = self.make_vote_decision(game_state)
        
        return {
            'speech': speech_suggestion,
            'vote': vote_suggestion
        }
    
    def generate_speech(self, game_state: dict) -> str:
        """生成发言建议"""
        role = game_state.get('role')
        night_results = game_state.get('night_results', [])
        
        # 根据角色和情况给出发言建议
        if role == 'seer' and game_state.get('should_reveal', False):
            return "建议：公开身份并报验人结果"
        elif role == 'werewolf':
            return "建议：低调发言，适当跟随主流观点"
        else:
            return "建议：分析夜晚结果，提出合理怀疑"
    
    def make_vote_decision(self, game_state: dict) -> str:
        """投票决策"""
        speeches = game_state.get('speeches', [])
        alive_players = game_state.get('alive_players', [])
        role = game_state.get('role')
        
        # 分析发言找出可疑玩家
        suspicious = self._identify_suspicious_players(speeches)
        
        if suspicious:
            return suspicious[0]
        elif alive_players:
            return alive_players[0]
        return ""
    
    def _identify_seer_from_speeches(self, game_state: dict) -> str:
        """从发言中识别可能的预言家"""
        speeches = game_state.get('speeches', [])
        # 简化版本：返回空或第一个活跃玩家
        # 实际可以分析发言内容
        return ""
    
    def _identify_suspicious_players(self, speeches: list) -> list:
        """识别可疑玩家"""
        # 简化版本：基于发言次数或内容分析
        # 这里可以添加更复杂的逻辑
        return []


class RiskAssessment:
    """风险评估模块 - 评估各种行动的风险"""
    
    def __init__(self):
        self.risk_threshold = 0.7  # 风险阈值
    
    def evaluate_action_risk(self, action: str, game_state: dict) -> float:
        """评估行动风险
        
        Args:
            action: 行动类型（如 'reveal_identity', 'aggressive_vote'）
            game_state: 当前游戏状态
            
        Returns:
            风险值 (0-1)，越高越危险
        """
        role = game_state.get('role')
        alive_count = game_state.get('alive_count', 9)
        
        risk_scores = {
            'reveal_identity': self._calculate_reveal_risk(role, alive_count),
            'aggressive_vote': self._calculate_aggressive_risk(game_state),
            'use_ability': self._calculate_ability_risk(role, game_state),
            'stay_silent': 0.3,  # 保持沉默的风险较低
        }
        
        return risk_scores.get(action, 0.5)
    
    def _calculate_reveal_risk(self, role: str, alive_count: int) -> float:
        """计算暴露身份的风险"""
        if role in ['seer', 'witch']:
            # 存活人数越少，暴露风险越低（已经到关键时刻）
            return max(0.3, 1.0 - (9 - alive_count) / 9)
        return 0.5
    
    def _calculate_aggressive_risk(self, game_state: dict) -> float:
        """计算激进投票的风险"""
        role = game_state.get('role')
        if role == 'werewolf':
            # 狼人过于激进容易暴露
            return 0.7
        return 0.4
    
    def _calculate_ability_risk(self, role: str, game_state: dict) -> float:
        """计算使用技能的风险"""
        if role == 'witch':
            night_count = game_state.get('night_count', 1)
            # 第一晚用药风险较低
            return 0.3 if night_count <= 2 else 0.6
        return 0.5
    
    def calculate_survival_probability(self, targets: list, game_state: dict) -> dict:
        """计算不同目标的生存概率
        
        Args:
            targets: 目标玩家列表
            game_state: 游戏状态
            
        Returns:
            {player_name: survival_probability}
        """
        probabilities = {}
        role = game_state.get('role')
        
        for target in targets:
            # 基础生存概率
            base_prob = 0.5
            
            # 如果是关键角色，生存概率降低
            if self._is_likely_key_role(target, game_state):
                base_prob -= 0.2
            
            # 如果发言很少，生存概率提高
            if self._is_silent_player(target, game_state):
                base_prob += 0.1
            
            probabilities[target] = max(0.1, min(0.9, base_prob))
        
        return probabilities
    
    def assess_win_probability(self, current_state: dict) -> float:
        """评估当前胜利概率
        
        Args:
            current_state: 当前游戏状态
            
        Returns:
            胜利概率 (0-1)
        """
        role = current_state.get('role')
        alive_count = current_state.get('alive_count', 9)
        wolves_alive = current_state.get('wolves_alive', 3)
        
        if role == 'werewolf':
            # 狼人胜利概率：狼人数量 / 总存活数
            return wolves_alive / alive_count if alive_count > 0 else 0
        else:
            # 好人胜利概率：与狼人数量成反比
            goods_alive = alive_count - wolves_alive
            return goods_alive / alive_count if alive_count > 0 else 0
    
    def _is_likely_key_role(self, player: str, game_state: dict) -> bool:
        """判断是否可能是关键角色"""
        speeches = game_state.get('speeches', [])
        # 简化：发言多且有逻辑的可能是关键角色
        return False  # 实际需要分析发言内容
    
    def _is_silent_player(self, player: str, game_state: dict) -> bool:
        """判断是否是沉默玩家"""
        speeches = game_state.get('speeches', [])
        # 简化实现
        return False


class TeamCoordination:
    """团队协作模块 - 管理队友识别和协作"""
    
    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self.known_teammates = []
        self.temporary_allies = []
        self.trust_scores = {}  # {player_name: trust_score}
    
    def identify_teammates(self, observations: list) -> list:
        """识别队友（主要用于狼人）
        
        Args:
            observations: 观察到的信息列表
            
        Returns:
            可能的队友列表
        """
        teammates = []
        
        # 从观察中提取队友信息
        for obs in observations:
            if 'WEREWOLVES ONLY' in obs:
                # 狼人频道的消息，提取其他狼人
                # 实际需要解析消息内容
                pass
        
        return teammates
    
    def coordinate_with_team(self, teammates: list, strategy: str, game_state: dict) -> dict:
        """与队友协调策略
        
        Args:
            teammates: 队友列表
            strategy: 策略类型
            game_state: 游戏状态
            
        Returns:
            协调建议
        """
        coordination = {
            'suggested_action': '',
            'target_priority': [],
            'communication_strategy': ''
        }
        
        if strategy == 'night_kill':
            # 协调夜晚击杀目标
            coordination['suggested_action'] = 'discuss_target'
            coordination['target_priority'] = self._prioritize_kill_targets(game_state)
            coordination['communication_strategy'] = '讨论并达成一致意见'
            
        elif strategy == 'day_vote':
            # 协调白天投票
            coordination['suggested_action'] = 'coordinate_votes'
            coordination['communication_strategy'] = '避免集体投同一人，要分散'
        
        return coordination
    
    def manage_alliances(self, temporary_allies: list, game_state: dict) -> dict:
        """管理临时联盟
        
        Args:
            temporary_allies: 临时盟友列表
            game_state: 游戏状态
            
        Returns:
            联盟管理建议
        """
        self.temporary_allies = temporary_allies
        
        # 评估每个盟友的可信度
        for ally in temporary_allies:
            if ally not in self.trust_scores:
                self.trust_scores[ally] = 0.5
            
            # 根据行为调整信任度
            # 实际需要分析历史行为
        
        return {
            'maintain_alliances': [a for a in temporary_allies if self.trust_scores.get(a, 0) > 0.6],
            'suspicious_allies': [a for a in temporary_allies if self.trust_scores.get(a, 0) < 0.4],
            'strategy': 'maintain' if len(temporary_allies) > 2 else 'expand'
        }
    
    def update_trust_score(self, player: str, action: str, outcome: str):
        """更新对某玩家的信任度
        
        Args:
            player: 玩家名字
            action: 该玩家的行动
            outcome: 行动结果
        """
        if player not in self.trust_scores:
            self.trust_scores[player] = 0.5
        
        # 根据行动和结果调整信任度
        if outcome == 'positive':
            self.trust_scores[player] = min(1.0, self.trust_scores[player] + 0.1)
        elif outcome == 'negative':
            self.trust_scores[player] = max(0.0, self.trust_scores[player] - 0.1)
    
    def _prioritize_kill_targets(self, game_state: dict) -> list:
        """为狼人优先排序击杀目标"""
        alive_players = game_state.get('alive_players', [])
        
        # 优先级排序
        priority_list = []
        
        # 1. 已知或怀疑的预言家
        suspected_seer = game_state.get('suspected_seer')
        if suspected_seer:
            priority_list.append(suspected_seer)
        
        # 2. 其他活跃玩家
        for player in alive_players:
            if player not in priority_list and player != self.agent_name:
                priority_list.append(player)
        
        return priority_list


class PlayerMemory:
    """玩家记忆系统 - 跨局学习的核心
    
    集成了三大学习模块：
    1. ExperienceMemory - 经验记忆系统
    2. StrategyOptimizer - 策略优化算法
    3. OnlineLearning - 在线学习能力
    """
    
    def __init__(self):
        # ==================== 基础记忆结构 ====================
        # 当前游戏状态
        self.current_game = {
            'role': None,
            'alive_players': [],
            'dead_players': [],
            'night_results': [],
            'speeches': [],
            'votes': [],
            'key_decisions': [],  # 新增：记录关键决策
            'game_round': 0,      # 新增：当前回合数
        }
        
        # 跨局记忆
        self.total_games = 0
        self.wins = 0
        self.losses = 0
        self.role_stats = {
            'werewolf': {'played': 0, 'won': 0},
            'villager': {'played': 0, 'won': 0},
            'seer': {'played': 0, 'won': 0},
            'witch': {'played': 0, 'won': 0},
            'hunter': {'played': 0, 'won': 0}
        }
        
        # ==================== 4.1 经验记忆系统 ====================
        # 游戏历史数据库（完整记录）
        self.game_history = []  # 存储所有游戏的详细数据
        
        # 对手行为模型
        self.opponent_models = {}  # {player_name: OpponentProfile}
        
        # 情境记忆索引（用于快速检索相似情况）
        self.situation_index = {
            'early_game': [],   # 前期情况
            'mid_game': [],     # 中期情况
            'late_game': [],    # 后期情况
        }
        
        # ==================== 4.2 策略优化算法 ====================
        # 策略权重系统
        self.strategy_weights = {
            'aggressive': 0.5,        # 激进程度 (0-1)
            'reveal_early': 0.3,      # 早期暴露身份倾向
            'trust_threshold': 0.6,   # 信任阈值
            'risk_tolerance': 0.5,    # 风险容忍度
            'deception_level': 0.5,   # 欺骗程度（狼人用）
            'follow_crowd': 0.4,      # 跟随大众倾向
        }
        
        # 成功模式库
        self.success_patterns = []  # [{'pattern': {...}, 'success_rate': 0.8, 'context': {...}}]
        
        # 失败分析
        self.failure_patterns = []  # [{'pattern': {...}, 'reason': '...', 'context': {...}}]
        
        # 策略学习率
        self.learning_rate = 0.1
        
        # ==================== 4.3 在线学习能力 ====================
        # 实时学习状态
        self.current_round_insights = {}  # 当前局实时学到的信息
        
        # 对手实时跟踪
        self.opponent_live_tracking = {}  # {player_name: {'actions': [], 'style': '...'}}
        
        # 动态策略调整记录
        self.strategy_adjustments = []  # 记录每次调整及原因
        
        # 游戏阶段适应参数
        self.phase_adaptation = {
            'early': {'risk': 0.3, 'aggression': 0.4},
            'mid': {'risk': 0.5, 'aggression': 0.5},
            'late': {'risk': 0.7, 'aggression': 0.7},
        }
    
    def update_role(self, role: str):
        """更新当前角色"""
        self.current_game['role'] = role
        if role in self.role_stats:
            self.role_stats[role]['played'] += 1
    
    def update_from_msg(self, msg: Msg):
        """从消息中提取信息更新记忆"""
        content = msg.content
        
        # 处理content可能是字符串或列表的情况
        if isinstance(content, list):
            content = " ".join(str(item) for item in content)
        elif not isinstance(content, str):
            content = str(content)

        # 🔴 新增：初始化存活玩家列表
        if "the players are:" in content.lower():
            # 提取玩家名单
            players = re.findall(r'Player\d+', content)
            self.current_game['alive_players'] = players
            # print(f"[记忆] 初始化存活玩家: {players}")

        # 🔴 合并：更新存活玩家列表和死亡信息
        if "has been eliminated" in content or "died" in content:
            player_pattern = r'Player\d+'
            players = re.findall(player_pattern, content)
            for player in players:
                # 只在玩家第一次死亡时处理和打印
                if player in self.current_game['alive_players']:
                    self.current_game['alive_players'].remove(player)
                    if player not in self.current_game['dead_players']:
                        self.current_game['dead_players'].append(player)
                        print(f"[记忆] {player} 已死亡，剩余: {self.current_game['alive_players']}")
        
        # 提取投票信息
        if "voting result" in content.lower() or "vote" in content.lower():
            self.current_game['votes'].append(content)
        
        # 提取夜晚结果
        if "Last night" in content or "night" in content.lower():
            self.current_game['night_results'].append(content)
        
        # 记录发言
        if hasattr(msg, 'name') and msg.name:
            self.current_game['speeches'].append({
                'player': msg.name,
                'content': content
            })
    
    # ==================== 4.1 经验记忆系统方法 ====================
    
    def store_game_experience(self, game_data: dict):
        """存储完整的游戏经验
        
        Args:
            game_data: 游戏数据，包含角色、胜负、关键决策、对手行为等
        """
        # 1. 存入游戏历史
        game_record = {
            'game_id': self.total_games,
            'role': game_data.get('role'),
            'won': game_data.get('won', False),
            'key_decisions': game_data.get('key_decisions', []),
            'opponent_behaviors': game_data.get('opponent_behaviors', {}),
            'game_flow': game_data.get('game_flow', []),
            'final_alive_count': game_data.get('final_alive_count', 0),
            'strategies_used': game_data.get('strategies_used', []),
        }
        self.game_history.append(game_record)
        
        # 保持历史记录不超过100局（避免内存过大）
        if len(self.game_history) > 100:
            self.game_history.pop(0)
        
        # 2. 更新对手模型
        for player_name, behaviors in game_data.get('opponent_behaviors', {}).items():
            self.build_opponent_model(player_name, behaviors)
        
        # 3. 索引到情境记忆
        game_phase = self._classify_game_phase(game_data)
        self.situation_index[game_phase].append({
            'game_id': self.total_games,
            'situation': self._extract_situation_features(game_data),
            'outcome': game_data.get('won', False),
        })
        
        # 4. 触发策略优化
        self.update_strategy_weights(game_data)
    
    def retrieve_similar_situations(self, current_state: dict) -> list:
        """检索相似的历史情况
        
        Args:
            current_state: 当前游戏状态
            
        Returns:
            相似情况列表，按相似度排序
        """
        similar_situations = []
        
        # 确定当前游戏阶段
        alive_count = current_state.get('alive_count', 9)
        if alive_count >= 7:
            phase = 'early_game'
        elif alive_count >= 4:
            phase = 'mid_game'
        else:
            phase = 'late_game'
        
        # 从对应阶段的情境索引中搜索
        for situation in self.situation_index.get(phase, []):
            similarity = self._calculate_situation_similarity(
                current_state, 
                situation['situation']
            )
            
            if similarity > 0.5:  # 相似度阈值
                similar_situations.append({
                    'game_id': situation['game_id'],
                    'similarity': similarity,
                    'outcome': situation['outcome'],
                    'situation': situation['situation'],
                })
        
        # 按相似度排序
        similar_situations.sort(key=lambda x: x['similarity'], reverse=True)
        
        return similar_situations[:5]  # 返回最相似的5个
    
    def build_opponent_model(self, player_name: str, behaviors: list):
        """构建/更新对手行为模型
        
        Args:
            player_name: 玩家名称
            behaviors: 该玩家的行为记录列表
        """
        if player_name not in self.opponent_models:
            self.opponent_models[player_name] = {
                'games_met': 0,
                'speech_style': 'neutral',  # aggressive, conservative, neutral
                'voting_pattern': 'independent',  # follower, independent, leader
                'role_history': [],  # 历史角色
                'trust_score': 0.5,  # 可信度评分
                'aggression_level': 0.5,
                'deception_detected': 0,
            }
        
        profile = self.opponent_models[player_name]
        profile['games_met'] += 1
        
        # 分析行为模式
        for behavior in behaviors:
            action_type = behavior.get('type')
            
            if action_type == 'speech':
                # 分析发言风格
                content = behavior.get('content', '')
                if len(content) > 100:
                    profile['speech_style'] = 'aggressive'
                elif len(content) < 30:
                    profile['speech_style'] = 'conservative'
            
            elif action_type == 'vote':
                # 分析投票模式
                vote_timing = behavior.get('timing', 'middle')
                if vote_timing == 'early':
                    profile['voting_pattern'] = 'leader'
                elif vote_timing == 'late':
                    profile['voting_pattern'] = 'follower'
            
            elif action_type == 'role_reveal':
                # 记录角色
                role = behavior.get('role')
                if role:
                    profile['role_history'].append(role)
        
        # 更新信任评分
        profile['trust_score'] = self._calculate_trust_score(profile)
    
    # ==================== 4.2 策略优化算法方法 ====================
    
    def update_strategy_weights(self, game_result: dict):
        """基于游戏结果更新策略权重
        
        使用简单的强化学习思想：
        - 获胜 → 增强使用的策略
        - 失败 → 降低使用的策略
        
        Args:
            game_result: 游戏结果数据
        """
        won = game_result.get('won', False)
        strategies_used = game_result.get('strategies_used', [])
        
        # 提取本局实际使用的策略参数快照
        used_weights = game_result.get('strategy_snapshot', {})
        
        for strategy_name in self.strategy_weights.keys():
            if strategy_name in used_weights:
                current_weight = self.strategy_weights[strategy_name]
                
                if won:
                    # 胜利：增加权重（但不超过1.0）
                    adjustment = self.learning_rate * (1 - current_weight)
                    self.strategy_weights[strategy_name] = min(
                        1.0, 
                        current_weight + adjustment
                    )
                else:
                    # 失败：降低权重（但不低于0.0）
                    adjustment = self.learning_rate * current_weight
                    self.strategy_weights[strategy_name] = max(
                        0.0, 
                        current_weight - adjustment * 0.5  # 降低幅度小一些
                    )
        
        # 记录调整
        print(f"[学习] 策略权重已更新 {'(胜利)' if won else '(失败)'}")
    
    def analyze_successful_patterns(self, winning_games: list):
        """分析成功模式
        
        Args:
            winning_games: 获胜游戏列表
        """
        if not winning_games:
            return
        
        # 按角色分组分析
        role_patterns = {}
        
        for game in winning_games:
            role = game.get('role')
            if role not in role_patterns:
                role_patterns[role] = []
            
            # 提取关键特征
            pattern = {
                'role': role,
                'key_decisions': game.get('key_decisions', []),
                'strategies_used': game.get('strategies_used', []),
                'game_length': len(game.get('game_flow', [])),
            }
            role_patterns[role].append(pattern)
        
        # 识别共同模式
        for role, patterns in role_patterns.items():
            if len(patterns) >= 2:  # 至少2个样本才分析
                common_strategies = self._find_common_strategies(patterns)
                
                if common_strategies:
                    self.success_patterns.append({
                        'role': role,
                        'strategies': common_strategies,
                        'success_rate': len(patterns) / self.role_stats[role]['played'],
                        'sample_count': len(patterns),
                    })
    
    def learn_from_failures(self, losing_games: list):
        """从失败中学习
        
        Args:
            losing_games: 失败游戏列表
        """
        for game in losing_games:
            # 识别失败原因
            failure_reason = self._analyze_failure_reason(game)
            
            self.failure_patterns.append({
                'role': game.get('role'),
                'reason': failure_reason,
                'key_decisions': game.get('key_decisions', []),
                'context': {
                    'game_length': len(game.get('game_flow', [])),
                    'final_alive': game.get('final_alive_count', 0),
                },
            })
        
        # 保持失败模式库不超过50条
        if len(self.failure_patterns) > 50:
            self.failure_patterns = self.failure_patterns[-50:]
    
    def get_strategy_recommendation(self, current_state: dict) -> dict:
        """根据历史经验推荐策略
        
        Args:
            current_state: 当前游戏状态
            
        Returns:
            策略建议字典
        """
        recommendations = {
            'suggested_aggression': self.strategy_weights['aggressive'],
            'suggested_risk': self.strategy_weights['risk_tolerance'],
            'confidence': 0.5,
            'reasoning': [],
        }
        
        # 检索相似情况
        similar = self.retrieve_similar_situations(current_state)
        
        if similar:
            # 基于相似情况调整建议
            successful_similar = [s for s in similar if s['outcome']]
            
            if successful_similar:
                recommendations['confidence'] = 0.8
                recommendations['reasoning'].append(
                    f"找到{len(successful_similar)}个成功的相似情况"
                )
            else:
                recommendations['reasoning'].append(
                    "相似情况的成功率较低，建议谨慎"
                )
                recommendations['suggested_risk'] *= 0.8
        
        return recommendations
    
    # ==================== 4.3 在线学习能力方法 ====================
    
    def adapt_to_game_flow(self, current_round: int, game_state: dict):
        """根据游戏进展动态调整策略
        
        Args:
            current_round: 当前回合数
            game_state: 游戏状态
        """
        alive_count = game_state.get('alive_count', 9)
        
        # 判断游戏阶段
        if alive_count >= 7:
            phase = 'early'
        elif alive_count >= 4:
            phase = 'mid'
        else:
            phase = 'late'
        
        # 应用阶段适应参数
        phase_params = self.phase_adaptation.get(phase, {})
        
        # 临时调整策略（不永久修改权重）
        self.current_round_insights['adjusted_risk'] = phase_params.get('risk', 0.5)
        self.current_round_insights['adjusted_aggression'] = phase_params.get('aggression', 0.5)
        self.current_round_insights['phase'] = phase
        
        # 移除重复日志（已在 _get_strategy_insights 中输出）
    
    def recognize_opponent_strategy(self, opponent_actions: list) -> str:
        """识别对手策略
        
        Args:
            opponent_actions: 对手的行动列表
            
        Returns:
            识别出的策略类型
        """
        if not opponent_actions:
            return 'unknown'
        
        # 简单的规则分类
        action_types = [a.get('type') for a in opponent_actions]
        
        # 统计特征
        speech_count = action_types.count('speech')
        vote_count = action_types.count('vote')
        
        if speech_count > len(opponent_actions) * 0.7:
            return 'aggressive'  # 激进型（频繁发言）
        elif speech_count < len(opponent_actions) * 0.3:
            return 'conservative'  # 保守型（很少发言）
        else:
            return 'balanced'  # 平衡型
    
    def adjust_strategy_realtime(self, new_information: dict):
        """实时调整策略
        
        Args:
            new_information: 新获得的信息
        """
        event_type = new_information.get('event_type')
        
        # 根据关键事件调整
        if event_type == 'key_role_died':
            # 关键角色死亡
            role = new_information.get('role')
            if role in ['seer', 'witch']:
                # 调高风险容忍度（形势紧急）
                self.current_round_insights['adjusted_risk'] = min(
                    1.0,
                    self.current_round_insights.get('adjusted_risk', 0.5) + 0.2
                )
                self.strategy_adjustments.append({
                    'round': new_information.get('round', 0),
                    'reason': f'{role}死亡，提高风险容忍度',
                    'adjustment': '+0.2 risk',
                })
        
        elif event_type == '被质疑':
            # 被多人质疑时降低激进程度
            self.current_round_insights['adjusted_aggression'] = max(
                0.2,
                self.current_round_insights.get('adjusted_aggression', 0.5) - 0.3
            )
            self.strategy_adjustments.append({
                'round': new_information.get('round', 0),
                'reason': '被质疑，降低激进程度',
                'adjustment': '-0.3 aggression',
            })
        
        elif event_type == 'alliance_formed':
            # 形成联盟时提高信任阈值
            self.current_round_insights['adjusted_trust'] = min(
                0.9,
                self.strategy_weights['trust_threshold'] + 0.1
            )
    
    # ==================== 辅助方法 ====================
    
    def _classify_game_phase(self, game_data: dict) -> str:
        """分类游戏阶段"""
        final_alive = game_data.get('final_alive_count', 0)
        if final_alive >= 7:
            return 'early_game'
        elif final_alive >= 4:
            return 'mid_game'
        else:
            return 'late_game'
    
    def _extract_situation_features(self, game_data: dict) -> dict:
        """提取情境特征"""
        return {
            'role': game_data.get('role'),
            'alive_count': game_data.get('final_alive_count', 0),
            'strategies_used': game_data.get('strategies_used', []),
        }
    
    def _calculate_situation_similarity(self, state1: dict, state2: dict) -> float:
        """计算情境相似度"""
        similarity = 0.0
        
        # 角色相同 +0.4
        if state1.get('role') == state2.get('role'):
            similarity += 0.4
        
        # 存活人数接近 +0.3
        alive1 = state1.get('alive_count', 9)
        alive2 = state2.get('alive_count', 9)
        if abs(alive1 - alive2) <= 1:
            similarity += 0.3
        elif abs(alive1 - alive2) <= 2:
            similarity += 0.15
        
        # 策略相似 +0.3
        strategies1 = set(state1.get('strategies_used', []))
        strategies2 = set(state2.get('strategies_used', []))
        if strategies1 and strategies2:
            overlap = len(strategies1 & strategies2) / len(strategies1 | strategies2)
            similarity += 0.3 * overlap
        
        return similarity
    
    def _calculate_trust_score(self, profile: dict) -> float:
        """计算对手信任评分"""
        # 简单规则
        base_score = 0.5
        
        # 见面次数多 → 更了解
        games_met = profile.get('games_met', 0)
        if games_met > 3:
            base_score += 0.1
        
        # 欺骗检测次数 → 降低信任
        deception = profile.get('deception_detected', 0)
        base_score -= deception * 0.1
        
        return max(0.0, min(1.0, base_score))
    
    def _find_common_strategies(self, patterns: list) -> list:
        """找出共同策略"""
        if not patterns:
            return []
        
        # 统计每个策略的出现次数
        strategy_count = {}
        for pattern in patterns:
            for strategy in pattern.get('strategies_used', []):
                strategy_count[strategy] = strategy_count.get(strategy, 0) + 1
        
        # 返回出现频率 >= 50% 的策略
        threshold = len(patterns) * 0.5
        common = [s for s, count in strategy_count.items() if count >= threshold]
        
        return common
    
    def _analyze_failure_reason(self, game: dict) -> str:
        """分析失败原因"""
        # 简化版本：基于游戏长度和角色
        game_length = len(game.get('game_flow', []))
        role = game.get('role')
        
        if game_length < 3:
            return '早期出局'
        elif role == 'werewolf':
            return '狼队被压制'
        elif role in ['seer', 'witch', 'hunter']:
            return '关键角色未发挥作用'
        else:
            return '好人阵营被狼压制'
    
    def record_game_result(self, won: bool):
        """记录游戏结果（增强版）"""
        self.total_games += 1
        if won:
            self.wins += 1
            if self.current_game['role']:
                self.role_stats[self.current_game['role']]['won'] += 1
        else:
            self.losses += 1
        
        # 存储完整游戏经验
        game_data = {
            'role': self.current_game['role'],
            'won': won,
            'key_decisions': self.current_game.get('key_decisions', []),
            'opponent_behaviors': {},  # 需要从 speeches 中提取
            'game_flow': self.current_game.get('night_results', []),
            'final_alive_count': len(self.current_game.get('alive_players', [])),
            'strategies_used': list(self.strategy_weights.keys()),
            'strategy_snapshot': self.strategy_weights.copy(),
        }
        
        # 存储经验
        self.store_game_experience(game_data)
        
        # 定期分析（每5局）
        if self.total_games % 5 == 0:
            winning_games = [g for g in self.game_history if g.get('won')]
            losing_games = [g for g in self.game_history if not g.get('won')]
            
            self.analyze_successful_patterns(winning_games)
            self.learn_from_failures(losing_games)
        
        # 重置当前游戏
        self.current_game = {
            'role': None,
            'alive_players': [],
            'dead_players': [],
            'night_results': [],
            'speeches': [],
            'votes': [],
            'key_decisions': [],
            'game_round': 0,
        }
        
        # 清空实时学习状态
        self.current_round_insights = {}
        self.opponent_live_tracking = {}
    
    def get_insights_for_prompt(self) -> str:
        """生成用于prompt的经验总结"""
        if self.total_games == 0:
            return "# 游戏经验\n这是你的第一局游戏，请谨慎观察和学习。"
        
        win_rate = self.wins / self.total_games if self.total_games > 0 else 0
        
        insights = f"""# 游戏经验总结
- 总游戏数: {self.total_games}
- 总体胜率: {win_rate:.2%}

## 各角色表现
"""
        for role, stats in self.role_stats.items():
            if stats['played'] > 0:
                role_wr = stats['won'] / stats['played']
                insights += f"- {role}: {stats['played']}局, 胜率{role_wr:.2%}\n"
        
        # 添加一些经验建议
        if win_rate < 0.3 and self.total_games >= 5:
            insights += "\n## 改进建议\n- 胜率较低，需要更谨慎的策略\n- 多观察其他玩家的发言模式\n- 避免过早暴露身份\n"
        elif win_rate > 0.6:
            insights += "\n## 继续保持\n- 当前策略效果良好，继续保持\n"
        
        return insights
    
    def to_dict(self) -> dict:
        """序列化为字典（增强版 - 包含所有学习数据）"""
        return {
            # 基础统计
            'total_games': self.total_games,
            'wins': self.wins,
            'losses': self.losses,
            'role_stats': self.role_stats,
            
            # 经验记忆系统
            'game_history': self.game_history[-20:],  # 只保存最近20局
            'opponent_models': self.opponent_models,
            'situation_index': self.situation_index,
            
            # 策略优化
            'strategy_weights': self.strategy_weights,
            'success_patterns': self.success_patterns,
            'failure_patterns': self.failure_patterns,
            'learning_rate': self.learning_rate,
            
            # 在线学习
            'phase_adaptation': self.phase_adaptation,
            'strategy_adjustments': self.strategy_adjustments[-10:],  # 最近10次调整
        }
    
    def from_dict(self, data: dict):
        """从字典恢复（增强版 - 恢复所有学习数据）"""
        # 基础统计
        self.total_games = data.get('total_games', 0)
        self.wins = data.get('wins', 0)
        self.losses = data.get('losses', 0)
        self.role_stats = data.get('role_stats', self.role_stats)
        
        # 经验记忆系统
        self.game_history = data.get('game_history', [])
        self.opponent_models = data.get('opponent_models', {})
        self.situation_index = data.get('situation_index', {
            'early_game': [],
            'mid_game': [],
            'late_game': [],
        })
        
        # 策略优化
        self.strategy_weights = data.get('strategy_weights', self.strategy_weights)
        self.success_patterns = data.get('success_patterns', [])
        self.failure_patterns = data.get('failure_patterns', [])
        self.learning_rate = data.get('learning_rate', 0.1)
        
        # 在线学习
        self.phase_adaptation = data.get('phase_adaptation', self.phase_adaptation)
        self.strategy_adjustments = data.get('strategy_adjustments', [])
        
        print(f"[记忆恢复] 已加载 {self.total_games} 局游戏经验")


class PlayerAgent(ReActAgent):
    """智能狼人杀Agent
    
    这是一个简化但高效的实现：
    - 80%的智能来自精心设计的Prompt
    - 20%来自简单的记忆和状态管理
    - 充分利用ReActAgent的内置功能
    """
    
    def __init__(self, name: str):
        """初始化Agent
        
        Args:
            name: 玩家名字
        """
        # 先保存name（在调用父类之前需要用到）
        self.name = name
        
        # 初始化记忆系统（使用 player_memory 避免与父类的 memory 冲突）
        self.player_memory = PlayerMemory()
        self.current_role: Optional[str] = None
        self._current_phase: Optional[str] = None  # 'night' 或 'day'
        self._last_logged_round: int = -1  # 用于减少重复日志
        
        # 初始化策略优化模块 - 你的负责部分！
        self.decision_maker = DecisionMaker(name)
        self.risk_assessment = RiskAssessment()
        self.team_coordination = TeamCoordination(name)
        # 新增：角色适应模块（RoleAdapter）
        self.role_adapter = RoleAdapter()
        
        # 调用父类初始化
        # 🔴 关键修复：禁用 ReActAgent 的工具系统，因为我们使用结构化输出
        super().__init__(
            name=name,
            sys_prompt=self._build_base_prompt(),  # 基础prompt
            model=self._get_model_config(),  # 🆓 使用配置的模型
            formatter=self._get_formatter(),  # 🆓 根据模型选择formatter
            toolkit=[],  # 🔴 传递空工具列表，禁用默认的 generate_response 工具
            max_iters=1,  # 🔴 只执行一次，不进行多轮推理
        )
        
        # 🔴 关键修复：覆盖 memory 的 add 方法以过滤工具调用
        original_add = self.memory.add
        def filtered_add(msg):
            """过滤工具调用消息后再添加到记忆"""
            if isinstance(msg, Msg):
                content = msg.content
                # 跳过工具调用消息
                if isinstance(content, dict) and content.get('type') == 'tool_use':
                    print(f"🔒 [{self.name}] 阻止工具调用进入记忆")
                    return
                # 跳过包含工具错误的消息
                if isinstance(content, str) and ('tool_result' in content or 'generate_response()' in content):
                    print(f"🔒 [{self.name}] 阻止工具错误进入记忆")
                    return
            return original_add(msg)
        
        self.memory.add = filtered_add
    
    def _get_model_config(self):
        """获取模型配置 - 支持多种免费模型"""
        # 🆓 从环境变量读取模型选择，默认使用 DeepSeek (你已经有 API Key)
        model_type = os.environ.get("WEREWOLF_MODEL", "deepseek")
        
        if model_type == "ollama":
            # Ollama - 本地运行，完全免费
            from agentscope.model import OllamaModel
            return OllamaModel(
                model_name=os.environ.get("OLLAMA_MODEL", "qwen2:7b"),  # 可选: llama3, mistral, qwen2
                host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
            )
        
        elif model_type == "dashscope":
            # 阿里云 DashScope - 新用户有免费额度
            from agentscope.model import DashScopeChatModel
            return DashScopeChatModel(
                api_key=os.environ.get("DASHSCOPE_API_KEY", "sk-ee90284984134a15b2a89a5359c845fa"),
                model_name=os.environ.get("DASHSCOPE_MODEL", "qwen-turbo"),  # qwen-turbo 有免费额度
            )
        
        elif model_type == "deepseek":
            # DeepSeek - 价格极低，有免费试用
            from agentscope.model import OpenAIChatModel
            return OpenAIChatModel(
                model_name="deepseek-chat",
                api_key=os.environ.get("DEEPSEEK_API_KEY", "sk-0737d735d2ac4ebdae618f16d335a631"),  # 你的 API Key
                client_args={
                    "base_url": "https://api.deepseek.com/v1",
                    "max_retries": 2,  # 降低重试次数加快失败恢复
                },
            )
        
        elif model_type == "groq":
            # Groq - 免费且极快
            from agentscope.model import OpenAIChatModel
            return OpenAIChatModel(
                model_name="llama-3.3-70b-versatile",  # 或 mixtral-8x7b-32768
                api_key=os.environ.get("GROQ_API_KEY"),
                client_args={"base_url": "https://api.groq.com/openai/v1"},  # 使用 client_args 传递 base_url
            )
        
        else:
            # 默认回退到 DeepSeek (你已经有 API Key)
            from agentscope.model import OpenAIChatModel
            return OpenAIChatModel(
                model_name="deepseek-chat",
                api_key="sk-0737d735d2ac4ebdae618f16d335a631",
                client_args={
                    "base_url": "https://api.deepseek.com/v1",
                    "max_retries": 2,
                },
            )
    
    def _get_formatter(self):
        """根据模型类型选择formatter"""
        model_type = os.environ.get("WEREWOLF_MODEL", "deepseek")
        
        if model_type == "dashscope":
            from agentscope.formatter import DashScopeMultiAgentFormatter
            return DashScopeMultiAgentFormatter()
        
        elif model_type == "deepseek":
            # DeepSeek 有专门的 formatter
            from agentscope.formatter import DeepSeekMultiAgentFormatter
            return DeepSeekMultiAgentFormatter()
        
        elif model_type == "ollama":
            # Ollama 有专门的 formatter
            from agentscope.formatter import OllamaMultiAgentFormatter
            return OllamaMultiAgentFormatter()
        
        elif model_type == "groq":
            # Groq 使用 OpenAI 兼容的 formatter (因为用的 OpenAI API 格式)
            from agentscope.formatter import OpenAIMultiAgentFormatter
            return OpenAIMultiAgentFormatter()
        
        else:
            # 默认使用 DeepSeek formatter
            from agentscope.formatter import DeepSeekMultiAgentFormatter
            return DeepSeekMultiAgentFormatter()
    
    @property
    def sys_prompt(self) -> str:
        """动态系统提示词 - 核心！
        
        根据当前角色和记忆动态生成prompt
        """
        base = self._build_base_prompt()
        role_specific = self._get_role_specific_prompt()
        memory_insights = self.player_memory.get_insights_for_prompt()
        strategy_insights = self._get_strategy_insights()  # 新增：策略建议
        
        return f"{base}\n\n{role_specific}\n\n{memory_insights}\n\n{strategy_insights}"
    
    def _get_strategy_insights(self) -> str:
        """获取策略模块的建议（增强版 - 集成学习模块）"""
        if not self.current_role:
            return ""
        
        # 构建当前游戏状态
        game_state = self._build_game_state()
        
        insights = "# 策略分析建议\n\n"
        
        # ========== 优化：仅在游戏开始后且存活玩家数据有效时执行学习模块 ==========
        alive_count = game_state.get('alive_count', 0)
        current_round = game_state.get('night_count', 0)
        
        # 只在游戏进行中且数据有效时调用学习模块
        if alive_count > 0 and current_round > 0:
            # ========== 在线学习 - 游戏流程适应 ==========
            self.player_memory.adapt_to_game_flow(current_round, game_state)
            # 只在轮次变化时输出日志，减少重复
            if current_round != getattr(self, '_last_logged_round', -1):
                print(f"🧠 [{self.name}] 学习模块: 游戏阶段适应 (轮次:{current_round}, 存活:{alive_count})")
                self._last_logged_round = current_round
        
        # 1. 风险评估（结合学习到的风险容忍度）
        reveal_risk = self.risk_assessment.evaluate_action_risk('reveal_identity', game_state)
        aggressive_risk = self.risk_assessment.evaluate_action_risk('aggressive_vote', game_state)
        win_prob = self.risk_assessment.assess_win_probability(game_state)
        
        # 应用实时调整的风险参数
        adjusted_risk = self.player_memory.current_round_insights.get('adjusted_risk', 0.5)
        adjusted_aggression = self.player_memory.current_round_insights.get('adjusted_aggression', 0.5)
        
        insights += f"## 当前局势评估\n"
        insights += f"- 暴露身份风险: {'高' if reveal_risk > 0.7 else '中' if reveal_risk > 0.4 else '低'}\n"
        insights += f"- 激进投票风险: {'高' if aggressive_risk > 0.7 else '中' if aggressive_risk > 0.4 else '低'}\n"
        insights += f"- 胜利概率: {win_prob:.1%}\n"
        insights += f"- 当前建议风险容忍度: {adjusted_risk:.2f}\n"
        insights += f"- 当前建议激进程度: {adjusted_aggression:.2f}\n\n"
        
        # ========== 经验记忆 - 检索相似情况（仅在有历史数据且游戏进行中时） ==========
        if alive_count > 0 and self.player_memory.total_games >= 3:
            similar_situations = self.player_memory.retrieve_similar_situations(game_state)
            if similar_situations:
                print(f"📚 [{self.name}] 学习模块: 检索到 {len(similar_situations)} 个相似历史情况")
                insights += f"## 历史经验参考\n"
                success_count = sum(1 for s in similar_situations if s['outcome'])
                insights += f"- 找到 {len(similar_situations)} 个相似情况\n"
                insights += f"- 其中成功 {success_count} 次\n"
                if success_count > len(similar_situations) / 2:
                    insights += f"- 建议: 当前情况历史胜率较高，可适度激进\n"
                else:
                    insights += f"- 建议: 当前情况历史胜率较低，需谨慎行动\n"
                insights += "\n"
        
        # ========== 策略优化 - 获取推荐策略（仅在游戏进行中且有历史数据时） ==========
        if alive_count > 0 and self.player_memory.total_games >= 2:
            strategy_rec = self.player_memory.get_strategy_recommendation(game_state)
            if strategy_rec.get('reasoning'):
                print(f"💡 [{self.name}] 学习模块: 生成策略推荐 (置信度:{strategy_rec['confidence']:.2f})")
                insights += f"## 策略推荐\n"
                for reason in strategy_rec['reasoning']:
                    insights += f"- {reason}\n"
                insights += f"- 推荐激进度: {strategy_rec['suggested_aggression']:.2f}\n"
                insights += f"- 推荐风险: {strategy_rec['suggested_risk']:.2f}\n"
                insights += f"- 置信度: {strategy_rec['confidence']:.2f}\n\n"
        
        # 移除冗余输出以减少token
        return insights if len(insights) < 100 else ""
    
    
    def _is_critical_event(self, content: str) -> bool:
        """判断是否为关键事件，只在关键事件时触发学习模块
        
        Args:
            content: 消息内容
            
        Returns:
            bool: 是否为关键事件
        """
        critical_keywords = [
            # 角色死亡
            'has been eliminated', 'died', '已被淘汰', '死亡',
            # 关键角色
            'seer', '预言家', 'witch', '女巫', 'hunter', '猎人',
            # 被质疑
            '怀疑', '可疑', 'suspect', 'suspicious',
            # 投票相关
            'vote', '投票', 'voting result', '投票结果',
            # 游戏结束
            'win', 'lose', '获胜', '失败', '游戏结束'
        ]
        
        content_lower = content.lower()
        return any(keyword in content_lower or keyword in content for keyword in critical_keywords)
    
    def _build_game_state(self) -> dict:
        """构建当前游戏状态字典"""
        alive_players = self.player_memory.current_game.get('alive_players', [])
        
        # 🔴 添加警告：如果存活玩家列表为空
        if not alive_players and self.player_memory.current_game.get('game_round', 0) > 0:
            print(f"⚠️ [{self.name}] 警告: alive_players 列表为空，游戏轮次 {self.player_memory.current_game.get('game_round', 0)}")
        
        return {
            'role': self.current_role,
            'alive_players': alive_players,
            'dead_players': self.player_memory.current_game.get('dead_players', []),
            'alive_count': len(alive_players),
            'speeches': self.player_memory.current_game.get('speeches', []),
            'night_results': self.player_memory.current_game.get('night_results', []),
            'votes': self.player_memory.current_game.get('votes', []),
            'wolves_alive': 3,  # 需要从游戏状态中推断
            'night_count': len(self.player_memory.current_game.get('night_results', [])) + 1
        }
    
    def _build_base_prompt(self) -> str:
        """构建基础prompt - 精简版以减少token消耗"""
        return f"""CRITICAL: You MUST respond in PLAIN TEXT only. NO function calls. NO tool_use format.

Role: {self.name} in 9-player Werewolf game
Goal: Your team wins

Players: 3 Wolves, 3 Villagers, 1 Seer, 1 Witch, 1 Hunter
Win: Wolves≥Villagers (Wolf win) | All wolves dead (Village win)

Output format:
- Plain text reasoning + JSON structure
- Example: "I think Player3 is suspicious. {{\"vote\":\"Player3\"}}"
- NEVER use: {{"type":"tool_use"}}, generate_response(), or function calls

Keep responses under 200 words.
"""
    
    def _get_role_specific_prompt(self) -> str:
        """根据当前角色返回特定的策略指导"""
        if not self.current_role:
            return ""
        # 优先使用新的角色适应模块
        fragment = self.role_adapter.get_prompt_fragment(self.current_role)
        if fragment:
            return fragment
        # 回退旧逻辑（保持兼容性）
        return ""
    
    def _extract_player_name_from_text(self, text: str | list) -> str | None:
        """
        从文本中提取玩家名字（用于当结构化输出缺失时的兜底）
        匹配格式: "Player1", "Player2", ..., "Player9"
        """
        import re
        
        # 处理content可能是列表的情况
        if isinstance(text, list):
            text = " ".join(str(item) for item in text)
        elif not isinstance(text, str):
            text = str(text)
        
        # 优先匹配 "Player[数字]" 格式 (支持Player1-Player99)
        pattern = r'\bPlayer\d+\b'
        matches = re.findall(pattern, text)
        if matches:
            # 返回最后一次出现的名字（通常是决策）
            return matches[-1]
        return None
    
    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        """观察函数 - 接收并处理游戏信息
        
        这里我们：
        1. 识别角色分配
        2. 更新游戏状态
        3. 积累经验
        """
        if msg is None:
            return await super().observe(msg)
        
        # 处理单个消息或消息列表
        messages = [msg] if isinstance(msg, Msg) else msg
        
        # 🔴 关键修复：过滤掉工具调用消息，防止后续玩家模仿
        filtered_messages = []
        for message in messages:
            content = message.content
            
            # 检测并跳过工具调用消息
            is_tool_call = False
            if isinstance(content, dict):
                if content.get('type') == 'tool_use' or 'name' in content and content.get('name') == 'generate_response':
                    is_tool_call = True
                    print(f"🔒 [{self.name}] 过滤掉工具调用消息")
            elif isinstance(content, str):
                if 'tool_result' in content or 'generate_response()' in content:
                    is_tool_call = True
                    print(f"🔒 [{self.name}] 过滤掉工具错误消息")
            
            if not is_tool_call:
                filtered_messages.append(message)
        
        # 只处理过滤后的消息
        for message in filtered_messages:
            # 处理content可能是字符串或列表的情况
            content = message.content
            if isinstance(content, list):
                content = " ".join(str(item) for item in content)
            elif not isinstance(content, str):
                content = str(content)
            
            # 识别角色分配（支持中英文）
            if f"[{self.name} ONLY]" in content or f"【仅{self.name}可见】" in content:
                # 英文: "your role is werewolf"
                role_match = re.search(r'your role is (\w+)', content, re.IGNORECASE)
                # 中文: "你的角色是狼人" 或 "身份是狼人"
                if not role_match:
                    role_match = re.search(r'(?:你的)?(?:角色|身份)(?:是|为)[:：]?\s*(\S+)', content)
                
                if role_match:
                    role = role_match.group(1).lower()
                    # 中文角色名映射到英文
                    role_mapping = {
                        '狼人': 'werewolf',
                        '村民': 'villager', 
                        '预言家': 'seer',
                        '女巫': 'witch',
                        '猎人': 'hunter'
                    }
                    self.current_role = role_mapping.get(role, role)
                    self.player_memory.update_role(self.current_role)
                    self._game_recorded = False  # 重置战绩记录标志
                    print(f"[{self.name}] 角色分配: {self.current_role}")
            
            # 识别游戏结束（支持中英文）
            content_lower = content.lower()
            is_game_over = (
                ("werewolves win" in content_lower or "狼人获胜" in content) or
                ("villagers win" in content_lower or "村民获胜" in content) or
                ("游戏结束" in content and ("获胜" in content or "win" in content_lower))
            )
            
            if is_game_over:
                # 判断是否获胜
                won = False
                is_werewolf_win = "werewolves win" in content_lower or "狼人获胜" in content
                is_villager_win = "villagers win" in content_lower or "村民获胜" in content
                
                if is_werewolf_win and self.current_role == "werewolf":
                    won = True
                elif is_villager_win and self.current_role != "werewolf":
                    won = True
                
                self.player_memory.record_game_result(won)
                self._game_recorded = True  # 标记已记录
                print(f"[{self.name}] 游戏结束，{'胜利' if won else '失败'}")
            
            # 更新记忆
            self.player_memory.update_from_msg(message)
            
            # 更新当前阶段
            if 'night' in content.lower():
                self._current_phase = 'night'
            elif 'day' in content.lower() or 'discussion' in content.lower():
                self._current_phase = 'day'
            
            # ========== 优化：实时学习 - 仅在关键事件时触发 ==========
            if self._is_critical_event(content):
                # 检测关键角色死亡
                if "has been eliminated" in content or "died" in content:
                    # 分析是否是关键角色
                    if "seer" in content.lower() or "预言家" in content:
                        print(f"⚡ [{self.name}] 关键事件: 预言家死亡，调整策略")
                        self.player_memory.adjust_strategy_realtime({
                            'event_type': 'key_role_died',
                            'role': 'seer',
                            'round': self.player_memory.current_game.get('game_round', 0),
                        })
                    elif "witch" in content.lower() or "女巫" in content:
                        print(f"⚡ [{self.name}] 关键事件: 女巫死亡，调整策略")
                        self.player_memory.adjust_strategy_realtime({
                            'event_type': 'key_role_died',
                            'role': 'witch',
                            'round': self.player_memory.current_game.get('game_round', 0),
                        })
                
                # 检测被质疑事件（如果消息中提到自己且有"怀疑"、"可疑"等词）
                if self.name in content:
                    if any(word in content for word in ['怀疑', '可疑', 'suspect', 'suspicious']):
                        print(f"⚡ [{self.name}] 关键事件: 被质疑，调整防御姿态")
                        self.player_memory.adjust_strategy_realtime({
                            'event_type': '被质疑',
                            'round': self.player_memory.current_game.get('game_round', 0),
                        })
            
            # ========== 优化：实时跟踪对手行为（仅在关键事件或有意义的发言时） ==========
            if hasattr(message, 'name') and message.name and message.name != self.name:
                # 排除 Moderator（裁判不是玩家）
                if message.name == 'Moderator':
                    continue
                
                # 只在有意义的内容时记录（长度>20字符或关键事件）
                if len(content) > 20 or self._is_critical_event(content):
                    player_name = message.name
                    
                    # 初始化跟踪
                    if player_name not in self.player_memory.opponent_live_tracking:
                        self.player_memory.opponent_live_tracking[player_name] = {
                            'actions': [],
                            'style': 'unknown',
                        }
                    
                    # 记录行为
                    action_type = 'speech' if len(content) > 20 else 'short_speech'
                    self.player_memory.opponent_live_tracking[player_name]['actions'].append({
                        'type': action_type,
                        'content': content[:100],  # 只保留前100字符
                        'round': self.player_memory.current_game.get('game_round', 0),
                    })
                    
                    # 实时识别策略（每3次行为分析一次）
                    if len(self.player_memory.opponent_live_tracking[player_name]['actions']) >= 3:
                        opponent_strategy = self.player_memory.recognize_opponent_strategy(
                            self.player_memory.opponent_live_tracking[player_name]['actions']
                        )
                        if opponent_strategy != self.player_memory.opponent_live_tracking[player_name]['style']:
                            print(f"👁️ [{self.name}] 对手分析: {player_name} 策略识别为 '{opponent_strategy}'")
                        self.player_memory.opponent_live_tracking[player_name]['style'] = opponent_strategy
        
        # 调用父类的observe
        return await super().observe(msg)
    
    async def __call__(self, *args, **kwargs):
        """重写__call__方法以验证结构化输出
        
        确保所有结构化输出字段都存在,避免metadata缺失导致的错误
        由于我们禁用了工具系统(toolkit=[])，不再需要处理工具调用错误
        """
        max_retries = 2
        last_error = None
        
        for attempt in range(max_retries):
            try:
                # 🔴 清理输入消息 - 移除工具调用以防止模型模仿
                if 'x' in kwargs:  # x 是 ReActAgent 的输入消息参数
                    x = kwargs['x']
                    if isinstance(x, Msg):
                        # 清理单个消息
                        if isinstance(x.content, dict) and x.content.get('type') == 'tool_use':
                            print(f"🔒 [{self.name}] 阻止工具调用消息进入模型")
                            x.content = "[此消息已过滤]"
                    elif isinstance(x, list):
                        # 清理消息列表
                        for i, item in enumerate(x):
                            if isinstance(item, Msg) and isinstance(item.content, dict):
                                if item.content.get('type') == 'tool_use':
                                    print(f"🔒 [{self.name}] 阻止工具调用消息进入模型")
                                    x[i].content = "[此消息已过滤]"
                
                # 🔴 关键修复：动态添加结构化输出格式提示
                if 'structured_model' in kwargs and kwargs['structured_model'] is not None:
                    model_class = kwargs['structured_model']
                    
                    # 构建字段说明
                    format_hint = "\n\n📋 REQUIRED OUTPUT FORMAT:\n"
                    if hasattr(model_class, 'model_fields'):
                        for field_name, field_info in model_class.model_fields.items():
                            field_type = "boolean" if field_info.annotation == bool else "string"
                            required = "REQUIRED" if field_info.is_required() else "optional"
                            desc = field_info.description or ""
                            format_hint += f"- {field_name} ({field_type}, {required}): {desc}\n"
                    
                    format_hint += "\nOutput ONLY the JSON object matching this schema.\n"
                    
                    # 将格式提示添加到输入消息
                    if 'x' in kwargs and isinstance(kwargs['x'], Msg):
                        original_content = kwargs['x'].content
                        kwargs['x'].content = f"{original_content}\n{format_hint}"
                
                # 调用父类方法
                response = await super().__call__(*args, **kwargs)
                
                # 🔴 强制过滤工具调用 - DeepSeek经常输出tool_use格式
                if hasattr(response, 'content'):
                    content = response.content
                    # 如果content是工具调用对象,提取实际文本
                    if isinstance(content, dict):
                        if 'type' in content and content['type'] == 'tool_use':
                            # 从工具调用中提取文本
                            if 'arguments' in content and isinstance(content['arguments'], dict):
                                if 'response' in content['arguments']:
                                    response.content = content['arguments']['response']
                                    print(f"⚠️ [{self.name}] 过滤工具调用,提取文本: {response.content[:50]}...")
                    # 如果content是列表,过滤掉工具调用元素
                    elif isinstance(content, list):
                        filtered = []
                        for item in content:
                            if isinstance(item, dict) and item.get('type') == 'tool_use':
                                if 'arguments' in item and 'response' in item['arguments']:
                                    filtered.append(item['arguments']['response'])
                                    print(f"⚠️ [{self.name}] 从列表过滤工具调用")
                            else:
                                filtered.append(item)
                        if filtered:
                            response.content = filtered
                
                # 如果使用了结构化输出模型,验证响应完整性
                if 'structured_model' in kwargs and kwargs['structured_model'] is not None:
                    model_class = kwargs['structured_model']
                    
                    # 🔴 修复: 确保metadata存在
                    if response.metadata is None:
                        response.metadata = {}
                    
                    # 🔴 新增：从文本中提取 JSON 并填充 metadata（DeepSeek 经常输出"文本+JSON"）
                    # 检查是否需要从文本提取（metadata为空或缺少必需字段）
                    needs_extraction = False
                    if hasattr(model_class, 'model_fields'):
                        for field_name, field_info in model_class.model_fields.items():
                            if field_info.is_required() and field_name not in response.metadata:
                                needs_extraction = True
                                break
                    
                    if needs_extraction:
                        import json
                        import re
                        
                        content_str = str(response.content)
                        # 尝试提取 JSON 对象（支持多行）
                        json_match = re.search(r'\{[^{}]*\}', content_str, re.DOTALL)
                        if json_match:
                            try:
                                json_obj = json.loads(json_match.group())
                                # 将提取的 JSON 填充到 metadata
                                for key, value in json_obj.items():
                                    if key not in response.metadata:
                                        response.metadata[key] = value
                                if attempt == 0:
                                    print(f"✅ [{self.name}] 从文本提取JSON到metadata: {json_obj}")
                            except json.JSONDecodeError as e:
                                if attempt == 0:
                                    print(f"⚠️ [{self.name}] JSON解析失败: {e}")
                    
                    # 🔴 修复字段名映射: LLM有时输出错误的字段名
                    field_mappings = {
                        'check_target': 'name',    # Seer: 查验目标
                        'target': 'name',          # 通用: 目标玩家
                        'shoot_target': 'name',    # Hunter: 射杀目标
                        'poison_target': 'name',   # Witch: 毒杀目标
                        'vote_target': 'vote',     # Vote: 投票目标
                    }
                    
                    for wrong_field, correct_field in field_mappings.items():
                        if wrong_field in response.metadata and correct_field not in response.metadata:
                            response.metadata[correct_field] = response.metadata[wrong_field]
                            del response.metadata[wrong_field]
                            print(f"⚠️ [{self.name}] 字段映射: {wrong_field} -> {correct_field} = {response.metadata[correct_field]}")
                    
                    # 🔴 修复: 确保所有必需字段都在metadata中
                    if hasattr(model_class, 'model_fields'):
                        for field_name, field_info in model_class.model_fields.items():
                            # 如果是必填字段但不在metadata中,添加默认值
                            if field_info.is_required() and field_name not in response.metadata:
                                # 根据字段类型设置默认值
                                if field_info.annotation == bool:
                                    default_value = False
                                elif field_name == 'reach_agreement':
                                    # 狼人讨论: 检测文本中是否有同意/达成一致的迹象
                                    text = str(response.content)
                                    agreement_keywords = ['agree', 'agreed', '同意', '一致', 'consensus', "let's go"]
                                    default_value = any(kw in text.lower() for kw in agreement_keywords)
                                elif field_name == 'poison':
                                    default_value = False  # 女巫默认不使用毒药
                                elif field_name == 'resurrect':
                                    default_value = False  # 女巫默认不救人
                                elif field_name == 'shoot':
                                    default_value = False  # 猎人默认不开枪
                                elif field_name == 'name':
                                    # name字段由LLM推理决定(查验/射杀/毒杀目标)
                                    extracted_name = self._extract_player_name_from_text(response.content)
                                    if extracted_name:
                                        default_value = extracted_name
                                        print(f"⚠️ [{self.name}] 从文本提取目标: {extracted_name}")
                                    else:
                                        # 无法提取则设为None (对于Witch/Hunter可选,Seer必填会触发重试)
                                        default_value = None
                                elif field_name == 'vote':
                                    # vote字段由LLM推理决定,是必填字段!
                                    extracted_name = self._extract_player_name_from_text(response.content)
                                    if extracted_name:
                                        default_value = extracted_name
                                        print(f"⚠️ [{self.name}] 从文本提取投票目标: {extracted_name}")
                                    else:
                                        # 完全无法提取,触发重试而非直接崩溃
                                        if attempt < max_retries - 1:
                                            print(f"⚠️ [{self.name}] 未找到投票目标,将重试...")
                                            raise ValueError(f"LLM未输出投票目标 'vote' 字段")
                                        else:
                                            # 最后一次尝试失败,随机选一个(避免游戏卡死)
                                            print(f"❌ [{self.name}] 多次重试仍无法提取投票目标,将跳过")
                                            default_value = None
                                else:
                                    default_value = None
                                
                                response.metadata[field_name] = default_value
                                if attempt == 0:  # 只在第一次尝试时打印
                                    print(f"⚠️ [{self.name}] 结构化输出修复: 添加缺失字段 '{field_name}' = {default_value}")
                
                return response
                
            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    print(f"⚠️ [{self.name}] 调用失败 (尝试 {attempt + 1}/{max_retries}): {str(e)[:100]}")
                    import asyncio
                    await asyncio.sleep(1)  # 短暂等待后重试
                else:
                    print(f"❌ [{self.name}] 调用失败，已达最大重试次数: {str(e)[:100]}")
                    raise
    
    def state_dict(self) -> dict:
        """保存状态 - 跨局学习的关键！"""
        base_state = super().state_dict()
        return {
            **base_state,
            'player_memory': self.player_memory.to_dict(),
            'current_role': self.current_role,
            '_current_phase': self._current_phase,
        }
    
    def load_state_dict(self, state: dict) -> None:
        """加载状态 - 恢复之前的经验"""
        # 加载父类状态
        super().load_state_dict(state)
        
        # 确保 player_memory 存在（防止被父类覆盖）
        if not hasattr(self, 'player_memory') or not isinstance(self.player_memory, PlayerMemory):
            self.player_memory = PlayerMemory()
        
        # 加载记忆
        if 'player_memory' in state:
            self.player_memory.from_dict(state['player_memory'])
        
        # 加载当前角色（如果有）
        self.current_role = state.get('current_role', None)
        self._current_phase = state.get('_current_phase', None)
        
        print(f"[{self.name}] 状态已加载，历史游戏数: {self.player_memory.total_games}")
