# -*- coding: utf-8 -*-
"""智能狼人杀Agent - 简化但高效的实现"""
import os
import re
import asyncio
from types import UnionType
from typing import Optional, Dict, Any, Literal, Type, Union, get_args, get_origin
from pydantic import BaseModel
from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeMultiAgentFormatter
from agentscope.model import DashScopeChatModel
from agentscope.message import Msg

# Debug flag to reduce high-frequency prints. Set env var `WEREWOLF_DEBUG=1` to enable.
WEREWOLF_DEBUG = str(os.environ.get("WEREWOLF_DEBUG", "0")).lower() in ("1", "true", "yes")

def dbg(msg: str) -> None:
    """Conditional debug print controlled by `WEREWOLF_DEBUG` env var."""
    if WEREWOLF_DEBUG:
        print(msg)

# 全局API限流器 - 限制并发API调用数量，避免qwen3-max限流
# 从环境变量读取最大并发数，默认为2（保守设置）
MAX_CONCURRENT_API_CALLS = int(os.environ.get("MAX_CONCURRENT_API_CALLS", "2"))
_api_semaphore = asyncio.Semaphore(MAX_CONCURRENT_API_CALLS)


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
            "狼人：目标——伪装好人，优先夜晚击杀关键功能役（预言家/女巫/猎人）；"
            "讨论时绝对绝对不要暴露自己的狼人身份，始终伪装成好人阵营；"
            "讨论时绝对绝对不要暴露自己的狼人身份，始终伪装成好人阵营；"
            "讨论时绝对绝对不要暴露自己的狼人身份，始终伪装成好人阵营；"
            "讨论时绝对绝对不要暴露自己的狼人身份，始终伪装成好人阵营；"
            "夜间：列2~3候选并说明最小风险理由；白天：低调跟随并给出中性疑点。"
        )

    def adjust_day_decision(self, decision: Dict[str, Any]) -> Dict[str, Any]:
        # 稍微缓和激进的投票建议，增加推理过程
        if isinstance(decision, dict) and 'vote' in decision and decision['vote']:
            decision['vote_reasoning'] = (
                "保持适度压力；避免过于强势以减少怀疑"
            )
        return decision


class _VillagerStrategy(_RoleStrategy):
    def prompt(self) -> str:  # noqa: D401 - short override
        return (
            "村民：目标——收集信息并投票保护好人；白天：提供简洁事实→推断→结论；"
            "投票：给出1~2名候选并说明关键理由，避免情绪化跟票。"
        )


class _SeerStrategy(_RoleStrategy):
    def prompt(self) -> str:  # noqa: D401 - short override
        return (
            "预言家：目标——保留信息价值并在合适时机公开；夜间：优先查验信息量大或引导节奏的玩家；"
            "公开时：给出简洁验人日志（N#: 玩家→结果→简短理由）。"
        )

    def adjust_night_decision(self, decision: Dict[str, Any]) -> Dict[str, Any]:
        # 确保推理字段的清晰性
        if 'reasoning' in decision:
            decision['reasoning'] += " | 专注于信息量大的目标"
        return decision


class _WitchStrategy(_RoleStrategy):
    def prompt(self) -> str:  # noqa: D401 - short override
        return (
            "女巫：目标——谨慎保药/毒以控制信息与存活；夜间：若死亡者为关键角色优先救；"
            "女巫和猎人都是好人阵营，不要互相攻击或怀疑，应该团结对抗狼人；"
            "中期毒人需≥2证据，公开用药时给出简短理由。"
        )


class _HunterStrategy(_RoleStrategy):
    def prompt(self) -> str:  # noqa: D401 - short override
        return (
            "猎人：目标——保留开枪权直至高置信度；开枪需≥70%置信，优先带走高概率狼。"
            "公布身份时适当考虑下当前局势"
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
    
    def __init__(self, agent_name: str, team_coordination=None):
        self.agent_name = agent_name
        self.team_coordination = team_coordination  # 新增：团队协作模块引用
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
        """狼人杀人决策 - 增强版
        
        优先级：预言家 > 女巫 > 猎人 > 活跃村民 > 普通村民
        """
        # print(f"\n🎯 [{self.agent_name}] ===== 狼人杀人决策开始 =====")
        alive_players = game_state.get('alive_players', [])
        speeches = game_state.get('speeches', [])
        votes = game_state.get('votes', [])
        night_count = game_state.get('night_count', 1)
        dead_players = game_state.get('dead_players', [])
        teammates = game_state.get('teammates', [])
        
        # print(f"   存活玩家: {alive_players}")
        # print(f"   夜晚轮次: {night_count}")
        
        # 🤝 调用团队协作模块（如果是狼人且有队友）
        if self.team_coordination and teammates:
            # print(f"\n🤝 [{self.agent_name}] ===== 调用团队协作模块 =====")
            # print(f"   队友列表: {teammates}")
            team_advice = self.team_coordination.coordinate_with_team(
                teammates, 'night_kill', game_state
            )
            # print(f"   团队建议目标: {team_advice.get('target_priority', [])}")
            # print(f"   协作策略: {team_advice.get('communication_strategy', '')}")
            # print(f"🤝 [{self.agent_name}] ===== 团队协作模块调用完成 =====\n")
        
        priority_targets = []
        reasoning_parts = []
        
        # 1. 识别可能的预言家
        suspected_seer = self._identify_seer_from_speeches(game_state)
        if suspected_seer and suspected_seer in alive_players:
            priority_targets.append(suspected_seer)
            reasoning_parts.append(f"疑似预言家: {suspected_seer}")
        
        # 2. 识别可能的女巫（第一晚有人被救说明女巫存活且用了解药）
        suspected_witch = self._identify_witch_from_behavior(game_state)
        if suspected_witch and suspected_witch in alive_players and suspected_witch not in priority_targets:
            priority_targets.append(suspected_witch)
            reasoning_parts.append(f"疑似女巫: {suspected_witch}")
        
        # 3. 识别可能的猎人（发言强势、逻辑清晰但不像预言家）
        suspected_hunter = self._identify_hunter_from_behavior(game_state)
        if suspected_hunter and suspected_hunter in alive_players and suspected_hunter not in priority_targets:
            priority_targets.append(suspected_hunter)
            reasoning_parts.append(f"疑似猎人: {suspected_hunter}")
        
        # 4. 如果没有明确目标，选择发言活跃的玩家（可能是关键角色）
        if not priority_targets:
            active_players = self._get_active_players(game_state)
            for player in active_players:
                if player in alive_players and player != self.agent_name:
                    priority_targets.append(player)
                    reasoning_parts.append(f"活跃玩家: {player}")
                    break
        
        # 5. 兜底：随机选择一个非队友的存活玩家
        if not priority_targets:
            teammates = game_state.get('teammates', [])
            for player in alive_players:
                if player != self.agent_name and player not in teammates:
                    priority_targets.append(player)
                    reasoning_parts.append(f"随机目标: {player}")
                    break
        
        result = {
            'suggested_targets': priority_targets,
            'reasoning': ' | '.join(reasoning_parts) if reasoning_parts else '无明确目标，随机选择'
        }
        # print(f"   推荐目标: {priority_targets}")
        # print(f"   决策理由: {result['reasoning']}")
        # print(f"🎯 [{self.agent_name}] ===== 狼人杀人决策结束 =====\n")
        return result
    
    def _identify_witch_from_behavior(self, game_state: dict) -> str:
        """从行为中识别可能的女巫"""
        speeches = game_state.get('speeches', [])
        night_results = game_state.get('night_results', [])
        
        witch_indicators = {}
        
        for speech in speeches:
            speaker = speech.get('speaker', '')
            content = speech.get('content', '').lower()
            
            if speaker not in witch_indicators:
                witch_indicators[speaker] = 0
            
            # 女巫特征：讨论救人/毒人、药水使用时机
            witch_keywords = ['救', '毒', '解药', '毒药', 'save', 'poison', 'heal', 'potion']
            for keyword in witch_keywords:
                if keyword in content:
                    witch_indicators[speaker] += 2
            
            # 提及夜晚有人被救
            if '被救' in content or 'saved' in content or '没死' in content:
                witch_indicators[speaker] += 1
        
        # 返回得分最高的玩家
        if witch_indicators:
            sorted_suspects = sorted(witch_indicators.items(), key=lambda x: x[1], reverse=True)
            if sorted_suspects[0][1] >= 3:
                return sorted_suspects[0][0]
        
        return ""
    
    def _identify_hunter_from_behavior(self, game_state: dict) -> str:
        """从行为中识别可能的猎人"""
        speeches = game_state.get('speeches', [])
        
        hunter_indicators = {}
        
        for speech in speeches:
            speaker = speech.get('speaker', '')
            content = speech.get('content', '').lower()
            
            if speaker not in hunter_indicators:
                hunter_indicators[speaker] = 0
            
            # 猎人特征：逻辑清晰、态度坚定、暗示有能力带走某人
            hunter_keywords = ['带走', '枪', '开枪', 'shoot', 'gun', 'take down']
            for keyword in hunter_keywords:
                if keyword in content:
                    hunter_indicators[speaker] += 3
            
            # 态度强硬但不跳预言家身份
            strong_keywords = ['一定是狼', '肯定是', '必须投', 'must vote', 'definitely']
            for keyword in strong_keywords:
                if keyword in content:
                    hunter_indicators[speaker] += 1
        
        if hunter_indicators:
            sorted_suspects = sorted(hunter_indicators.items(), key=lambda x: x[1], reverse=True)
            if sorted_suspects[0][1] >= 3:
                return sorted_suspects[0][0]
        
        return ""
    
    def _get_active_players(self, game_state: dict) -> list:
        """获取发言活跃的玩家列表"""
        speeches = game_state.get('speeches', [])
        speech_count = {}
        
        for speech in speeches:
            speaker = speech.get('speaker', '')
            if speaker:
                speech_count[speaker] = speech_count.get(speaker, 0) + 1
        
        # 按发言次数排序
        sorted_players = sorted(speech_count.items(), key=lambda x: x[1], reverse=True)
        return [player for player, count in sorted_players]
    
    def seer_check_decision(self, game_state: dict) -> dict:
        """预言家查验决策"""
        # print(f"\n🔮 [{self.agent_name}] ===== 预言家查验决策开始 =====")
        alive_players = game_state.get('alive_players', [])
        speeches = game_state.get('speeches', [])
        # print(f"   存活玩家: {alive_players}")
        
        # 优先查验发言可疑或跳预言家的人
        suspicious_players = self._identify_suspicious_players(speeches)
        # print(f"   可疑玩家: {suspicious_players}")
        
        suggested = suspicious_players[0] if suspicious_players else (alive_players[0] if alive_players else None)
        
        result = {
            'suggested_target': suggested,
            'reasoning': '查验发言可疑的玩家'
        }
        # print(f"   推荐查验: {suggested}")
        # print(f"🔮 [{self.agent_name}] ===== 预言家查验决策结束 =====\n")
        return result
    
    def witch_potion_decision(self, game_state: dict) -> dict:
        """女巫用药决策 - 增强版"""
        # print(f"\n🧙 [{self.agent_name}] ===== 女巫用药决策开始 =====")
        killed_player = game_state.get('killed_player')
        has_heal = game_state.get('has_heal', True)
        has_poison = game_state.get('has_poison', True)
        night_count = game_state.get('night_count', 1)
        alive_players = game_state.get('alive_players', [])
        speeches = game_state.get('speeches', [])
        
        # print(f"   被杀玩家: {killed_player}")
        # print(f"   拥有解药: {has_heal}, 拥有毒药: {has_poison}")
        # print(f"   夜晚轮次: {night_count}")
        
        decision = {
            'use_heal': False,
            'use_poison': False,
            'poison_target': None,
            'reasoning': []
        }
        
        # === 解药决策 ===
        if has_heal and killed_player:
            should_heal = False
            heal_reason = ""
            
            # 判断被杀者是否可能是关键角色
            if self._is_likely_seer(killed_player, game_state):
                should_heal = True
                heal_reason = f"被杀者{killed_player}疑似预言家，必须救"
            elif night_count == 1:
                # 第一晚信息少，倾向于救人
                should_heal = True
                heal_reason = "第一晚信息不足，选择救人"
            elif self._is_active_player(killed_player, game_state):
                # 活跃玩家可能是关键角色
                should_heal = True
                heal_reason = f"{killed_player}发言活跃，可能是关键角色"
            elif len(alive_players) <= 5:
                # 人数少时谨慎使用解药
                should_heal = False
                heal_reason = "人数较少，保留解药"
            
            decision['use_heal'] = should_heal
            if heal_reason:
                decision['reasoning'].append(heal_reason)
        
        # === 毒药决策 ===
        if has_poison and not decision['use_heal']:
            poison_target = self._find_poison_target(game_state)
            if poison_target:
                decision['use_poison'] = True
                decision['poison_target'] = poison_target
                decision['reasoning'].append(f"高置信度毒{poison_target}")
        
        decision['reasoning'] = ' | '.join(decision['reasoning']) if decision['reasoning'] else '保留药水'
        
        # print(f"   决策结果: 使用解药={decision['use_heal']}, 使用毒药={decision['use_poison']}")
        # if decision['poison_target']:
        #     print(f"   毒药目标: {decision['poison_target']}")
        # print(f"   决策理由: {decision['reasoning']}")
        # print(f"🧙 [{self.agent_name}] ===== 女巫用药决策结束 =====\n")
        return decision
    
    def _is_likely_seer(self, player: str, game_state: dict) -> bool:
        """判断玩家是否可能是预言家"""
        speeches = game_state.get('speeches', [])
        for speech in speeches:
            if speech.get('speaker') == player:
                content = speech.get('content', '').lower()
                seer_keywords = ['验', '查', '是狼', '是好人', 'check', 'werewolf', 'villager']
                for keyword in seer_keywords:
                    if keyword in content:
                        return True
        return False
    
    def _is_active_player(self, player: str, game_state: dict) -> bool:
        """判断玩家是否活跃"""
        speeches = game_state.get('speeches', [])
        count = sum(1 for s in speeches if s.get('speaker') == player)
        return count >= 2
    
    def _find_poison_target(self, game_state: dict) -> str:
        """寻找毒药目标 - 增强版
        
        毒药是稀缺资源，需要高置信度才使用。
        优先毒：已确认狼人 > 高度可疑玩家 > 投票行为异常者
        """
        speeches = game_state.get('speeches', [])
        votes = game_state.get('votes', [])
        alive_players = game_state.get('alive_players', [])
        verified_wolves = game_state.get('verified_wolves', [])  # 预言家确认的狼人
        
        # 1. 如果有确认的狼人，直接毒
        for wolf in verified_wolves:
            if wolf in alive_players:
                return wolf
        
        # 2. 综合分析可疑度
        suspicion_scores = {}
        for speech in speeches:
            speaker = speech.get('speaker', '')
            content = speech.get('content', '').lower()
            if speaker not in suspicion_scores:
                suspicion_scores[speaker] = 0
            
            # 狼人典型特征：踩真预言家、保狼队友
            wolf_patterns = [
                ('假预言家', 4), ('悍跳', 4), ('狼预言家', 4),
                ('fake seer', 4), ('wolf seer', 4),
                ('不是真的预言家', 3), ('他在撒谎', 2),
            ]
            for pattern, score in wolf_patterns:
                if pattern in content:
                    suspicion_scores[speaker] += score
            
            # 发言与投票矛盾（先说某人可疑但不投）
            # 修复：votes可能是字符串列表或字典列表，需要兼容处理
            if ('可疑' in content or '是狼' in content) and votes:
                # 检查votes中的元素类型
                if votes and isinstance(votes[0], dict):
                    if speaker in [v.get('voter') for v in votes]:
                        voted_target = next((v.get('target') for v in votes if v.get('voter') == speaker), None)
                        mentioned = re.findall(r'Player\d+', content, re.IGNORECASE)
                        if mentioned and voted_target and voted_target not in mentioned:
                            suspicion_scores[speaker] += 3  # 言行不一
            
            # 逻辑混乱/自相矛盾
            if ('是狼' in content and '是好人' in content) or ('投' in content and '不投' in content):
                suspicion_scores[speaker] += 3
            
            # 过度洗白某人
            if '肯定不是狼' in content or 'definitely not wolf' in content:
                suspicion_scores[speaker] += 2
        
        # 3. 分析投票行为异常
        vote_analysis = self._analyze_vote_pattern(votes, alive_players)
        for player, anomaly_score in vote_analysis.items():
            if player in suspicion_scores:
                suspicion_scores[player] += anomaly_score
            else:
                suspicion_scores[player] = anomaly_score
        
        # 返回可疑度最高且存活的玩家（阈值提高到5，确保高置信度）
        for player, score in sorted(suspicion_scores.items(), key=lambda x: x[1], reverse=True):
            if player in alive_players and player != self.agent_name and score >= 5:
                return player
        return ""
    
    def _analyze_vote_pattern(self, votes: list, alive_players: list) -> dict:
        """分析投票模式异常"""
        anomaly_scores = {}
        
        # 修复：检查votes是否为空或元素不是字典
        if not votes or not isinstance(votes[0], dict):
            return anomaly_scores
        
        # 统计每个玩家的投票目标
        vote_targets = {}
        for vote in votes:
            voter = vote.get('voter', '')
            target = vote.get('target', '')
            if voter not in vote_targets:
                vote_targets[voter] = []
            vote_targets[voter].append(target)
        
        # 检测抱团投票（多人投同一目标）
        target_voters = {}
        for voter, targets in vote_targets.items():
            for target in targets:
                if target not in target_voters:
                    target_voters[target] = []
                target_voters[target].append(voter)
        
        # 如果3人及以上投同一人，这些投票者可能是狼人抱团
        for target, voters in target_voters.items():
            if len(voters) >= 3:
                for voter in voters:
                    anomaly_scores[voter] = anomaly_scores.get(voter, 0) + 2
        
        return anomaly_scores
    
    def hunter_shoot_decision(self, game_state: dict) -> dict:
        """猎人开枪决策 - 增强版
        
        猎人开枪是关键技能，需要最大化收益：
        1. 有确认狼人必须带走
        2. 无确认时跟随场上主流判断
        3. 完全无信息时才放弃开枪
        """
        alive_players = game_state.get('alive_players', [])
        suspected_wolves = game_state.get('suspected_wolves', [])
        verified_wolves = game_state.get('verified_wolves', [])  # 预言家确认的
        votes = game_state.get('votes', [])
        speeches = game_state.get('speeches', [])
        
        target = None
        reasoning = ""
        
        # 1. 最高优先级：确认的狼人
        for wolf in verified_wolves:
            if wolf in alive_players:
                target = wolf
                reasoning = f"带走已确认的狼人{wolf}"
                break
        
        # 2. 次优先级：高度怀疑的狼人
        if not target and suspected_wolves:
            for wolf in suspected_wolves:
                if wolf in alive_players:
                    target = wolf
                    reasoning = f"带走高度怀疑的狼人{wolf}"
                    break
        
        # 3. 跟随主流票：分析投票找出被多人投的玩家
        if not target and votes:
            vote_counts = {}
            # 修复：检查votes元素是否为字典
            if isinstance(votes[0], dict):
                for vote in votes:
                    voted = vote.get('target', '')
                    if voted and voted in alive_players:
                        vote_counts[voted] = vote_counts.get(voted, 0) + 1
            
            if vote_counts:
                # 找出得票最多的玩家
                sorted_votes = sorted(vote_counts.items(), key=lambda x: x[1], reverse=True)
                top_voted, top_count = sorted_votes[0]
                # 至少2票才跟随
                if top_count >= 2:
                    target = top_voted
                    reasoning = f"跟随主流判断带走{top_voted}（{top_count}票）"
        
        # 4. 分析发言找出最可疑的人
        if not target:
            suspicious = self._identify_suspicious_players(speeches)
            for player in suspicious:
                if player in alive_players and player != self.agent_name:
                    target = player
                    reasoning = f"带走发言可疑的{player}"
                    break
        
        # 5. 只有完全无信息时才放弃开枪
        should_shoot = target is not None
        if not should_shoot:
            reasoning = "完全无法判断目标，保留不开枪"
        
        return {
            'should_shoot': should_shoot,
            'target': target,
            'reasoning': reasoning
        }
    
    def day_phase_decision(self, game_state: dict) -> dict:
        """白天阶段决策"""
        # print(f"\n☀️ [{self.agent_name}] ===== 白天阶段决策开始 =====")
        role = game_state.get('role')
        alive_players = game_state.get('alive_players', [])
        # print(f"   当前角色: {role}")
        # print(f"   存活玩家: {alive_players}")
        
        # 生成发言建议
        speech_suggestion = self.generate_speech(game_state)
        # print(f"   发言建议: {speech_suggestion[:100] if speech_suggestion else 'None'}...")
        
        # 生成投票建议
        vote_suggestion = self.make_vote_decision(game_state)
        # print(f"   投票建议: {vote_suggestion}")
        
        # print(f"☀️ [{self.agent_name}] ===== 白天阶段决策结束 =====\n")
        return {
            'speech': speech_suggestion,
            'vote': vote_suggestion
        }
    
    def generate_speech(self, game_state: dict) -> str:
        """生成发言建议 - 增强版"""
        role = game_state.get('role')
        night_results = game_state.get('night_results', [])
        speeches = game_state.get('speeches', [])
        alive_players = game_state.get('alive_players', [])
        dead_players = game_state.get('dead_players', [])
        day_count = game_state.get('day_count', 1)
        
        suggestions = []
        
        # 根据角色生成不同的发言策略
        if role == 'seer':
            check_results = game_state.get('check_results', [])
            if game_state.get('should_reveal', False) or len(check_results) >= 2:
                suggestions.append("建议公开身份并按时间顺序报验人结果")
                if check_results:
                    suggestions.append(f"验人记录：{check_results}")
            else:
                suggestions.append("建议隐藏身份，以普通村民视角分析")
                suggestions.append("可适度提出怀疑但不暴露验人信息来源")
        
        elif role == 'werewolf':
            suggestions.append("保持低调，跟随主流观点")
            suggestions.append("适当分析局势但避免过度引导")
            suggestions.append("不要急于攻击真正的预言家")
            # 分析当前被怀疑的玩家
            suspicious = self._identify_suspicious_players(speeches)
            if suspicious:
                suggestions.append(f"可考虑跟投: {suspicious[0]}")
        
        elif role == 'witch':
            suggestions.append("以村民视角发言，不透露用药信息")
            if night_results:
                suggestions.append("可分析夜晚死亡情况但不暴露自己知道详情")
        
        elif role == 'hunter':
            suggestions.append("保持中度活跃，建立可信度")
            suggestions.append("收集信息为可能的开枪做准备")
            suggestions.append("不要暗示自己有特殊能力")
        
        else:  # 村民
            suggestions.append("积极分析发言，寻找逻辑漏洞")
            suggestions.append("关注投票行为与发言的一致性")
            if dead_players:
                suggestions.append(f"可分析死者{dead_players[-1]}的死亡原因")
        
        # 通用建议
        if day_count == 1:
            suggestions.append("第一天信息少，建议谨慎发言")
        
        return " | ".join(suggestions)
    
    def make_vote_decision(self, game_state: dict) -> str:
        """投票决策"""
        speeches = game_state.get('speeches', [])
        alive_players = game_state.get('alive_players', [])
        role = game_state.get('role')
        
        # 分析发言找出可疑玩家
        suspicious = self._identify_suspicious_players(speeches)
        # 对手行为画像辅助：优先选择低信任/高激进/高欺骗信号的可疑者
        if suspicious:
            memory = game_state.get('memory')
            if memory and hasattr(memory, 'get_opponent_profile'):
                ranked = []
                for p in suspicious:
                    prof = memory.get_opponent_profile(p)
                    score = 0.6 * (1.0 - prof.get('trust_score', 0.5)) 
                    score += 0.2 * prof.get('aggression_level', 0.5)
                    deceps = prof.get('deception_signals', 0)
                    score += 0.2 * min(1.0, deceps / 3.0)
                    ranked.append((p, score))
                ranked.sort(key=lambda x: x[1], reverse=True)
                return ranked[0][0]
            return suspicious[0]
        elif alive_players:
            memory = game_state.get('memory')
            if memory and hasattr(memory, 'get_opponent_profile'):
                ranked = []
                for p in alive_players:
                    prof = memory.get_opponent_profile(p)
                    score = (1.0 - prof.get('trust_score', 0.5)) + 0.2 * prof.get('aggression_level', 0.5)
                    ranked.append((p, score))
                ranked.sort(key=lambda x: x[1], reverse=True)
                return ranked[0][0]
            return alive_players[0]
        return ""
    
    def _identify_seer_from_speeches(self, game_state: dict) -> str:
        """从发言中识别可能的预言家 - 增强版
        
        预言家特征：
        1. 声称验过某人且给出明确结果
        2. 有验人日志或时间线
        3. 发言中包含验人相关关键词
        4. 对某些玩家有确定性判断
        """
        speeches = game_state.get('speeches', [])
        alive_players = game_state.get('alive_players', [])
        
        seer_scores = {}
        
        for speech in speeches:
            speaker = speech.get('speaker', '')
            content = speech.get('content', '').lower()
            
            if speaker not in seer_scores:
                seer_scores[speaker] = 0
            
            # 直接跳预言家身份
            if '我是预言家' in content or 'i am seer' in content or 'i am the seer' in content:
                seer_scores[speaker] += 10
            
            # 验人相关关键词
            seer_keywords = [
                ('验了', 3), ('查了', 3), ('验过', 3), ('查过', 3),
                ('是狼人', 4), ('是好人', 4), ('是村民', 3),
                ('checked', 3), ('verified', 3),
                ('werewolf', 2), ('villager', 2),
                ('n1验', 5), ('n2验', 5), ('第一晚验', 5), ('第二晚验', 5),
            ]
            for keyword, score in seer_keywords:
                if keyword in content:
                    seer_scores[speaker] += score
            
            # 有验人日志格式
            if re.search(r'n\d+.*验.*player\d+', content) or re.search(r'第.晚.*验', content):
                seer_scores[speaker] += 5
            
            # 确定性判断（预言家通常更确定）
            certainty_keywords = ['确定', '肯定', '一定', 'certain', 'definitely', 'sure']
            for keyword in certainty_keywords:
                if keyword in content:
                    seer_scores[speaker] += 1
        
        # 返回得分最高的存活玩家
        if seer_scores:
            sorted_suspects = sorted(seer_scores.items(), key=lambda x: x[1], reverse=True)
            for player, score in sorted_suspects:
                if player in alive_players and score >= 5:
                    return player
        
        return ""
    
    def _identify_suspicious_players(self, speeches: list) -> list:
        """识别可疑玩家（可能是狼人）- 增强版
        
        狼人特征：
        1. 发言模糊、回避关键问题
        2. 逻辑前后矛盾
        3. 过度攻击真正有逻辑的玩家
        4. 投票与发言不一致
        5. 关键时刻沉默或转移话题
        """
        suspicion_scores = {}
        
        for speech in speeches:
            speaker = speech.get('speaker', '')
            content = speech.get('content', '').lower()
            
            if speaker not in suspicion_scores:
                suspicion_scores[speaker] = 0
            
            # 模糊发言特征
            vague_keywords = [
                ('可能', 1), ('也许', 1), ('不确定', 1), ('不好说', 2),
                ('maybe', 1), ('perhaps', 1), ('not sure', 1),
                ('我觉得吧', 2), ('感觉', 1),
            ]
            for keyword, score in vague_keywords:
                if keyword in content:
                    suspicion_scores[speaker] += score
            
            # 转移话题/回避特征
            evasion_keywords = [
                ('先不说', 2), ('再看看', 1), ('不着急', 2),
                ('let\'s wait', 1), ('hold on', 1),
            ]
            for keyword, score in evasion_keywords:
                if keyword in content:
                    suspicion_scores[speaker] += score
            
            # 过度攻击他人（可能是狼人带节奏）
            attack_keywords = [
                ('肯定是狼', 2), ('一定是狼', 2), ('必须投', 2),
                ('definitely wolf', 2), ('must vote', 2),
            ]
            attack_count = 0
            for keyword, score in attack_keywords:
                if keyword in content:
                    attack_count += 1
                    suspicion_scores[speaker] += score
            
            # 过度攻击多人更可疑
            if attack_count >= 2:
                suspicion_scores[speaker] += 3
            
            # 自我辩护过多
            defense_keywords = [
                ('我不是狼', 2), ('相信我', 1), ('我是好人', 1),
                ('i am not wolf', 2), ('trust me', 1), ('i am villager', 1),
            ]
            for keyword, score in defense_keywords:
                if keyword in content:
                    suspicion_scores[speaker] += score
            
            # 逻辑矛盾检测（简化版：同时出现对立词）
            if ('是狼' in content and '是好人' in content) or \
               ('投' in content and '不投' in content):
                suspicion_scores[speaker] += 3
        
        # 按可疑度排序返回
        sorted_suspects = sorted(suspicion_scores.items(), key=lambda x: x[1], reverse=True)
        return [player for player, score in sorted_suspects if score >= 3]


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
        """计算不同目标的生存概率 - 增强版
        
        综合考虑：
        1. 是否是关键角色（容易被狼人盯上）
        2. 发言活跃度（沉默玩家相对安全）
        3. 投票趋势（被多人投票=危险）
        4. 历史被杀规律（首夜常杀活跃玩家）
        5. 女巫保护可能性
        
        Args:
            targets: 目标玩家列表
            game_state: 游戏状态
            
        Returns:
            {player_name: survival_probability}
        """
        probabilities = {}
        votes = game_state.get('votes', [])
        night_count = game_state.get('night_count', 1)
        has_witch_heal = game_state.get('has_witch_heal', True)
        
        # 统计每个玩家被投票次数
        vote_counts = {}
        # 修复：检查votes是否包含字典
        if votes and isinstance(votes[0], dict):
            for vote in votes:
                target = vote.get('target', '')
                if target:
                    vote_counts[target] = vote_counts.get(target, 0) + 1
        
        for target in targets:
            # 基础生存概率
            base_prob = 0.6
            
            # 因素1: 关键角色更危险
            if self._is_likely_key_role(target, game_state):
                base_prob -= 0.25
            
            # 因素2: 沉默玩家相对安全
            if self._is_silent_player(target, game_state):
                base_prob += 0.15
            
            # 因素3: 被投票越多越危险
            target_votes = vote_counts.get(target, 0)
            if target_votes >= 3:
                base_prob -= 0.3
            elif target_votes >= 2:
                base_prob -= 0.15
            elif target_votes >= 1:
                base_prob -= 0.05
            
            # 因素4: 首夜活跃玩家更危险
            if night_count == 1:
                speeches = game_state.get('speeches', [])
                speech_count = sum(1 for s in speeches if s.get('speaker') == target)
                if speech_count >= 3:
                    base_prob -= 0.15  # 首夜话多容易被盯
            
            # 因素5: 女巫可能救关键角色
            if has_witch_heal and self._is_likely_key_role(target, game_state):
                base_prob += 0.1  # 有被救的可能
            
            probabilities[target] = max(0.05, min(0.95, base_prob))
        
        return probabilities
    
    def assess_win_probability(self, current_state: dict) -> float:
        """评估当前胜利概率 - 增强版
        
        综合考虑：
        1. 人数比例
        2. 神职存活情况（预言家/女巫/猎人）
        3. 药水剩余情况
        4. 信息优势
        
        Args:
            current_state: 当前游戏状态
            
        Returns:
            胜利概率 (0-1)
        """
        role = current_state.get('role')
        alive_count = current_state.get('alive_count', 9)
        wolves_alive = current_state.get('wolves_alive', 3)
        
        # 神职存活情况
        seer_alive = current_state.get('seer_alive', True)
        witch_alive = current_state.get('witch_alive', True)
        hunter_alive = current_state.get('hunter_alive', True)
        
        # 药水情况
        has_heal = current_state.get('has_witch_heal', True)
        has_poison = current_state.get('has_witch_poison', True)
        
        if role == 'werewolf':
            # 狼人胜利概率基础值：狼人比例
            base_prob = wolves_alive / alive_count if alive_count > 0 else 0
            
            # 神职对狼人的威胁
            if seer_alive:
                base_prob -= 0.15  # 预言家是最大威胁
            if witch_alive and has_poison:
                base_prob -= 0.1   # 女巫毒药威胁
            if hunter_alive:
                base_prob -= 0.05  # 猎人可以换一个
            
            # 狼人人数优势加成
            if wolves_alive >= alive_count / 2:
                base_prob += 0.2  # 接近胜利
            
            return max(0.0, min(1.0, base_prob))
        
        else:
            # 好人胜利概率基础值
            goods_alive = alive_count - wolves_alive
            base_prob = goods_alive / alive_count if alive_count > 0 else 0
            
            # 神职存活加成
            if seer_alive:
                base_prob += 0.15  # 预言家能验人
            if witch_alive:
                if has_heal:
                    base_prob += 0.08  # 解药能救人
                if has_poison:
                    base_prob += 0.1   # 毒药能杀狼
            if hunter_alive:
                base_prob += 0.05  # 猎人能换狼
            
            # 狼人少于好人一半时优势大
            if wolves_alive <= 1 and goods_alive >= 3:
                base_prob += 0.15
            
            return max(0.0, min(1.0, base_prob))
    
    def _is_likely_key_role(self, player: str, game_state: dict) -> bool:
        """判断是否可能是关键角色（预言家/女巫/猎人）
        
        关键角色特征：
        1. 发言质量高、逻辑清晰
        2. 对局势有独特见解
        3. 发言中包含角色相关关键词
        4. 被其他玩家重点关注
        """
        speeches = game_state.get('speeches', [])
        votes = game_state.get('votes', [])
        
        key_role_score = 0
        speech_count = 0
        
        for speech in speeches:
            if speech.get('speaker') == player:
                speech_count += 1
                content = speech.get('content', '').lower()
                
                # 预言家特征
                seer_keywords = ['验', '查', 'check', 'verify', '是狼', '是好人']
                for kw in seer_keywords:
                    if kw in content:
                        key_role_score += 3
                
                # 女巫特征
                witch_keywords = ['救', '毒', '解药', '毒药', 'save', 'poison']
                for kw in witch_keywords:
                    if kw in content:
                        key_role_score += 2
                
                # 猎人特征
                hunter_keywords = ['带走', '开枪', 'shoot', 'gun']
                for kw in hunter_keywords:
                    if kw in content:
                        key_role_score += 2
                
                # 逻辑性发言（包含分析结构）
                logic_keywords = ['因为', '所以', '分析', '推理', 'because', 'therefore']
                for kw in logic_keywords:
                    if kw in content:
                        key_role_score += 1
        
        # 被投票次数多说明被重点关注
        # 修复：检查votes是否包含字典
        if votes and isinstance(votes[0], dict):
            vote_count = sum(1 for v in votes if v.get('target') == player)
            if vote_count >= 2:
                key_role_score += 2
        
        # 发言活跃度
        if speech_count >= 3:
            key_role_score += 1
        
        return key_role_score >= 5
    
    def _is_silent_player(self, player: str, game_state: dict) -> bool:
        """判断是否是沉默玩家
        
        沉默玩家特征：
        1. 发言次数少
        2. 发言内容短
        3. 不主动参与讨论
        """
        speeches = game_state.get('speeches', [])
        
        speech_count = 0
        total_length = 0
        
        for speech in speeches:
            if speech.get('speaker') == player:
                speech_count += 1
                total_length += len(speech.get('content', ''))
        
        # 发言少于2次或总字数少于50认为是沉默玩家
        if speech_count <= 1:
            return True
        if speech_count <= 2 and total_length < 50:
            return True
        
        return False
    
    def calculate_threat_level(self, player: str, game_state: dict) -> float:
        """计算玩家对当前角色的威胁程度
        
        Args:
            player: 目标玩家
            game_state: 游戏状态
            
        Returns:
            威胁值 (0-1)
        """
        role = game_state.get('role')
        speeches = game_state.get('speeches', [])
        
        threat = 0.0
        
        # 如果是狼人，预言家/女巫是最大威胁
        if role == 'werewolf':
            if self._is_likely_key_role(player, game_state):
                threat += 0.4
            
            # 检查该玩家是否在攻击我方
            for speech in speeches:
                if speech.get('speaker') == player:
                    content = speech.get('content', '').lower()
                    # 检查是否在怀疑自己的队友
                    teammates = game_state.get('teammates', [])
                    for teammate in teammates:
                        if teammate.lower() in content and ('狼' in content or 'wolf' in content):
                            threat += 0.3
        
        # 如果是好人，发言可疑的玩家是威胁
        else:
            speeches = game_state.get('speeches', [])
            
            # 分析该玩家的可疑特征
            for speech in speeches:
                if speech.get('speaker') == player:
                    content = speech.get('content', '').lower()
                    
                    # 狼人典型发言特征
                    wolf_indicators = [
                        ('可能', 0.05), ('也许', 0.05), ('不确定', 0.05),
                        ('先不说', 0.1), ('再看看', 0.08),
                        ('肯定是狼', 0.1), ('必须投', 0.1),  # 过度带节奏
                        ('我不是狼', 0.08), ('相信我', 0.05),  # 自我辩护
                    ]
                    for keyword, score in wolf_indicators:
                        if keyword in content:
                            threat += score
                    
                    # 攻击已确认好人的行为很可疑
                    verified_goods = game_state.get('verified_goods', [])
                    for good_player in verified_goods:
                        if good_player.lower() in content and ('狼' in content or '投' in content):
                            threat += 0.2
                    
                    # 逻辑矛盾
                    if ('是狼' in content and '是好人' in content):
                        threat += 0.15
            
            # 投票行为分析
            votes = game_state.get('votes', [])
            verified_goods = game_state.get('verified_goods', [])
            # 修复：检查votes是否包含字典
            if votes and isinstance(votes[0], dict):
                for vote in votes:
                    if vote.get('voter') == player:
                        # 投票给已确认好人很可疑
                        if vote.get('target') in verified_goods:
                            threat += 0.25
        
        return min(1.0, threat)


class TeamCoordination:
    """团队协作模块 - 管理队友识别和协作"""
    
    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self.known_teammates = []
        self.temporary_allies = []
        self.trust_scores = {}  # {player_name: trust_score}
    
    def identify_teammates(self, observations: list) -> list:
        """识别队友（主要用于狼人）- 增强版
        
        从游戏消息中识别狼人队友，主要通过：
        1. 解析 WEREWOLVES ONLY 消息
        2. 解析夜晚狼人讨论消息
        3. 识别消息中提到的其他狼人名字
        
        Args:
            observations: 观察到的信息列表（可以是字符串或Msg对象）
            
        Returns:
            队友玩家名列表
        """
        teammates = []
        
        for obs in observations:
            # 处理不同类型的观察数据
            if isinstance(obs, str):
                content = obs
            elif hasattr(obs, 'content'):
                content = str(obs.content)
            elif isinstance(obs, dict):
                content = obs.get('content', '')
            else:
                continue
            
            # 识别狼人专属消息
            if 'WEREWOLVES ONLY' in content or '[狼人频道]' in content or 'werewolves' in content.lower():
                # 提取玩家名（格式：Player1, Player2 等）
                player_matches = re.findall(r'Player\d+', content, re.IGNORECASE)
                for player in player_matches:
                    # 标准化玩家名格式
                    player = player.capitalize()
                    if player != self.agent_name and player not in teammates:
                        teammates.append(player)
                
                # 也尝试提取中文玩家名格式（玩家1, 玩家2 等）
                cn_matches = re.findall(r'玩家\d+', content)
                for player in cn_matches:
                    if player != self.agent_name and player not in teammates:
                        teammates.append(player)
            
            # 识别狼人同伴的特殊标记
            if 'your teammates' in content.lower() or '你的队友' in content:
                player_matches = re.findall(r'Player\d+', content, re.IGNORECASE)
                for player in player_matches:
                    player = player.capitalize()
                    if player != self.agent_name and player not in teammates:
                        teammates.append(player)
            
            # 识别角色分配消息中的狼人同伴
            if 'werewolf' in content.lower() and 'are' in content.lower():
                # 例如：The werewolves are Player1, Player2, Player3
                player_matches = re.findall(r'Player\d+', content, re.IGNORECASE)
                for player in player_matches:
                    player = player.capitalize()
                    if player != self.agent_name and player not in teammates:
                        teammates.append(player)
        
        # 更新已知队友列表
        for teammate in teammates:
            if teammate not in self.known_teammates:
                self.known_teammates.append(teammate)
        
        return self.known_teammates
    
    def coordinate_with_team(self, teammates: list, strategy: str, game_state: dict) -> dict:
        """与队友协调策略 - 增强版
        
        根据不同策略类型提供详细的协调建议
        
        Args:
            teammates: 队友列表
            strategy: 策略类型 ('night_kill', 'day_vote', 'defense', 'attack')
            game_state: 游戏状态
            
        Returns:
            协调建议字典
        """
        coordination = {
            'suggested_action': '',
            'target_priority': [],
            'communication_strategy': '',
            'vote_distribution': {},
            'risk_assessment': ''
        }
        
        alive_teammates = [t for t in teammates if t in game_state.get('alive_players', [])]
        
        if strategy == 'night_kill':
            # 协调夜晚击杀目标
            coordination['suggested_action'] = 'discuss_target'
            coordination['target_priority'] = self._prioritize_kill_targets(game_state)
            coordination['communication_strategy'] = '快速达成一致，优先击杀预言家'
            
            # 添加击杀建议
            if coordination['target_priority']:
                coordination['risk_assessment'] = f"建议目标: {coordination['target_priority'][0]}"
            
        elif strategy == 'day_vote':
            # 协调白天投票 - 分散票型避免暴露
            coordination['suggested_action'] = 'coordinate_votes'
            coordination['communication_strategy'] = '分散投票，避免集体暴露'
            
            # 计算票型分配
            alive_players = game_state.get('alive_players', [])
            non_teammates = [p for p in alive_players if p not in teammates and p != self.agent_name]
            
            if non_teammates and alive_teammates:
                # 将票分散到不同目标
                for i, teammate in enumerate(alive_teammates):
                    target_idx = i % len(non_teammates)
                    coordination['vote_distribution'][teammate] = non_teammates[target_idx]
            
            coordination['risk_assessment'] = '注意不要同时攻击同一个目标'
            
        elif strategy == 'defense':
            # 防守策略 - 当队友被怀疑时
            coordination['suggested_action'] = 'provide_cover'
            coordination['communication_strategy'] = '适度为队友辩护，但不要过度'
            coordination['risk_assessment'] = '过度防守会暴露阵营关系'
            
        elif strategy == 'attack':
            # 进攻策略 - 主动引导投票
            coordination['suggested_action'] = 'lead_vote'
            target = self._find_attack_target(game_state)
            if target:
                coordination['target_priority'] = [target]
            coordination['communication_strategy'] = '构建合理逻辑引导投票'
        
        return coordination
    
    def _find_attack_target(self, game_state: dict) -> str:
        """寻找适合攻击的目标"""
        alive_players = game_state.get('alive_players', [])
        speeches = game_state.get('speeches', [])
        
        # 找发言少或逻辑弱的好人作为攻击目标
        for player in alive_players:
            if player not in self.known_teammates and player != self.agent_name:
                return player
        return ""
    
    def manage_alliances(self, temporary_allies: list, game_state: dict) -> dict:
        """管理临时联盟 - 增强版
        
        分析临时盟友的可信度并给出维护建议
        
        Args:
            temporary_allies: 临时盟友列表
            game_state: 游戏状态
            
        Returns:
            联盟管理建议
        """
        self.temporary_allies = temporary_allies
        speeches = game_state.get('speeches', [])
        votes = game_state.get('votes', [])
        
        # 评估每个盟友的可信度
        for ally in temporary_allies:
            if ally not in self.trust_scores:
                self.trust_scores[ally] = 0.5
            
            # 根据发言分析信任度
            ally_speeches = [s for s in speeches if s.get('speaker') == ally]
            for speech in ally_speeches:
                content = speech.get('content', '').lower()
                
                # 如果盟友帮我说话，增加信任
                if self.agent_name.lower() in content and ('好人' in content or 'villager' in content):
                    self.trust_scores[ally] = min(1.0, self.trust_scores[ally] + 0.15)
                
                # 如果盟友攻击我，降低信任
                if self.agent_name.lower() in content and ('狼' in content or 'wolf' in content):
                    self.trust_scores[ally] = max(0.0, self.trust_scores[ally] - 0.2)
            
            # 根据投票分析信任度
            # 修复：检查votes是否包含字典
            if votes and isinstance(votes[0], dict):
                for vote in votes:
                    if vote.get('voter') == ally:
                        # 如果投我，大幅降低信任
                        if vote.get('target') == self.agent_name:
                            self.trust_scores[ally] = max(0.0, self.trust_scores[ally] - 0.3)
                        # 如果投了我推荐的人，增加信任
                        # (这里需要更复杂的逻辑来追踪推荐)
        
        # 分类盟友
        maintain = [a for a in temporary_allies if self.trust_scores.get(a, 0) > 0.6]
        suspicious = [a for a in temporary_allies if self.trust_scores.get(a, 0) < 0.4]
        neutral = [a for a in temporary_allies if 0.4 <= self.trust_scores.get(a, 0) <= 0.6]
        
        return {
            'maintain_alliances': maintain,
            'suspicious_allies': suspicious,
            'neutral_allies': neutral,
            'strategy': 'maintain' if len(maintain) >= 2 else ('rebuild' if len(suspicious) > len(maintain) else 'expand'),
            'trust_scores': {a: self.trust_scores.get(a, 0.5) for a in temporary_allies}
        }
    
    def update_trust_score(self, player: str, action: str, outcome: str):
        """更新对某玩家的信任度 - 增强版
        
        Args:
            player: 玩家名字
            action: 该玩家的行动类型
            outcome: 行动结果 ('positive', 'negative', 'neutral')
        """
        if player not in self.trust_scores:
            self.trust_scores[player] = 0.5
        
        # 根据不同行动类型调整信任度幅度
        action_weights = {
            'vote_for_me': -0.3,
            'vote_against_wolf': 0.2,
            'defend_me': 0.25,
            'attack_me': -0.25,
            'share_info': 0.1,
            'suspicious_speech': -0.15,
            'logical_speech': 0.1,
        }
        
        # 获取行动对应的权重
        weight = action_weights.get(action, 0)
        
        # 根据结果调整
        if outcome == 'positive':
            adjustment = abs(weight) if weight >= 0 else weight * 0.5
        elif outcome == 'negative':
            adjustment = -abs(weight) if weight <= 0 else weight * 0.5
        else:
            adjustment = weight
        
        self.trust_scores[player] = max(0.0, min(1.0, self.trust_scores[player] + adjustment))
    
    def _prioritize_kill_targets(self, game_state: dict) -> list:
        """为狼人优先排序击杀目标 - 增强版
        
        优先级：预言家 > 女巫 > 猎人 > 活跃村民 > 沉默村民
        """
        alive_players = game_state.get('alive_players', [])
        speeches = game_state.get('speeches', [])
        
        # 计算每个玩家的击杀优先级得分
        priority_scores = {}
        
        for player in alive_players:
            if player == self.agent_name or player in self.known_teammates:
                continue
            
            score = 0
            player_speeches = [s for s in speeches if s.get('speaker') == player]
            
            for speech in player_speeches:
                content = speech.get('content', '').lower()
                
                # 预言家特征 - 最高优先级
                if '验' in content or 'check' in content or '是狼' in content:
                    score += 10
                if '我是预言家' in content or 'i am seer' in content:
                    score += 15
                
                # 女巫特征
                if '救' in content or '毒' in content or 'poison' in content:
                    score += 7
                
                # 猎人特征
                if '带走' in content or 'shoot' in content:
                    score += 5
                
                # 逻辑清晰的玩家（威胁较大）
                if '因为' in content or '所以' in content:
                    score += 2
            
            # 发言活跃度
            score += len(player_speeches) * 0.5
            
            priority_scores[player] = score
        
        # 按优先级排序
        sorted_targets = sorted(priority_scores.items(), key=lambda x: x[1], reverse=True)
        return [player for player, score in sorted_targets]


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
            'night_count': 1,     # 新增：夜晚计数
            'day_count': 0,       # 新增：白天计数
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

    def get_opponent_profile(self, player_name: str) -> Dict[str, Any]:
        """Return opponent profile snapshot for a player.

        Args:
            player_name (`str`):
                Target player.

        Returns:
            `dict`:
                Keys include `speech_count`, `avg_speech_len`, `aggression_level`,
                `deception_signals`, `vote_early_ratio`, `vote_focus_index`, `trust_score`.
        """
        prof = self.opponent_models.get(player_name)
        if not prof:
            return {
                'speech_count': 0,
                'avg_speech_len': 0.0,
                'aggression_level': 0.5,
                'deception_signals': 0,
                'vote_early_ratio': 0.0,
                'vote_focus_index': 0.0,
                'trust_score': 0.5,
            }
        return {
            'speech_count': prof.get('speech_count', 0),
            'avg_speech_len': prof.get('avg_speech_len', 0.0),
            'aggression_level': prof.get('aggression_level', 0.5),
            'deception_signals': prof.get('deception_detected', 0),
            'vote_early_ratio': prof.get('vote_early_ratio', 0.0),
            'vote_focus_index': prof.get('vote_focus_index', 0.0),
            'trust_score': prof.get('trust_score', 0.5),
        }

    def update_opponents_live(self, game_state: dict) -> None:
        """Update opponent models with current speeches and votes.

        Args:
            game_state (`dict`):
                Must contain `speeches` (list[{speaker|player, content}]) and
                `votes` (list[{voter, target, timing?}]).
        """
        speeches = game_state.get('speeches', [])
        votes = game_state.get('votes', [])

        speech_stats: Dict[str, Dict[str, Any]] = {}
        for s in speeches:
            sp = s.get('speaker') or s.get('player')
            if not sp:
                continue
            content = str(s.get('content', ''))
            st = speech_stats.setdefault(sp, {'count': 0, 'total_len': 0, 'aggr_hits': 0, 'decep_hits': 0})
            st['count'] += 1
            st['total_len'] += len(content)
            for kw in ['必须投', '一定是狼', '肯定是狼', 'must vote', 'definitely wolf']:
                if kw in content:
                    st['aggr_hits'] += 1
            if ('是狼' in content and '是好人' in content) or ('我不是狼' in content) or ('相信我' in content):
                st['decep_hits'] += 1

        voter_targets: Dict[str, Dict[str, int]] = {}
        voter_early: Dict[str, int] = {}
        voter_total: Dict[str, int] = {}
        for v in votes:
            voter = v.get('voter')
            target = v.get('target')
            if not voter or not target:
                continue
            voter_total[voter] = voter_total.get(voter, 0) + 1
            targets = voter_targets.setdefault(voter, {})
            targets[target] = targets.get(target, 0) + 1
            timing = v.get('timing', 'middle')
            if timing == 'early':
                voter_early[voter] = voter_early.get(voter, 0) + 1

        players = set(list(speech_stats.keys()) + list(voter_total.keys()))
        for p in players:
            prof = self.opponent_models.setdefault(p, {
                'games_met': 0,
                'speech_style': 'neutral',
                'voting_pattern': 'independent',
                'role_history': [],
                'trust_score': 0.5,
                'aggression_level': 0.5,
                'deception_detected': 0,
            })

            st = speech_stats.get(p, {'count': 0, 'total_len': 0, 'aggr_hits': 0, 'decep_hits': 0})
            vt = voter_targets.get(p, {})
            total_votes = voter_total.get(p, 0)
            early_votes = voter_early.get(p, 0)

            avg_len = (st['total_len'] / st['count']) if st['count'] else 0.0
            aggr_level = min(1.0, 0.3 + 0.1 * st['aggr_hits'] + 0.02 * avg_len)
            decep = st['decep_hits']
            vote_early_ratio = (early_votes / total_votes) if total_votes else 0.0
            focus = 0.0
            if total_votes:
                for c in vt.values():
                    r = c / total_votes
                    focus += r * r

            prof.update({
                'speech_count': st['count'],
                'avg_speech_len': avg_len,
                'aggression_level': aggr_level,
                'deception_detected': prof.get('deception_detected', 0) + decep,
                'vote_early_ratio': vote_early_ratio,
                'vote_focus_index': focus,
            })

            trust = prof.get('trust_score', 0.5)
            trust += 0.05 * (1.0 - aggr_level)
            trust -= 0.08 * decep
            trust -= 0.04 * focus
            prof['trust_score'] = max(0.0, min(1.0, trust))
    
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

        # 🔴 新增：初始化存活玩家列表（支持中英文）
        # 只在游戏开始时初始化（避免讨论阶段的"现在存活玩家有"消息覆盖）
        if ("参与玩家包括" in content or "the players are:" in content.lower()) and ("新的一局" in content or "new game" in content.lower()):
            # 提取玩家名单
            players = re.findall(r'Player\d+', content)
            if players:
                self.current_game['alive_players'] = players
                dbg(f"✅ [记忆] 初始化存活玩家列表: {players}")
        # 🔴 新增：更新存活玩家列表（从"现在存活玩家有"消息中同步）
        elif "现在存活玩家有" in content or "now the alive players are" in content.lower():
            # 提取当前存活玩家列表
            current_alive = re.findall(r'Player\d+', content)
            if current_alive:
                # 同步更新：找出已死亡但未记录的玩家
                for player in self.current_game['alive_players']:
                    if player not in current_alive and player not in self.current_game['dead_players']:
                        self.current_game['dead_players'].append(player)
                        dbg(f"[记忆] 同步死亡信息: {player} 已死亡")
                # 更新存活列表
                self.current_game['alive_players'] = current_alive
                dbg(f"[记忆] 同步存活玩家列表: {current_alive}")

        # 🔴 合并：更新存活玩家列表和死亡信息（支持中英文）
        if "has been eliminated" in content or "died" in content or "已被淘汰" in content or "死亡" in content or "出局" in content:
            player_pattern = r'Player\d+'
            players = re.findall(player_pattern, content)
            for player in players:
                # 只在玩家第一次死亡时处理和打印
                if player in self.current_game['alive_players']:
                    self.current_game['alive_players'].remove(player)
                    if player not in self.current_game['dead_players']:
                        self.current_game['dead_players'].append(player)
                        dbg(f"[记忆] {player} 已死亡，剩余: {self.current_game['alive_players']}")
        
        # 🔴 统计天数和夜晚（中英文支持）
        # 检测夜晚开始（支持中英文关键词）
        if any(kw in content for kw in ["Night falls", "夜幕降临", "天黑请闭眼", "Night", "夜晚来临","天黑了"]):
            current_night = self.current_game.get('night_count', 0)
            # 避免重复计数同一个夜晚
            if "Night falls" in content or "夜幕降临" in content or "天黑请闭眼" in content:
                self.current_game['night_count'] = current_night + 1
                # print(f"🌙 [记忆] 进入第 {self.current_game['night_count']} 晚")
        
        # 检测白天开始（支持中英文关键词）
        if any(kw in content for kw in ["Day breaks", "天亮了", "天亮请睁眼", "白天到来", "discussion phase", "讨论阶段"]):
            current_day = self.current_game.get('day_count', 0)
            # 避免重复计数同一天
            if "Day breaks" in content or "天亮了" in content or "天亮请睁眼" in content:
                self.current_game['day_count'] = current_day + 1
                # print(f"☀️ [记忆] 进入第 {self.current_game['day_count']} 天")
        
        # 提取投票信息
        if "voting result" in content.lower() or "vote" in content.lower() or "投票" in content:
            self.current_game['votes'].append(content)
        
        # 提取夜晚结果
        if "Last night" in content or "昨晚" in content or "night result" in content.lower():
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
        dbg(f"[学习] 策略权重已更新 {'(胜利)' if won else '(失败)'}")
    
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
        old_total_games = self.total_games
        self.total_games += 1
        dbg(
            f"[PlayerMemory] record_game_result: "
            f"{old_total_games} -> {self.total_games}, won={won}",
        )
        if won:
            self.wins += 1
            if self.current_game['role']:
                self.role_stats[self.current_game['role']]['won'] += 1
        else:
            self.losses += 1
        
        # 🔴 保存游戏结束时的玩家列表，供反思阶段使用
        final_alive_players = self.current_game.get('alive_players', []).copy()
        final_dead_players = self.current_game.get('dead_players', []).copy()
        
        # 存储完整游戏经验
        game_data = {
            'role': self.current_game['role'],
            'won': won,
            'key_decisions': self.current_game.get('key_decisions', []),
            'opponent_behaviors': {},  # 需要从 speeches 中提取
            'game_flow': self.current_game.get('night_results', []),
            'final_alive_count': len(final_alive_players),
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
        
        # 🔴 重置当前游戏，但保留玩家列表供反思阶段使用
        self.current_game = {
            'role': None,
            'alive_players': final_alive_players,  # 保留最终玩家列表
            'dead_players': final_dead_players,    # 保留最终死亡列表
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
        
        # 🔴 修复：重新初始化 current_game（checkpoint 不保存当前游戏状态）
        self.current_game = {
            'role': None,
            'alive_players': [],
            'dead_players': [],
            'night_results': [],
            'speeches': [],
            'votes': [],
            'key_decisions': [],
            'game_round': 0,
            'night_count': 1,
            'day_count': 0,
        }
        
        # 重置实时学习状态
        self.current_round_insights = {}
        self.opponent_live_tracking = {}
        
        dbg(f"[记忆恢复] 已加载 {self.total_games} 局游戏经验")


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
        self.team_coordination = TeamCoordination(name)  # 先初始化
        self.decision_maker = DecisionMaker(name, self.team_coordination)  # 传入team_coordination
        self.risk_assessment = RiskAssessment()
        # 新增：角色适应模块（RoleAdapter）
        self.role_adapter = RoleAdapter()
        
        # 调用父类初始化
        # 🔴 关键修复：禁用 ReActAgent 的工具系统，因为我们使用结构化输出
        # 注意：sys_prompt 传入空字符串，实际的动态 prompt 通过 @property sys_prompt 提供
        super().__init__(
            name=name,
            sys_prompt="",  # 🔴 传入空字符串，让 @property sys_prompt 接管
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
                    dbg(f"🔒 [{self.name}] 阻止工具调用进入记忆")
                    return
                # 跳过包含工具错误的消息
                if isinstance(content, str) and ('tool_result' in content or 'generate_response()' in content):
                    dbg(f"🔒 [{self.name}] 阻止工具错误进入记忆")
                    return
            return original_add(msg)
        
        self.memory.add = filtered_add
    
    def _get_model_config(self):
        """获取模型配置 - 支持多种免费模型"""
        # 🆓 从环境变量读取模型选择，默认使用 DeepSeek (你已经有 API Key)
        model_type = os.environ.get("WEREWOLF_MODEL", "dashscope")
        
        if model_type == "ollama":
            # Ollama - 本地运行，完全免费
            from agentscope.model import OllamaModel
            return OllamaModel(
                model_name=os.environ.get("OLLAMA_MODEL", "qwen2:7b"),  # 可选: llama3, mistral, qwen2
                host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
            )
        
        elif model_type == "dashscope":
            # 阿里云 DashScope - 读取环境变量，不再使用硬编码后备
            from agentscope.model import DashScopeChatModel
            api_key = os.environ.get("DASHSCOPE_API_KEY","sk-de0bd3773b344a799f34841e038981ac")
            if not api_key:
                raise RuntimeError("未设置DASHSCOPE_API_KEY。请在环境变量中设置，或者选择其他WEREWOLF_MODEL。")
            return DashScopeChatModel(
                api_key=api_key,
                model_name=os.environ.get("DASHSCOPE_MODEL", "qwen3-max"),  # qwen-turbo 有免费额度
                generate_kwargs={
                    "result_format": "message",  # 使用message格式而不是tools格式
                },
            )
        
        elif model_type == "deepseek":
            # DeepSeek - 读取环境变量，不再使用硬编码后备
            from agentscope.model import OpenAIChatModel
            api_key = os.environ.get("DEEPSEEK_API_KEY")
            if not api_key:
                raise RuntimeError("未设置DEEPSEEK_API_KEY。请在环境变量中设置，或者选择其他WEREWOLF_MODEL。")
            return OpenAIChatModel(
                model_name="deepseek-chat",
                api_key=api_key,
                client_args={
                    "base_url": "https://api.deepseek.com/v1",
                    "max_retries": 2,  # 降低重试次数加快失败恢复
                },
                generate_kwargs={
                    "max_tokens": 256,
                    "temperature": 0.7,
                    "tool_choice": "none",  # 禁用工具调用
                },
            )
        
        elif model_type == "groq":
            # Groq - 读取环境变量，如无则提示
            from agentscope.model import OpenAIChatModel
            api_key = os.environ.get("GROQ_API_KEY")
            if not api_key:
                raise RuntimeError("未设置GROQ_API_KEY。请在环境变量中设置，或者选择其他WEREWOLF_MODEL。")
            return OpenAIChatModel(
                model_name="llama-3.3-70b-versatile",
                api_key=api_key,
                client_args={"base_url": "https://api.groq.com/openai/v1", "max_retries": 2},
                generate_kwargs={
                    "max_tokens": 256,
                    "temperature": 0.7,
                    "tool_choice": "none",  # 禁用工具调用
                },
            )
        
        else:
            raise RuntimeError(f"不支持的WEREWOLF_MODEL '{model_type}'。请将WEREWOLF_MODEL设置为以下之一：ollama, dashscope, deepseek, groq。")
    
    def _get_formatter(self):
        """根据模型类型选择formatter"""
        model_type = os.environ.get("WEREWOLF_MODEL", "dashcope")
        
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
        # 🔴🔴🔴 强制打印：验证 @property 是否被调用
        # if not hasattr(self, '_sys_prompt_call_count'):
        #     self._sys_prompt_call_count = 0
        # self._sys_prompt_call_count += 1
        # print(f"\n{'='*60}")
        # print(f"🚨 [{self.name}] @property sys_prompt 被调用 (第 {self._sys_prompt_call_count} 次)")
        # print(f"   当前角色: {self.current_role}")
        # print(f"{'='*60}\n")
        
        base = self._build_base_prompt()
        role_specific = self._get_role_specific_prompt()
        memory_insights = self.player_memory.get_insights_for_prompt()
        strategy_insights = self._get_strategy_insights()  # 新增：策略建议
        
        full_prompt = f"{base}\n\n{role_specific}\n\n{memory_insights}\n\n{strategy_insights}"
        
        # 打印 prompt 长度统计
        # print(f"📊 [{self.name}] Prompt 组成部分:")
        # print(f"   - Base: {len(base)} 字符")
        # print(f"   - Role Specific: {len(role_specific)} 字符")
        # print(f"   - Memory Insights: {len(memory_insights)} 字符")
        # print(f"   - Strategy Insights: {len(strategy_insights)} 字符")
        # print(f"   - Total: {len(full_prompt)} 字符\n")
        
        # # 🔴 可选：打印完整 prompt 内容（用于调试）
        # if os.environ.get('WEREWOLF_DEBUG_PROMPT') == '1':
        #     print(f"📝 [{self.name}] 完整 System Prompt:")
        #     print("="*80)
        #     print(full_prompt)
        #     print("="*80 + "\n")

        return full_prompt
    
    def _get_strategy_insights(self) -> str:
        """获取策略模块的建议（增强版 - 集成学习模块）"""
        
        if not self.current_role:
            print(f"   ⚠️ 角色未设置，返回空策略")
            return ""
        
        # 构建当前游戏状态
        game_state = self._build_game_state()
        
        insights = "# 策略分析建议\n\n"
        
        # 提取关键状态数据
        alive_count = game_state.get('alive_count', 0)
        current_round = game_state.get('night_count', 0)
        day_count = game_state.get('day_count', 0)
        player_count = game_state.get('player_count', 0)
        alive_players = game_state.get('alive_players', [])
        
        
        
        # ========== 实时学习模块（游戏进行时） ==========
        if alive_count > 0 and current_round > 0:
            self.player_memory.adapt_to_game_flow(current_round, game_state)
            if current_round != getattr(self, '_last_logged_round', -1):
                dbg(f"🧠 [{self.name}] 学习模块: 游戏阶段适应 (轮次:{current_round}, 存活:{alive_count})")
                self._last_logged_round = current_round
        
        # ========== 1. 实时风险评估（总是生成） ==========
        # print(f"\n📊 [{self.name}] ===== 开始风险评估 =====")
        reveal_risk = self.risk_assessment.evaluate_action_risk('reveal_identity', game_state)
        # print(f"   暴露身份风险: {reveal_risk:.2f}")
        aggressive_risk = self.risk_assessment.evaluate_action_risk('aggressive_vote', game_state)
        # print(f"   激进投票风险: {aggressive_risk:.2f}")
        win_prob = self.risk_assessment.assess_win_probability(game_state)
        # print(f"   胜利概率: {win_prob:.1%}")
        # print(f"📊 [{self.name}] ===== 风险评估完成 =====\n")
        
        # 应用实时调整的风险参数
        adjusted_risk = self.player_memory.current_round_insights.get('adjusted_risk', 0.5)
        adjusted_aggression = self.player_memory.current_round_insights.get('adjusted_aggression', 0.5)
        
        insights += f"## 当前局势评估 (第{current_round}晚/第{day_count}天)\n"
        insights += f"- 存活玩家: {alive_count}/{player_count}\n"
        insights += f"- 暴露身份风险: {'🔴高' if reveal_risk > 0.7 else '🟡中' if reveal_risk > 0.4 else '🟢低'} ({reveal_risk:.2f})\n"
        insights += f"- 激进投票风险: {'🔴高' if aggressive_risk > 0.7 else '🟡中' if aggressive_risk > 0.4 else '🟢低'} ({aggressive_risk:.2f})\n"
        insights += f"- 胜利概率: {win_prob:.1%}\n"
        insights += f"- 建议风险容忍度: {adjusted_risk:.2f}\n"
        insights += f"- 建议激进程度: {adjusted_aggression:.2f}\n\n"
        
        # ========== 2. 动态战术建议（基于当前状态） ==========
        insights += f"## 战术建议\n"
        
        # 根据游戏进程给出不同建议
        if current_round == 1:
            insights += "- ⚠️ 第一晚：以观察为主，避免暴露身份\n"
            insights += "- 💡 收集其他玩家的发言习惯和逻辑\n"
        elif current_round == 2:
            insights += "- 📊 第二晚：可以开始初步分析和试探\n"
            insights += "- 🎯 注意谁在主导讨论节奏\n"
        elif alive_count <= player_count * 0.6:
            insights += "- ⚔️ 中后期：局势紧张，需要更明确的立场\n"
            insights += "- 🔍 重点关注投票模式和阵营划分\n"
        
        # 根据角色给出建议
        if self.current_role == 'werewolf':
            insights += f"- 🐺 狼人战术：{'隐藏' if alive_count > player_count * 0.7 else '适度激进'}\n"
        elif self.current_role == 'villager':
            insights += f"- 👤 村民战术：{'谨慎发言' if reveal_risk > 0.6 else '积极推理'}\n"
        elif self.current_role == 'seer':
            insights += f"- 🔮 预言家战术：{'隐藏' if reveal_risk > 0.5 else '可考虑跳出'}\n"
        
        insights += "\n"
        
        # ========== 5. 角色专属策略建议（调用DecisionMaker） ==========
        if alive_count > 0:  # 只在游戏进行中调用
            # print(f"\n🎲 [{self.name}] ===== 调用角色策略模块 =====")
            # print(f"   当前角色: {self.current_role}")
            
            # 调用决策制定模块
            decision_advice = self.decision_maker.night_phase_decision(self.current_role, game_state)
            
            if decision_advice:
                insights += f"## 🎯 {self.current_role.upper()} 专属策略建议\n"
                
                # 根据不同角色展示不同的建议
                if self.current_role == 'werewolf' and 'suggested_targets' in decision_advice:
                    targets = decision_advice['suggested_targets']
                    reasoning = decision_advice.get('reasoning', '')
                    insights += f"- 推荐击杀目标: {', '.join(targets) if targets else '无明确目标'}\n"
                    insights += f"- 决策理由: {reasoning}\n"
                    # print(f"   狼人策略 - 推荐目标: {targets}, 理由: {reasoning}")
                
                elif self.current_role == 'seer' and 'suggested_target' in decision_advice:
                    target = decision_advice['suggested_target']
                    reasoning = decision_advice.get('reasoning', '')
                    insights += f"- 推荐查验目标: {target if target else '无明确目标'}\n"
                    insights += f"- 决策理由: {reasoning}\n"
                    # print(f"   预言家策略 - 推荐查验: {target}, 理由: {reasoning}")
                
                elif self.current_role == 'witch' and 'reasoning' in decision_advice:
                    use_heal = decision_advice.get('use_heal', False)
                    use_poison = decision_advice.get('use_poison', False)
                    poison_target = decision_advice.get('poison_target')
                    reasoning = decision_advice.get('reasoning', '')
                    insights += f"- 解药建议: {'使用' if use_heal else '保留'}\n"
                    insights += f"- 毒药建议: {'使用' if use_poison else '保留'}"
                    if poison_target:
                        insights += f" (目标: {poison_target})"
                    insights += "\n"
                    insights += f"- 决策理由: {reasoning}\n"
                    # print(f"   女巫策略 - 解药:{use_heal}, 毒药:{use_poison}, 理由: {reasoning}")
                
                elif self.current_role == 'hunter' and 'should_shoot' in decision_advice:
                    should_shoot = decision_advice.get('should_shoot', False)
                    target = decision_advice.get('target')
                    reasoning = decision_advice.get('reasoning', '')
                    insights += f"- 开枪建议: {'开枪' if should_shoot else '不开枪'}"
                    if target:
                        insights += f" (目标: {target})"
                    insights += "\n"
                    insights += f"- 决策理由: {reasoning}\n"
                    # print(f"   猎人策略 - 开枪:{should_shoot}, 目标:{target}, 理由: {reasoning}")
                
                insights += "\n"
            
            # print(f"🎲 [{self.name}] ===== 角色策略模块调用完成 =====\n")
        
        # ========== 3. 历史经验参考（仅在有数据时） ==========
        if self.player_memory.total_games >= 3:
            similar_situations = self.player_memory.retrieve_similar_situations(game_state)
            if similar_situations:
                success_count = sum(1 for s in similar_situations if s['outcome'])
                insights += f"## 历史经验参考\n"
                insights += f"- 找到 {len(similar_situations)} 个相似情况\n"
                insights += f"- 历史胜率: {success_count}/{len(similar_situations)} ({success_count/len(similar_situations):.1%})\n"
                if success_count > len(similar_situations) / 2:
                    insights += f"- 💪 建议: 当前情况历史胜率较高，可适度激进\n"
                else:
                    insights += f"- 🛡️ 建议: 当前情况历史胜率较低，需谨慎行动\n"
                insights += "\n"
        
        # ========== 4. 策略优化建议（仅在有历史数据时） ==========
        if self.player_memory.total_games >= 2:
            strategy_rec = self.player_memory.get_strategy_recommendation(game_state)
            if strategy_rec.get('reasoning'):
                insights += f"## 策略推荐 (置信度:{strategy_rec['confidence']:.1%})\n"
                for reason in strategy_rec['reasoning']:
                    insights += f"- {reason}\n"
                insights += f"- 建议激进度: {strategy_rec['suggested_aggression']:.2f}\n"
                insights += f"- 建议风险: {strategy_rec['suggested_risk']:.2f}\n\n"
        


        # print(f"[{self.name}] 策略建议生成完毕: {insights[:100]}...")  # 打印前100字符预览
        # 返回策略建议（如果内容过长则截断以减少token消耗）
        if len(insights) > 2000:
            # 截断过长的内容，保留关键部分
            return insights[:2000] + "\n[...内容过长已截断...]"
        return insights
    
    
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
        dead_players = self.player_memory.current_game.get('dead_players', [])
        
        # 🔴 修复：如果 alive_players 为空，使用默认推断
        if not alive_players:
            # 如果有死亡玩家但没有存活玩家列表，说明数据不完整
            # 这种情况下使用默认的9人局配置
            if dead_players:
                # 从死亡玩家推断可能的所有玩家（假设9人局）
                all_player_nums = set(range(1, 10))  # Player1-Player9
                dead_nums = {int(p.replace('Player', '')) for p in dead_players if 'Player' in p}
                alive_nums = all_player_nums - dead_nums
                alive_players = [f'Player{n}' for n in sorted(alive_nums)]
                dbg(f"🔧 [{self.name}] 从死亡列表推断存活玩家: {alive_players}")
                self.player_memory.current_game['alive_players'] = alive_players
            else:
                # 完全没有数据，可能是游戏刚开始或游戏已结束（反思阶段）
                # 使用 dbg 而不是 print，避免在反思阶段产生干扰性警告
                dbg(f"⚠️ [{self.name}] 警告: 玩家列表未初始化（游戏可能刚开始或已结束）")
        
        # 计算总玩家数
        total_players = len(alive_players) + len(dead_players)
        if total_players == 0:
            total_players = 9  # 默认9人局
        
        return {
            'role': self.current_role,
            'alive_players': alive_players,
            'dead_players': dead_players,
            'alive_count': len(alive_players),
            'player_count': total_players,
            'speeches': self.player_memory.current_game.get('speeches', []),
            'night_results': self.player_memory.current_game.get('night_results', []),
            'votes': self.player_memory.current_game.get('votes', []),
            'wolves_alive': 3,  # 需要从游戏状态中推断
            'night_count': self.player_memory.current_game.get('night_count', 0),  # 使用统计的夜晚数
            'day_count': self.player_memory.current_game.get('day_count', 0)
        }
    
    def _build_base_prompt(self) -> str:
        """构建基础prompt - 精简版以减少token消耗"""
        # 🔴 根据角色动态插入关键指令（放在最前面）
        role_critical_instruction = ""
        if self.current_role == 'werewolf':
            role_critical_instruction = """
🚨🚨🚨 狼人关键规则 - 绝对禁止暴露身份 🚨🚨🚨
如果你是狼人，你必须**永远不要**向其他玩家暴露这一点。

❌ 绝对禁止的发言方式：
- "作为狼人，我..." ← 永远不要说
- "我是狼人，需要..." ← 永远不要说
- "我们狼人应该..." ← 永远不要说
- "我需要伪装成村民..." ← 这暴露了你在伪装
- "我要隐藏狼人身份..." ← 这暴露了你的身份
- 任何提到"狼人"、"伪装"、"隐藏身份"的话 ← 全部禁止

✅ 正确的发言方式：
- 直接以村民的口吻说话："我觉得Player7很可疑"
- 不要提及自己的真实身份
- 不要提及你的推理过程（如"我需要低调"）
- 只说表面的、村民会说的话

记住：你在公开发言时，**只能说村民会说的话**！
其他玩家能看到你说的每一个字！
违反此规则 = 你的阵营立即失败
🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨🚨

"""
        
        return f"""{role_critical_instruction}🚫🚫🚫 严禁使用工具调用格式 🚫🚫🚫
- 绝对不要输出：{{"name": "generate_response", ...}}
- 绝对不要输出：{{"type": "tool_use", ...}}
- 绝对不要使用任何函数调用或工具格式
- 只输出纯文本或标准JSON对象

角色：{self.name} 在9人狼人杀游戏中
目标：帮助你的阵营获胜

响应规则：
1. 对于讨论、反思或一般性讲话：仅输出自然语言（不使用 JSON）
2. 对于结构化操作（当提示中出现“📋 必需的输出格式”时）：
- 遵循该提示部分的具体输出格式说明
- 某些操作需要推理 + JSON，有些操作只需要 JSON
3. 永不使用函数调用或工具使用格式。主持人提示始终具有权威性。
4. 🚫 独立思考：永远不要复制其他玩家的陈述。用自己的话分析。
5. 🚫 不要替代主持人/法官的角色。

胜利条件：狼人阵营在狼人数≥好人数时获胜；好人阵营在所有狼人死亡时获胜。
"""

    def _build_schema_hint(self, model_class) -> str:
            """为当前模型构建人类可读的schema指导。"""
            if not hasattr(model_class, 'model_fields'):
                return ""

            schema_name = getattr(model_class, '__name__', '')
            
            # 🔴 只有特定身份的特定环节需要"推理+JSON"共存
            # 狼人讨论(DiscussionModel)、女巫用药(WitchResurrectModel/WitchPoisonModel)、
            # 猎人开枪(HunterModel)、预言家验人(SeerModel)
            needs_reasoning_with_json = schema_name in {
                'DiscussionModel',      # 狼人讨论投票
                'WitchResurrectModel',  # 女巫救人
                'WitchPoisonModel',     # 女巫毒人
                'SeerModel',            # 预言家验人
                'HunterModel'           # 猎人开枪
            }
            
            lines = ["📋 必需的输出格式："]
            for field_name, field_info in model_class.model_fields.items():
                field_type = self._describe_field_type(field_info.annotation)
                required = "必填" if field_info.is_required() else "可选"
                field_desc = field_info.description or ""
                literal_values = self._extract_literal_values(field_info.annotation)
                if literal_values:
                    allowed_values = ", ".join(literal_values)
                    suffix = f" 允许的值：{allowed_values}"
                else:
                    suffix = ""
                lines.append(
                    f"- {field_name}: {field_type}, {required}. {field_desc}{suffix}".strip()
                )

            schema_rule = self._schema_specific_rule(schema_name)
            if schema_rule:
                lines.append(schema_rule)

            # 🔴 根据模型类型决定输出格式
            if needs_reasoning_with_json:
                lines.append(
                    "\n⚠️ 输出格式：首先用自然语言解释你的推理过程（2-5句话），"
                    "然后在新的一行输出符合上述schema的JSON对象。"
                    "你必须同时提供推理文本和JSON！"
                    "\n📌 JSON格式要求：必须使用标准JSON格式（双引号），例如 {\"key\": \"value\"} 而不是 {'key': 'value'}"
                )
            else:
                lines.append(
                    "\n⚠️ 输出格式：只输出符合上述schema的JSON对象。"
                    "不要在JSON前后添加任何额外文本。"
                    "\n📌 JSON格式要求：必须使用标准JSON格式（双引号），例如 {\"key\": \"value\"} 而不是 {'key': 'value'}"
                )
            
            return "\n".join(lines)

    @staticmethod
    def _describe_field_type(annotation: Any) -> str:
            """将Pydantic字段注解映射为人类可读的标签。"""
            if annotation is bool:
                return "boolean"

            origin = get_origin(annotation)
            if origin is Literal:
                literal_values = ", ".join(str(value) for value in get_args(annotation))
                return f"enum[{literal_values}]"
            if origin in (list, tuple, set):
                return f"{origin.__name__}"
            if origin is dict:
                return "object"
            if origin in (Union, UnionType, Optional):
                args = [arg for arg in get_args(annotation) if arg is not type(None)]
                if len(args) == 1:
                    return f"optional {PlayerAgent._describe_field_type(args[0])}"
                return "union"
            return "string"

    @staticmethod
    def _extract_literal_values(annotation: Any) -> list[str]:
            """返回Literal/Optional[Literal]注解的字面值字符串。"""
            origin = get_origin(annotation)
            if origin is Literal:
                return [str(value) for value in get_args(annotation)]
            if origin in (Union, UnionType, Optional):
                values: list[str] = []
                for arg in get_args(annotation):
                    if arg is type(None):
                        continue
                    values.extend(PlayerAgent._extract_literal_values(arg))
                return values
            return []

    @staticmethod
    def _schema_specific_rule(schema_name: str) -> str:
            """为每种模式类型提供额外的规则提示。"""
            rules = {
                'DiscussionModel': "🔴 狼人讨论阶段 → 只能输出：纯文本讨论 或 {\"reach_agreement\": true}。绝对不要在讨论阶段输出投票！不要输出target、vote或name字段。必须所有人输出{\"reach_agreement\"=\"true\"}，游戏才会进入投票阶段。⚠️ 禁止工具格式：不要使用{\"name\": \"generate_response\", ...}",
                'VoteModel': "🔴 投票阶段 → 只有在讨论阶段达成共识后才能投票。JSON必须是 {\"vote\": \"PlayerX\"}。不要输出target或name字段。⚠️ 禁止工具格式。",
                'WitchResurrectModel': "救人决定 → JSON必须是 {\"resurrect\": true/false}。⚠️ 禁止工具格式。",
                'WitchPoisonModel': "毒人决定 → JSON必须是 {\"poison\": true/false, \"name\": \"PlayerX\" or null}。⚠️ 禁止工具格式。",
                'SeerModel': "预言家行动 → JSON必须是 {\"name\": \"PlayerX\"}。⚠️ 禁止工具格式。",
                'HunterModel': "猎人行动 → JSON必须是 {\"shoot\": true/false, \"name\": \"PlayerX\" or null}。⚠️ 禁止工具格式。",
            }
            return rules.get(schema_name, "")

    def _inject_hint_into_inputs(self, args, kwargs, hint: str) -> None:
            """将schema提示附加到传递给agent的最新主持人消息中。"""
            if not hint:
                return

            seen_ids: set[int] = set()

            def register(target):
                if target is None:
                    return None
                obj_id = id(target)
                if obj_id in seen_ids:
                    return None
                seen_ids.add(obj_id)
                return target

            target = register(kwargs.get('x'))
            if target is not None:
                self._append_hint_to_target(target, hint)

            if args:
                target = register(args[0])
                if target is not None:
                    self._append_hint_to_target(target, hint)

    def _append_hint_to_target(self, target, hint: str) -> None:
            """安全地将提示文本附加到Msg对象或Msg列表中。"""
            if target is None:
                return

            if isinstance(target, Msg):
                content = target.content
                if isinstance(content, list):
                    content = " ".join(str(item) for item in content)
                elif not isinstance(content, str):
                    content = str(content)
                content = content or ""
                if hint in content:
                    return
                target.content = f"{content}\n{hint}" if content else hint
                return

            if isinstance(target, list) and target:
                # 仅标注批次中的最新消息以保持上下文清晰
                self._append_hint_to_target(target[-1], hint)
    
    def _sanitize_message(self, msg: Msg | list[Msg] | None) -> Msg | list[Msg] | None:
        """从传入消息中移除工具调用载荷。
        
        Args:
            msg (`Msg | list[Msg] | None`):
                原始传入的消息。
        
        Returns:
            `Msg | list[Msg] | None`:
                已过滤工具调用的消息。
        """
        if msg is None:
            return None
        
        if isinstance(msg, Msg):
            # 过滤各种工具相关格式
            if isinstance(msg.content, dict):
                if msg.content.get('type') in ('tool_use', 'tool_result', 'tool_call'):
                    msg.content = "[此消息已过滤]"
                elif 'tool_calls' in msg.content or 'tool_call_id' in msg.content:
                    msg.content = "[此消息已过滤]"
            elif isinstance(msg.content, str):
                # 过滤包含工具错误的消息
                if any(keyword in msg.content.lower() for keyword in ['tool_result', 'tool_call', 'arguments validation error']):
                    msg.content = "[此消息已过滤]"
            return msg
        
        if isinstance(msg, list):
            filtered_list = []
            for item in msg:
                if isinstance(item, Msg):
                    # 检查并过滤工具调用消息
                    should_filter = False
                    if isinstance(item.content, dict):
                        if item.content.get('type') in ('tool_use', 'tool_result', 'tool_call'):
                            should_filter = True
                        elif 'tool_calls' in item.content or 'tool_call_id' in item.content:
                            should_filter = True
                    elif isinstance(item.content, str):
                        if any(keyword in item.content.lower() for keyword in ['tool_result', 'tool_call', 'arguments validation error']):
                            should_filter = True
                    
                    if should_filter:
                        # 完全移除这条消息，而不是替换内容
                        dbg(f"🔒 _sanitize_message: 移除工具调用消息")
                        continue
                    
                filtered_list.append(item)
            return filtered_list if filtered_list else msg  # 如果全部被过滤，返回原始消息
        
        return msg
    
    async def _clean_memory_tool_calls(self) -> None:
        """清理记忆中的所有tool_calls消息，防止DashScope API报错。"""
        if not hasattr(self, 'memory') or self.memory is None:
            return
        
        try:
            # 获取当前记忆中的所有消息
            memory_messages = await self.memory.get_memory()
            
            if not memory_messages:
                return
            
            # 过滤掉包含tool_calls的消息
            cleaned_messages = []
            removed_count = 0
            
            for msg in memory_messages:
                should_remove = False
                
                # 检查content
                if hasattr(msg, 'content'):
                    content = msg.content
                    
                    # 检查dict类型的content
                    if isinstance(content, dict):
                        if 'tool_calls' in content or 'tool_call_id' in content:
                            should_remove = True
                        elif content.get('type') in ('tool_use', 'tool_result', 'tool_call'):
                            should_remove = True
                    # 检查字符串类型的content
                    elif isinstance(content, str):
                        if any(kw in content.lower() for kw in ['tool_call', 'tool_result', 'tool_use', 'arguments validation error']):
                            should_remove = True
                    # 检查列表类型的content
                    elif isinstance(content, list):
                        for item in content:
                            if isinstance(item, dict) and ('tool_calls' in item or item.get('type') in ('tool_use', 'tool_result', 'tool_call')):
                                should_remove = True
                                break
                
                # 检查role
                if hasattr(msg, 'role') and msg.role in ('tool', 'function'):
                    should_remove = True
                
                if should_remove:
                    removed_count += 1
                else:
                    cleaned_messages.append(msg)
            
            # 如果有消息被移除，更新记忆
            if removed_count > 0:
                # 清空记忆并重新添加清理后的消息
                await self.memory.clear()
                for msg in cleaned_messages:
                    await self.memory.add(msg)
                dbg(f"🧹 [{self.name}] 已从记忆中移除 {removed_count} 条tool_calls消息")
        
        except Exception as e:
            # 清理失败不应该阻止游戏继续
            dbg(f"⚠️ [{self.name}] 清理记忆时出错: {e}")
    
    def _extract_input_content(self, args, kwargs) -> str:
            """从args或kwargs中提取输入消息的内容"""
            # 尝试从 kwargs['x'] 获取
            if 'x' in kwargs:
                x = kwargs['x']
                if isinstance(x, Msg):
                    content = x.content
                    if isinstance(content, list):
                        return " ".join(str(item) for item in content)
                    return str(content)
                elif isinstance(x, list) and x:
                    # 取最后一条消息
                    last_msg = x[-1]
                    if isinstance(last_msg, Msg):
                        content = last_msg.content
                        if isinstance(content, list):
                            return " ".join(str(item) for item in content)
                        return str(content)
            
            # 尝试从 args 获取
            for arg in args:
                if isinstance(arg, Msg):
                    content = arg.content
                    if isinstance(content, list):
                        return " ".join(str(item) for item in content)
                    return str(content)
                elif isinstance(arg, list):
                    for item in arg:
                        if isinstance(item, Msg):
                            content = item.content
                            if isinstance(content, list):
                                return " ".join(str(item2) for item2 in content)
                            return str(content)
            
            return ""
    
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
        
        # 🔴 关键修复：过滤掉工具调用消息和纯JSON消息，防止后续玩家模仿
        filtered_messages = []
        for message in messages:
            content = message.content
            
            # 检测并跳过工具调用消息
            is_tool_call = False
            if isinstance(content, dict):
                # 过滤所有工具相关的dict消息
                if content.get('type') in ('tool_use', 'tool_result', 'tool_call'):
                    is_tool_call = True
                    dbg(f"🔒 [{self.name}] 过滤掉工具调用消息")
                elif 'name' in content and content.get('name') == 'generate_response':
                    is_tool_call = True
                    dbg(f"🔒 [{self.name}] 过滤掉generate_response消息")
                elif 'tool_calls' in content or 'tool_call_id' in content:
                    is_tool_call = True
                    dbg(f"🔒 [{self.name}] 过滤掉含tool_calls的消息")
            elif isinstance(content, list):
                # 检查列表中是否包含工具调用
                for item in content:
                    if isinstance(item, dict) and item.get('type') in ('tool_use', 'tool_result', 'tool_call'):
                        is_tool_call = True
                        dbg(f"🔒 [{self.name}] 过滤掉列表中的工具调用消息")
                        break
            elif isinstance(content, str):
                # 过滤包含工具相关关键词的字符串消息
                if any(keyword in content.lower() for keyword in ['tool_result', 'tool_call', 'tool_use', 'generate_response()', 'arguments validation error']):
                    is_tool_call = True
                    dbg(f"🔒 [{self.name}] 过滤掉工具错误消息")
                # 过滤掉其他玩家的纯JSON结构化输出（避免混淆）
                elif message.name != self.name and message.name != 'Moderator':
                    stripped = content.strip()
                    if stripped.startswith('{') and stripped.endswith('}') and len(stripped) < 200:
                        # 可能是纯JSON输出，跳过以避免混淆
                        is_tool_call = True
                        dbg(f"🔒 [{self.name}] 过滤掉{message.name}的纯JSON消息")
            
            # 额外检查：如果消息的role是tool，也要过滤
            if hasattr(message, 'role') and message.role in ('tool', 'function'):
                is_tool_call = True
                dbg(f"🔒 [{self.name}] 过滤掉role=tool的消息")
            
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
                    dbg(f"[{self.name}] 角色分配: {self.current_role}")
            
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
                dbg(f"[{self.name}] 游戏结束，{'胜利' if won else '失败'}")
            
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
                        dbg(f"⚡ [{self.name}] 关键事件: 预言家死亡，调整策略")
                        self.player_memory.adjust_strategy_realtime({
                            'event_type': 'key_role_died',
                            'role': 'seer',
                            'round': self.player_memory.current_game.get('game_round', 0),
                        })
                    elif "witch" in content.lower() or "女巫" in content:
                        dbg(f"⚡ [{self.name}] 关键事件: 女巫死亡，调整策略")
                        self.player_memory.adjust_strategy_realtime({
                            'event_type': 'key_role_died',
                            'role': 'witch',
                            'round': self.player_memory.current_game.get('game_round', 0),
                        })
                
                # 检测被质疑事件（如果消息中提到自己且有"怀疑"、"可疑"等词）
                if self.name in content:
                    if any(word in content for word in ['怀疑', '可疑', 'suspect', 'suspicious']):
                        dbg(f"⚡ [{self.name}] 关键事件: 被质疑，调整防御姿态")
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
                            dbg(f"👁️ [{self.name}] 对手分析: {player_name} 策略识别为 '{opponent_strategy}'")
                        self.player_memory.opponent_live_tracking[player_name]['style'] = opponent_strategy
        
        # 调用父类的observe，传递过滤后的消息
        # 如果过滤后没有消息了，传递None
        if filtered_messages:
            return await super().observe(filtered_messages if len(filtered_messages) > 1 else filtered_messages[0])
        else:
            return await super().observe(None)
    
    async def __call__(
        self,
        msg: Msg | list[Msg] | None = None,
        *,
        structured_model: Type[BaseModel] | None = None,
    ) -> Msg:
        """Generate agent response with structured output support.
        
        Args:
            msg (`Msg | list[Msg] | None`, optional):
                Input message(s) from moderator or other agents.
            structured_model (`Type[BaseModel] | None`, optional):
                Pydantic model for structured output validation.
        
        Returns:
            `Msg`:
                Response message with content and optional metadata.
        """
        # 🔴 关键修复：在调用前清理记忆中的tool_calls消息
        await self._clean_memory_tool_calls()
        
        max_retries = 2
        model_class = structured_model
        
        # 清理输入消息 - 移除工具调用以防止模型模仿
        sanitized_msg = self._sanitize_message(msg)
        
        # 注入提示信息到消息中
        if sanitized_msg is not None:
            if model_class is not None and hasattr(model_class, '__name__'):
                schema_name = model_class.__name__
                if schema_name == 'DiscussionModel':
                    # 狼人讨论阶段：提供灵活的输出选项
                    discussion_hint = """
🚨 狼人内部讨论阶段（私密频道，仅狼人可见）- 必须先达成共识才能投票 🚨

📢 重要：这是**狼人内部私密讨论**，其他玩家看不到！你可以自由说真话。

这个阶段只有两个选择：要么发表讨论（继续交流），要么直接给出 {"reach_agreement": true}（表示达成一致）。

你有两个输出选项：

选项1 - 继续讨论：
只输出自然语言（2-4句话）：
- 分享你的分析：我们应该淘汰谁以及为什么？
- 回应其他狼人的建议
- 考虑：威胁等级、角色怀疑、投票模式
- 可以说"我觉得我们应该杀Player5"这样的真话（因为这是私密频道）

选项2 - 表示达成一致（仅当所有狼人都同意时）：
同时输出自然语言和JSON：
- 首先，用1-2句话表达你的同意
- 然后准确输出：{"reach_agreement": true}
- ⚠️ 不要输出其他字段！不要输出target、vote或name！
- 讨论阶段只需要reach_agreement字段，投票在后续VoteModel阶段进行

🔴 关键规则（必须遵守）：
1. 必须有狼人输出 {"reach_agreement": true} 后，游戏才会进入投票阶段
2. 讨论阶段绝对不要输出 {"vote": "..."} 格式
3. 只有当狼人之间有明确共识时才输出 {"reach_agreement": true}
4. 如果不确定，选择选项1继续讨论
5. 📌 讨论阶段不涉及投票，不要输出任何玩家名字的字段

🚫 严禁使用工具格式：
- 不要输出：{"name": "generate_response", ...}
- 不要输出：{"type": "tool_use", ...}
"""
                    self._append_hint_to_target(sanitized_msg, discussion_hint)
            
            if model_class is not None:
                # 有结构化模型：注入JSON格式提示
                format_hint = self._build_schema_hint(model_class)
                self._append_hint_to_target(sanitized_msg, format_hint)
            else:
                # 无结构化模型：明确要求自然语言输出
                natural_hint = """
⚠️ 重要提示：这是自由讨论/发言阶段（公开发言）
- 只输出自然语言（不要JSON，不要结构化格式）
- 简洁但有信息量（2-5句话）
- 不要输出JSON对象如 {{"vote": "..."}} 或 {{"reach_agreement": ...}}

🎭 角色扮演规则（极其重要）：
- 这是**公开发言**，所有玩家都能看到你说的话！
- 如果你是狼人：只说村民会说的话，不要提及"狼人"、"伪装"等词
- 如果你是村民/神职：正常表达你对局势的看法
- ❌ 禁止说内心想法：不要说"作为XXX，我需要..."
- ✅ 直接说表面话语：例如"我觉得Player7很可疑，因为..."
"""
                self._append_hint_to_target(sanitized_msg, natural_hint)
        
        for attempt in range(max_retries):
            try:
                # 🔴 关键修复：完全禁用 structured_model，避免 ReActAgent 注册 generate_response 工具
                # ReActAgent 的机制：如果传递 structured_model，会自动注册工具并强制 tool_choice="required"
                # 这会导致 LLM 必须调用 generate_response 工具，产生 tool_use/tool_result 格式
                # 我们采用手动解析 JSON 的方式，因此不需要 ReActAgent 的结构化输出机制
                schema_name = getattr(model_class, '__name__', '') if model_class else ''
                dbg(f"🔧 [{self.name}] 禁用 structured_model 以避免工具调用（我们手动解析JSON）")
                
                # 🚦 使用全局信号量限制并发API调用，避免qwen3-max限流
                async with _api_semaphore:
                    dbg(f"🚦 [{self.name}] 获取API调用许可（当前并发限制: {MAX_CONCURRENT_API_CALLS}）")
                    # 调用父类方法 - 永远传递 None 作为 structured_model
                    response = await super().__call__(
                        msg=sanitized_msg,
                        structured_model=None,  # 🔴 关键：永远传 None，完全禁用工具系统
                    )
                    dbg(f"✅ [{self.name}] API调用完成，释放许可")
                
                # 🔴 强制过滤工具调用 - DeepSeek/DashScope经常输出tool_use/tool_result格式
                # 这里需要提取实际文本并立即解析JSON到metadata
                if hasattr(response, 'content'):
                    content = response.content
                    extracted_text = None
                    
                    # 如果content是工具调用对象,提取实际文本
                    if isinstance(content, dict):
                        # 处理 tool_use 格式: {"type": "tool_use", "arguments": {"response": "..."}}
                        if 'type' in content and content['type'] == 'tool_use':
                            if 'arguments' in content and isinstance(content['arguments'], dict):
                                if 'response' in content['arguments']:
                                    extracted_text = content['arguments']['response']
                                    response.content = extracted_text
                                    dbg(f"⚠️ [{self.name}] 已过滤tool_use，提取文本: {extracted_text[:50] if len(extracted_text) > 50 else extracted_text}...")
                        # 处理 tool_result 格式: {"type": "tool_result", "output": [{"type": "text", "text": "..."}]}
                        elif 'type' in content and content['type'] == 'tool_result':
                            if 'output' in content and isinstance(content['output'], list):
                                for output_item in content['output']:
                                    if isinstance(output_item, dict) and output_item.get('type') == 'text':
                                        extracted_text = output_item.get('text', '')
                                        response.content = extracted_text
                                        dbg(f"⚠️ [{self.name}] 已过滤tool_result，提取文本: {extracted_text[:50] if len(extracted_text) > 50 else extracted_text}...")
                                        break
                    # 如果content是列表,过滤掉工具调用元素
                    elif isinstance(content, list):
                        filtered = []
                        for item in content:
                            if isinstance(item, dict):
                                # 处理 tool_use
                                if item.get('type') == 'tool_use':
                                    if 'arguments' in item and 'response' in item['arguments']:
                                        extracted = item['arguments']['response']
                                        filtered.append({'type': 'text', 'text': extracted})
                                        if extracted_text is None:
                                            extracted_text = extracted
                                        dbg(f"⚠️ [{self.name}] 已从列表过滤tool_use")
                                # 处理 tool_result
                                elif item.get('type') == 'tool_result':
                                    if 'output' in item and isinstance(item['output'], list):
                                        for output_item in item['output']:
                                            if isinstance(output_item, dict) and output_item.get('type') == 'text':
                                                extracted = output_item.get('text', '')
                                                filtered.append({'type': 'text', 'text': extracted})
                                                if extracted_text is None:
                                                    extracted_text = extracted
                                                dbg(f"⚠️ [{self.name}] 已从列表过滤tool_result")
                                                break
                                else:
                                    filtered.append(item)
                            elif isinstance(item, str):
                                filtered.append({'type': 'text', 'text': item})
                            else:
                                filtered.append(item)
                        if filtered:
                            response.content = filtered
                    
                    # 🔴 关键修复：如果提取了文本且需要结构化输出，立即解析JSON到metadata
                    if extracted_text and model_class is not None:
                        import json
                        import re
                        
                        # 确保metadata存在
                        if response.metadata is None:
                            response.metadata = {}
                        
                        # 尝试从提取的文本中解析JSON
                        json_obj = None
                        json_match = re.search(r'\{[^{}]*\}', extracted_text, re.DOTALL)
                        if json_match:
                            json_str = json_match.group()
                            try:
                                json_obj = json.loads(json_str)
                            except json.JSONDecodeError:
                                # 尝试修复常见的JSON格式问题
                                fixed_json = json_str.replace("'", '"').replace('True', 'true').replace('False', 'false').replace('None', 'null')
                                try:
                                    json_obj = json.loads(fixed_json)
                                except json.JSONDecodeError:
                                    pass
                        
                        # 如果成功解析，填充到metadata（先过滤不需要的字段）
                        if json_obj:
                            schema_name = getattr(model_class, '__name__', '')
                            # 对DiscussionModel，移除多余字段
                            if schema_name == 'DiscussionModel':
                                # 只保留reach_agreement字段
                                filtered_obj = {k: v for k, v in json_obj.items() if k == 'reach_agreement'}
                                response.metadata.update(filtered_obj)
                                dbg(f"✅ [{self.name}] 从tool_use提取JSON(已过滤): {filtered_obj}")
                            else:
                                response.metadata.update(json_obj)
                                dbg(f"✅ [{self.name}] 从tool_use提取的JSON已填充到metadata: {json_obj}")
                
                # 如果使用了结构化输出模型,验证响应完整性
                if model_class is not None:
                    
                    # 🔴 修复: 确保metadata存在且是字典
                    if response.metadata is None or not isinstance(response.metadata, dict):
                        response.metadata = {}
                        dbg(f"⚠️ [{self.name}] metadata为None或非字典，已初始化为空字典")
                    
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
                        
                        # 尝试多种方式提取和解析 JSON
                        json_obj = None
                        
                        # 方法1: 直接提取标准JSON对象
                        json_match = re.search(r'\{[^{}]*\}', content_str, re.DOTALL)
                        if json_match:
                            json_str = json_match.group()
                            try:
                                json_obj = json.loads(json_str)
                            except json.JSONDecodeError:
                                # 方法2: 尝试修复常见的JSON格式问题
                                # 将单引号替换为双引号
                                fixed_json = json_str.replace("'", '"')
                                # 修复 True/False -> true/false
                                fixed_json = fixed_json.replace('True', 'true').replace('False', 'false')
                                # 修复 None -> null
                                fixed_json = fixed_json.replace('None', 'null')
                                try:
                                    json_obj = json.loads(fixed_json)
                                    dbg(f"✅ [{self.name}] JSON修复成功")
                                except json.JSONDecodeError as e:
                                    # 方法3: 使用正则表达式手动提取键值对
                                    dbg(f"⚠️ [{self.name}] JSON解析失败: {e}，尝试手动提取")
                                    # 提取 "key": value 或 'key': value 格式
                                    kv_pattern = r'["\']?(\w+)["\']?\s*:\s*(["\']?)([^,}\'"]+)\2'
                                    matches = re.findall(kv_pattern, json_str)
                                    if matches:
                                        json_obj = {}
                                        for key, _, value in matches:
                                            # 尝试转换值的类型
                                            value = value.strip()
                                            if value.lower() == 'true':
                                                json_obj[key] = True
                                            elif value.lower() == 'false':
                                                json_obj[key] = False
                                            elif value.lower() == 'null':
                                                json_obj[key] = None
                                            else:
                                                json_obj[key] = value
                                        dbg(f"✅ [{self.name}] 手动提取JSON成功: {json_obj}")
                        
                        # 如果成功提取到JSON，填充到metadata
                        if json_obj:
                            for key, value in json_obj.items():
                                if key not in response.metadata:
                                    response.metadata[key] = value
                    
                    # 🔴 修复字段名映射: LLM有时输出错误的字段名（根据schema类型条件映射）
                    schema_name = getattr(model_class, '__name__', '')
                    
                    # 根据不同的schema类型应用不同的字段映射
                    field_mappings = {}
                    if schema_name == 'SeerModel':
                        field_mappings = {'check_target': 'name', 'target': 'name'}
                    elif schema_name == 'HunterModel':
                        field_mappings = {'shoot_target': 'name', 'target': 'name'}
                    elif schema_name == 'WitchPoisonModel':
                        field_mappings = {'poison_target': 'name', 'target': 'name'}
                    elif schema_name == 'VoteModel':
                        field_mappings = {'vote_target': 'vote', 'target': 'vote'}
                    elif schema_name == 'DiscussionModel':
                        # DiscussionModel只有reach_agreement字段，移除所有多余字段
                        # 🔴 智能检测：如果LLM输出了vote/target/name，说明想投票，自动设置reach_agreement=true
                        has_vote_intent = False
                        if 'vote' in response.metadata or 'target' in response.metadata or 'name' in response.metadata:
                            has_vote_intent = True
                            dbg(f"🎯 [{self.name}] 检测到投票意图，自动设置 reach_agreement=True")
                        
                        # 移除多余字段
                        if 'target' in response.metadata:
                            del response.metadata['target']
                        if 'vote' in response.metadata:
                            del response.metadata['vote']
                        if 'name' in response.metadata:
                            del response.metadata['name']
                        
                        # 如果检测到投票意图，自动设置reach_agreement=true
                        if has_vote_intent and 'reach_agreement' not in response.metadata:
                            response.metadata['reach_agreement'] = True
                    
                    for wrong_field, correct_field in field_mappings.items():
                        if wrong_field in response.metadata and correct_field not in response.metadata:
                            response.metadata[correct_field] = response.metadata[wrong_field]
                            del response.metadata[wrong_field]
                    
                    # 🔴 修复: 确保所有必需字段都在metadata中
                    if hasattr(model_class, 'model_fields'):
                        schema_name = model_class.__name__ if hasattr(model_class, '__name__') else ''
                        dbg(f"🔍 [{self.name}] 验证schema: {schema_name}, 当前metadata: {response.metadata}")
                        
                        for field_name, field_info in model_class.model_fields.items():
                            # 如果是必填字段但不在metadata中,添加默认值
                            if field_info.is_required() and field_name not in response.metadata:
                                # 🔴 特殊处理: DiscussionModel的reach_agreement不应强制要求
                                # 允许狼人自由讨论,只有主动表示达成一致时才输出true
                                if schema_name == 'DiscussionModel' and field_name == 'reach_agreement':
                                    # 添加False表示继续讨论(这是正常的讨论流程)
                                    response.metadata[field_name] = False
                                    dbg(f"✅ [{self.name}] DiscussionModel: 添加默认值 reach_agreement=False (继续讨论)")
                                    continue
                                
                                # 根据字段类型设置默认值
                                if field_info.annotation == bool:
                                    default_value = False
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
                
                # 🔴 最终清理：确保返回的response不包含任何工具调用格式
                if hasattr(response, 'content'):
                    content = response.content
                    # 如果content还是dict且包含工具格式，强制转换为字符串
                    if isinstance(content, dict):
                        if content.get('type') in ('tool_use', 'tool_result', 'tool_call'):
                            # 提取实际文本或使用空字符串
                            if 'arguments' in content and 'response' in content['arguments']:
                                response.content = content['arguments']['response']
                            elif 'output' in content and isinstance(content['output'], list):
                                for item in content['output']:
                                    if isinstance(item, dict) and item.get('type') == 'text':
                                        response.content = item.get('text', '')
                                        break
                            else:
                                # 兜底：转为字符串
                                response.content = str(content)
                            dbg(f"🧹 [{self.name}] 最终清理：移除了返回内容中的工具格式")
                    # 如果content是列表，过滤掉工具元素
                    elif isinstance(content, list):
                        normalized: list[dict[str, object]] = []
                        for item in content:
                            if isinstance(item, dict):
                                if item.get('type') in ('tool_use', 'tool_result', 'tool_call'):
                                    # 提取文本或继续保持原样
                                    if 'arguments' in item and isinstance(item['arguments'], dict):
                                        response_text = item['arguments'].get('response')
                                        if isinstance(response_text, str):
                                            normalized.append({'type': 'text', 'text': response_text})
                                    elif 'output' in item and isinstance(item['output'], list):
                                        for out in item['output']:
                                            if isinstance(out, dict) and out.get('type') == 'text':
                                                normalized.append({'type': 'text', 'text': out.get('text', '')})
                                                break
                                else:
                                    normalized.append(item)
                            else:
                                normalized.append({'type': 'text', 'text': str(item)})

                        if normalized:
                            if len(normalized) == 1 and normalized[0].get('type') == 'text':
                                response.content = normalized[0].get('text', '')
                            else:
                                response.content = normalized
                            dbg(f"🧹 [{self.name}] 最终清理：过滤了列表中的工具格式")
                
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
        """保存状态 - 跨局学习的关键！（确保消息格式正确）"""
        base_state = super().state_dict()
        
        # 修复base_state中的消息格式问题（检查两种可能的结构）
        memory_state = None
        if 'memory' in base_state:
            memory_state = base_state['memory']
        elif '_module_dict' in base_state and 'memory' in base_state['_module_dict']:
            memory_state = base_state['_module_dict']['memory']
        
        if memory_state and 'content' in memory_state and isinstance(memory_state['content'], list):
            fixed_content = []
            for msg_data in memory_state['content']:
                if isinstance(msg_data, dict):
                    # 深拷贝消息
                    fixed_msg = msg_data.copy()
                    if 'content' in fixed_msg:
                        # 确保content是字符串或列表
                        content = fixed_msg['content']
                        if isinstance(content, dict):
                            # 如果是字典（如 {'type': 'text', 'text': '...'}），提取text
                            if 'text' in content:
                                fixed_msg['content'] = content['text']
                            else:
                                fixed_msg['content'] = str(content)
                        elif not isinstance(content, (str, list)):
                            fixed_msg['content'] = str(content) if content is not None else ""
                    fixed_content.append(fixed_msg)
                else:
                    fixed_content.append(msg_data)
            memory_state['content'] = fixed_content

        dbg(
            f"[{self.name}] save checkpoint: "
            f"total_games={getattr(self.player_memory, 'total_games', None)}",
        )

        return {
            **base_state,
            'player_memory': self.player_memory.to_dict(),
            'current_role': self.current_role,
            '_current_phase': self._current_phase,
        }
    
    def load_state_dict(self, state: dict) -> None:
        """加载状态 - 恢复之前的经验（真正修复checkpoint格式）"""
        dbg(
            f"[{self.name}] load_state_dict called, "
            f"keys={list(state.keys())}",
        )
        # 深度修复checkpoint中的消息格式问题
        def fix_message_content(msg_dict):
            """递归修复消息字典中的content字段"""
            if isinstance(msg_dict, dict):
                # 创建新字典避免修改原始数据
                fixed = {}
                for key, value in msg_dict.items():
                    if key == 'content':
                        # 修复content字段：必须是字符串或列表
                        if isinstance(value, (str, list)):
                            fixed[key] = value
                        elif value is None:
                            fixed[key] = ""  # None转为空字符串
                        else:
                            fixed[key] = str(value)  # 其他类型转为字符串
                    elif isinstance(value, dict):
                        fixed[key] = fix_message_content(value)
                    elif isinstance(value, list):
                        fixed[key] = [fix_message_content(item) if isinstance(item, dict) else item for item in value]
                    else:
                        fixed[key] = value
                return fixed
            return msg_dict
        
        # 修复state中所有的消息数据（检查两种可能的结构）
        memory_state = None
        if 'memory' in state:
            memory_state = state['memory']
        elif '_module_dict' in state and 'memory' in state['_module_dict']:
            memory_state = state['_module_dict']['memory']
        
        if memory_state and 'content' in memory_state and isinstance(memory_state['content'], list):
            msg_count = len(memory_state['content'])
            print(f"[DEBUG] [{self.name}] 检测到 {msg_count} 条消息，开始修复...")
            fixed_content = []
            fixed_count = 0
            for i, msg_data in enumerate(memory_state['content']):
                try:
                    fixed_msg = fix_message_content(msg_data)
                    # 检查是否真的修复了
                    if isinstance(fixed_msg, dict) and 'content' in fixed_msg:
                        if not isinstance(fixed_msg['content'], (str, list)):
                            print(f"[DEBUG] [{self.name}] 消息 {i} content类型: {type(fixed_msg['content'])}")
                        else:
                            fixed_count += 1
                    fixed_content.append(fixed_msg)
                except Exception as e:
                    print(f"[WARN] [{self.name}] 消息 {i} 修复失败: {str(e)[:50]}")
                    continue
            memory_state['content'] = fixed_content
            print(f"[DEBUG] [{self.name}] 修复完成: {fixed_count}/{msg_count} 条消息格式正确")
        
        try:
            # 加载父类状态
            super().load_state_dict(state)
            print(f"[OK] [{self.name}] checkpoint加载成功")
        except (AssertionError, ValueError, TypeError) as e:
            print(f"[FAIL] [{self.name}] checkpoint加载仍然失败: {str(e)[:100]}")
            print(f"       这可能是更深层的格式问题，将使用默认状态")
            # 即使失败也继续，使用默认状态
            pass
        
        # 确保 player_memory 存在（防止被父类覆盖）
        if not hasattr(self, 'player_memory') or not isinstance(self.player_memory, PlayerMemory):
            self.player_memory = PlayerMemory()
        
        # 加载记忆
        if 'player_memory' in state:
            try:
                dbg(
                    f"[{self.name}] load_state_dict: "
                    f"player_memory.total_games in state="
                    f"{state['player_memory'].get('total_games', None)}",
                )
                self.player_memory.from_dict(state['player_memory'])
                dbg(
                    f"[{self.name}] load_state_dict: "
                    f"player_memory.total_games after load="
                    f"{self.player_memory.total_games}",
                )
            except Exception as e:
                print(f"[WARN] [{self.name}] 无法加载player_memory，使用默认: {str(e)[:50]}")
        
        # 加载当前角色（如果有）
        self.current_role = state.get('current_role', None)
        self._current_phase = state.get('_current_phase', None)
        
        print(f"[OK] [{self.name}] 状态已加载，历史游戏数: {self.player_memory.total_games}")
