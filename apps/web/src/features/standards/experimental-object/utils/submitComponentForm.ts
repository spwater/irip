/**
 * submitComponentForm — 提交新建数据接口表单。
 *
 * 从 ExperimentalObjectPage.tsx 提取。根据高级模式/表单模式
 * 校验并提交数据接口创建请求。
 */

import type { FormInstance } from 'antd';
import { buildManifestYaml, FORM_FIELD_NAMES } from '@/shared/component-utils';

/**
 * 提交数据接口表单：
 * - 高级模式：直接提交 manifest_yaml
 * - 表单模式：收集字段后自动生成 YAML 再提交
 */
export async function submitComponentForm(
  compForm: FormInstance,
  compAdvancedMode: boolean,
  mutate: (vars: {
    manifest_yaml: string;
    department_id?: string | null;
    visible_departments?: string[] | null;
  }) => void,
): Promise<void> {
  try {
    if (compAdvancedMode) {
      const values = await compForm.validateFields(['manifest_yaml', 'department_id', 'visible_departments']);
      mutate({
        manifest_yaml: values.manifest_yaml as string,
        department_id: (values.department_id as string) ?? null,
        visible_departments: (values.visible_departments as string[] | undefined) ?? null,
      });
    } else {
      const values = await compForm.validateFields([...FORM_FIELD_NAMES, 'department_id', 'visible_departments']);
      const yaml = buildManifestYaml({
        display_name: values.display_name as string,
        description: (values.description as string) ?? '',
        prompt: (values.prompt as string) ?? '',
        tool_type: (values.tool_type as string) ?? 'llm_converter',
      });
      mutate({
        manifest_yaml: yaml,
        department_id: (values.department_id as string) ?? null,
        visible_departments: (values.visible_departments as string[] | undefined) ?? null,
      });
    }
  } catch {
    // validation error
  }
}
