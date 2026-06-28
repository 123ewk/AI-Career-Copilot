"""MQ 消费者 Handler 包

职责：
- 存放各业务域的 MQ 消费者 handler 函数
- 通过 @register 装饰器注册到 CONSUMER_REGISTRY
- ConsumerManager 在 lifespan startup 时自动拉起

已注册的 handler：
- job_analysis.py: Job Analysis Consumer（copilot.agent.job_analysis）
"""
