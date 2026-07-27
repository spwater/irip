/**
 * 实验室与设备 API 模块（F-23: 按领域拆分）
 *
 * 从 client.ts 拆分出的实验室管理和设备仪器相关类型和函数。
 * 通过 re-export 保持与 client.ts 的兼容性。
 */

export {
  type Department,
  type DepartmentListItem,
  type DepartmentListResponse,
  type DepartmentUser,
  type UserDepartment,
  type Equipment,
  type EquipmentListItem,
  type EquipmentListResponse,
  type EquipmentVariable,
  apiListDepartments,
  apiGetDepartment,
  apiCreateDepartment,
  apiUpdateDepartment,
  apiUpdateDepartmentStatus,
  apiDeleteDepartment,
  apiGetDepartmentUsers,
  apiGetUserDepartments,
  apiSetUserDepartments,
  apiListEquipment,
  apiGetEquipment,
  apiCreateEquipment,
  apiUpdateEquipment,
  apiUpdateEquipmentStatus,
  apiGetEquipmentVariables,
  apiSetEquipmentVariables,
  apiDeleteEquipment,
} from './client';
