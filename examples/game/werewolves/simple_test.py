# -*- coding: utf-8 -*-
"""简单测试脚本 - 确保agent.py能跑通"""
import os
import sys

# 测试导入
try:
    from agent import PlayerAgent, DecisionMaker, RiskAssessment, TeamCoordination, PlayerMemory
    print("✅ 成功导入所有类")
except Exception as e:
    print(f"❌ 导入失败: {e}")
    sys.exit(1)

# 测试实例化
try:
    print("\n测试实例化...")
    
    # 测试策略模块
    decision_maker = DecisionMaker("TestPlayer")
    print("✅ DecisionMaker 实例化成功")
    
    risk_assessment = RiskAssessment()
    print("✅ RiskAssessment 实例化成功")
    
    team_coordination = TeamCoordination("TestPlayer")
    print("✅ TeamCoordination 实例化成功")
    
    memory = PlayerMemory()
    print("✅ PlayerMemory 实例化成功")
    
except Exception as e:
    print(f"❌ 模块实例化失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# 测试策略模块功能
try:
    print("\n测试策略模块功能...")
    
    # 构建测试用的game_state
    test_game_state = {
        'role': 'werewolf',
        'alive_players': ['Player1', 'Player2', 'Player3', 'Player4'],
        'dead_players': [],
        'alive_count': 4,
        'speeches': [
            {'player': 'Player1', 'content': '我觉得Player2很可疑'},
            {'player': 'Player2', 'content': '我是预言家，昨晚查验了Player3'}
        ],
        'night_results': [],
        'votes': [],
        'wolves_alive': 2,
        'night_count': 1
    }
    
    # 测试DecisionMaker
    decision = decision_maker.night_phase_decision('werewolf', test_game_state)
    print(f"✅ DecisionMaker.night_phase_decision: {decision}")
    
    decision = decision_maker.day_phase_decision(test_game_state)
    print(f"✅ DecisionMaker.day_phase_decision: {decision}")
    
    # 测试RiskAssessment
    risk = risk_assessment.evaluate_action_risk('reveal_identity', test_game_state)
    print(f"✅ RiskAssessment.evaluate_action_risk: {risk}")
    
    survival = risk_assessment.calculate_survival_probability(['Player1', 'Player2'], test_game_state)
    print(f"✅ RiskAssessment.calculate_survival_probability: {survival}")
    
    win_prob = risk_assessment.assess_win_probability(test_game_state)
    print(f"✅ RiskAssessment.assess_win_probability: {win_prob}")
    
    # 测试TeamCoordination
    teammates = team_coordination.identify_teammates([])
    print(f"✅ TeamCoordination.identify_teammates: {teammates}")
    
    coordination = team_coordination.coordinate_with_team([], 'night_kill', test_game_state)
    print(f"✅ TeamCoordination.coordinate_with_team: {coordination}")
    
    # 测试PlayerMemory
    memory.update_role('werewolf')
    print("✅ PlayerMemory.update_role")
    
    memory.record_game_result(True)
    print("✅ PlayerMemory.record_game_result")
    
    insights = memory.get_insights_for_prompt()
    print(f"✅ PlayerMemory.get_insights_for_prompt: {len(insights)} 字符")
    
except Exception as e:
    print(f"❌ 策略模块功能测试失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# 测试PlayerAgent（需要API Key）
print("\n测试 PlayerAgent...")
if not os.environ.get("DASHSCOPE_API_KEY"):
    print("⚠️  未设置 DASHSCOPE_API_KEY，跳过 PlayerAgent 完整测试")
    print("   如需完整测试，请运行:")
    print('   $env:DASHSCOPE_API_KEY="your_api_key"')
else:
    try:
        agent = PlayerAgent("TestPlayer")
        print("✅ PlayerAgent 实例化成功")
        
        # 测试属性
        print(f"   - 名字: {agent.name}")
        print(f"   - 当前角色: {agent.current_role}")
        print(f"   - 决策器: {type(agent.decision_maker).__name__}")
        print(f"   - 风险评估: {type(agent.risk_assessment).__name__}")
        print(f"   - 团队协作: {type(agent.team_coordination).__name__}")
        
        # 测试状态保存/加载
        state = agent.state_dict()
        print(f"✅ state_dict 成功，包含 {len(state)} 个键")
        
        agent.load_state_dict(state)
        print("✅ load_state_dict 成功")
        
    except Exception as e:
        print(f"❌ PlayerAgent 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

print("\n" + "="*60)
print("🎉 所有测试通过！代码可以运行了！")
print("="*60)
print("\n下一步:")
print("1. 设置 API Key: $env:DASHSCOPE_API_KEY=\"your_key\"")
print("2. 运行完整游戏: python main.py")
print("3. 开始优化你的策略模块！")
