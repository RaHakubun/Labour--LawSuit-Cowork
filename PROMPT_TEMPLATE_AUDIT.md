# Prompt Template 审计报告

## 审计目标
- 检查每个场景模板中的 `analysis.scene_id.enum` 是否与后端场景目录一致。
- 不做自动修复，只暴露问题。

## 后端场景目录
recruitment_probation, adjustment_transfer, performance_discipline, salary_overtime_social, leave_medical_period, female_protection, work_injury, termination_layoff, noncompete_confidentiality, dispute_arbitration, rules_policy_effectiveness, flexible_employment_relationship, flexible_platform_employment, law_case_research, legal_qa_proxy, evidence_doc_generator

## 模板明细

| 模板 | 场景提示 | 期望 enum | 实际 enum 值数量 | 实际 enum 值 | 匹配 |
|---|---|---|---:|---|---|
| `ControllerAgent.md` | `ControllerAgent` | recruitment_probation, adjustment_transfer, performance_discipline, salary_overtime_social, leave_medical_period, female_protection, work_injury, termination_layoff, noncompete_confidentiality, dispute_arbitration, rules_policy_effectiveness, flexible_employment_relationship, flexible_platform_employment, law_case_research, legal_qa_proxy, evidence_doc_generator | 16 | recruitment_probation, adjustment_transfer, performance_discipline, salary_overtime_social, leave_medical_period, female_protection, work_injury, termination_layoff, noncompete_confidentiality, dispute_arbitration, rules_policy_effectiveness, flexible_employment_relationship, flexible_platform_employment, law_case_research, legal_qa_proxy, evidence_doc_generator | 是 |
| `adjustment_transfer.md` | `employer:adjustment_transfer` | adjustment_transfer | 1 | adjustment_transfer | 是 |
| `dispute_arbitration.md` | `employer:dispute_arbitration` | dispute_arbitration | 1 | dispute_arbitration | 是 |
| `evidence_doc_generator.md` | `employer:evidence_doc_generator` | evidence_doc_generator | 1 | evidence_doc_generator | 是 |
| `female_protection.md` | `employer:female_protection` | female_protection | 1 | female_protection | 是 |
| `flexible_employment_relationship.md` | `employer:flexible_employment_relationship` | flexible_employment_relationship | 1 | flexible_employment_relationship | 是 |
| `flexible_platform_employment.md` | `employer:flexible_platform_employment` | flexible_platform_employment | 1 | flexible_platform_employment | 是 |
| `law_case_research.md` | `employer:law_case_research` | law_case_research | 1 | law_case_research | 是 |
| `leave_medical_period.md` | `employer:leave_medical_period` | leave_medical_period | 1 | leave_medical_period | 是 |
| `legal_qa_proxy.md` | `employer:legal_qa_proxy` | legal_qa_proxy | 1 | legal_qa_proxy | 是 |
| `noncompete_confidentiality.md` | `employer:noncompete_confidentiality` | noncompete_confidentiality | 1 | noncompete_confidentiality | 是 |
| `performance_discipline.md` | `employer:performance_discipline` | performance_discipline | 1 | performance_discipline | 是 |
| `recruitment_probation.md` | `employer:recruitment_probation` | recruitment_probation | 1 | recruitment_probation | 是 |
| `rules_policy_effectiveness.md` | `employer:rules_policy_effectiveness` | rules_policy_effectiveness | 1 | rules_policy_effectiveness | 是 |
| `salary_overtime_social.md` | `employer:salary_overtime_social` | salary_overtime_social | 1 | salary_overtime_social | 是 |
| `termination_layoff.md` | `employer:termination_layoff` | termination_layoff | 1 | termination_layoff | 是 |
| `work_injury.md` | `employer:work_injury` | work_injury | 1 | work_injury | 是 |
| `adjustment_transfer.md` | `worker:adjustment_transfer` | adjustment_transfer | 1 | adjustment_transfer | 是 |
| `dispute_arbitration.md` | `worker:dispute_arbitration` | dispute_arbitration | 1 | dispute_arbitration | 是 |
| `evidence_doc_generator.md` | `worker:evidence_doc_generator` | evidence_doc_generator | 1 | evidence_doc_generator | 是 |
| `female_protection.md` | `worker:female_protection` | female_protection | 1 | female_protection | 是 |
| `flexible_employment_relationship.md` | `worker:flexible_employment_relationship` | flexible_employment_relationship | 1 | flexible_employment_relationship | 是 |
| `flexible_platform_employment.md` | `worker:flexible_platform_employment` | flexible_platform_employment | 1 | flexible_platform_employment | 是 |
| `law_case_research.md` | `worker:law_case_research` | law_case_research | 1 | law_case_research | 是 |
| `leave_medical_period.md` | `worker:leave_medical_period` | leave_medical_period | 1 | leave_medical_period | 是 |
| `legal_qa_proxy.md` | `worker:legal_qa_proxy` | legal_qa_proxy | 1 | legal_qa_proxy | 是 |
| `noncompete_confidentiality.md` | `worker:noncompete_confidentiality` | noncompete_confidentiality | 1 | noncompete_confidentiality | 是 |
| `performance_discipline.md` | `worker:performance_discipline` | performance_discipline | 1 | performance_discipline | 是 |
| `recruitment_probation.md` | `worker:recruitment_probation` | recruitment_probation | 1 | recruitment_probation | 是 |
| `rules_policy_effectiveness.md` | `worker:rules_policy_effectiveness` | rules_policy_effectiveness | 1 | rules_policy_effectiveness | 是 |
| `salary_overtime_social.md` | `worker:salary_overtime_social` | salary_overtime_social | 1 | salary_overtime_social | 是 |
| `termination_layoff.md` | `worker:termination_layoff` | termination_layoff | 1 | termination_layoff | 是 |
| `work_injury.md` | `worker:work_injury` | work_injury | 1 | work_injury | 是 |
| `adjustment_transfer.md` | `lawyer:adjustment_transfer` | adjustment_transfer | 1 | adjustment_transfer | 是 |
| `dispute_arbitration.md` | `lawyer:dispute_arbitration` | dispute_arbitration | 1 | dispute_arbitration | 是 |
| `evidence_doc_generator.md` | `lawyer:evidence_doc_generator` | evidence_doc_generator | 1 | evidence_doc_generator | 是 |
| `female_protection.md` | `lawyer:female_protection` | female_protection | 1 | female_protection | 是 |
| `flexible_employment_relationship.md` | `lawyer:flexible_employment_relationship` | flexible_employment_relationship | 1 | flexible_employment_relationship | 是 |
| `flexible_platform_employment.md` | `lawyer:flexible_platform_employment` | flexible_platform_employment | 1 | flexible_platform_employment | 是 |
| `law_case_research.md` | `lawyer:law_case_research` | law_case_research | 1 | law_case_research | 是 |
| `leave_medical_period.md` | `lawyer:leave_medical_period` | leave_medical_period | 1 | leave_medical_period | 是 |
| `legal_qa_proxy.md` | `lawyer:legal_qa_proxy` | legal_qa_proxy | 1 | legal_qa_proxy | 是 |
| `noncompete_confidentiality.md` | `lawyer:noncompete_confidentiality` | noncompete_confidentiality | 1 | noncompete_confidentiality | 是 |
| `performance_discipline.md` | `lawyer:performance_discipline` | performance_discipline | 1 | performance_discipline | 是 |
| `recruitment_probation.md` | `lawyer:recruitment_probation` | recruitment_probation | 1 | recruitment_probation | 是 |
| `rules_policy_effectiveness.md` | `lawyer:rules_policy_effectiveness` | rules_policy_effectiveness | 1 | rules_policy_effectiveness | 是 |
| `salary_overtime_social.md` | `lawyer:salary_overtime_social` | salary_overtime_social | 1 | salary_overtime_social | 是 |
| `termination_layoff.md` | `lawyer:termination_layoff` | termination_layoff | 1 | termination_layoff | 是 |
| `work_injury.md` | `lawyer:work_injury` | work_injury | 1 | work_injury | 是 |

## 结论
- 全部模板的 `scene_id.enum` 与后端目录一致。
