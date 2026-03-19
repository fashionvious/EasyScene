"""
Redis管理模块 - 视频生成项目状态管理
用于管理视频生成项目的全局状态、任务执行状态和用户待办队列
"""
import logging
from concurrent_log_handler import ConcurrentRotatingFileHandler
import redis.asyncio as redis
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional, List
from enum import Enum
import uuid
import json
import time
from datetime import datetime


# ============================================================================
# 枚举定义
# ============================================================================

class ProjectStage(str, Enum):
    """项目阶段枚举"""
    CHAR_DESC = "char_desc"                    # 角色描述生成阶段
    CHAR_SIX_VIEW = "char_six_view"            # 角色六视图生成阶段
    SHOTLIST_SCRIPT = "shotlist_script"        # 分镜头脚本生成阶段
    SHOTLIST_IMAGE = "shotlist_image"          # 分镜头图片生成阶段
    SHOTLIST_VIDEO = "shotlist_video"          # 分镜头视频生成阶段
    COMPLETED = "completed"                    # 项目完成


class ProjectStatus(str, Enum):
    """项目状态枚举"""
    INITIALIZED = "initialized"                # 已初始化
    RUNNING = "running"                        # 运行中
    WAITING_REVIEW = "waiting_review"          # 等待用户审核
    REVIEWING = "reviewing"                    # 用户审核中
    MODIFYING = "modifying"                    # 修改中
    COMPLETED = "completed"                    # 已完成
    FAILED = "failed"                          # 失败


class TaskStatus(str, Enum):
    """任务状态枚举"""
    PENDING = "pending"                        # 待执行
    RUNNING = "running"                        # 执行中
    SUCCESS = "success"                        # 成功
    FAILED = "failed"                          # 失败
    RETRY = "retry"                            # 重试中


# ============================================================================
# 数据模型定义
# ============================================================================

class ProjectGlobalState(BaseModel):
    """项目全局状态数据模型"""
    project_id: str = Field(..., description="项目唯一标识")
    user_id: str = Field(..., description="用户ID")
    current_stage: ProjectStage = Field(..., description="当前阶段")
    current_status: ProjectStatus = Field(..., description="当前状态")
    current_task_id: Optional[str] = Field(None, description="当前关联的Celery任务ID")
    error_message: Optional[str] = Field(None, description="错误信息")
    created_at: float = Field(default_factory=time.time, description="创建时间戳")
    updated_at: float = Field(default_factory=time.time, description="更新时间戳")
    stage_history: List[Dict[str, Any]] = Field(default_factory=list, description="阶段历史记录")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="项目元数据")


class TaskExecutionState(BaseModel):
    """任务执行状态数据模型"""
    task_id: str = Field(..., description="任务唯一标识")
    project_id: str = Field(..., description="关联的项目ID")
    stage: ProjectStage = Field(..., description="所属阶段")
    status: TaskStatus = Field(..., description="任务状态")
    started_at: Optional[float] = Field(None, description="任务开始时间")
    ended_at: Optional[float] = Field(None, description="任务结束时间")
    result_summary: Optional[str] = Field(None, description="执行结果摘要")
    retry_count: int = Field(default=0, description="重试次数")
    error_message: Optional[str] = Field(None, description="错误信息")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="任务元数据")


class UserTodoItem(BaseModel):
    """用户待办项数据模型"""
    project_id: str = Field(..., description="项目ID")
    user_id: str = Field(..., description="用户ID")
    stage: ProjectStage = Field(..., description="待审核阶段")
    created_at: float = Field(default_factory=time.time, description="创建时间")
    message: Optional[str] = Field(None, description="提示消息")


# ============================================================================
# Redis配置类
# ============================================================================

class RedisConfig:
    """Redis配置类"""
    REDIS_HOST = "localhost"
    REDIS_PORT = 6379
    REDIS_DB = 0
    REDIS_PASSWORD = "123456"
    
    # TTL配置
    PROJECT_TTL = 86400          # 项目状态TTL: 24小时
    TASK_TTL = 3600              # 任务状态TTL: 1小时
    TODO_TTL = 86400             # 待办队列TTL: 24小时
    
    # Redis键前缀
    PROJECT_KEY_PREFIX = "video_project"
    TASK_KEY_PREFIX = "video_task"
    TODO_KEY_PREFIX = "video_todo"
    USER_PROJECTS_KEY_PREFIX = "user_projects"


# ============================================================================
# 日志配置
# ============================================================================

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logger.handlers = []

# 创建控制台处理器
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
))
logger.addHandler(console_handler)


# ============================================================================
# Redis管理器类
# ============================================================================

class VideoProjectRedisManager:
    """
    视频生成项目Redis管理器
    管理项目全局状态、任务执行状态和用户待办队列
    """
    
    def __init__(
        self,
        redis_host: str = RedisConfig.REDIS_HOST,
        redis_port: int = RedisConfig.REDIS_PORT,
        redis_db: int = RedisConfig.REDIS_DB,
        redis_password: Optional[str] = RedisConfig.REDIS_PASSWORD
    ):
        """
        初始化Redis管理器
        
        Args:
            redis_host: Redis主机地址
            redis_port: Redis端口
            redis_db: Redis数据库编号
            redis_password: Redis密码
        """
        self.redis_client = redis.Redis(
            host=redis_host,
            port=redis_port,
            db=redis_db,
            password=redis_password,
            decode_responses=True
        )
        logger.info("Redis客户端初始化成功")
    
    async def close(self):
        """关闭Redis连接"""
        await self.redis_client.close()
        logger.info("Redis连接已关闭")
    
    # ========================================================================
    # 项目全局状态管理
    # ========================================================================
    
    async def create_project(
        self,
        user_id: str,
        project_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        创建新项目
        
        Args:
            user_id: 用户ID
            project_id: 项目ID(可选,不提供则自动生成)
            metadata: 项目元数据
            
        Returns:
            str: 项目ID
        """
        if project_id is None:
            project_id = str(uuid.uuid4())
        
        # 创建项目状态
        project_state = ProjectGlobalState(
            project_id=project_id,
            user_id=user_id,
            current_stage=ProjectStage.CHAR_DESC,
            current_status=ProjectStatus.INITIALIZED,
            metadata=metadata or {}
        )
        
        # 存储项目状态
        project_key = f"{RedisConfig.PROJECT_KEY_PREFIX}:{project_id}"
        await self.redis_client.set(
            project_key,
            project_state.model_dump_json(),
            ex=RedisConfig.PROJECT_TTL
        )
        
        # 将项目添加到用户的项目列表
        user_projects_key = f"{RedisConfig.USER_PROJECTS_KEY_PREFIX}:{user_id}"
        await self.redis_client.sadd(user_projects_key, project_id)
        await self.redis_client.expire(user_projects_key, RedisConfig.PROJECT_TTL)
        
        logger.info(f"创建项目成功: project_id={project_id}, user_id={user_id}")
        return project_id
    
    async def get_project_state(self, project_id: str) -> Optional[ProjectGlobalState]:
        """
        获取项目全局状态
        
        Args:
            project_id: 项目ID
            
        Returns:
            Optional[ProjectGlobalState]: 项目状态对象,不存在则返回None
        """
        project_key = f"{RedisConfig.PROJECT_KEY_PREFIX}:{project_id}"
        project_data = await self.redis_client.get(project_key)
        
        if not project_data:
            return None
        
        return ProjectGlobalState.model_validate_json(project_data)
    
    async def update_project_state(
        self,
        project_id: str,
        stage: Optional[ProjectStage] = None,
        status: Optional[ProjectStatus] = None,
        task_id: Optional[str] = None,
        error_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        更新项目状态
        
        Args:
            project_id: 项目ID
            stage: 新阶段(可选)
            status: 新状态(可选)
            task_id: 关联的任务ID(可选)
            error_message: 错误信息(可选)
            metadata: 要更新的元数据(可选)
            
        Returns:
            bool: 更新是否成功
        """
        project_state = await self.get_project_state(project_id)
        if not project_state:
            logger.error(f"项目不存在: project_id={project_id}")
            return False
        
        # 记录阶段变更历史
        if stage and stage != project_state.current_stage:
            history_entry = {
                "from_stage": project_state.current_stage.value,
                "to_stage": stage.value,
                "timestamp": time.time(),
                "task_id": task_id
            }
            project_state.stage_history.append(history_entry)
            project_state.current_stage = stage
        
        # 更新状态
        if status:
            project_state.current_status = status
        if task_id is not None:
            project_state.current_task_id = task_id
        if error_message is not None:
            project_state.error_message = error_message
        if metadata:
            project_state.metadata.update(metadata)
        
        project_state.updated_at = time.time()
        
        # 保存更新
        project_key = f"{RedisConfig.PROJECT_KEY_PREFIX}:{project_id}"
        await self.redis_client.set(
            project_key,
            project_state.model_dump_json(),
            ex=RedisConfig.PROJECT_TTL
        )
        
        logger.info(f"更新项目状态: project_id={project_id}, stage={stage}, status={status}")
        return True
    
    async def get_user_projects(self, user_id: str) -> List[str]:
        """
        获取用户的所有项目ID列表
        
        Args:
            user_id: 用户ID
            
        Returns:
            List[str]: 项目ID列表
        """
        user_projects_key = f"{RedisConfig.USER_PROJECTS_KEY_PREFIX}:{user_id}"
        project_ids = await self.redis_client.smembers(user_projects_key)
        
        # 过滤掉已过期的项目
        valid_project_ids = []
        for project_id in project_ids:
            project_key = f"{RedisConfig.PROJECT_KEY_PREFIX}:{project_id}"
            if await self.redis_client.exists(project_key):
                valid_project_ids.append(project_id)
            else:
                # 从用户项目列表中移除过期项目
                await self.redis_client.srem(user_projects_key, project_id)
        
        return valid_project_ids
    
    async def delete_project(self, project_id: str) -> bool:
        """
        删除项目及其相关数据
        
        Args:
            project_id: 项目ID
            
        Returns:
            bool: 删除是否成功
        """
        project_state = await self.get_project_state(project_id)
        if not project_state:
            return False
        
        # 删除项目状态
        project_key = f"{RedisConfig.PROJECT_KEY_PREFIX}:{project_id}"
        await self.redis_client.delete(project_key)
        
        # 从用户项目列表中移除
        user_projects_key = f"{RedisConfig.USER_PROJECTS_KEY_PREFIX}:{project_state.user_id}"
        await self.redis_client.srem(user_projects_key, project_id)
        
        # 从待办队列中移除
        await self.remove_from_todo_queue(project_id)
        
        logger.info(f"删除项目成功: project_id={project_id}")
        return True
    
    # ========================================================================
    # 任务执行状态管理
    # ========================================================================
    
    async def create_task(
        self,
        project_id: str,
        stage: ProjectStage,
        task_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        创建新任务
        
        Args:
            project_id: 项目ID
            stage: 所属阶段
            task_id: 任务ID(可选,不提供则自动生成)
            metadata: 任务元数据
            
        Returns:
            str: 任务ID
        """
        if task_id is None:
            task_id = str(uuid.uuid4())
        
        # 创建任务状态
        task_state = TaskExecutionState(
            task_id=task_id,
            project_id=project_id,
            stage=stage,
            status=TaskStatus.PENDING,
            metadata=metadata or {}
        )
        
        # 存储任务状态
        task_key = f"{RedisConfig.TASK_KEY_PREFIX}:{task_id}"
        await self.redis_client.set(
            task_key,
            task_state.model_dump_json(),
            ex=RedisConfig.TASK_TTL
        )
        
        # 将任务添加到项目的任务列表
        project_tasks_key = f"{RedisConfig.PROJECT_KEY_PREFIX}:{project_id}:tasks"
        await self.redis_client.sadd(project_tasks_key, task_id)
        await self.redis_client.expire(project_tasks_key, RedisConfig.PROJECT_TTL)
        
        logger.info(f"创建任务成功: task_id={task_id}, project_id={project_id}, stage={stage}")
        return task_id
    
    async def get_task_state(self, task_id: str) -> Optional[TaskExecutionState]:
        """
        获取任务执行状态
        
        Args:
            task_id: 任务ID
            
        Returns:
            Optional[TaskExecutionState]: 任务状态对象,不存在则返回None
        """
        task_key = f"{RedisConfig.TASK_KEY_PREFIX}:{task_id}"
        task_data = await self.redis_client.get(task_key)
        
        if not task_data:
            return None
        
        return TaskExecutionState.model_validate_json(task_data)
    
    async def update_task_state(
        self,
        task_id: str,
        status: Optional[TaskStatus] = None,
        result_summary: Optional[str] = None,
        error_message: Optional[str] = None,
        increment_retry: bool = False
    ) -> bool:
        """
        更新任务状态
        
        Args:
            task_id: 任务ID
            status: 新状态(可选)
            result_summary: 执行结果摘要(可选)
            error_message: 错误信息(可选)
            increment_retry: 是否增加重试次数
            
        Returns:
            bool: 更新是否成功
        """
        task_state = await self.get_task_state(task_id)
        if not task_state:
            logger.error(f"任务不存在: task_id={task_id}")
            return False
        
        # 更新状态
        if status:
            task_state.status = status
            
            # 记录开始/结束时间
            if status == TaskStatus.RUNNING and not task_state.started_at:
                task_state.started_at = time.time()
            elif status in [TaskStatus.SUCCESS, TaskStatus.FAILED]:
                task_state.ended_at = time.time()
        
        if result_summary is not None:
            task_state.result_summary = result_summary
        if error_message is not None:
            task_state.error_message = error_message
        if increment_retry:
            task_state.retry_count += 1
        
        # 保存更新
        task_key = f"{RedisConfig.TASK_KEY_PREFIX}:{task_id}"
        await self.redis_client.set(
            task_key,
            task_state.model_dump_json(),
            ex=RedisConfig.TASK_TTL
        )
        
        logger.info(f"更新任务状态: task_id={task_id}, status={status}")
        return True
    
    async def get_project_tasks(self, project_id: str) -> List[str]:
        """
        获取项目的所有任务ID列表
        
        Args:
            project_id: 项目ID
            
        Returns:
            List[str]: 任务ID列表
        """
        project_tasks_key = f"{RedisConfig.PROJECT_KEY_PREFIX}:{project_id}:tasks"
        task_ids = await self.redis_client.smembers(project_tasks_key)
        
        # 过滤掉已过期的任务
        valid_task_ids = []
        for task_id in task_ids:
            task_key = f"{RedisConfig.TASK_KEY_PREFIX}:{task_id}"
            if await self.redis_client.exists(task_key):
                valid_task_ids.append(task_id)
            else:
                # 从项目任务列表中移除过期任务
                await self.redis_client.srem(project_tasks_key, task_id)
        
        return valid_task_ids
    
    # ========================================================================
    # 用户待办队列管理
    # ========================================================================
    
    async def add_to_todo_queue(
        self,
        project_id: str,
        user_id: str,
        stage: ProjectStage,
        message: Optional[str] = None
    ) -> bool:
        """
        将项目添加到用户待办队列
        
        Args:
            project_id: 项目ID
            user_id: 用户ID
            stage: 待审核阶段
            message: 提示消息
            
        Returns:
            bool: 添加是否成功
        """
        # 创建待办项
        todo_item = UserTodoItem(
            project_id=project_id,
            user_id=user_id,
            stage=stage,
            message=message or f"项目 {project_id} 在阶段 {stage.value} 等待审核"
        )
        
        # 存储待办项
        todo_key = f"{RedisConfig.TODO_KEY_PREFIX}:{user_id}:{project_id}"
        await self.redis_client.set(
            todo_key,
            todo_item.model_dump_json(),
            ex=RedisConfig.TODO_TTL
        )
        
        # 将项目ID添加到用户的待办队列
        user_todo_queue_key = f"{RedisConfig.TODO_KEY_PREFIX}:{user_id}:queue"
        await self.redis_client.sadd(user_todo_queue_key, project_id)
        await self.redis_client.expire(user_todo_queue_key, RedisConfig.TODO_TTL)
        
        logger.info(f"添加到待办队列: user_id={user_id}, project_id={project_id}, stage={stage}")
        return True
    
    async def remove_from_todo_queue(self, project_id: str) -> bool:
        """
        从待办队列中移除项目
        
        Args:
            project_id: 项目ID
            
        Returns:
            bool: 移除是否成功
        """
        # 获取项目状态以确定用户ID
        project_state = await self.get_project_state(project_id)
        if not project_state:
            return False
        
        user_id = project_state.user_id
        
        # 删除待办项
        todo_key = f"{RedisConfig.TODO_KEY_PREFIX}:{user_id}:{project_id}"
        await self.redis_client.delete(todo_key)
        
        # 从用户待办队列中移除
        user_todo_queue_key = f"{RedisConfig.TODO_KEY_PREFIX}:{user_id}:queue"
        await self.redis_client.srem(user_todo_queue_key, project_id)
        
        logger.info(f"从待办队列移除: user_id={user_id}, project_id={project_id}")
        return True
    
    async def get_user_todo_queue(self, user_id: str) -> List[UserTodoItem]:
        """
        获取用户的待办队列
        
        Args:
            user_id: 用户ID
            
        Returns:
            List[UserTodoItem]: 待办项列表
        """
        user_todo_queue_key = f"{RedisConfig.TODO_KEY_PREFIX}:{user_id}:queue"
        project_ids = await self.redis_client.smembers(user_todo_queue_key)
        
        todo_items = []
        for project_id in project_ids:
            todo_key = f"{RedisConfig.TODO_KEY_PREFIX}:{user_id}:{project_id}"
            todo_data = await self.redis_client.get(todo_key)
            
            if todo_data:
                todo_items.append(UserTodoItem.model_validate_json(todo_data))
            else:
                # 清理过期的待办项
                await self.redis_client.srem(user_todo_queue_key, project_id)
        
        return todo_items
    
    async def get_todo_item(self, user_id: str, project_id: str) -> Optional[UserTodoItem]:
        """
        获取特定的待办项
        
        Args:
            user_id: 用户ID
            project_id: 项目ID
            
        Returns:
            Optional[UserTodoItem]: 待办项对象,不存在则返回None
        """
        todo_key = f"{RedisConfig.TODO_KEY_PREFIX}:{user_id}:{project_id}"
        todo_data = await self.redis_client.get(todo_key)
        
        if not todo_data:
            return None
        
        return UserTodoItem.model_validate_json(todo_data)
    
    # ========================================================================
    # 辅助方法
    # ========================================================================
    
    async def advance_project_stage(
        self,
        project_id: str,
        task_id: str
    ) -> bool:
        """
        推进项目到下一阶段
        
        Args:
            project_id: 项目ID
            task_id: 当前任务ID
            
        Returns:
            bool: 推进是否成功
        """
        project_state = await self.get_project_state(project_id)
        if not project_state:
            return False
        
        # 定义阶段顺序
        stage_order = [
            ProjectStage.CHAR_DESC,
            ProjectStage.CHAR_SIX_VIEW,
            ProjectStage.SHOTLIST_SCRIPT,
            ProjectStage.SHOTLIST_IMAGE,
            ProjectStage.SHOTLIST_VIDEO,
            ProjectStage.COMPLETED
        ]
        
        current_index = stage_order.index(project_state.current_stage)
        if current_index < len(stage_order) - 1:
            next_stage = stage_order[current_index + 1]
            return await self.update_project_state(
                project_id,
                stage=next_stage,
                status=ProjectStatus.RUNNING if next_stage != ProjectStage.COMPLETED else ProjectStatus.COMPLETED,
                task_id=task_id
            )
        
        return False
    
    async def set_project_waiting_review(
        self,
        project_id: str,
        task_id: str,
        message: Optional[str] = None
    ) -> bool:
        """
        设置项目为等待审核状态
        
        Args:
            project_id: 项目ID
            task_id: 当前任务ID
            message: 待办提示消息
            
        Returns:
            bool: 设置是否成功
        """
        project_state = await self.get_project_state(project_id)
        if not project_state:
            return False
        
        # 更新项目状态为等待审核
        success = await self.update_project_state(
            project_id,
            status=ProjectStatus.WAITING_REVIEW,
            task_id=task_id
        )
        
        if success:
            # 添加到待办队列
            await self.add_to_todo_queue(
                project_id,
                project_state.user_id,
                project_state.current_stage,
                message
            )
        
        return success
    
    async def complete_project_review(
        self,
        project_id: str,
        approved: bool,
        feedback: Optional[str] = None
    ) -> bool:
        """
        完成项目审核
        
        Args:
            project_id: 项目ID
            approved: 是否通过审核
            feedback: 审核反馈
            
        Returns:
            bool: 操作是否成功
        """
        project_state = await self.get_project_state(project_id)
        if not project_state:
            return False
        
        # 从待办队列移除
        await self.remove_from_todo_queue(project_id)
        
        if approved:
            # 通过审核,推进到下一阶段
            return await self.advance_project_stage(project_id, project_state.current_task_id)
        else:
            # 未通过审核,设置为修改状态
            return await self.update_project_state(
                project_id,
                status=ProjectStatus.MODIFYING,
                error_message=feedback
            )


# ============================================================================
# 工厂函数
# ============================================================================

def get_video_project_manager() -> VideoProjectRedisManager:
    """
    获取视频项目Redis管理器实例
    
    Returns:
        VideoProjectRedisManager: 管理器实例
    """
    manager = VideoProjectRedisManager(
        redis_host=RedisConfig.REDIS_HOST,
        redis_port=RedisConfig.REDIS_PORT,
        redis_db=RedisConfig.REDIS_DB,
        redis_password=RedisConfig.REDIS_PASSWORD
    )
    
    logger.info("视频项目Redis管理器初始化成功")
    return manager
