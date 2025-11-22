# -*- coding: utf-8 -*-
"""Default prompts"""


class EnglishPrompts:
    """English prompts used to guide the werewolf game."""

    to_dead_player = (
        "{}, you're eliminated now. Now you can make a final statement to "
        "all alive players before you leave the game.\n\n"
        "⚠️ CRITICAL RULES FOR FINAL STATEMENT:\n"
        "1. DO NOT reveal information that only your team knows (e.g., who your werewolf teammates are)\n"
        "2. DO NOT expose other players' secret roles unless they were publicly confirmed\n"
        "3. You MAY share your observations, suspicions, and reasoning\n"
        "4. You MAY reveal YOUR OWN role (but not your teammates')\n"
        "5. Keep your statement under 100 words\n\n"
        "This is standard Werewolf game etiquette - protect information that should remain secret."
    )

    to_all_new_game = (
        "A new game is starting, the players are: {}. Now we randomly "
        "reassign the roles to each player and inform them of their roles "
        "privately."
    )

    to_all_night = (
        "Night has fallen, everyone close your eyes. Werewolves open your "
        "eyes and choose a player to eliminate tonight."
    )

    to_wolves_discussion = (
        "[WEREWOLVES ONLY] {}, you should discuss and "
        "decide on a player to eliminate tonight. Current alive players "
        "are {}.\n\n"
        "IMPORTANT: When you reach an agreement, you MUST output in this exact JSON format:\n"
        '{{"reach_agreement": true/false}}  // true when you all agree on a target'
    )

    to_wolves_vote = (
        "[WEREWOLVES ONLY] Which player do you vote to kill?\n\n"
        "IMPORTANT: You MUST output your vote in this exact JSON format:\n"
        '{{"vote": "PlayerX"}}  // PlayerX is the player you want to kill'
    )

    to_wolves_res = (
        "[WEREWOLVES ONLY] The voting result is {}. So you have chosen to "
        "eliminate {}."
    )

    to_all_witch_turn = (
        "Witch's turn, witch open your eyes and decide your action tonight..."
    )
    to_witch_resurrect = (
        "[WITCH ONLY] {witch_name}, you're the witch, and tonight {dead_name} "
        "is eliminated. You can resurrect him/her by using your healing "
        "potion, "
        "and note you can only use it once in the whole game. Do you want to "
        "resurrect {dead_name}? Give me your reason and decision.\n\n"
        "IMPORTANT: You MUST output your decision in this exact JSON format:\n"
        '{{"resurrect": true/false}}  // true to resurrect, false to skip'
    )

    to_witch_resurrect_no = (
        "[WITCH ONLY] The witch has chosen not to resurrect the player."
    )
    to_witch_resurrect_yes = (
        "[WITCH ONLY] The witch has chosen to resurrect the player."
    )

    to_witch_poison = (
        "[WITCH ONLY] {witch_name}, as a witch, you have a one-time-use "
        "poison potion, do you want to use it tonight? Give me your reason "
        "and decision.\n\n"
        "IMPORTANT: You MUST output your decision in this exact JSON format:\n"
        '{{"poison": true/false, "name": "PlayerX"}}  // poison: whether to use poison, name: target player (null if not using)'
    )

    to_all_seer_turn = (
        "Seer's turn, seer open your eyes and check one player's identity "
        "tonight..."
    )

    to_seer = (
        "[SEER ONLY] {}, as the seer you can check one player's identity "
        "tonight.\nAlive players: {}\nWho do you want to check? Give me your reason and decision.\n\n"
        "IMPORTANT: You MUST output your decision in this exact JSON format:\n"
        '{{"name": "PlayerX"}}  // PlayerX is the player you want to check'
    )

    to_seer_result = (
        "[SEER ONLY] You've checked {agent_name}, and the result is: {role}."
    )

    to_hunter = (
        "[HUNTER ONLY] {name}, as the hunter you're eliminated tonight. You "
        "can choose one player to take down with you. Also, you can choose "
        "not to use this ability. Give me your reason and decision.\n\n"
        "IMPORTANT: You MUST output your decision in this exact JSON format:\n"
        '{{"shoot": true/false, "name": "PlayerX"}}  // shoot: whether to use ability, name: target player (null if not shooting)'
    )

    to_all_hunter_shoot = (
        "The hunter has chosen to shoot {} down with him/herself."
    )

    to_all_day = (
        "The day is coming, all players open your eyes. Last night, "
        "the following player(s) has been eliminated: {}."
    )

    to_all_peace = (
        "The day is coming, all the players open your eyes. Last night is "
        "peaceful, no player is eliminated."
    )

    to_all_discuss = (
        "Now the alive players are {names}. The game goes on, it's time to "
        "discuss and vote a player to be eliminated. Now you each take turns "
        "to speak once in the order of {names}."
    )

    to_all_vote = (
        "Now the discussion is over. Everyone, please vote to eliminate one "
        "player from the alive players: {}.\n\n"
        "IMPORTANT: You MUST output your vote in this exact JSON format:\n"
        "{{\"vote\": \"PlayerX\"}}  // PlayerX is the player you want to vote for"
    )

    to_all_res = "The voting result is {}. So {} has been voted out."

    to_all_wolf_win = (
        "There are {n_alive} players alive, and {n_werewolves} of them are "
        "werewolves. "
        "The game is over and werewolves win🐺🎉!"
        "In this game, the true roles of all players are: {true_roles}"
    )

    to_all_village_win = (
        "All the werewolves have been eliminated."
        "The game is over and villagers win🏘️🎉!"
        "In this game, the true roles of all players are: {true_roles}"
    )

    to_all_continue = "The game goes on."

    to_all_reflect = (
        "The game is over. Now each player can reflect on their performance. "
        "Note each player only has one chance to speak and the reflection is "
        "only visible to themselves."
    )


class ChinesePrompts:
    """Chinese prompts used to guide the werewolf game."""

    to_dead_player = (
        "{}, 你已被淘汰。现在你可以向所有存活玩家发表最后的遗言。\n\n"
        "⚠️ 遗言规则（必读）：\n"
        "1. 严禁透露只有你团队知道的信息（如你的狼人队友是谁）\n"
        "2. 严禁暴露其他未公开玩家的真实身份\n"
        "3. 可以分享你的观察、怀疑和推理\n"
        "4. 可以透露你自己的身份（但不能透露队友）\n"
        "5. 遗言控制在100字以内\n\n"
        "这是狼人杀标准规则 - 保护应当保密的信息。"
    )

    to_all_new_game = "新的一局游戏开始，参与玩家包括：{}。现在为每位玩家重新随机分配身份，并私下告知各自身份。"

    to_all_night = "天黑了，请所有人闭眼。狼人请睁眼，选择今晚要淘汰的一名玩家..."

    to_wolves_discussion = (
        "[仅狼人可见] {}, 你们可以讨论并决定今晚要淘汰的玩家。当前存活玩家有：{}。\n\n"
        "重要: 当你们达成一致时，你必须按照以下JSON格式输出:\n"
        '{"reach_agreement": true/false}  // true表示达成一致，false表示继续讨论'
    )

    to_wolves_vote = """[仅狼人可见] 你投票要杀死哪位玩家？

重要: 你必须按照以下JSON格式输出投票:
{{"vote": "PlayerX"}}  // PlayerX是你要杀死的玩家"""

    to_wolves_res = "[仅狼人可见] 投票结果为 {}，你们选择淘汰 {}。"

    to_all_witch_turn = "轮到女巫行动，女巫请睁眼并决定今晚的操作..."
    to_witch_resurrect = (
        "[仅女巫可见] {witch_name}，你是女巫，今晚{dead_name}被淘汰。"
        "你可以用解药救他/她，注意解药全局只能用一次。你要救{dead_name}吗？"
        "请给出理由和决定。"
    )

    to_witch_resurrect_no = "[仅女巫可见] 女巫选择不救该玩家。"
    to_witch_resurrect_yes = "[仅女巫可见] 女巫选择救活该玩家。"

    to_witch_poison = """[仅女巫可见] {witch_name}，你有一瓶一次性毒药，今晚要使用吗？请给出理由和决定。

重要: 你必须按照以下JSON格式输出决策:
{{"poison": true/false, "name": "PlayerX"}}  // poison表示是否使用毒药，name是目标玩家（不使用则为null）"""

    to_all_seer_turn = "轮到预言家行动，预言家请睁眼并查验一名玩家身份..."

    to_seer = """[仅预言家可见] {}, 你是预言家，今晚可以查验一名玩家身份。
当前存活玩家: {}
你要查谁？请给出理由和决定。

重要: 你必须按照以下JSON格式输出决策（字段名必须完全一致）:
{{"name": "PlayerX"}}  // PlayerX是你要查验的玩家名字，必须从存活玩家中选择"""

    to_seer_result = "[仅预言家可见] 你查验了{agent_name}，结果是：{role}。"

    to_hunter = """[仅猎人可见] {name}，你是猎人，今晚被淘汰。你可以选择带走一名玩家，也可以选择不带走。
请给出理由和决定。

重要: 你必须按照以下JSON格式输出决策:
{{"shoot": true/false, "name": "PlayerX"}}  // shoot表示是否开枪，name是目标玩家（不开枪则为null）"""

    to_all_hunter_shoot = "猎人选择带走 {} 一起出局。"

    to_all_day = "天亮了，请所有玩家睁眼。昨晚被淘汰的玩家有：{}。"

    to_all_peace = "天亮了，请所有玩家睁眼。昨晚平安夜，无人被淘汰。"

    to_all_discuss = "现在存活玩家有：{names}。游戏继续，大家开始讨论并投票淘汰一名玩家。请按顺序（{names}）依次发言。"

    to_all_vote = """讨论结束。请大家从存活玩家中投票淘汰一人：{}。

重要: 你必须按照以下JSON格式输出投票:
{{"vote": "PlayerX"}}  // PlayerX是你要投票淘汰的玩家名字"""

    to_all_res = "投票结果为 {}，{} 被淘汰。"

    to_all_wolf_win = (
        "当前存活玩家共{n_alive}人，其中{n_werewolves}人为狼人。"
        "游戏结束，狼人获胜🐺🎉！"
        "本局所有玩家真实身份为：{true_roles}"
    )

    to_all_village_win = "所有狼人已被淘汰。游戏结束，村民获胜🏘️🎉！本局所有玩家真实身份为：{true_roles}"

    to_all_continue = "游戏继续。"

    to_all_reflect = "游戏结束。现在每位玩家可以对自己的表现进行反思。注意每位玩家只有一次发言机会，且反思内容仅自己可见。"
