"""
Celery配置和实例
独立模块，避免循环依赖：tasks → service → celery_config 单向依赖
"""
from celery import Celery


class CeleryConfig:
    """Celery配置类"""
    CELERY_BROKER_URL = "redis://:123456@localhost:6379/0"
    CELERY_RESULT_BACKEND = "redis://:123456@localhost:6379/0"


celery_app = Celery(
    main="text2video_tasks",
    broker=CeleryConfig.CELERY_BROKER_URL,
    backend=CeleryConfig.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,
    task_soft_time_limit=3000,
)
