# Prompt Template 审计报告

## 审计目标
- Controller 模板必须覆盖后端全部可路由场景；运行时 contract 收窄到后端目录。
- 每个 Scenario 模板的 `analysis.scene_id` 必须与对应场景精确一致。
- 不做自动修复，只暴露问题。

## 后端场景目录
recruitment_probation, adjustment_transfer, performance_discipline, salary_overtime_social, leave_medical_period, female_protection, work_injury, termination_layoff, noncompete_confidentiality, dispute_arbitration

## 模板明细

| 模板 | 场景提示 | 期望 enum | 实际 enum 值数量 | 实际 enum 值 | 匹配 |
|---|---|---|---:|---|---|
| `ControllerAgent.md` | `ControllerAgent` | recruitment_probation, adjustment_transfer, performance_discipline, salary_overtime_social, leave_medical_period, female_protection, work_injury, termination_layoff, noncompete_confidentiality, dispute_arbitration | 16 | recruitment_probation, adjustment_transfer, performance_discipline, salary_overtime_social, leave_medical_period, female_protection, work_injury, termination_layoff, noncompete_confidentiality, dispute_arbitration, rules_policy_effectiveness, flexible_employment_relationship, flexible_platform_employment, law_case_research, legal_qa_proxy, evidence_doc_generator | 是 |
| `adjustment_transfer.md` | `worker:adjustment_transfer` | adjustment_transfer | 1 | adjustment_transfer | 是 |
| `dispute_arbitration.md` | `worker:dispute_arbitration` | dispute_arbitration | 1 | dispute_arbitration | 是 |
| `female_protection.md` | `worker:female_protection` | female_protection | 1 | female_protection | 是 |
| `leave_medical_period.md` | `worker:leave_medical_period` | leave_medical_period | 1 | leave_medical_period | 是 |
| `noncompete_confidentiality.md` | `worker:noncompete_confidentiality` | noncompete_confidentiality | 1 | noncompete_confidentiality | 是 |
| `performance_discipline.md` | `worker:performance_discipline` | performance_discipline | 1 | performance_discipline | 是 |
| `recruitment_probation.md` | `worker:recruitment_probation` | recruitment_probation | 1 | recruitment_probation | 是 |
| `salary_overtime_social.md` | `worker:salary_overtime_social` | salary_overtime_social | 1 | salary_overtime_social | 是 |
| `termination_layoff.md` | `worker:termination_layoff` | termination_layoff | 1 | termination_layoff | 是 |
| `work_injury.md` | `worker:work_injury` | work_injury | 1 | work_injury | 是 |
| `adjustment_transfer.md` | `employer:adjustment_transfer` | adjustment_transfer | 1 | adjustment_transfer | 是 |
| `dispute_arbitration.md` | `employer:dispute_arbitration` | dispute_arbitration | 1 | dispute_arbitration | 是 |
| `female_protection.md` | `employer:female_protection` | female_protection | 1 | female_protection | 是 |
| `leave_medical_period.md` | `employer:leave_medical_period` | leave_medical_period | 1 | leave_medical_period | 是 |
| `noncompete_confidentiality.md` | `employer:noncompete_confidentiality` | noncompete_confidentiality | 1 | noncompete_confidentiality | 是 |
| `performance_discipline.md` | `employer:performance_discipline` | performance_discipline | 1 | performance_discipline | 是 |
| `recruitment_probation.md` | `employer:recruitment_probation` | recruitment_probation | 1 | recruitment_probation | 是 |
| `salary_overtime_social.md` | `employer:salary_overtime_social` | salary_overtime_social | 1 | salary_overtime_social | 是 |
| `termination_layoff.md` | `employer:termination_layoff` | termination_layoff | 1 | termination_layoff | 是 |
| `work_injury.md` | `employer:work_injury` | work_injury | 1 | work_injury | 是 |
| `adjustment_transfer.md` | `lawyer:adjustment_transfer` | adjustment_transfer | 1 | adjustment_transfer | 是 |
| `dispute_arbitration.md` | `lawyer:dispute_arbitration` | dispute_arbitration | 1 | dispute_arbitration | 是 |
| `female_protection.md` | `lawyer:female_protection` | female_protection | 1 | female_protection | 是 |
| `leave_medical_period.md` | `lawyer:leave_medical_period` | leave_medical_period | 1 | leave_medical_period | 是 |
| `noncompete_confidentiality.md` | `lawyer:noncompete_confidentiality` | noncompete_confidentiality | 1 | noncompete_confidentiality | 是 |
| `performance_discipline.md` | `lawyer:performance_discipline` | performance_discipline | 1 | performance_discipline | 是 |
| `recruitment_probation.md` | `lawyer:recruitment_probation` | recruitment_probation | 1 | recruitment_probation | 是 |
| `salary_overtime_social.md` | `lawyer:salary_overtime_social` | salary_overtime_social | 1 | salary_overtime_social | 是 |
| `termination_layoff.md` | `lawyer:termination_layoff` | termination_layoff | 1 | termination_layoff | 是 |
| `work_injury.md` | `lawyer:work_injury` | work_injury | 1 | work_injury | 是 |

## 结论
- Controller 覆盖全部可路由场景，Scenario 场景协议均精确匹配。
