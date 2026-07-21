"""IRIP 机构/实验室管理领域包。

提供实验室（Department）与用户-实验室关联（AppUserDepartment）的
ORM 模型、数据仓库、业务服务。

模块结构：
- entities: ORM 模型（Department, AppUserDepartment, DepartmentStatus）
- repository: DepartmentRepository（纯数据访问 DAO）
- service: DepartmentService（业务编排）
- user_departments: UserDepartmentService（用户-实验室关联管理，P1）
"""
