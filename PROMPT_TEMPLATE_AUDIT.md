# Prompt Template 审计报告

## 审计目标
- 检查每个场景模板中的 `analysis.scene_id.enum` 是否与后端场景目录一致。
- 不做自动修复，只暴露问题。

## 后端场景目录
recruitment_probation, adjustment_transfer, performance_discipline, salary_overtime_social, leave_medical_period, female_protection, work_injury, termination_layoff, noncompete_confidentiality, dispute_arbitration

## 模板明细

| 模板 | 场景提示 | 期望 enum | 实际 enum 值数量 | 实际 enum 值 | 匹配 |
|---|---|---|---:|---|---|
| `ControllerAgent.md` | `ControllerAgent` | recruitment_probation, adjustment_transfer, performance_discipline, salary_overtime_social, leave_medical_period, female_protection, work_injury, termination_layoff, noncompete_confidentiality, dispute_arbitration | 10 | recruitment_probation, adjustment_transfer, performance_discipline, salary_overtime_social, leave_medical_period, female_protection, work_injury, termination_layoff, noncompete_confidentiality, dispute_arbitration | 是 |
| `adjustment_transfer.md` | `adjustment_transfer` | adjustment_transfer | 1 | adjustment_transfer | 是 |
| `dispute_arbitration.md` | `dispute_arbitration` | dispute_arbitration | 1 | dispute_arbitration | 是 |
| `female_protection.md` | `female_protection` | female_protection | 1 | female_protection | 是 |
| `leave_medical_period.md` | `leave_medical_period` | leave_medical_period | 1 | leave_medical_period | 是 |
| `noncompete_confidentiality.md` | `noncompete_confidentiality` | noncompete_confidentiality | 1 | noncompete_confidentiality | 是 |
| `performance_discipline.md` | `performance_discipline` | performance_discipline | 1 | performance_discipline | 是 |
| `recruitment_probation.md` | `recruitment_probation` | recruitment_probation | 1 | recruitment_probation | 是 |
| `salary_overtime_social.md` | `salary_overtime_social` | salary_overtime_social | 1 | salary_overtime_social | 是 |
| `termination_layoff.md` | `termination_layoff` | termination_layoff | 1 | termination_layoff | 是 |
| `work_injury.md` | `work_injury` | work_injury | 1 | work_injury | 是 |

## 结论
- 全部模板的 `scene_id.enum` 与后端目录一致。
