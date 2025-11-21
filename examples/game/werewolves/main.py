# -*- coding: utf-8 -*-
# flake8: noqa: E501
"""The main entry point for the werewolf game."""
import asyncio
import os

from game import werewolves_game
from agent import PlayerAgent  # ← 导入你的Agent

from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeMultiAgentFormatter
from agentscope.model import DashScopeChatModel
from agentscope.session import JSONSession


def get_official_agents(name: str) -> ReActAgent:
    """Get the official werewolves game agents."""
    agent = ReActAgent(
        name=name,
        sys_prompt=f"""You're a werewolf game player named {name}.

# YOUR TARGET
Your target is to win the game with your teammates as much as possible.

# GAME RULES
- In werewolf game, players are divided into three werewolves, three villagers, one seer, one hunter and one witch.
    - Werewolves: kill one player each night, and must hide identity during the day.
    - Villagers: ordinary players without special abilities, try to identify and eliminate werewolves.
        - Seer: A special villager who can check one player's identity each night.
        - Witch: A special villager with two one-time-use potions: a healing potion to save a player from being killed at night, and a poison to eliminate one player at night.
        - Hunter: A special villager who can take one player down with them when they are eliminated.
- The game alternates between night and day phases until one side wins:
    - Night Phase
        - Werewolves choose one victim
        - Seer checks one player's identity
        - Witch decides whether to use potions
        - Moderator announces who died during the night
    - Day Phase
        - All players discuss and vote to eliminate one suspected player

# GAME GUIDANCE
- Try your best to win the game with your teammates, tricks, lies, and deception are all allowed, e.g. pretending to be a different role.
- During discussion, don't be political, be direct and to the point.
- The day phase voting provides important clues. For example, the werewolves may vote together, attack the seer, etc.
## GAME GUIDANCE FOR WEREWOLF
- Seer is your greatest threat, who can check one player's identity each night. Analyze players' speeches, find out the seer and eliminate him/her will greatly increase your chances of winning.
- In the first night, making random choices is common for werewolves since no information is available.
- Pretending to be other roles (seer, witch or villager) is a common strategy to hide your identity and mislead other villagers in the day phase.
- The outcome of the night phase provides important clues. For example, if witch uses the healing or poison potion, if the dead player is hunter, etc. Use this information to adjust your strategy.
## GAME GUIDANCE FOR SEER
- Seer is very important to villagers, exposing yourself too early may lead to being targeted by werewolves.
- Your ability to check one player's identity is crucial.
- The outcome of the night phase provides important clues. For example, if witch uses the healing or poison potion, if the dead player is hunter, etc. Use this information to adjust your strategy.
## GAME GUIDANCE FOR WITCH
- Witch has two powerful potions, use them wisely to protect key villagers or eliminate suspected werewolves.
- The outcome of the night phase provides important clues. For example, if the dead player is hunter, etc. Use this information to adjust your strategy.
## GAME GUIDANCE FOR HUNTER
- Using your ability in day phase will expose your role (since only hunter can take one player down)
- The outcome of the night phase provides important clues. For example, if witch uses the healing or poison potion, etc. Use this information to adjust your strategy.
## GAME GUIDANCE FOR VILLAGER
- Protecting special villagers, especially the seer, is crucial for your team's success.
- Werewolves may pretend to be the seer. Be cautious and don't trust anyone easily.
- The outcome of the night phase provides important clues. For example, if witch uses the healing or poison potion, if the dead player is hunter, etc. Use this information to adjust your strategy.

# NOTE
- [IMPORTANT] DO NOT make up any information that is not provided by the moderator or other players.
- This is a TEXT-based game, so DO NOT use or make up any non-textual information.
- Always critically reflect on whether your evidence exist, and avoid making assumptions.
- Your response should be specific and concise, provide clear reason and avoid unnecessary elaboration.
- Generate your one-line response by using the `generate_response` function.
- Don't repeat the others' speeches.""",
        model=DashScopeChatModel(
            api_key=os.environ.get("DASHSCOPE_API_KEY"),
            model_name="qwen3-max",
        ),
        formatter=DashScopeMultiAgentFormatter(),
    )
    return agent


async def main() -> None:
    """The main entry point for the werewolf game."""

    # Uncomment the following lines if you want to use Agentscope Studio
    # to visualize the game process.
    # import agentscope
    # agentscope.init(
    #     studio_url="http://localhost:3000",
    #     project="werewolf_game",
    # )

    # Prepare 9 players, you can change their names here
    
    #players = [get_official_agents(f"Player{_ + 1}") for _ in range(9)]
    players = [PlayerAgent(f"Player{_ + 1}") for _ in range(9)]

    # Note: You can replace your own agents here, or use all your own agents

    # Load states from a previous checkpoint
    session = JSONSession(save_dir="./checkpoints")
    await session.load_session_state(
        session_id="players_checkpoint",
        **{player.name: player for player in players},
    )

    # 🎮 多局游戏循环 - 让AI学习和进化
    num_games = 5  # 设置游戏局数（可以修改为 5, 20, 50, 100 等）
    
    for game_round in range(1, num_games + 1):
        print(f"\n{'='*70}")
        print(f"🎮 开始第 {game_round}/{num_games} 局游戏")
        print(f"{'='*70}\n")
        
        # 运行一局游戏
        await werewolves_game(players)
        
        # 每5局保存一次检查点（防止意外中断丢失数据）
        if game_round % 5 == 0 or game_round == num_games:
            await session.save_session_state(
                session_id="players_checkpoint",
                **{player.name: player for player in players},
            )
            print(f"\n💾 已保存第 {game_round} 局的检查点")
        
        print(f"\n{'='*70}")
        print(f"✅ 第 {game_round}/{num_games} 局游戏结束")
        
        # 显示当前学习进度
        sample_player = players[0]
        if hasattr(sample_player, 'player_memory'):
            total = sample_player.player_memory.total_games
            wins = sample_player.player_memory.wins
            win_rate = wins / total if total > 0 else 0
            print(f"📊 {sample_player.name} 当前战绩: {wins}/{total} 胜 (胜率: {win_rate:.1%})")
        print(f"{'='*70}\n")
        
        # 短暂停顿，让日志更清晰
        await asyncio.sleep(1)
    
    # 最终保存
    await session.save_session_state(
        session_id="players_checkpoint",
        **{player.name: player for player in players},
    )
    
    print(f"\n{'='*70}")
    print(f"🎉 所有 {num_games} 局游戏已完成！")
    print(f"{'='*70}")
    
    # 显示最终统计
    print(f"\n📈 最终学习成果统计:\n")
    for player in players:
        if hasattr(player, 'player_memory'):
            mem = player.player_memory
            win_rate = mem.wins / mem.total_games if mem.total_games > 0 else 0
            print(f"  {player.name}: {mem.wins}/{mem.total_games} 胜 (胜率: {win_rate:.1%})")
    print()


asyncio.run(main())
