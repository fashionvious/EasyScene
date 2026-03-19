"""
Redis管理模块测试文件
用于测试redis2.py的功能是否正常工作
"""
import asyncio
import sys
from typing import Optional

# 添加项目路径
sys.path.insert(0, "d:\\1-6 AI_Agent\\0_reading_sourcecode\\full-stack-fastapi-template\\backend")

from app.agent.utils.redis2 import (
    VideoProjectRedisManager,
    ProjectStage,
    ProjectStatus,
    TaskStatus,
    get_video_project_manager
)


async def test_project_lifecycle():
    """测试项目完整生命周期"""
    print("=" * 80)
    print("测试1: 项目完整生命周期")
    print("=" * 80)
    
    manager = get_video_project_manager()
    
    try:
        # 1. 创建项目
        print("\n【步骤1】创建项目")
        user_id = "user_001"
        project_id = await manager.create_project(
            user_id=user_id,
            metadata={
                "project_name": "测试视频项目",
                "script_text": "这是一个测试剧本内容..."
            }
        )
        print(f"✓ 项目创建成功: project_id={project_id}")
        
        # 2. 查看项目初始状态
        print("\n【步骤2】查看项目初始状态")
        project_state = await manager.get_project_state(project_id)
        if project_state:
            print(f"✓ 项目状态:")
            print(f"  - 项目ID: {project_state.project_id}")
            print(f"  - 用户ID: {project_state.user_id}")
            print(f"  - 当前阶段: {project_state.current_stage.value}")
            print(f"  - 当前状态: {project_state.current_status.value}")
            print(f"  - 创建时间: {project_state.created_at}")
        
        # 3. 创建第一个任务(角色描述生成)
        print("\n【步骤3】创建角色描述生成任务")
        task_id_1 = await manager.create_task(
            project_id=project_id,
            stage=ProjectStage.CHAR_DESC,
            metadata={"task_type": "generate_char_desc"}
        )
        print(f"✓ 任务创建成功: task_id={task_id_1}")
        
        # 4. 更新项目状态为运行中
        print("\n【步骤4】更新项目状态为运行中")
        await manager.update_project_state(
            project_id=project_id,
            status=ProjectStatus.RUNNING,
            task_id=task_id_1
        )
        print("✓ 项目状态已更新为运行中")
        
        # 5. 更新任务状态为运行中
        print("\n【步骤5】更新任务状态为运行中")
        await manager.update_task_state(
            task_id=task_id_1,
            status=TaskStatus.RUNNING
        )
        print("✓ 任务状态已更新为运行中")
        
        # 6. 模拟任务完成
        print("\n【步骤6】模拟任务完成")
        await manager.update_task_state(
            task_id=task_id_1,
            status=TaskStatus.SUCCESS,
            result_summary="角色描述生成完成: 主角是一个勇敢的冒险家..."
        )
        print("✓ 任务已完成")
        
        # 7. 设置项目为等待审核状态
        print("\n【步骤7】设置项目为等待审核状态")
        await manager.set_project_waiting_review(
            project_id=project_id,
            task_id=task_id_1,
            message="角色描述已生成,请审核确认"
        )
        print("✓ 项目已设置为等待审核")
        
        # 8. 查看待办队列
        print("\n【步骤8】查看用户待办队列")
        todo_items = await manager.get_user_todo_queue(user_id)
        print(f"✓ 待办队列中有 {len(todo_items)} 个项目:")
        for item in todo_items:
            print(f"  - 项目ID: {item.project_id}")
            print(f"    阶段: {item.stage.value}")
            print(f"    消息: {item.message}")
        
        # 9. 模拟用户审核通过
        print("\n【步骤9】模拟用户审核通过")
        await manager.complete_project_review(
            project_id=project_id,
            approved=True,
            feedback="角色描述符合要求"
        )
        print("✓ 审核通过,项目推进到下一阶段")
        
        # 10. 查看项目当前状态
        print("\n【步骤10】查看项目当前状态")
        project_state = await manager.get_project_state(project_id)
        if project_state:
            print(f"✓ 项目当前状态:")
            print(f"  - 当前阶段: {project_state.current_stage.value}")
            print(f"  - 当前状态: {project_state.current_status.value}")
            print(f"  - 阶段历史记录数: {len(project_state.stage_history)}")
            if project_state.stage_history:
                print("  - 最新历史记录:")
                latest = project_state.stage_history[-1]
                print(f"    从 {latest['from_stage']} -> {latest['to_stage']}")
        
        print("\n✅ 测试1完成: 项目生命周期测试成功\n")
        
    except Exception as e:
        print(f"\n❌ 测试1失败: {str(e)}\n")
        import traceback
        traceback.print_exc()
    finally:
        await manager.close()


async def test_multiple_stages():
    """测试多个阶段的推进"""
    print("=" * 80)
    print("测试2: 多阶段推进测试")
    print("=" * 80)
    
    manager = get_video_project_manager()
    
    try:
        # 创建项目
        print("\n【步骤1】创建项目")
        user_id = "user_002"
        project_id = await manager.create_project(user_id=user_id)
        print(f"✓ 项目创建成功: project_id={project_id}")
        
        # 模拟完成所有阶段
        stages = [
            (ProjectStage.CHAR_DESC, "角色描述生成"),
            (ProjectStage.CHAR_SIX_VIEW, "角色六视图生成"),
            (ProjectStage.SHOTLIST_SCRIPT, "分镜头脚本生成"),
            (ProjectStage.SHOTLIST_IMAGE, "分镜头图片生成"),
            (ProjectStage.SHOTLIST_VIDEO, "分镜头视频生成")
        ]
        
        for stage, stage_name in stages:
            print(f"\n【阶段】{stage_name}")
            
            # 创建任务
            task_id = await manager.create_task(
                project_id=project_id,
                stage=stage
            )
            print(f"  ✓ 创建任务: {task_id}")
            
            # 更新项目状态
            await manager.update_project_state(
                project_id=project_id,
                status=ProjectStatus.RUNNING,
                task_id=task_id
            )
            
            # 更新任务状态
            await manager.update_task_state(
                task_id=task_id,
                status=TaskStatus.RUNNING
            )
            await manager.update_task_state(
                task_id=task_id,
                status=TaskStatus.SUCCESS,
                result_summary=f"{stage_name}完成"
            )
            
            # 设置等待审核
            await manager.set_project_waiting_review(
                project_id=project_id,
                task_id=task_id,
                message=f"{stage_name}完成,请审核"
            )
            print(f"  ✓ 等待审核")
            
            # 模拟审核通过
            await manager.complete_project_review(
                project_id=project_id,
                approved=True
            )
            print(f"  ✓ 审核通过,推进到下一阶段")
        
        # 查看最终状态
        print("\n【最终状态】")
        project_state = await manager.get_project_state(project_id)
        if project_state:
            print(f"✓ 项目最终状态:")
            print(f"  - 当前阶段: {project_state.current_stage.value}")
            print(f"  - 当前状态: {project_state.current_status.value}")
            print(f"  - 阶段历史记录:")
            for i, history in enumerate(project_state.stage_history, 1):
                print(f"    {i}. {history['from_stage']} -> {history['to_stage']}")
        
        # 查看所有任务
        print("\n【任务列表】")
        task_ids = await manager.get_project_tasks(project_id)
        print(f"✓ 项目共有 {len(task_ids)} 个任务:")
        for task_id in task_ids:
            task_state = await manager.get_task_state(task_id)
            if task_state:
                print(f"  - 任务ID: {task_id}")
                print(f"    阶段: {task_state.stage.value}")
                print(f"    状态: {task_state.status.value}")
                print(f"    结果: {task_state.result_summary}")
        
        print("\n✅ 测试2完成: 多阶段推进测试成功\n")
        
    except Exception as e:
        print(f"\n❌ 测试2失败: {str(e)}\n")
        import traceback
        traceback.print_exc()
    finally:
        await manager.close()


async def test_review_rejection():
    """测试审核拒绝和修改流程"""
    print("=" * 80)
    print("测试3: 审核拒绝和修改流程")
    print("=" * 80)
    
    manager = get_video_project_manager()
    
    try:
        # 创建项目
        print("\n【步骤1】创建项目")
        user_id = "user_003"
        project_id = await manager.create_project(user_id=user_id)
        print(f"✓ 项目创建成功: project_id={project_id}")
        
        # 创建任务并完成
        print("\n【步骤2】创建任务并完成")
        task_id = await manager.create_task(
            project_id=project_id,
            stage=ProjectStage.CHAR_DESC
        )
        await manager.update_project_state(
            project_id=project_id,
            status=ProjectStatus.RUNNING,
            task_id=task_id
        )
        await manager.update_task_state(
            task_id=task_id,
            status=TaskStatus.SUCCESS,
            result_summary="角色描述: 一个普通的冒险家"
        )
        print(f"✓ 任务完成: task_id={task_id}")
        
        # 设置等待审核
        print("\n【步骤3】设置等待审核")
        await manager.set_project_waiting_review(
            project_id=project_id,
            task_id=task_id,
            message="角色描述已生成,请审核"
        )
        print("✓ 等待审核")
        
        # 查看待办队列
        print("\n【步骤4】查看待办队列")
        todo_items = await manager.get_user_todo_queue(user_id)
        print(f"✓ 待办队列中有 {len(todo_items)} 个项目")
        
        # 模拟审核拒绝
        print("\n【步骤5】模拟审核拒绝")
        await manager.complete_project_review(
            project_id=project_id,
            approved=False,
            feedback="角色描述太普通,需要更有特色"
        )
        print("✓ 审核拒绝,项目进入修改状态")
        
        # 查看项目状态
        print("\n【步骤6】查看项目状态")
        project_state = await manager.get_project_state(project_id)
        if project_state:
            print(f"✓ 项目状态:")
            print(f"  - 当前阶段: {project_state.current_stage.value}")
            print(f"  - 当前状态: {project_state.current_status.value}")
            print(f"  - 错误信息: {project_state.error_message}")
        
        # 查看待办队列(应该为空)
        print("\n【步骤7】查看待办队列")
        todo_items = await manager.get_user_todo_queue(user_id)
        print(f"✓ 待办队列中有 {len(todo_items)} 个项目(应该为0)")
        
        # 创建新任务进行修改
        print("\n【步骤8】创建修改任务")
        new_task_id = await manager.create_task(
            project_id=project_id,
            stage=ProjectStage.CHAR_DESC,
            metadata={"is_revision": True}
        )
        await manager.update_project_state(
            project_id=project_id,
            status=ProjectStatus.MODIFYING,
            task_id=new_task_id
        )
        await manager.update_task_state(
            task_id=new_task_id,
            status=TaskStatus.SUCCESS,
            result_summary="角色描述(修改版): 一个拥有神秘力量的年轻冒险家"
        )
        print(f"✓ 修改任务完成: task_id={new_task_id}")
        
        # 再次设置等待审核
        print("\n【步骤9】再次设置等待审核")
        await manager.set_project_waiting_review(
            project_id=project_id,
            task_id=new_task_id,
            message="角色描述已修改,请重新审核"
        )
        print("✓ 等待重新审核")
        
        # 模拟审核通过
        print("\n【步骤10】模拟审核通过")
        await manager.complete_project_review(
            project_id=project_id,
            approved=True,
            feedback="修改后的角色描述很好"
        )
        print("✓ 审核通过,推进到下一阶段")
        
        # 查看最终状态
        print("\n【最终状态】")
        project_state = await manager.get_project_state(project_id)
        if project_state:
            print(f"✓ 项目最终状态:")
            print(f"  - 当前阶段: {project_state.current_stage.value}")
            print(f"  - 当前状态: {project_state.current_status.value}")
        
        print("\n✅ 测试3完成: 审核拒绝和修改流程测试成功\n")
        
    except Exception as e:
        print(f"\n❌ 测试3失败: {str(e)}\n")
        import traceback
        traceback.print_exc()
    finally:
        await manager.close()


async def test_user_projects():
    """测试用户项目管理"""
    print("=" * 80)
    print("测试4: 用户项目管理")
    print("=" * 80)
    
    manager = get_video_project_manager()
    
    try:
        user_id = "user_004"
        
        # 创建多个项目
        print("\n【步骤1】创建多个项目")
        project_ids = []
        for i in range(3):
            project_id = await manager.create_project(
                user_id=user_id,
                metadata={"project_name": f"测试项目{i+1}"}
            )
            project_ids.append(project_id)
            print(f"✓ 创建项目{i+1}: {project_id}")
        
        # 查看用户所有项目
        print("\n【步骤2】查看用户所有项目")
        user_projects = await manager.get_user_projects(user_id)
        print(f"✓ 用户共有 {len(user_projects)} 个项目:")
        for pid in user_projects:
            project_state = await manager.get_project_state(pid)
            if project_state:
                print(f"  - 项目ID: {pid}")
                print(f"    项目名称: {project_state.metadata.get('project_name', 'N/A')}")
                print(f"    当前阶段: {project_state.current_stage.value}")
        
        # 删除一个项目
        print("\n【步骤3】删除一个项目")
        deleted_project_id = project_ids[0]
        await manager.delete_project(deleted_project_id)
        print(f"✓ 项目已删除: {deleted_project_id}")
        
        # 再次查看用户项目
        print("\n【步骤4】再次查看用户项目")
        user_projects = await manager.get_user_projects(user_id)
        print(f"✓ 用户现在有 {len(user_projects)} 个项目(应该为2)")
        
        print("\n✅ 测试4完成: 用户项目管理测试成功\n")
        
    except Exception as e:
        print(f"\n❌ 测试4失败: {str(e)}\n")
        import traceback
        traceback.print_exc()
    finally:
        await manager.close()


async def test_task_retry():
    """测试任务重试机制"""
    print("=" * 80)
    print("测试5: 任务重试机制")
    print("=" * 80)
    
    manager = get_video_project_manager()
    
    try:
        # 创建项目和任务
        print("\n【步骤1】创建项目和任务")
        user_id = "user_005"
        project_id = await manager.create_project(user_id=user_id)
        task_id = await manager.create_task(
            project_id=project_id,
            stage=ProjectStage.CHAR_DESC
        )
        print(f"✓ 项目和任务创建成功")
        
        # 模拟任务失败
        print("\n【步骤2】模拟任务失败")
        await manager.update_task_state(
            task_id=task_id,
            status=TaskStatus.FAILED,
            error_message="API调用超时"
        )
        print("✓ 任务状态已更新为失败")
        
        # 查看任务状态
        task_state = await manager.get_task_state(task_id)
        if task_state:
            print(f"✓ 任务状态:")
            print(f"  - 状态: {task_state.status.value}")
            print(f"  - 错误信息: {task_state.error_message}")
            print(f"  - 重试次数: {task_state.retry_count}")
        
        # 模拟重试
        print("\n【步骤3】模拟任务重试")
        for i in range(3):
            await manager.update_task_state(
                task_id=task_id,
                status=TaskStatus.RETRY,
                increment_retry=True
            )
            await manager.update_task_state(
                task_id=task_id,
                status=TaskStatus.RUNNING
            )
            print(f"✓ 第{i+1}次重试")
        
        # 查看重试后的状态
        print("\n【步骤4】查看重试后的状态")
        task_state = await manager.get_task_state(task_id)
        if task_state:
            print(f"✓ 任务状态:")
            print(f"  - 状态: {task_state.status.value}")
            print(f"  - 重试次数: {task_state.retry_count}")
        
        # 最终成功
        print("\n【步骤5】任务最终成功")
        await manager.update_task_state(
            task_id=task_id,
            status=TaskStatus.SUCCESS,
            result_summary="任务在第3次重试后成功"
        )
        
        task_state = await manager.get_task_state(task_id)
        if task_state:
            print(f"✓ 任务最终状态:")
            print(f"  - 状态: {task_state.status.value}")
            print(f"  - 结果: {task_state.result_summary}")
            print(f"  - 总重试次数: {task_state.retry_count}")
        
        print("\n✅ 测试5完成: 任务重试机制测试成功\n")
        
    except Exception as e:
        print(f"\n❌ 测试5失败: {str(e)}\n")
        import traceback
        traceback.print_exc()
    finally:
        await manager.close()


async def main():
    """主测试函数"""
    print("\n" + "=" * 80)
    print("开始测试 Redis 视频项目管理模块")
    print("=" * 80 + "\n")
    
    # 运行所有测试
    await test_project_lifecycle()
    await test_multiple_stages()
    await test_review_rejection()
    await test_user_projects()
    await test_task_retry()
    
    print("\n" + "=" * 80)
    print("所有测试完成!")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    # Windows平台事件循环策略
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    # 运行测试
    asyncio.run(main())
