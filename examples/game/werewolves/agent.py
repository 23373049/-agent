# -*- coding: utf-8 -*-
"""智能狼人杀Agent - 简化但高效的实现"""
import os
import re
from typing import Optional
from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeMultiAgentFormatter
from agentscope.model import DashScopeChatModel
from agentscope.message import Msg


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
                insights += f"## 夜晚决策建议\n"
                insights += f"- {decision.get('reasoning', '根据当前情况谨慎选择')}\n\n"
            elif self._current_phase == 'day':
                decision = self.decision_maker.day_phase_decision(game_state)
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
        
        role_prompts = {
            'werewolf': """
# 狼人策略 🐺
你现在是狼人！

## 目标
- 和狼人队友配合，消灭好人
- 隐藏身份，避免被发现
- 找出并消灭预言家（最大威胁）

## 夜晚行动
- 第一晚可以随机选择目标（因为没有信息）
- 后续优先消灭：预言家 > 女巫 > 猎人 > 村民
- 和队友讨论，达成一致

## 白天策略
- 可以伪装成村民或预言家
- 如果伪装成预言家，要确保逻辑自洽
- 适当质疑真预言家，混淆视听
- 注意：不要和队友互相指认！

## 关键提示
- 预言家是最大威胁，尽快找出并消灭
- 避免狼人集体给同一人投票（太明显）
- 如果被怀疑，冷静应对，不要慌张
""",
            'seer': """
# 预言家策略 🔮
你现在是预言家！

## 目标
- 使用查验能力找出狼人
- 引导好人投票淘汰狼人
- 保护自己不被狼人发现

## 夜晚行动
- 优先查验发言可疑的人
- 或查验活跃的玩家（可能是狼人在带节奏）
- 记住每次查验的结果

## 白天策略
- 前期不要过早暴露身份（会被狼人针对）
- 如果查到狼人，可以适当引导投票，但要隐晦
- 如果局势紧张，可以公开身份并报验人结果
- 公开后要给出清晰的验人历史

## 关键提示
- 你是好人方最关键的角色
- 存活比查验更重要（死了就没用了）
- 注意有人可能伪装预言家（跳预言家）
""",
            'witch': """
# 女巫策略 🧙‍♀️
你现在是女巫！

## 能力
- 解药：救活一个被杀的人（只能用一次）
- 毒药：毒死一个人（只能用一次）

## 药水使用建议

### 解药
- 第一晚如果有人死，可以考虑救（可能是预言家）
- 后期如果知道是关键好人被杀，优先救
- 不要盲目使用，留给关键时刻

### 毒药
- 确定某人是狼人后再用
- 可以毒掉跳假预言家的人
- 关键时刻使用，帮助好人扳平局势

## 白天策略
- 前期隐藏身份
- 如果局势需要，可以公开身份说明用药情况
- 利用信息优势（你知道谁被杀）进行推理

## 关键提示
- 两瓶药都很宝贵，不要浪费
- 第一晚最好不要同时用药（会暴露身份）
- 你掌握关键信息，要善用
""",
            'hunter': """
# 猎人策略 🏹
你现在是猎人！

## 能力
- 死亡时可以开枪带走一人
- 也可以选择不开枪

## 策略
- 白天不要过早暴露身份（会被女巫毒）
- 如果要被投票淘汰，可以提前说明身份
- 死亡时，优先带走确定的狼人
- 如果不确定，宁可不开枪

## 白天行动
- 可以适当激进一些（因为有开枪保底）
- 但不要太跳，避免被女巫毒
- 帮助分析局势，引导投票

## 关键提示
- 你的枪是威慑，有时不开枪也是策略
- 被女巫毒死不能开枪
- 确定目标再开枪，不要浪费
""",
            'villager': """
# 村民策略 👨‍🌾
你现在是普通村民！

## 角色定位
- 虽然没有特殊能力，但人数众多
- 通过逻辑推理找出狼人
- 保护特殊好人角色

## 策略
- 仔细分析每个人的发言
- 注意投票模式（谁投了谁）
- 观察谁在带节奏
- 保护预言家（如果有人跳预言家）

## 发言技巧
- 不要乱跳身份（会干扰信息）
- 给出清晰的推理逻辑
- 适当质疑可疑的人
- 团结其他村民

## 关键提示
- 你的投票很重要
- 不要被狼人带节奏
- 相信预言家的验人结果
- 避免内讧
"""
        }
        
        return role_prompts.get(self.current_role, "")
    
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
