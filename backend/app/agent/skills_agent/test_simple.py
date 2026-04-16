"""
简单测试 - 验证 Agent 可以正常创建
"""
import os
import sys

# 添加当前目录到路径
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

# 获取 skill_root
skill_root = os.path.abspath(
    os.path.join(
        current_dir,
        "..",
        "..",
        "..",
        "..",
        "jianying-editor-skill"
    )
)

print("测试 Agent 创建...")
print(f"Skill Root: {skill_root}")

try:
    # 导入模块
    from jianying_agent import JianYingSkillMiddleware
    
    # 创建中间件
    print("\n创建中间件...")
    middleware = JianYingSkillMiddleware(skill_root)
    
    print(f"✅ 中间件创建成功")
    print(f"   - 技能数量: {len(middleware.skills)}")
    print(f"   - 工具数量: {len(middleware.tools)}")
    
    # 显示工具名称
    print("\n注册的工具:")
    for i, tool in enumerate(middleware.tools, 1):
        print(f"   {i}. {tool.name}")
    
    print("\n✅ 所有测试通过！")
    
except Exception as e:
    print(f"\n❌ 测试失败: {e}")
    import traceback
    traceback.print_exc()
