"""方法子包 ORM 模型与业务服务（已废弃）。

method / method_version 两张表及相关 ORM 类、MethodService 已在迁移 0056
中删除（实际未被业务使用，converter 接口已隐含方法信息）。

本模块保留为空，仅用于兼容历史 import 路径。请勿再引用本模块中的任何
符号；如需方法信息，请从摄入配置 / converter 元数据中获取。
"""
