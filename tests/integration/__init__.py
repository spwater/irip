"""IRIP 集成测试包。

需要真实外部依赖（数据库/对象存储）的测试放在本目录。
运行前需确保测试数据库容器已启动并执行迁移：
    docker compose -f deployments/compose/test.compose.yaml up -d postgres-test
    IRIP_DATABASE_URL=postgresql+psycopg://irip:irip_dev_password@localhost:55432/irip_test \
      .venv/bin/python -m alembic upgrade head
"""
