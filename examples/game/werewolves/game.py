# -*- coding: utf-8 -*-
# pylint: disable=too-many-branches, too-many-statements, no-name-in-module
"""A werewolf game implemented by agentscope."""
import numpy as np

from utils import (
    majority_vote,
    names_to_str,
    EchoAgent,
    MAX_GAME_ROUND,
    MAX_DISCUSSION_ROUND,
    Players,
)
from structured_model import (
    DiscussionModel,
    get_vote_model,
    get_poison_model,
    WitchResurrectModel,
    get_seer_model,
    get_hunter_model,
)
# from prompt import EnglishPrompts as Prompts

# 使用中文提示词
from prompt import ChinesePrompts as Prompts


from agentscope.agent import ReActAgent
from agentscope.pipeline import (
    MsgHub,
    sequential_pipeline,
    fanout_pipeline,
)


moderator = EchoAgent()


def _sanitize_text(text: str) -> str:
    """Sanitize text by removing tool-call artifacts and excessive control tokens.

    This removes sequences like <｜tool▁calls▁begin｜> ... <｜tool▁calls▁end｜>
    and other generate_response wrappers which sometimes leak into LLM output.
    """
    import re

    if not isinstance(text, str):
        text = str(text)

    # remove special tool markers
    text = re.sub(r"<｜tool.*?｜>", "", text)
    # remove angled unicode tool markers
    text = re.sub(r"<\｜.*?\｜>", "", text)
    # remove repeated generate_response fragments
    text = text.replace('generate_response', '')
    # collapse multiple spaces/newlines
    text = re.sub(r"\s{2,}", " ", text)
    text = text.strip()
    return text


async def sanitize_msgs(msgs: list) -> list:
    """Return a new list of Msg-like objects with sanitized content and ensured metadata."""
    sanitized = []
    for m in msgs:
        try:
            # m may be a Msg or simple string
            if hasattr(m, 'content'):
                content = m.content
                if isinstance(content, list):
                    # join list parts
                    content = " ".join(str(x) for x in content)
                if isinstance(content, str):
                    m.content = _sanitize_text(content)
                else:
                    m.content = _sanitize_text(str(content))
                if not hasattr(m, 'metadata') or m.metadata is None:
                    m.metadata = {}
                sanitized.append(m)
            else:
                # if raw string, wrap into moderator Msg
                s = _sanitize_text(str(m))
                nm = await moderator(s)
                nm.metadata = {}
                sanitized.append(nm)
        except Exception:
            # best-effort: keep original if sanitization fails
            sanitized.append(m)
    return sanitized


async def safe_call(agent, msg=None, **kwargs):
    """Safe wrapper for calling agents.

    If the agent call raises an exception (e.g. API error), return a
    moderator-generated fallback Msg with an empty metadata dict so the
    game can continue.
    """
    try:
        # Avoid passing `structured_model` down to arbitrary internal handlers
        # which may not accept it (this previously caused unexpected kw errors).
        structured_model = kwargs.pop('structured_model', None)

        if msg is None:
            res = await agent(**kwargs)
        else:
            res = await agent(msg, **kwargs)
        # Ensure metadata exists to avoid AttributeError in game logic
        if not hasattr(res, 'metadata') or res.metadata is None:
            res.metadata = {}
        return res
    except Exception as e:
        print(f"⚠️ [safe_call] Agent {getattr(agent, 'name', 'Agent')} failed: {e}")
        # Return a moderator message as fallback with empty metadata
        fallback = await moderator(f"[{getattr(agent, 'name', 'Agent')}] fallback due to error: {str(e)[:120]}")
        fallback.metadata = {}
        return fallback


async def hunter_stage(
    hunter_agent: ReActAgent,
    players: Players,
) -> str | None:
    """Because the hunter's stage may happen in two places: killed at night
    or voted during the day, we define a function here to avoid duplication."""
    global moderator
    msg_hunter = await safe_call(
        hunter_agent,
        await moderator(Prompts.to_hunter.format(name=hunter_agent.name)),
        structured_model=get_hunter_model(players.current_alive),
    )
    if msg_hunter.metadata.get("shoot"):
        return msg_hunter.metadata.get("name", None)
    return None


async def werewolves_game(agents: list[ReActAgent]) -> None:
    """The main entry of the werewolf game

    Args:
        agents (`list[ReActAgent]`):
            A list of 9 agents.
    """
    assert len(agents) == 9, "The werewolf game needs exactly 9 players."

    # Init the players' status
    players = Players()

    # If the witch has healing and poison potion
    healing, poison = True, True

    # If it's the first day, the dead can leave a message
    first_day = True

    # Broadcast the game begin message
    async with MsgHub(participants=agents) as greeting_hub:
        await greeting_hub.broadcast(
            await moderator(
                Prompts.to_all_new_game.format(names_to_str(agents)),
            ),
        )

    # Assign roles to the agents
    roles = ["werewolf"] * 3 + ["villager"] * 3 + ["seer", "witch", "hunter"]
    np.random.shuffle(agents)
    np.random.shuffle(roles)

    for agent, role in zip(agents, roles):
        # Tell the agent its role
        await agent.observe(
            await moderator(
                f"[{agent.name} ONLY] {agent.name}, your role is {role}.",
            ),
        )
        players.add_player(agent, role)

    # Printing the roles
    players.print_roles()

    # GAME BEGIN!
    for _ in range(MAX_GAME_ROUND):
        # Create a MsgHub for all players to broadcast messages
        async with MsgHub(
            participants=players.current_alive,
            enable_auto_broadcast=False,  # manual broadcast only
            name="alive_players",
        ) as alive_players_hub:
            # Night phase
            await alive_players_hub.broadcast(
                await moderator(Prompts.to_all_night),
            )
            killed_player, poisoned_player, shot_player = None, None, None

            # Werewolves discuss (collect messages first to avoid repetitive identical outputs)
            async with MsgHub(
                players.werewolves,
                enable_auto_broadcast=False,
                announcement=await moderator(
                    Prompts.to_wolves_discussion.format(
                        names_to_str(players.werewolves),
                        names_to_str(players.current_alive),
                    ),
                ),
                name="werewolves",
            ) as werewolves_hub:
                # Discussion: collect replies and stop when consensus reached.
                n_werewolves = len(players.werewolves)
                werewolf_replies = []
                for i in range(1, MAX_DISCUSSION_ROUND * n_werewolves + 1):
                    # call each werewolf safely; discussion may fail if model errors
                    res = await safe_call(
                        players.werewolves[i % n_werewolves],
                        None,
                        structured_model=DiscussionModel,
                    )
                    # keep reply for later sanitized broadcast
                    werewolf_replies.append(res)
                    # If a round boundary and a consensus signal appears, stop early
                    if i % n_werewolves == 0 and res.metadata.get("reach_agreement"):
                        break

                # Broadcast collected werewolf discussion once (sanitized, de-duplicated)
                try:
                    # Sanitize messages
                    werewolf_msgs = await sanitize_msgs(werewolf_replies)
                    # Optionally compress duplicates by only broadcasting the last
                    # consensus message if provided, otherwise broadcast all sanitized messages.
                    consensus_msgs = [m for m in werewolf_msgs if getattr(m, 'metadata', {}).get('reach_agreement')]
                    if consensus_msgs:
                        # broadcast only the last consensus message and contextual messages
                        await werewolves_hub.broadcast([consensus_msgs[-1]])
                    else:
                        await werewolves_hub.broadcast(werewolf_msgs)
                except Exception as e:
                    print(f"⚠️ [werewolves discuss] sanitize/broadcast failed: {e}")

                # Werewolves vote (each votes privately)
                try:
                    msgs_vote = await fanout_pipeline(
                        players.werewolves,
                        msg=await moderator(content=Prompts.to_wolves_vote),
                        structured_model=get_vote_model(players.current_alive),
                        enable_gather=False,
                    )
                except Exception as e:
                    print(f"⚠️ [werewolves vote] fanout_pipeline failed: {e}")
                    msgs_vote = []
                    for p in players.werewolves:
                        m = await moderator(f"[{p.name}] fallback vote")
                        m.metadata = {}
                        msgs_vote.append(m)

                killed_player, votes = majority_vote([
                    _.metadata.get("vote") for _ in msgs_vote
                ])

                # Postpone the broadcast of voting (sanitize agent outputs first)
                res_m = await moderator(
                    Prompts.to_wolves_res.format(votes, killed_player),
                )
                msgs_to_broadcast = await sanitize_msgs([*msgs_vote, res_m])
                await werewolves_hub.broadcast(msgs_to_broadcast)

            # Witch's turn
            await alive_players_hub.broadcast(
                await moderator(Prompts.to_all_witch_turn),
            )
            msg_witch_poison = None
            for agent in players.witch:
                # Cannot heal witch herself
                msg_witch_resurrect = None
                if healing and killed_player != agent.name:
                    msg_witch_resurrect = await safe_call(
                        agent,
                        await moderator(
                            Prompts.to_witch_resurrect.format(
                                witch_name=agent.name,
                                dead_name=killed_player,
                            ),
                        ),
                        structured_model=WitchResurrectModel,
                    )
                    if msg_witch_resurrect.metadata.get("resurrect"):
                            # Remember the name, then clear the planned killed player so update_players won't remove them.
                            resurrected_name = killed_player
                            killed_player = None
                            healing = False
                            # Announce resurrection succinctly
                            try:
                                await alive_players_hub.broadcast(
                                    await moderator(Prompts.to_witch_resurrect_yes),
                                )
                                await alive_players_hub.broadcast(
                                    await moderator(f"{agent.name} resurrected {resurrected_name}.")
                                )
                            except Exception:
                                pass
                            # Announce resurrection succinctly
                            try:
                                # Simple announcement: use existing prompt then a short named message
                                await alive_players_hub.broadcast(
                                    await moderator(Prompts.to_witch_resurrect_yes),
                                )
                                await alive_players_hub.broadcast(
                                    await moderator(f"{agent.name} resurrected {resurrected_name}.")
                                )
                            except Exception:
                                pass

                # Has poison potion and hasn't used the healing potion
                if poison and not (
                    msg_witch_resurrect
                    and msg_witch_resurrect.metadata.get("resurrect")
                ):
                    msg_witch_poison = await safe_call(
                        agent,
                        await moderator(
                            Prompts.to_witch_poison.format(
                                witch_name=agent.name,
                            ),
                        ),
                        structured_model=get_poison_model(
                            players.current_alive,
                        ),
                    )
                    if msg_witch_poison.metadata.get("poison"):
                        poisoned_player = msg_witch_poison.metadata.get("name")
                        poison = False

            # Seer's turn
            await alive_players_hub.broadcast(
                await moderator(Prompts.to_all_seer_turn),
            )
            for agent in players.seer:
                msg_seer = await safe_call(
                    agent,
                    await moderator(
                        Prompts.to_seer.format(
                            agent.name,
                            names_to_str(players.current_alive),
                        ),
                    ),
                    structured_model=get_seer_model(players.current_alive),
                )
                if msg_seer.metadata.get("name"):
                    player = msg_seer.metadata["name"]
                    await agent.observe(
                        await moderator(
                            Prompts.to_seer_result.format(
                                agent_name=player,
                                role=players.name_to_role[player],
                            ),
                        ),
                    )

            # Hunter's turn
            for agent in players.hunter:
                # If killed and not by witch's poison
                if (
                    killed_player == agent.name
                    and poisoned_player != agent.name
                ):
                    shot_player = await hunter_stage(agent, players)

            # Update alive players
            dead_tonight = [killed_player, poisoned_player, shot_player]
            players.update_players(dead_tonight)

            # Day phase
            if len([_ for _ in dead_tonight if _]) > 0:
                await alive_players_hub.broadcast(
                    await moderator(
                        Prompts.to_all_day.format(
                            names_to_str([_ for _ in dead_tonight if _]),
                        ),
                    ),
                )

                # The killed player leave a last message in first night
                if killed_player and first_day:
                    msg_moderator = await moderator(
                        Prompts.to_dead_player.format(killed_player),
                    )
                    await alive_players_hub.broadcast(msg_moderator)
                    # Leave a message
                    last_msg = await safe_call(players.name_to_agent[killed_player])
                    await alive_players_hub.broadcast(last_msg)

            else:
                await alive_players_hub.broadcast(
                    await moderator(Prompts.to_all_peace),
                )

            # Check winning
            res = players.check_winning()
            if res:
                await moderator(res)
                break

            # Discussion
            await alive_players_hub.broadcast(
                await moderator(
                    Prompts.to_all_discuss.format(
                        names=names_to_str(players.current_alive),
                    ),
                ),
            )
            # Open the auto broadcast to enable discussion
            alive_players_hub.set_auto_broadcast(True)
            # Use try/except around sequential pipeline to avoid a single model error
            try:
                await sequential_pipeline(players.current_alive)
            except Exception as e:
                print(f"⚠️ [discussion] sequential_pipeline failed: {e}")
            # Disable auto broadcast to avoid leaking info
            alive_players_hub.set_auto_broadcast(False)

            # Voting
            try:
                msgs_vote = await fanout_pipeline(
                    players.current_alive,
                    await moderator(
                        Prompts.to_all_vote.format(
                            names_to_str(players.current_alive),
                        ),
                    ),
                    structured_model=get_vote_model(players.current_alive),
                    enable_gather=False,
                )
            except Exception as e:
                print(f"⚠️ [global vote] fanout_pipeline failed: {e}")
                msgs_vote = []
                for p in players.current_alive:
                    m = await moderator(f"[{p.name}] fallback vote")
                    m.metadata = {}
                    msgs_vote.append(m)
            voted_player, votes = majority_vote(
                [_.metadata.get("vote") for _ in msgs_vote],
            )
            # Broadcast the voting messages together to avoid influencing
            # each other
            voting_msgs = [
                *msgs_vote,
                await moderator(
                    Prompts.to_all_res.format(votes, voted_player),
                ),
            ]

            # Leave a message if voted
            if voted_player:
                prompt_msg = await moderator(
                    Prompts.to_dead_player.format(voted_player),
                )
                last_msg = await safe_call(players.name_to_agent[voted_player], prompt_msg)
                voting_msgs.extend([prompt_msg, last_msg])

            # Sanitize voting messages before broadcasting
            voting_msgs = await sanitize_msgs(voting_msgs)
            await alive_players_hub.broadcast(voting_msgs)

            # If the voted player is the hunter, he can shoot someone
            shot_player = None
            for agent in players.hunter:
                if voted_player == agent.name:
                    shot_player = await hunter_stage(agent, players)
                    if shot_player:
                        await alive_players_hub.broadcast(
                            await moderator(
                                Prompts.to_all_hunter_shoot.format(
                                    shot_player,
                                ),
                            ),
                        )

            # Update alive players
            dead_today = [voted_player, shot_player]
            players.update_players(dead_today)

            # Check winning
            res = players.check_winning()
            if res:
                async with MsgHub(players.all_players) as all_players_hub:
                    res_msg = await moderator(res)
                    await all_players_hub.broadcast(res_msg)
                break

        # The day ends
        first_day = False

    # Game over, record results for each player
    # 确保所有玩家都记录了游戏结果
    for player in agents:
        if hasattr(player, 'player_memory'):
            # 检查是否已经记录过（通过 observe 方法）
            # 如果没记录，这里补充记录
            if not hasattr(player, '_game_recorded') or not player._game_recorded:
                # 判断是否获胜
                won = False
                if res:  # res 是获胜消息
                    content = res.lower() if isinstance(res, str) else str(res).lower()
                    is_werewolf_win = "werewolves win" in content or "狼人获胜" in content
                    is_villager_win = "villagers win" in content or "村民获胜" in content
                    
                    if is_werewolf_win and player.current_role == "werewolf":
                        won = True
                    elif is_villager_win and player.current_role != "werewolf":
                        won = True
                
                player.player_memory.record_game_result(won)
                player._game_recorded = True
                print(f"📊 [{player.name}] 记录战绩: {'胜利' if won else '失败'} (总计: {player.player_memory.total_games}局)")
    
    # Each player reflects
    try:
        await fanout_pipeline(
            agents=agents,
            msg=await moderator(Prompts.to_all_reflect),
        )
    except Exception as e:
        print(f"⚠️ [reflect] fanout_pipeline failed: {e}")
        # best-effort: broadcast a simple moderator message instead
        await moderator("Reflection skipped due to model errors.")
