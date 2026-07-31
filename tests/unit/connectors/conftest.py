"""连接器测试 fixtures（标准层空表清理后精简版）。

原 mapping_service / mapping_profile_service / standard_service fixture
依赖已删除的 MappingService / MappingProfileService / StandardService
（migration 0057），已移除。create_published_variable 辅助函数依赖
StandardService，一并移除。

保留：tests/conftest.py 的 sync_engine / async_session_factory /
test_user fixtures 仍可被 file_connectors 测试使用。
"""
