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
    """玩家记忆系统 - 跨局学习的核心"""
    
    def __init__(self):
        # 当前游戏状态
        self.current_game = {
            'role': None,
            'alive_players': [],
            'dead_players': [],
            'night_results': [],
            'speeches': [],
            'votes': []
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
        
        # 玩家行为模式（简单版本）
        self.player_profiles = {}  # {player_name: {'suspicious_count': 0, 'survived_games': 0}}
        
        # 策略经验
        self.learned_patterns = []  # 成功的策略模式
    
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
        
        # 提取死亡信息
        if "has been eliminated" in content or "died" in content:
            # 简单的玩家名提取
            import re
            player_pattern = r'Player\d+'
            players = re.findall(player_pattern, content)
            for player in players:
                if player not in self.current_game['dead_players']:
                    self.current_game['dead_players'].append(player)
                if player in self.current_game['alive_players']:
                    self.current_game['alive_players'].remove(player)
        
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
    
    def record_game_result(self, won: bool):
        """记录游戏结果"""
        self.total_games += 1
        if won:
            self.wins += 1
            if self.current_game['role']:
                self.role_stats[self.current_game['role']]['won'] += 1
        else:
            self.losses += 1
        
        # 重置当前游戏
        self.current_game = {
            'role': None,
            'alive_players': [],
            'dead_players': [],
            'night_results': [],
            'speeches': [],
            'votes': []
        }
    
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
        """序列化为字典"""
        return {
            'total_games': self.total_games,
            'wins': self.wins,
            'losses': self.losses,
            'role_stats': self.role_stats,
            'player_profiles': self.player_profiles,
            'learned_patterns': self.learned_patterns
        }
    
    def from_dict(self, data: dict):
        """从字典恢复"""
        self.total_games = data.get('total_games', 0)
        self.wins = data.get('wins', 0)
        self.losses = data.get('losses', 0)
        self.role_stats = data.get('role_stats', self.role_stats)
        self.player_profiles = data.get('player_profiles', {})
        self.learned_patterns = data.get('learned_patterns', [])


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
        
        # 初始化策略优化模块 - 你的负责部分！
        self.decision_maker = DecisionMaker(name)
        self.risk_assessment = RiskAssessment()
        self.team_coordination = TeamCoordination(name)
        # 新增：角色适应模块（RoleAdapter）
        self.role_adapter = RoleAdapter()
        
        # 调用父类初始化
        super().__init__(
            name=name,
            sys_prompt=self._build_base_prompt(),  # 基础prompt
            model=DashScopeChatModel(
                api_key=os.environ.get("DASHSCOPE_API_KEY"),
                model_name="qwen3-max",
            ),
            formatter=DashScopeMultiAgentFormatter(),
        )
    
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
        """获取策略模块的建议"""
        if not self.current_role:
            return ""
        
        # 构建当前游戏状态
        game_state = self._build_game_state()
        
        insights = "# 策略分析建议\n\n"
        
        # 1. 风险评估
        reveal_risk = self.risk_assessment.evaluate_action_risk('reveal_identity', game_state)
        aggressive_risk = self.risk_assessment.evaluate_action_risk('aggressive_vote', game_state)
        win_prob = self.risk_assessment.assess_win_probability(game_state)
        
        insights += f"## 当前局势评估\n"
        insights += f"- 暴露身份风险: {'高' if reveal_risk > 0.7 else '中' if reveal_risk > 0.4 else '低'}\n"
        insights += f"- 激进投票风险: {'高' if aggressive_risk > 0.7 else '中' if aggressive_risk > 0.4 else '低'}\n"
        insights += f"- 胜利概率: {win_prob:.1%}\n\n"
        
        # 2. 决策建议（如果有具体场景）
        if hasattr(self, '_current_phase') and self._current_phase:
            if self._current_phase == 'night':
                decision = self.decision_maker.night_phase_decision(self.current_role, game_state)
                # 角色策略微调夜晚决策
                decision = self.role_adapter.shape_night_decision(self.current_role, decision)
                insights += f"## 夜晚决策建议\n"
                insights += f"- {decision.get('reasoning', '根据当前情况谨慎选择')}\n\n"
            elif self._current_phase == 'day':
                decision = self.decision_maker.day_phase_decision(game_state)
                # 角色策略微调白天决策
                decision = self.role_adapter.shape_day_decision(self.current_role, decision)
                insights += f"## 白天策略建议\n"
                insights += f"- 发言策略: {decision.get('speech', '分析局势')}\n\n"
        
        # 3. 团队协作（狼人专用）
        if self.current_role == 'werewolf':
            coordination = self.team_coordination.coordinate_with_team(
                [], 'night_kill', game_state
            )
            insights += f"## 团队协作提示\n"
            insights += f"- {coordination.get('communication_strategy', '与队友充分沟通')}\n\n"
        
        return insights
    
    def _build_game_state(self) -> dict:
        """构建当前游戏状态字典"""
        return {
            'role': self.current_role,
            'alive_players': self.player_memory.current_game.get('alive_players', []),
            'dead_players': self.player_memory.current_game.get('dead_players', []),
            'alive_count': len(self.player_memory.current_game.get('alive_players', [])),
            'speeches': self.player_memory.current_game.get('speeches', []),
            'night_results': self.player_memory.current_game.get('night_results', []),
            'votes': self.player_memory.current_game.get('votes', []),
            'wolves_alive': 3,  # 需要从游戏状态中推断
            'night_count': len(self.player_memory.current_game.get('night_results', [])) + 1
        }
    
    def _build_base_prompt(self) -> str:
        """构建基础prompt"""
        return f"""你是 {self.name}，一名狼人杀游戏玩家。

# 游戏目标
你的目标是和你的队友一起获得胜利。

# 游戏规则
九人局狼人杀：
- 3个狼人 🐺：每晚杀一人，白天隐藏身份
- 3个村民 👨‍🌾：无特殊能力，通过推理找出狼人
- 1个预言家 🔮：每晚可以查验一人的身份
- 1个女巫 🧙‍♀️：有一瓶解药和一瓶毒药（各只能用一次）
- 1个猎人 🏹：死亡时可以开枪带走一人

胜利条件：
- 狼人胜：狼人数量 ≥ 好人数量
- 好人胜：所有狼人死亡

# 游戏流程
1. 夜晚阶段：
   - 狼人讨论并投票杀人
   - 女巫决定是否使用药水
   - 预言家查验一人身份
   
2. 白天阶段：
   - 法官宣布夜晚结果
   - 所有存活玩家依次发言
   - 投票淘汰一人

# 核心策略指导

## 通用原则
- 仔细分析每个人的发言，寻找逻辑漏洞
- 注意投票行为，谁投了谁很重要
- 夜晚结果提供关键线索（是否有人被救、被毒等）
- 不要编造不存在的信息
- 发言要简洁有力，提供清晰的推理链

## 身份隐藏与伪装
- 作为狼人时，可以伪装成村民或其他角色
- 保持发言的一致性，避免前后矛盾
- 适当时候可以质疑别人，但不要过于激进

## 信息分析
- 谁说自己是预言家？验证结果是否合理？
- 谁的发言逻辑有问题？
- 谁在跟票？谁在带节奏？
- 注意观察沉默的玩家

## 重要提醒
- 这是文本游戏，不要编造非文本信息
- 必须基于已知事实进行推理
- 避免重复他人的发言
- 你的回复要简洁（避免超过2048字符）
- 决策要在30秒内完成
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
        
        for message in messages:
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
                print(f"[{self.name}] 游戏结束，{'胜利' if won else '失败'}")
            
            # 更新记忆
            self.player_memory.update_from_msg(message)
            
            # 更新当前阶段
            if 'night' in content.lower():
                self._current_phase = 'night'
            elif 'day' in content.lower() or 'discussion' in content.lower():
                self._current_phase = 'day'
        
        # 调用父类的observe
        return await super().observe(msg)
    
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
